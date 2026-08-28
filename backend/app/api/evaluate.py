"""API valutazione: esegue metriche sul val split servendo il modello via vLLM."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    lang = parse_lang(request.headers.get("accept-language"))

    with connect() as conn:
        _get_project_or_404(conn, project_id)
    try:
        result = evmod.evaluate_project(
            project_id,
            server_url=payload.server_url,
            model=payload.model,
            with_text=payload.with_text,
            limit=payload.limit,
            lang=lang,
        )
        if payload.baseline_server_url or payload.baseline_model:
            baseline = evmod.evaluate_project(
                project_id,
                server_url=payload.baseline_server_url,
                model=payload.baseline_model,
                with_text=payload.with_text,
                limit=payload.limit,
                lang=lang,
            )
            result["baseline"] = baseline
            result["comparison"] = {
                "layout_recall_delta": result["aggregates"]["layout"]["recall"] - baseline["aggregates"]["layout"]["recall"],
                "layout_precision_delta": result["aggregates"]["layout"]["precision"] - baseline["aggregates"]["layout"]["precision"],
                "text_cer_delta": (result["aggregates"]["text"]["mean_cer"] or 0) - (baseline["aggregates"]["text"]["mean_cer"] or 0),
                "order_delta": result["aggregates"]["order"]["mean_levenshtein_norm"] - baseline["aggregates"]["order"]["mean_levenshtein_norm"],
            }
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
