"""The official PaddleOCR-VL path installs its local runtime when it is missing,
also when the VLM is remote (a rented GPU behind a tunnel)."""
from __future__ import annotations

import pytest
from PIL import Image

from app.services import paddle_official


def test_missing_runtime_is_installed_once_then_used(monkeypatch):
    installed = {"done": False, "calls": 0}

    def ensure_ready():
        installed["calls"] += 1
        installed["done"] = True

    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda **_k: installed["done"])
    monkeypatch.setattr(paddle_official.paddle_runtime, "ensure_ready", ensure_ready)
    paddle_official._require_runtime()
    paddle_official._require_runtime()
    assert installed["calls"] == 1


def test_failed_install_says_why(monkeypatch):
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda **_k: False)

    def ensure_ready():
        raise RuntimeError("pip: no matching distribution")

    monkeypatch.setattr(paddle_official.paddle_runtime, "ensure_ready", ensure_ready)
    with pytest.raises(RuntimeError, match="non installabile.*no matching distribution"):
        paddle_official._require_runtime()


def test_dead_model_server_is_reported_before_any_install(monkeypatch):
    calls = []
    monkeypatch.setattr(paddle_official, "_endpoint_alive", lambda _e: False)
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda **_k: False)
    monkeypatch.setattr(paddle_official.paddle_runtime, "ensure_ready", lambda: calls.append(1))
    with pytest.raises(RuntimeError, match="non risponde"):
        paddle_official.parse_page(Image.new("RGB", (10, 10)), "http://127.0.0.1:1/v1", "m", 10, 10)
    assert calls == []


def test_layout_detection_installs_the_runtime_too(monkeypatch):
    calls = []
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda **_k: bool(calls))
    monkeypatch.setattr(paddle_official.paddle_runtime, "ensure_ready", lambda: calls.append(1))

    class Done(Exception):
        pass

    def stop(*_a, **_k):
        raise Done

    monkeypatch.setattr(paddle_official.subprocess, "run", stop)
    with pytest.raises(Done):
        paddle_official.detect_layout(Image.new("RGB", (10, 10)), threshold=0.5)
    assert calls == [1]
