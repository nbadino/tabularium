"""API valutazione: esegue metriche sul val split servendo il modello via vLLM."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..db import connect
from ..services import evaluate as evmod
from ..services.i18n import parse_lang
from ..services import auth as authsvc
from .deps import require_resource

router = APIRouter(
    tags=["evaluate"],
    dependencies=[Depends(authsvc.get_current_user)],
)


class EvalRequest(BaseModel):
    server_url: str | None = None
    model: str | None = None
    with_text: bool = True
    limit: int = Field(default=50, ge=1, le=500)
    baseline_server_url: str | None = None
    baseline_model: str | None = None


@router.post("/api/projects/{project_id}/evaluate")
def run_evaluation(
    project_id: int,
    payload: EvalRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Start the evaluation in the background; poll `/evaluate/status` for the report."""
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return evmod.start_evaluation(
        project_id,
        server_url=payload.server_url,
        model=payload.model,
        with_text=payload.with_text,
        limit=payload.limit,
        lang=parse_lang(request.headers.get("accept-language")),
        baseline_server_url=payload.baseline_server_url,
        baseline_model=payload.baseline_model,
    )


@router.get("/api/projects/{project_id}/evaluate/status")
def evaluation_status(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Evaluation state (idle/running/done/error); `report` is set when done."""
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return evmod.evaluation_status(project_id)
