"""Xiaomi-OCR-0: a 0.8B Qwen3.5 recognition model behind PP-DocLayoutV3.

Facts from github.com/xiaomi-research/Xiaomi-OCR-0 (README, INSTALL.md,
`evaluation_pipeline/`, `skills/xiaomi-ocr/mcp_ocr_server.py`) and the Hugging Face card,
read on 2026-10-31. The model emits no boxes: the vendor's region mode detects layout with
PaddleX PP-DocLayoutV3 and sends each crop with a task prompt.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.paddleocr_vl import PADDLE_LABELS
from ..models.types import ServeRecipe, WorkflowField

# PP-DocLayoutV3's vocabulary, as `layout_detect_paddlex.LABEL_TASK_MAPPING` routes it.
# Same detector as PaddleOCR-VL, so the shared names keep PaddleOCR-VL's translation;
# the five that pipeline never emits are added here.
XIAOMI_LAYOUT_TASKS: dict[str, str] = {
    **{label: "text" for label in (
        "abstract", "algorithm", "content", "doc_title", "figure_title", "paragraph_title",
        "reference_content", "text", "vertical_text", "vision_footnote", "seal",
        "formula_number", "header", "footer", "number", "footnote", "aside_text", "reference",
    )},
    "table": "table",
    "display_formula": "formula",
    "inline_formula": "formula",
    **{label: "skip" for label in ("chart", "image", "footer_image", "header_image")},
}
_EXTRA_LABELS = {
    "algorithm": "Text",
    "reference_content": "Text",
    "formula_number": "Text",
    "display_formula": "Formula",
    "inline_formula": "Formula",
}
XIAOMI_LABELS: dict[str, str] = {
    label: PADDLE_LABELS.get(label) or _EXTRA_LABELS[label] for label in XIAOMI_LAYOUT_TASKS
}

SPEC = ModelSpec(
    adapter_id="xiaomi-ocr-0",
    serve=Declared(ServeRecipe(
        adapter_id="xiaomi-ocr-0",
        hf_repo="xiaomi-research/Xiaomi-OCR-0",
        served_model_name="xiaomi-ocr-0",
        runtime="vllm",
        # The vendor pins no vLLM ("a release that supports Qwen3_5ForConditionalGeneration");
        # 0.25.1 is the release Datalab ships for the same architecture (Surya 2.5), and
        # a pin keeps the Vast environment reproducible.
        vllm_version="0.25.1",
        serve_args=("--trust-remote-code", "--max-model-len", "16384"),
        source="Xiaomi-OCR-0 INSTALL.md: vllm serve ... --trust-remote-code --max-model-len 16384",
    )),
    train=Declared({
        "maturity": "inference-only",
        "toolchain": "none",
        "data_view": "none",
        "families": (),
        "official_doc": "https://github.com/xiaomi-research/Xiaomi-OCR-0",
        "note": "The vendor publishes no training code and no SFT recipe.",
        "official_notes": (
            "The model card describes the method (Q-Mask anchoring, CPT, Mix-RL) in prose "
            "only, with no hyperparameters.",
        ),
    }),
    labels=Declared(XIAOMI_LABELS),
    cloud=NotApplicable("no Modal template: the model is served with stock vLLM, locally or on Vast.ai"),
    settings=Declared(SettingsSpec(
        workflow=(
            WorkflowField("layout_threshold", "float", default=0.25, minimum=0.0, maximum=1.0,
                          step=0.05, label="settings.xiaomiLayoutThreshold"),
        ),
        workflow_intro="settings.xiaomiWorkflowIntro",
    )),
)
