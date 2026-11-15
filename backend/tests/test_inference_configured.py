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


def test_unlimited_ocr_2_is_not_taken_for_version_1(monkeypatch):
    """The served name "Unlimited-OCR-2" contains "unlimited-ocr": the guess
    returned Unlimited-OCR 1, and it won over the saved adapter, so recognitions
    would have used version 1's protocol and parser."""
    from app.services import inference

    assert inference._adapter_for_endpoint("Unlimited-OCR-2", "http://127.0.0.1:58237/v1") == "unlimited-ocr-2"
    assert inference._adapter_for_endpoint("Unlimited-OCR", "http://127.0.0.1:9/v1") == "unlimited-ocr"


def test_the_saved_adapter_wins_over_a_guess_from_the_name(tmp_path, monkeypatch):
    from app.services import inference

    inference.save_inference_config({
        "url": "http://127.0.0.1:58237/v1", "model": "unlimited-ocr-something",
        "adapter_id": "unlimited-ocr-2", "provider": "vast", "enabled": True,
    })
    assert inference.get_inference_config()["adapter_id"] == "unlimited-ocr-2"
    assert inference.get_vllm_client().adapter.adapter_id == "unlimited-ocr-2"
