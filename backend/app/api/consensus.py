"""Consensus API: saved model profiles, runs over pages, one page's comparison."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import auth as authsvc
from ..services import consensus as consensussvc
from ..services.i18n import msg, parse_lang
from .deps import require_resource

router = APIRouter(
    tags=["consensus"],
    dependencies=[Depends(authsvc.get_current_user)],
)


def _admin(user: dict = Depends(authsvc.get_current_user)) -> dict:
    # Profiles hold endpoints and keys: the same rule as the global inference config.
    return authsvc.require_admin(user)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ConsensusRunCreate(BaseModel):
    page_ids: list[int] = Field(min_length=1, max_length=consensussvc.MAX_PAGES)
    profile_ids: list[int] = Field(min_length=1, max_length=consensussvc.MAX_MODELS)


@router.get("/api/consensus/profiles")
def list_profiles() -> dict:
    return {"items": consensussvc.list_profiles()}


def _lang(request: Request) -> str:
    return parse_lang(request.headers.get("accept-language"))


@router.post("/api/consensus/profiles", status_code=201)
def save_profile(payload: ProfileCreate, request: Request, user: dict = Depends(_admin)) -> dict:
    return consensussvc.save_current_as_profile(payload.name, actor=user, lang=_lang(request))


@router.delete("/api/consensus/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: int, request: Request, user: dict = Depends(_admin)) -> Response:
    consensussvc.delete_profile(profile_id, actor=user, lang=_lang(request))
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/consensus-runs")
def list_runs(project_id: int, _user: dict = Depends(require_resource(write=False))) -> dict:
    return {"items": consensussvc.list_runs(project_id)}


@router.post("/api/projects/{project_id}/consensus-runs", status_code=202)
def create_run(
    project_id: int,
    payload: ConsensusRunCreate,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    return consensussvc.create_run(
        project_id, payload.page_ids, payload.profile_ids,
        owner_id=user.get("id"), lang=_lang(request),
    )


def _run_of_project(project_id: int, run_id: int, request: Request) -> dict:
    lang = _lang(request)
    run = consensussvc.get_run(run_id, lang)
    if int(run["project_id"]) != project_id:
        raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    return run


@router.get("/api/projects/{project_id}/consensus-runs/{run_id}")
def get_run(
    project_id: int, run_id: int, request: Request,
    _user: dict = Depends(require_resource(write=False)),
) -> dict:
    return _run_of_project(project_id, run_id, request)


@router.post("/api/projects/{project_id}/consensus-runs/{run_id}/cancel")
def cancel_run(
    project_id: int, run_id: int, request: Request,
    _user: dict = Depends(require_resource(write=True)),
) -> dict:
    _run_of_project(project_id, run_id, request)
    return consensussvc.cancel_run(run_id)


@router.get("/api/pages/{page_id}/consensus")
def page_consensus(
    page_id: int,
    request: Request,
    run_id: int | None = None,
    _user: dict = Depends(require_resource(write=False)),
) -> dict:
    lang = _lang(request)
    if run_id is not None:
        # A run id from the query must belong to the page's project (R12).
        from ..adapters import pages as pagestore  # noqa: PLC0415

        run = consensussvc.get_run(run_id, lang)
        if int(run["project_id"]) != pagestore.project_of_page(page_id):
            raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    return consensussvc.page_comparison(page_id, run_id, lang)
