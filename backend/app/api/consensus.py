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


class AdoptRequest(BaseModel):
    model_key: str = Field(min_length=1, max_length=64)
    # None: every page the model finished in the run.
    page_ids: list[int] | None = Field(default=None, max_length=consensussvc.MAX_PAGES)
    replace_human: bool = False


class AdoptBlockRequest(BaseModel):
    model_key: str = Field(min_length=1, max_length=64)
    page_id: int
    # The block's position in the model's output for the page.
    index: int = Field(ge=0)


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


@router.post("/api/projects/{project_id}/consensus-runs/{run_id}/retry", status_code=202)
def retry_run(
    project_id: int, run_id: int, request: Request,
    _user: dict = Depends(require_resource(write=True)),
) -> dict:
    _run_of_project(project_id, run_id, request)
    return consensussvc.retry_failed(run_id, _lang(request))


@router.post("/api/projects/{project_id}/consensus-runs/{run_id}/adopt")
def adopt_output(
    project_id: int, run_id: int, payload: AdoptRequest, request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """One model's reading of the run becomes the drafts of its pages."""
    _run_of_project(project_id, run_id, request)
    out = consensussvc.adopt_output(
        run_id, payload.model_key, payload.page_ids,
        replace_human=payload.replace_human, actor=user, lang=_lang(request),
    )
    for page in out["written"]:
        _notify_live(page["page_id"], page["annotation_revision"], user)
    return out


@router.post("/api/projects/{project_id}/consensus-runs/{run_id}/adopt-block")
def adopt_block(
    project_id: int, run_id: int, payload: AdoptBlockRequest, request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """One block of a model's reading (a table only the models read) becomes a draft."""
    _run_of_project(project_id, run_id, request)
    out = consensussvc.adopt_block(
        run_id, payload.model_key, payload.page_id, payload.index, actor=user, lang=_lang(request),
    )
    _notify_live(payload.page_id, out["annotation_revision"], user)
    return out


def _notify_live(page_id: int, revision: int, user: dict) -> None:
    # A Studio open on the page reloads; a lost event must not fail the write.
    try:
        from ..services import live  # noqa: PLC0415

        live.hub.publish(page_id, {
            "type": "annotations_changed", "page_id": page_id, "revision": revision,
            "action": "consensus", "author": {"id": user.get("id"), "username": user.get("username")},
        })
    except Exception:  # noqa: BLE001
        pass


@router.get("/api/projects/{project_id}/consensus-runs/{run_id}/pages")
def run_pages(
    project_id: int, run_id: int, request: Request,
    _user: dict = Depends(require_resource(write=False)),
) -> dict:
    """Each page of the run: the models' states, the agreement, what is left to look at."""
    _run_of_project(project_id, run_id, request)
    return {"items": consensussvc.run_pages(run_id, _lang(request))}


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


@router.post("/api/blocks/{block_id}/consensus")
def block_consensus(
    block_id: int,
    grid: dict,
    request: Request,
    _user: dict = Depends(require_resource(write=False)),
) -> dict:
    """The models' readings for the table being edited; `grid` is the editor's current one."""
    return consensussvc.block_comparison(block_id, grid, _lang(request))

