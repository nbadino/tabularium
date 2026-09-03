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
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from ..db import connect
from . import inference, ocr as ocrmod, prefill
from .i18n import msg

_ACTIVE: dict[int, threading.Event] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provider(cfg: dict[str, Any]) -> str:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT provider FROM compute_profiles WHERE active=1 LIMIT 1"
            ).fetchone()
        if row:
            return str(row["provider"])
    except Exception:
        pass
    url = str(cfg.get("url") or "").lower()
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


def _run_out(row, *, include_items: bool = True) -> dict[str, Any]:
    out = dict(row)
    if not include_items:
        return out
    with connect() as conn:
        items = conn.execute(
            """SELECT i.*, p.rel_path, p.status AS page_status,
                      (SELECT COUNT(*) FROM blocks b
                        WHERE b.recognition_run_id=i.run_id AND b.page_id=i.page_id) AS blocks,
                      (SELECT COUNT(*) FROM blocks b
                        WHERE b.recognition_run_id=i.run_id AND b.page_id=i.page_id
                          AND b.confirmed=0) AS drafts
                 FROM recognition_run_items i
                 JOIN pages p ON p.id=i.page_id
                WHERE i.run_id=? ORDER BY i.id""",
            (row["id"],),
        ).fetchall()
    out["items"] = [dict(item) | {"result": json.loads(item["result_json"] or "{}")} for item in items]
    for item in out["items"]:
        item.pop("result_json", None)
    return out


def get_run(run_id: int) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM recognition_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="sessione di riconoscimento non trovata")
    return _run_out(row)


def list_runs(project_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM recognition_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recognition_runs WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
    return [_run_out(row, include_items=False) for row in rows]


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

    cfg = inference.get_inference_config()
    if engine == "model" and not cfg.get("enabled", True):
        raise HTTPException(status_code=409, detail="attiva un modello prima di avviare il riconoscimento")
    placeholders = ",".join("?" for _ in unique_ids)
    with connect() as conn:
        if engine == "model":
            active = conn.execute(
                "SELECT id FROM recognition_runs "
                "WHERE engine='model' AND state IN ('queued','running') "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if active is not None:
                raise HTTPException(
                    status_code=409,
                    detail=f"il modello sta già elaborando la sessione #{active['id']}",
                )
        rows = conn.execute(
            f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})",
            (project_id, *unique_ids),
        ).fetchall()
        found = {int(row["id"]) for row in rows}
        missing = [value for value in unique_ids if value not in found]
        if missing:
            raise HTTPException(status_code=404, detail="una o più pagine non appartengono al progetto")
        if engine == "model":
            # L'endpoint dichiarato deve rispondere PRIMA di accodare migliaia
            # di pagine destinate a fallire una a una. La sonda è corta e non
            # avvia nulla: per un serverless freddo è il segnale giusto per
            # andare a riattivarlo dalla pagina Modelli, non un falso blocco.
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
        provider = _provider(cfg)
        job = conn.execute(
            """INSERT INTO jobs
               (kind, owner_id, project_id, provider, state, heartbeat_at,
                command_json, recovery_strategy)
               VALUES ('recognition',?,?,?,?,?,?,?)""",
            (
                owner_id,
                project_id,
                provider,
                "queued",
                _now(),
                json.dumps({"page_ids": unique_ids, "engine": engine}),
                "persisted-results-mark-interrupted",
            ),
        )
        run = conn.execute(
            """INSERT INTO recognition_runs
               (project_id, owner_id, job_id, state, engine, mode, model_mode,
                model_name, adapter_id, provider, endpoint, stop_policy, total_pages)
               VALUES (?,?,?,'queued',?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                owner_id,
                job.lastrowid,
                engine,
                mode,
                model_mode,
                cfg.get("model") if engine == "model" else getattr(ocrmod.OcrEngine(), "name", "ocr"),
                cfg.get("adapter_id") if engine == "model" else "local-ocr",
                provider,
                cfg.get("url") if engine == "model" else None,
                stop_policy,
                len(unique_ids),
            ),
        )
        run_id = int(run.lastrowid)
        conn.executemany(
            "INSERT INTO recognition_run_items (run_id,page_id,state) VALUES (?,?,'queued')",
            [(run_id, page_id) for page_id in unique_ids],
        )
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
    fields = ["state=?"]
    params: list[Any] = [state]
    for key in ("detected", "inserted", "result_json", "error", "started_at", "ended_at"):
        if key in values:
            fields.append(f"{key}=?")
            params.append(values[key])
    params.append(item_id)
    with connect() as conn:
        conn.execute(f"UPDATE recognition_run_items SET {', '.join(fields)} WHERE id=?", params)


def _refresh_counts(run_id: int) -> None:
    now = _now()
    with connect() as conn:
        counts = {
            row["state"]: int(row["n"])
            for row in conn.execute(
                "SELECT state,COUNT(*) AS n FROM recognition_run_items WHERE run_id=? GROUP BY state",
                (run_id,),
            ).fetchall()
        }
        completed = sum(counts.get(key, 0) for key in ("finished", "failed", "cancelled"))
        conn.execute(
            """UPDATE recognition_runs
                  SET completed_pages=?, succeeded_pages=?, failed_pages=?, heartbeat_at=?
                WHERE id=?""",
            (completed, counts.get("finished", 0), counts.get("failed", 0), now, run_id),
        )
        conn.execute(
            "UPDATE jobs SET heartbeat_at=? WHERE id=(SELECT job_id FROM recognition_runs WHERE id=?)",
            (now, run_id),
        )


def _worker(run_id: int, cancel: threading.Event) -> None:
    started = _now()
    with connect() as conn:
        run = conn.execute("SELECT * FROM recognition_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            return
        conn.execute(
            "UPDATE recognition_runs SET state='running',started_at=?,heartbeat_at=? WHERE id=?",
            (started, started, run_id),
        )
        conn.execute("UPDATE jobs SET state='running',heartbeat_at=? WHERE id=?", (started, run["job_id"]))
        items = conn.execute(
            "SELECT id,page_id FROM recognition_run_items WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()

    try:
        opts = prefill.PrelabelOptions(
            mode=run["mode"],
            model_mode=run["model_mode"],
            recognition_run_id=run_id,
        )
        client = inference.get_vllm_client() if run["engine"] == "model" else None
        engine = ocrmod.OcrEngine() if run["engine"] == "ocr" else None
        if engine is not None and not engine.available:
            raise RuntimeError("nessun motore OCR locale disponibile")

        for item in items:
            if cancel.is_set():
                _mark_item(item["id"], "cancelled", ended_at=_now())
                _refresh_counts(run_id)
                continue
            _mark_item(item["id"], "running", started_at=_now(), error=None)
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

        with connect() as conn:
            counts = {
                row["state"]: int(row["n"])
                for row in conn.execute(
                    "SELECT state,COUNT(*) AS n FROM recognition_run_items WHERE run_id=? GROUP BY state",
                    (run_id,),
                ).fetchall()
            }
            if cancel.is_set():
                state = "cancelled"
            elif counts.get("failed", 0):
                state = "finished_with_errors"
            else:
                state = "finished"
            ended = _now()
            conn.execute(
                "UPDATE recognition_runs SET state=?,ended_at=?,heartbeat_at=? WHERE id=?",
                (state, ended, ended, run_id),
            )
            conn.execute(
                "UPDATE jobs SET state=?,ended_at=?,heartbeat_at=? WHERE id=?",
                ("finished" if state.startswith("finished") else "stopped", ended, ended, run["job_id"]),
            )
        if run["stop_policy"] == "disable_inference" and run["engine"] == "model":
            try:
                cfg = inference.get_inference_config()
                _release_inference(
                    str(run["provider"]),
                    resource_id=cfg.get("resource_id"),
                    credential_ref=cfg.get("provider_credential_ref"),
                )
            except Exception as exc:  # noqa: BLE001
                # Il riconoscimento e i suoi risultati sono già conclusi:
                # un problema nel rilascio della GPU non deve trasformarli in
                # una run fallita. Resta però uno stato visibile da risolvere.
                with connect() as conn:
                    conn.execute(
                        "UPDATE recognition_runs SET state='finished_with_errors',error=? WHERE id=?",
                        (f"risultati salvati, ma il modello non è stato arrestato: {exc}", run_id),
                    )
    except Exception as exc:  # noqa: BLE001
        ended = _now()
        with connect() as conn:
            conn.execute(
                "UPDATE recognition_runs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
                (str(exc), ended, ended, run_id),
            )
            conn.execute(
                "UPDATE jobs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
                (str(exc), ended, ended, run["job_id"]),
            )
    finally:
        with _LOCK:
            _ACTIVE.pop(run_id, None)


def cancel_run(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    if run["state"] not in {"queued", "running"}:
        return run
    with _LOCK:
        event = _ACTIVE.get(run_id)
    if event is not None:
        event.set()
    return get_run(run_id)


def retry_failed_run(run_id: int, *, owner_id: int | None = None, allow_stop: bool = False) -> dict[str, Any]:
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
    return create_run(
        int(run["project_id"]),
        page_ids,
        engine=str(run["engine"]),
        mode=str(run["mode"]),
        model_mode=str(run["model_mode"]),
        stop_policy=str(run["stop_policy"]) if allow_stop else "none",
        owner_id=owner_id,
    )


def reconcile_runs() -> None:
    """Un riavvio non perde i risultati finiti: marca solo la coda interrotta."""
    ended = _now()
    # Il lifespan puo essere riaperto dallo stesso processo (test client,
    # reload controllati). In quel caso i thread ancora vivi non sono run
    # interrotte e non vanno riconciliati come falliti.
    with _LOCK:
        active_ids = set(_ACTIVE)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,job_id FROM recognition_runs WHERE state IN ('queued','running')"
        ).fetchall()
        for row in rows:
            if int(row["id"]) in active_ids:
                continue
            message = "backend riavviato durante il riconoscimento; riprova le sole pagine non completate"
            conn.execute(
                "UPDATE recognition_runs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
                (message, ended, ended, row["id"]),
            )
            conn.execute(
                "UPDATE recognition_run_items SET state='failed',error=?,ended_at=? "
                "WHERE run_id=? AND state IN ('queued','running')",
                (message, ended, row["id"]),
            )
            if row["job_id"]:
                conn.execute(
                    "UPDATE jobs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
                    (message, ended, ended, row["job_id"]),
                )


def export_run(run_id: int, scope: str) -> dict[str, Any]:
    run = get_run(run_id)
    if scope not in {"raw", "reviewed"}:
        raise HTTPException(status_code=422, detail="export non valido")
    pages: list[dict[str, Any]] = []
    with connect() as conn:
        for item in run["items"]:
            if scope == "raw":
                blocks = item.get("result", {}).get("blocks", [])
            else:
                rows = conn.execute(
                    "SELECT id,label,kind,points,content,order_idx,confirmed,prefill_source "
                    "FROM blocks WHERE page_id=? ORDER BY COALESCE(order_idx,999999),id",
                    (item["page_id"],),
                ).fetchall()
                blocks = []
                for block in rows:
                    value = dict(block)
                    value["points"] = json.loads(value["points"] or "[]")
                    blocks.append(value)
            pages.append(
                {
                    "page_id": item["page_id"],
                    "rel_path": item["rel_path"],
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
        "pages": pages,
    }


def export_run_text(run_id: int, scope: str) -> str:
    """Versione leggibile dell'export, ordinata per pagina e blocco."""
    exported = export_run(run_id, scope)
    sections: list[str] = []
    for page in exported["pages"]:
        content = [
            str(block.get("content") or "").strip()
            for block in page["blocks"]
            if str(block.get("content") or "").strip()
        ]
        sections.append(f"=== {page['rel_path']} ===\n" + "\n\n".join(content))
    return "\n\n".join(sections).rstrip() + "\n"


def export_run_csv(run_id: int, scope: str) -> str:
    """CSV tabellare UTF-8: una riga per blocco, pronto per fogli di calcolo."""
    exported = export_run(run_id, scope)
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
