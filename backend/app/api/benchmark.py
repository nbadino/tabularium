"""API benchmark multi-modello (M17): superficie a sé, staccata dal Fine-tuning.

Si scelgono **modelli** (target: adapter + endpoint + nome servito) e **pagine**
(con annotazione verificata). Il riferimento è l'annotazione umana; il benchmark
non scrive nel DB, produce solo un report su disco.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import connect
from ..services import auth as authsvc
from ..services import benchmark as benchmod
from ..services import dataset_builder as builder
from ..services.i18n import parse_lang
from .deps import require_resource
from .projects import _get_project_or_404

router = APIRouter(
    tags=["benchmark"],
    dependencies=[Depends(authsvc.get_current_user)],
)


class BenchmarkTarget(BaseModel):
    adapter_id: str
    url: str | None = None
    model: str | None = None
    provider: str | None = None
    api_key: str | None = None


class BenchmarkRequest(BaseModel):
    targets: list[BenchmarkTarget]
    page_ids: list[int] | None = None
    limit: int = Field(default=10, ge=1, le=200)
    with_text: bool = True
    use_cache: bool = True
    table_only: bool = False


@router.get("/api/projects/{project_id}/benchmark/pages")
def benchmark_pages(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Pagine selezionabili per il benchmark (annotazione verificata)."""
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return {"items": benchmod.selectable_pages(project_id)}


@router.post("/api/projects/{project_id}/benchmark")
def run_benchmark(
    project_id: int,
    payload: BenchmarkRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Avvia il benchmark in background (nessuna scrittura nel DB)."""
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    try:
        return benchmod.start_benchmark(
            project_id,
            [t.model_dump(exclude_none=True) for t in payload.targets],
            page_ids=payload.page_ids,
            limit=payload.limit,
            with_text=payload.with_text,
            use_cache=payload.use_cache,
            table_only=payload.table_only,
            lang=lang,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/projects/{project_id}/benchmark/status")
def benchmark_status(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Stato del benchmark (running/done/error) — sopravvive al refresh della pagina."""
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return benchmod.benchmark_status(project_id)


@router.get("/api/projects/{project_id}/benchmark/latest")
def latest_benchmark(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Ultimo report di benchmark del progetto, se esiste (sola lettura)."""
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    eval_dir = builder._project_dir(project_id) / "eval"  # noqa: SLF001
    reports = sorted(eval_dir.glob("bench_*/report.json")) if eval_dir.is_dir() else []
    if not reports:
        return {"available": False}
    try:
        report = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False}
    report["available"] = True
    return report
