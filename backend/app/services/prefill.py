"""Orchestrazione del prefill (pseudo-labeling): la logica, non l'HTTP.

Il router (``api/prelabel.py``) resta la porta: autorizzazione, schema della
richiesta e scelta del motore. Qui sta il resto — filtro delle detection,
promozione a tabella, sostituzione dei blocchi esistenti, scrittura delle
griglie — così che il caso d'uso sia leggibile e testabile senza il server.
"""
from __future__ import annotations

import json
import threading
from .table_provenance import with_cell_provenance

from dataclasses import dataclass

from fastapi import HTTPException

from ..adapters import blocks as blockstore
from ..adapters import projects as projectstore
from . import labeling, ocr as ocrmod, table_detect, table_hint
from ..domain import otsl, table_headers
from ..domain.prefill import (
    _TABLE_MIN_COLS,
    _TABLE_MIN_ROWS,
    _iou,
    _nms_lite,
    _table_cluster,
    _scale_bbox,
    _containment,
    _box_of,
    _label_of,
    _drop_nested_duplicates,
    _dedupe_model_items,
)
from . import model_labels
from .inference import OutputTruncated
from .model_adapters import supported_prefill_modes
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
    # Presente quando l'orchestrazione arriva da una run bulk persistente.
    # Collega ogni bozza alla sua provenienza senza cambiare il comportamento
    # storico del prefill sulla singola pagina.
    recognition_run_id: int | None = None


def _looks_like_table(image, bbox: list[int]) -> bool:
    """Vero se il ritaglio ha la struttura di una tabella allineata a spazi.

    DEPRECATO: poggia su `table_detect` (percorso geometrico, v. l'intestazione
    di quel modulo). È la promozione a `Table` del prefill OCR, in via d'uscita
    insieme al rilevatore.
    """
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


def _recognize_table(client, image, bbox: list[int], on_delta=None, cancel_event=None, hint: str | None = None) -> dict:
    """Griglia di una tabella: confini di riga dalla geometria, testo dal modello."""
    x1, y1, x2, y2 = bbox
    crop = image.crop((x1, y1, x2, y2))
    row_bounds: list[int] | None = None
    try:
        detection = table_detect.detect_grid(crop)
        row_bounds = [round(h * crop.height) for h in detection.hlines]
    except Exception:  # noqa: BLE001
        row_bounds = None
    return client.table_grid(crop, row_bounds=row_bounds, on_delta=on_delta, cancel_event=cancel_event, hint=hint)




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














def native_mode(adapter) -> str:
    """Restituisce il percorso predefinito dal produttore per questo adapter.

    Il numero di passaggi è una proprietà del modello, non una preferenza
    globale: MonkeyOCRv2 e MinerU usano layout + crop; Qwen, TeleOCR e Paddle
    eseguono i rispettivi parser ufficiali; Unlimited e dots.mocr usano la
    generazione completa.
    Un prompt disponibile non basta a dichiarare supporto nativo: se il
    workflow del produttore non è integrato, il prefill deve fermarsi.
    """
    mode = getattr(adapter, "native_prefill_mode", None)
    if mode == "official":
        if getattr(adapter, "page_layout_fallback", None) == "official-pipeline":
            return mode
    elif mode == "end2end":
        try:
            adapter.prompt_for("end2end")
            return mode
        except NotImplementedError:
            pass
    elif mode == "two_stage":
        try:
            adapter.prompt_for("layout")
            return mode
        except NotImplementedError:
            pass
    raise ValueError(
        f"adapter '{adapter.adapter_id}': workflow nativo del produttore "
        "non ancora integrato nel prefill"
    )


def native_page_items(client, image, *, on_delta=None, cancel_event=None) -> list[dict]:
    """Blocchi di una pagina dal percorso nativo «official» dell'adapter.

    The adapter declares which client method runs its official pipeline
    (`native_runner`), so adding a model is a declaration, not a branch here. The
    methods return bbox 0–1000 + label + content in one pass; the client adapts each
    vendor's coordinate convention before returning.
    """
    runner = getattr(client.adapter, "native_runner", None)
    if not runner:
        raise RuntimeError(
            f"adapter '{client.adapter.adapter_id}': workflow nativo del produttore "
            "non ancora integrato nel prefill"
        )
    method = getattr(client, f"{runner}_native_page", None)
    if not callable(method):
        raise RuntimeError(
            f"adapter '{client.adapter.adapter_id}': runner nativo "
            f"'{runner}_native_page' non esiste nel client"
        )
    return method(image, on_delta=on_delta, cancel_event=cancel_event)


def _project_schema_labels(project_id: int) -> list:
    """Classi del progetto (default + custom) per il bridge nativa → canonica."""
    return labeling.project_schema(projectstore.settings_json(project_id)).labels


def _project_table_hint(project_id: int) -> str:
    """Hint di schema tabella del progetto (v. `services/table_hint.py`)."""
    return table_hint.get_table_hint(projectstore.settings_json(project_id))


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
        mm = native_mode(client.adapter)
    adapter_id = getattr(getattr(client, "adapter", None), "adapter_id", None)
    # Le classi del PROGETTO (default + custom): una label nativa si traduce nel
    # vocabolario canonico del progetto. Prima si usava la sola tassonomia di
    # default, quindi le classi custom del ricercatore venivano coercizzate a
    # `Text` in silenzio.
    schema_labels = _project_schema_labels(project_id)
    table_hint_text = _project_table_hint(project_id)
    with blockstore.writing() as conn:
        for pid in page_ids:
            if cancel_event is not None and cancel_event.is_set():
                return
            page = conn.page(pid, project_id)
            if page is None:
                raise HTTPException(status_code=404, detail=msg("page_not_in_project", lang, id=pid))
            keeps_blocks = conn.has_blocks(pid)
            # Il SELECT apre una transazione di lettura. Chiuderla prima di
            # chiamare il modello evita che l'inferenza (minuti) trattenga il
            # lock e faccia fallire «elimina annotazioni»/autosave concorrenti.
            conn.commit()
            yield {"type": "page", "page_id": pid}
            try:
                # Only replace_all clears the blocks the rotation would misplace.
                image, applied_angle = pagesvc.maybe_auto_deskew(
                    page, may_transform=opts.mode in {"replace", "replace_all"} or not keeps_blocks
                )
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

            page_mode = mm
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

                def call_live(method, *args, phase: str, on_retry=None, **extra):
                    """Compatibilità con client/mock legacy privi di on_delta."""
                    callback = lambda d: emit(d, phase)
                    try:
                        kwargs = {"on_delta": callback, "cancel_event": cancel_event, **extra}
                        if on_retry is not None:
                            kwargs["on_retry"] = on_retry
                        return method(*args, **kwargs)
                    except TypeError as exc:
                        if not any(key in str(exc) for key in ("on_delta", "cancel_event", "on_retry", *extra)):
                            raise
                        try:
                            return method(*args, on_delta=callback, **extra)
                        except TypeError as retry_exc:
                            if "on_delta" not in str(retry_exc):
                                raise
                            return method(*args)

                if mm == "official":
                    items = native_page_items(
                        client,
                        image,
                        on_delta=lambda delta: emit(delta, "layout"),
                        cancel_event=cancel_event,
                    )
                elif mm == "end2end":
                    try:
                        items = call_live(
                            client.end2end,
                            image,
                            phase="end2end",
                            on_retry=lambda: on_output and on_output({
                                "type": "output_reset", "page_id": pid, "phase": "end2end"
                            }),
                        )
                    except OutputTruncated:
                        # Una pagina densa non sta nel budget di uscita (2 su 11
                        # nel benchmark Calcutta): il due-stadi la recupera. Il
                        # riepilogo della pagina dichiara il percorso usato.
                        if not supported_prefill_modes(client.adapter)["supports_two_stage"]:
                            raise
                        if on_output:
                            on_output({"type": "output_reset", "page_id": pid, "phase": "end2end"})
                        page_mode = "two_stage"
                        items = call_live(client.layout, image, phase="layout")
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
            unmapped: dict[str, int] = {}
            for item in items:
                bbox = _scale_bbox(item, width, height)
                if bbox[2] - bbox[0] < opts.min_size or bbox[3] - bbox[1] < opts.min_size:
                    continue
                # Bridge nativa → canonica. Una label fuori tassonomia non entra
                # in silenzio: si conta e si dichiara nel riepilogo, e il blocco
                # ricade su `Text` (la UI lo mostra e l'utente può aggiungere
                # l'alias o la classe).
                resolved = model_labels.resolve_native_label(item["label"], schema_labels, adapter_id)
                if resolved is None:
                    raw = str(item["label"] or "").strip() or "—"
                    unmapped[raw] = unmapped.get(raw, 0) + 1
                    resolved = "Text"
                kept.append(
                    {
                        "bbox": bbox,
                        "label": resolved,
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
                mm != "official"
                and getattr(getattr(client, "adapter", None), "page_layout_fallback", None) == "ocr"
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
                if (
                    adapter_id != "llamaindex-ocria-1"
                    and k["label"] in ("Table", "Text")
                    and _looks_like_table(image, k["bbox"])
                ):
                    k["label"] = "Table"
                    tables += 1
            # Native runners return Table blocks directly; the loop above only
            # counts geometric promotions. Report the actual number of table
            # drafts regardless of which path classified them.
            tables = sum(1 for k in kept if k["label"] == "Table")

            replaced = conn.replace_blocks(pid, opts.mode)
            # Il DELETE di `_replaced_blocks` apre una transazione di
            # scrittura che, senza commit, resterebbe aperta attraverso TUTTA
            # l'inferenza della pagina (minuti): ogni altra scrittura concorrente
            # (sync delle annotazioni, run di riconoscimento) attenderebbe il
            # busy_timeout e poi fallirebbe con 500 "database is locked".
            # Rilasciamo subito il lock; i blocchi nuovi arrivano sotto, un
            # commit ciascuno.
            conn.commit()
            grids = 0
            table_sources: list[str] = []
            for i, k in enumerate(kept, start=1):
                # Nel due-stadi il primo giro produce solo geometria/classi:
                # il testo va riconosciuto sul crop nativo del blocco. Senza
                # questo passaggio la UI mostrava bbox corretti ma textarea
                # vuote, dando l'impressione che l'inferenza fosse fallita.
                if (
                    page_mode == "two_stage"
                    and k["label"] not in {"Column", "Picture"}
                    and not k.get("content")
                    and not (
                        k["label"] == "Table"
                        and getattr(client.adapter, "table_recognition_strategy", None) == "full_crop"
                    )
                ):
                    try:
                        x1, y1, x2, y2 = k["bbox"]
                        crop = image.crop((x1, y1, x2, y2))
                        k["content"] = call_live(
                            client.recognize,
                            crop,
                            k["label"],
                            phase="text",
                            hint=table_hint_text,
                        )
                    except Exception as exc:  # noqa: BLE001
                        k["error"] = f"{k['bbox']}: {exc}"
                if cancel_event is not None and cancel_event.is_set():
                    # Stop prima di qualsiasi scrittura: con il commit per
                    # blocco (sotto) ogni INSERT è già definitivo, quindi qui
                    # non esiste più un rollback che possa rimediare.
                    return
                block_id = conn.insert_block(
                    pid,
                    label=k["label"],
                    points=[[k["bbox"][0], k["bbox"][1]], [k["bbox"][2], k["bbox"][3]]],
                    content=k.get("content", ""),
                    order_idx=i,
                    prefill_source=f"model:{client.model}:{page_mode}",
                    recognition_run_id=opts.recognition_run_id,
                )
                grid_json = None
                if k["label"] == "Table":
                    # END2END può già contenere OTSL. Se non è valido (o il layout
                    # ha promosso geometricamente un Text a Table), si ricade sul
                    # riconoscimento del crop. Il fallback è misurabile e dichiarato
                    # nel risultato, non una sostituzione silenziosa.
                    grid = None
                    used_end2end_content = False
                    if page_mode in {"end2end", "official"} and k.get("content"):
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
                            candidate = table_headers.repair_flat_header(otsl.otsl_to_grid(candidate_content))
                            if candidate.get("rows") and candidate.get("cols"):
                                grid = candidate
                                used_end2end_content = True
                        except Exception:  # noqa: BLE001
                            grid = None
                    try:
                        if grid is None:
                            if page_mode == "official" and client.adapter.adapter_id in {
                                "teleocr", "mineru2.5", "llamaindex-ocria-1",
                            }:
                                # These producer-owned runners have already
                                # recognized and post-processed the table.
                                # Do not substitute a separate crop workflow
                                # when their native result cannot be parsed.
                                k["error"] = f"output tabella nativo {client.adapter.adapter_id} non valido: tabella lasciata da revisionare"
                            else:
                                grid = _recognize_table(
                                    client, image, k["bbox"],
                                    on_delta=lambda d: emit(d, "table"),
                                    cancel_event=cancel_event,
                                    hint=table_hint_text,
                                )
                    except Exception as exc:  # noqa: BLE001
                        k["error"] = f"{k['bbox']}: {exc}"
                        grid = None
                    if grid and grid["rows"]:
                        grid = with_cell_provenance(grid, f"model:{client.model}", generated=True)
                        conn.upsert_grid(block_id, grid)
                        grids += 1
                        source = "end2end" if used_end2end_content else "table_crop_fallback"
                        k["table_source"] = source
                        table_sources.append(source)
                        grid_json = grid
                # Commit subito dopo ogni scrittura: l'evento che segue dichiara
                # "ciò che la UI mostra esiste davvero lato server" — ma una riga
                # dentro una transazione aperta non è visibile alle altre
                # connessioni. E soprattutto: tra un blocco e l'altro c'è
                # inferenza (secondo stadio, crop tabellari) e il lock di
                # scrittura non deve sopravvivere a quei secondi.
                conn.commit()

                yield {
                    "type": "block",
                    "page_id": pid,
                    "block": {
                        "id": block_id,
                        "label": k["label"],
                        "points": [[k["bbox"][0], k["bbox"][1]], [k["bbox"][2], k["bbox"][3]]],
                        "content": k.get("content", ""),
                        "order_idx": i,
                        "prefill_source": f"model:{client.model}:{page_mode}",
                        "grid": grid_json,
                        "error": k.get("error"),
                    },
                }

            revision = conn.bump_revision(pid) if (replaced["blocks"] or kept) else None
            yield {
                "type": "page_done",
                "page_id": pid,
                "summary": {
                    "page_id": pid,
                    "annotation_revision": revision,
                    "detected": len(items),
                    "inserted": len(kept),
                    "tables": tables,
                    "grids": grids,
                    "mode": opts.mode,
                    "replaced_blocks": replaced["blocks"],
                    "replaced_grids": replaced["grids"],
                    "model_mode": page_mode,
                    "table_sources": table_sources,
                    "deskew_angle": applied_angle,
                    "unmapped_labels": unmapped,
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
    with blockstore.writing() as conn:
        for pid in page_ids:
            page = conn.page(pid, project_id)
            if page is None:
                raise HTTPException(status_code=404, detail=msg("page_not_in_project", lang, id=pid))
            keeps_blocks = conn.has_blocks(pid)
            conn.commit()
            yield {"type": "page", "page_id": pid}
            try:
                # Only replace_all clears the blocks the rotation would misplace.
                image, applied_angle = pagesvc.maybe_auto_deskew(
                    page, may_transform=opts.mode in {"replace", "replace_all"} or not keeps_blocks
                )
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

            replaced = conn.replace_blocks(pid, opts.mode)
            # Come in model_prelabel_events: rilasciare subito il lock di
            # scrittura aperto dal DELETE, prima dell'inferenza sui crop.
            conn.commit()
            grids = 0
            for i, o in enumerate(outputs, start=1):
                block_id = conn.insert_block(
                    pid,
                    label=o["label"],
                    points=
                            [[o["bbox"][0], o["bbox"][1]], [o["bbox"][2], o["bbox"][3]]]
                        ,
                    content=o["content"],
                    order_idx=i,
                    prefill_source=o["prefill"],
                    recognition_run_id=opts.recognition_run_id,
                )
                grid_json = None
                if o["label"] == "Table":
                    grid = _ocr_table_grid(image, o["bbox"], engine, min_score=0.0)
                    if grid:
                        conn.upsert_grid(block_id, grid)
                        grids += 1
                        grid_json = grid
                # Commit per blocco: v. model_prelabel_events.
                conn.commit()

                yield {
                    "type": "block",
                    "page_id": pid,
                    "block": {
                        "id": block_id,
                        "label": o["label"],
                        "points": [[o["bbox"][0], o["bbox"][1]], [o["bbox"][2], o["bbox"][3]]],
                        "content": o["content"],
                        "order_idx": i,
                        "prefill_source": o["prefill"],
                        "grid": grid_json,
                        "error": None,
                    },
                }

            revision = conn.bump_revision(pid) if (replaced["blocks"] or outputs) else None
            yield {
                "type": "page_done",
                "page_id": pid,
                "summary": {
                    "page_id": pid,
                    "annotation_revision": revision,
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
