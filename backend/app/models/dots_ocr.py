"""dots.mocr: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import (
    ALL_FAMILIES,
    CANONICAL_TRAIN_PROMPTS,
    ServeRecipe,
)
SPEC = ModelSpec(
    adapter_id="dots-ocr",
    serve=Declared(ServeRecipe(
        adapter_id="dots-ocr",
        hf_repo="dots-studio/dots.mocr",
        served_model_name="dots-mocr",
        runtime="vllm",
        vllm_version="0.28.0",
        serve_args=(
            "--gpu-memory-utilization", "0.9",
            "--trust-remote-code",
            "--chat-template-content-format", "string",
        ),
        source="README dots.mocr: senza il formato stringa il template di chat non combacia",
    )),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-20 · Vast.ai A100 40 GB · 3-step LoRA (loss 2.170, eval 2.983, 16.9 GiB), checkpoint-3, merge 5.7 GB; transformers 4.57.1, flash-attn, max_pixels 1 MP",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        "families": ALL_FAMILIES,
        "official_doc": "https://github.com/modelscope/ms-swift/blob/main/docs/source_en/Instruction/Supported-models-and-datasets.md",
        "swift_model_type": "dots_ocr",
        "swift_template": "dots_ocr",
        "defaults": {
            "learning_rate": 1e-4,
            "epochs": 1.0,
            "batch_size": 1,
            "grad_accum": 8,
            "max_length": 16384,
            "max_pixels": 1000000,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 200,
            "save_steps": 500,
        },
        "flags": {
            # flash_attention_2 fa fallire l'import: DotsVisionTransformer non
            # dichiara il supporto e transformers rifiuta. La torre visiva usa
            # comunque flash-attn al proprio interno; il LLM va in eager.
            "attn_impl": "eager",
            "torch_dtype": "bfloat16",
            "packing": False,
            "padding_free": False,
            "gradient_checkpointing": True,
        },
        "environment": {"transformers": "==4.57.1", "flash-attn": "==2.8.3"},
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "official_notes": (
            "ms-swift integra `dots_ocr`; il repo del modello non pubblica training ufficiale.",
            "Lo script community (wjbmattingly/dots.ocr) usa 2e-5 full e 1e-4 LoRA: coerente con i default qui, "
            "ma resta community, non vendor.",
            "Limite noto: dots.mocr è end2end HTML; qui il target dei ritagli è OTSL. Il fine-tune "
            "per-task non allena il formato nativo finché non esiste la famiglia end2end.",
            "Misurato: transformers 4.57.1, flash-attn installato (la torre visiva lo importa), "
            "`--attn_impl eager` (flash_attention_2 viene rifiutato: il modello non dichiara il supporto) "
            "e `max_pixels 1000000`: una pagina intera a 11 MP fa allocare 30 GiB e va in OOM.",
        ),
    }),
    labels=NotApplicable("the official end2end prompt enumerates the public canonical labels (Caption, Footnote, Formula, List-item, Page-footer, Page-header, Picture, Section-header, Table, Text, Title)"),
    cloud=Declared("dots-ocr"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": 11_289_600},
        image_fields=("max_pixels",),
    )),
)
