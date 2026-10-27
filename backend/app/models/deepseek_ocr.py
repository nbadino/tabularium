"""DeepSeek-OCR-2: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, SettingsSpec
from ..models.types import (
    ALL_FAMILIES,
    CANONICAL_TRAIN_PROMPTS,
    ServeRecipe,
    WorkflowField,
)
DEEPSEEK_LABELS: dict[str, str] = {
    "title": "Title", "doc_title": "Title", "paragraph_title": "Section-header",
    "section_header": "Section-header", "text": "Text", "paragraph": "Text",
    "table": "Table", "figure": "Picture", "image": "Picture", "chart": "Picture",
    "caption": "Caption", "footnote": "Footnote", "header": "Page-header",
    "footer": "Page-footer", "list": "List-item", "list_item": "List-item",
    "formula": "Formula", "equation": "Formula",
}

SPEC = ModelSpec(
    adapter_id="deepseek-ocr",
    serve=Declared(ServeRecipe(
        adapter_id="deepseek-ocr",
        hf_repo="deepseek-ai/DeepSeek-OCR-2",
        served_model_name="deepseek-ocr-2",
        runtime="vllm",
        vllm_version="0.12.0",
        serve_args=(
            "--trust-remote-code",
            "--logits-processors", "vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
        ),
        source="recipe vLLM ufficiale DeepSeek-OCR: n-gram logits processor contro i loop",
    )),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-20 · Vast.ai A100 40 GB · 3-step LoRA (loss 2.523, 15.6 GiB), checkpoint-3, merge 6.4 GB",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        "families": ALL_FAMILIES,
        "official_doc": "https://github.com/modelscope/ms-swift/blob/main/docs/source_en/Instruction/Supported-models-and-datasets.md",
        "swift_model_type": "deepseek_ocr2",
        "swift_template": "deepseek_ocr2",
        "defaults": {
            "learning_rate": 1e-4,
            "epochs": 1.0,
            "batch_size": 1,
            "grad_accum": 8,
            "max_length": 8192,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 200,
            "save_steps": 500,
        },
        "flags": {
            "attn_impl": "flash_attention_2",
            "torch_dtype": "bfloat16",
            "packing": False,
            "padding_free": True,
            "gradient_checkpointing": True,
        },
        "environment": {"ms-swift": "==4.6.0", "transformers": "==4.46.3"},
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "official_notes": (
            "ms-swift integra `deepseek_ocr2` (tabella Supported-models-and-datasets).",
            "Il vendor pubblica un notebook Unsloth per il fine-tuning, non una CLI riproducibile: "
            "la ricetta eseguibile è ms-swift, non il notebook.",
            "Limite noto: il training per-task usa OTSL, l'inferenza end2end usa grounded Markdown; "
            "finché manca la famiglia end2end i due non coincidono.",
        ),
    }),
    labels=Declared(DEEPSEEK_LABELS),
    cloud=Declared("deepseek-ocr"),
    settings=Declared(SettingsSpec(
        workflow=(
            WorkflowField("ngram_size", "int", default=30, minimum=1, maximum=256,
                          label="settings.deepseekNgramSize"),
            WorkflowField("window_size", "int", default=90, minimum=1, maximum=1024,
                          label="settings.deepseekWindowSize"),
        ),
        workflow_intro="settings.deepseekWorkflowIntro",
    )),
)
