"""`configured` tells a model the user chose apart from the built-in default endpoint."""
from __future__ import annotations

from app.services import inference as infmod


def test_fresh_install_is_not_configured():
    # The seeded "legacy-active" profile mirrors the default endpoint: no choice was made.
    assert infmod.is_configured({}, {"name": "legacy-active"}, {}) is False
    assert infmod.is_configured({}, None, {}) is False


def test_saved_endpoint_or_provider_counts_as_configured():
    assert infmod.is_configured({"inference_url": "https://x.modal.run/v1"}, None, {}) is True
    assert infmod.is_configured({"inference_provider": "vast"}, {"name": "legacy-active"}, {}) is True


def test_activated_profile_counts_as_configured():
    assert infmod.is_configured({}, {"name": "modal-mineru"}, {}) is True


def test_environment_endpoint_counts_as_configured():
    assert infmod.is_configured({}, None, {"TABULARIUM_VLLM_URL": "http://gpu:8000/v1"}) is True


def test_config_and_api_expose_configured(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(infmod, "is_configured", lambda *_a, **_k: False)
    assert infmod.get_inference_config()["configured"] is False
    with TestClient(app) as client:
        assert client.get("/api/system/inference").json()["configured"] is False
