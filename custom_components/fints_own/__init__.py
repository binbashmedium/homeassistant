from homeassistant.core import HomeAssistant, ServiceCall
from pathlib import Path
from .ocr_engine import scan_folder
from pathlib import Path
import base64, re, logging

_LOGGER = logging.getLogger(__name__)
DOMAIN = "fints_own"

UPLOAD_DIR = Path("/config/www/receipts_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

async def async_setup(hass: HomeAssistant, config: dict):
    async def handle_upload(call: ServiceCall):
        filename = call.data.get("filename", "upload.jpg")
        data_uri = call.data.get("image_data", "")

        # Extrahiere Base64-Daten aus data:image/…;base64,
        match = re.match(r"^data:image/\w+;base64,(.*)", data_uri)
        if not match:
            _LOGGER.error("Ungültige Bilddaten empfangen")
            return

        image_bytes = base64.b64decode(match.group(1))
        file_path = UPLOAD_DIR / filename

        file_path.write_bytes(image_bytes)
        _LOGGER.info("Bild gespeichert: %s", file_path)

    hass.services.async_register(DOMAIN, "upload_image", handle_upload)
    return True

    async def handle_scan(call: ServiceCall):
        folder_path = call.data.get("folder_path", "/config/www/receipts_uploads")
        results = await hass.async_add_executor_job(scan_folder, Path(folder_path))
        hass.bus.async_fire("fints_ocr_completed", {"count": len(results)})

        hass.services.async_register("fints_own", "scan_receipts", handle_scan)
    return True

    

