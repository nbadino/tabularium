"""Invariants for the model surfaces (program.md, phase P0; rules R4 and R5).

These tests are the reason `AGENTS.md` can be short. They do not describe how to add a
model; they make an incomplete model fail, whoever added it and wherever they stopped.
"""
from __future__ import annotations

from app.services import model_adapters, model_labels, model_surface
from app.services.model_adapters import ModelCapabilities


def _format(missing: list[tuple[str, model_surface.SurfaceState]]) -> str:
    return "\n".join(
        f"  {adapter_id}.{cell.surface}: {cell.reason}" for adapter_id, cell in missing
    )


def test_every_required_surface_is_declared() -> None:
    missing = model_surface.problems()
    assert not missing, (
        "The model registry is incomplete. A surface is missing, or the model was not "
        "declared as legitimately without it:\n" + _format(missing)
    )


def test_excuses_are_still_true() -> None:
    stale = model_surface.stale_excuses()
    assert not stale, (
        "CANONICAL_VOCABULARY contains entries that no longer describe the code:\n  "
        + "\n  ".join(stale)
    )


def test_canonical_vocabulary_models_really_have_no_native_map() -> None:
    # The invariant above already covers this, but this one names the property that
    # matters: an "excuse" and a real map must never coexist for the same model.
    for adapter_id in model_surface.CANONICAL_VOCABULARY:
        assert adapter_id not in model_labels.NATIVE_LABELS, adapter_id


def test_dispatch_vocabularies_are_complete() -> None:
    violations = model_surface.vocabulary_violations()
    assert not violations, (
        "A dispatch value is not in the declared vocabulary. Add it to the vocabulary "
        "and to the code that handles it, or the value will fall through silently:\n  "
        + "\n  ".join(violations)
    )


def test_matrix_covers_every_adapter() -> None:
    declared = {cap["adapter_id"] for cap in model_adapters.list_adapters()}
    assert {row.adapter_id for row in model_surface.matrix()} == declared


def test_every_model_has_serving_settings() -> None:
    # Settings is the surface most often forgotten because it has no registry of its
    # own: it is a function with per-model branches. Assert it explicitly.
    for row in model_surface.matrix():
        assert row.state("settings").state == "declared", row.adapter_id


def test_a_model_added_without_recipes_is_reported(monkeypatch) -> None:
    """The guard must not be vacuous: a new model with nothing but a class fails."""

    class FakeAdapter:
        adapter_id = "fake-model"
        chat_protocol = "openai"
        native_prefill_mode = "end2end"
        capabilities = ModelCapabilities(
            adapter_id="fake-model",
            display_name="Fake",
            tasks=("text",),
            coordinate_system="normalized-0-1000",
            table_format=None,
            training_types=("lora",),
            inference_modes=("vllm",),
            hardware=("cuda",),
            serve_backend="vllm-openai",
        )

    monkeypatch.setattr(
        model_adapters,
        "list_adapters",
        lambda: [{"adapter_id": "fake-model", "display_name": "Fake"}],
    )
    monkeypatch.setattr(model_adapters, "get_adapter", lambda _adapter_id: FakeAdapter())

    missing = model_surface.problems()
    surfaces = {cell.surface for _adapter_id, cell in missing}
    assert any(adapter_id == "fake-model" for adapter_id, _cell in missing)
    # Labels/train/serve have no entry; settings resolve through the registry too.
    assert {"labels", "serve", "train"} <= surfaces


def test_an_official_prefill_declares_a_runner_the_client_can_execute() -> None:
    """`official` without a runner is a mode nothing can run — the silent absence."""
    from app.services.inference import VllmClient
    from app.services import model_adapters

    official = [
        adapter for adapter in model_adapters._ADAPTERS.values()
        if getattr(adapter, "native_prefill_mode", None) == "official"
    ]
    assert official, "no model declares an official prefill: the rule is untested"
    for adapter in official:
        runner = getattr(adapter, "native_runner", None)
        assert runner in model_surface.NATIVE_RUNNERS, adapter.adapter_id
        # The declaration must point at a method that exists, or the failure arrives
        # the first time a page is prefilled with that model.
        assert callable(getattr(VllmClient, f"{runner}_native_page", None)), (
            f"{adapter.adapter_id}: client has no {runner}_native_page"
        )
