"""Helper per le pagine: percorsi anteprime, generazione on-demand."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

from .. import config
from . import images as imgmod
from . import scan as scanmod

THUMB_SIDE = 512
PREVIEW_SIDE = 1600
TILE_SIZE = 512
TRANSFORM_VERSION = 4


def thumb_dir() -> Path:
    return config.ROOT_DIR / "thumbs"


def thumb_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}.jpg"


def preview_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_preview.jpg"


def pdf_image_path(page_id: int) -> Path:
    """PNG materializzato all'import del PDF, sorgente veloce e lossless."""
    return thumb_dir() / f"p{page_id}_source.png"


def original_preview_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_original_preview.jpg"


def candidate_preview_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_candidate_preview.jpg"


def deskew_path(page_id: int) -> Path:
    """Master trasformato lossless usato da canvas, OCR, crop ed export."""
    return thumb_dir() / f"p{page_id}_transform.png"


def legacy_deskew_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_deskew.jpg"


def candidate_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_candidate.png"


def transform_meta_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_deskew.json"


def candidate_meta_path(page_id: int) -> Path:
    return thumb_dir() / f"p{page_id}_candidate.json"


def mark_transform(
    page_id: int,
    level: str,
    size: tuple[int, int],
    *,
    path: Path | None = None,
    details: dict | None = None,
) -> dict:
    path = path or transform_meta_path(page_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "version": TRANSFORM_VERSION,
        "level": level,
        "engine": level,
        "size": list(size),
        "created_at": datetime.now(UTC).isoformat(),
        **(details or {}),
    }
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def read_transform_meta(page_id: int, *, candidate: bool = False) -> dict | None:
    path = candidate_meta_path(page_id) if candidate else transform_meta_path(page_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _active_transform_path(page_id: int) -> Path | None:
    current = deskew_path(page_id)
    if current.exists():
        return current
    legacy = legacy_deskew_path(page_id)
    return legacy if legacy.exists() else None


def _has_invalid_transform(page: sqlite3.Row) -> bool:
    """Riconosce trasformazioni derivate da una tela diversa dall'originale."""
    transformed = _active_transform_path(page["id"])
    metadata = transform_meta_path(page["id"])
    if transformed is None:
        return False
    try:
        details = json.loads(metadata.read_text(encoding="utf-8"))
        if details.get("version") not in {3, TRANSFORM_VERSION}:
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
    legacy_deskew_path(page["id"]).unlink(missing_ok=True)
    transform_meta_path(page["id"]).unlink(missing_ok=True)
    thumb_path(page["id"]).unlink(missing_ok=True)
    preview_path(page["id"]).unlink(missing_ok=True)
    import shutil

    shutil.rmtree(config.ROOT_DIR / "tiles" / f"p{page['id']}", ignore_errors=True)


def source_missing(page: sqlite3.Row) -> HTTPException:
    """404 che dice *quale* file manca.

    I percorsi delle pagine sono assoluti nel database: rinominare o spostare
    la cartella del progetto li invalida tutti in un colpo, e un «file sorgente
    non presente» nudo costringe a interrogare il database per capirlo. Il path
    atteso rende la diagnosi immediata.
    """
    try:
        expected = page["abs_path"]
    except (KeyError, IndexError):
        expected = None
    detail = "file sorgente non presente"
    if expected:
        detail = f"{detail}: {expected}"
    return HTTPException(status_code=404, detail=detail)


def _load_source_image(page: sqlite3.Row, use_deskew: bool = True) -> Image.Image | None:
    """Ritorna l'immagine sorgente della pagina (deskewata se attiva, altrimenti
    image originale o pdf+indice)."""
    if use_deskew:
        d = _active_transform_path(page["id"])
        if d is not None and not _has_invalid_transform(page):
            return Image.open(d)
    src = page["abs_path"]
    if not Path(src).exists():
        return None
    if page["source_kind"] == "pdf":
        # Il nome è deterministico: consente di riutilizzare e completare
        # anche le pagine importate prima della materializzazione PNG.
        cached = pdf_image_path(int(page["id"]))
        if cached.exists():
            return Image.open(cached)
        try:
            extracted = json.loads(page["meta_json"] or "{}").get("extracted_path")
            if extracted and Path(extracted).exists():
                return Image.open(extracted)
        except (TypeError, ValueError, OSError):
            pass
        idx = page["pdf_page"] or 0
        rendered = scanmod.render_pdf_page(src, idx)
        if rendered is None:
            return None
        image = rendered[0]
        # Materializzazione lazy per i PDF già presenti nel database: la prima
        # apertura converte solo quella pagina, senza rileggere il documento.
        cached.parent.mkdir(parents=True, exist_ok=True)
        image.save(cached, format="PNG", optimize=True)
        return Image.open(cached)
    return Image.open(src)


def load_source_image(page: sqlite3.Row) -> Image.Image | None:
    """Wrapper pubblico: immagine sorgente della pagina (preferisce il deskew)."""
    return _load_source_image(page)


def load_original_source_image(page: sqlite3.Row) -> Image.Image | None:
    """Immagine ORIGINALE (senza deskew), per rigenerare il deskew."""
    return _load_source_image(page, use_deskew=False)


def maybe_auto_deskew(page: sqlite3.Row, threshold: float = 0.10) -> tuple[Image.Image, float]:
    """Usa il master accettato; solo in sua assenza applica l'auto-deskew.

    È intenzionale non ripartire mai dall'originale quando esiste un dewarp:
    prefill, crop ed export devono osservare gli stessi pixel del canvas.
    """
    import shutil

    from . import images as imgmod

    active = _active_transform_path(page["id"])
    if active is not None and not _has_invalid_transform(page):
        return Image.open(active), 0.0
    img = _load_source_image(page, use_deskew=False)
    if img is None:
        raise source_missing(page)
    aligned, angle = imgmod.deskew(img)
    if abs(angle) >= threshold:
        desk = deskew_path(page["id"])
        desk.parent.mkdir(parents=True, exist_ok=True)
        aligned.convert("RGB").save(desk, "PNG", compress_level=4)
        mark_transform(
            page["id"],
            "deskew",
            aligned.size,
            details={"requested_engine": "auto", "actual_engine": "deskew", "angle": angle},
        )
        for p in (thumb_path(page["id"]), preview_path(page["id"])):
            if p.exists():
                p.unlink(missing_ok=True)
        tiles = config.ROOT_DIR / "tiles" / f"p{page['id']}"
        if tiles.exists():
            shutil.rmtree(tiles, ignore_errors=True)
    return aligned, angle


def ensure_transform_preview(page: sqlite3.Row, kind: str) -> Path:
    """Anteprima JPEG derivata; i master originali/trasformati restano lossless."""
    if kind == "original":
        expected = original_preview_path(page["id"])
        image = load_original_source_image(page)
    elif kind == "candidate":
        expected = candidate_preview_path(page["id"])
        source = candidate_path(page["id"])
        image = Image.open(source) if source.exists() else None
    else:
        raise HTTPException(status_code=400, detail="transform_preview_kind_invalid")
    if expected.exists():
        return expected
    if image is None:
        raise HTTPException(status_code=404, detail="transform_preview_missing")
    expected.parent.mkdir(parents=True, exist_ok=True)
    preview = image.convert("RGB")
    preview.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.Resampling.LANCZOS)
    preview.save(expected, "JPEG", quality=90)
    return expected


def clear_candidate(page_id: int) -> None:
    candidate_path(page_id).unlink(missing_ok=True)
    candidate_meta_path(page_id).unlink(missing_ok=True)
    candidate_preview_path(page_id).unlink(missing_ok=True)


def ensure_thumbnail(conn: sqlite3.Connection, page: sqlite3.Row) -> Path:
    """Ritorna (generate se mancante) la thumbnail JPEG della pagina."""
    _purge_invalid_transform(page)
    expected = thumb_path(page["id"])
    if expected.exists():
        return expected
    img = _load_source_image(page)
    if img is None:
        raise source_missing(page)
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
        raise source_missing(page)
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
        raise source_missing(page)
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
        raise source_missing(page)
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


def compute_readiness(conn, page_id: int, lang: str = "it") -> dict:
    """Passaggi di annotazione completati per una pagina e blocco all'approvazione.

    È la definizione di «pagina pronta» condivisa da readiness, approvazione
    e coda di annotazione: struttura (blocchi con geometria), contenuto
    (trascrizioni), tabelle (griglia OTSL valida) e revisione. Il router la
    espone via HTTP; la logica sta qui.
    """
    from . import otsl as otslmod  # noqa: PLC0415
    from .i18n import msg  # noqa: PLC0415

    page = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    if page is None:
        raise HTTPException(status_code=404, detail="pagina non trovata")
    blocks = conn.execute("SELECT * FROM blocks WHERE page_id=? ORDER BY id", (page_id,)).fetchall()
    errors: list[str] = []
    warnings: list[str] = []
    content_ok = True
    tables_ok = True
    geometry_ok = True
    if not blocks:
        errors.append(msg("no_blocks", lang))
    for block in blocks:
        try:
            points = json.loads(block["points"] or "[]")
        except (TypeError, ValueError):
            points = []
        if len(points) < 2:
            geometry_ok = False
            errors.append(msg("geometry_missing", lang, id=block["id"]))
        if not block["confirmed"]:
            # Una pseudo-label non confermata non è ground truth. Lasciarla
            # come semplice warning consentiva di approvare e poi esportare
            # direttamente predizioni grezze del modello/OCR.
            errors.append(msg("not_confirmed", lang, id=block["id"]))
        label = block["label"]
        if label in {"Text", "Headline", "Byline", "Issue-header", "Advertisement", "Note/Adder"} and not (block["content"] or "").strip():
            content_ok = False
            errors.append(msg("empty_transcription", lang, id=block["id"]))
        if label == "Table":
            row = conn.execute("SELECT grid_json FROM tables WHERE block_id=?", (block["id"],)).fetchone()
            if not row:
                tables_ok = False
                errors.append(msg("table_grid_missing", lang, id=block["id"]))
            else:
                try:
                    grid = json.loads(row["grid_json"] or "{}")
                    otslmod.grid_to_otsl(grid)
                except (TypeError, ValueError) as exc:
                    tables_ok = False
                    errors.append(msg("table_grid_invalid", lang, id=block["id"], exc=exc))
    structure_ok = bool(blocks) and geometry_ok
    return {
        "page_id": page_id, "status": page["status"], "ready": not errors,
        "errors": errors, "warnings": warnings,
        "stages": {
            "structure": structure_ok,
            "content": structure_ok and content_ok,
            "table": structure_ok and tables_ok,
            "review": not errors and not warnings,
        },
    }
