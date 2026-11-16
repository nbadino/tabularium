"""TeleOCR: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, SettingsSpec
from ..models.types import (
    ServeRecipe,
    WorkflowField,
)
TELEOCR_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "table": "Table",
    "image": "Picture",
    "header": "Page-header",
    "footer": "Page-footer",
    # Sul corpus la testata porta il numero del fascicolo.
    "page_number": "Issue-number",
    "page_footnote": "Footnote",
    "aside_text": "Text",
    "equation": "Formula",
    "equation_block": "Formula",
    "ref_text": "List-item",
    "list": "List-item",
    "table_caption": "Caption",
    "image_caption": "Caption",
    "table_footnote": "Footnote",
    "image_footnote": "Footnote",
}

SPEC = ModelSpec(
    adapter_id="teleocr",
    serve=Declared(ServeRecipe(
        adapter_id="teleocr",
        hf_repo="StarDoc-AI/TeleOCR",
        served_model_name="StarDoc-AI/TeleOCR",
        runtime="teleocr-native",
        # Il runner ufficiale usa TeleOCRClient con backend vllm-async-engine;
        # l'engine e il pipeline layout+OCR vivono nello stesso processo. La
        # dipendenza upstream installa il modello vLLM e il logits processor.
        vllm_version="0.11.0",
        min_compute_capability=7.0,
        transformers_version="4.57.1",
        native_remote_port=8889,
        pip_extra=(
            # Upstream documents pip install -e . at the repository root.
            # This registers both the architecture plugin and the official
            # TeleOCR package, including its no-repeat-ngram logits processor.
            "git+https://github.com/caipeng328/TeleOCR.git@main",
        ),
        serve_args=(
            "--trust-remote-code",
            "--logits-processors",
            "TeleOCR.vlm_utils.vlm_client.vllm_v1_no_repeat_ngram:VllmV1NoRepeatNGramLogitsProcessor",
            "--dtype", "bfloat16",
            # TeleOCR/config.py upstream recommends 0.95.
            "--gpu-memory-utilization", "0.95",
            "--max-model-len", "16384",
        ),
        source="README e config ufficiali TeleOCR: infer.py / vllm-async-engine",
    )),
    train=Declared({
        "maturity": "inference-only",
        "toolchain": "none",
        "data_view": "none",
        "families": (),
        "official_doc": "https://github.com/caipeng328/TeleOCR",
        "note": (
            "No vendor training code, and no toolchain registers the architecture: a Qwen2.5-VL "
            "vision tower with a Qwen3ForCausalLM decoder, which ms-swift's `qwen2_5_vl` cannot load."
        ),
        "official_notes": (
            "The paper describes four stages (alignment, deformation, content-structure, GRPO), "
            "but the repository has no `train/` and no toolchain: the recipe is not reproducible.",
            "The HF `config.json` says `qwen2_5_vl`, but the weights are not stock Qwen2.5-VL: "
            "`modeling_naviocr.py` (its `auto_map`) gives the decoder head_dim 128 on hidden 1024 "
            "with 16 heads, q/k RMSNorm and no qkv bias, and the vendor's vLLM plugin "
            "(`TeleOCR-vllm/TeleOCR_vllm/qwen2_5_vl.py`) builds the language model as "
            "`Qwen3ForCausalLM`.",
            "ms-swift's `qwen2_5_vl` loads transformers' `Qwen2_5_VLForConditionalGeneration` "
            "(head_dim = hidden/heads = 64, qkv bias, no q/k norm) and has no option to use the "
            "checkpoint's `auto_map`: `swift sft --model_type qwen2_5_vl` would not match these "
            "weights. A custom registration (`--custom_register_path`) would be Tabularium's own "
            "recipe, not a documented one, so the model stays inference-only.",
            "If it becomes trainable, the data is already right: TeleOCR emits OTSL "
            "(`DEFAULT_PROMPTS['table']` asks for it, `post_process/otsl2html.py` converts it) and "
            "its prompts start with a newline (`\\nPlease output the text content from the image.`).",
        ),
    }),
    labels=Declared(TELEOCR_LABELS),
    cloud=Declared("teleocr"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": 64_000_000},
        image_fields=("max_pixels",),
        image_cap=64_000_000,
        workflow=(
            WorkflowField("layout_mode", "enum", default="Detection", essential=True,
                          values=("Detection", "Segmentation"),
                          label="settings.teleocrLayoutMode",
                          hints=(("Detection", "settings.teleocrDetectionHint"),
                                 ("Segmentation", "settings.teleocrSegmentationHint"))),
        ),
        workflow_intro="settings.teleocrLayoutOfficial",
        no_repeat_ngram=True,
    )),
)
