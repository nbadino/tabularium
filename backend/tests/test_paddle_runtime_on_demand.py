"""The official PaddleOCR-VL path installs its local runtime when it is missing,
also when the VLM is remote (a rented GPU behind a tunnel)."""
from __future__ import annotations

import pytest
from PIL import Image

from app.services import paddle_official
from app.services.paddle_runtime import cpu_unsupported_reason


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


@pytest.fixture(autouse=True)
def _fresh_attempt(monkeypatch):
    monkeypatch.setattr(paddle_official, "_INSTALL_ATTEMPTED", False)
    monkeypatch.setattr(paddle_official.paddle_runtime, "cpu_unsupported_reason", lambda: None)


IVY_BRIDGE = "processor\t: 0\nmodel name\t: Intel(R) Xeon(R) CPU E5-2690 v2\nflags\t\t: fpu sse4_2 avx popcnt\n"


def test_cpu_without_avx2_is_named_before_any_install(monkeypatch):
    reason = cpu_unsupported_reason(system="linux", machine="x86_64", cpuinfo=IVY_BRIDGE)
    assert reason and "AVX2" in reason
    assert cpu_unsupported_reason(
        system="linux", machine="x86_64", cpuinfo=IVY_BRIDGE.replace("avx ", "avx avx2 ")) is None
    assert cpu_unsupported_reason(system="darwin", machine="arm64", cpuinfo="") is None

    calls = []
    monkeypatch.setattr(paddle_official.paddle_runtime, "cpu_unsupported_reason", lambda: reason)
    monkeypatch.setattr(paddle_official.paddle_runtime, "ensure_ready", lambda: calls.append(1))
    with pytest.raises(RuntimeError, match="AVX2"):
        paddle_official._require_runtime()
    assert calls == []


def test_a_failed_install_is_not_retried_on_every_page(monkeypatch):
    calls = []
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda **_k: False)
    monkeypatch.setattr(paddle_official.paddle_runtime, "ensure_ready", lambda: calls.append(1))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            paddle_official._require_runtime()
    assert calls == [1]


def test_illegal_instruction_is_explained():
    import subprocess

    crashed = subprocess.CompletedProcess([], -4, stdout="", stderr="")
    assert "illegal instruction" in str(paddle_official._runner_failure(crashed, "PaddleOCRVL"))
    failed = subprocess.CompletedProcess([], 1, stdout="", stderr="ValueError: bad page\n")
    assert str(paddle_official._runner_failure(failed, "X")) == "X: ValueError: bad page"
