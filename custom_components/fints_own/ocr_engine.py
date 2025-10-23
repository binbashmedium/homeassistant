import pytesseract
from PIL import Image
from pathlib import Path
import re, json, shutil, logging

_LOGGER = logging.getLogger(__name__)

# Storage
DB_PATH = Path("/config/custom_components/fints_own/receipts.json")
UPLOAD_DIR = Path("/config/www/receipts_uploads")
PROCESSED_DIR = UPLOAD_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# Path to tesseract binary
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"


def read_existing():
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text())
        except Exception as e:
            _LOGGER.error("Fehler beim Lesen von receipts.json: %s", e)
    return []


def save_all(data):
    try:
        DB_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        _LOGGER.error("Fehler beim Schreiben von receipts.json: %s", e)



def parse_receipt(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Laden = erste Zeile mit Großbuchstaben
    store = next((l for l in lines if l.isupper()), lines[0])

    names = []
    prices = []
    amounts = []

    in_prices = False

    # Regex
    price_re = re.compile(r"(\d+[,\.]\d{2})")
    qty_re = re.compile(r"(\d+)\s*(Stk|x)\s*(\d+[,\.]\d{2})", re.IGNORECASE)

    for line in lines:

        # Preise beginnen ab "EUR"
        if line.upper() == "EUR":
            in_prices = True
            continue

        if not in_prices:
            # Artikelzeilen
            m_qty = qty_re.search(line)
            if m_qty:
                # Stückzahlzeile gefunden
                qty = int(m_qty.group(1))
                price_each = float(m_qty.group(3).replace(",", "."))
                names.append(line)
                amounts.append(qty * price_each)
                continue

            # Zeilen ohne Preise = Artikelnamen
            if not price_re.search(line) and len(line) > 3:
                names.append(line)
                amounts.append(None)

        else:
            # Preise extrahieren
            m_price = price_re.match(line)
            if m_price:
                prices.append(float(m_price.group(1).replace(",", ".")))

    # Jetzt Names + Prices + Amounts matchen
    items = []
    p_idx = 0

    for idx, name in enumerate(names):
        if amounts[idx] is not None:
            # Stückpreis
            items.append({
                "name": name,
                "price": amounts[idx],
                "qty": int(qty_re.search(name).group(1))
            })
        else:
            if p_idx < len(prices):
                items.append({
                    "name": name,
                    "price": prices[p_idx],
                    "qty": 1
                })
                p_idx += 1

    # Total = letzter Betrag
    total = None
    if prices:
        total = max(prices)

    return {
        "store": store,
        "total": total,
        "items": items,
    }




def process_receipt(file_path: Path):
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang="deu")

        parsed = parse_receipt(text)

        result = {
            "file": file_path.name,
            "store": parsed["store"],
            "total": parsed["total"],
            "items": parsed["items"],
            "raw_text": text.strip()
        }

        _LOGGER.info(
            "OCR abgeschlossen für %s: %d Artikel erkannt",
            file_path.name, len(parsed["items"])
        )

        return result

    except Exception as e:
        _LOGGER.error("Fehler bei OCR für %s: %s", file_path, e)
        return None


def scan_folder(folder: Path = UPLOAD_DIR):
    data = read_existing()
    known_files = {entry["file"] for entry in data}
    new_results = []

    for img in folder.glob("*.[jp][pn]g"):
        if img.name in known_files:
            continue

        _LOGGER.info("Starte OCR für Datei: %s", img.name)
        result = process_receipt(img)
        if not result:
            continue

        data.append(result)
        new_results.append(result)

        # Verschiebe nach processed/
        target = PROCESSED_DIR / img.name
        try:
            shutil.move(str(img), target)
            _LOGGER.info("Verschoben nach: %s", target)
        except Exception as e:
            _LOGGER.error("Fehler beim Verschieben: %s", e)

    if new_results:
        save_all(data)
        _LOGGER.info("%d neue Belege verarbeitet und gespeichert.", len(new_results))
    else:
        _LOGGER.info("Keine neuen Belege gefunden.")

    return new_results
