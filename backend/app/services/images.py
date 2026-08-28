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


def perspective_rectify(
    image: Image.Image,
    points: list[tuple[float, float]] | list[list[float]],
) -> Image.Image:
    """Rettifica un quadrilatero sulla tela originale.

    ``points`` è ordinato TL, TR, BR, BL ed espresso in pixel sorgente. La
    tela resta invariata: tutte le coordinate successive continuano quindi a
    usare ``pages.width``/``pages.height``.
    """
    import cv2
    import numpy as np

    if len(points) != 4:
        raise ValueError("perspective_requires_four_points")
    src = np.asarray(points, dtype=np.float32)
    if src.shape != (4, 2) or not np.isfinite(src).all():
        raise ValueError("perspective_points_invalid")
    width, height = image.size
    if (
        (src[:, 0] < 0).any()
        or (src[:, 0] > width - 1).any()
        or (src[:, 1] < 0).any()
        or (src[:, 1] > height - 1).any()
    ):
        raise ValueError("perspective_points_outside")
    # Un quadrilatero concavo o autointersecante produrrebbe una pagina
    # ripiegata: OpenCV lo accetta, ma non è un risultato utile per l'OCR.
    if not cv2.isContourConvex(src.reshape((-1, 1, 2))):
        raise ValueError("perspective_points_not_convex")
    if abs(float(cv2.contourArea(src.reshape((-1, 1, 2))))) < width * height * 0.05:
        raise ValueError("perspective_area_too_small")
    dst = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    out = cv2.warpPerspective(
        np.asarray(image.convert("RGB")),
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )
    return Image.fromarray(out)


def mesh_rectify(
    image: Image.Image,
    grid: list[list[list[float]]] | list[list[tuple[float, float]]],
) -> Image.Image:
    """Rettifica una griglia manuale normalizzata con celle quadrilatere.

    I punti sorgente sono normalizzati 0–1; la destinazione è una griglia
    uniforme sull'intera tela. È lo stesso principio della mesh manuale di
    strumenti di digitalizzazione: l'utente fa seguire le righe della griglia
    alla carta curva, poi ogni cella viene riportata a rettangolo.
    """
    import numpy as np

    if len(grid) < 2 or any(len(row) < 2 for row in grid):
        raise ValueError("mesh_grid_too_small")
    cols = len(grid[0])
    if any(len(row) != cols for row in grid):
        raise ValueError("mesh_grid_ragged")
    arr = np.asarray(grid, dtype=float)
    if arr.shape != (len(grid), cols, 2) or not np.isfinite(arr).all():
        raise ValueError("mesh_points_invalid")
    if (arr < 0).any() or (arr > 1).any():
        raise ValueError("mesh_points_outside")
    # Le righe e le colonne non possono invertirsi: impedisce celle piegate o
    # sovrapposte che Pillow trasformerebbe senza segnalare l'errore.
    if (np.diff(arr[:, :, 0], axis=1) <= 0).any() or (np.diff(arr[:, :, 1], axis=0) <= 0).any():
        raise ValueError("mesh_points_crossed")

    width, height = image.size
    px = arr.copy()
    px[:, :, 0] *= width - 1
    px[:, :, 1] *= height - 1
    rows = len(grid)
    mesh: list[tuple[tuple[int, int, int, int], tuple[float, ...]]] = []
    for r in range(rows - 1):
        top = round(r * height / (rows - 1))
        bottom = round((r + 1) * height / (rows - 1))
        for c in range(cols - 1):
            left = round(c * width / (cols - 1))
            right = round((c + 1) * width / (cols - 1))
            tl, tr = px[r, c], px[r, c + 1]
            bl, br = px[r + 1, c], px[r + 1, c + 1]
            # Pillow QUAD: UL, LL, LR, UR.
            quad = (*tl, *bl, *br, *tr)
            mesh.append(((left, top, right, bottom), tuple(float(v) for v in quad)))
    return image.convert("RGB").transform(
        (width, height),
        Image.Transform.MESH,
        mesh,
        resample=Image.Resampling.BICUBIC,
        fillcolor="white",
    )
