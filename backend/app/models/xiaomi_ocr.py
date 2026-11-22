"""Xiaomi-OCR-0: a 0.8B Qwen3.5 recognition model behind PP-DocLayoutV3.

Facts from github.com/xiaomi-research/Xiaomi-OCR-0 (README, INSTALL.md,
`evaluation_pipeline/`, `skills/xiaomi-ocr/mcp_ocr_server.py`) and the Hugging Face card,
read on 2026-10-31. The model emits no boxes: the vendor's region mode detects layout with
PaddleX PP-DocLayoutV3 and sends each crop with a task prompt.

Training (read on 2026-11-16): the vendor publishes none, but the checkpoint is a stock
`Qwen3_5ForConditionalGeneration` (`config.json`), which ms-swift registers as model type and
template `qwen3_5` since release 4.0.0 (`swift/model/models/qwen.py`). The recipe follows
ms-swift's own Qwen3.5 Best Practice and stays `experimental` until a real run.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.paddleocr_vl import PADDLE_LABELS
from ..models.types import (
    FAMILY_FORMULA,
    FAMILY_TABLE,
    FAMILY_TEXT,
    RECOGNITION_FAMILIES,
    ServeRecipe,
    WorkflowField,
)

# The vendor's task prompts (`evaluation_pipeline/twostage_img2md.py` and
# `skills/xiaomi-ocr/mcp_ocr_server.py` `PROMPTS`, identical in both). One owner: the adapter
# sends them at inference and the training recipe trains on them.
XIAOMI_TASK_PROMPTS: dict[str, str] = {
    "text": "Extract the text in the image.",
    "table": "Parse the table in the image into OTSL.",
    "formula": "Identify the formula in the image and represent it using LATEX format.",
}

# Image bounds the served model sees. `evaluation_pipeline/cropping.py` fits every input within
# 2048 x 2048 px (MAX_IMAGE_EDGE), i.e. at most 4096 tokens of 32 x 32 px (patch 16, merge 2);
# the HF `processor_config.json` sets `shortest_edge` 65536 px, i.e. at least 64 tokens.
XIAOMI_PAGE_MAX_EDGE = 2048
XIAOMI_TOKEN_PX = 32
XIAOMI_IMAGE_MIN_TOKENS = 65_536 // (XIAOMI_TOKEN_PX * XIAOMI_TOKEN_PX)
XIAOMI_IMAGE_MAX_TOKENS = XIAOMI_PAGE_MAX_EDGE * XIAOMI_PAGE_MAX_EDGE // (XIAOMI_TOKEN_PX * XIAOMI_TOKEN_PX)

# Gold-set evaluation on the training GPU (`services/gold_eval.py`). The image is the
# qwen training image unchanged (its 11 layers are the first 11 of this one, checked on
# GHCR on 2026-11-17) plus `/root/eval-env`, the stack the manual gold evaluations of
# November 2026 were scored with; the digest is what `:v1` pointed to that day. Its torch
# wheel is built for CUDA 13.0, so the host driver must support 13.0.
XIAOMI_GOLD_EVAL = {
    "image": "ghcr.io/nbadino/tabularium-eval-xiaomi:v1",
    "image_digest": "sha256:fd41c392fb545b1cc6a27aa8af211fa6ad6e8c1f78594c3756d485f2f3e14fbd",
    "python": "/root/eval-env/bin/python",
    "min_cuda": 13.0,
    "stack": {"torch": "2.14.0+cu130", "transformers": "5.17.0"},
    # The processor `size` bounds of the trained image tokens (64 to 4096 tokens of 32 x 32 px).
    "image_size": {
        "shortest_edge": XIAOMI_IMAGE_MIN_TOKENS * XIAOMI_TOKEN_PX * XIAOMI_TOKEN_PX,
        "longest_edge": XIAOMI_IMAGE_MAX_TOKENS * XIAOMI_TOKEN_PX * XIAOMI_TOKEN_PX,
    },
    "chat_template_kwargs": {"enable_thinking": False},
    # A full register table in OTSL: the longest of the 135 manual gold outputs (Nov 2026)
    # took 4,997 tokens, none was truncated at this limit.
    "max_new_tokens": 12288,
    "page_timeout_s": 1500,
}

# `config.json` `text_config` (revision bbd8cf8) and the size of `model.safetensors`.
XIAOMI_OCR_0_ARCH = {
    "hidden_size": 1024,
    "intermediate_size": 3584,
    "num_hidden_layers": 24,
    "vocab_size": 248320,
    "weights_mib": 1666,
}

# PP-DocLayoutV3's vocabulary, as `layout_detect_paddlex.LABEL_TASK_MAPPING` routes it.
# Same detector as PaddleOCR-VL, so the shared names keep PaddleOCR-VL's translation;
# the five that pipeline never emits are added here.
XIAOMI_LAYOUT_TASKS: dict[str, str] = {
    **{label: "text" for label in (
        "abstract", "algorithm", "content", "doc_title", "figure_title", "paragraph_title",
        "reference_content", "text", "vertical_text", "vision_footnote", "seal",
        "formula_number", "header", "footer", "number", "footnote", "aside_text", "reference",
    )},
    "table": "table",
    "display_formula": "formula",
    "inline_formula": "formula",
    **{label: "skip" for label in ("chart", "image", "footer_image", "header_image")},
}
_EXTRA_LABELS = {
    "algorithm": "Text",
    "reference_content": "Text",
    "formula_number": "Text",
    "display_formula": "Formula",
    "inline_formula": "Formula",
}
XIAOMI_LABELS: dict[str, str] = {
    label: PADDLE_LABELS.get(label) or _EXTRA_LABELS[label] for label in XIAOMI_LAYOUT_TASKS
}

SPEC = ModelSpec(
    adapter_id="xiaomi-ocr-0",
    serve=Declared(ServeRecipe(
        adapter_id="xiaomi-ocr-0",
        hf_repo="xiaomi-research/Xiaomi-OCR-0",
        served_model_name="xiaomi-ocr-0",
        runtime="vllm",
        # The vendor pins no vLLM ("a release that supports Qwen3_5ForConditionalGeneration");
        # 0.25.1 is the release Datalab ships for the same architecture (Surya 2.5), and
        # a pin keeps the Vast environment reproducible.
        vllm_version="0.25.1",
        serve_args=("--trust-remote-code", "--max-model-len", "16384"),
        source="Xiaomi-OCR-0 INSTALL.md: vllm serve ... --trust-remote-code --max-model-len 16384",
    )),
    train=Declared({
        "maturity": "experimental",
        "toolchain": "ms-swift",
        "data_view": "ms-swift-jsonl",
        # The model emits no boxes: its layout is PP-DocLayoutV3's, trained by its own recipe
        # (`models/pp_doclayoutv3.py`) and picked with the `layout_model` setting.
        "families": RECOGNITION_FAMILIES,
        "official_doc": "https://github.com/modelscope/ms-swift/blob/main/docs/source_en/BestPractices/Qwen3_8-Best-Practice.md",
        "swift_model_type": "qwen3_5",
        "swift_template": "qwen3_5",
        "note": "The vendor publishes no training code: this is ms-swift's Qwen3.5 recipe, not Xiaomi's.",
        # Best Practice, Qwen3.5 dense LoRA: lr 1e-4, r8/a32 on all-linear, warmup 0.05,
        # eval/save every 50 steps. Its batch is 4 per GPU on 4 GPUs; one GPU reaches the same
        # global batch with 1 x 16. `max_length` is not the BP's 2048: a crop alone may take
        # 4096 image tokens, so 8192 leaves room for a register table's OTSL.
        "defaults": {
            "learning_rate": 1e-4,
            "epochs": 1.0,
            "batch_size": 1,
            "grad_accum": 16,
            "max_length": 8192,
            "lora_rank": 8,
            "lora_alpha": 32,
            "eval_steps": 50,
            "save_steps": 50,
        },
        # ms-swift's documented default lr for full-parameter training; the BP has no dense
        # full-SFT example for the transformers backend.
        "type_defaults": {"full": {"learning_rate": 1e-5}},
        "flags": {
            "attn_impl": "flash_attn",
            "torch_dtype": "bfloat16",
            "deepspeed": "zero2",
            # ms-swift defaults, declared so the script states them.
            "freeze_vit": True,
            "freeze_aligner": True,
            "freeze_llm": False,
            "gradient_checkpointing": True,
            "image_min_token_num": XIAOMI_IMAGE_MIN_TOKENS,
            "image_max_token_num": XIAOMI_IMAGE_MAX_TOKENS,
            "eval_batch_size": 1,
        },
        "environment": {"ms-swift": ">=4.3.1", "transformers": ">=5.9"},
        "prompts": {
            FAMILY_TEXT: XIAOMI_TASK_PROMPTS["text"],
            FAMILY_TABLE: XIAOMI_TASK_PROMPTS["table"],
            FAMILY_FORMULA: XIAOMI_TASK_PROMPTS["formula"],
        },
        "architecture": XIAOMI_OCR_0_ARCH,
        "gold_eval": XIAOMI_GOLD_EVAL,
        "official_notes": (
            "Xiaomi publishes no training code; the model card describes its method (Q-Mask "
            "anchoring, CPT, Mix-RL) in prose only. The checkpoint is a stock "
            "`Qwen3_5ForConditionalGeneration`, which ms-swift registers as `qwen3_5` (model type "
            "and template) since 4.0.0: the recipe is ms-swift's Qwen3.5 Best Practice.",
            "Thinking: the vendor serves with `enable_thinking: false`, i.e. the answer follows "
            "`<think>\\n\\n</think>\\n\\n`. The `qwen3_5` template adds that prefix to every "
            "target (`--add_non_thinking_prefix`, default true) and, from ms-swift 4.3.1, keeps "
            "it out of the loss (`ignore_empty_think` is appended automatically).",
            "Prompts are the vendor's task prompts, identical in `twostage_img2md.py` and "
            "`mcp_ocr_server.py`: `Extract the text in the image.`, `Parse the table in the image "
            "into OTSL.`, `Identify the formula in the image and represent it using LATEX format.`",
            "Tables are trained as OTSL (`fcel/ecel/lcel/ucel/xcel/nl`), the format the model "
            "emits and the vendor's `postprocess/otsl.py` reads; spanned header cells keep their "
            "`lcel`/`ucel` continuations.",
            "Image tokens match serving: `IMAGE_MIN_TOKEN_NUM=64` (processor `shortest_edge` "
            "65536 px) and `IMAGE_MAX_TOKEN_NUM=4096` (the 2048 x 2048 px input bound). Crops come "
            "from the full-resolution scan, while serving crops a page first fitted to 2048 px: on "
            "scans larger than that, training crops are sharper than served ones.",
            "Order in the message: the image comes before the prompt (`<image>…`), as Tabularium "
            "and the vendor's MCP server send it; the vendor's evaluation pipeline sends the text first.",
            "No packing or padding_free: ms-swift's FAQ says Qwen3.5's linear attention has no "
            "var_len and packing is not recommended. The BP installs `flash-linear-attention` and "
            "`causal-conv1d` for speed; without them the GatedDeltaNet layers use the torch path.",
            "Not verified against the model's own output: the trailing `<nl>` of an OTSL table "
            "and the LaTeX delimiters of a formula (Tabularium writes `$…$`; the vendor strips "
            "`$$`, `\\[`, `\\(` in post-processing).",
        ),
    }),
    labels=Declared(XIAOMI_LABELS),
    cloud=NotApplicable("no Modal template: the model is served with stock vLLM, locally or on Vast.ai"),
    settings=Declared(SettingsSpec(
        workflow=(
            WorkflowField("layout_threshold", "float", default=0.25, minimum=0.0, maximum=1.0,
                          step=0.05, label="settings.xiaomiLayoutThreshold"),
            # PP-DocLayoutV3 weights: the vendor's, or a layout run trained here
            # (`models/pp_doclayoutv3.py`), as `<project_id>/<run_id>`.
            WorkflowField("layout_model", "layout_model", default="official",
                          label="settings.xiaomiLayoutModel"),
            # `evaluation_pipeline/twostage_img2md.py`: `--retry-repetitive` (off by
            # default) reruns a region whose output looks like a loop with
            # `--retry-presence-penalty` 1.5.
            WorkflowField("retry_repetitive", "bool", default=False,
                          label="settings.xiaomiRetryRepetitive"),
            WorkflowField("retry_presence_penalty", "float", default=1.5, minimum=-2.0, maximum=2.0,
                          step=0.1, label="settings.xiaomiRetryPresencePenalty"),
        ),
        workflow_intro="settings.xiaomiWorkflowIntro",
    )),
)
