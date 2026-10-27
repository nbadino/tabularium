"""Qwen3-VL-8B: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, SettingsSpec
from ..models.types import (
    ALL_FAMILIES,
    CANONICAL_TRAIN_PROMPTS,
    QWEN3_VL_8B_ARCH,
    ServeRecipe,
)
QWEN3_LABELS: dict[str, str] = {
    "caption": "Caption", "footnote": "Footnote", "footer": "Page-footer",
    "header": "Page-header", "list-item": "List-item", "formula": "Formula",
    "image": "Picture", "chart": "Picture", "table": "Table",
}

SPEC = ModelSpec(
    adapter_id="qwen3-vl-8b",
    serve=Declared(ServeRecipe(
        adapter_id="qwen3-vl-8b",
        hf_repo="Qwen/Qwen3-VL-8B-Instruct",
        served_model_name="qwen3-vl-8b",
        runtime="vllm",
        vllm_version="0.28.0",
        serve_args=(
            "--dtype", "bfloat16",
            "--max-model-len", "32768",
            "--limit-mm-per-prompt", '{"image":4,"video":0}',
        ),
        source="model card Qwen3-VL: limiti multimodali espliciti per non saturare il prefill",
    )),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-20 · Vast.ai A100 40 GB · 3-step LoRA (train loss 1.476, eval 0.693, 24.2 GiB), checkpoint-3, swift export merge 17 GB",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        "families": ALL_FAMILIES,
        "official_doc": "https://github.com/modelscope/ms-swift/blob/main/docs/source_en/BestPractices/Qwen3-VL-Best-Practice.md",
        "swift_model_type": "qwen3_vl",
        "swift_template": "qwen3_vl",
        "defaults": {
            "learning_rate": 1e-4,
            "epochs": 1.0,
            "batch_size": 1,
            "grad_accum": 2,
            "max_length": 4096,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 100,
            "save_steps": 100,
        },
        "flags": {
            "attn_impl": "flash_attn",
            "torch_dtype": "bfloat16",
            "deepspeed": "zero2",
            "packing": True,
            "padding_free": True,
            "freeze_vit": True,
            "freeze_aligner": True,
            "freeze_llm": False,
            "gradient_checkpointing": True,
            "vit_gradient_checkpointing": False,
            "image_max_token_num": 1024,
            "eval_batch_size": 1,
        },
        "environment": {"transformers": ">=5.0"},
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "architecture": QWEN3_VL_8B_ARCH,
        "official_notes": (
            "La Best Practice Qwen3-VL di ms-swift è il riferimento: LoRA 1e-4, `--packing true`, "
            "`--deepspeed zero2`, `freeze_vit`/`freeze_aligner` true.",
            "Il numero di token immagine si limita con `IMAGE_MAX_TOKEN_NUM=1024`, non con `--max_pixels`.",
            "Limite noto: questo training è per-task sui ritagli (testo/OTSL), mentre l'inferenza nativa "
            "Qwen3-VL è full-page HTML (`qwenvl html`). Il fine-tune migliora i task dei ritagli, non il "
            "parser full-page: per quello servono pagine intere HTML, famiglia non ancora esportata.",
            "Il vendor pubblica anche `qwen-vl-finetune` (torchrun + ZeRO-3): non è usato qui perché registrare "
            "un dataset richiede di modificare `qwenvl/data/__init__.py` nel checkout, mentre ms-swift accetta "
            "il percorso del JSONL. La doc Qwen indica 1e-6…2e-7 per il full-tuning, non per LoRA.",
            "Per i modelli MoE la Best Practice indica Megatron-SWIFT; qui il training è su GPU singola via ms-swift.",
        ),
    }),
    labels=Declared(QWEN3_LABELS),
    cloud=Declared("qwen3-vl"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": 512 * 32 * 32, "max_pixels": 2_048 * 32 * 32},
        image_fields=("min_pixels", "max_pixels"),
    )),
)
