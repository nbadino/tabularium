"""Stored references to a removed model must not break startup or the settings pages.

`unlimited-ocr` (v1) was removed in favour of `unlimited-ocr-2`: other installs may
still have the old id in `meta`, in compute profiles, in custom models and in historic
runs. The registries answer `ValueError` for an unknown id; these tests pin that the
readers that face those rows tolerate it, and that the two actions that cannot go on
say so instead of answering 500.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.adapters import compute_profiles as profilestore
from app.db import connect, init_db
from app.main import app
from app.services import compute_profiles, custom_models, inference, model_adapters

REMOVED = "unlimited-ocr"


def _seed_stored_references() -> int:
    """Rows an install upgraded from a version with the v1 adapter can hold."""
    init_db()
    with connect() as conn:
        for key, value in (
            ("inference_adapter_id", REMOVED),
            ("inference_model", "Unlimited-OCR"),
            ("inference_url", "http://127.0.0.1:1/v1"),
            (f"model_settings:{REMOVED}", json.dumps({"workflow": {"ngram_size": 40}})),
        ):
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.execute(
            "INSERT OR REPLACE INTO custom_models "
            "(id, display_name, hf_repo, served_model_name, base_adapter_id) VALUES (?,?,?,?,?)",
            ("custom-finetune-v1", "Fine-tune v1", "local/x", "x", REMOVED),
        )
        profile_id = profilestore.insert(
            {
                "name": "saved-v1",
                "provider": "custom",
                "purpose": "both",
                "model_adapter_id": REMOVED,
                "served_model_name": "Unlimited-OCR",
                "endpoint": "https://example.invalid/v1",
            },
            conn,
        )
    return profile_id


def _clear() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM meta WHERE key LIKE 'inference_%' OR key = ?", (f"model_settings:{REMOVED}",))
        conn.execute("DELETE FROM custom_models WHERE id = 'custom-finetune-v1'")
        conn.execute("DELETE FROM compute_profiles WHERE name = 'saved-v1'")


def test_the_removed_adapter_is_unknown_to_the_registry():
    try:
        model_adapters.get_adapter(REMOVED)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("v1 must not be registered")
    assert all(item["adapter_id"] != REMOVED for item in model_adapters.list_adapters())


def test_stored_references_do_not_break_startup_or_the_settings_pages():
    profile_id = _seed_stored_references()
    try:
        # The context manager runs the lifespan: the app must start with these rows.
        with TestClient(app) as client:
            assert inference.get_inference_config()["adapter_id"] == REMOVED
            for route in (
                "/api/models",
                "/api/system/model-adapters",
                "/api/system/train-recipes",
                "/api/system/model-settings",
                "/api/system/prefill-engines",
                "/api/system/inference",
                "/api/system/compute-profiles",
            ):
                assert client.get(route).status_code == 200, route
            profiles = client.get("/api/system/compute-profiles").json()
            assert any(p["id"] == profile_id and p["model_adapter_id"] == REMOVED for p in profiles)
            # The settings of a removed model are gone, as for any unknown id.
            assert client.get(f"/api/system/model-settings/{REMOVED}").status_code == 404
    finally:
        _clear()


def test_a_custom_model_built_on_the_removed_adapter_survives_as_a_generic_model():
    _seed_stored_references()
    try:
        adapter = model_adapters.get_adapter("custom-finetune-v1")
        assert adapter._base is None  # noqa: SLF001 - the base is what was removed
        assert adapter.capabilities.tasks == ()
        ids = {item["adapter_id"] for item in model_adapters.list_adapters()}
        assert "custom-finetune-v1" in ids
        assert custom_models.get("custom-finetune-v1")["base_adapter_id"] == REMOVED
    finally:
        _clear()


def test_activating_a_saved_profile_of_a_removed_model_says_so(monkeypatch):
    profile_id = _seed_stored_references()
    monkeypatch.setattr(compute_profiles, "validate_endpoint", lambda url: url)
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/system/compute-profiles/{profile_id}/activate")
        assert response.status_code == 409
        assert "unlimited-ocr" in response.json()["detail"]
    finally:
        _clear()


def test_saving_the_active_removed_model_as_a_profile_says_so():
    _seed_stored_references()
    try:
        with TestClient(app) as client:
            response = client.post("/api/consensus/profiles", json={"name": "copy-of-v1"})
        assert response.status_code == 409, response.text
        assert "unlimited-ocr" in response.json()["detail"]
    finally:
        _clear()


def test_unlimited_ocr2_endpoint_maps_to_its_own_adapter():
    """The endpoint alias of the removed model was a prefix of v2's served name."""
    assert inference._adapter_for_endpoint("Unlimited-OCR-2", "http://x/v1") == "unlimited-ocr-2"  # noqa: SLF001
    assert inference._adapter_for_endpoint("Unlimited-OCR", "http://x/v1") is None  # noqa: SLF001
