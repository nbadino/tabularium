"""Ownership invariants for the settings panel (program.md, phase P2).

Settings used to be a chain of `adapter_id == ...` ternaries: the default for one model
lived in the same function as the default for every other. The defaults now come from
the model's spec. What remains is *validation*, which is still per-model and is being
replaced by a declared field schema (P2b); this test keeps it from growing in the
meantime.
"""
from __future__ import annotations

import inspect
import re

from app.services import model_settings

def _branch_count(source: str) -> int:
    # A branch compares the id with literal values; iterating over a dict keyed by
    # adapter id (`for adapter_id in ...`) is not a per-model rule.
    return len(re.findall(r"adapter_id (?:==|not in|in) (?:\{|\")", source))


def test_defaults_never_branch_per_model() -> None:
    source = inspect.getsource(model_settings._defaults)
    assert _branch_count(source) == 0, (
        "`_defaults` must read the model's spec, not compare adapter ids: a default "
        "declared twice is a default that will drift."
    )


def test_no_per_model_branch_anywhere_in_the_module() -> None:
    source = inspect.getsource(model_settings)
    found = _branch_count(source)
    assert found == 0, (
        f"{found} per-model branch(es) in model_settings: declare the field in the model's "
        "SettingsSpec instead of special-casing the adapter id"
    )
