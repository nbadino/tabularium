"""OCRia-1 through LlamaCloud's native asynchronous parsing API.

A hosted parser, not a chat model: there is nothing to serve and nothing to train, and
both absences are declared here instead of being inferred from the capabilities.
"""
from __future__ import annotations

from ..models.types import (
    WorkflowField,
    llamaindex_workflow_defaults,
    llamaindex_workflow_fields,
)
from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
LLAMAINDEX_LABELS: dict[str, str] = {
    "text": "Text",
    "code": "Text",
    "link": "Text",
    "heading": "Title",
    "title": "Title",
    "section_header": "Section-header",
    "list": "List-item",
    "list_item": "List-item",
    "table": "Table",
    "image": "Picture",
    "header": "Page-header",
    "page_header": "Page-header",
    "footer": "Page-footer",
    "page_footer": "Page-footer",
    "page_number": "Issue-number",
    "caption": "Caption",
    "footnote": "Footnote",
    "formula": "Formula",
}

SPEC = ModelSpec(
    adapter_id="llamaindex-ocria-1",
    serve=NotApplicable("hosted API (LlamaCloud): there is no server of ours to launch"),
    train=NotApplicable("hosted parser: the vendor publishes no training code"),
    labels=Declared(LLAMAINDEX_LABELS),
    cloud=NotApplicable("hosted API: no Modal template"),
    settings=Declared(SettingsSpec(
        sections=("workflow",),
        workflow=llamaindex_workflow_fields(),
    )),
)
