from homeassistant.core import HomeAssistant, ServiceCall
from pathlib import Path
import base64, re, logging

_LOGGER = logging.getLogger(__name__)
DOMAIN = "shopping_ocr"

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
        _LOGGER.info("✅ Bild gespeichert: %s", file_path)

    hass.services.async_register(DOMAIN, "upload_image", handle_upload)
    return True
  
