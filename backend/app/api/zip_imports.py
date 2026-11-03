"""API ZIP import: chunked upload, inspection, import job (docs/COLLECTIONS.md)."""
from __future__ import annotations

import threading
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints

from ..adapters import collections as collectionstore
from ..services import auth as authsvc
from ..services import zip_import
from ..services.i18n import msg, parse_lang
from .deps import require_resource

router = APIRouter(
    tags=["zip-imports"],
    dependencies=[Depends(authsvc.get_current_user)],
)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class ZipUploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)


class ZipImportIn(BaseModel):
    # `collections`: one collection per first-level folder; `flat`: every page
    # into `collection_id` (`null` is "no collection").
    layout: Literal["collections", "flat"]
    collection_id: int | None = None
    # A flat import into a new collection with this name (an existing one with
    # the same name, in any case, is reused). Exclusive with `collection_id`.
    collection_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)] | None = None


def _lang(request: Request) -> str:
    return parse_lang(request.headers.get("accept-language"))


def _refusal(exc: zip_import.UploadError, lang: str) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=msg(exc.code, lang, **exc.params))


def _project_or_404(project_id: int) -> None:
    from .projects import _get_project_or_404  # noqa: PLC0415

    _get_project_or_404(None, project_id)


@router.post("/api/projects/{project_id}/zip-uploads", status_code=201)
def start_upload(
    project_id: int, payload: ZipUploadIn, request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    _project_or_404(project_id)
    try:
        return zip_import.start_upload(project_id, payload.filename, payload.size)
    except zip_import.UploadError as exc:
        raise _refusal(exc, _lang(request)) from exc


@router.put("/api/projects/{project_id}/zip-uploads/{upload_id}")
async def upload_chunk(
    project_id: int, upload_id: str, request: Request,
    offset: int = Query(ge=0),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    lang = _lang(request)
    declared = int(request.headers.get("content-length") or 0)
    if declared > zip_import.config.ZIP_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail=msg("zip_chunk_too_large", lang))
    data = await request.body()
    try:
        return zip_import.append_chunk(project_id, upload_id, offset, data)
    except zip_import.UploadError as exc:
        raise _refusal(exc, lang) from exc


@router.delete("/api/projects/{project_id}/zip-uploads/{upload_id}")
def cancel_upload(
    project_id: int, upload_id: str, request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    try:
        zip_import.cancel(project_id, upload_id)
    except zip_import.UploadError as exc:
        raise _refusal(exc, _lang(request)) from exc
    return {"cancelled": True}


@router.post("/api/projects/{project_id}/zip-uploads/{upload_id}/inspect")
def inspect_upload(
    project_id: int, upload_id: str, request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    try:
        return zip_import.inspect(project_id, upload_id)
    except zip_import.UploadError as exc:
        raise _refusal(exc, _lang(request)) from exc


def _run_job(job_id: str, project_id: int, upload_id: str, payload: ZipImportIn, lang: str) -> None:
    def progress(phase: str, current: int, total: int) -> None:
        with _jobs_lock:
            _jobs[job_id].update(phase=phase, current=current, total=total)

    try:
        report = zip_import.run_import(
            project_id, upload_id, layout=payload.layout, collection_id=payload.collection_id,
            collection_name=payload.collection_name, lang=lang, on_progress=progress,
        )
    except zip_import.UploadError as exc:
        with _jobs_lock:
            _jobs[job_id].update(status="failed", error=msg(exc.code, lang, **exc.params))
        return
    except Exception as exc:  # noqa: BLE001  (the job must end with a state the UI can show)
        with _jobs_lock:
            _jobs[job_id].update(status="failed", error=str(exc))
        return
    with _jobs_lock:
        _jobs[job_id].update(status="done", report=report)


@router.post("/api/projects/{project_id}/zip-uploads/{upload_id}/import")
def import_upload(
    project_id: int, upload_id: str, payload: ZipImportIn, request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    lang = _lang(request)
    if payload.collection_id is not None and payload.collection_name:
        raise HTTPException(status_code=422, detail=msg("zip_target_ambiguous", lang))
    if payload.collection_id is not None and collectionstore.get(project_id, payload.collection_id) is None:
        raise HTTPException(status_code=404, detail=msg("collection_not_found", lang))
    try:
        # Refuse at once what the job would refuse: an unknown, incomplete or
        # already imported archive, or one over a global limit.
        zip_import.inspect(project_id, upload_id)
    except zip_import.UploadError as exc:
        raise _refusal(exc, lang) from exc
    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id, "project_id": project_id, "status": "running",
            "phase": "extracting", "current": 0, "total": 0, "report": None, "error": None,
        }
    threading.Thread(target=_run_job, args=(job_id, project_id, upload_id, payload, lang), daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/projects/{project_id}/zip-jobs/{job_id}")
def import_job(
    project_id: int, job_id: str, request: Request,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        job = dict(job) if job else None
    if not job or job.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail=msg("zip_job_not_found", _lang(request)))
    return job
