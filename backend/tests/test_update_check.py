"""The check for a newer release: never an error, cached, can be turned off."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.services import update_check


@pytest.fixture(autouse=True)
def _fresh_cache():
    update_check.reset_cache()
    yield
    update_check.reset_cache()


def _release(tag: str):
    calls = []

    def fake():
        calls.append(tag)
        return {"latest": tag.lstrip("v"), "url": f"https://example.test/{tag}", "published_at": None}

    return fake, calls


def test_newer_release_is_reported(monkeypatch):
    monkeypatch.setattr(config, "VERSION", "0.7.0")
    fake, _ = _release("v0.8.0")
    monkeypatch.setattr(update_check, "_fetch_latest", fake)
    status = update_check.status()
    assert status["update_available"] is True
    assert status["latest"] == "0.8.0"


def test_same_or_older_release_is_not_an_update(monkeypatch):
    monkeypatch.setattr(config, "VERSION", "0.7.0")
    fake, _ = _release("v0.7.0")
    monkeypatch.setattr(update_check, "_fetch_latest", fake)
    assert update_check.status()["update_available"] is False


def test_result_is_cached(monkeypatch):
    fake, calls = _release("v9.0.0")
    monkeypatch.setattr(update_check, "_fetch_latest", fake)
    update_check.status()
    update_check.status()
    assert len(calls) == 1
    update_check.status(force=True)
    assert len(calls) == 2


def test_network_failure_is_unknown_not_an_error(monkeypatch):
    def boom():
        raise OSError("offline")

    monkeypatch.setattr(update_check, "_fetch_latest", boom)
    status = update_check.status()
    assert status["update_available"] is None
    assert status["error"] == "OSError"


def test_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("TABULARIUM_UPDATE_CHECK", "0")

    def never():
        raise AssertionError("must not call GitHub")

    monkeypatch.setattr(update_check, "_fetch_latest", never)
    status = update_check.status()
    assert status["enabled"] is False
    assert status["update_available"] is None


def test_endpoint_answers(monkeypatch):
    fake, _ = _release("v99.0.0")
    monkeypatch.setattr(update_check, "_fetch_latest", fake)
    with TestClient(app) as client:
        r = client.get("/api/system/update")
        assert r.status_code == 200
        assert r.json()["update_available"] is True
