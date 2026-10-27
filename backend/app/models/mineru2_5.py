"""MinerU2.5: model spec (program.md phase P1).

The data this model needs across the application: its serving recipe, its training
block, its native label map and its cloud template. The registries derive their
entries from here.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, SettingsSpec
from ..models.types import (
    FAMILY_FORMULA,
    FAMILY_TABLE,
    FAMILY_TEXT,
    RECOGNITION_FAMILIES,
    ServeRecipe,
    WorkflowField,
)
MINERU_LAYOUT_LABELS: dict[str, str] = {
    "text": "Text",
    "title": "Title",
    "doc_title": "Title",
    "table": "Table",
    "equation": "Formula",
    "image": "Picture",
    "chart": "Picture",
}

MINERU_NATIVE_LABELS: dict[str, str] = {
    "text": "Text", "title": "Title", "table": "Table",
    "equation": "Formula", "formula_number": "Formula",
    "equation_block": "Formula", "image": "Picture", "chart": "Picture",
    "image_block": "Picture", "header": "Page-header",
    "footer": "Page-footer", "page_number": "Page-footer",
    "page_footnote": "Footnote", "table_footnote": "Footnote",
    "image_footnote": "Footnote", "list": "List-item",
    "list_item": "List-item", "ref_text": "List-item",
    "table_caption": "Caption", "image_caption": "Caption",
    "code_caption": "Caption", "code": "Text", "algorithm": "Text",
    "aside_text": "Note", "index": "Text", "phonetic": "Text",
}

SPEC = ModelSpec(
    adapter_id="mineru2.5",
    serve=Declared(ServeRecipe(
        local_serve="mlx-mineru",
        adapter_id="mineru2.5",
        hf_repo="opendatalab/MinerU2.5-Pro-2605-1.2B",
        served_model_name="mineru2.5",
        # The official MinerUClient owns layout preparation, block extraction,
        # asynchronous OCR fan-out, and post-processing. vLLM remains the
        # model server; a small sidecar invokes that upstream client.
        runtime="mineru-native",
        # MinerU 2.0.x è la linea upstream corrente; il suo extra [vllm]
        # dichiara vLLM >=0.19.1,<0.29.0. Manteniamo il pin 0.21.0 già
        # verificato con questo checkpoint, evitando l'installazione del
        # vecchio client 1.x che non include gli aggiornamenti del pipeline.
        vllm_version="0.21.0",
        native_remote_port=8891,
        pip_extra=("mineru-vl-utils[vllm]==2.0.5",),
        serve_args=(
            "--logits-processors", "mineru_vl_utils:MinerULogitsProcessor",
        ),
        source="README MinerU2.5: MinerUClient http-client + logits processor ufficiale",
    )),
    train=Declared({
        "maturity": "planned",
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
    }),
    labels=Declared({**MINERU_LAYOUT_LABELS, **MINERU_NATIVE_LABELS}),
    cloud=Declared("mineru"),
    settings=Declared(SettingsSpec(
        image={},
        image_fields=(),
        workflow=(
            WorkflowField("layout_image_size", "int_pair", default=[1036, 1036],
                          minimum=256, maximum=4096),
            WorkflowField("min_image_edge", "int", default=28, minimum=1, maximum=4096,
                          label="settings.mineru.minImageEdge"),
            WorkflowField("max_image_edge_ratio", "float", default=50.0, minimum=1, maximum=200,
                          label="settings.mineru.maxImageRatio"),
            WorkflowField("simple_post_process", "bool", default=False, label="settings.mineru.simple_post_process"),
            WorkflowField("handle_equation_block", "bool", default=True, label="settings.mineru.handle_equation_block"),
            WorkflowField("abandon_list", "bool", default=False, label="settings.mineru.abandon_list"),
            WorkflowField("abandon_paratext", "bool", default=False, label="settings.mineru.abandon_paratext"),
            WorkflowField("image_analysis", "bool", default=False, label="settings.mineru.image_analysis"),
            WorkflowField("enable_table_formula_eq_wrap", "bool", default=False, label="settings.mineru.enable_table_formula_eq_wrap"),
        ),
        workflow_intro="settings.mineruWorkflowIntro",
        no_repeat_ngram=True,
        workflow_needs_restart=True,
    )),
)
