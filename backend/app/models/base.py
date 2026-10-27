"""One model, one file: the type that owns a model's data.

Phase 1 of `docs/architecture/program.md`. Before this package, a model was known in
six places (`model_adapters`, `serve_recipes`, `train_recipes`, `model_labels`,
`model_settings`, `model_registry`) and no check tied them together. Here a model
declares its surfaces once; the legacy registries derive from these specs, so a call
site does not change and a half-declared model cannot be constructed.

`Declared(value)` means the surface exists. `NotApplicable(reason)` means it must not
exist, and says why. Both are required: a `ModelSpec` with a missing field raises
`TypeError` at import time, which is the earliest possible failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .types import SettingsSpec

T = TypeVar("T")


@dataclass(frozen=True)
class Declared(Generic[T]):
    """A surface this model declares, with the data the application reads."""

    value: T


@dataclass(frozen=True)
class NotApplicable:
    """A surface this model must not have. The reason is shown, never inferred."""

    reason: str


# The surfaces a model declares here. `capabilities` stays in `model_adapters` until
# phase P4, because it is behaviour (prompts, parsers) as much as data.
SPEC_SURFACES: tuple[str, ...] = ("serve", "train", "labels", "cloud", "settings")


@dataclass(frozen=True)
class ModelSpec:
    """Everything about a model that is data, in one place.

    Fields are required and keyword-only: a spec written positionally is unreadable,
    and a spec that forgets a surface should fail at import, not six months later.
    """

    adapter_id: str
    serve: Declared[Any] | NotApplicable = field(kw_only=True)
    train: Declared[Any] | NotApplicable = field(kw_only=True)
    labels: Declared[dict[str, str]] | NotApplicable = field(kw_only=True)
    cloud: Declared[str] | NotApplicable = field(kw_only=True)
    settings: Declared[SettingsSpec] | NotApplicable = field(kw_only=True)

    def surface(self, name: str) -> Declared[Any] | NotApplicable:
        if name not in SPEC_SURFACES:
            raise KeyError(f"'{name}' is not a spec surface: {SPEC_SURFACES}")
        return getattr(self, name)

    def value(self, name: str) -> Any:
        surface = self.surface(name)
        return surface.value if isinstance(surface, Declared) else None

    def reason(self, name: str) -> str | None:
        surface = self.surface(name)
        return surface.reason if isinstance(surface, NotApplicable) else None


# --- helpers used by the legacy registries ---------------------------------


def declared_values(surface: str) -> dict[str, Any]:
    """`{adapter_id: value}` for every spec that declares the surface."""
    from . import SPECS  # imported here: `models/__init__` imports the spec modules

    return {
        adapter_id: spec.value(surface)
        for adapter_id, spec in SPECS.items()
        if isinstance(spec.surface(surface), Declared)
    }


def not_applicable_reasons(surface: str) -> dict[str, str]:
    from . import SPECS

    return {
        adapter_id: spec.reason(surface) or ""
        for adapter_id, spec in SPECS.items()
        if isinstance(spec.surface(surface), NotApplicable)
    }


def spec_for(adapter_id: str) -> ModelSpec | None:
    from . import SPECS

    return SPECS.get(adapter_id)
