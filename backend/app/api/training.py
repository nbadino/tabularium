"""API training: stato GPU, start/stop run, status e streaming log via SSE."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db import connect
from ..schemas import TrainConfig
from ..services import trainer
from ..services import custom_models
from ..services import checkpoint_prepare, auto_training
from ..services.i18n import parse_lang
from ..services import auth as authsvc
from ..services import audit as auditsvc
from .deps import require_resource

router = APIRouter(
    tags=["training"],
    dependencies=[Depends(authsvc.get_current_user)],
)


def _require_project(project_id: int) -> None:
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    with connect() as conn:
        _get_project_or_404(conn, project_id)


@router.get("/api/system/gpu")
def system_gpu() -> dict:
    gpus = trainer.gpu_snapshot()
    return {"gpus": gpus, "available": bool(gpus)}


@router.post("/api/projects/{project_id}/training/start")
def training_start(
    project_id: int,
    payload: TrainConfig,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    _require_project(project_id)
    lang = parse_lang(request.headers.get("accept-language"))
    try:
        result = trainer.start_run(
            project_id,
            payload.model_dump(exclude_unset=True),
            owner_id=user.get("id"),
            lang=lang,
        )
        with connect() as conn:
            auditsvc.record(conn, user, "training.started", resource_type="project", resource_id=project_id, payload={"run_id": result.get("run", {}).get("run_id")})
        return result
    except (FileNotFoundError, ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/training/preflight")
def training_preflight(
    project_id: int,
    payload: TrainConfig,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Verifica dataset, repo/env e GPU prima dell'avvio."""
    _require_project(project_id)
    lang = parse_lang(request.headers.get("accept-language"))
    return trainer.preflight(project_id, payload.model_dump(exclude_unset=True), lang=lang)


@router.get("/api/projects/{project_id}/training/runs")
def training_runs(
    project_id: int,
    limit: int = 50,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Storico delle run del progetto, dalla più recente.

    Prima la pagina conosceva solo l'ultima run: confrontare due tentativi o
    riprendere da un checkpoint vecchio richiedeva di ricordarsi un ID.
    """
    _require_project(project_id)
    return {"items": trainer.list_runs(project_id, limit=max(1, min(200, int(limit))))}


@router.get("/api/projects/{project_id}/training/status")
def training_status(
    project_id: int,
    run_id: str | None = None,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    _require_project(project_id)
    return trainer._status(project_id, run_id=run_id)  # noqa: SLF001


@router.post("/api/projects/{project_id}/training/stop")
def training_stop(
    project_id: int,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    _require_project(project_id)
    result = trainer.stop_run(project_id)
    with connect() as conn:
        auditsvc.record(conn, user, "training.stopped", resource_type="project", resource_id=project_id)
    return result


@router.post("/api/projects/{project_id}/training/cleanup")
def training_cleanup(
    project_id: int,
    payload: dict,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Rimuove la directory di una run remota dopo una conferma esplicita."""
    _require_project(project_id)
    try:
        result = trainer.cleanup_remote_run(project_id, str(payload.get("run_id") or ""))
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        auditsvc.record(
            conn,
            user,
            "training.remote_cleanup",
            resource_type="project",
            resource_id=project_id,
            payload={"run_id": result["run_id"]},
        )
    return result


@router.get("/api/projects/{project_id}/training/prepare-checkpoint")
def training_prepare_checkpoint_status(
    project_id: int,
    run_id: str,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Stato del merge/registrazione del checkpoint, con il log del job."""
    _require_project(project_id)
    try:
        return checkpoint_prepare._job_status(project_id, run_id)  # noqa: SLF001
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/training/prepare-checkpoint")
def training_prepare_checkpoint(
    project_id: int,
    payload: dict,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Avvia in background il merge (remoto se la run era remota) e la registrazione."""
    _require_project(project_id)
    run_id = str(payload.get("run_id") or "")
    try:
        result = checkpoint_prepare.start_prepare(project_id, run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        auditsvc.record(
            conn,
            user,
            "training.checkpoint_prepare_started",
            resource_type="project",
            resource_id=project_id,
            payload={"run_id": run_id, "mode": result.get("plan", {}).get("mode")},
        )
    return result


@router.post("/api/projects/{project_id}/training/auto")
def training_auto(
    project_id: int,
    payload: dict,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Un click: noleggia la GPU, prepara l'ambiente e avvia la run.

    Spende denaro sull'account Vast.ai: resta riservato all'amministratore,
    che possiede la chiave. Il job è persistito e la UI mostra le fasi.
    """
    from ..services import vault

    authsvc.require_admin(user)
    _require_project(project_id)
    cfg_in = payload.get("config")
    if not isinstance(cfg_in, dict):
        raise HTTPException(status_code=400, detail="configurazione di training mancante")
    cfg = TrainConfig(**cfg_in).model_dump(exclude_unset=True)
    ref = str(payload.get("credential_ref") or "vault:vast_api_key").strip()
    api_key = str(payload.get("api_key") or "").strip() or vault.get(ref)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="API Key di Vast.ai non configurata: aggiungila in Modelli → Cloud.",
        )
    try:
        job = auto_training.start_auto(
            project_id,
            cfg,
            api_key,
            owner_id=user.get("id"),
            lang=parse_lang(payload.get("lang")),
            max_dph=max(0.1, min(20.0, float(payload.get("max_dph") or 1.0))),
            min_gpu_ram_gb=max(8, min(160, int(payload.get("min_gpu_ram_gb") or 24))),
            disk_gb=max(20, min(500, int(payload.get("disk_gb") or 100))),
        )
    except (ValueError, TypeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        auditsvc.record(
            conn,
            user,
            "training.auto_started",
            resource_type="project",
            resource_id=project_id,
            payload={"job_id": job["id"], "adapter_id": job["adapter_id"], "profile": job["profile"]},
        )
    return job


@router.get("/api/projects/{project_id}/training/auto/{job_id}")
def training_auto_status(
    project_id: int,
    job_id: str,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    _require_project(project_id)
    try:
        return auto_training.get_job(job_id, project_id=project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/training/auto/{job_id}/cancel")
def training_auto_cancel(
    project_id: int,
    job_id: str,
    payload: dict | None = None,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    from ..services import vault

    authsvc.require_admin(user)
    _require_project(project_id)
    try:
        auto_training.get_job(job_id, project_id=project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = payload or {}
    ref = str(payload.get("credential_ref") or "vault:vast_api_key").strip()
    api_key = str(payload.get("api_key") or "").strip() or vault.get(ref)
    try:
        return auto_training.cancel_auto(job_id, api_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/training/register-model")
def training_register_model(
    project_id: int,
    payload: dict,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Rende selezionabile nel Registro il checkpoint dell'ultima run finita."""
    _require_project(project_id)
    try:
        model = custom_models.register_training_checkpoint(
            project_id, str(payload.get("run_id") or "")
        )
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        auditsvc.record(
            conn,
            user,
            "training.model_registered",
            resource_type="project",
            resource_id=project_id,
            payload={"run_id": model.get("source_run_id"), "adapter_id": model.get("id")},
        )
    return model


@router.get("/api/projects/{project_id}/training/stream")
def training_stream(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> StreamingResponse:
    _require_project(project_id)

    async def gen():
        last = None
        idle = 0
        while True:
            try:
                st = trainer._status(project_id)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                break
            state = st["run"].get("state") if st.get("run") else None
            payload = json.dumps(
                {
                    "state": state,
                    "active": st["active"],
                    "log_tail": st["log_tail"],
                    "metrics": st["metrics"][-200:],
                    "gpu": st["gpu"],
                },
                ensure_ascii=False,
            )
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
                idle = 0
            else:
                idle += 1
            if not st["active"] and state in ("finished", "failed", "stopped"):
                # termina poco dopo la fine della run
                if idle >= 5:
                    yield "data: {\"state\": \"done\"}\n\n"
                    break
            await asyncio.sleep(2.0)

    return StreamingResponse(gen(), media_type="text/event-stream")
