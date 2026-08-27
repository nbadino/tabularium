"""Utilità immagini: dimensione, anteprime, metadati EXIF.

Nessuna dipendenza pesante: solo Pillow.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# Tag EXIF rilevanti (dati decimali)
_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_DATETIME = 0x0132


def image_size(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return im.size


def make_thumbnail(src: str | Path, dst: str | Path, max_side: int = 512) -> None:
    """Genera una JPEG di anteprima (lato max `max_side`) preservando l'orientamento."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(dst, "JPEG", quality=82)


def exif_datetime(path: str | Path) -> str | None:
    """Tentativo di data scatto/scansione dall'EXIF (stringa YYYY-MM-DD), se presente."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            raw = (
                exif.get(_EXIF_DATETIME_ORIGINAL)
                or exif.get(_EXIF_DATETIME)
                or None
            )
            if raw:
                return str(raw).strip()[:10]
    except Exception:
        return None
    return None


# --- deskew (richiede OpenCV, già presente con rapidocr) ---------------------


def _projection_skew(gray, lo: float = -5.0, hi: float = 5.0, step: float = 0.2) -> float:
    """Angolo che massimizza la varianza del profilo di proiezione orizzontale.

    Metodo robusto (stile Tesseract) su documenti densi: le righe di testo
    producono picchi netti quando l'immagine è allineata.
    """
    import cv2
    import numpy as np

    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = thresh.shape[:2]
    def score_at(ang: float) -> float:
        m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
        rotated = cv2.warpAffine(
            thresh, m, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        # varianza delle somme per riga (righe di testo = righe "piene")
        proj = rotated.sum(axis=1) / 255.0
        return float(proj.var())

    best_angle = 0.0
    best_score = -1.0
    for ang in np.arange(lo, hi + step / 2, step):
        score = score_at(float(ang))
        if score > best_score:
            best_score = score
            best_angle = float(ang)
    # Rifinitura angolare: 0,2° è sufficiente per l'inclinazione grossolana,
    # ma lascia visibilmente storte le righe dopo il dewarp. Cerca attorno al
    # massimo con passo 0,05°.
    fine_step = min(0.05, step / 4)
    for ang in np.arange(best_angle - step, best_angle + step + fine_step / 2, fine_step):
        score = score_at(float(ang))
        if score > best_score:
            best_score = score
            best_angle = float(ang)
    # Una pagina vuota/non testuale ha profilo piatto: non interpretare il
    # primo angolo provato (-5°) come una rotazione reale.
    return best_angle if best_score > 0.0 else 0.0


def estimate_skew(
    image: Image.Image,
    min_line_len: int = 60,
    hough_threshold: int = 80,
    max_angle: float = 45.0,
) -> float:
    """Angolo di inclinazione del testo (gradi), robusto sui documenti densi.

    Usa la proiezione orizzontale (metodo OCR) come metodo principale.
    """
    import numpy as np

    img = image.convert("L")
    # downscale per velocità e stabilità (l'angolo non cambia con la scala).
    # Anche pagine relativamente grandi devono restare rapide: la ricerca
    # prova molte rotazioni e il costo cresce col numero di pixel.
    longest = max(img.size)
    if longest > 1200:
        scale = 1200 / longest
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    gray = np.asarray(img)
    if gray.size == 0:
        return 0.0
    return _projection_skew(gray)


def deskew(image: Image.Image, min_line_len: int = 60) -> tuple[Image.Image, float]:
    """Raddrizza la pagina: ritorna (immagine deskewata, angolo applicato gradi).

    L'angolo stimato ~0 (|<0.05°|) viene trattato come "nessuna rotazione".
    """
    import cv2
    import numpy as np

    angle = estimate_skew(image, min_line_len=min_line_len)
    if abs(angle) < 0.05:
        return image, 0.0
    arr = np.asarray(image.convert("RGB"))
    h, w = arr.shape[:2]
    # convenzione OpenCV (y verso il basso): angolo positivo = senso orario visivo
    matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(
        arr, matrix, (w, h), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255),
    )
    return Image.fromarray(rotated), angle
