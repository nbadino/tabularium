"""Helper per le pagine: percorsi anteprime, generazione on-demand."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from .. import config
from . import images as imgmod
from . import scan as scanmod

THUMB_SIDE = 512
PREVIEW_SIDE = 1600
TILE_SIZE = 512
TRANSFORM_VERSION = 3


def thumb_dir() -> Path:
    return config.ROOT_DIR / "thumbs"


def thumb_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}.jpg"


def preview_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_preview.jpg"


def deskew_path(page_id: int) -> Path:
    """Immagine raddrizzata (deskew) della pagina; se presente sostituisce l'originale
    in preview/thumbnail/crop/dataset export."""
    return thumb_dir() / f"p{page_id}_deskew.jpg"


def transform_meta_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_deskew.json"


def mark_transform(page_id: int, level: str, size: tuple[int, int]) -> None:
    path = transform_meta_path(page_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": TRANSFORM_VERSION, "level": level, "size": list(size)}), encoding="utf-8")


def _has_invalid_transform(page: sqlite3.Row) -> bool:
    """Riconosce trasformazioni derivate da una tela diversa dall'originale."""
    transformed = deskew_path(page["id"])
    metadata = transform_meta_path(page["id"])
    if not transformed.exists() or page["source_kind"] == "pdf":
        return False
    try:
        details = json.loads(metadata.read_text(encoding="utf-8"))
        if details.get("version") != TRANSFORM_VERSION:
            return True
        with Image.open(transformed) as img:
            return img.size != (int(page["width"]), int(page["height"]))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return True


def _purge_invalid_transform(page: sqlite3.Row) -> None:
    """Elimina solo cache derivati sicuramente non compatibili con la fonte."""
    if not _has_invalid_transform(page):
        return
    deskew_path(page["id"]).unlink(missing_ok=True)
    transform_meta_path(page["id"]).unlink(missing_ok=True)
    thumb_path(page["id"]).unlink(missing_ok=True)
    preview_path(page["id"]).unlink(missing_ok=True)
    import shutil

    shutil.rmtree(config.ROOT_DIR / "tiles" / f"p{page['id']}", ignore_errors=True)


def _load_source_image(page: sqlite3.Row, use_deskew: bool = True) -> Image.Image | None:
    """Ritorna l'immagine sorgente della pagina (deskewata se attiva, altrimenti
    image originale o pdf+indice)."""
    if use_deskew:
        d = deskew_path(page["id"])
        if d.exists() and not _has_invalid_transform(page):
            return Image.open(d)
    src = page["abs_path"]
    if not Path(src).exists():
        return None
    if page["source_kind"] == "pdf":
        images = scanmod.render_pdf_pages(src)
        idx = page["pdf_page"] or 0
        if idx >= len(images):
            return None
        return images[idx][0]
    return Image.open(src)


def load_source_image(page: sqlite3.Row) -> Image.Image | None:
    """Wrapper pubblico: immagine sorgente della pagina (preferisce il deskew)."""
    return _load_source_image(page)


def load_original_source_image(page: sqlite3.Row) -> Image.Image | None:
    """Immagine ORIGINALE (senza deskew), per rigenerare il deskew."""
    return _load_source_image(page, use_deskew=False)


def maybe_auto_deskew(page: sqlite3.Row, threshold: float = 0.10) -> tuple[Image.Image, float]:
    """Raddrizza la pagina se serve (rotazione >= soglia), salva il deskew e
    invalida thumb/preview/tiles. Ritorna (immagine allineata, angolo applicato)."""
    import shutil

    from . import images as imgmod

    img = _load_source_image(page, use_deskew=False)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    aligned, angle = imgmod.deskew(img)
    if abs(angle) >= threshold:
        desk = deskew_path(page["id"])
        desk.parent.mkdir(parents=True, exist_ok=True)
        aligned.convert("RGB").save(desk, "JPEG", quality=92)
        mark_transform(page["id"], "auto", aligned.size)
        for p in (thumb_path(page["id"]), preview_path(page["id"])):
            if p.exists():
                p.unlink(missing_ok=True)
        tiles = config.ROOT_DIR / "tiles" / f"p{page['id']}"
        if tiles.exists():
            shutil.rmtree(tiles, ignore_errors=True)
    return aligned, angle


def ensure_thumbnail(conn: sqlite3.Connection, page: sqlite3.Row) -> Path:
    """Ritorna (generate se mancante) la thumbnail JPEG della pagina."""
    _purge_invalid_transform(page)
    expected = thumb_path(page["id"])
    if expected.exists():
        return expected
    img = _load_source_image(page)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    expected.parent.mkdir(parents=True, exist_ok=True)
    img = img.convert("RGB")
    img.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    img.save(expected, "JPEG", quality=82)
    conn.execute("UPDATE pages SET thumb_path=? WHERE id=?", (str(expected), page["id"]))
    return expected


def ensure_preview(conn: sqlite3.Connection, page: sqlite3.Row) -> Path:
    """Ritorna (generate se mancante) un'anteprima a risoluzione maggiore."""
    _purge_invalid_transform(page)
    expected = preview_path(page["id"])
    if expected.exists():
        return expected
    img = _load_source_image(page)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    expected.parent.mkdir(parents=True, exist_ok=True)
    img = img.convert("RGB")
    img.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
    img.save(expected, "JPEG", quality=88)
    return expected


def save_pdf_thumb(pil_image: Image.Image, page_id: int) -> Path:
    """Salva la thumbnail di una pagina PDF renderizzata."""
    thumb = thumb_path(page_id)
    thumb.parent.mkdir(parents=True, exist_ok=True)
    img = pil_image.convert("RGB")
    img.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    img.save(thumb, "JPEG", quality=82)
    return thumb


def crop_block_image(page: sqlite3.Row, bbox: tuple[int, int, int, int]) -> Image.Image:
    """Crop della regione (pixel pagina) come immagine PIL.

    Sorgente identica a quella servita al canvas: chi analizza il ritaglio
    (rilevatore di griglia, OCR) lavora sugli stessi pixel che l'utente vede.
    """
    img = _load_source_image(page)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    x1, y1, x2, y2 = bbox
    img = img.convert("RGB")
    crop = img.crop(
        (max(0, x1), max(0, y1), min(img.width, x2), min(img.height, y2))
    )
    if crop.width <= 0 or crop.height <= 0:
        raise HTTPException(status_code=400, detail="crop vuoto")
    return crop


def crop_block_jpeg(page: sqlite3.Row, bbox: tuple[int, int, int, int]) -> bytes:
    """Crop della regione (pixel pagina) come JPEG in-memory."""
    from io import BytesIO

    buf = BytesIO()
    crop_block_image(page, bbox).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def tile_path(page_id: int, level: int, x: int, y: int) -> Path:
    return config.ROOT_DIR / "tiles" / f"p{page_id}" / f"z{level}" / f"{x}_{y}.jpg"


def _level_path(page_id: int, level: int) -> Path:
    return config.ROOT_DIR / "tiles" / f"p{page_id}" / f"z{level}" / "_level.jpg"


def ensure_tile(page: sqlite3.Row, level: int, x: int, y: int) -> Path:
    """Genera on-demand un tile JPEG, con cache per pagina/livello."""
    if not (0 <= level <= 8 and x >= 0 and y >= 0):
        raise HTTPException(status_code=400, detail="tile non valido")
    out = tile_path(page["id"], level, x, y)
    if out.exists():
        return out
    img = _load_source_image(page)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    # level 0 è la vista più dettagliata; ogni livello successivo dimezza.
    scale = 2**level
    width = max(1, (img.width + scale - 1) // scale)
    height = max(1, (img.height + scale - 1) // scale)
    max_x = (width + TILE_SIZE - 1) // TILE_SIZE
    max_y = (height + TILE_SIZE - 1) // TILE_SIZE
    if x >= max_x or y >= max_y:
        raise HTTPException(status_code=404, detail="tile fuori immagine")
    level_file = _level_path(page["id"], level)
    if level_file.exists():
        resized = Image.open(level_file)
    else:
        resized = img.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        level_file.parent.mkdir(parents=True, exist_ok=True)
        resized.save(level_file, "JPEG", quality=86, optimize=True)
    left, top = x * TILE_SIZE, y * TILE_SIZE
    tile = resized.crop((left, top, min(left + TILE_SIZE, width), min(top + TILE_SIZE, height)))
    out.parent.mkdir(parents=True, exist_ok=True)
    tile.save(out, "JPEG", quality=86, optimize=True)
    return out
