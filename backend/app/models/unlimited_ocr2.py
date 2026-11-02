"""Unlimited-OCR-2: model spec.

Facts from huggingface.co/baidu/Unlimited-OCR-2 (README, config.json, revision
c14071b), github.com/baidu/Unlimited-OCR (`utils/process_unlimitedOCR2.py`) and ms-swift
(`examples/models/unlimited_ocr2/train.sh`, commit d52d0c4), read on 2026-10-31. A new
model, not a revision of Unlimited-OCR: new attention (DASH), new output format, new
labels, and SGLang instead of vLLM.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import ALL_FAMILIES, CANONICAL_TRAIN_PROMPTS, ServeRecipe, WorkflowField

_REVISION = "c14071b549f4fcab10909f3c4acc6a2edb366947"
# The vendor's SGLang build, shipped inside the model repository.
SGLANG_WHEEL = (
    f"https://huggingface.co/baidu/Unlimited-OCR-2/resolve/{_REVISION}/wheel/"
    "sglang-0.5.20.dev13%2Bg75b9b06b02-py3-none-any.whl"
)

# `process_unlimitedOCR2._BASE_LABELS`. `cross_`/`append_` prefixes mark a block that
# continues across pages; the parser strips them before this lookup.
UNLIMITED2_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "header": "Page-header",
    "footer": "Page-footer",
    "page_number": "Page-footer",
    "image": "Picture",
    "figure": "Picture",
    "chart": "Picture",
    "seal": "Picture",
    "table": "Table",
    "image_caption": "Caption",
    "image_footnote": "Footnote",
    "page_footnote": "Footnote",
    "equation": "Formula",
    "ref_text": "Text",
    "aside_text": "Text",
    "algorithm": "Text",
}

SPEC = ModelSpec(
    adapter_id="unlimited-ocr-2",
    serve=Declared(ServeRecipe(
        adapter_id="unlimited-ocr-2",
        hf_repo="baidu/Unlimited-OCR-2",
        served_model_name="Unlimited-OCR-2",
        runtime="sglang",
        python_version="3.10",
        # The wheel pins torch 2.13 / transformers 5.12.1; fastokens backs
        # `--tokenizer-backend fastokens`.
        pip_extra=(SGLANG_WHEEL, "fastokens==0.3.2"),
        # Tested on an A800; the DASH kernels are Triton and the recipe is bf16.
        min_compute_capability=8.0,
        serve_args=(
            "--chat-template", "unlimited-ocr",
            "--tokenizer-backend", "fastokens",
            "--tokenizer-worker-num", "8",
            "--mem-fraction-static", "0.7",
            "--max-running-requests", "64",
            "--max-prefill-tokens", "2048",
            "--enable-custom-logit-processor",
            "--enable-streaming-session",
            "--allow-auto-truncate",
            "--disable-radix-cache",
            "--mm-preprocess-cache-size-mb", "0",
            "--cuda-graph-backend-decode", "full", "--cuda-graph-max-bs-decode", "64",
            "--cuda-graph-backend-prefill", "full",
            "--cuda-graph-max-bs-prefill", "16384",
            "--cuda-graph-bs-prefill", "1024", "2048",
            "--cuda-graph-config", '{"prefill": {"backend": "full", "full_prefill_max_req": 64}}',
        ),
        serve_env=(
            ("SGLANG_UOCR_EANABLE_DOC_LIMIT", "1"),  # sic: the vendor's spelling
            ("SGLANG_UOCR_DOC_GUNDAM_PAGE_WINDOW", "10"),
            ("SGLANG_UOCR_DOC_BASE_PAGE_WINDOW", "10"),
            ("SGLANG_UOCR_MAX_PAGE_TOKENS", "20000"),
            ("SGLANG_OPT_UOCR_SAM_REL_POS_BIAS_MB", "64"),
            ("SGLANG_UOCR_SHM_WRAP_MODEL_DATA", "1"),
            ("SGLANG_UOCR_SHM_WRAP_MIN_MB", "1"),
            ("SGLANG_VLM_CACHE_SIZE_MB", "0"),
            ("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"),
        ),
        source="huggingface.co/baidu/Unlimited-OCR-2 README, SGLang section (revision c14071b)",
    )),
    train=Declared({
        "maturity": "experimental",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        "families": ALL_FAMILIES,
        "official_doc": "https://github.com/modelscope/ms-swift/blob/d52d0c45000b875683e23dcf85d9a6e2ef346e52/examples/models/unlimited_ocr2/train.sh",
        # ms-swift 4.6.0 (the latest release) knows only v1: `unlimited-ocr` (with the
        # hyphen, "not a typo") arrived on main in d52d0c4.
        "swift_model_type": "unlimited-ocr",
        "swift_template": "unlimited_ocr",
        "defaults": {
            "learning_rate": 1e-4,
            "epochs": 1.0,
            "batch_size": 1,
            "grad_accum": 16,
            "max_length": 4096,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 50,
            "save_steps": 50,
        },
        # `--target_modules all-linear` and `--warmup_ratio 0.05` are what the ms-swift
        # generator always writes, and what the example uses.
        "flags": {
            "torch_dtype": "bfloat16",
            "freeze_vit": True,
            "freeze_aligner": True,
        },
        "environment": {
            "ms-swift": "@ git+https://github.com/modelscope/ms-swift@d52d0c45000b875683e23dcf85d9a6e2ef346e52",
            "transformers": "==4.52.1",
        },
        "prompts": CANONICAL_TRAIN_PROMPTS,
        "official_notes": (
            "Hyperparameters are ms-swift's official example for this model (LoRA, 26 GiB).",
            "The model is end2end: inference uses the native block protocol, while this per-task "
            "training produces OTSL on crops. The two formats are not aligned yet.",
        ),
    }),
    labels=Declared(UNLIMITED2_LABELS),
    cloud=NotApplicable("no Modal template: on Vast.ai the instance installs the vendor's SGLang build"),
    settings=Declared(SettingsSpec(
        # vLLM's serving knobs and the MLX section do not apply to an SGLang server.
        sections=("generation", "workflow"),
        workflow=(
            WorkflowField("image_mode", "enum", default="gundam", values=("base", "gundam"),
                          label="settings.unlimitedImageMode",
                          hints=(("gundam", "settings.unlimitedGundamHint"),
                                 ("base", "settings.unlimitedBaseHint"))),
            WorkflowField("ngram_size", "int", default=35, minimum=1, maximum=512,
                          label="settings.unlimitedNgramSize"),
            WorkflowField("window_size", "int", default=128, minimum=1, maximum=2048,
                          label="settings.unlimitedWindowSize"),
            WorkflowField("max_page_tokens", "int", default=20000, minimum=0, maximum=131072,
                          label="settings.unlimited2MaxPageTokens"),
        ),
        workflow_intro="settings.unlimited2WorkflowIntro",
    )),
)
