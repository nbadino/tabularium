"""Surya OCR 2.5 (Datalab): a 0.7B Qwen3.5 VLM for layout, OCR and tables.

Every fact here comes from the model card (huggingface.co/datalab-to/surya-ocr-2.5) and
from the `surya` package that serves it (github.com/datalab-to/surya,
`surya/inference/backends/vllm.py`, `surya/settings.py`), read on 2026-10-31.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
from ..models.types import ServeRecipe

# The raw labels the model writes in a block header (`surya/inference/prompts.py`
# LAYOUT_LABEL_SET, plus `Picture` from `compact.BLOCK_LABELS`). `Blank-Page` has no
# entry on purpose: it marks an empty page, not a block, and the parser drops it.
SURYA_LABELS: dict[str, str] = {
    "Text": "Text",
    "Section-Header": "Section-header",
    "Page-Header": "Page-header",
    "Page-Footer": "Page-footer",
    "Caption": "Caption",
    "Footnote": "Footnote",
    "Equation-Block": "Formula",
    "List-Group": "List-item",
    "Table": "Table",
    "Table-Of-Contents": "Text",
    "Form": "Text",
    "Code-Block": "Text",
    "Complex-Block": "Text",
    "Bibliography": "Text",
    "Image": "Picture",
    "Figure": "Picture",
    "Picture": "Picture",
    "Diagram": "Picture",
    "Chemical-Block": "Picture",
}

SPEC = ModelSpec(
    adapter_id="surya-ocr-2.5",
    serve=Declared(ServeRecipe(
        adapter_id="surya-ocr-2.5",
        hf_repo="datalab-to/surya-ocr-2.5",
        served_model_name="surya-ocr-2.5",
        # The package launches exactly this image (`VLLM_DOCKER_IMAGE`): Qwen3.5 needs
        # Transformers >= 5.12.1, which the image already carries.
        runtime="docker",
        docker_image="vllm/vllm-openai:v0.25.1",
        # bfloat16 needs compute capability >= 8.0 (`surya/settings.py` VLLM_DTYPE note).
        min_compute_capability=8.0,
        # The vendor's argv minus `--max-num-seqs`/`--max-num-batched-tokens`, which
        # it derives from a per-GPU-type table (VLLM_GPU_TYPE): vLLM's own defaults
        # apply instead of a value tuned for a card we do not know.
        serve_args=(
            "--no-enforce-eager",
            "--dtype", "bfloat16",
            "--max-model-len", "24576",
            "--gpu-memory-utilization", "0.85",
            "--enable-prefix-caching",
            "--mm-processor-kwargs", '{"min_pixels": 3136, "max_pixels": 6291456}',
        ),
        source="github.com/datalab-to/surya surya/inference/backends/vllm.py (2026-10-29)",
    )),
    train=Declared({
        "maturity": "inference-only",
        "toolchain": "none",
        "data_view": "none",
        "families": (),
        "official_doc": "https://huggingface.co/datalab-to/surya-ocr-2.5",
        "note": "The vendor publishes no training code: fine-tuning is offered as a service.",
        "official_notes": (
            "Model card, Training: \"If you want help finetuning Surya on your own data "
            "... reach us at hi@datalab.to\". No hyperparameters are published.",
        ),
    }),
    labels=Declared(SURYA_LABELS),
    cloud=NotApplicable("no Modal template: on Vast.ai the instance is rented with the vendor's vLLM image"),
    settings=Declared(SettingsSpec()),
)
