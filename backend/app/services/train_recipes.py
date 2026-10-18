"""Registro delle ricette di training, una per adapter (M18).

Gemello di `serve_recipes.py`: dichiara, per ogni modello **a pesi aperti**,
come si addestra secondo la sua documentazione ufficiale. Il training center
legge la ricetta dell'adapter scelto e non conosce nessun modello per nome.

La regola è una sola: **la ricetta è la doc ufficiale**. Per ogni modello qui
sotto c'è un URL vendor, il toolchain che quel documento usa davvero, il
formato dati che quel toolchain accetta e i suoi iperparametri. Quando il
vendor non pubblica un numero, il campo lo dice (`official_notes`) e si usano
i default del toolchain, non un numero inventato.

Maturità (come serving ed eval): `verified` **solo dopo una run reale**
documentata in `docs/guide/training.md`; altrimenti `experimental`; `planned`
quando esiste una ricetta teorica ma non ancora eseguibile; `inference-only`
quando il vendor non distribuisce codice di training. I modelli chiusi non
compaiono affatto: `training_types` vuoto lo dichiarerebbe già.

Toolchain supportati (uno per modello, quello della sua doc):

- ``ms-swift`` — MonkeyOCRv2 (fork ufficiale in `parsing/train/ms-swift`),
  Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2, dots.ocr, MinerU2.5.
  MonkeyOCRv2 usa il fork ufficiale, dove il flag si chiama ``--train_type``;
  l'ms-swift upstream usa ``--tuner_type``. La ricetta dichiara quale.
- ``llama-factory`` — GLM-OCR: il vendor documenta LLaMA-Factory
  (`examples/finetune/README.md`), non ms-swift.
- ``paddleformers`` — PaddleOCR-VL: il vendor documenta `paddleformers-cli`
  (before PaddleFormers, ERNIEKit was the only path and it was full-SFT only).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .model_adapters import get_adapter, list_adapters

# Famiglie del dataset canonico (v. `dataset_builder.py`).
FAMILY_LAYOUT = "layout"
FAMILY_TEXT = "text_rec"
FAMILY_TABLE = "table"
FAMILY_FORMULA = "formula"

ALL_FAMILIES = (FAMILY_LAYOUT, FAMILY_TEXT, FAMILY_TABLE, FAMILY_FORMULA)
# Task di riconoscimento: layout escluso. Sono i modelli che non producono
# bbox (GLM-OCR, PaddleOCR-VL): addestrarli sul layout insegnerebbe un output
# che il loro protocollo non ha.
RECOGNITION_FAMILIES = (FAMILY_TEXT, FAMILY_TABLE, FAMILY_FORMULA)

# Vista dati per toolchain: dove il training center scrive i file del run.
DATA_VIEWS = {
    "ms-swift": "ms-swift-jsonl",
    "llama-factory": "llamafactory-sharegpt",
    "paddleformers": "paddleformers-messages",
    "none": "none",
}


@dataclass(frozen=True)
class TrainRecipe:
    adapter_id: str
    display_name: str
    hf_repo: str
    toolchain: str
    train_types: tuple[str, ...]
    data_view: str
    families: tuple[str, ...]
    official_doc: str
    maturity: str
    note: str = ""
    # Iperparametri ufficiali. `defaults` vale per ogni tipo di training;
    # `type_defaults` lo specializza (PaddleFormers full 5e-6 / LoRA 5e-4,
    # GLM full 1e-5 / LoRA 1e-4).
    defaults: dict[str, Any] = field(default_factory=dict)
    type_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Flag comportamentali del toolchain, non iperparametri: deepspeed,
    # packing, freeze_*, attn_impl. Sono quelli che NON si copiano da un
    # modello all'altro.
    flags: dict[str, Any] = field(default_factory=dict)
    # Prompt ufficiali di training, quando il vendor ne impone di propri
    # (GLM-OCR e PaddleOCR-VL). `None` = usa i prompt dell'adapter, che sono
    # quelli con cui il modello viene interrogato in inferenza.
    prompts: dict[str, str] | None = None
    # ms-swift
    swift_model_type: str = ""
    swift_template: str = ""
    tuner_flag: str = "--tuner_type"
    # LLaMA-Factory
    llamafactory_template: str = ""
    # PaddleFormers: config ufficiale per tipo di training e per il merge LoRA.
    paddleformers_config: dict[str, str] = field(default_factory=dict)
    paddleformers_export: str = ""
    # Geometria per la stima VRAM. `None` = il modello non ha una config
    # verificata qui: il preflight non blocca su una stima che non sa fare.
    architecture: dict[str, Any] | None = None
    # Cosa dice la doc ufficiale, in chiaro, per la UI e per docs/training.md.
    official_notes: tuple[str, ...] = ()
    # Data e macchina dell'ultima run reale che ha promosso la ricetta.
    verified_on: str = ""

    @property
    def trainable(self) -> bool:
        return bool(self.train_types) and self.toolchain != "none"

    @property
    def script_ready(self) -> bool:
        """Vero quando il generatore sa davvero produrre lo script."""
        if self.toolchain == "ms-swift":
            return bool(self.swift_model_type and self.swift_template)
        if self.toolchain == "llama-factory":
            return bool(self.llamafactory_template)
        if self.toolchain == "paddleformers":
            return bool(self.paddleformers_config.get("full") or self.paddleformers_config.get("lora"))
        return False

    @property
    def merge_supported(self) -> bool:
        """LoRA si può unire a un checkpoint pieno con un comando ufficiale?"""
        return self.toolchain in {"ms-swift", "llama-factory", "paddleformers"}

    def defaults_for(self, train_type: str) -> dict[str, Any]:
        """Iperparametri ufficiali per il tipo di training scelto."""
        merged = dict(self.defaults)
        merged.update(self.type_defaults.get(train_type, {}))
        return merged

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["trainable"] = self.trainable
        data["script_ready"] = self.script_ready
        data["merge_supported"] = self.merge_supported
        return data


# ---------------------------------------------------------------------------
# Geometrie note (per la stima VRAM). Solo dove la `config.json` del
# checkpoint è stata letta davvero; altrove `None` e il preflight non stima.
# ---------------------------------------------------------------------------
_MONKEY_B_ARCH = {
    "hidden_size": 1024,
    "intermediate_size": 3072,
    "num_hidden_layers": 28,
    "vocab_size": 151936,
    "weights_mib": 2048,
}
_QWEN3_VL_8B_ARCH = {
    "hidden_size": 4096,
    "intermediate_size": 12288,
    "num_hidden_layers": 36,
    "vocab_size": 151936,
    "weights_mib": 16300,
}

# ---------------------------------------------------------------------------
# Prompt ufficiali di training. Sono quelli che il vendor mette nei suoi
# esempi SFT, non i prompt di inferenza: se cambiano, il modello impara un
# formato che a runtime non usa (v. AGENTS.md §2.3).
# ---------------------------------------------------------------------------
# Prompt canonici di training per i modelli che non hanno prompt per-task
# (Unlimited-OCR e dots.mocr sono end2end-only; Qwen3-VL usa un preset HTML
# full-page). Sono i prompt ufficiali §2.3, con il target OTSL del dataset:
# coerenti con ciò che il builder scrive, anche dove l'inferenza nativa usa
# un altro protocollo (limite noto, v. docs/guide/training.md).
CANONICAL_TRAIN_PROMPTS = {
    "layout": "Please output the categories and coordinates of the document elements in reading order.",
    "text_rec": "Please output the text content from the image.",
    "table": "Please extract the table from the image and represent it in OTSL format.",
    "formula": "Please write out the expression of the formula in the image using LaTeX format.",
}

GLM_TRAIN_PROMPTS = {
    FAMILY_TEXT: "Text Recognition:",
    FAMILY_TABLE: "Table Recognition:",
    FAMILY_FORMULA: "Formula Recognition:",
}
PADDLE_TRAIN_PROMPTS = {
    FAMILY_TEXT: "OCR:",
    FAMILY_TABLE: "Table Recognition:",
    FAMILY_FORMULA: "Formula Recognition:",
}


# ---------------------------------------------------------------------------
# Specifiche per adapter. Ogni valore viene da una doc vendor elencata in
# `official_doc`; le note dicono cosa il vendor NON pubblica.
# ---------------------------------------------------------------------------
_SPECS: dict[str, dict[str, Any]] = {
    "monkeyocrv2-parsing": {
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
        "architecture": _MONKEY_B_ARCH,
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
    },
    "qwen3-vl-8b": {
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
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "architecture": _QWEN3_VL_8B_ARCH,
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
    },
    "unlimited-ocr": {
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
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "official_notes": (
            "Il repo ufficiale dichiara il supporto ms-swift (2026-07-21) ma non pubblica "
            "iperparametri SFT: i valori qui sono i default ms-swift per LoRA, non una ricetta del vendor.",
            "Il modello è end2end: l'inferenza usa il protocollo grounded nativo, mentre questo training "
            "per-task produce OTSL sui ritagli. I due formati non sono allineati: il fine-tune è "
            "sperimentale finché il dataset non esporta anche la famiglia end2end.",
        ),
    },
    "deepseek-ocr": {
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
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "official_notes": (
            "ms-swift integra `deepseek_ocr2` (tabella Supported-models-and-datasets).",
            "Il vendor pubblica un notebook Unsloth per il fine-tuning, non una CLI riproducibile: "
            "la ricetta eseguibile è ms-swift, non il notebook.",
            "Limite noto: il training per-task usa OTSL, l'inferenza end2end usa grounded Markdown; "
            "finché manca la famiglia end2end i due non coincidono.",
        ),
    },
    "dots-ocr": {
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
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "official_notes": (
            "ms-swift integra `dots_ocr`; il repo del modello non pubblica training ufficiale.",
            "Lo script community (wjbmattingly/dots.ocr) usa 2e-5 full e 1e-4 LoRA: coerente con i default qui, "
            "ma resta community, non vendor.",
            "Limite noto: dots.mocr è end2end HTML; qui il target dei ritagli è OTSL. Il fine-tune "
            "per-task non allena il formato nativo finché non esiste la famiglia end2end.",
        ),
    },
    "mineru2.5": {
        # Il vendor *vieta* `swift sft`: la ricetta ufficiale è il wrapper
        # `python run_mineru.py sft --config configs/sft/sft_table_rec.yaml`,
        # distribuito in `mineru_sft_code.zip`. Il config è dentro lo zip e il
        # suo schema non è pubblico, quindi Tabularium non può generarlo in
        # modo fedele: la ricetta resta `planned` e la UI porta alla doc.
        "toolchain": "mineru-sft",
        "data_view": "ms-swift-jsonl",
        "families": RECOGNITION_FAMILIES,
        "official_doc": "https://github.com/opendatalab/mineru-tutorials/tree/main/04%E8%AF%BE%EF%BC%9AMinerU%201.2B%20%E6%A8%A1%E5%9E%8B%E5%BE%AE%E8%B0%83",
        "prompts": {
            FAMILY_TEXT: "\nText Recognition:",
            FAMILY_TABLE: "\nTable Recognition:",
            FAMILY_FORMULA: "\nFormula Recognition:",
        },
        "official_notes": (
            "La lezione ufficiale dice esplicitamente di **non** usare `swift sft`, ma "
            "`python run_mineru.py sft --config configs/sft/sft_table_rec.yaml`: il wrapper carica "
            "`mineru_ext` con template propri.",
            "Il codice è `mineru_sft_code.zip`, allegato alla lezione e non versionato: `configs/sft/sft_table_rec.yaml` "
            "sta dentro lo zip, quindi il generatore non può produrre uno script fedele senza quel bundle.",
            "I prompt di training ufficiali hanno il newline iniziale: `\\nTable Recognition:`, `\\nFormula Recognition:`, "
            "`\\nText Recognition:`.",
        ),
    },
    "glm-ocr": {
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
    },
    "paddleocr-vl": {
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
        "prompts": PADDLE_TRAIN_PROMPTS,
        "official_notes": (
            "PaddleFormers è il percorso corrente del vendor: `paddleformers-cli train <config> key=value`, "
            "dataset `messages` (lo stesso formato di ms-swift) e `template: paddleocr_vl`.",
            "Full 5e-6 / LoRA 5e-4, batch 8, accum 8, 2 epoche, `max_seq_len 16384`, eval/save 400.",
            "Il dataset ufficiale non contiene bbox: il layout resta al detector PP-DocLayout, "
            "addestrato a parte con PaddleX (vista `layout_coco`).",
            "I prompt ufficiali sono `OCR:` / `Table Recognition:` / `Formula Recognition:`; le tabelle sono OTSL.",
        ),
    },
    "teleocr": {
        "toolchain": "none",
        "data_view": "none",
        "families": (),
        "official_doc": "https://github.com/caipeng328/TeleOCR",
        "note": "Il repo ufficiale distribuisce solo inferenza: nessun codice o config di training.",
        "official_notes": (
            "Il paper descrive quattro stadi (allineamento, deformazione, contenuto-struttura, GRPO) "
            "ma il repo non contiene `train/` né un toolchain: la ricetta non è riproducibile.",
        ),
    },
    "lightonocr-3": {
        "toolchain": "none",
        "data_view": "none",
        "families": (),
        "official_doc": "https://github.com/lightonai/LightOnOCR",
        "note": "Il repo ufficiale è client/CLI/benchmark: nessun codice o config di training.",
        "official_notes": (
            "Il blog vendor documenta metodo e reward (IoU/0.75) ma non pubblica né codice né iperparametri SFT.",
        ),
    },
}

# Doc e note non ripetute altrove; la maturità è l'unica cosa che si aggiorna
# a mano dopo una run reale (v. docs/guide/training.md).
_DEFAULT_MATURITY = {
    "monkeyocrv2-parsing": "verified",
    "qwen3-vl-8b": "experimental",
    "unlimited-ocr": "experimental",
    "deepseek-ocr": "experimental",
    "dots-ocr": "experimental",
    "mineru2.5": "planned",
    "glm-ocr": "experimental",
    "paddleocr-vl": "experimental",
    "teleocr": "inference-only",
    "lightonocr-3": "inference-only",
}

_VERIFIED_ON = {
    "monkeyocrv2-parsing": "2026-10-18 · Vast.ai RTX A5000 24 GB · generated script: 1-step LoRA (loss 0.866), checkpoint-1, swift export merge",
}


def _build() -> dict[str, TrainRecipe]:
    recipes: dict[str, TrainRecipe] = {}
    for cap in list_adapters():
        adapter_id = cap["adapter_id"]
        if adapter_id.startswith("custom-"):
            continue
        spec = _SPECS.get(adapter_id)
        if spec is None:
            continue
        adapter = get_adapter(adapter_id)
        # Un modello senza pesi (chiuso) non ha ricetta di training.
        train_types = tuple(cap.get("training_types") or ())
        toolchain = str(spec.get("toolchain") or "none")
        if not train_types:
            toolchain = "none"
        recipes[adapter_id] = TrainRecipe(
            adapter_id=adapter_id,
            display_name=str(getattr(adapter.capabilities, "display_name", adapter_id)),
            hf_repo=str(getattr(adapter.capabilities, "hf_repo", "") or ""),
            toolchain=toolchain,
            train_types=train_types,
            data_view=str(spec.get("data_view") or DATA_VIEWS.get(toolchain, "none")),
            families=tuple(spec.get("families") or ()),
            official_doc=str(spec.get("official_doc") or ""),
            maturity=_DEFAULT_MATURITY.get(adapter_id, "planned"),
            note=str(spec.get("note") or ""),
            defaults=dict(spec.get("defaults") or {}),
            type_defaults={k: dict(v) for k, v in (spec.get("type_defaults") or {}).items()},
            flags=dict(spec.get("flags") or {}),
            prompts=dict(spec["prompts"]) if spec.get("prompts") else None,
            swift_model_type=str(spec.get("swift_model_type") or ""),
            swift_template=str(spec.get("swift_template") or ""),
            tuner_flag=str(spec.get("tuner_flag") or "--tuner_type"),
            llamafactory_template=str(spec.get("llamafactory_template") or ""),
            paddleformers_config=dict(spec.get("paddleformers_config") or {}),
            paddleformers_export=str(spec.get("paddleformers_export") or ""),
            architecture=dict(spec["architecture"]) if spec.get("architecture") else None,
            official_notes=tuple(spec.get("official_notes") or ()),
            verified_on=_VERIFIED_ON.get(adapter_id, ""),
        )
    return recipes


TRAIN_RECIPES: dict[str, TrainRecipe] = _build()


def get_recipe(adapter_id: str) -> TrainRecipe | None:
    return TRAIN_RECIPES.get(adapter_id)


def list_recipes() -> list[dict]:
    return [recipe.to_dict() for recipe in TRAIN_RECIPES.values()]


def trainable_adapters() -> list[str]:
    return [rid for rid, recipe in TRAIN_RECIPES.items() if recipe.trainable]
