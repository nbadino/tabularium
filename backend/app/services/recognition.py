"""Run persistenti di riconoscimento bulk.

La connessione HTTP crea e osserva il lavoro; non lo possiede. Ogni pagina
viene elaborata in un worker backend e il risultato normalizzato viene salvato
subito in SQLite, quindi cambiare pagina o chiudere il browser non interrompe
la coda. Le bozze restano modificabili dallo Studio anche a inferenza spenta.
"""
from __future__ import annotations

import json
import csv
import io
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from ..adapters import runs as runstore
from . import inference, ocr as ocrmod, prefill, run_documents
from .i18n import msg

log = logging.getLogger(__name__)

_ACTIVE: dict[int, threading.Event] = {}
# Runs the user paused: the worker finishes the page in hand, then waits here
# without calling the model. Kept in memory only: a restart ends every run anyway
# (see `reconcile_runs`), and the pages not done yet can be retried.
_PAUSED: set[int] = set()
_LOCK = threading.Lock()
# How often a paused worker re-checks, and refreshes the heartbeat.
PAUSE_POLL_S = 1.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider(cfg: dict[str, Any]) -> str:
    active = runstore.active_provider()
    if active:
        return active
    if cfg.get("provider"):
        return str(cfg["provider"])
    url = str(cfg.get("url") or "").lower()
    if "api.cloud.llamaindex.ai" in url or "llamaindex" in url:
        return "llamaindex"
    if ".modal.run" in url:
        return "modal"
    if "runpod" in url:
        return "runpod"
    return "local" if "127.0.0.1" in url or "localhost" in url else "custom"



def _release_inference(
    provider: str,
    resource_id: str | None = None,
    credential_ref: str | None = None,
) -> None:
    """Impedisce nuove chiamate e libera la risorsa usata dalla run.

    Con `stop_policy='disable_inference'` il patto con l'utente è «a fine
    sessione la GPU torna libera». Per il serving locale significa fermare il
    processo vLLM; per un provider remoto significa fermare **l'istanza
    esatta** registrata dalla run (`resource_id`): cercare «la prima che
    capita» nell'elenco dell'account rischierebbe di spegnere una risorsa
    diversa da quella che ha lavorato. Se l'identità puntuale manca (endpoint
    custom, credenziale assente) si disattiva solo il client: meno sicuro, ma
    mai distruttivo verso la risorsa sbagliata.
    """
    inference.save_inference_config({"enabled": False})
    if provider == "local":
        from . import serve_manager

        serve_manager.stop()
        return
    if provider not in ("vast", "runpod") or not resource_id:
        return
    api_key = ""
    if credential_ref:
        from . import vault

        api_key = vault.get(credential_ref)
    if not api_key:
        raise RuntimeError(
            "credenziale del provider non disponibile: impossibile arrestare "
            f"la risorsa {resource_id} in sicurezza"
        )
    from . import cloud_manager

    if provider == "vast":
        cloud_manager.control_vast_instance(api_key, int(resource_id), "stop")
    else:
        cloud_manager.control_runpod_pod(api_key, str(resource_id), "stop")


def _run_out(row, *, include_items: bool = True, results: str | int = "all") -> dict[str, Any]:
    """Una sessione con le sue pagine.

    `results` decide quanto output grezzo del modello viaggia con le pagine:
    `"all"` (export), `"none"` (liste) o l'id di una pagina (lo Studio, che
    ne mostra una sola). Tutto l'output di 200 pagine pesava 4,7 MB, riletto
    ogni due secondi da una lista che non lo mostra.
    """
    out = dict(row)
    with _LOCK:
        out["paused"] = int(row["id"]) in _PAUSED and out.get("state") in {"queued", "running"}
    if not include_items:
        return out
    items = runstore.items_for_run(int(row["id"]))

    def _result(item) -> dict[str, Any] | None:
        if results == "all" or (results != "none" and int(item["page_id"]) == int(results)):
            return json.loads(item["result_json"] or "{}")
        return None

    out["items"] = [dict(item) | {"result": _result(item)} for item in items]
    for item in out["items"]:
        item.pop("result_json", None)
    return out



def get_run(run_id: int, *, results: str | int = "all") -> dict[str, Any]:
    row = runstore.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="sessione di riconoscimento non trovata")
    return _run_out(row, results=results)



def list_runs(project_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return [
        _run_out(row, include_items=False)
        for row in runstore.list_runs(project_id, limit)
    ]



def create_run(
    project_id: int,
    page_ids: list[int],
    *,
    engine: str = "model",
    mode: str = "replace_drafts",
    model_mode: str = "native",
    stop_policy: str = "disable_inference",
    owner_id: int | None = None,
    lang: str | None = None,
    profile_id: int | None = None,
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(int(value) for value in page_ids))
    if not unique_ids:
        raise HTTPException(status_code=422, detail="seleziona almeno una pagina")
    if len(unique_ids) > 10000:
        raise HTTPException(status_code=422, detail="una sessione può contenere al massimo 10000 pagine")
    if engine not in {"model", "ocr"}:
        raise HTTPException(status_code=422, detail="motore di riconoscimento non valido")
    if mode not in {"merge", "replace_drafts", "replace_all"}:
        raise HTTPException(status_code=422, detail="modalità di aggiornamento non valida")
    if stop_policy not in {"none", "disable_inference"}:
        raise HTTPException(status_code=422, detail="politica di arresto non valida")

    if engine == "model" and profile_id is not None:
        return _create_profile_run(
            project_id, unique_ids, profile_id, mode=mode, model_mode=model_mode,
            owner_id=owner_id, lang=lang,
        )

    cfg = inference.get_inference_config()
    if engine == "model" and not cfg.get("enabled", True):
        raise HTTPException(status_code=409, detail="attiva un modello prima di avviare il riconoscimento")
    if engine == "model":
        _refuse_host_blocked(str(cfg.get("adapter_id") or ""), lang)
    try:
        runstore.check(project_id, unique_ids, engine=engine)
        if engine == "model":
            # L'endpoint dichiarato deve rispondere PRIMA di accodare migliaia di
            # pagine destinate a fallire una a una. La sonda è corta e non avvia
            # nulla: per un serverless freddo è il segnale giusto per andare a
            # riattivarlo dalla pagina Modelli, non un falso blocco.
            client_probe = inference.get_vllm_client(timeout=5)
            probe = client_probe.test_connection(timeout=5.0)
            if not probe.get("ok") and str(client_probe.url).startswith(("http://127.0.0.1:", "http://localhost:")):
                try:
                    from . import cloud_manager
                    cloud_manager.reconcile_tunnel()
                    cfg = inference.get_inference_config()
                    probe = inference.get_vllm_client(timeout=5).test_connection(timeout=5.0)
                except Exception:
                    pass
            if not probe.get("ok"):
                raise HTTPException(
                    status_code=409,
                    detail=msg("model_endpoint_unreachable", lang, url=cfg.get("url") or ""),
                )

        # L'OCR è un percorso locale alternativo: non deve ereditare il provider
        # dell'endpoint modello precedentemente configurato (per esempio Vast.ai),
        # altrimenti storico e lifecycle descrivono una risorsa cloud che non usa.
        provider = _provider(cfg) if engine == "model" else "local"
        run_id = runstore.create(
            project_id,
            unique_ids,
            engine=engine,
            mode=mode,
            model_mode=model_mode,
            stop_policy=stop_policy,
            owner_id=owner_id,
            model_name=(
                str(cfg.get("model") or "") if engine == "model"
                else str(getattr(ocrmod.OcrEngine(), "name", "ocr"))
            ),
            adapter_id=str(cfg.get("adapter_id") or "") if engine == "model" else "local-ocr",
            provider=provider,
            endpoint=cfg.get("url") if engine == "model" else None,
            release={
                "resource_id": cfg.get("resource_id"),
                "credential_ref": cfg.get("provider_credential_ref"),
            } if engine == "model" else None,
        )
    except runstore.ModelBusy as exc:
        raise HTTPException(
            status_code=409,
            detail=f"il modello sta già elaborando la sessione #{exc.run_id}",
        ) from exc
    except runstore.PagesBusy as exc:
        raise HTTPException(
            status_code=409, detail=msg("pages_in_active_run", lang, run=exc.run_id)
        ) from exc
    except runstore.MissingPages as exc:
        raise HTTPException(
            status_code=404, detail="una o più pagine non appartengono al progetto"
        ) from exc
    _start_worker(run_id)
    return get_run(run_id)


def _refuse_host_blocked(adapter_id: str, lang: str | None) -> None:
    """Refuse a run this host cannot do at all, before queuing pages that would
    each fail the same way (a PaddleOCR-VL run on a CPU without AVX2 failed all
    527 pages of a document)."""
    from . import model_registry  # noqa: PLC0415

    blocker = model_registry.host_blocker(adapter_id) if adapter_id else None
    if blocker:
        raise HTTPException(status_code=409, detail=msg(f"model_host_{blocker}", lang))


def _create_profile_run(
    project_id: int,
    page_ids: list[int],
    profile_id: int,
    *,
    mode: str,
    model_mode: str,
    owner_id: int | None,
    lang: str | None,
) -> dict[str, Any]:
    """A run on a saved model (a compute profile): an API service, reached with the
    API key of the user who starts the run (`model_keys`).

    Nothing is activated, served or stopped: the active model stays as it is, the
    run reaches the saved model as a consensus run does (`client_for_profile`).
    """
    from ..adapters import compute_profiles as profilestore
    from . import model_keys

    profile = profilestore.get(profile_id)
    if profile is None or profile["name"] == "legacy-active":
        raise HTTPException(status_code=404, detail=msg("saved_model_not_found", lang))
    model_keys.require_usable(profile, owner_id, lang)
    _refuse_host_blocked(str(profile.get("model_adapter_id") or ""), lang)
    try:
        run_id = runstore.create(
            project_id,
            page_ids,
            engine="model",
            mode=mode,
            model_mode=model_mode,
            stop_policy="none",
            owner_id=owner_id,
            model_name=str(profile.get("name") or profile.get("served_model_name") or ""),
            adapter_id=str(profile.get("model_adapter_id") or ""),
            provider=str(profile.get("provider") or "custom"),
            endpoint=str(profile.get("endpoint") or "") or None,
            profile_id=profile_id,
        )
    except runstore.PagesBusy as exc:
        raise HTTPException(status_code=409, detail=msg("pages_in_active_run", lang, run=exc.run_id)) from exc
    except runstore.MissingPages as exc:
        raise HTTPException(status_code=404, detail="una o più pagine non appartengono al progetto") from exc
    _start_worker(run_id)
    return get_run(run_id)


def _start_worker(run_id: int) -> None:
    cancel = threading.Event()
    with _LOCK:
        _ACTIVE[run_id] = cancel
    threading.Thread(
        target=_worker,
        args=(run_id, cancel),
        name=f"recognition-{run_id}",
        daemon=True,
    ).start()


def _mark_item(item_id: int, state: str, **values: Any) -> None:
    runstore.mark_item(item_id, state, **values)



def _refresh_counts(run_id: int) -> None:
    runstore.refresh_counts(run_id)



def _recognize_page(run, item, opts, client, engine, cancel: threading.Event):
    """Riconosce una pagina: blocchi emessi, riepilogo, eventuale errore."""
    blocks: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None
    failure: str | None = None
    events = (
        prefill.model_prelabel_events(
            run["project_id"], [item["page_id"]], opts, client, "it",
            cancel_event=cancel,
        )
        if client is not None
        else prefill.ocr_prelabel_events(run["project_id"], [item["page_id"]], opts, engine, "it")
    )
    try:
        for event in events:
            if event["type"] == "block":
                blocks.append(event["block"])
                # Persist each emitted block immediately.  The run API
                # is also the live transport for the Studio: polling
                # it must not wait for the whole page to finish.
                _mark_item(
                    item["id"],
                    "running",
                    detected=len(blocks),
                    result_json=json.dumps(
                        {"summary": summary, "blocks": blocks},
                        ensure_ascii=False,
                    ),
                )
            elif event["type"] == "page_done":
                summary = event["summary"]
            elif event["type"] == "error":
                failure = str(event.get("message") or "riconoscimento non riuscito")
    except Exception as exc:  # noqa: BLE001
        failure = str(exc)
    return blocks, summary, failure


# Quanto aspettare che il server locale riavviato risponda: con i pesi già in
# cache MLX ci mette 20–60 s; il margine copre un Mac lento o sotto pressione.
RECOVERY_READY_TIMEOUT_S = 240


def _recover_local_server(run, cancel: threading.Event) -> bool:
    """Riavvia il server locale del modello se è morto; vero se ora risponde.

    Vale solo per i modelli serviti su questa macchina: un endpoint remoto
    non si riavvia da qui. Se il server è ancora vivo l'errore era un altro, e
    riprovare non servirebbe.
    """
    if run["engine"] != "model" or str(run["provider"] or "") != "local" or not run["adapter_id"]:
        return False
    from urllib.parse import urlparse

    from . import serve_manager

    # Il server esce poco dopo il guasto: si lascia il tempo di accorgersene.
    deadline = time.monotonic() + 5
    while serve_manager.get_status().running and time.monotonic() < deadline:
        time.sleep(0.5)
    if serve_manager.get_status().running:
        return False
    port = urlparse(str(run["endpoint"] or "")).port or 8888
    try:
        serve_manager.start_async(str(run["adapter_id"]), port=port, owner_id=run["owner_id"])
    except (ValueError, RuntimeError):
        return False
    deadline = time.monotonic() + RECOVERY_READY_TIMEOUT_S
    while time.monotonic() < deadline and not cancel.is_set():
        status = serve_manager.get_status()
        progress = serve_manager.progress(status)
        if progress["phase"] == "ready":
            return True
        if progress["error"] or not (status.running or status.starting):
            return False
        time.sleep(2)
    return False


def _worker(run_id: int, cancel: threading.Event) -> None:
    run = None
    try:
        # Inside the try: a start that fails (e.g. "database is locked") must still
        # end the run and free `_ACTIVE`, or "model busy" holds until a restart.
        run = runstore.start(run_id, _now())
        if run is None:
            return
        items = runstore.items_for_run(run_id)
        opts = prefill.PrelabelOptions(
            mode=run["mode"],
            model_mode=run["model_mode"],
            recognition_run_id=run_id,
        )
        client = None
        if run["engine"] == "model":
            profile_id = runstore.run_profile_id(run["job_id"])
            if profile_id is None:
                client = inference.get_vllm_client()
            else:
                from ..adapters import compute_profiles as profilestore

                from . import model_keys

                profile = profilestore.get(profile_id)
                if profile is None:
                    raise RuntimeError("il modello salvato di questa sessione è stato eliminato")
                try:
                    key = model_keys.key_for(profile, run["owner_id"])
                except model_keys.MissingKey as exc:
                    # Stored in Italian, the source language `localize_detail` reads back.
                    raise RuntimeError(msg("saved_model_key_missing", "it", name=exc.name)) from exc
                client = inference.client_for_profile(profile, api_key=key)
        engine = ocrmod.OcrEngine() if run["engine"] == "ocr" else None
        if engine is not None and not engine.available:
            raise RuntimeError("nessun motore OCR locale disponibile")

        for item in items:
            _wait_while_paused(run_id, cancel)
            if cancel.is_set():
                _mark_item(item["id"], "cancelled", ended_at=_now())
                _refresh_counts(run_id)
                continue
            _mark_item(item["id"], "running", started_at=_now(), error=None)
            blocks, summary, failure = _recognize_page(run, item, opts, client, engine, cancel)
            # Un server locale morto a metà (su un Mac piccolo il GPU può andare
            # in timeout e il server esce) non deve far fallire la pagina: lo si
            # riavvia e la si riprova una volta. Solo se il tentativo fallito non
            # ha scritto niente, così non si duplicano blocchi.
            if failure and not blocks and not cancel.is_set() and _recover_local_server(run, cancel):
                _mark_item(item["id"], "running", error=None)
                blocks, summary, failure = _recognize_page(run, item, opts, client, engine, cancel)

            if cancel.is_set() and summary is None:
                _mark_item(item["id"], "cancelled", ended_at=_now())
            elif failure or summary is None:
                _mark_item(item["id"], "failed", error=failure or "nessun risultato", ended_at=_now())
            else:
                raw = {"summary": summary, "blocks": blocks}
                _mark_item(
                    item["id"],
                    "finished",
                    detected=int(summary.get("detected", 0)),
                    inserted=int(summary.get("inserted", 0)),
                    result_json=json.dumps(raw, ensure_ascii=False),
                    ended_at=_now(),
                )
            _refresh_counts(run_id)

        counts = runstore.count_items(run_id)
        if cancel.is_set():
            state = "cancelled"
        elif counts.get("failed", 0):
            state = "finished_with_errors"
        else:
            state = "finished"
        runstore.finish(
            run_id,
            run["job_id"],
            state=state,
            job_state="finished" if state.startswith("finished") else "stopped",
            ended=_now(),
        )
        if run["stop_policy"] == "disable_inference" and run["engine"] == "model":
            try:
                target = runstore.release_target(run["job_id"])
                _release_inference(
                    str(run["provider"]),
                    resource_id=target.get("resource_id"),
                    credential_ref=target.get("credential_ref"),
                )
            except Exception as exc:  # noqa: BLE001
                # Il riconoscimento e i suoi risultati sono già conclusi:
                # un problema nel rilascio della GPU non deve trasformarli in
                # una run fallita. Resta però uno stato visibile da risolvere.
                runstore.set_error(
                    run_id,
                    f"risultati salvati, ma il modello non è stato arrestato: {exc}",
                )
    except Exception as exc:  # noqa: BLE001
        runstore.fail(run_id, run["job_id"] if run else None, error=str(exc), ended=_now())
    finally:
        with _LOCK:
            _ACTIVE.pop(run_id, None)
            _PAUSED.discard(run_id)


def _wait_while_paused(run_id: int, cancel: threading.Event) -> None:
    """Hold the queue between two pages while the run is paused (a stop ends it)."""
    while not cancel.is_set():
        with _LOCK:
            if run_id not in _PAUSED:
                return
        _refresh_counts(run_id)
        cancel.wait(PAUSE_POLL_S)


def cancel_run(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    if run["state"] not in {"queued", "running"}:
        return run
    with _LOCK:
        event = _ACTIVE.get(run_id)
    if event is not None:
        event.set()
    return get_run(run_id)


def _set_paused(run_id: int, paused: bool, lang: str | None = None) -> dict[str, Any]:
    run = get_run(run_id, results="none")
    with _LOCK:
        live = run_id in _ACTIVE
        if live and run["state"] in {"queued", "running"}:
            if paused:
                _PAUSED.add(run_id)
            else:
                _PAUSED.discard(run_id)
    if not live or run["state"] not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail=msg("run_not_active", lang))
    return get_run(run_id, results="none")


def pause_run(run_id: int, lang: str | None = None) -> dict[str, Any]:
    """Stop calling the model after the page in hand; `resume_run` picks up from there."""
    return _set_paused(run_id, True, lang)


def resume_run(run_id: int, lang: str | None = None) -> dict[str, Any]:
    return _set_paused(run_id, False, lang)


def retry_failed_run(
    run_id: int, *, owner_id: int | None = None, allow_stop: bool = False, lang: str | None = None,
) -> dict[str, Any]:
    run = get_run(run_id)
    if run["state"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="attendi la fine della sessione prima di riprovare")
    page_ids = [
        int(item["page_id"])
        for item in run["items"]
        if item["state"] in {"failed", "cancelled"}
    ]
    if not page_ids:
        raise HTTPException(status_code=409, detail="questa sessione non contiene pagine da riprovare")
    # A run on a saved model is retried on the same model (with the retrier's key),
    # never on whatever model is active now.
    profile_id = (
        runstore.run_profile_id(run["job_id"]) if run["engine"] == "model" and run.get("job_id") else None
    )
    return create_run(
        int(run["project_id"]),
        page_ids,
        engine=str(run["engine"]),
        mode=str(run["mode"]),
        model_mode=str(run["model_mode"]),
        stop_policy=str(run["stop_policy"]) if allow_stop else "none",
        owner_id=owner_id,
        lang=lang,
        profile_id=profile_id,
    )


def reconcile_runs() -> None:
    """Un riavvio non perde i risultati finiti: marca solo la coda interrotta."""
    # Il lifespan puo essere riaperto dallo stesso processo (test client,
    # reload controllati). In quel caso i thread ancora vivi non sono run
    # interrotte e non vanno riconciliate come fallite.
    with _LOCK:
        active_ids = set(_ACTIVE)
    runstore.reconcile(
        active_ids,
        message="backend riavviato durante il riconoscimento; riprova le sole pagine non completate",
        ended=_now(),
    )



def export_run(run_id: int, scope: str, collection: int | str | None = None) -> dict[str, Any]:
    """The run's pages and blocks; `collection` limits them to one collection
    (an id, or `"none"` for the pages without one)."""
    run = get_run(run_id)
    if scope not in {"raw", "reviewed"}:
        raise HTTPException(status_code=422, detail="export non valido")
    items = run["items"]
    collection_out = None
    if collection is not None:
        from ..adapters import collections as collectionstore  # noqa: PLC0415

        project_id = int(run["project_id"])
        of_page = collectionstore.page_collections(project_id)
        if collection == "none":
            wanted = None
            collection_out = {"id": None, "name": None}
        else:
            wanted = int(collection)
            collection_out = {"id": wanted, "name": collectionstore.names(project_id).get(wanted)}
        items = [item for item in items if of_page.get(item["page_id"]) == wanted]
    pages: list[dict[str, Any]] = []
    for item in items:
        if scope == "raw":
            blocks = item.get("result", {}).get("blocks", [])
        else:
            blocks = []
            for block in runstore.reviewed_blocks(item["page_id"]):
                value = dict(block)
                value["points"] = json.loads(value["points"] or "[]")
                blocks.append(value)
        pages.append(
            {
                "page_id": item["page_id"],
                "rel_path": item["rel_path"],
                "pdf_page": item.get("pdf_page"),
                "state": item["state"],
                "blocks": blocks,
            }
        )

    return {
        "schema": "tabularium-recognition-export/v1",
        "scope": scope,
        "run": {key: run[key] for key in (
            "id", "project_id", "model_name", "adapter_id", "provider", "endpoint",
            "created_at", "ended_at",
        )},
        "collection": collection_out,
        "pages": pages,
    }


EXPORT_FORMATS = ("json", "text", "csv", "markdown", "pdf")
_EXTENSIONS = {"json": "json", "text": "txt", "csv": "csv", "markdown": "md", "pdf": "pdf"}


def export_document(
    run_id: int, scope: str, fmt: str, collection: int | str | None = None
) -> tuple[Any, str]:
    """The run export in `fmt`, and the file name to give it.

    `json` is the export dict, `text`/`csv`/`markdown` a string, `pdf` an iterator
    of bytes produced page by page: a long document starts downloading at once
    instead of waiting, behind a proxy timeout, for the whole file.
    """
    exported = export_run(run_id, scope, collection)
    filename = f"{run_documents.download_stem(exported)}.{_EXTENSIONS[fmt]}"
    if fmt == "text":
        return _run_text(exported), filename
    if fmt == "csv":
        return _run_csv(exported), filename
    if fmt == "markdown":
        return run_documents.run_markdown(exported, _table_grids(exported)), filename
    if fmt == "pdf":
        return _run_pdf(exported), filename
    return exported, filename


def _run_text(exported: dict[str, Any]) -> str:
    """The readable export, by page and block."""
    sections: list[str] = []
    for page in exported["pages"]:
        content = [
            str(block.get("content") or "").strip()
            for block in page["blocks"]
            if str(block.get("content") or "").strip()
        ]
        sections.append(f"=== {page['rel_path']} ===\n" + "\n\n".join(content))
    return "\n\n".join(sections).rstrip() + "\n"


def _table_grids(exported: dict[str, Any]) -> dict[int, dict]:
    """The saved grids of the reviewed tables; the raw output has none."""
    if exported["scope"] != "reviewed":
        return {}
    ids = [
        int(block["id"])
        for page in exported["pages"]
        for block in page["blocks"]
        if block.get("label") == "Table" and block.get("id") is not None
    ]
    return runstore.table_grids(ids)


def _run_pdf(exported: dict[str, Any]):
    from . import pages as pagesvc  # noqa: PLC0415
    from . import searchable_pdf  # noqa: PLC0415
    from ..adapters import pages as pagestore  # noqa: PLC0415

    grids = _table_grids(exported)

    def page_inputs():
        for page in exported["pages"]:
            row = pagestore.get(int(page["page_id"]))
            image = None
            if row is not None:
                try:
                    image = pagesvc.load_source_image(row)
                except Exception:  # noqa: BLE001 - one unreadable scan must not truncate the file
                    log.warning("searchable PDF: page %s has no readable image", page["page_id"], exc_info=True)
            yield searchable_pdf.PdfPageInput(
                image=image,
                dpi=searchable_pdf.page_dpi(row, image),
                blocks=page["blocks"],
                grids=grids,
                size_px=(int(row["width"] or 0), int(row["height"] or 0)) if row else None,
            )

    return searchable_pdf.write_pdf(page_inputs(), title=run_documents.download_stem(exported))


def _run_csv(exported: dict[str, Any]) -> str:
    """UTF-8 CSV, one row per block, ready for a spreadsheet."""
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow((
        "run_id", "page_id", "rel_path", "page_state", "block_id", "label",
        "kind", "content", "order", "confirmed", "source", "points",
    ))
    for page in exported["pages"]:
        blocks = page["blocks"] or [{}]
        for block in blocks:
            writer.writerow((
                exported["run"]["id"], page["page_id"], page["rel_path"], page["state"],
                block.get("id", ""), block.get("label", ""), block.get("kind", ""),
                block.get("content", ""), block.get("order_idx", ""),
                block.get("confirmed", ""), block.get("prefill_source", ""),
                json.dumps(block.get("points", []), ensure_ascii=False),
            ))
    return output.getvalue()
