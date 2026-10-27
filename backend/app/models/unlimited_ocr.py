"""Unlimited-OCR: model spec (program.md phase P1).

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
UNLIMITED_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "table": "Table",
    "figure": "Picture",
    "image": "Picture",
    "chart": "Picture",
    "formula": "Formula",
    "equation": "Formula",
    "header": "Page-header",
    "page_header": "Page-header",
    "footer": "Page-footer",
    "page_footer": "Page-footer",
    "page_number": "Page-footer",
    "list": "List-item",
    "list_item": "List-item",
    "caption": "Caption",
    "footnote": "Footnote",
    "reference": "Text",
}

SPEC = ModelSpec(
    adapter_id="unlimited-ocr",
    serve=Declared(ServeRecipe(
        local_serve="mlx-unlimited",
        adapter_id="unlimited-ocr",
        hf_repo="baidu/Unlimited-OCR",
        served_model_name="Unlimited-OCR",
        # L'architettura non è nella wheel pip stabile: serve l'immagine
        # dedicata, che su Vast.ai si ottiene noleggiando l'istanza con essa.
        runtime="docker",
        docker_image="vllm/vllm-openai:unlimited-ocr",
        serve_args=(
            "--trust-remote-code",
            "--logits_processors", "vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor",
            "--no-enable-prefix-caching",
            "--mm-processor-cache-gb", "0",
            "--max-model-len", "32768",
        ),
        source="recipes.vllm.ai/baidu/Unlimited-OCR",
    )),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-20 · Vast.ai A100 40 GB · 3-step LoRA (loss 3.287, eval 2.447, 19.1 GiB), checkpoint-3, merge 6.3 GB",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        "families": ALL_FAMILIES,
        "official_doc": "https://github.com/baidu/Unlimited-OCR",
        "swift_model_type": "unlimited_ocr",
        "swift_template": "unlimited_ocr",
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
            "Il repo ufficiale dichiara il supporto ms-swift (2026-07-21) ma non pubblica "
            "iperparametri SFT: i valori qui sono i default ms-swift per LoRA, non una ricetta del vendor.",
            "Il modello è end2end: l'inferenza usa il protocollo grounded nativo, mentre questo training "
            "per-task produce OTSL sui ritagli. I due formati non sono allineati: il fine-tune è "
            "sperimentale finché il dataset non esporta anche la famiglia end2end.",
        ),
    }),
    labels=Declared(UNLIMITED_LABELS),
    cloud=Declared("unlimited-ocr"),
    settings=Declared(SettingsSpec(
        workflow=(
            WorkflowField("ngram_size", "int", default=35, minimum=1, maximum=512,
                          label="settings.unlimitedNgramSize"),
            WorkflowField("window_size", "int", default=128, minimum=1, maximum=2048,
                          label="settings.unlimitedWindowSize"),
            WorkflowField("image_mode", "enum", default="gundam", values=("base", "gundam"),
                          label="settings.unlimitedImageMode",
                          hints=(("gundam", "settings.unlimitedGundamHint"),
                                 ("base", "settings.unlimitedBaseHint"))),
        ),
        workflow_intro="settings.unlimitedWorkflowIntro",
    )),
)
