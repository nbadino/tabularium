"""Consensus API: saved model profiles, runs over pages, one page's comparison."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import auth as authsvc
from ..services import consensus as consensussvc
from ..services import consensus_decisions as decisionsvc
from ..services import model_keys
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


class ModelKeyIn(BaseModel):
    # The user's own key for a saved model. Write-only: no route ever returns it.
    api_key: str = Field(max_length=4096)


class ConsensusRunCreate(BaseModel):
    page_ids: list[int] = Field(min_length=1, max_length=consensussvc.MAX_PAGES)
    profile_ids: list[int] = Field(min_length=1, max_length=consensussvc.MAX_MODELS)


@router.get("/api/consensus/profiles")
def list_profiles(user: dict = Depends(authsvc.get_current_user)) -> dict:
    """The saved models, with whether the current user has added a key for each."""
    return {"items": consensussvc.list_profiles(user)}


def _lang(request: Request) -> str:
    return parse_lang(request.headers.get("accept-language"))


@router.post("/api/consensus/profiles", status_code=201)
def save_profile(payload: ProfileCreate, request: Request, user: dict = Depends(_admin)) -> dict:
    return consensussvc.save_current_as_profile(payload.name, actor=user, lang=_lang(request))


@router.put("/api/consensus/profiles/{profile_id}/key")
def set_model_key(
    profile_id: int, payload: ModelKeyIn, request: Request,
    user: dict = Depends(authsvc.get_current_user),
) -> dict:
    """Store the current user's own API key for a saved model (encrypted, write-only)."""
    return model_keys.set_key(profile_id, user, payload.api_key, _lang(request))


@router.delete("/api/consensus/profiles/{profile_id}/key", status_code=204)
def delete_model_key(
    profile_id: int, request: Request, user: dict = Depends(authsvc.get_current_user),
) -> Response:
    """Remove the current user's own key; nobody else's is touched."""
    model_keys.delete_key(profile_id, user, _lang(request))
    return Response(status_code=204)


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
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    _run_of_project(project_id, run_id, request)
    return consensussvc.retry_failed(run_id, _lang(request), user_id=user.get("id"))


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



# --- shared decisions --------------------------------------------------------------


class CellState(BaseModel):
    r: int = Field(ge=0, le=100_000)
    c: int = Field(ge=0, le=10_000)
    text: str | None = Field(default=None, max_length=4000)
    verified: bool | None = None
    source: str | None = Field(default=None, max_length=120)


class BlockState(BaseModel):
    # A saved block's id, or the editor's local id for one not saved yet.
    id: str | None = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=80)
    content: str = Field(default="", max_length=20_000)


class DecisionState(BaseModel):
    cells: list[CellState] | None = Field(default=None, max_length=5000)
    blocks: list[BlockState] | None = Field(default=None, max_length=200)


class DecisionCreate(BaseModel):
    run_id: int
    kind: Literal["resolved", "reopened", "use_model", "removed", "confirm_mine", "group_confirm", "comment"]
    cluster_id: int | None = Field(default=None, ge=0)
    block_id: int | None = None
    model_key: str | None = Field(default=None, min_length=1, max_length=64)
    before: DecisionState | None = None
    after: DecisionState | None = None
    comment: str | None = Field(default=None, max_length=2000)
    # For a comment: the decision it answers.
    about_id: int | None = None


class RevertRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


@router.get("/api/pages/{page_id}/consensus/decisions")
def page_decisions(
    page_id: int,
    request: Request,
    run_id: int | None = None,
    _user: dict = Depends(require_resource(write=False)),
) -> dict:
    """Who settled what on the page in a comparison (newest first), and the areas settled."""
    from ..adapters import consensus as runstore  # noqa: PLC0415
    from ..adapters import pages as pagestore  # noqa: PLC0415

    lang = _lang(request)
    if run_id is None:
        run_id = runstore.latest_run_for_page(page_id)
    else:
        run = consensussvc.get_run(run_id, lang)
        if int(run["project_id"]) != pagestore.project_of_page(page_id):
            raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    return decisionsvc.page_state(run_id, page_id)


@router.post("/api/pages/{page_id}/consensus/decisions")
def record_decision(
    page_id: int,
    payload: DecisionCreate,
    request: Request,
    response: Response,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Record a decision taken in the Studio; 200 with no decision when it changes nothing."""
    out = decisionsvc.record(page_id, payload.model_dump(exclude_none=True), user, _lang(request))
    response.status_code = 201 if out["decision"] is not None else 200
    return out


@router.post("/api/pages/{page_id}/consensus/decisions/{decision_id}/revert", status_code=201)
def revert_decision(
    page_id: int,
    decision_id: int,
    payload: RevertRequest,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Put back the value the decision replaced; the revert is a decision too."""
    return decisionsvc.revert(page_id, decision_id, payload.comment, user, _lang(request))
