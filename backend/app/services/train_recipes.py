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

from .. import models as model_specs
from ..models.types import (
    ALL_FAMILIES,
    CANONICAL_TRAIN_PROMPTS,
    DATA_VIEWS,
    FAMILY_FORMULA,
    FAMILY_LAYOUT,
    FAMILY_TABLE,
    FAMILY_TEXT,
    GLM_TRAIN_PROMPTS,
    MONKEY_B_ARCH,
    PADDLE_TRAIN_PROMPTS,
    QWEN3_VL_8B_ARCH,
    RECOGNITION_FAMILIES,
)
from .model_adapters import get_adapter, list_adapters

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
    # Versioni che il modello richiede davvero, misurate in una run reale.
    # Non è un iperparametro e non si copia: due modelli possono richiedere
    # transformers incompatibili tra loro (Qwen3-VL ≥5, DeepSeek-OCR-2 4.46.3),
    # quindi ogni ricetta dichiara il proprio ambiente.
    environment: dict[str, str] = field(default_factory=dict)
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
# Specifiche per adapter. Ogni valore viene da una doc vendor elencata in
# `official_doc`; le note dicono cosa il vendor NON pubblica.
# ---------------------------------------------------------------------------
def _build() -> dict[str, TrainRecipe]:
    """Assemble the training registry from the model specs.

    A model without a training block (a closed model, a hosted API) has none; the
    explicit form is `NotApplicable(reason)`, and it is skipped here exactly like an
    absent block. Maturity and the date of the real run that verified the recipe are
    part of the model's own spec, not a table that has to be kept in step.
    """
    recipes: dict[str, TrainRecipe] = {}
    for cap in list_adapters():
        adapter_id = cap["adapter_id"]
        if adapter_id.startswith("custom-"):
            continue
        declared = model_specs.spec_for(adapter_id)
        spec = declared.value("train") if declared is not None else None
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
            maturity=str(spec.get("maturity") or "planned"),
            note=str(spec.get("note") or ""),
            defaults=dict(spec.get("defaults") or {}),
            type_defaults={k: dict(v) for k, v in (spec.get("type_defaults") or {}).items()},
            flags=dict(spec.get("flags") or {}),
            environment=dict(spec.get("environment") or {}),
            prompts=dict(spec["prompts"]) if spec.get("prompts") else None,
            swift_model_type=str(spec.get("swift_model_type") or ""),
            swift_template=str(spec.get("swift_template") or ""),
            tuner_flag=str(spec.get("tuner_flag") or "--tuner_type"),
            llamafactory_template=str(spec.get("llamafactory_template") or ""),
            paddleformers_config=dict(spec.get("paddleformers_config") or {}),
            paddleformers_export=str(spec.get("paddleformers_export") or ""),
            architecture=dict(spec["architecture"]) if spec.get("architecture") else None,
            official_notes=tuple(spec.get("official_notes") or ()),
            verified_on=str(spec.get("verified_on") or ""),
        )
    return recipes


TRAIN_RECIPES: dict[str, TrainRecipe] = _build()


def get_recipe(adapter_id: str) -> TrainRecipe | None:
    return TRAIN_RECIPES.get(adapter_id)


def list_recipes() -> list[dict]:
    return [recipe.to_dict() for recipe in TRAIN_RECIPES.values()]


def trainable_adapters() -> list[str]:
    return [rid for rid, recipe in TRAIN_RECIPES.items() if recipe.trainable]
