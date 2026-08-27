"""API dataset: costruzione e stato del dataset di un progetto."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import connect
from ..services import dataset_builder as builder
from ..services import export_formats
from ..services.i18n import parse_lang

router = APIRouter(tags=["datasets"])


class BuildRequest(BaseModel):
    split_ratio: float = Field(default=0.90, gt=0.0, lt=1.0)
    seed: int = 42
    split_strategy: str = Field(default="page", pattern="^(page|issue|year|source|scanner|collection|page_type)$")
    adapter_id: str = "monkeyocrv2-parsing"
    approved_only: bool = False
    pilot_only: bool = False
    table_band_rows: int = Field(default=15, ge=4, le=100)
    table_band_overlap: int = Field(default=2, ge=0, le=20)
    include_full_tables: bool = True


def _require_project(project_id: int) -> None:
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    with connect() as conn:
        _get_project_or_404(conn, project_id)


@router.post("/api/projects/{project_id}/datasets/build")
def build_dataset(project_id: int, payload: BuildRequest, request: Request) -> dict:
    lang = parse_lang(request.headers.get("accept-language"))
    _require_project(project_id)
    try:
        return builder.build_datasets(
            project_id,
            payload.split_ratio,
            payload.seed,
            payload.split_strategy,
            payload.adapter_id,
            payload.approved_only,
            payload.pilot_only,
            payload.table_band_rows,
            payload.table_band_overlap,
            payload.include_full_tables,
            lang=lang,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/projects/{project_id}/datasets")
def get_dataset(project_id: int) -> dict:
    _require_project(project_id)
    report_path = builder._project_dir(project_id) / "dataset" / "report.json"  # noqa: SLF001
    if not report_path.exists():
        return {"built": False, "report": None}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (TypeError, ValueError):
        return {"built": False, "report": None}
    return {"built": True, "report": report}


@router.post("/api/projects/{project_id}/datasets/export/{format_name}")
def export_dataset_format(project_id: int, format_name: str) -> dict:
    """Esporta il medesimo ground truth in un formato non legato a ms-swift."""
    _require_project(project_id)
    if format_name not in {"internal", "coco", "html", "page", "alto"}:
        raise HTTPException(status_code=400, detail="formato non supportato: internal|coco|html|page|alto")
    return export_formats.export_formats(project_id, (format_name,))
