from homeassistant.core import HomeAssistant, ServiceCall
from pathlib import Path
from .ocr_engine import scan_folder
import base64, re, logging

_LOGGER = logging.getLogger(__name__)
DOMAIN = "fints_own"

UPLOAD_DIR = Path("/config/www/receipts_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def async_setup(hass: HomeAssistant, config: dict):
    """Registriert die Services 'upload_image' und 'scan_receipts'."""

    #  1. Service: Bild hochladen
    async def handle_upload(call: ServiceCall):
        filename = call.data.get("filename", "upload.jpg")
        data_uri = call.data.get("image_data", "")

        # Extract Base64 data from data:image/...;base64,
        match = re.match(r"^data:image/\w+;base64,(.*)", data_uri)
        if not match:
            _LOGGER.error("Ungültige Bilddaten empfangen. Erhaltene Daten beginnen mit: %s", data_uri[:50])
            return


        try:
            image_bytes = base64.b64decode(match.group(1))
            file_path = UPLOAD_DIR / filename
            # write file in executor to avoid blocking call warning
            def _write_file():
                file_path.write_bytes(image_bytes)

            await hass.async_add_executor_job(_write_file)
            _LOGGER.info("📸 Bild gespeichert: %s", file_path)
            _LOGGER.info("Bild gespeichert: %s", file_path)

            # OCR automatisch starten
            _LOGGER.info("Starte automatischen OCR-Scan nach Upload...")
            await hass.async_add_executor_job(scan_folder, UPLOAD_DIR)
            _LOGGER.info("Automatischer OCR-Scan abgeschlossen.")

        except Exception as e:
            _LOGGER.exception("Fehler beim Upload oder OCR-Scan: %s", e)

    hass.services.async_register(DOMAIN, "upload_image", handle_upload)

    #  2. Service: Manuell OCR-Scan auslösen
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

    _LOGGER.info("fints_own: Services 'upload_image' und 'scan_receipts' erfolgreich registriert.")
    return True
