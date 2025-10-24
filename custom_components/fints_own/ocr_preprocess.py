import cv2
import numpy as np
from PIL import Image
import pytesseract
import os

# Debug-Bilder werden hier gespeichert
DEBUG_DIR = "debug_steps"
os.makedirs(DEBUG_DIR, exist_ok=True)

def save_debug(img, name):
    path = f"{DEBUG_DIR}/{name}.png"
    cv2.imwrite(path, img)
    print(f"[DEBUG] saved {path}")


# ----------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------
def rotate_bound(image, angle):
    (h,w) = image.shape[:2]
    center = (w//2, h//2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(M[0,0]); sin = abs(M[0,1])
    nW = int((h*sin)+(w*cos))
    nH = int((h*cos)+(w*sin))
    M[0,2] += (nW/2)-center[0]
    M[1,2] += (nH/2)-center[1]
    return cv2.warpAffine(image, M, (nW,nH))


def shading_correction(gray, ksize=101):
    bg = cv2.medianBlur(gray, ksize)
    corr = cv2.divide(gray, bg, scale=128)
    return corr


def largest_component(mask):
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if num <= 1:
        return mask
    # größte Komponente (Label 0 = Hintergrund)
    biggest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    out = np.zeros_like(mask)
    out[labels == biggest] = 255
    return out


# ----------------------------------------------------------
# Robust: Papier isolieren + gerade croppen via minAreaRect
# ----------------------------------------------------------
def extract_receipt_roi1(gray, dbg_prefix="A"):
    corr = shading_correction(gray, 101)
    save_debug(corr, f"{dbg_prefix}_shading_corr")

    # Papier hell → binärisieren
    _, bin_ = cv2.threshold(corr,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)

    # Löcher schließen
    bin_ = cv2.morphologyEx(bin_, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT,(9,9)),
                            iterations=2)
    save_debug(bin_, f"{dbg_prefix}_paper_bin")

    paper = largest_component(bin_)
    save_debug(paper, f"{dbg_prefix}_paper_lcc")

    ys, xs = np.where(paper>0)
    if xs.size < 50 or ys.size < 50:
        print("[extract_receipt_roi] keine Papierkomponente → fallback")
        return gray, paper

    pts = np.column_stack([xs,ys]).astype(np.float32)
    rect = cv2.minAreaRect(pts)   # ((cx,cy),(w,h),angle)
    box  = cv2.boxPoints(rect).astype(np.float32)

    dbg = cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
    cv2.drawContours(dbg,[box.astype(int)],0,(0,255,0),2)
    save_debug(dbg, f"{dbg_prefix}_minAreaRect")

    w,h = int(rect[1][0]), int(rect[1][1])
    angle = rect[2]

    # Hochformat sicherstellen
    if w < h:
        w,h = h,w
        angle += 90

    def order(p):
        s = p.sum(axis=1)
        diff = np.diff(p,axis=1)
        return np.array([
            p[np.argmin(s)],
            p[np.argmin(diff)],
            p[np.argmax(s)],
            p[np.argmax(diff)]
        ], dtype=np.float32)

    src = order(box)
    dst = np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]], dtype=np.float32)
    M2 = cv2.getPerspectiveTransform(src,dst)
    warped = cv2.warpPerspective(gray, M2, (w,h))

    save_debug(warped, f"{dbg_prefix}_warpedROI")
    return warped, paper

def extract_receipt_roi2(gray, dbg_prefix="A"):
    # --- 1: emphasize character edges
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT,
                            cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    save_debug(grad, f"{dbg_prefix}_text_grad")

    # --- 2: close horizontally (text lines)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT,(25,2))
    closed = cv2.morphologyEx(grad, cv2.MORPH_CLOSE, kernel, iterations=3)
    save_debug(closed, f"{dbg_prefix}_text_closed")
    # remove weak gradient noise (hands, floor)
    density = cv2.blur(closed, (35,35))
    _, dense = cv2.threshold(density, 30, 255, cv2.THRESH_BINARY)
    save_debug(dense, f"{dbg_prefix}_text_dense")

    paper = cv2.bitwise_and(closed, dense)
    save_debug(paper, f"{dbg_prefix}_paper_mask")

    paper = largest_component(paper)
    save_debug(paper, f"{dbg_prefix}_paper_lcc")


# --- 3: largest connected text component
    paper = largest_component(closed)
    save_debug(paper, f"{dbg_prefix}_paper_from_text")

    ys, xs = np.where(paper > 0)
    if xs.size < 50 or ys.size < 50:
        print("[extract_receipt_roi] no text cluster → fallback")
        return gray, paper

    # bounding rect on text cluster
    pts = np.column_stack([xs, ys]).astype(np.float32)
    rect = cv2.minAreaRect(pts)
    box  = cv2.boxPoints(rect).astype(np.float32)

    dbg = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(dbg, [box.astype(int)], 0, (0,255,0), 2)
    save_debug(dbg, f"{dbg_prefix}_minAreaRect_text")
    # target width and height (as integers)
    w, h = int(rect[1][0]), int(rect[1][1])

    # ensure portrait orientation
    if w < h:
        w, h = h, w

        # sanity checks
    img_area = gray.shape[0] * gray.shape[1]
    box_area = w * h
    ar = h / float(w)

    if box_area > img_area * 0.8:
        print("[ROI] box too large → fallback to deskew only")
    return gray, paper

    if ar < 1.1:
        print("[ROI] aspect ratio too horizontal → fallback")
    return gray, paper

    # order the box points TL,TR,BR,BL
    def order(pts):
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)
        return np.array([
            pts[np.argmin(s)],      # TL
            pts[np.argmin(diff)],   # TR
            pts[np.argmax(s)],      # BR
            pts[np.argmax(diff)]    # BL
        ], dtype=np.float32)

    src = order(box)
    dst = np.array([[0, 0],
                    [w-1, 0],
                    [w-1, h-1],
                    [0,   h-1]], dtype=np.float32)

    M = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(gray, M, (w, h))
    save_debug(warped, f"{dbg_prefix}_warpedROI")

    return warped, paper

def extract_receipt_roi3(gray, dbg_prefix="A"):
    # 1) character gradients
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT,
                            cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    save_debug(grad, f"{dbg_prefix}_grad")

    # 2) text line clustering
    hor = cv2.morphologyEx(grad, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_RECT,(30,2)),
                           iterations=3)
    save_debug(hor, f"{dbg_prefix}_hor")

    # 3) largest text blob
    blob = largest_component(hor)
    save_debug(blob, f"{dbg_prefix}_blob")

    ys, xs = np.where(blob > 0)
    if xs.size < 50:
        return gray, blob

    # === 4) 2D projection envelope ===

    # row projection (top / bottom)
    proj_r = np.sum(blob > 0, axis=1)
    proj_r_s = cv2.blur(proj_r.reshape(-1,1),(1,35)).flatten()
    row_mask = proj_r_s > np.max(proj_r_s)*0.08
    rows = np.where(row_mask)[0]
    top = max(rows[0]-80,0)
    bot = min(rows[-1]+80,gray.shape[0]-1)

    # col projection (left / right)
    proj_c = np.sum(blob > 0, axis=0)
    proj_c_s = cv2.blur(proj_c.reshape(1,-1),(35,1)).flatten()
    col_mask = proj_c_s > np.max(proj_c_s)*0.10
    cols = np.where(col_mask)[0]
    left  = max(cols[0]-60,0)
    right = min(cols[-1]+60, gray.shape[1]-1)

    # rectangular envelope
    hull = np.zeros_like(gray)
    hull[top:bot, left:right] = 255
    save_debug(hull, f"{dbg_prefix}_hull")

    # combine text + hull
    mask = cv2.bitwise_or(hull, blob)
    save_debug(mask, f"{dbg_prefix}_mask")

    # close gaps
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT,(25,25))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    save_debug(closed, f"{dbg_prefix}_closed_env")

    # largest component -> real receipt shape
    paper = largest_component(closed)
    save_debug(paper, f"{dbg_prefix}_paper")

    ys, xs = np.where(paper > 0)
    pts = np.column_stack([xs,ys]).astype(np.float32)

    # bounding box
    rect = cv2.minAreaRect(pts)
    box  = cv2.boxPoints(rect).astype(np.float32)

    dbg = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(dbg,[box.astype(int)],0,(0,255,0),2)
    save_debug(dbg, f"{dbg_prefix}_minAreaRect_env")

    w,h = int(rect[1][0]), int(rect[1][1])
    if w < h: w,h = h,w

    def order(pts):
        s=pts.sum(axis=1); diff=np.diff(pts,axis=1)
        return np.array([
            pts[np.argmin(s)],
            pts[np.argmin(diff)],
            pts[np.argmax(s)],
            pts[np.argmax(diff)]
        ],dtype=np.float32)

    src = order(box)
    dst = np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]],np.float32)
    M = cv2.getPerspectiveTransform(src,dst)
    warped = cv2.warpPerspective(gray, M, (w,h))
    save_debug(warped, f"{dbg_prefix}_warpedROI")

    return warped, paper

def _order_pts(pts):
    rect = np.zeros((4,2), dtype="float32")
    s = pts.sum(axis=1); d = np.diff(pts, axis=1)
    rect[0] = pts[np.argmin(s)]     # tl
    rect[2] = pts[np.argmax(s)]     # br
    rect[1] = pts[np.argmin(d)]     # tr
    rect[3] = pts[np.argmax(d)]     # bl
    return rect

def _warp(image, pts):
    rect = _order_pts(pts)
    (tl,tr,br,bl) = rect
    wA = np.linalg.norm(br-bl); wB = np.linalg.norm(tr-tl)
    hA = np.linalg.norm(tr-br); hB = np.linalg.norm(tl-bl)
    W = int(max(wA,wB)); H = int(max(hA,hB))
    dst = np.array([[0,0],[W-1,0],[W-1,H-1],[0,H-1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (W,H))

def extract_receipt_roi(gray, dbg_prefix="A"):
    """
    Findet den kompletten Kassenzettel (größte helle Komponente),
    markiert ihn, entzerrt ihn und schneidet außen herum ab.
    Speichert jeden Schritt via save_debug.
    """
    # 0) Normalisierung / Kontrast
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    norm = clahe.apply(gray)
    save_debug(norm, f"{dbg_prefix}_00_clahe")

    # 1) Leichte Glättung
    blur = cv2.GaussianBlur(norm, (5,5), 0)
    save_debug(blur, f"{dbg_prefix}_01_blur")

    # 2) "Papiermaske": größte helle Fläche
    #    Otsu -> sicherstellen, dass Papier weiß ist
    th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    if th.mean() < 127:   # falls invertiert
        th = 255 - th
    save_debug(th, f"{dbg_prefix}_02_otsu_white")

    # 3) Lücken schließen (Text auf Papier)
    close = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT,(15,15)), iterations=1)
    open_ = cv2.morphologyEx(close, cv2.MORPH_OPEN,  cv2.getStructuringElement(cv2.MORPH_RECT,(5,5)),  iterations=1)
    save_debug(close, f"{dbg_prefix}_03_close")
    save_debug(open_, f"{dbg_prefix}_04_open")

    # 4) Größte Komponente = Kassenzettel
    cnts, _ = cv2.findContours(open_, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        save_debug(open_, f"{dbg_prefix}_05_no_contours")
        return gray, None

    big = max(cnts, key=cv2.contourArea)
    rect = cv2.minAreaRect(big)
    box  = cv2.boxPoints(rect).astype(np.float32)

    # 5) Plausibilitäts-Checks & Sicherheitssrand
    #    (typisch: hochkant, flächenmäßig signifikant)
    h, w = gray.shape[:2]
    area_img = h * w
    area_box = cv2.contourArea(box.astype(np.int32))
    if area_box < 0.08 * area_img:
        # Fallback: nimm BoundingRect
        x,y,bw,bh = cv2.boundingRect(big)
        box = np.array([[x,y],[x+bw,y],[x+bw,y+bh],[x,y+bh]], dtype=np.float32)

    # Sicherheitsrand +10 %
    cx, cy = box.mean(axis=0)
    box = (box - [cx,cy]) * 1.10 + [cx,cy]  # 10% größer
    box[:,0] = np.clip(box[:,0], 0, w-1)
    box[:,1] = np.clip(box[:,1], 0, h-1)

    # 6) Visualisierung auf Original
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    cv2.drawContours(vis, [box.astype(np.int32)], -1, (0,255,0), 3)
    save_debug(vis, f"{dbg_prefix}_05_detected_box")

    # 7) Perspektivisches Entzerren
    warped_gray = _warp(gray, box)
    save_debug(warped_gray, f"{dbg_prefix}_06_warped")

    # 8) Knapp trimmen (Papier gegen Rand)
    th2 = cv2.threshold(warped_gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]
    if th2.mean() < 127: th2 = 255 - th2
    cnts2, _ = cv2.findContours(th2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts2:
        c2 = max(cnts2, key=cv2.contourArea)
        x,y,bw,bh = cv2.boundingRect(c2)
        # Optional: 2% Innenrand weg, damit schwarze Ränder verschwinden
        pad = int(0.02 * max(bw,bh))
        x = max(0, x - pad); y = max(0, y - pad)
        bw = min(warped_gray.shape[1]-x, bw + 2*pad)
        bh = min(warped_gray.shape[0]-y, bh + 2*pad)
        warped_crop = warped_gray[y:y+bh, x:x+bw]
    else:
        warped_crop = warped_gray
    save_debug(warped_crop, f"{dbg_prefix}_07_final_crop")

    return warped_crop, None





# ----------------------------------------------------------
# Fein-Deskew (nur kleine Restwinkel)
# ----------------------------------------------------------
def deskew(gray, dbg_prefix="A"):
    inv = cv2.bitwise_not(gray)
    _, bw = cv2.threshold(inv,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    save_debug(bw, f"{dbg_prefix}_a_deskew_bin")

    angles = np.arange(-4.0,4.0,0.2)
    scores = []
    for a in angles:
        r = rotate_bound(bw,a)
        hist = np.sum(r,axis=1)
        scores.append(np.var(hist))
    best = float(angles[int(np.argmax(scores))])

    if abs(best) < 0.3:
        print("[Deskew] already straight")
        return gray

    print(f"[Deskew] rotating {best:.2f}°")
    grey_rotated = rotate_bound(gray, best)
    save_debug(grey_rotated,f"{dbg_prefix}_b_deskew_gray")
    return grey_rotated


# ----------------------------------------------------------
# Pipeline
# ----------------------------------------------------------
def preprocess(path):
    img = cv2.imread(path)
    save_debug(img,"00_original")

    gray = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    save_debug(gray,"01_gray")

    blur = cv2.bilateralFilter(gray,9,15,15)
    save_debug(blur,"02_bilateral")

    # 1) Papier extrahieren + grob begradigen
    roi, paper = extract_receipt_roi(blur, dbg_prefix="03")

    # 2) Fein-Deskew (kleiner Restwinkel)
    desk = deskew(roi, dbg_prefix="04")

    # 3) Kontrast verbessern
    clahe = cv2.createCLAHE(clipLimit=3.0,tileGridSize=(8,8))
    cl = clahe.apply(desk)
    save_debug(cl,"05_clahe")

    # 4) Adaptive Threshold
    thr = cv2.adaptiveThreshold(
        cl,255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        25,15
    )
    save_debug(thr,"06_thresh")

    return thr


# ----------------------------------------------------------
# OCR Wrapper
# ----------------------------------------------------------
def ocr(img):
    return pytesseract.image_to_string(
        Image.fromarray(img),
        lang="eng",       # englischer Bon → eng besser
        config="--psm 6"
    )


# ----------------------------------------------------------
# Main
# ----------------------------------------------------------
if __name__ == "__main__":

    INPUT = "test3.jpg"   # anpassen
    processed = preprocess(INPUT)
    text = ocr(processed)

    with open("ocr_output.txt","w",encoding="utf8") as f:
        f.write(text)

    print("\n=== OCR RESULT ===\n")
    print(text)
    print("Ergebnis gespeichert in ocr_output.txt")
