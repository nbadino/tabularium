"""API annotazioni (blocchi) per pagina e tassonomia etichette."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image

from ..db import connect
from ..services import auth as authsvc
from .deps import require_resource
from ..schemas import (
    BlockBulkWrite,
    BlockListOut,
    BlockOut,
    BlockUpdate,
    LabelSchemaOut,
    TableCell,
    TableCellRecognizeOut,
    TableCellRecognizeRequest,
    TableDetectOut,
    TableDetectRequest,
    TableGrid,
    TableGridOut,
    TableSaveOut,
)
from ..services import inference, labeling, otsl, table_detect
from ..services import blocks as blockssvc
from ..services import audit as auditsvc
from ..services import ocr as ocrmod
from ..services import pages as pagesvc
from ..services.i18n import msg, parse_lang

router = APIRouter(
    tags=["annotations"],
    dependencies=[Depends(authsvc.get_current_user)],
)


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
def list_annotations(
    page_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> BlockListOut:
    return blockssvc.list_page_blocks(page_id)


@router.put("/api/pages/{page_id}/annotations", response_model=BlockListOut)
def replace_annotations(
    page_id: int,
    payload: BlockBulkWrite,
    user: dict = Depends(require_resource(write=True)),
) -> BlockListOut:
    """Sincronizza i blocchi della pagina (logica in ``services/blocks.py``)."""
    return blockssvc.sync_annotations(page_id, payload, actor=user)


@router.delete("/api/pages/{page_id}/annotations")
def delete_page_annotations(
    page_id: int,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Elimina tutti i blocchi della pagina, incluse le griglie collegate."""
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        count = conn.execute(
            "SELECT COUNT(*) FROM blocks WHERE page_id=?", (page_id,)
        ).fetchone()[0]
        conn.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
        conn.execute(
            "UPDATE pages SET status='new', annotation_revision=annotation_revision+1 WHERE id=?",
            (page_id,),
        )
        auditsvc.record(conn, user, "page.annotations_deleted", resource_type="page", resource_id=page_id, payload={"deleted": int(count)})
    return {"deleted": int(count), "page_id": page_id}


@router.patch("/api/blocks/{block_id}", response_model=BlockOut)
def update_block(
    block_id: int,
    payload: BlockUpdate,
    user: dict = Depends(require_resource(write=True)),
) -> BlockOut:
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
        conn.execute(
            "UPDATE pages SET annotation_revision=annotation_revision+1 "
            "WHERE id=(SELECT page_id FROM blocks WHERE id=?)",
            (block_id,),
        )
        auditsvc.record(conn, user, "block.updated", resource_type="block", resource_id=block_id)
        out = _block_out(_get_block_or_404(conn, block_id))
        out.annotation_revision = conn.execute(
            "SELECT annotation_revision FROM pages WHERE id=?", (out.page_id,)
        ).fetchone()[0]
        return out


@router.delete("/api/blocks/{block_id}")
def delete_block(
    block_id: int,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    with connect() as conn:
        block = _get_block_or_404(conn, block_id)
        conn.execute("DELETE FROM blocks WHERE id=?", (block_id,))
        conn.execute(
            "UPDATE pages SET annotation_revision=annotation_revision+1 WHERE id=?",
            (block["page_id"],),
        )
        auditsvc.record(conn, user, "block.deleted", resource_type="block", resource_id=block_id)
    return {"deleted": True, "id": block["id"]}


@router.get("/api/projects/{project_id}/labels", response_model=LabelSchemaOut)
def project_labels(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> LabelSchemaOut:
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
def block_table_get(
    block_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> TableGridOut:
    with connect() as conn:
        _get_block_or_404(conn, block_id)
        row = conn.execute(
            "SELECT * FROM tables WHERE block_id=?", (block_id,)
        ).fetchone()
        grid = _get_table_grid(row)
        return TableGridOut(grid=TableGrid.model_validate(grid) if grid else None)


@router.put("/api/blocks/{block_id}/table", response_model=TableSaveOut)
def block_table_put(
    block_id: int,
    payload: TableGrid,
    user: dict = Depends(require_resource(write=True)),
) -> TableSaveOut:
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
    if payload.row_columns or payload.row_columns_proven:
        if len(payload.row_columns) != payload.rows:
            raise HTTPException(
                status_code=400,
                detail="row_columns: deve contenere una riga per ogni riga della griglia",
            )
        if len(payload.row_columns_proven) != payload.rows:
            raise HTTPException(
                status_code=400,
                detail="row_columns_proven: deve contenere una riga per ogni riga della griglia",
            )
        for row, proven in zip(payload.row_columns, payload.row_columns_proven):
            if len(row) != max(0, payload.cols - 1) or len(proven) != len(row):
                raise HTTPException(
                    status_code=400,
                    detail="row_columns: cardinalità dei confini interni non valida",
                )
            if any(value < 0 or value > 1 for value in row) or any(
                right <= left for left, right in zip(row, row[1:])
            ):
                raise HTTPException(
                    status_code=400,
                    detail="row_columns: i confini devono essere crescenti e compresi tra 0 e 1",
                )
    grid = {
        "rows": payload.rows,
        "cols": payload.cols,
        "cells": [c.model_dump() for c in payload.cells],
        "phantom_cols": payload.phantom_cols,
        "header_rows": payload.header_rows,
        "vlines": payload.vlines,
        "hlines": payload.hlines,
        "row_columns": payload.row_columns,
        "row_columns_proven": payload.row_columns_proven,
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
        conn.execute(
            "UPDATE pages SET annotation_revision=annotation_revision+1 "
            "WHERE id=(SELECT page_id FROM blocks WHERE id=?)",
            (block_id,),
        )
        revision = conn.execute(
            "SELECT annotation_revision FROM pages "
            "WHERE id=(SELECT page_id FROM blocks WHERE id=?)",
            (block_id,),
        ).fetchone()[0]
        auditsvc.record(conn, user, "table.updated", resource_type="block", resource_id=block_id)
    return TableSaveOut(
        grid=TableGrid.model_validate(grid),
        otsl=otsl_str,
        annotation_revision=int(revision),
    )


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
    block_id: int,
    payload: TableDetectRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
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
    prefill_source: str | None = None

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
                # Lo scorrimento serve anche qui: senza, i varchi fra parole si
                # cercano nel sistema sbagliato e il confine non piega dove deve.
                shear=float(detection.diagnostics.get("shear", 0.0)),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=msg("ocr_engine_failed", lang, engine=engine.name, exc=exc),
            ) from exc
        ocr_stats["engine"] = engine.name
        prefill_source = "ocr"

    elif payload.fill == "model":
        # Qui il modello dà struttura E contenuto: la griglia che torna è la
        # sua, non quella geometrica. I confini di riga rilevati servono solo a
        # tagliare le bande senza spezzare una riga a metà.
        cfg = inference.get_inference_config()
        if not cfg.get("enabled", True):
            raise HTTPException(
                status_code=400,
                detail="L'inferenza del modello (GPU/Cloud) è disattivata. Attivala nelle impostazioni o usa il riempimento OCR/nessuno.",
            )
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
        prefill_source = "model"

    # Il prefill non è verità: ogni cella con testo nasce non verificata, così
    # l'export distingue il testo proposto da quello confermato dall'umano. Le
    # celle senza testo non c'entrano (non c'è niente da verificare).
    if prefill_source:
        cells = [
            {
                **c,
                "source": prefill_source,
                "verified": not (c.get("text") or "").strip(),
            }
            for c in cells
        ]

    grid = TableGrid(
        rows=rows,
        cols=cols,
        cells=[TableCell.model_validate(c) for c in cells],
        phantom_cols=[],
        vlines=vlines,
        hlines=hlines,
        row_columns=(
            detection.diagnostics.get("row_columns", [])
            if payload.fill != "model" else []
        ),
        row_columns_proven=(
            detection.diagnostics.get("row_columns_proven", [])
            if payload.fill != "model" else []
        ),
    )
    return TableDetectOut(
        grid=grid,
        column_support=detection.column_support,
        warnings=detection.warnings,
        diagnostics=detection.diagnostics,
        ocr=ocr_stats,
    )


@router.post(
    "/api/blocks/{block_id}/table/cell-recognize",
    response_model=TableCellRecognizeOut,
)
def block_table_cell_recognize(
    block_id: int,
    payload: TableCellRecognizeRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> TableCellRecognizeOut:
    """Ri-riconosce una sola cella della griglia salvata.

    Non persiste nulla: il testo torna all'editor come proposta (`source=ocr`,
    `verified=false`) finché l'annotatore non la conferma. Serve quando una
    cella sola è sbagliata: rigirare tutta la tabella cancellerebbe il lavoro
    fatto — e verificato — sulle altre.
    """
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        block = _get_block_or_404(conn, block_id)
        page = conn.execute(
            "SELECT * FROM pages WHERE id=?", (block["page_id"],)
        ).fetchone()
        if page is None:
            raise HTTPException(status_code=404, detail="pagina non trovata")
        grid = _get_table_grid(
            conn.execute("SELECT * FROM tables WHERE block_id=?", (block_id,)).fetchone()
        )
    if not grid:
        raise HTTPException(
            status_code=422, detail=msg("table_grid_missing_skip", lang, id=block_id)
        )

    rows, cols = int(grid["rows"]), int(grid["cols"])
    if payload.r >= rows or payload.c >= cols:
        raise HTTPException(status_code=422, detail="cella fuori dalla griglia")

    # La cella richiesta può essere coperta da una unita: si riconosce l'estensione
    # completa del proprietario, non la sola traccia.
    owner: dict | None = None
    for cell in grid.get("cells", []):
        if (
            cell["r"] <= payload.r < cell["r"] + cell["rowspan"]
            and cell["c"] <= payload.c < cell["c"] + cell["colspan"]
        ):
            owner = cell
            break
    if owner is None:
        raise HTTPException(status_code=422, detail="cella fuori dalla griglia")

    hlines = [float(v) for v in grid.get("hlines", [])] or [
        i / rows for i in range(rows + 1)
    ]
    vlines = [float(v) for v in grid.get("vlines", [])] or [
        i / cols for i in range(cols + 1)
    ]

    image = pagesvc.crop_block_image(page, _block_bbox(block))
    width, height = image.width, image.height
    r0, r1 = owner["r"], owner["r"] + owner["rowspan"]
    c0, c1 = owner["c"], owner["c"] + owner["colspan"]
    x0 = round(vlines[c0] * width)
    x1 = round(vlines[c1] * width)
    y0 = round(hlines[r0] * height)
    y1 = round(hlines[r1] * height)

    # Stesso trattamento del prefill a griglia piena (table_detect.fill_cells):
    # i riconoscitori vogliono un po' d'aria e un'altezza tipica, altrimenti le
    # celle corte tornano quasi sempre vuote.
    diffs = sorted(b - a for a, b in zip(hlines, hlines[1:]) if b > a)
    pitch = max(8, int(round(diffs[len(diffs) // 2] * height)) if diffs else height // rows)
    pad = max(2, int(0.25 * pitch))
    target_h = max(24, int(2.5 * pitch))

    engine = ocrmod.OcrEngine()
    if not engine.available:
        raise HTTPException(status_code=400, detail=msg("ocr_unavailable", lang))
    try:
        crop = image.crop(
            (
                max(0, x0 - pad),
                max(0, y0 - pad),
                min(width, x1 + pad),
                min(height, y1 + pad),
            )
        )
        if crop.width < 2 or crop.height < 2:
            raise HTTPException(status_code=422, detail="cella troppo piccola")
        scale = target_h / crop.height
        crop = crop.resize(
            (max(8, int(crop.width * scale)), target_h), Image.LANCZOS
        )
        text, score = engine.recognize_line(crop)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail=msg("ocr_engine_failed", lang, engine=engine.name, exc=exc),
        ) from exc

    if score < payload.min_score:
        text = ""
    return TableCellRecognizeOut(text=text, score=round(score, 4), engine=engine.name)


@router.get("/api/blocks/{block_id}/crop")
def block_crop(
    block_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> Response:
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
