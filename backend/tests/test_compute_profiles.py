from __future__ import annotations

from app import db
from app.services import compute_profiles


class HealthyClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def test_connection(self, timeout=10):
        return {"ok": True, "model": self.kwargs["model"]}


def test_profile_activation_is_atomic_and_verified(monkeypatch):
    db.init_db()
    monkeypatch.setattr(compute_profiles, "VllmClient", HealthyClient)
    profiles = compute_profiles.list_profiles()
    assert profiles and profiles[0]["active"] is True
    created = compute_profiles.create_profile(
        {
            "name": "test-local",
            "provider": "local",
            "purpose": "inference",
            "model_adapter_id": "monkeyocrv2-parsing",
            "served_model_name": "MonkeyOCRv2-test",
            "endpoint": "http://127.0.0.1:8888/v1",
            "credential_ref": None,
            "hardware_profile": {"gpu": "test"},
        }
    )
    active = compute_profiles.activate(created["id"])
    assert active["active"] is True
    assert active["served_model_name"] == "MonkeyOCRv2-test"
    assert [p for p in compute_profiles.list_profiles() if p["active"]][0]["id"] == created["id"]
