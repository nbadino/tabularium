"""Model specs: one module per model, one `SPEC` each.

Phase 1 of `docs/architecture/program.md`. A model's data (serving recipe, training
entry, native labels, cloud template) is declared here, once. The registries
(`serve_recipes`, `train_recipes`, `model_labels`, `model_registry`) export the same
public dictionaries, assembled from these specs; they hold no per-model literal.

Adding a model means: write one module here, add it to `_MODULES`, and register the
adapter behaviour in `model_adapters`. `scripts/surfaces.py --check` then proves that
nothing is missing anywhere else in the application.
"""
from __future__ import annotations

from . import (
    deepseek_ocr,
    dots_ocr,
    gemini_argon,
    glm_ocr,
    lightonocr3,
    llamaindex_ocria,
    mineru2_5,
    monkeyocrv2,
    paddleocr_vl,
    qwen3_vl_8b,
    surya_ocr,
    teleocr,
    unlimited_ocr,
    unlimited_ocr2,
    xiaomi_ocr,
)
from .base import (
    SPEC_SURFACES,
    Declared,
    ModelSpec,
    NotApplicable,
    declared_values,
    not_applicable_reasons,
    spec_for,
)

# The module list is explicit on purpose: a spec that is not listed here does not
# exist, and the surface check will say so. Import order does not matter.
_MODULES = (
    deepseek_ocr,
    dots_ocr,
    gemini_argon,
    glm_ocr,
    lightonocr3,
    llamaindex_ocria,
    mineru2_5,
    monkeyocrv2,
    paddleocr_vl,
    qwen3_vl_8b,
    surya_ocr,
    teleocr,
    unlimited_ocr,
    unlimited_ocr2,
    xiaomi_ocr,
)

SPECS: dict[str, ModelSpec] = {module.SPEC.adapter_id: module.SPEC for module in _MODULES}

__all__ = [
    "SPECS",
    "SPEC_SURFACES",
    "Declared",
    "ModelSpec",
    "NotApplicable",
    "declared_values",
    "not_applicable_reasons",
    "spec_for",
]
