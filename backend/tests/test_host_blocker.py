"""A model the Tabularium host cannot run is marked so, automatically, and its
runs are refused before any page is queued."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services import model_registry, paddle_runtime, recognition

NO_AVX2 = "la CPU di questo server non supporta AVX2"


@pytest.fixture
def old_cpu(monkeypatch):
    monkeypatch.setattr(paddle_runtime, "cpu_unsupported_reason", lambda: NO_AVX2)


@pytest.fixture
def new_cpu(monkeypatch):
    monkeypatch.setattr(paddle_runtime, "cpu_unsupported_reason", lambda: None)


def test_paddle_models_are_blocked_on_a_cpu_without_avx2(old_cpu):
    assert model_registry.host_blocker("paddleocr-vl") == "cpu_no_avx2"
    assert model_registry.host_blocker("xiaomi-ocr-0") == "cpu_no_avx2"
    # Models that do not run Paddle here are not affected.
    assert model_registry.host_blocker("mineru2.5") is None
    assert model_registry.host_blocker("llamaindex-ocria-1") is None


def test_nothing_is_blocked_on_a_cpu_with_avx2(new_cpu):
    assert model_registry.host_blocker("paddleocr-vl") is None


def test_catalog_carries_the_blocker(old_cpu, monkeypatch):
    monkeypatch.setattr(model_registry, "ensure_runtime_async", lambda _a: None)
    items = {item["adapter_id"]: item for item in model_registry.list_models()}
    assert items["paddleocr-vl"]["host_blocker"] == "cpu_no_avx2"
    assert items["mineru2.5"]["host_blocker"] is None


def test_no_runtime_install_is_started_where_it_cannot_work(old_cpu, monkeypatch):
    monkeypatch.setattr(model_registry, "is_installed", lambda _a: True)
    started = []
    monkeypatch.setattr(model_registry.threading, "Thread", lambda *a, **k: started.append(1))
    model_registry.ensure_runtime_async("paddleocr-vl")
    assert started == []


def test_a_blocked_model_run_is_refused_before_queuing(old_cpu, monkeypatch):
    monkeypatch.setattr(
        recognition.inference, "get_inference_config",
        lambda: {"enabled": True, "adapter_id": "paddleocr-vl", "url": "http://127.0.0.1:1/v1"},
    )
    with pytest.raises(HTTPException) as refused:
        recognition.create_run(1, [1], engine="model", lang="en")
    assert refused.value.status_code == 409
    assert "AVX2" in refused.value.detail
