from pathlib import Path
import logging, json, re, shutil

_LOGGER = logging.getLogger(__name__)

DB_PATH = Path("/config/custom_components/fints_own/receipts.json")
UPLOAD_DIR = Path("/config/www/receipts_uploads")
PROCESSED_DIR = UPLOAD_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# OCR-Engines
from paddleocr import PaddleOCR
from doctr.models import ocr_predictor
from doctr.io import DocumentFile

reader_paddle = PaddleOCR(lang="german")
reader_doctr = ocr_predictor(pretrained=True)

def extract_items(lines):
    items = []
    for line in lines:
        match = re.match(r"(.+?)\s+(\d+,\d{2})", line)
        if match:
            name, price = match.groups()
            items.append({
                "name": name.strip(),
                "price": float(price.replace(",", "."))
            })
    return items


def run_paddleocr(file):
    results = reader_paddle.ocr(str(file), cls=True)
    lines = []
    for block in results:
        for line in block:
            lines.append(line[1][0])
    return lines


def run_doctr(file):
    doc = DocumentFile.from_images(str(file))
    result = reader_doctr(doc)
    text = result.render()
    return [t.strip() for t in text.splitlines() if t.strip()]


def process_receipt(file_path: Path):
    """Testet mehrere OCR-Engines und vergleicht Ergebnisse"""
    try:
        ocr_results = {}

        paddle_lines = run_paddleocr(file_path)
        doctr_lines = run_doctr(file_path)
        
        ocr_results["paddleocr"] = {
            "lines": paddle_lines,
            "items": extract_items(paddle_lines)
        }
        ocr_results["doctr"] = {
            "lines": doctr_lines,
            "items": extract_items(doctr_lines)
        }

        result = {
            "file": file_path.name,
            "ocr_results": ocr_results
        }

        _LOGGER.info(
            "OCR abgeschlossen für %s (Paddle:%d, Doctr:%d Zeilen)",
            file_path.name,len(paddle_lines), len(doctr_lines)
        )

        return result
    except Exception as e:
        _LOGGER.error("Fehler bei OCR für %s: %s", file_path, e)
        return None


def scan_folder(folder: Path = UPLOAD_DIR):
    data = []
    for img in folder.glob("*.[jp][pn]g"):
        res = process_receipt(img)
        if not res:
            continue
        data.append(res)
        shutil.move(str(img), PROCESSED_DIR / img.name)
    if data:
        DB_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        _LOGGER.info("OCR-Ergebnisse gespeichert: %d Dateien", len(data))
    else:
        _LOGGER.info("Keine neuen Dateien gefunden")
