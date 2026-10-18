"""Registro delle ricette di training, una per adapter (M18).

Gemello di `serve_recipes.py`: dichiara, per ogni modello **a pesi aperti**,
come si addestra secondo le sue best practice e la sua documentazione ufficiale.
Il training center legge la ricetta dell'adapter scelto e non conosce
MonkeyOCRv2 per nome.

Disciplina (come serving ed eval): una ricetta è `verified` **solo dopo una run
reale** documentata; altrimenti è `experimental`. Nessuna ricetta senza una doc
ufficiale a supporto. I modelli chiusi (Gemini 4 Argon) non hanno pesi e quindi
non compaiono fra gli addestrabili: `training_types` vuoto lo dichiara già.

La `data_view` è la vista dati che il dataset builder deve produrre per quel
toolchain: i JSONL `messages` multimodali di ms-swift (§2.2, `dataset_builder`),
le tre viste Paddle (`paddle_dataset.py`), la conversione ShareGPT di
LLaMA-Factory (`alternative_training.py`). Il builder non converte mai il target
in silenzio: la vista la sceglie la ricetta.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .model_adapters import get_adapter, list_adapters

# Vista dati per toolchain: dove il dataset builder scrive per quel modello.
_DATA_VIEW = {
    "ms-swift": "ms-swift-jsonl",
    "erniekit+paddlex": "paddle",
    "llama-factory": "llamafactory",
    "unsloth": "unsloth",
    "community": "community",
    "none": "none",
}

# Doc ufficiale e maturità per adapter. La doc è l'eterno riferimento: un
# toolchain senza doc ufficiale non entra. Solo dove l'URL è noto lo si mette
# (MonkeyOCRv2, §14); per gli altri si cita la fonte per nome, senza inventare
# indirizzi.
_DOC_MATURITY: dict[str, tuple[str, str, str]] = {
    "monkeyocrv2-parsing": (
        "https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/parsing/train",
        "verified",
        "Riferimento: ms-swift, OTSL, due stadi. È il target su cui è tarato il dataset builder.",
    ),
    "unlimited-ocr": (
        "https://github.com/baidu/Unlimited-OCR + ms-swift (model_type `unlimited_ocr`)",
        "experimental",
        "Legge molto bene (CER 0.006) ma sbaglia la tassonomia: candidato LoRA sulla testa.",
    ),
    "qwen3-vl-8b": (
        "Qwen3-VL (repo ufficiale) + ms-swift (template `qwen3_vl`)",
        "experimental",
        "Il percorso MLX locale è instabile; il training resta su GPU.",
    ),
    "paddleocr-vl": (
        "PaddleOCR-VL + ERNIEKit (VLM) e PaddleX (detector)",
        "experimental",
        "Due viste Paddle (layout e VLM); preflight in `paddle_training.py`.",
    ),
    "glm-ocr": (
        "GLM-OCR (repo ufficiale) + ms-swift (model_type `glm_ocr`)",
        "experimental",
        "ms-swift supporta GLM-OCR ufficialmente (tabella Supported-models).",
    ),
    "deepseek-ocr": (
        "DeepSeek-OCR-2 (repo ufficiale) + ms-swift (model_type `deepseek_ocr2`)",
        "experimental",
        "ms-swift supporta DeepSeek-OCR-2 ufficialmente (tabella Supported-models).",
    ),
    "dots-ocr": (
        "rednote-hilab/dots.ocr + ms-swift (model_type `dots_ocr`)",
        "experimental",
        "ms-swift supporta dots.ocr ufficialmente (tabella Supported-models).",
    ),
    "mineru2.5": (
        "MinerU2.5-Pro + ms-swift (model_type `qwen2_vl`)",
        "experimental",
        "ms-swift lo tratta come `qwen2_vl` (tabella Supported-models).",
    ),
    "teleocr": ("TeleOCR (repo ufficiale)", "planned", "Ricetta di training da aggiungere."),
    "lightonocr-3": ("LightOnOCR-3 (repo ufficiale)", "planned", "Ricetta di training da aggiungere."),
}


@dataclass(frozen=True)
class TrainRecipe:
    adapter_id: str
    display_name: str
    toolchain: str
    train_types: tuple[str, ...]
    data_view: str
    official_doc: str
    maturity: str  # verified | experimental | planned | none
    note: str = ""
    # Per il toolchain ms-swift: `--model_type`/`--template` del modello. Vuoti
    # finché la ricetta non è verificata: il generatore di script fallisce
    # esplicito invece di produrre uno script sbagliato in silenzio.
    swift_model_type: str = ""
    swift_template: str = ""

    @property
    def trainable(self) -> bool:
        return bool(self.train_types) and self.toolchain != "none"

    @property
    def script_ready(self) -> bool:
        """Vero quando il generatore di script sa davvero produrre lo script."""
        if self.toolchain == "ms-swift":
            return bool(self.swift_model_type and self.swift_template)
        return False


# `--model_type`/`--template` ms-swift per adapter (dalla doc/recipe ufficiale).
_SWIFT = {
    "monkeyocrv2-parsing": ("monkeyocrv2", "monkeyocrv2"),
    "qwen3-vl-8b": ("qwen3_vl", "qwen3_vl"),
    # Valori dalla tabella ufficiale ms-swift "Supported-models-and-datasets"
    # (main): Model Type | Default Template.
    "unlimited-ocr": ("unlimited_ocr", "unlimited_ocr"),
    "deepseek-ocr": ("deepseek_ocr2", "deepseek_ocr2"),
    "glm-ocr": ("glm_ocr", "glm_ocr"),
    "mineru2.5": ("qwen2_vl", "qwen2_vl"),
    "dots-ocr": ("dots_ocr", "dots_ocr"),
}


def _build() -> dict[str, TrainRecipe]:
    recipes: dict[str, TrainRecipe] = {}
    for cap in list_adapters():
        adapter_id = cap["adapter_id"]
        if adapter_id.startswith("custom-"):
            continue
        adapter = get_adapter(adapter_id)
        toolchain = str(cap.get("train_toolchain") or "none")
        train_types = tuple(cap.get("training_types") or ())
        doc, maturity, note = _DOC_MATURITY.get(adapter_id, ("", "planned", ""))
        # Un modello senza pesi (chiuso) non ha ricetta di training.
        if not train_types and toolchain == "none" and not doc:
            continue
        if not train_types:
            maturity = "planned" if maturity in {"verified", "experimental"} else maturity
        swift_model_type, swift_template = _SWIFT.get(adapter_id, ("", ""))
        recipes[adapter_id] = TrainRecipe(
            adapter_id=adapter_id,
            display_name=str(getattr(adapter.capabilities, "display_name", adapter_id)),
            toolchain=toolchain,
            train_types=train_types,
            data_view=_DATA_VIEW.get(toolchain, "none"),
            official_doc=doc,
            maturity=maturity,
            note=note,
            swift_model_type=swift_model_type,
            swift_template=swift_template,
        )
    return recipes


TRAIN_RECIPES: dict[str, TrainRecipe] = _build()


def get_recipe(adapter_id: str) -> TrainRecipe | None:
    return TRAIN_RECIPES.get(adapter_id)


def list_recipes() -> list[dict]:
    items = []
    for recipe in TRAIN_RECIPES.values():
        data = asdict(recipe)
        data["script_ready"] = recipe.script_ready
        items.append(data)
    return items


def trainable_adapters() -> list[str]:
    return [rid for rid, recipe in TRAIN_RECIPES.items() if recipe.trainable]
