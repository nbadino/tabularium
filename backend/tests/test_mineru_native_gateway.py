from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def _load_gateway():
    path = Path(__file__).resolve().parents[2] / "scripts/cloud/mineru_native_gateway.py"
    spec = importlib.util.spec_from_file_location("mineru_native_gateway_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gateway_delegates_page_to_official_client_and_returns_blocks(monkeypatch):
    gateway = _load_gateway()

    async def noop_startup():
        return None

    monkeypatch.setattr(gateway, "_startup", noop_startup)
    monkeypatch.setenv("TABULARIUM_SERVER_API_KEY", "test-key")

    class FakeMinerUClient:
        async def aio_two_step_extract(self, image):
            assert image.size == (12, 8)
            return [{"type": "table", "bbox": [0.1, 0.2, 0.9, 0.8], "content": "<table/>"}]

    gateway._client = FakeMinerUClient()
    payload = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(payload, format="PNG")
    with TestClient(gateway.app) as client:
        response = client.post(
            "/parse", content=payload.getvalue(),
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    assert response.json() == {
        "blocks": [{"type": "table", "bbox": [0.1, 0.2, 0.9, 0.8], "content": "<table/>"}]
    }


def test_gateway_applies_upstream_workflow_and_generation_settings(monkeypatch):
    gateway = _load_gateway()
    calls = {}

    class FakeMinerUClient:
        def __init__(self, **kwargs):
            calls["options"] = kwargs
            self.sampling_params = {"table": type("Sampling", (), {
                "temperature": 0.0, "top_p": 0.01, "top_k": 1,
                "presence_penalty": 1.0, "frequency_penalty": 0.005,
                "repetition_penalty": 1.0, "no_repeat_ngram_size": 100,
                "max_new_tokens": None,
            })()}

    fake_module = type(sys)("mineru_vl_utils")
    fake_module.MinerUClient = FakeMinerUClient
    sampling_module = type(sys)("mineru_vl_utils.mineru_client")

    class FakeSamplingParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    sampling_module.MinerUSamplingParams = FakeSamplingParams
    monkeypatch.setitem(sys.modules, "mineru_vl_utils", fake_module)
    monkeypatch.setitem(sys.modules, "mineru_vl_utils.mineru_client", sampling_module)
    monkeypatch.setenv("TABULARIUM_MINERU_SETTINGS", '{"serving":{"max_num_seqs":3},"workflow":{"layout_image_size":[1200,1200],"image_analysis":true},"generation":{"temperature":0.2,"max_tokens":1000}}')
    gateway._client = None

    asyncio.run(gateway._startup())

    assert calls["options"]["backend"] == "http-client"
    assert calls["options"]["max_concurrency"] == 3
    assert calls["options"]["layout_image_size"] == (1200, 1200)
    assert calls["options"]["image_analysis"] is True
    sampling = gateway._client.sampling_params["table"]
    assert sampling.temperature == 0.2
    assert sampling.max_new_tokens == 1000
