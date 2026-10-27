"""PaddleOCR-VL: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, SettingsSpec
from ..models.types import (
    PADDLE_TRAIN_PROMPTS,
    PADDLE_WORKFLOW_FIELDS,
    RECOGNITION_FAMILIES,
    ServeRecipe,
    WorkflowField,
    paddle_workflow_fields,
)
PADDLE_LABELS: dict[str, str] = {
    # titoli
    "doc_title": "Title",
    "paragraph_title": "Title",
    "title": "Title",
    # didascalie di figure, tabelle e grafici
    "figure_title": "Caption",
    "chart_title": "Caption",
    "table_title": "Caption",
    # testata e piede
    "number": "Issue-number",
    "header": "Page-header",
    "footer": "Page-footer",
    "footnote": "Footnote",
    "vision_footnote": "Footnote",
    # corpi di testo
    "text": "Text",
    "abstract": "Text",
    "content": "Text",
    "aside_text": "Text",
    "vertical_text": "Text",
    "reference": "List-item",
    # contenuti non testuali
    "table": "Table",
    "formula": "Formula",
    "image": "Picture",
    "figure": "Picture",
    "chart": "Picture",
    "seal": "Picture",
    "header_image": "Picture",
    "footer_image": "Picture",
}

SPEC = ModelSpec(
    adapter_id="paddleocr-vl",
    serve=Declared(ServeRecipe(
        adapter_id="paddleocr-vl",
        hf_repo="PaddlePaddle/PaddleOCR-VL-1.6",
        served_model_name="PaddleOCR-VL-1.6",
        runtime="vllm",
        vllm_version="0.28.0",
        serve_args=(
            "--trust-remote-code",
            "--max-num-batched-tokens", "16384",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
        ),
        source="recipe PaddleOCR-VL: prefix caching e cache del processore multimodale vanno spenti",
    )),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-20 · Vast.ai RTX 3090 24 GB · 3-step LoRA (loss 0.271), LoRA export 1.8 GB",
        "toolchain": "paddleformers",
        "data_view": "paddleformers-messages",
        "families": RECOGNITION_FAMILIES,
        "official_doc": "https://github.com/PaddlePaddle/PaddleFormers/tree/develop/examples/best_practices/PaddleOCR-VL",
        "paddleformers_config": {
            "full": "examples/best_practices/PaddleOCR-VL/paddleocr-vl_full_16k_config.yaml",
            "lora": "examples/best_practices/PaddleOCR-VL/paddleocr-vl_lora_16k_config.yaml",
        },
        "paddleformers_export": "examples/best_practices/PaddleOCR-VL/paddleocr-vl_lora_export.yaml",
        "defaults": {
            "learning_rate": 5e-6,
            "epochs": 2.0,
            "batch_size": 8,
            "grad_accum": 8,
            "max_length": 16384,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 400,
            "save_steps": 400,
        },
        "type_defaults": {
            "full": {"learning_rate": 5e-6},
            "lora": {"learning_rate": 5e-4},
        },
        "flags": {
            "bf16": True,
            "pre_alloc_memory": {"full": 24, "lora": 16},
        },
        "environment": {"paddlepaddle-gpu": "==3.3.0", "triton": ">=3.1"},
        "prompts": PADDLE_TRAIN_PROMPTS,
        "official_notes": (
            "PaddleFormers è il percorso corrente del vendor: `paddleformers-cli train <config> key=value`, "
            "dataset `messages` (lo stesso formato di ms-swift) e `template: paddleocr_vl`.",
            "Full 5e-6 / LoRA 5e-4, batch 8, accum 8, 2 epoche, `max_seq_len 16384`, eval/save 400.",
            "Il dataset ufficiale non contiene bbox: il layout resta al detector PP-DocLayout, "
            "addestrato a parte con PaddleX (vista `layout_coco`).",
            "I prompt ufficiali sono `OCR:` / `Table Recognition:` / `Formula Recognition:`; le tabelle sono OTSL.",
        ),
    }),
    labels=Declared(PADDLE_LABELS),
    cloud=Declared("paddleocr-vl"),
    settings=Declared(SettingsSpec(
        image_fields=("min_pixels", "max_pixels"),
        workflow=paddle_workflow_fields(),
        workflow_intro="settings.paddleWorkflowIntro",
    )),
)
