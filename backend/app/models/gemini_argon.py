"""Gemini 4 Argon: a closed model reached through an OpenAI/Google-compatible gateway.

No weights, no serving process, no training. The label map is explicitly absent because
the end2end prompt enumerates the canonical project taxonomy itself.
"""
from __future__ import annotations

from ..models.base import Declared, ModelSpec, NotApplicable, SettingsSpec
SPEC = ModelSpec(
    adapter_id="gemini-argon",
    serve=NotApplicable("closed API reached through a gateway: no weights to serve"),
    train=NotApplicable("closed model: no fine-tuning and no published weights"),
    labels=NotApplicable(
        "hosted model: the end2end prompt enumerates the canonical project taxonomy, "
        "so there is no vendor vocabulary to translate"
    ),
    cloud=NotApplicable("hosted API: no Modal template"),
    settings=Declared(SettingsSpec()),
)
