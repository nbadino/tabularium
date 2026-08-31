"""Orchestrazione del prefill (pseudo-labeling): la logica, non l'HTTP.

Il router (``api/prelabel.py``) resta la porta: autorizzazione, schema della
richiesta e scelta del motore. Qui sta il resto — filtro delle detection,
promozione a tabella, sostituzione dei blocchi esistenti, scrittura delle
griglie — così che il caso d'uso sia leggibile e testabile senza il server.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from fastapi import HTTPException

from ..db import connect
from . import labeling, ocr as ocrmod, otsl, table_detect
from . import pages as pagesvc
from .i18n import msg


@dataclass
class PrelabelOptions:
    """Le decisioni che l'utente prende nel dialog di prefill."""

    mode: str = "replace_all"
    confidence: float = 0.5
    min_size: int = 10
    # Il percorso nativo evita un secondo giro quando il modello restituisce
    # già struttura e contenuto (Unlimited-OCR, MonkeyOCRv2 END2END).
    model_mode: str = "native"
    table_promote: bool = True


def _replaced_blocks(conn, page_id: int, mode: str) -> dict:
    """Cancella i blocchi esistenti secondo la modalità richiesta.

    Restituisce quanto è stato rimosso (blocchi e griglie tabellari collegate,
    che seguono via ``ON DELETE CASCADE``) così che la risposta possa dichiarare
    l'entità della sostituzione invece di cancellare in silenzio.
    """
    if mode == "merge":
        return {"blocks": 0, "grids": 0}
    if mode == "replace_drafts":
        # Solo le bozze del prefill: un blocco generato dall'OCR/modello e non
        # ancora confermato. Il lavoro umano (blocchi disegnati a mano o
        # confermati) non è mai una bozza, qualunque sia la sua provenienza.
        rows = conn.execute(
            "SELECT id FROM blocks WHERE page_id=? AND prefill_source IS NOT NULL "
            "AND confirmed=0",
            (page_id,),
        ).fetchall()
    else:  # replace / replace_all
        rows = conn.execute(
            "SELECT id FROM blocks WHERE page_id=?", (page_id,)
        ).fetchall()
    ids = [row["id"] for row in rows]
    if not ids:
        return {"blocks": 0, "grids": 0}
    placeholders = ",".join("?" for _ in ids)
    grids = conn.execute(
        f"SELECT COUNT(*) FROM tables WHERE block_id IN ({placeholders})", ids
    ).fetchone()[0]
    conn.execute(f"DELETE FROM blocks WHERE id IN ({placeholders})", ids)
    return {"blocks": len(ids), "grids": int(grids)}


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


def _recognize_table(client, image, bbox: list[int], on_delta=None, cancel_event=None) -> dict:
    """Griglia di una tabella: confini di riga dalla geometria, testo dal modello."""
    x1, y1, x2, y2 = bbox
    crop = image.crop((x1, y1, x2, y2))
    row_bounds: list[int] | None = None
    try:
        detection = table_detect.detect_grid(crop)
        row_bounds = [round(h * crop.height) for h in detection.hlines]
    except Exception:  # noqa: BLE001
        row_bounds = None
    return client.table_grid(crop, row_bounds=row_bounds, on_delta=on_delta, cancel_event=cancel_event)


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


def _scale_bbox(item: dict, width: int, height: int) -> list[int]:
    """bbox normalizzato 0–1000 → pixel pagina, clamped ai bordi."""
    x1, y1, x2, y2 = item["bbox"]
    return [
        max(0, min(round(x1 / 1000 * width), width)),
        max(0, min(round(y1 / 1000 * height), height)),
        max(0, min(round(x2 / 1000 * width), width)),
        max(0, min(round(y2 / 1000 * height), height)),
    ]


def _dedupe_model_items(items: list[dict], iou_threshold: float = 0.92) -> list[dict]:
    """Rimuove duplicati evidenti dalla lista generata dal modello.

    Alcuni server/vLLM possono ripetere lo stesso record durante una
    generazione end2end lunga. Non è corretto creare decine di bozze identiche
    né mostrarle come blocchi distinti: conserviamo il primo record nell'ordine
    di lettura e lasciamo intatti i blocchi realmente diversi.
    """
    kept: list[dict] = []
    for item in items:
        bbox = item.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            kept.append(item)
            continue
        try:
            box = [float(value) for value in bbox]
        except (TypeError, ValueError):
            kept.append(item)
            continue
        label = str(item.get("label") or item.get("category") or "")
        duplicate = False
        for previous in kept:
            previous_box = previous.get("bbox")
            previous_label = str(previous.get("label") or previous.get("category") or "")
            if (
                isinstance(previous_box, (list, tuple))
                and len(previous_box) == 4
                and previous_label == label
                and _iou(box, [float(value) for value in previous_box]) >= iou_threshold
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept


def _native_mode(adapter) -> str:
    """Il percorso nativo di un adapter, sondato come le modalità prefill:
    una generazione di parsing per chi la ha verificata (MonkeyOCRv2
    END2END), il protocollo client ufficiale a due passi per chi è nato così
    (MinerU2.5). ``NotImplementedError`` → il modello non è integrato nel
    percorso nativo e va detto con un errore esplicito, non con un prompt
    sbagliato in silenzio."""
    try:
        adapter.prompt_for("end2end")
        return "end2end"
    except NotImplementedError:
        pass
    try:
        adapter.prompt_for("layout")
        return "two_stage"
    except NotImplementedError as exc:
        if getattr(adapter, "page_layout_fallback", None) == "ocr":
            return "two_stage"
        raise ValueError(
            f"adapter '{adapter.adapter_id}': nessun percorso nativo integrato nel prefill"
        ) from exc


def model_prelabel_events(
    project_id: int,
    page_ids: list[int],
    opts: PrelabelOptions,
    client,
    lang: str,
    on_output=None,
    cancel_event: threading.Event | None = None,
):
    """Versione a eventi del prefill modello: un generatore che emette un
    evento per ogni blocco scritto, così l'endpoint SSE può far comparire la
    pagina sul canvas progressivamente invece di far attendere tutto.

    Eventi: ``page`` (inizio pagina), ``output`` (delta grezzo del modello),
    ``block`` (blocco inserito, con griglia se tabella), ``page_done``
    (riepilogo identico alla risposta batch), ``error`` (pagina saltata con
    motivo). La scrittura su DB resta qui:
    l'evento parte solo dopo l'INSERT — ciò che la UI mostra è ciò che esiste
    davvero lato server.
    """
    # `native` si risolve nel percorso nativo dell'adapter una volta sola.
    mm = opts.model_mode
    if mm == "native":
        mm = _native_mode(client.adapter)
    known = {label.name for label in labeling.DEFAULT_LABELS}
    with connect() as conn:
        for pid in page_ids:
            if cancel_event is not None and cancel_event.is_set():
                return
            page = conn.execute(
                "SELECT * FROM pages WHERE id=? AND project_id=?", (pid, project_id)
            ).fetchone()
            if page is None:
                raise HTTPException(status_code=404, detail=msg("page_not_in_project", lang, id=pid))
            yield {"type": "page", "page_id": pid}
            try:
                image, applied_angle = pagesvc.maybe_auto_deskew(page)
            except HTTPException as exc:
                yield {"type": "error", "page_id": pid, "message": str(exc.detail)}
                continue
            if image is None:
                yield {
                    "type": "error",
                    "page_id": pid,
                    "message": str(msg("image_not_available", lang)),
                }
                continue

            try:
                output_phases: dict[int, str] = {}

                def emit(delta: str, phase: str = "model") -> None:
                    if on_output and delta:
                        # MinerU/Paddle possono restituire direttamente OTSL
                        # anche durante la fase nominalmente chiamata
                        # ``layout`` (pagina dominata da una tabella). La
                        # fase mostrata dalla UI deve descrivere il payload
                        # effettivo, altrimenti l'utente vede "layout" e un
                        # dump tabellare apparentemente non parsato.
                        if phase == "layout" and otsl.looks_like_otsl(delta):
                            output_phases[pid] = "table"
                        visible_phase = output_phases.get(pid, phase)
                        on_output({"type": "output", "page_id": pid, "phase": visible_phase, "text": delta})

                def call_live(method, *args, phase: str, on_retry=None):
                    """Compatibilità con client/mock legacy privi di on_delta."""
                    callback = lambda d: emit(d, phase)
                    try:
                        kwargs = {"on_delta": callback, "cancel_event": cancel_event}
                        if on_retry is not None:
                            kwargs["on_retry"] = on_retry
                        return method(*args, **kwargs)
                    except TypeError as exc:
                        if not any(key in str(exc) for key in ("on_delta", "cancel_event", "on_retry")):
                            raise
                        try:
                            return method(*args, on_delta=callback)
                        except TypeError as retry_exc:
                            if "on_delta" not in str(retry_exc):
                                raise
                            return method(*args)

                if mm == "end2end":
                    items = call_live(
                        client.end2end,
                        image,
                        phase="end2end",
                        on_retry=lambda: on_output and on_output({
                            "type": "output_reset", "page_id": pid, "phase": "end2end"
                        }),
                    )
                elif getattr(client.adapter, "page_layout_fallback", None) == "ocr":
                    detector = ocrmod.OcrEngine()
                    if not detector.available:
                        raise RuntimeError(
                            "PaddleOCR-VL richiede un detector locale per il layout "
                            "(attiva RapidOCR oppure usa MonkeyOCRv2/MinerU2.5)"
                        )
                    detections = detector.detect(image)
                    items = [
                        {
                            "bbox": [
                                round(d["bbox"][0] / image.width * 1000),
                                round(d["bbox"][1] / image.height * 1000),
                                round(d["bbox"][2] / image.width * 1000),
                                round(d["bbox"][3] / image.height * 1000),
                            ],
                            "label": "Text",
                            "content": d.get("text") or "",
                            "score": d.get("score", 0),
                        }
                        for d in detections
                        if d.get("score", 0) >= opts.confidence
                    ]
                else:
                    items = call_live(client.layout, image, phase="layout")
            except RuntimeError as exc:
                yield {"type": "error", "page_id": pid, "message": str(exc)}
                continue

            # End2end può produrre lo stesso bbox molte volte quando il
            # decoder entra in una sequenza ripetitiva. Deduplicare prima del
            # DB evita una colonna di bozze identiche e mantiene il primo
            # ordine di lettura emesso dal modello.
            items = _dedupe_model_items(items)

            width, height = page["width"], page["height"]
            kept = []
            for item in items:
                bbox = _scale_bbox(item, width, height)
                if bbox[2] - bbox[0] < opts.min_size or bbox[3] - bbox[1] < opts.min_size:
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
            # Nel percorso Paddle+detector OCR il primo stadio restituisce
            # righe, non un blocco Table. Applichiamo la stessa promozione
            # geometrica del motore OCR locale prima di riconoscere i crop:
            # così il secondo stadio Paddle riceve l'intero registro e può
            # restituire HTML/OTSL con colonne e celle unite.
            if (
                getattr(client.adapter, "page_layout_fallback", None) == "ocr"
                and opts.table_promote
            ):
                candidate = _table_cluster(kept)
                if candidate and _looks_like_table(image, candidate[0]):
                    table_bbox, consumed = candidate
                    consumed_set = set(consumed)
                    kept = [k for i, k in enumerate(kept) if i not in consumed_set]
                    kept.append({"bbox": table_bbox, "label": "Table", "content": ""})
                    kept.sort(key=lambda k: (k["bbox"][1], k["bbox"][0]))
            for k in kept:
                if k["label"] in ("Table", "Text") and _looks_like_table(image, k["bbox"]):
                    k["label"] = "Table"
                    tables += 1

            replaced = _replaced_blocks(conn, pid, opts.mode)
            grids = 0
            table_sources: list[str] = []
            for i, k in enumerate(kept, start=1):
                # Nel due-stadi il primo giro produce solo geometria/classi:
                # il testo va riconosciuto sul crop nativo del blocco. Senza
                # questo passaggio la UI mostrava bbox corretti ma textarea
                # vuote, dando l'impressione che l'inferenza fosse fallita.
                if (
                    mm == "two_stage"
                    and k["label"] not in {"Column", "Picture"}
                    and not k.get("content")
                ):
                    try:
                        x1, y1, x2, y2 = k["bbox"]
                        crop = image.crop((x1, y1, x2, y2))
                        k["content"] = call_live(
                            client.recognize,
                            crop,
                            k["label"],
                            phase="text",
                        )
                    except Exception as exc:  # noqa: BLE001
                        k["error"] = f"{k['bbox']}: {exc}"
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
                        f"model:{client.model}:{mm}",
                    ),
                )
                if cancel_event is not None and cancel_event.is_set():
                    # Non lasciare una bozza appena inserita dopo Stop. Il
                    # controllo è prima di qualsiasi griglia/evento; il
                    # rollback della connessione elimina anche questo INSERT.
                    conn.rollback()
                    return
                grid_json = None
                if k["label"] == "Table":
                    # END2END può già contenere OTSL. Se non è valido (o il layout
                    # ha promosso geometricamente un Text a Table), si ricade sul
                    # riconoscimento del crop. Il fallback è misurabile e dichiarato
                    # nel risultato, non una sostituzione silenziosa.
                    grid = None
                    used_end2end_content = False
                    if mm == "end2end" and k.get("content"):
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
                            grid = _recognize_table(
                                client, image, k["bbox"],
                                on_delta=lambda d: emit(d, "table"),
                                cancel_event=cancel_event,
                            )
                    except Exception as exc:  # noqa: BLE001
                        k["error"] = f"{k['bbox']}: {exc}"
                        grid = None
                    if grid and grid["rows"]:
                        conn.execute(
                            "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
                            "ON CONFLICT(block_id) DO UPDATE SET grid_json=excluded.grid_json, "
                            "updated_at=datetime('now')",
                            (cur.lastrowid, json.dumps(grid)),
                        )
                        grids += 1
                        source = "end2end" if used_end2end_content else "table_crop_fallback"
                        k["table_source"] = source
                        table_sources.append(source)
                        grid_json = grid

                yield {
                    "type": "block",
                    "page_id": pid,
                    "block": {
                        "id": cur.lastrowid,
                        "label": k["label"],
                        "points": [[k["bbox"][0], k["bbox"][1]], [k["bbox"][2], k["bbox"][3]]],
                        "content": k.get("content", ""),
                        "order_idx": i,
                        "prefill_source": f"model:{client.model}:{mm}",
                        "grid": grid_json,
                        "error": k.get("error"),
                    },
                }

            yield {
                "type": "page_done",
                "page_id": pid,
                "summary": {
                    "page_id": pid,
                    "detected": len(items),
                    "inserted": len(kept),
                    "tables": tables,
                    "grids": grids,
                    "mode": opts.mode,
                    "replaced_blocks": replaced["blocks"],
                    "replaced_grids": replaced["grids"],
                    "model_mode": mm,
                    "table_sources": table_sources,
                    "deskew_angle": applied_angle,
                },
            }


def _collect_summaries(events) -> list[dict]:
    """Consuma un generatore di eventi e ricostruisce la risposta batch.

    Il percorso batch (POST /prelabel) resta identico a prima: stesso schema
    di risposta, stessi test. Lo streaming è un consumatore diverso della
    stessa orchestrazione, non una seconda logica.
    """
    summaries: list[dict] = []
    for ev in events:
        if ev["type"] == "page_done":
            summaries.append(ev["summary"])
        elif ev["type"] == "error":
            summaries.append(
                {"page_id": ev["page_id"], "detected": 0, "inserted": 0, "error": ev["message"]}
            )
    return summaries


def model_prelabel_pages(
    project_id: int, page_ids: list[int], opts: PrelabelOptions, client, lang: str
) -> list[dict]:
    """Bozze prodotte dal modello base MonkeyOCRv2 servito da vLLM.

    A differenza dell'OCR di riga, il modello restituisce blocchi **già
    classificati** e nell'ordine di lettura che propone: una pagina indice esce
    con il suo blocco `Table` invece che con centinaia di righe `Text`. Le
    coordinate arrivano normalizzate 0–1000 e vengono riportate a pixel pagina.
    """
    return _collect_summaries(model_prelabel_events(project_id, page_ids, opts, client, lang))


def ocr_prelabel_events(
    project_id: int, page_ids: list[int], opts: PrelabelOptions, engine, lang: str
):
    """Versione a eventi di `ocr_prelabel_pages` (v. `model_prelabel_events`)."""
    with connect() as conn:
        for pid in page_ids:
            page = conn.execute(
                "SELECT * FROM pages WHERE id=? AND project_id=?", (pid, project_id)
            ).fetchone()
            if page is None:
                raise HTTPException(status_code=404, detail=msg("page_not_in_project", lang, id=pid))
            yield {"type": "page", "page_id": pid}
            try:
                image, applied_angle = pagesvc.maybe_auto_deskew(page)
            except HTTPException as exc:
                yield {"type": "error", "page_id": pid, "message": str(exc.detail)}
                continue
            if image is None:
                yield {
                    "type": "error",
                    "page_id": pid,
                    "message": str(msg("image_not_available", lang)),
                }
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
                if d["score"] < opts.confidence:
                    continue
                bbox = d["bbox"]
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                if w < opts.min_size or h < opts.min_size:
                    continue
                bbox = [
                    max(0, min(round(bbox[0]), page["width"])),
                    max(0, min(round(bbox[1]), page["height"])),
                    max(0, min(round(bbox[2]), page["width"])),
                    max(0, min(round(bbox[3]), page["height"])),
                ]
                if bbox[2] - bbox[0] < opts.min_size or bbox[3] - bbox[1] < opts.min_size:
                    continue
                kept.append({"bbox": bbox, "text": d["text"].strip(), "score": d["score"]})
            kept = _nms_lite(kept)

            # Promozione a tabella: il più grande cluster di righe consecutive
            # che la geometria dimostra essere una griglia diventa un blocco
            # `Table` con celle precompilate; le righe fuori dal cluster
            # (testata, numero del fascicolo) restano `Text`.
            table_bbox: list[int] | None = None
            if opts.table_promote:
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

            replaced = _replaced_blocks(conn, pid, opts.mode)
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
                grid_json = None
                if o["label"] == "Table":
                    grid = _ocr_table_grid(image, o["bbox"], engine, min_score=0.0)
                    if grid:
                        conn.execute(
                            "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
                            "ON CONFLICT(block_id) DO UPDATE SET grid_json=excluded.grid_json, "
                            "updated_at=datetime('now')",
                            (cur.lastrowid, json.dumps(grid)),
                        )
                        grids += 1
                        grid_json = grid

                yield {
                    "type": "block",
                    "page_id": pid,
                    "block": {
                        "id": cur.lastrowid,
                        "label": o["label"],
                        "points": [[o["bbox"][0], o["bbox"][1]], [o["bbox"][2], o["bbox"][3]]],
                        "content": o["content"],
                        "order_idx": i,
                        "prefill_source": o["prefill"],
                        "grid": grid_json,
                        "error": None,
                    },
                }

            yield {
                "type": "page_done",
                "page_id": pid,
                "summary": {
                    "page_id": pid,
                    "detected": len(detections),
                    "inserted": len(outputs),
                    "tables": 1 if table_bbox else 0,
                    "grids": grids,
                    "mode": opts.mode,
                    "replaced_blocks": replaced["blocks"],
                    "replaced_grids": replaced["grids"],
                    "deskew_angle": applied_angle,
                },
            }


def ocr_prelabel_pages(
    project_id: int, page_ids: list[int], opts: PrelabelOptions, engine, lang: str
) -> list[dict]:
    """Bozze prodotte dall'OCR di riga locale (RapidOCR/PaddleOCR)."""
    return _collect_summaries(ocr_prelabel_events(project_id, page_ids, opts, engine, lang))
