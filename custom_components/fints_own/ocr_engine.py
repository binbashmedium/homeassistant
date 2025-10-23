import pytesseract
from PIL import Image
from pathlib import Path
import re, json, shutil, logging

_LOGGER = logging.getLogger(__name__)

# Paths
DB_PATH = Path("/config/custom_components/fints_own/receipts.json")
UPLOAD_DIR = Path("/config/www/receipts_uploads")
PROCESSED_DIR = UPLOAD_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# Explicit path to tesseract binary (helps inside HA containers)
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"


def read_existing():
    """Lese bestehende receipts.json ein."""
    if DB_PATH.exists():
        try:
            return json.loads(DB_PATH.read_text())
        except Exception as e:
            _LOGGER.error("Fehler beim Lesen von receipts.json: %s", e)
    return []


def save_all(data):
    """Speichere alle OCR-Ergebnisse in JSON-Datei."""
    try:
        DB_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        _LOGGER.error("Fehler beim Schreiben von receipts.json: %s", e)


def extract_items(text: str):
    """
    Extrahiere Artikelzeilen mit Preisen.
    Erkennt Formate wie:
      'Milch 1,29'
      '2 Stk x 1,69'
      'BIO HAFERFLOCKEN  2,49 B'
      'SPAGHETTIGER. TO SB  1.69'
    """
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # 1️⃣ Preis mit Komma oder Punkt, evtl. gefolgt von Buchstaben (B/A)
        m = re.search(r"(\d+[\.,]\d{2})\s*[BA]?$", line)
        if not m:
            continue

        price_str = m.group(1).replace(",", ".")
        try:
            price = float(price_str)
        except ValueError:
            continue

        # 2️⃣ Artikelname = alles vor dem Preis
        name = re.sub(r"\s*\d+[\.,]\d{2}\s*[BA]?$", "", line).strip(" -;:,.")
        if len(name) < 2:
            continue

        # 3️⃣ Optionale Stückzahl '2 Stk x'
        name = re.sub(r"^\d+\s*(Stk|x|X)\s*", "", name, flags=re.IGNORECASE)

        items.append({
            "name": name,
            "price": price
        })

    return items



def process_receipt(file_path: Path):
    """Führe OCR auf einer Bilddatei aus und extrahiere Artikel."""
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang="deu")

        items = extract_items(text)
        result = {
            "file": file_path.name,
            "items": items,
            "raw_text": text.strip()
        }

        _LOGGER.info(
            "OCR abgeschlossen für %s: %d Artikel erkannt",
            file_path.name, len(items)
        )
        return result

    except Exception as e:
        _LOGGER.error("Fehler bei OCR für %s: %s", file_path, e)
        return None


def scan_folder(folder: Path = UPLOAD_DIR):
    """Scanne alle neuen Bilder im Upload-Ordner."""
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

        # Verschiebe in processed/
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
