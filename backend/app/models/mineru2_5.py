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
        # mineru-vl-utils 2.0.8 (2026-10-29) is the current client; its [vllm]
        # extra declares vLLM >=0.19.1,<0.31.0. The 0.21.0 pin measured with this
        # checkpoint stays inside that range.
        vllm_version="0.21.0",
        native_remote_port=8891,
        pip_extra=("mineru-vl-utils[vllm]==2.0.8",),
        serve_args=(
            "--logits-processors", "mineru_vl_utils:MinerULogitsProcessor",
        ),
        source="README MinerU2.5: MinerUClient http-client + logits processor ufficiale",
    )),
    train=Declared({
        "maturity": "planned",
        # The vendor forbids `swift sft`: the official recipe is the wrapper
        # `python run_mineru.py sft --config configs/sft/sft_table_rec.yaml`, shipped in
        # `mineru_sft_code.zip`, which is not published anywhere we can fetch (checked
        # 2026-11-16, evidence in official_notes). Tabularium cannot generate a faithful
        # script without that bundle, so the recipe stays `planned` and the UI links
        # to the lesson.
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
            "The official lesson explicitly says **not** to use `swift sft` but "
            "`python run_mineru.py sft --config configs/sft/sft_table_rec.yaml`: the wrapper loads "
            "`mineru_ext` with its own templates and training logic.",
            "The code is `mineru_sft_code.zip`, attached to the lesson and not versioned. Checked on "
            "2026-11-16: the opendatalab/mineru-tutorials tree at 761b4a9895 holds only the lesson "
            "Markdown and PDF, the repository has no releases, and GitHub code search finds no "
            "`run_mineru.py` with an `sft` command, no `sft_table_rec.yaml` and no `mineru_ext` "
            "anywhere. The lesson points to the camp platform (heywhale.com, enrolment required) for "
            "the bundle.",
            "The only public `run_mineru.py` is in the unmerged MetaX edition of the lesson "
            "(opendatalab/mineru-tutorials#1, head c0d209c2): it says the official wrapper may add "
            "`mineru_ext` logic and replaces it with a stub that calls plain `swift sft` "
            "(`model_type`/`template` `qwen2_vl`, LoRA rank 8, `MAX_PIXELS` 50176, `max_steps: 1` "
            "smoke test). It contradicts the main lesson and is a smoke test, so it is not adopted "
            "as the recipe.",
            "The lesson trains `MinerU2.5-2509-1.2B` (AGPL-3.0). The served checkpoint "
            "`MinerU2.5-Pro-2605-1.2B` (Apache-2.0) has the same `Qwen2VLForConditionalGeneration` "
            "architecture, but whether the bundle supports it is unverified.",
            "Table targets in both lessons are HTML (`<table><tr><td>…`) after "
            "`<image>\\nTable Recognition:`, in ms-swift `messages` + `images` JSONL; the demo "
            "datasets that would fix the exact schema are inside the zip.",
            "To unblock: supply `mineru_sft_code.zip` (from the lesson page or the camp platform) so "
            "it can be declared as an external checkout with its checksum and license.",
            "The official training prompts have the leading newline: `\\nTable Recognition:`, "
            "`\\nFormula Recognition:`, `\\nText Recognition:`.",
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
