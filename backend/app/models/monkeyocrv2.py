"""MonkeyOCRv2-Parsing: the reference model, and the canon other models are read against.

Its serving recipe and its place in the cloud templates are data, so they live here.
`labels` is explicitly not applicable: this model's prompts and labels *are* the
canonical taxonomy (`parsing/core_runner.py`), so a native map would translate the
canon into itself.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import (
    ALL_FAMILIES,
    MONKEY_B_ARCH,
    ServeRecipe,
    WorkflowField,
)
SPEC = ModelSpec(
    adapter_id="monkeyocrv2-parsing",
    serve=Declared(
        ServeRecipe(
            adapter_id="monkeyocrv2-parsing",
            hf_repo="zenosai/MonkeyOCRv2-B-Parsing",
            served_model_name="MonkeyOCRv2",
            runtime="monkeyocr",
            # The upstream README pins 0.25.1 for DFlash-enabled serving.
            vllm_version="0.25.1",
            source="parsing/serve.py and the version recommended in the official README",
        )
    ),
    train=Declared({
        "maturity": "verified",
        "verified_on": "2026-10-18 · Vast.ai RTX A5000 24 GB · generated script: 1-step LoRA (loss 0.866), checkpoint-1, swift export merge",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        "families": ALL_FAMILIES,
        "official_doc": "https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/parsing/train",
        "swift_model_type": "monkeyocrv2",
        "swift_template": "monkeyocrv2",
        # Il fork ufficiale in parsing/train/ms-swift usa `--train_type`;
        # l'ms-swift upstream usa `--tuner_type`. Non sono intercambiabili.
        "tuner_flag": "--train_type",
        "defaults": {
            "learning_rate": 1e-5,
            "epochs": 1.0,
            "batch_size": 4,
            "grad_accum": 1,
            "max_length": 16384,
            "max_pixels": 1003520,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 200,
            "save_steps": 500,
        },
        "flags": {
            "attn_impl": "flash_attention_2",
            "torch_dtype": "bfloat16",
            "deepspeed": "zero1",
            "packing": False,
            "padding_free": True,
            "freeze_vit": True,
            "freeze_aligner": False,
            "freeze_llm": False,
            "gradient_checkpointing": True,
        },
        "architecture": MONKEY_B_ARCH,
        "environment": {"transformers": "==4.57.1", "deepspeed": "==0.17.1"},
        "official_notes": (
            "`scripts/lora.sh` e `scripts/full.sh` sono la fonte dei flag; il ViT resta congelato "
            "(`--freeze_vit True`) e l'allineatore/LLM no.",
            "`MAX_PIXELS=1003520` e `--max_length 16384` sono i valori con cui è stato addestrato il checkpoint.",
            "Il fork ufficiale accetta `--train_type`; il resume è `get_latest_checkpoint` sul `checkpoint-N` più alto.",
            "Deviazione misurata: lo script ufficiale usa `--dataloader_num_workers 4`; con DeepSpeed "
            "zero1 su torch 2.6/transformers 4.57 i worker non serializzano l'hook del motore e il run "
            "muore al primo batch. Tabularium usa 0 worker quando DeepSpeed è attivo.",
            "La tabella si addestra in OTSL: il README propone anche HTML, ma a inferenza il modello è interrogato in OTSL.",
        ),
    }),
    labels=NotApplicable(
        "reference model: its prompts and labels are the canon from parsing/core_runner.py"
    ),
    cloud=Declared("monkeyocrv2"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": 1_003_520},
        image_fields=("max_pixels",),
        workflow=(
            WorkflowField("dflash_enabled", "bool", default=True,
                          label="settings.monkeyDFlashEnabled"),
            WorkflowField("dflash_num_speculative_tokens", "int", default=None,
                          minimum=1, maximum=16, nullable=True,
                          label="settings.monkeyDFlashTokens"),
        ),
        workflow_intro="settings.monkeyDFlashIntro",
        serving_dtype=False,
        workflow_needs_restart=True,
    )),
)
