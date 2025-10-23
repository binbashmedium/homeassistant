from homeassistant.core import HomeAssistant, ServiceCall
from pathlib import Path
from .ocr_engine import scan_folder
import base64, re, logging, json, unicodedata

_LOGGER = logging.getLogger(__name__)
DOMAIN = "fints_own"

UPLOAD_DIR = Path("/config/www/receipts_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RECEIPTS_DB = Path("/config/custom_components/fints_own/receipts.json")


def _find_receipt_for(amount: float, store: str | None = None) -> dict | None:
    receipts = _load_receipts()
    if not receipts:
        return None

    AMOUNT_TOL = 0.05
    MIN_STORE_SIM = 0.4

    best = None
    best_score = 0.0

    for r in receipts:
        if abs(r.get("total", 0) - amount) > AMOUNT_TOL:
            continue

        score = 1.0 - abs(r.get("total", 0) - amount)

        # optional Store matching
        if store:
            sim = _token_overlap(store, r.get("store", ""))
            if sim < MIN_STORE_SIM:
                continue
            score += sim

        if score > best_score:
            best = r
            best_score = score

    return best

def _norm(s: str) -> str:
    # einfache Normalisierung: lower, accents raus, Sonderzeichen → Space, Multi-Spaces → 1 Space
    s = (s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_overlap(a: str, b: str) -> float:
    ta = set(_norm(a).split())
    tb = set(_norm(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(len(ta), len(tb))


def _load_receipts() -> list:
    if RECEIPTS_DB.exists():
        try:
            return json.loads(RECEIPTS_DB.read_text())
        except Exception as e:
            _LOGGER.error("get_receipt_details: Kann receipts.json nicht lesen: %s", e)
    return []


async def async_setup(hass: HomeAssistant, config: dict):
    """Registriert die Services 'upload_image', 'scan_receipts' und 'get_receipt_details'."""

    #
    # Bild-Upload
    #
    async def handle_upload(call: ServiceCall):
        filename = call.data.get("filename", "upload.jpg")
        data_uri = call.data.get("image_data", "")

        match = re.match(r"^data:image/\w+;base64,(.*)", data_uri)
        if not match:
            _LOGGER.error("Ungültige Bilddaten empfangen. Erhaltene Daten beginnen mit: %s", data_uri[:50])
            return

        try:
            image_bytes = base64.b64decode(match.group(1))
            file_path = UPLOAD_DIR / filename

            def _write_file():
                file_path.write_bytes(image_bytes)

            await hass.async_add_executor_job(_write_file)
            _LOGGER.info("Bild gespeichert: %s", file_path)

            # OCR automatisch starten
            _LOGGER.info("Starte automatischen OCR-Scan nach Upload...")
            await hass.async_add_executor_job(scan_folder, UPLOAD_DIR)
            _LOGGER.info("Automatischer OCR-Scan abgeschlossen.")

        except Exception as e:
            _LOGGER.exception("Fehler beim Upload oder OCR-Scan: %s", e)

    hass.services.async_register(DOMAIN, "upload_image", handle_upload)

    #
    # OCR-Ordner manuell scannen
    #
    async def handle_scan(call: ServiceCall):
        folder_path = call.data.get("folder_path", str(UPLOAD_DIR))
        _LOGGER.info("Starte manuellen OCR-Scan für Ordner: %s", folder_path)

        try:
            results = await hass.async_add_executor_job(scan_folder, Path(folder_path))
            hass.bus.async_fire("fints_ocr_completed", {"count": len(results)})
            _LOGGER.info("OCR abgeschlossen: %d neue Belege", len(results))
        except Exception as e:
            _LOGGER.exception("Fehler beim OCR-Scan: %s", e)

    hass.services.async_register(DOMAIN, "scan_receipts", handle_scan)

    #
    #  Quittungsdetails finden
    #
    async def handle_get_details(call: ServiceCall):
        """
        Service: fints_own.get_receipt_details
        data:
          store: str (optional)
          total: float (required)
          date:  str (optional)
        """
        store = call.data.get("store")
        total = call.data.get("total")
        date = call.data.get("date")

        if total is None:
            return {"result": []}

        try:
            total_val = float(str(total).replace(",", "."))
        except Exception:
            _LOGGER.warning("get_receipt_details: total nicht parsebar: %r", total)
            return {"result": []}

        # Toleranz und minimale Ähnlichkeit
        AMOUNT_TOL = 0.05
        MIN_STORE_SIM = 0.4 if store else 0.0

        receipts = await hass.async_add_executor_job(_load_receipts)
        candidates = []

        for rec in receipts:
            rec_total = rec.get("total")
            rec_store = rec.get("store") or ""
            if rec_total is None:
                continue

            if abs(float(rec_total) - total_val) > AMOUNT_TOL:
                continue

            # Optional Store-Matching
            if store:
                sim = _token_overlap(store, rec_store)
                if sim < MIN_STORE_SIM:
                    continue
            else:
                sim = 0.0

            date_score = 0.0
            if date:
                dt = str(date)
                pattern = r"(?:\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b|\b\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\b)"
                rx = re.compile(pattern)
                if rec.get("raw_text") and rx.search(rec["raw_text"]):
                    date_score = 0.1

            score = 1.0 - abs(float(rec_total) - total_val) + sim + date_score
            candidates.append((score, rec))

        if not candidates:
            return {"result": []}

        candidates.sort(key=lambda t: t[0], reverse=True)
        best = candidates[0][1]
        return {"result": best}

    hass.services.async_register(
        DOMAIN, "get_receipt_details", handle_get_details, supports_response=True
    )

    _LOGGER.info("fints_own: Alle Services registriert.")
    return True
