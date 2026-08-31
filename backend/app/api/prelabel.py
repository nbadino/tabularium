"""API pseudo-labeling: generazione bozze di blocchi (OCR locale o modello).

Il router è una porta: autorizzazione, schema della richiesta, scelta e
controllo del motore. L'orchestrazione vera sta in ``services/prefill.py``.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import connect
from ..services import inference, prefill as prefillsvc
from ..services import ocr as ocrmod
from ..services.i18n import msg, parse_lang
from ..services import auth as authsvc
from .deps import require_resource

router = APIRouter(
    tags=["prelabel"],
    dependencies=[Depends(authsvc.get_current_user)],
)


class PrelabelRequest(BaseModel):
    page_ids: list[int] = Field(min_length=1)
    # Cosa succede ai blocchi già presenti sulla pagina:
    # - `merge`: i nuovi blocchi si aggiungono, nulla viene cancellato;
    # - `replace_drafts`: vengono cancellati solo i blocchi generati dal
    #   prefill e non ancora confermati (le bozze), mai il lavoro umano;
    # - `replace_all`: si cancella tutto, griglie tabellari comprese
    #   (ON DELETE CASCADE): è distruttivo e la UI deve confermarlo;
    # - `replace`: alias storico di `replace_all`, accettato per compatibilità
    #   con i client esistenti ma non più usato dalla UI.
    mode: Literal["merge", "replace", "replace_drafts", "replace_all"] = "replace"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    min_size: int = Field(default=10, ge=2)
    # `ocr` trova righe di testo e le etichetta tutte `Text`: va bene su pagine
    # di prosa, male su tabelle allineate a spazi (fonde le colonne in una riga
    # sola). `model` usa il modello servito da vLLM nel suo percorso nativo.
    engine: Literal["ocr", "model"] = "ocr"
    # Percorso di inferenza del modello:
    # - `native` (default): l'immagine va al modello com'è e il percorso è
    #   quello nativo dell'adapter — una generazione di parsing per chi ne ha
    #   una (MonkeyOCRv2 END2END), il protocollo client ufficiale per chi è
    #   nato a due passi (MinerU2.5). È la condizione per ottenere risultati
    #   identici all'uso diretto del modello fuori da Tabularium;
    # - `two_stage` / `end2end`: percorsi espliciti storici, accettati per
    #   compatibilità ma non più proposti dalla UI.
    model_mode: Literal["native", "two_stage", "end2end"] = "native"
    # Il percorso OCR vede solo righe: qui si prova a promuovere il più grande
    # cluster di righe consecutive a blocco `Table` (griglia + celle precompilate
    # via OCR). Va bene lasciarlo attivo: la promozione non è un'ipotesi, la
    # geometria la dimostra, e in caso di dubbio resta tutto `Text` come prima.
    table_promote: bool = True


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

    opts = prefillsvc.PrelabelOptions(
        mode=payload.mode,
        confidence=payload.confidence,
        min_size=payload.min_size,
        model_mode=payload.model_mode,
        table_promote=payload.table_promote,
    )

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
        results = prefillsvc.model_prelabel_pages(
            project_id, payload.page_ids, opts, client, lang
        )
        return {"engine": "model", "model": client.model, "results": results}

    engine = ocrmod.OcrEngine()
    if not engine.available:
        raise HTTPException(
            status_code=400,
            detail=msg("ocr_unavailable", lang),
        )

    results = prefillsvc.ocr_prelabel_pages(
        project_id, payload.page_ids, opts, engine, lang
    )
    return {"engine": engine.name, "results": results}


@router.post("/api/projects/{project_id}/prelabel/stream")
def prelabel_stream(
    project_id: int,
    payload: PrelabelRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
):
    """Stesso prefill, risposta a eventi (SSE): eventi ``output`` per i delta
    vLLM e ``block`` per ogni blocco scritto, così la UI mostra il decoding e
    poi il risultato parsato progressivamente invece di attendere in silenzio.

    Gli errori di pagina non interrompono lo stream (evento ``error``); solo
    i controlli iniziali — progetto, motore, endpoint modello — restano
    risposte HTTP d'errore, perché arrivano prima del primo evento.
    """
    import json as jsonlib  # noqa: PLC0415

    from fastapi.responses import StreamingResponse  # noqa: PLC0415

    from ..api.projects import _get_project_or_404  # noqa: PLC0415
    import threading  # noqa: PLC0415

    lang = parse_lang(request.headers.get("accept-language"))
    # È condiviso fra il generatore SSE e il thread che esegue il modello.
    # Quando il browser chiude la connessione (Stop o cambio pagina), `gen` lo
    # segnala al client vLLM, che interrompe la lettura invece di lasciare una
    # richiesta Modal costosa in esecuzione.
    cancel_event = threading.Event()

    with connect() as conn:
        _get_project_or_404(conn, project_id)

    opts = prefillsvc.PrelabelOptions(
        mode=payload.mode,
        confidence=payload.confidence,
        min_size=payload.min_size,
        model_mode=payload.model_mode,
        table_promote=payload.table_promote,
    )

    engine_name: str
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
        event_factory = lambda sink: prefillsvc.model_prelabel_events(
            project_id,
            payload.page_ids,
            opts,
            client,
            lang,
            on_output=sink,
            cancel_event=cancel_event,
        )
        engine_name = "model"
    else:
        engine = ocrmod.OcrEngine()
        if not engine.available:
            raise HTTPException(status_code=400, detail=msg("ocr_unavailable", lang))
        event_factory = lambda _sink: prefillsvc.ocr_prelabel_events(
            project_id, payload.page_ids, opts, engine, lang
        )
        engine_name = engine.name

    def gen():
        # Il generatore di Starlette viene iterato su thread pool DIVERSI tra
        # un yield e l'altro: una connessione SQLite aperta lì muore con
        # "SQLite objects created in a thread can only be used in that same
        # thread" (riprodotto). Tutto il lavoro DB gira quindi in UN thread
        # dedicato; qui si consuma solo una coda e si formattano gli eventi.
        import queue as queuelib  # noqa: PLC0415
        q: queuelib.Queue = queuelib.Queue()
        _DONE = object()
        events = event_factory(q.put)

        def worker():
            try:
                for event in events:
                    q.put(event)
            except BaseException as exc:  # noqa: BLE036
                q.put(exc)
            finally:
                q.put(_DONE)

        threading.Thread(target=worker, daemon=True).start()

        try:
            yield f"data: {jsonlib.dumps({'type': 'start', 'engine': engine_name})}\n\n"
            while True:
                event = q.get()
                if event is _DONE:
                    break
                if isinstance(event, BaseException):
                    if isinstance(event, HTTPException):
                        payload = {"type": "error", "message": str(event.detail)}
                    else:
                        payload = {"type": "error", "message": str(event)}
                    yield f"data: {jsonlib.dumps(payload)}\n\n"
                    break
                yield f"data: {jsonlib.dumps(event, ensure_ascii=False)}\n\n"
            yield f"data: {jsonlib.dumps({'type': 'end'})}\n\n"
        finally:
            # Starlette esegue il finally anche quando il client disconnette
            # prima dell'evento `end`.
            cancel_event.set()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
