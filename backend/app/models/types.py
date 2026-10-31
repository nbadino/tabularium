"""Types shared by the model specs and the registries that derive from them.

`ServeRecipe` lives here rather than in `serve_recipes.py` so that a spec module can
build one without importing the registry that consumes it (which would be a cycle:
registry -> specs -> registry). `serve_recipes.ServeRecipe` stays importable as a
re-export until phase P4.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class ServeRecipe:
    """How a model is served, according to its official documentation."""

    adapter_id: str
    hf_repo: str
    served_model_name: str
    # "vllm": `vllm serve <weights> <args>` — "teleocr-native": the official
    # TeleOCRClient on vllm-async-engine — "monkeyocr": the official wrapper
    # `parsing/serve.py` — "docker": a dedicated image, not pip-installable —
    # "sglang": `python -m sglang.launch_server` from the packages in `pip_extra`.
    runtime: str
    vllm_version: str = ""
    # Vast offer filters use Vast's documented `compute_cap` scale (e.g.
    # 750 = sm_75). This is the runtime floor, distinct from a model's
    # preferred dtype; BF16 recipes can use FP16 on older supported cards.
    min_compute_capability: float = 7.5
    # The pinned vLLM wheels used by the cloud recipes target CUDA 12.9.
    min_cuda_driver: float = 12.9
    # Empty means let the pinned vLLM release resolve its supported range.
    # Model plugins may constrain Transformers themselves (TeleOCR does).
    transformers_version: str = ""
    docker_image: str = ""
    # Optional model-owned HTTP bridge port (e.g. TeleOCR's official runner).
    native_remote_port: int | None = None
    pip_extra: tuple[str, ...] = ()
    # Process environment the vendor's launch command sets (SGLang knobs live there).
    serve_env: tuple[tuple[str, str], ...] = ()
    # Interpreter the runtime needs when it is not the host's (Unlimited-OCR-2: 3.10).
    python_version: str = ""
    # Flags after the model. `--host`, `--port` and `--served-model-name` are added
    # by `serve_argv`: they are infrastructure, not part of the recipe.
    serve_args: tuple[str, ...] = field(default_factory=tuple)
    source: str = ""
    # Which local runner starts this model on Apple Silicon: "" is the standard
    # MLX server, and the exceptions name themselves here instead of as an
    # `adapter_id ==` branch inside `serve_manager`.
    local_serve: str = ""

    @property
    def needs_own_image(self) -> bool:
        """True when the architecture lives only inside a dedicated image.

        On Vast.ai that is not an obstacle: the container image is chosen when the
        instance is rented. On Modal it has to be built — the difference is who
        provides it, not whether it can be used.
        """
        return self.runtime == "docker"

    @property
    def installs_vllm(self) -> bool:
        """False when vLLM is already inside the image, or the runtime is not vLLM."""
        return self.runtime not in ("docker", "sglang")


# ---------------------------------------------------------------------------
# Dataset families, data views and vendor training presets.
#
# These live next to the types because a model spec needs them: the training
# block of a spec is written in terms of the families (`ALL_FAMILIES`,
# `RECOGNITION_FAMILIES`) and, for GLM/Paddle, of the vendor prompts. Keeping them
# in `train_recipes.py` would force every spec module to import the registry that
# consumes the specs, which is a cycle.
# ---------------------------------------------------------------------------
# Dataset families (see `dataset_builder.py`).
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

# ---------------------------------------------------------------------------
# Geometrie note (per la stima VRAM). Solo dove la `config.json` del
# checkpoint è stata letta davvero; altrove `None` e il preflight non stima.
# ---------------------------------------------------------------------------
MONKEY_B_ARCH = {
    "hidden_size": 1024,
    "intermediate_size": 3072,
    "num_hidden_layers": 28,
    "vocab_size": 151936,
    "weights_mib": 2048,
}
QWEN3_VL_8B_ARCH = {
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
# Vendor workflow vocabularies.
#
# These are the knobs each vendor's pipeline exposes. They live here because both
# sides need them from one source: a model's spec declares its workflow defaults in
# terms of these fields, and `model_settings` validates user overrides against the
# same declarations. A copy in each place is how a default and its validator drift.
# ---------------------------------------------------------------------------
PADDLE_BOOLEAN_WORKFLOW = {
    "use_layout_detection",
    "layout_nms",
    "use_doc_orientation_classify",
    "use_doc_unwarping",
    "use_chart_recognition",
    "use_seal_recognition",
    "use_ocr_for_image_block",
    "format_block_content",
    "merge_layout_blocks",
    "use_queues",
}
PADDLE_WORKFLOW_LIMITS = {
    "layout_threshold": (0.0, 1.0),
    "layout_unclip_ratio": (0.01, 10.0),
    "vl_rec_max_concurrency": (1, 128),
}
PADDLE_WORKFLOW_ENUMS = {
    "layout_merge_bboxes_mode": {"large", "small", "union"},
    "layout_shape_mode": {"auto", "rect", "quad", "poly"},
}
UNLIMITED_WORKFLOW_LIMITS = {"ngram_size": (1, 512), "window_size": (1, 2048)}
UNLIMITED_WORKFLOW_ENUMS = {"image_mode": {"gundam", "base"}}
PADDLE_WORKFLOW_FIELDS = (
    PADDLE_BOOLEAN_WORKFLOW
    | set(PADDLE_WORKFLOW_LIMITS)
    | set(PADDLE_WORKFLOW_ENUMS)
)

# LlamaCloud accepts Tesseract-style ISO 639-3 language codes. Keeping the
# documented set here gives the UI a bounded native vocabulary and prevents a
# typo from becoming a paid, failed parse job.
LLAMAINDEX_OCR_LANGUAGES = frozenset({
    "abq", "ach", "afr", "aka", "amh", "ara", "asm", "ava", "aym", "aze",
    "bak", "bam", "bel", "bem", "ben", "bod", "bos", "bre", "bul", "cat",
    "ceu", "ces", "cha", "che", "chi_sim", "chi_tra", "chr", "cos", "cym",
    "dan", "deu", "div", "dzo", "ell", "eng", "enm", "epo", "est", "eus",
    "fao", "fas", "fij", "fin", "fra", "frk", "frm", "fry", "gla", "gle",
    "glg", "grc", "grn", "guj", "hat", "hau", "heb", "hin", "hrv", "hun",
    "hye", "ibo", "ido", "iku", "ile", "ina", "ind", "isl", "ita", "jav",
    "jpn", "kan", "kat", "kaz", "khm", "kir", "kmr", "kor", "lao", "lat",
    "lav", "lit", "ltz", "mal", "mar", "mkd", "mlt", "mon", "mri", "msa",
    "mya", "nep", "nld", "nor", "oci", "ori", "orm", "pan", "pol", "por",
    "pus", "que", "ron", "rus", "san", "sin", "slk", "slv", "snd", "spa",
    "sqi", "srp", "sun", "swa", "swe", "syr", "tam", "tat", "tel", "tgk",
    "tha", "tir", "ton", "tur", "uig", "ukr", "urd", "uzb", "vie", "yid",
    "yor",
})
LLAMAINDEX_WORKFLOW_BOOLEANS = {
    "aggressive_table_extraction",
    "disable_heuristics",
    "cost_optimizer",
    "ignore_diagonal_text",
    "ignore_hidden_text",
    "ignore_text_in_image",
    "camera_photo_correction",
    "crop_enabled",
    "extract_printed_page_number",
    "markdown_annotate_links",
    "markdown_annotate_revisions",
    "markdown_annotate_line_numbers",
    "markdown_compact_tables",
    "disable_cache",
}
LLAMAINDEX_WORKFLOW_LIMITS = {
    "job_timeout_in_seconds": (30, 86_400),
    "job_timeout_extra_time_per_page_in_seconds": (0, 3_600),
    "allowed_page_failure_ratio": (0.0, 1.0),
}
LLAMAINDEX_WORKFLOW_ENUMS = {
    "tier": {"cost_effective", "agentic", "agentic_plus"},
    "confidence_score_effort": {"standard", "high"},
    "specialized_chart_parsing": {"off", "efficient", "agentic", "agentic_plus"},
    "watermark_handling": {"move_to_end", "move_to_start", "remove"},
}
LLAMAINDEX_WORKFLOW_FIELDS = (
    set(LLAMAINDEX_WORKFLOW_BOOLEANS)
    | set(LLAMAINDEX_WORKFLOW_LIMITS)
    | set(LLAMAINDEX_WORKFLOW_ENUMS)
    | {"version", "languages", "custom_prompt", "crop_box"}
)


def llamaindex_workflow_defaults() -> dict[str, Any]:
    return {
        # Every tier that runs a model. `fast` is rule-based and returns no
        # recognition; `agentic_plus` is a separate pipeline (45 credits/page).
        "tier": "agentic",
        "version": "latest",
        "confidence_score_effort": "standard",
        "languages": ["eng"],
        "aggressive_table_extraction": True,
        "disable_heuristics": False,
        "specialized_chart_parsing": "off",
        "cost_optimizer": False,
        "ignore_diagonal_text": False,
        "ignore_hidden_text": False,
        "ignore_text_in_image": False,
        "camera_photo_correction": False,
        "crop_enabled": False,
        "crop_box": {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0},
        "watermark_handling": "move_to_end",
        "extract_printed_page_number": False,
        "markdown_annotate_links": True,
        "markdown_annotate_revisions": False,
        "markdown_annotate_line_numbers": False,
        "markdown_compact_tables": True,
        "custom_prompt": "",
        "job_timeout_in_seconds": 300,
        "job_timeout_extra_time_per_page_in_seconds": 30,
        "allowed_page_failure_ratio": 0.0,
        "disable_cache": False,
    }


@dataclass(frozen=True)
class WorkflowField:
    """One knob of a vendor pipeline, in the shape the panel and the validator need.

    `kind` decides both how the value is validated and how the panel renders it:
    "bool", "int", "float", "enum", "version" (latest or YYYY-MM-DD), "str",
    "str_list", "int_pair", "crop_box". `nullable` means the user can leave it to the
    vendor ("auto"), which is the default for the Paddle knobs.
    """

    key: str
    kind: str
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    values: tuple[str, ...] = ()
    nullable: bool = False
    label: str = ""                     # i18n key; empty = show the key itself
    hints: tuple[tuple[str, str], ...] = ()  # (value, i18n key) for enum options
    max_len: int | None = None
    max_items: int | None = None
    # Cross-field rule: this value may only be enabled when `requires` holds, e.g.
    # cost_optimizer requires tier in (agentic, agentic_plus).
    requires: tuple[str, tuple[str, ...]] | None = None


def paddle_workflow_fields() -> tuple[WorkflowField, ...]:
    """The PaddleOCR-VL pipeline knobs, from the vendor vocabulary in this module."""
    fields: list[WorkflowField] = []
    for key in sorted(PADDLE_BOOLEAN_WORKFLOW):
        fields.append(WorkflowField(
            key, "bool", default=None, nullable=True, label=f"settings.paddleWorkflow.{key}",
        ))
    for key, (low, high) in PADDLE_WORKFLOW_LIMITS.items():
        kind = "int" if key == "vl_rec_max_concurrency" else "float"
        fields.append(WorkflowField(
            key, kind, default=None, minimum=low, maximum=high, nullable=True,
            label="settings.paddleConcurrency" if key == "vl_rec_max_concurrency" else "",
        ))
    for key, choices in PADDLE_WORKFLOW_ENUMS.items():
        fields.append(WorkflowField(
            key, "enum", default=None, values=tuple(sorted(choices)), nullable=True,
            label=f"settings.paddleWorkflow.{key}",
            hints=tuple((value, f"settings.paddleLayoutShape.{value}") for value in sorted(choices))
            if key == "layout_shape_mode" else (),
        ))
    return tuple(fields)


def llamaindex_workflow_fields() -> tuple[WorkflowField, ...]:
    """The LlamaCloud job knobs, from the vendor vocabulary and their defaults."""
    defaults = llamaindex_workflow_defaults()
    fields: list[WorkflowField] = []
    for key, default in defaults.items():
        if key in LLAMAINDEX_WORKFLOW_BOOLEANS:
            fields.append(WorkflowField(key, "bool", default=default))
        elif key in LLAMAINDEX_WORKFLOW_ENUMS:
            fields.append(WorkflowField(
                key, "enum", default=default, values=tuple(sorted(LLAMAINDEX_WORKFLOW_ENUMS[key])),
            ))
        elif key in LLAMAINDEX_WORKFLOW_LIMITS:
            low, high = LLAMAINDEX_WORKFLOW_LIMITS[key]
            fields.append(WorkflowField(
                key, "float" if key == "allowed_page_failure_ratio" else "int",
                default=default, minimum=low, maximum=high,
            ))
        elif key == "version":
            fields.append(WorkflowField(key, "version", default=default))
        elif key == "languages":
            fields.append(WorkflowField(
                key, "str_list", default=list(default), values=tuple(sorted(LLAMAINDEX_OCR_LANGUAGES)),
                max_items=10,
            ))
        elif key == "custom_prompt":
            fields.append(WorkflowField(key, "str", default=default, max_len=4000))
        elif key == "crop_box":
            fields.append(WorkflowField(key, "crop_box", default=dict(default)))
    # The vendor rejects cost_optimizer outside the agentic tiers; declare it, do not
    # special-case the model in the validator.
    fields = [
        replace(field, requires=("tier", ("agentic", "agentic_plus"))) if field.key == "cost_optimizer" else field
        for field in fields
    ]
    return tuple(fields)


@dataclass(frozen=True)
class SettingsSpec:
    """What the settings panel exposes for this model, declared once.

    `workflow` is the vendor pipeline's field schema; the defaults, the validation and
    (in P2c) the panel are all derived from it. `image_fields` says which image caps the
    model actually honours, `sections` which settings groups exist at all (a hosted API
    that only accepts job options declares just "workflow").
    """

    image: dict[str, int | None] = field(
        default_factory=lambda: {"min_pixels": None, "max_pixels": None}
    )
    workflow: tuple[WorkflowField, ...] = ()
    workflow_intro: str = ""
    image_fields: tuple[str, ...] = ("max_pixels",)
    image_cap: int | None = None
    sections: tuple[str, ...] = ("serving", "generation", "image", "workflow", "mlx")
    serving_dtype: bool = True
    generation_unsupported: frozenset[str] = frozenset()
    no_repeat_ngram: bool = False
    workflow_needs_restart: bool = False

    def workflow_defaults(self) -> dict[str, Any]:
        return {field.key: field.default for field in self.workflow}

    def workflow_fields(self) -> tuple[WorkflowField, ...]:
        return self.workflow
