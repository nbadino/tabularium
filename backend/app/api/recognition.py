"""API delle sessioni persistenti di riconoscimento bulk."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from ..services import auth as authsvc
from ..services import recognition as recognitionsvc
from ..services.i18n import parse_lang
from .deps import require_resource

router = APIRouter(
    tags=["recognition"],
    dependencies=[Depends(authsvc.get_current_user)],
)


class RecognitionRunCreate(BaseModel):
    page_ids: list[int] = Field(min_length=1, max_length=10000)
    engine: Literal["model", "ocr"] = "model"
    mode: Literal["merge", "replace_drafts", "replace_all"] = "replace_drafts"
    model_mode: Literal["native", "two_stage", "end2end"] = "native"
    stop_policy: Literal["none", "disable_inference"] = "disable_inference"


@router.get("/api/recognition-runs")
def list_recognition_runs(
    project_id: int | None = Query(default=None),
    user: dict = Depends(authsvc.get_current_user),
) -> dict:
    if project_id is not None:
        authsvc.require_project_access(project_id, user, write=False)
    runs = recognitionsvc.list_runs(project_id)
    if project_id is None and not authsvc.is_admin(user):
        runs = [
            run for run in runs
            if authsvc.get_project_access(int(run["project_id"]), user) is not None
        ]
    return {"items": runs}


@router.post("/api/projects/{project_id}/recognition-runs", status_code=202)
def create_recognition_run(
    project_id: int,
    payload: RecognitionRunCreate,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    if payload.stop_policy != "none" and not authsvc.is_admin(user):
        raise HTTPException(status_code=403, detail="solo un amministratore può disattivare l'inferenza globale")
    return recognitionsvc.create_run(
        project_id,
        payload.page_ids,
        engine=payload.engine,
        mode=payload.mode,
        model_mode=payload.model_mode,
        stop_policy=payload.stop_policy,
        owner_id=user.get("id"),
        lang=parse_lang(request.headers.get("accept-language")),
    )


@router.get("/api/projects/{project_id}/recognition-runs/{run_id}")
def get_recognition_run(
    project_id: int,
    run_id: int,
    _user: dict = Depends(require_resource(write=False)),
) -> dict:
    run = recognitionsvc.get_run(run_id)
    if int(run["project_id"]) != project_id:
        raise HTTPException(status_code=404, detail="sessione di riconoscimento non trovata")
    return run


@router.post("/api/projects/{project_id}/recognition-runs/{run_id}/cancel")
def cancel_recognition_run(
    project_id: int,
    run_id: int,
    _user: dict = Depends(require_resource(write=True)),
) -> dict:
    run = recognitionsvc.get_run(run_id)
    if int(run["project_id"]) != project_id:
        raise HTTPException(status_code=404, detail="sessione di riconoscimento non trovata")
    return recognitionsvc.cancel_run(run_id)


@router.post("/api/projects/{project_id}/recognition-runs/{run_id}/retry", status_code=202)
def retry_recognition_run(
    project_id: int,
    run_id: int,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    run = recognitionsvc.get_run(run_id)
    if int(run["project_id"]) != project_id:
        raise HTTPException(status_code=404, detail="sessione di riconoscimento non trovata")
    return recognitionsvc.retry_failed_run(
        run_id,
        owner_id=user.get("id"),
        allow_stop=authsvc.is_admin(user),
    )


@router.get("/api/projects/{project_id}/recognition-runs/{run_id}/export")
def export_recognition_run(
    project_id: int,
    run_id: int,
    scope: Literal["raw", "reviewed"] = "reviewed",
    format: Literal["json", "text", "csv"] = "json",
    _user: dict = Depends(require_resource(write=False)),
):
    run = recognitionsvc.get_run(run_id)
    if int(run["project_id"]) != project_id:
        raise HTTPException(status_code=404, detail="sessione di riconoscimento non trovata")
    if format == "text":
        return PlainTextResponse(
            recognitionsvc.export_run_text(run_id, scope),
            headers={
                "Content-Disposition": f'attachment; filename="tabularium-run-{run_id}-{scope}.txt"'
            },
        )
    if format == "csv":
        return Response(
            "\ufeff" + recognitionsvc.export_run_csv(run_id, scope),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="tabularium-run-{run_id}-{scope}.csv"'
            },
        )
    return recognitionsvc.export_run(run_id, scope)
