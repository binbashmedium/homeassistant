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


import re

def parse_receipt(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    store = lines[0]                           # Zeile 0 ist der Ladenname
    total = None
    names = []
    prices = []

    # 1️⃣ Artikel-Namen sammeln (vor "EUR")
    for line in lines:
        if line.upper() == "EUR":
            break
        if re.match(r".*[A-ZÄÖÜa-zäöü]+.*", line) and not re.match(r".*\d+,\d{2}", line):
            names.append(line)

    # 2️⃣ Preise + Steuer-Code sammeln (nach "EUR")
    euro_section = False
    for line in lines:
        if line.upper() == "EUR":
            euro_section = True
            continue
        if euro_section:
            m = re.match(r"(\d+,\d{2})\s*[AB]?", line)
            if m:
                prices.append(float(m.group(1).replace(",", ".")))

    # 3️⃣ Kombiniere Artikel + Preise
    items = []
    for name, price in zip(names, prices):
        items.append({
            "name": name,
            "price": price
        })

    # 4️⃣ Gesamtbetrag suchen (höchster erkannter Preis)
    if prices:
        total = max(prices)

    return {
        "store": store,
        "total": total,
        "items": items
    }




def process_receipt(file_path: Path):
    """Führe OCR auf einer Bilddatei aus und extrahiere Artikel."""
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang="deu")

        parsed = parse_receipt(text)
        items = parsed["items"]
        store = parsed["store"]
        total = parsed["total"]
        items = extract_items(text)
        result =  {
                "file": file_path.name,
                "store": store,
                "total": total,
                "items": items,
                "raw_text": text.strip()}

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
