"""API annotazioni (blocchi) per pagina e tassonomia etichette."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response

from ..db import connect
from ..schemas import (
    BlockBulkWrite,
    BlockListOut,
    BlockOut,
    BlockUpdate,
    LabelSchemaOut,
    TableCell,
    TableDetectOut,
    TableDetectRequest,
    TableGrid,
    TableGridOut,
    TableSaveOut,
)
from ..services import inference, labeling, otsl, table_detect
from ..services import ocr as ocrmod
from ..services import pages as pagesvc
from ..services.i18n import msg, parse_lang

router = APIRouter(tags=["annotations"])


def _get_page_or_404(conn, page_id: int):
    row = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="pagina non trovata")
    return row


def _get_block_or_404(conn, block_id: int):
    row = conn.execute("SELECT * FROM blocks WHERE id=?", (block_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="blocco non trovato")
    return row


def _block_out(row) -> BlockOut:
    try:
        points = json.loads(row["points"] or "[]")
    except (TypeError, ValueError):
        points = []
    return BlockOut(
        id=row["id"],
        page_id=row["page_id"],
        label=row["label"],
        kind=row["kind"],
        points=[[float(px), float(py)] for px, py in points],
        content=row["content"],
        order_idx=row["order_idx"],
        confirmed=bool(row["confirmed"]),
        prefill_source=row["prefill_source"],
        updated_at=row["updated_at"],
    )


@router.get("/api/pages/{page_id}/annotations", response_model=BlockListOut)
def list_annotations(page_id: int) -> BlockListOut:
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        rows = conn.execute(
            "SELECT * FROM blocks WHERE page_id=? "
            "ORDER BY COALESCE(order_idx, 2147483647), id",
            (page_id,),
        ).fetchall()
        return BlockListOut(items=[_block_out(r) for r in rows])


@router.put("/api/pages/{page_id}/annotations", response_model=BlockListOut)
def replace_annotations(page_id: int, payload: BlockBulkWrite) -> BlockListOut:
    """Sincronizza i blocchi della pagina preservando gli ID esistenti.

    Le tabelle sono legate a ``blocks.id`` con ``ON DELETE CASCADE``. Il
    precedente delete+insert cancellava quindi ogni griglia al successivo
    autosave del canvas, anche quando il blocco Table era ancora presente.
    """
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        existing = {
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM blocks WHERE page_id=?", (page_id,)
            ).fetchall()
        }
        kept: set[int] = set()
        for item in payload.items:
            values = (
                item.label,
                item.kind,
                json.dumps(item.points),
                item.content,
                item.order_idx,
                1 if item.confirmed else 0,
            )
            if item.id is not None:
                if item.id not in existing:
                    raise HTTPException(
                        status_code=409,
                        detail=f"blocco {item.id} non appartiene alla pagina {page_id}",
                    )
                conn.execute(
                    "UPDATE blocks SET label=?, kind=?, points=?, content=?, "
                    "order_idx=?, confirmed=?, updated_at=datetime('now') WHERE id=?",
                    (*values, item.id),
                )
                kept.add(item.id)
            else:
                cursor = conn.execute(
                    "INSERT INTO blocks "
                    "(page_id, label, kind, points, content, order_idx, confirmed) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (page_id, *values),
                )
                kept.add(int(cursor.lastrowid))
        removed = existing - kept
        if removed:
            placeholders = ",".join("?" for _ in removed)
            conn.execute(
                f"DELETE FROM blocks WHERE id IN ({placeholders})", sorted(removed)
            )
        conn.execute(
            "UPDATE pages SET status='annotated' WHERE id=? AND status='new'",
            (page_id,),
        )
        rows = conn.execute(
            "SELECT * FROM blocks WHERE page_id=? "
            "ORDER BY COALESCE(order_idx, 2147483647), id",
            (page_id,),
        ).fetchall()
        return BlockListOut(items=[_block_out(r) for r in rows])


@router.patch("/api/blocks/{block_id}", response_model=BlockOut)
def update_block(block_id: int, payload: BlockUpdate) -> BlockOut:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="nessun campo da aggiornare")
    if "points" in updates:
        updates["points"] = json.dumps(updates["points"])
    if "confirmed" in updates:
        updates["confirmed"] = 1 if updates["confirmed"] else 0
    sets = ", ".join(f"{k}=?" for k in updates)
    with connect() as conn:
        _get_block_or_404(conn, block_id)
        conn.execute(
            f"UPDATE blocks SET {sets}, updated_at=datetime('now') WHERE id=?",
            (*updates.values(), block_id),
        )
        return _block_out(_get_block_or_404(conn, block_id))


@router.delete("/api/blocks/{block_id}")
def delete_block(block_id: int) -> dict:
    with connect() as conn:
        block = _get_block_or_404(conn, block_id)
        conn.execute("DELETE FROM blocks WHERE id=?", (block_id,))
    return {"deleted": True, "id": block["id"]}


@router.get("/api/projects/{project_id}/labels", response_model=LabelSchemaOut)
def project_labels(project_id: int) -> LabelSchemaOut:
    # Verifica che il progetto esista (endpoint attualmente indipendente da settings).
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return labeling.default_schema()


# --- Tabelle ------------------------------------------------------------------
def _get_table_grid(row) -> dict | None:
    if row is None:
        return None
    try:
        grid = json.loads(row["grid_json"] or "{}")
    except (TypeError, ValueError):
        return None
    return grid if grid else None


@router.get("/api/blocks/{block_id}/table", response_model=TableGridOut)
def block_table_get(block_id: int) -> TableGridOut:
    with connect() as conn:
        _get_block_or_404(conn, block_id)
        row = conn.execute(
            "SELECT * FROM tables WHERE block_id=?", (block_id,)
        ).fetchone()
        grid = _get_table_grid(row)
        return TableGridOut(grid=TableGrid.model_validate(grid) if grid else None)


@router.put("/api/blocks/{block_id}/table", response_model=TableSaveOut)
def block_table_put(block_id: int, payload: TableGrid) -> TableSaveOut:
    for name, lines, expected in (
        ("vlines", payload.vlines, payload.cols + 1),
        ("hlines", payload.hlines, payload.rows + 1),
    ):
        if not lines:
            continue
        if len(lines) != expected:
            raise HTTPException(
                status_code=400, detail=f"{name}: attesi {expected} confini"
            )
        if any(value < 0 or value > 1 for value in lines) or any(
            right <= left for left, right in zip(lines, lines[1:])
        ):
            raise HTTPException(
                status_code=400,
                detail=f"{name}: i confini devono essere crescenti e compresi tra 0 e 1",
            )
    grid = {
        "rows": payload.rows,
        "cols": payload.cols,
        "cells": [c.model_dump() for c in payload.cells],
        "phantom_cols": payload.phantom_cols,
        "vlines": payload.vlines,
        "hlines": payload.hlines,
    }
    try:
        otsl_str = otsl.grid_to_otsl(grid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with connect() as conn:
        _get_block_or_404(conn, block_id)
        conn.execute(
            "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
            "ON CONFLICT(block_id) DO UPDATE SET "
            "grid_json=excluded.grid_json, updated_at=datetime('now')",
            (block_id, json.dumps(grid)),
        )
    return TableSaveOut(grid=TableGrid.model_validate(grid), otsl=otsl_str)


def _block_bbox(block) -> tuple[int, int, int, int]:
    try:
        points = json.loads(block["points"] or "[]")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="blocco senza punti validi") from exc
    if not points:
        raise HTTPException(status_code=400, detail="blocco senza punti")
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


@router.post("/api/blocks/{block_id}/table/detect", response_model=TableDetectOut)
def block_table_detect(
    block_id: int, payload: TableDetectRequest, request: Request
) -> TableDetectOut:
    """Propone righe e colonne del blocco tabella a partire dalla geometria.

    Non salva nulla: l'esito torna all'editor come bozza, che l'utente accetta,
    corregge o scarta. `column_support` dice su quante righe ogni confine è
    attestato, così si vede subito quali reggono e quali vanno controllati.
    """
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        block = _get_block_or_404(conn, block_id)
        page = conn.execute(
            "SELECT * FROM pages WHERE id=?", (block["page_id"],)
        ).fetchone()
        if page is None:
            raise HTTPException(status_code=404, detail="pagina non trovata")
        image = pagesvc.crop_block_image(page, _block_bbox(block))

    try:
        detection = table_detect.detect_grid(
            image,
            min_support=payload.min_support,
            suppress_leaders=payload.suppress_leaders,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rows, cols = detection.rows, detection.cols
    vlines, hlines = detection.vlines, detection.hlines
    cells = table_detect.empty_cells(rows, cols)
    ocr_stats: dict | None = None

    if payload.fill == "ocr":
        engine = ocrmod.OcrEngine()
        if not engine.available:
            raise HTTPException(status_code=400, detail=msg("ocr_unavailable", lang))
        try:
            cells, ocr_stats = table_detect.fill_cells(
                image,
                vlines,
                hlines,
                engine,
                pitch=int(detection.diagnostics.get("pitch_px", 20)),
                min_score=payload.min_score,
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=msg("ocr_engine_failed", lang, engine=engine.name, exc=exc),
            ) from exc
        ocr_stats["engine"] = engine.name

    elif payload.fill == "model":
        # Qui il modello dà struttura E contenuto: la griglia che torna è la
        # sua, non quella geometrica. I confini di riga rilevati servono solo a
        # tagliare le bande senza spezzare una riga a metà.
        client = inference.get_vllm_client()
        if not client.ping():
            raise HTTPException(
                status_code=400, detail=msg("model_unavailable", lang, url=client.url)
            )
        try:
            grid_dict = client.table_grid(
                image,
                row_bounds=[round(h * image.height) for h in hlines],
                rows_per_band=payload.rows_per_band,
                header_rows=payload.header_rows,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if grid_dict["rows"]:
            rows, cols = grid_dict["rows"], grid_dict["cols"]
            cells = grid_dict["cells"]
            # Le righe del modello non coincidono con quelle geometriche: i
            # filetti tornano a spaziatura uniforme finché l'utente non li muove.
            vlines = [i / cols for i in range(cols + 1)]
            hlines = [i / rows for i in range(rows + 1)]
        filled = sum(1 for c in cells if (c.get("text") or "").strip())
        ocr_stats = {
            "engine": f"model:{client.model}",
            "cells": len(cells),
            "filled": filled,
            "blank": len(cells) - filled,
            "below_threshold": 0,
            "mean_score": 0.0,
        }

    grid = TableGrid(
        rows=rows,
        cols=cols,
        cells=[TableCell.model_validate(c) for c in cells],
        phantom_cols=[],
        vlines=vlines,
        hlines=hlines,
    )
    return TableDetectOut(
        grid=grid,
        column_support=detection.column_support,
        diagnostics=detection.diagnostics,
        ocr=ocr_stats,
    )


@router.get("/api/blocks/{block_id}/crop")
def block_crop(block_id: int) -> Response:
    """Crop della regione del blocco (pixel pagina) come JPEG in memoria."""
    with connect() as conn:
        block = _get_block_or_404(conn, block_id)
        page = conn.execute(
            "SELECT * FROM pages WHERE id=?", (block["page_id"],)
        ).fetchone()
        if page is None:
            raise HTTPException(status_code=404, detail="pagina non trovata")
        data = pagesvc.crop_block_jpeg(page, _block_bbox(block))
    return Response(content=data, media_type="image/jpeg")
