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
        "note": "Il repo ufficiale distribuisce solo inferenza: nessun codice o config di training.",
        "official_notes": (
            "Il paper descrive quattro stadi (allineamento, deformazione, contenuto-struttura, GRPO) "
            "ma il repo non contiene `train/` né un toolchain: la ricetta non è riproducibile.",
        ),
    }),
    labels=Declared(TELEOCR_LABELS),
    cloud=Declared("teleocr"),
    settings=Declared(SettingsSpec(
        image={"min_pixels": None, "max_pixels": 64_000_000},
        image_fields=("max_pixels",),
        image_cap=64_000_000,
        workflow=(
            WorkflowField("layout_mode", "enum", default="Detection",
                          values=("Detection", "Segmentation"),
                          label="settings.teleocrLayoutMode",
                          hints=(("Detection", "settings.teleocrDetectionHint"),
                                 ("Segmentation", "settings.teleocrSegmentationHint"))),
        ),
        workflow_intro="settings.teleocrLayoutOfficial",
        no_repeat_ngram=True,
    )),
)
