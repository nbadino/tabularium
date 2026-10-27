"""Model surfaces: what every model must declare, and which absences are legitimate.

Phase 0 of `docs/architecture/program.md` (rules R4 and R5). This module changes no
product behaviour. It *projects* the registries that already exist into a single
matrix — one row per adapter, one column per surface — so that a missing piece is a
failure instead of a silence.

Before this module, a model without a serving recipe, or a model with no native label
map, looked exactly like a model nobody had finished: the dictionaries simply had no
key. Here every absence has to be *earned*: either the surface is present, or a rule
says why it cannot be, or the model is listed in `CANONICAL_VOCABULARY` with a reason.

The invariants live in `backend/tests/test_model_surface.py`; the generated manifest
is `scripts/surfaces.py`.

Phase 1 turns the derived rules into declared fields on `ModelSpec`, so a
half-declared model cannot even be constructed. Until then, this is the check.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import models as model_specs
from . import model_adapters, model_labels, model_settings, serve_recipes, train_recipes

# --- the surfaces -----------------------------------------------------------
# A "surface" is a place where a model must be known for the application to work
# completely. Add one here only together with the check that tests it.
SURFACES: tuple[str, ...] = ("capabilities", "labels", "serve", "train", "settings")

# Backends reached over someone else's API: there is no server of ours to launch,
# so a serve recipe would be a lie. This is a category, not a per-model excuse.
API_ONLY_BACKENDS: frozenset[str] = frozenset({"opencode-gateway", "llamaindex-api"})

# Models whose prompts already speak the canonical taxonomy: they declare it in their
# spec (`labels=NotApplicable(reason)`), because a native map would translate the canon
# into itself. Derived, so an excuse cannot outlive the fact it describes.
CANONICAL_VOCABULARY: dict[str, str] = model_specs.not_applicable_reasons("labels")


# --- stringly-typed vocabularies -------------------------------------------
# These values are dispatch keys read with `getattr(...) == "..."` in serve_manager,
# inference and prefill. A new value that nobody handles used to fall through
# silently; declaring the vocabulary makes adding one a deliberate act.
SERVE_BACKENDS: frozenset[str] = frozenset({
    "vllm-openai",
    "vllm-openai-native-client",
    "docker-vllm-openai",
    "opencode-gateway",
    "llamaindex-api",
})
SERVE_RUNTIMES: frozenset[str] = frozenset({
    "vllm", "docker", "monkeyocr", "teleocr-native", "mineru-native",
})
CHAT_PROTOCOLS: frozenset[str] = frozenset({"openai", "google-generatecontent"})
NATIVE_PREFILL_MODES: frozenset[str] = frozenset({"two_stage", "end2end", "official"})

# Which `VllmClient.<runner>_native_page` serves a model's official pipeline. A runner
# is behaviour, so it is declared on the adapter (next to `native_prefill_mode`), and the
# prefill dispatches on it instead of comparing adapter ids.
NATIVE_RUNNERS: frozenset[str] = frozenset(
    {"teleocr", "glmocr", "mineru", "llamaindex", "qwen", "paddle"}
)


@dataclass(frozen=True)
class SurfaceState:
    """One cell of the matrix.

    `state` is "declared" when the surface exists, "not_applicable" when a rule says
    it must not exist, and "missing" when it should exist and does not. `reason` is
    always populated: for a declared surface it says where it is declared.
    """

    surface: str
    state: str
    reason: str

    @property
    def ok(self) -> bool:
        return self.state != "missing"


@dataclass(frozen=True)
class ModelRow:
    adapter_id: str
    display_name: str
    surfaces: dict[str, SurfaceState]
    cloud_template: str | None

    def state(self, surface: str) -> SurfaceState:
        return self.surfaces[surface]


def _required(adapter_id: str, surface: str) -> tuple[bool, str]:
    """Whether a surface is required for this model, and the rule that decides it.

    The rule is derived from what the model already declares about itself, so it
    cannot go stale when a model is removed or added.
    """
    if adapter_id.startswith("custom-"):
        return False, "checkpoint registered at runtime: its surfaces are the registry row"
    adapter = model_adapters.get_adapter(adapter_id)
    cap = adapter.capabilities
    if surface == "capabilities":
        return True, "every model declares its identity and capabilities"
    if surface == "settings":
        return True, "every model has serving/generation/image settings"
    if surface == "serve":
        if cap.serve_backend in API_ONLY_BACKENDS:
            return False, f"serve_backend '{cap.serve_backend}' is an external API: nothing to launch"
        return True, f"serve_backend '{cap.serve_backend}' is a runtime we launch"
    if surface == "train":
        if not cap.training_types:
            return False, "training_types is empty: the model is not trainable by design"
        return True, f"training_types {tuple(cap.training_types)} requires a recipe"
    if surface == "labels":
        if adapter_id in CANONICAL_VOCABULARY:
            return False, CANONICAL_VOCABULARY[adapter_id]
        return True, "the model emits a vendor vocabulary that must be mapped to the canon"
    raise ValueError(f"unknown surface '{surface}'")


def _presence(adapter_id: str, surface: str) -> tuple[bool, str]:
    """Whether the surface is actually declared, and where."""
    if surface == "capabilities":
        return True, f"model_adapters.get_adapter('{adapter_id}').capabilities"
    if surface == "labels":
        present = adapter_id in model_labels.NATIVE_LABELS
        return present, "model_labels.NATIVE_LABELS"
    if surface == "serve":
        present = adapter_id in serve_recipes.RECIPES
        return present, "serve_recipes.RECIPES"
    if surface == "train":
        present = adapter_id in train_recipes.TRAIN_RECIPES
        return present, "train_recipes.TRAIN_RECIPES"
    if surface == "settings":
        try:
            settings = model_settings.get_settings(adapter_id)
        except (ValueError, KeyError):
            # Unknown adapter: the surface cannot be resolved, so it is not declared.
            return False, "model_settings.get_settings()"
        present = "recommended" in settings and "effective" in settings
        return present, "model_settings.get_settings()"
    raise ValueError(f"unknown surface '{surface}'")


def surface_state(adapter_id: str, surface: str) -> SurfaceState:
    required, rule = _required(adapter_id, surface)
    present, where = _presence(adapter_id, surface)
    if surface == "train" and present:
        # A recipe with toolchain "none" is a documented "cannot be trained"
        # (inference-only models), not a usable training path. Calling it
        # "declared" would tell the UI and the reader a false story.
        toolchain = train_recipes.TRAIN_RECIPES[adapter_id].toolchain
        if toolchain == "none":
            if required:
                return SurfaceState(
                    surface,
                    "missing",
                    "training_types is non-empty but the recipe has toolchain 'none'",
                )
            return SurfaceState(
                surface,
                "not_applicable",
                "inference-only: no training_types and the recipe declares that training is not supported",
            )
    if present:
        return SurfaceState(surface, "declared", where)
    if required:
        return SurfaceState(surface, "missing", rule)
    return SurfaceState(surface, "not_applicable", rule)


def matrix() -> list[ModelRow]:
    """Every model, every surface. The one thing `scripts/surfaces.py` prints."""
    rows: list[ModelRow] = []
    for cap in model_adapters.list_adapters():
        adapter_id = cap["adapter_id"]
        rows.append(
            ModelRow(
                adapter_id=adapter_id,
                display_name=cap.get("display_name") or adapter_id,
                surfaces={surface: surface_state(adapter_id, surface) for surface in SURFACES},
                cloud_template=_cloud_template(adapter_id),
            )
        )
    return rows


def _cloud_template(adapter_id: str) -> str | None:
    # Imported lazily: model_registry imports model_adapters and pulls in the
    # download machinery, which the invariant test does not need.
    from . import model_registry

    return model_registry._CLOUD_TEMPLATES.get(adapter_id)


def problems() -> list[tuple[str, SurfaceState]]:
    """Every cell that should exist and does not, with the model it belongs to."""
    found: list[tuple[str, SurfaceState]] = []
    for row in matrix():
        for surface in SURFACES:
            cell = row.state(surface)
            if not cell.ok:
                found.append((row.adapter_id, cell))
    return found


def problems_by_model() -> dict[str, list[SurfaceState]]:
    out: dict[str, list[SurfaceState]] = {}
    for row in matrix():
        missing = [row.state(s) for s in SURFACES if not row.state(s).ok]
        if missing:
            out[row.adapter_id] = missing
    return out


def stale_excuses() -> list[str]:
    """Entries of `CANONICAL_VOCABULARY` that no longer describe reality.

    An excuse that outlives its reason is worse than no excuse: it hides a model that
    has since grown a native map, and it tells the next reader a false story.
    """
    stale: list[str] = []
    known = {cap["adapter_id"] for cap in model_adapters.list_adapters()}
    for adapter_id, reason in CANONICAL_VOCABULARY.items():
        if adapter_id not in known:
            stale.append(f"'{adapter_id}' is not in the registry: {reason}")
        elif adapter_id in model_labels.NATIVE_LABELS:
            stale.append(
                f"'{adapter_id}' now has a native label map in model_labels.NATIVE_LABELS: "
                "remove the excuse"
            )
    return stale


def vocabulary_violations() -> list[str]:
    """Declared dispatch values that are not in the declared vocabulary."""
    violations: list[str] = []
    for cap in model_adapters.list_adapters():
        adapter = model_adapters.get_adapter(cap["adapter_id"])
        backend = adapter.capabilities.serve_backend
        if backend not in SERVE_BACKENDS:
            violations.append(
                f"{cap['adapter_id']}: serve_backend '{backend}' is not in SERVE_BACKENDS: "
                "add it to the vocabulary and to the dispatch before using it"
            )
        protocol = getattr(adapter, "chat_protocol", "openai")
        if protocol not in CHAT_PROTOCOLS:
            violations.append(
                f"{cap['adapter_id']}: chat_protocol '{protocol}' is not in CHAT_PROTOCOLS"
            )
        mode = getattr(adapter, "native_prefill_mode", "")
        if mode and mode not in NATIVE_PREFILL_MODES:
            violations.append(
                f"{cap['adapter_id']}: native_prefill_mode '{mode}' is not in NATIVE_PREFILL_MODES"
            )
        runner = getattr(adapter, "native_runner", None)
        if runner is not None and runner not in NATIVE_RUNNERS:
            violations.append(
                f"{cap['adapter_id']}: native_runner '{runner}' is not in NATIVE_RUNNERS: "
                "add it to the vocabulary and to VllmClient before using it"
            )
        if mode == "official" and not runner:
            violations.append(
                f"{cap['adapter_id']}: declares an official prefill but no native_runner, "
                "so nothing can execute it"
            )
    for adapter_id, recipe in serve_recipes.RECIPES.items():
        if recipe.runtime not in SERVE_RUNTIMES:
            violations.append(
                f"{adapter_id}: serve recipe runtime '{recipe.runtime}' is not in SERVE_RUNTIMES"
            )
    return violations
