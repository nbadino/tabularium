"""API pseudo-labeling: generazione bozze di blocchi via OCR locale."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import connect
from ..services import inference, labeling, otsl, table_detect
from ..services import ocr as ocrmod
from ..services import pages as pagesvc
from ..services.i18n import msg, parse_lang
from ..services import auth as authsvc
from .deps import require_resource

router = APIRouter(
    tags=["prelabel"],
    dependencies=[Depends(authsvc.get_current_user)],
)


class PrelabelRequest(BaseModel):
    page_ids: list[int] = Field(min_length=1)
    mode: Literal["replace", "merge"] = "replace"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    min_size: int = Field(default=10, ge=2)
    # `ocr` trova righe di testo e le etichetta tutte `Text`: va bene su pagine
    # di prosa, male su tabelle allineate a spazi (fonde le colonne in una riga
    # sola). `model` usa MonkeyOCRv2 servito da vLLM, che restituisce blocchi
    # già classificati e riconosce le tabelle: è il percorso previsto da
    # AGENTS.md §3.2 e migliora a ogni ciclo di fine-tuning.
    engine: Literal["ocr", "model"] = "ocr"
    # END2END è ufficiale ma più lento e va confrontato sul corpus. Il default
    # resta esplicito per non cambiare silenziosamente run esistenti.
    model_mode: Literal["two_stage", "end2end"] = "two_stage"
    # Il percorso OCR vede solo righe: qui si prova a promuovere il più grande
    # cluster di righe consecutive a blocco `Table` (griglia + celle precompilate
    # via OCR). Va bene lasciarlo attivo: la promozione non è un'ipotesi, la
    # geometria la dimostra, e in caso di dubbio resta tutto `Text` come prima.
    table_promote: bool = True


def _iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _nms_lite(items: list[dict], thr: float = 0.85) -> list[dict]:
    kept: list[dict] = []
    for item in sorted(items, key=lambda x: -x["score"]):
        if any(_iou(item["bbox"], k["bbox"]) > thr for k in kept):
            continue
        kept.append(item)
    return kept


# Un blocco è una tabella se la geometria mostra una griglia regolare: almeno
# tre colonne separate e un buon numero di righe alla stessa spaziatura. Sotto
# queste soglie è un paragrafo, o una colonna di testo.
_TABLE_MIN_COLS = 3
_TABLE_MIN_ROWS = 6


def _looks_like_table(image, bbox: list[int]) -> bool:
    """Vero se il ritaglio ha la struttura di una tabella allineata a spazi."""
    x1, y1, x2, y2 = bbox
    if x2 - x1 < 50 or y2 - y1 < 50:
        return False
    try:
        detection = table_detect.detect_grid(image.crop((x1, y1, x2, y2)))
    except Exception:  # noqa: BLE001
        return False
    if detection.rows < _TABLE_MIN_ROWS or detection.cols < _TABLE_MIN_COLS:
        return False
    # I confini interni devono essere attestati su gran parte delle righe:
    # allineamenti sporadici capitano anche nella prosa giustificata.
    internal = detection.column_support[1:-1]
    if not internal:
        return False
    solid = sum(1 for s in internal if s >= 0.5 * detection.rows)
    return solid >= _TABLE_MIN_COLS - 1


def _recognize_table(client, image, bbox: list[int]) -> dict:
    """Griglia di una tabella: confini di riga dalla geometria, testo dal modello."""
    x1, y1, x2, y2 = bbox
    crop = image.crop((x1, y1, x2, y2))
    row_bounds: list[int] | None = None
    try:
        detection = table_detect.detect_grid(crop)
        row_bounds = [round(h * crop.height) for h in detection.hlines]
    except Exception:  # noqa: BLE001
        row_bounds = None
    return client.table_grid(crop, row_bounds=row_bounds)


def _table_cluster(kept: list[dict]) -> tuple[list[int], list[int]] | None:
    """Il più grande cluster verticale di righe consecutive, candidato tabella.

    L'OCR di riga non sa che è una tabella, ma le righe del registro si
    riconoscono per contiguità: il passo fra una riga e la successiva è
    dell'ordine dell'altezza di riga, mentre sopra (testata, numero del
    fascicolo) c'è un salto. Prendere il cluster più grande evita il difetto
    misurato di passare al rilevatore la pagina intera, testata compresa.
    """
    if len(kept) < _TABLE_MIN_ROWS:
        return None
    heights = sorted(b["bbox"][3] - b["bbox"][1] for b in kept)
    med_h = max(1, heights[len(heights) // 2])
    order = sorted(range(len(kept)), key=lambda i: kept[i]["bbox"][1])
    clusters: list[list[int]] = []
    for i in order:
        if clusters and kept[i]["bbox"][1] - kept[clusters[-1][-1]]["bbox"][3] <= 1.5 * med_h:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    best = max(clusters, key=len)
    if len(best) < _TABLE_MIN_ROWS:
        return None
    xs1 = [kept[i]["bbox"][0] for i in best]
    ys1 = [kept[i]["bbox"][1] for i in best]
    xs2 = [kept[i]["bbox"][2] for i in best]
    ys2 = [kept[i]["bbox"][3] for i in best]
    return [min(xs1), min(ys1), max(xs2), max(ys2)], best


def _ocr_table_grid(
    image, bbox: list[int], engine, min_score: float
) -> dict | None:
    """Griglia + celle precompilate per un blocco tabella via OCR locale.

    Struttura dal rilevatore geometrico, testo cella per cella: è il solo modo
    in cui l'OCR di riga smette di fondere le colonne. Celle con testo nascono
    `verified=False`: sono una proposta, non trascrizioni confermate.
    """
    x1, y1, x2, y2 = bbox
    crop = image.crop((x1, y1, x2, y2))
    try:
        detection = table_detect.detect_grid(crop)
        cells, _stats = table_detect.fill_cells(
            crop,
            detection.vlines,
            detection.hlines,
            engine,
            pitch=int(detection.diagnostics.get("pitch_px", 20)),
            min_score=min_score,
        )
    except Exception:  # noqa: BLE001
        return None
    cells = [
        {
            **c,
            "source": "ocr" if (c.get("text") or "").strip() else "manual",
            "verified": not (c.get("text") or "").strip(),
        }
        for c in cells
    ]
    return {
        "rows": detection.rows,
        "cols": detection.cols,
        "cells": cells,
        "phantom_cols": [],
        "vlines": detection.vlines,
        "hlines": detection.hlines,
    }


def _prelabel_with_model(
    project_id: int, payload: PrelabelRequest, client, lang: str
) -> dict:
    """Bozze prodotte dal modello base MonkeyOCRv2 servito da vLLM.

    A differenza dell'OCR di riga, il modello restituisce blocchi **già
    classificati** e nell'ordine di lettura che propone: una pagina indice esce
    con il suo blocco `Table` invece che con centinaia di righe `Text`. Le
    coordinate arrivano normalizzate 0–1000 e vengono riportate a pixel pagina.
    """
    known = {label.name for label in labeling.DEFAULT_LABELS}
    results = []
    with connect() as conn:
        for pid in payload.page_ids:
            page = conn.execute(
                "SELECT * FROM pages WHERE id=? AND project_id=?", (pid, project_id)
            ).fetchone()
            if page is None:
                raise HTTPException(status_code=404, detail=msg("page_not_in_project", lang, id=pid))
            try:
                image, applied_angle = pagesvc.maybe_auto_deskew(page)
            except HTTPException as exc:
                results.append({"page_id": pid, "detected": 0, "inserted": 0, "error": exc.detail})
                continue
            if image is None:
                results.append({"page_id": pid, "detected": 0, "inserted": 0, "error": msg("image_not_available", lang)})
                continue

            try:
                items = (
                    client.end2end(image)
                    if payload.model_mode == "end2end"
                    else client.layout(image)
                )
            except RuntimeError as exc:
                results.append({"page_id": pid, "detected": 0, "inserted": 0, "error": str(exc)})
                continue

            width, height = page["width"], page["height"]
            kept = []
            for item in items:
                x1, y1, x2, y2 = item["bbox"]
                bbox = [
                    max(0, min(round(x1 / 1000 * width), width)),
                    max(0, min(round(y1 / 1000 * height), height)),
                    max(0, min(round(x2 / 1000 * width), width)),
                    max(0, min(round(y2 / 1000 * height), height)),
                ]
                if bbox[2] - bbox[0] < payload.min_size or bbox[3] - bbox[1] < payload.min_size:
                    continue
                label = item["label"] if item["label"] in known else "Text"
                kept.append(
                    {
                        "bbox": bbox,
                        "label": label,
                        "content": str(item.get("content") or "").strip(),
                    }
                )

            # Il modello base etichetta `Text` il registro di una pagina indice:
            # riconoscere una tabella *senza filetti* è esattamente ciò che il
            # fine-tuning deve insegnargli. Nel frattempo la decisione non serve
            # chiederla al modello: la geometria la dimostra. Un blocco con una
            # griglia regolare di più colonne attestate su molte righe è una
            # tabella, e viene promosso prima del riconoscimento.
            tables = 0
            for k in kept:
                if k["label"] in ("Table", "Text") and _looks_like_table(image, k["bbox"]):
                    k["label"] = "Table"
                    tables += 1

            if payload.mode == "replace":
                conn.execute("DELETE FROM blocks WHERE page_id=?", (pid,))
            grids = 0
            for i, k in enumerate(kept, start=1):
                cur = conn.execute(
                    "INSERT INTO blocks (page_id, label, kind, points, content, order_idx, "
                    "prefill_source, confirmed) VALUES (?,?,?,?,?,?,?,0)",
                    (
                        pid,
                        k["label"],
                        "rect",
                        json.dumps([[k["bbox"][0], k["bbox"][1]], [k["bbox"][2], k["bbox"][3]]]),
                        k.get("content", ""),
                        i,
                        f"model:{client.model}:{payload.model_mode}",
                    ),
                )
                if k["label"] != "Table":
                    continue
                # END2END può già contenere OTSL. Se non è valido (o il layout
                # ha promosso geometricamente un Text a Table), si ricade sul
                # riconoscimento del crop. Il fallback è misurabile e dichiarato
                # nel risultato, non una sostituzione silenziosa.
                grid = None
                used_end2end_content = False
                if payload.model_mode == "end2end" and k.get("content"):
                    try:
                        raw_content = k["content"]
                        # END2END segue il formato ufficiale di parsing: per
                        # le tabelle il contenuto è spesso HTML, non OTSL.
                        # Convertiamo prima HTML→OTSL; altrimenti il parser
                        # OTSL leggerebbe tutti i tag HTML come una sola riga.
                        candidate_content = (
                            otsl.html_to_otsl(raw_content)
                            if "<table" in raw_content.lower()
                            else raw_content
                        )
                        candidate = otsl.otsl_to_grid(candidate_content)
                        if candidate.get("rows") and candidate.get("cols"):
                            grid = candidate
                            used_end2end_content = True
                    except Exception:  # noqa: BLE001
                        grid = None
                try:
                    if grid is None:
                        grid = _recognize_table(client, image, k["bbox"])
                except Exception as exc:  # noqa: BLE001
                    results_error = f"{k['bbox']}: {exc}"
                    kept[i - 1]["error"] = results_error
                    continue
                if grid["rows"]:
                    conn.execute(
                        "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
                        "ON CONFLICT(block_id) DO UPDATE SET grid_json=excluded.grid_json, "
                        "updated_at=datetime('now')",
                        (cur.lastrowid, json.dumps(grid)),
                    )
                    grids += 1
                    k["table_source"] = (
                        "end2end" if used_end2end_content else "table_crop_fallback"
                    )

            results.append(
                {
                    "page_id": pid,
                    "detected": len(items),
                    "inserted": len(kept),
                    "tables": tables,
                    "grids": grids,
                    "mode": payload.mode,
                    "model_mode": payload.model_mode,
                    "table_sources": [
                        item.get("table_source")
                        for item in kept
                        if item.get("table_source")
                    ],
                    "deskew_angle": applied_angle,
                }
            )
    return {"engine": "model", "model": client.model, "results": results}


@router.post("/api/projects/{project_id}/prelabel")
def prelabel(
    project_id: int,
    payload: PrelabelRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    lang = parse_lang(request.headers.get("accept-language"))

    with connect() as conn:
        _get_project_or_404(conn, project_id)

    if payload.engine == "model":
        cfg = inference.get_inference_config()
        if not cfg.get("enabled", True):
            raise HTTPException(
                status_code=400,
                detail="L'inferenza del modello (GPU/Cloud) è disattivata. Attivala nelle impostazioni o usa l'OCR locale.",
            )
        client = inference.get_vllm_client()
        if not client.ping():
            raise HTTPException(
                status_code=400, detail=msg("model_unavailable", lang, url=client.url)
            )
        return _prelabel_with_model(project_id, payload, client, lang)

    engine = ocrmod.OcrEngine()
    if not engine.available:
        raise HTTPException(
            status_code=400,
            detail=msg("ocr_unavailable", lang),
        )

    results = []
    with connect() as conn:
        for pid in payload.page_ids:
            page = conn.execute(
                "SELECT * FROM pages WHERE id=? AND project_id=?", (pid, project_id)
            ).fetchone()
            if page is None:
                raise HTTPException(status_code=404, detail=msg("page_not_in_project", lang, id=pid))
            try:
                image, applied_angle = pagesvc.maybe_auto_deskew(page)
            except HTTPException as exc:
                results.append({"page_id": pid, "detected": 0, "inserted": 0, "error": exc.detail})
                continue
            if image is None:
                results.append({"page_id": pid, "detected": 0, "inserted": 0, "error": msg("image_not_available", lang)})
                continue

            try:
                detections = engine.detect(image)
            except Exception as exc:  # noqa: BLE001
                # Il motore è installato ma non parte (versione di libreria
                # incompatibile, modelli corrotti, runtime mancante). Meglio
                # dirlo con il nome del motore che restituire un 500 muto.
                raise HTTPException(
                    status_code=400,
                    detail=msg("ocr_engine_failed", lang, engine=engine.name, exc=exc),
                ) from exc
            kept = []
            for d in detections:
                if d["score"] < payload.confidence:
                    continue
                bbox = d["bbox"]
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                if w < payload.min_size or h < payload.min_size:
                    continue
                bbox = [
                    max(0, min(round(bbox[0]), page["width"])),
                    max(0, min(round(bbox[1]), page["height"])),
                    max(0, min(round(bbox[2]), page["width"])),
                    max(0, min(round(bbox[3]), page["height"])),
                ]
                if bbox[2] - bbox[0] < payload.min_size or bbox[3] - bbox[1] < payload.min_size:
                    continue
                kept.append({"bbox": bbox, "text": d["text"].strip(), "score": d["score"]})
            kept = _nms_lite(kept)

            # Promozione a tabella: il più grande cluster di righe consecutive
            # che la geometria dimostra essere una griglia diventa un blocco
            # `Table` con celle precompilate; le righe fuori dal cluster
            # (testata, numero del fascicolo) restano `Text`.
            table_bbox: list[int] | None = None
            if payload.table_promote:
                candidate = _table_cluster(kept)
                if candidate and _looks_like_table(image, candidate[0]):
                    table_bbox, consumed = candidate
                    consumed_set = set(consumed)
                    kept = [k for i, k in enumerate(kept) if i not in consumed_set]

            outputs: list[dict] = [
                {
                    "label": "Text",
                    "bbox": k["bbox"],
                    "content": k["text"],
                    "prefill": f"{engine.name}:{k['score']:.4f}",
                }
                for k in kept
            ]
            if table_bbox:
                outputs.append(
                    {
                        "label": "Table",
                        "bbox": table_bbox,
                        "content": "",
                        "prefill": f"{engine.name}:table",
                    }
                )
            # Ordine di lettura: dall'alto verso il basso (e da sinistra), la
            # stessa convenzione dell'annotazione manuale.
            outputs.sort(key=lambda o: (o["bbox"][1], o["bbox"][0]))

            if payload.mode == "replace":
                conn.execute("DELETE FROM blocks WHERE page_id=?", (pid,))
            grids = 0
            for i, o in enumerate(outputs, start=1):
                cur = conn.execute(
                    "INSERT INTO blocks (page_id, label, kind, points, content, order_idx, "
                    "prefill_source, confirmed) VALUES (?,?,?,?,?,?,?,0)",
                    (
                        pid,
                        o["label"],
                        "rect",
                        json.dumps(
                            [[o["bbox"][0], o["bbox"][1]], [o["bbox"][2], o["bbox"][3]]]
                        ),
                        o["content"],
                        i,
                        o["prefill"],
                    ),
                )
                if o["label"] != "Table":
                    continue
                grid = _ocr_table_grid(image, o["bbox"], engine, min_score=0.0)
                if grid:
                    conn.execute(
                        "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
                        "ON CONFLICT(block_id) DO UPDATE SET grid_json=excluded.grid_json, "
                        "updated_at=datetime('now')",
                        (cur.lastrowid, json.dumps(grid)),
                    )
                    grids += 1
            results.append(
                {
                    "page_id": pid,
                    "detected": len(detections),
                    "inserted": len(outputs),
                    "tables": 1 if table_bbox else 0,
                    "grids": grids,
                    "mode": payload.mode,
                    "deskew_angle": applied_angle,
                }
            )

    return {"engine": engine.name, "results": results}
