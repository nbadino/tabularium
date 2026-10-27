"""Invariants for the model specs (program.md, phase P1).

A spec owns a model's data; the registries derive their entries from it. These tests
keep three statements true now that the migration is complete:

1. every registered model has exactly one spec, and every spec names a real model;
2. a surface declared `NotApplicable` has no entry anywhere;
3. a spec cannot be built without declaring all four surfaces.
"""
from __future__ import annotations

import pytest

from app import models as model_specs
from app.models import Declared, ModelSpec, NotApplicable
from app.services import (
    model_adapters,
    model_labels,
    model_registry,
    model_settings,
    serve_recipes,
    train_recipes,
)


def _registry(surface: str):
    return {
        "serve": serve_recipes.RECIPES,
        "train": train_recipes.TRAIN_RECIPES,
        "labels": model_labels.NATIVE_LABELS,
        "cloud": model_registry._CLOUD_TEMPLATES,
    }[surface]


def _has_entry(adapter_id: str, surface: str) -> bool:
    if surface == "settings":
        # Settings has no dictionary: it is assembled per adapter from the spec plus
        # the serving recipe and the adapter's sampling defaults.
        return "recommended" in model_settings.get_settings(adapter_id)
    return adapter_id in _registry(surface)


def _static_adapters() -> set[str]:
    # `custom-*` adapters are registered from the database at runtime: their surfaces
    # are the registry row itself, and they have no spec by design.
    return {
        cap["adapter_id"]
        for cap in model_adapters.list_adapters()
        if not cap["adapter_id"].startswith("custom-")
    }


def test_every_registered_model_has_a_spec() -> None:
    missing = _static_adapters() - set(model_specs.SPECS)
    assert not missing, (
        "A model exists in the adapter registry but has no spec: add "
        "`backend/app/models/<id>.py` with all four surfaces declared.\n  "
        + "\n  ".join(sorted(missing))
    )


def test_a_spec_always_describes_a_real_model() -> None:
    unknown = set(model_specs.SPECS) - _static_adapters()
    assert not unknown, f"specs for models that are not in the registry: {sorted(unknown)}"


def test_a_declared_absence_has_no_entry() -> None:
    leftovers: list[str] = []
    for adapter_id, spec in model_specs.SPECS.items():
        for surface in model_specs.SPEC_SURFACES:
            if isinstance(spec.surface(surface), NotApplicable) and _has_entry(adapter_id, surface):
                leftovers.append(f"{adapter_id}.{surface}")
    assert not leftovers, (
        "Declared NotApplicable but still present in the registry:\n  " + "\n  ".join(leftovers)
    )


def test_declared_surfaces_reach_the_registries() -> None:
    for adapter_id, spec in model_specs.SPECS.items():
        for surface in model_specs.SPEC_SURFACES:
            if isinstance(spec.surface(surface), Declared):
                assert _has_entry(adapter_id, surface), f"{adapter_id}.{surface} not derived"


def test_a_spec_cannot_be_built_without_all_four_surfaces() -> None:
    # This is the guarantee that replaced the debt ratchet: forgetting a surface is a
    # TypeError at import time, not a silent gap discovered months later.
    with pytest.raises(TypeError):
        ModelSpec(adapter_id="incomplete")  # type: ignore[call-arg]
