"""GLM-OCR: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, SettingsSpec
from ..models.types import (
    GLM_TRAIN_PROMPTS,
    RECOGNITION_FAMILIES,
    ServeRecipe,
    WorkflowField,
)
GLM_LABELS: dict[str, str] = {
    "title": "Title", "text": "Text", "table": "Table",
    "figure": "Picture", "formula": "Formula", "header": "Page-header",
    "footer": "Page-footer", "page_number": "Page-footer", "seal": "Picture",
    "reference": "Text", "chart": "Picture", "image": "Picture",
    "display_formula": "Formula", "inline_formula": "Formula",
}

SPEC = ModelSpec(
    adapter_id="glm-ocr",
    serve=Declared(ServeRecipe(
        adapter_id="glm-ocr",
        hf_repo="zai-org/GLM-OCR",
        served_model_name="glm-ocr",
        runtime="vllm",
        vllm_version="0.19.0",
        transformers_version="5.3.1",
        native_remote_port=8890,
        pip_extra=("glmocr[selfhosted]", "fastapi", "uvicorn"),
        serve_args=(
            "--speculative-config", '{"method": "mtp", "num_speculative_tokens": 1}',
        ),
        source="README GLM-OCR: vLLM MTP ufficiale + SDK self-hosted PP-DocLayout/OCR",
    )),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-20 · Vast.ai A100 40 GB · 2-step LoRA (loss 5.406, eval 0.682), checkpoint-2, llamafactory-cli export 2.1 GB",
        "toolchain": "llama-factory",
        "data_view": "llamafactory-sharegpt",
        "families": RECOGNITION_FAMILIES,
        "official_doc": "https://github.com/zai-org/GLM-OCR/blob/main/examples/finetune/README.md",
        "llamafactory_template": "glm_ocr",
        "defaults": {
            "learning_rate": 1e-4,
            "epochs": 3.0,
            "batch_size": 4,
            "grad_accum": 4,
            "max_length": 16384,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 200,
            "save_steps": 500,
        },
        "type_defaults": {
            "full": {
                "learning_rate": 1e-5,
                "grad_accum": 2,
            },
            "lora": {
                "learning_rate": 1e-4,
                "grad_accum": 4,
            },
        },
        "flags": {
            "bf16": True,
            "freeze_vision_tower": True,
            "freeze_multi_modal_projector": True,
            "freeze_language_model": False,
            "lora_target": "all",
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.1,
            "trust_remote_code": True,
            "deepspeed": "",
        },
        "environment": {"transformers": "==5.8.0"},
        "prompts": GLM_TRAIN_PROMPTS,
        "official_notes": (
            "Il vendor documenta LLaMA-Factory, non ms-swift: `template: glm_ocr`, ShareGPT, "
            "`freeze_vision_tower`/`freeze_multi_modal_projector` true, LoRA rank 8 target `all`.",
            "Full: lr 1e-5, accum 2. LoRA: lr 1e-4, accum 4. Entrambi: 4 per device, 3 epoche.",
            "Il config ufficiale è minimale (`cutoff_len: 2048`, `max_samples: 1000`); qui la lunghezza "
            "sale a 16384 perché un registro tabellare non entra in 2048 token.",
            "I prompt ufficiali sono `Text Recognition:` / `Table Recognition:` / `Formula Recognition:`; "
            "GLM-OCR non produce bbox, quindi il layout non entra nel dataset di training.",
        ),
    }),
    labels=Declared(GLM_LABELS),
    cloud=Declared("glm-ocr"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": 71_372_800},
        image_fields=("max_pixels",),
        workflow=(
            WorkflowField("speculative_tokens", "int", default=1, minimum=1, maximum=16,
                          label="settings.glmSpeculativeTokens"),
        ),
        workflow_intro="settings.glmWorkflowIntro",
        generation_unsupported=frozenset({"presence_penalty", "frequency_penalty"}),
        workflow_needs_restart=True,
    )),
)
