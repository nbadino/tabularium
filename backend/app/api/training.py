"""API training: stato GPU, start/stop run, status e streaming log via SSE."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db import connect
from ..schemas import TrainConfig
from ..services import trainer
from ..services.i18n import parse_lang
from ..services import auth as authsvc
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
    return {"gpus": trainer.gpu_snapshot(), "available": trainer.gpu_snapshot() != []}


@router.post("/api/projects/{project_id}/training/start")
def training_start(
    project_id: int,
    payload: TrainConfig,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    _require_project(project_id)
    try:
        return trainer.start_run(project_id, payload.model_dump(exclude_unset=True))
    except (FileNotFoundError, ValueError) as exc:
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


@router.get("/api/projects/{project_id}/training/status")
def training_status(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    _require_project(project_id)
    return trainer._status(project_id)  # noqa: SLF001


@router.post("/api/projects/{project_id}/training/stop")
def training_stop(
    project_id: int,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    _require_project(project_id)
    return trainer.stop_run(project_id)


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
