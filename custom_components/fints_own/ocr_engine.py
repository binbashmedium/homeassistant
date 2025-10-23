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
    # normalize lines (strip and drop empties)
    lines_all = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines_all:
        return {"store": None, "total": None, "items": []}

    # 1) store = first line, then we iterate from the second line onward
    store = lines_all[0]
    lines = lines_all[1:]

    # regexes
    price_re_beg = re.compile(r"^(\d+[.,]\d{2})")  # price at line start (e.g., '2,99 B')
    price_any = re.compile(r"\d+[.,]\d{2}")        # price anywhere in line
    qty_re = re.compile(r"^\s*(\d+)\s*(Stk|x)\s*(?:x|×)?\s*(\d+[.,]\d{2})\s*$", re.IGNORECASE)

    def is_name_line(s: str) -> bool:
        """A product name line contains letters/spaces/punct, NO digits, and is not control tokens."""
        if not s or len(s) < 3:
            return False
        up = s.upper()
        if up in ("EUR", "SUMME", "TOTAL"):
            return False
        if "UID" in up:
            return False
        # reject anything that contains digits (addresses, PLZ, IDs, etc.)
        if re.search(r"\d", s):
            return False
        # allow typical chars in product names
        return bool(re.match(r"^[A-Za-zÄÖÜäöüß .,'\-]+$", s))

    names: list[str] = []
    prices: list[float] = []
    amounts: list[float | None] = []  # computed totals for qty lines; None for plain names

    in_items = False
    in_prices = False

    for line in lines:
        # switch to price section at EUR
        if line.upper() == "EUR":
            in_items = False
            in_prices = True
            continue

        if not in_items and not in_prices:
            # we are still in header; start items only when we see a valid product name line
            if is_name_line(line):
                in_items = True
                names.append(line)
                amounts.append(None)
            # else stay in header (skip)
            continue

        if in_items:
            # quantity line like "2 Stk x 0,90"
            m_qty = qty_re.match(line)
            if m_qty:
                qty = int(m_qty.group(1))
                price_each = float(m_qty.group(3).replace(",", "."))
                names.append(line)
                amounts.append(qty * price_each)
                continue

            # plain product name (no price in line)
            if is_name_line(line) and not price_any.search(line):
                names.append(line)
                amounts.append(None)
                continue

            # if we encounter something else (digits/junk) while in items block, just skip it
            continue

        if in_prices:
            # accept only lines that START with a price (filters out '2208' etc. that don't look like prices)
            m_p = price_re_beg.match(line)
            if m_p:
                prices.append(float(m_p.group(1).replace(",", ".")))
            # else skip (e.g. tax code, garbage)

    # map names to prices
    items = []
    p_idx = 0
    for idx, name in enumerate(names):
        if amounts[idx] is not None:
            # quantity line: computed total, derive qty from the text
            qmatch = qty_re.match(name)
            qty_val = int(qmatch.group(1)) if qmatch else 1
            items.append({
                "name": name,
                "price": round(amounts[idx], 2),
                "qty": qty_val
            })
        else:
            if p_idx < len(prices):
                items.append({
                    "name": name,
                    "price": round(prices[p_idx], 2),
                    "qty": 1
                })
                p_idx += 1

    # total = last/maximum price in the price block (here: use max to match your sample)
    total = max(prices) if prices else None

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
