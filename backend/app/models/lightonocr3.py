"""LightOnOCR-3: a 1B end-to-end model with no vendor training code.

The first model whose data lives entirely in one file (program.md phase P1). Its four
surfaces are here: the serving recipe, the training entry (which exists only to tell
the UI *why* it cannot be trained), the native label map and the cloud template.
`serve_recipes`, `train_recipes`, `model_labels` and `model_registry` derive their
entries from this spec, so there is no second place to update.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import ServeRecipe
SPEC = ModelSpec(
    adapter_id="lightonocr-3",
    serve=Declared(
        ServeRecipe(
            adapter_id="lightonocr-3",
            hf_repo="lightonai/LightOnOCR-3-1B",
            served_model_name="lightonocr-3",
            runtime="vllm",
            # The version the vendor used for its throughput benchmarks.
            vllm_version="0.30.0",
            serve_args=(
                "--limit-mm-per-prompt", '{"image": 1}',
                "--mm-processor-cache-gb", "0",
                "--no-enable-prefix-caching",
            ),
            source=(
                "LightOnOCR-3-1B model card: one image per prompt, multimodal cache and "
                "prefix caching disabled"
            ),
        )
    ),
    train=Declared(
        {
            "maturity": "inference-only",
            "toolchain": "none",
            "data_view": "none",
            "families": (),
            "official_doc": "https://github.com/lightonai/LightOnOCR",
            "note": (
                "The official repository is client/CLI/benchmark only: no training code "
                "and no training config."
            ),
            "official_notes": (
                "The vendor blog documents the method and the reward (IoU/0.75) but "
                "publishes neither code nor SFT hyperparameters.",
            ),
        }
    ),
    labels=Declared(
        {
            "text": "Text",
            "title": "Title",
            "list": "List-item",
            "header": "Page-header",
            "footer": "Page-footer",
            # As with TeleOCR: on `index` pages this is the issue number.
            "page_number": "Issue-number",
            "footnote": "Footnote",
            "caption": "Caption",
            "formula": "Formula",
            "code": "Text",
            "table": "Table",
            "aside_text": "Note",
            "image": "Picture",
            "chart": "Picture",
            "header_image": "Picture",
            "footer_image": "Picture",
        }
    ),
    cloud=NotApplicable(
        "no Modal template: the model is served locally with vLLM and has no cloud image"
    ),
    settings=Declared(SettingsSpec()),
)
