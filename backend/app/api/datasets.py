"""API dataset: costruzione e stato del dataset di un progetto."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..db import connect
from ..services import dataset_builder as builder
from ..services import export_formats
from ..services import paddle_dataset
from ..services import paddle_training
from ..services import alternative_training
from ..services.i18n import parse_lang
from ..services import auth as authsvc
from .deps import require_resource

router = APIRouter(
    tags=["datasets"],
    dependencies=[Depends(authsvc.get_current_user)],
)


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


class PaddleBuildRequest(BaseModel):
    split_ratio: float = Field(default=0.90, gt=0.0, lt=1.0)
    seed: int = 42
    approved_only: bool = True


def _require_project(project_id: int) -> None:
    from ..api.projects import _get_project_or_404  # noqa: PLC0415

    with connect() as conn:
        _get_project_or_404(conn, project_id)


@router.post("/api/projects/{project_id}/datasets/build")
def build_dataset(
    project_id: int,
    payload: BuildRequest,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
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
def get_dataset(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    _require_project(project_id)
    report_path = builder._project_dir(project_id) / "dataset" / "report.json"  # noqa: SLF001
    if not report_path.exists():
        return {"built": False, "report": None}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (TypeError, ValueError):
        return {"built": False, "report": None}
    return {"built": True, "report": report}


@router.post("/api/projects/{project_id}/datasets/build-paddle")
def build_paddle_dataset(
    project_id: int,
    payload: PaddleBuildRequest,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Crea le viste ufficiali Paddle: layout COCO, VLM ERNIEKit, celle PP-OCR rec.

    Le tre servono modelli diversi: il rilevatore di layout impara dove sta una
    tabella, il VLM impara a leggerla intera, il riconoscitore di riga impara la
    singola cella. Quest'ultimo è il motore locale usato dal prefill OCR, e il
    suo dataset è fatto di celle con testo *verificato* da un umano.
    """
    _require_project(project_id)
    try:
        return paddle_dataset.build(
            project_id, payload.split_ratio, payload.seed, payload.approved_only
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/datasets/paddle-training")
def prepare_paddle_training(
    project_id: int,
    payload: dict | None = None,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Prepara le recipe ufficiali ERNIEKit e PaddleX, senza lanciarle."""
    _require_project(project_id)
    payload = payload or {}
    try:
        return paddle_training.prepare(
            project_id,
            str(payload.get("vlm_model") or "PaddlePaddle/PaddleOCR-VL"),
            str(payload.get("ernie_dir") or ""),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/datasets/paddle-training/preflight")
def paddle_training_preflight(
    project_id: int,
    payload: dict | None = None,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    """Verifica dataset e ambienti Paddle/ERNIEKit senza avviare processi."""
    _require_project(project_id)
    payload = payload or {}
    return paddle_training.preflight(
        project_id,
        str(payload.get("ernie_dir") or ""),
        str(payload.get("paddlex_dir") or ""),
    )


@router.post("/api/projects/{project_id}/datasets/glm-training")
def prepare_glm_training(
    project_id: int,
    payload: dict | None = None,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Prepara la vista ShareGPT e la config LLaMA-Factory per GLM-OCR."""
    _require_project(project_id)
    try:
        return alternative_training.prepare_glm(
            project_id, bool((payload or {}).get("approved_only", True))
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/datasets/deepseek-training")
def prepare_deepseek_training(
    project_id: int,
    payload: dict | None = None,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Prepara il dataset Unsloth DeepSeek-OCR-2 e il launcher del runner."""
    _require_project(project_id)
    try:
        return alternative_training.prepare_deepseek(
            project_id, bool((payload or {}).get("approved_only", True))
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/datasets/{adapter_id}-training")
def prepare_grounded_training(
    project_id: int,
    adapter_id: str,
    payload: dict | None = None,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Prepara il dataset end-to-end per dots.ocr o Unlimited-OCR."""
    _require_project(project_id)
    try:
        if adapter_id == "mineru2.5":
            return alternative_training.prepare_mineru(
                project_id, bool((payload or {}).get("approved_only", True))
            )
        return alternative_training.prepare_grounded_end2end(
            project_id, adapter_id, bool((payload or {}).get("approved_only", True))
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/datasets/export/{format_name}")
def export_dataset_format(
    project_id: int,
    format_name: str,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Esporta il medesimo ground truth in un formato non legato a ms-swift."""
    _require_project(project_id)
    if format_name not in {"internal", "coco", "html", "page", "alto"}:
        raise HTTPException(status_code=400, detail="formato non supportato: internal|coco|html|page|alto")
    return export_formats.export_formats(project_id, (format_name,))
