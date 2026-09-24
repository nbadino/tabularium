from __future__ import annotations

import importlib.util
import io
import sys
import types
from pathlib import Path
from types import SimpleNamespace
import asyncio

from fastapi.testclient import TestClient
from PIL import Image


def _load_gateway():
    path = Path(__file__).resolve().parents[2] / "scripts/cloud/teleocr_native_gateway.py"
    spec = importlib.util.spec_from_file_location("teleocr_native_gateway_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gateway_delegates_to_official_batch_runner_and_forwards_settings(monkeypatch):
    gateway = _load_gateway()
    async def noop_startup():
        return None

    monkeypatch.setattr(gateway, "_startup", noop_startup)

    called = {}

    class FakeTeleOCRClient:
        def __init__(self):
            self.sampling_params = {
                "text": SimpleNamespace(
                    temperature=0.0, top_p=0.01, top_k=1, presence_penalty=1.0,
                    frequency_penalty=0.05, repetition_penalty=1.0,
                    no_repeat_ngram_size=100, max_new_tokens=None,
                )
            }

        async def aio_batch_two_step_extract(self, images):
            called["image_count"] = len(images)
            called["image_size"] = images[0].size
            called["layout_mode"] = config.LAYOUT_MODE
            called["max_pixels"] = config.MAX_PIXELS
            called["sampling"] = self.sampling_params["text"].temperature
            return [[{"type": "text", "bbox": [0.1, 0.2, 0.9, 0.8], "content": "native"}]]

    gateway._client = FakeTeleOCRClient()
    config = types.ModuleType("TeleOCR.config")
    config.LAYOUT_MODE = "Detection"
    config.MAX_PIXELS = 64_000_000
    package = types.ModuleType("TeleOCR")
    package.__path__ = []
    subpackage = types.ModuleType("TeleOCR.vlm_utils")
    subpackage.__path__ = []
    client_module = types.ModuleType("TeleOCR.vlm_utils.TeleOCR_client")

    class FakeSamplingParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    client_module.TeleOCRSamplingParams = FakeSamplingParams
    monkeypatch.setitem(sys.modules, "TeleOCR", package)
    monkeypatch.setitem(sys.modules, "TeleOCR.config", config)
    monkeypatch.setitem(sys.modules, "TeleOCR.vlm_utils", subpackage)
    monkeypatch.setitem(sys.modules, "TeleOCR.vlm_utils.TeleOCR_client", client_module)
    monkeypatch.setenv("TABULARIUM_SERVER_API_KEY", "secret-test-key")

    image = Image.new("RGB", (12, 8), "white")
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    with TestClient(gateway.app) as client:
        response = client.post(
            "/parse",
            content=payload.getvalue(),
            headers={
                "Authorization": "Bearer secret-test-key",
                "x-teleocr-layout-mode": "Segmentation",
                "x-teleocr-max-pixels": "2000000",
                "x-teleocr-generation": '{"temperature":0.2}',
            },
        )

    assert response.status_code == 200
    assert response.json()["blocks"] == [
        {"type": "text", "bbox": [0.1, 0.2, 0.9, 0.8], "content": "native"}
    ]
    assert called == {
        "image_count": 1,
        "image_size": (12, 8),
        "layout_mode": "Segmentation",
        "max_pixels": 2_000_000,
        "sampling": 0.2,
    }


def test_startup_uses_official_async_engine_and_effective_serving_settings(monkeypatch):
    gateway = _load_gateway()
    gateway._client = None
    calls = {}

    package = types.ModuleType("TeleOCR")
    package.__path__ = []
    config = types.ModuleType("TeleOCR.config")
    config.MAX_PIXELS = 0
    subpackage = types.ModuleType("TeleOCR.vlm_utils")
    subpackage.__path__ = []
    client_module = types.ModuleType("TeleOCR.vlm_utils.TeleOCR_client")

    class FakeTeleOCRClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs

    client_module.TeleOCRClient = FakeTeleOCRClient
    for name, module in {
        "TeleOCR": package,
        "TeleOCR.config": config,
        "TeleOCR.vlm_utils": subpackage,
        "TeleOCR.vlm_utils.TeleOCR_client": client_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    class FakeAsyncEngineArgs:
        def __init__(self, model_path, **kwargs):
            calls["engine_args"] = (model_path, kwargs)

    class FakeAsyncLLM:
        @classmethod
        def from_engine_args(cls, args):
            calls["async_llm_args"] = args
            return "vendor-async-engine"

    vllm = types.ModuleType("vllm")
    vllm.__path__ = []
    engine = types.ModuleType("vllm.engine")
    engine.__path__ = []
    arg_utils = types.ModuleType("vllm.engine.arg_utils")
    arg_utils.AsyncEngineArgs = FakeAsyncEngineArgs
    v1 = types.ModuleType("vllm.v1")
    v1.__path__ = []
    v1_engine = types.ModuleType("vllm.v1.engine")
    v1_engine.__path__ = []
    async_llm_module = types.ModuleType("vllm.v1.engine.async_llm")
    async_llm_module.AsyncLLM = FakeAsyncLLM
    for name, module in {
        "vllm": vllm,
        "vllm.engine": engine,
        "vllm.engine.arg_utils": arg_utils,
        "vllm.v1": v1,
        "vllm.v1.engine": v1_engine,
        "vllm.v1.engine.async_llm": async_llm_module,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setenv("TABULARIUM_TELEOCR_MODEL_PATH", "/root/models/TeleOCR")
    monkeypatch.setenv(
        "TABULARIUM_TELEOCR_SETTINGS",
        '{"serving":{"gpu_memory_utilization":0.9,"max_model_len":8192,'
        '"max_num_seqs":2,"max_num_batched_tokens":4096},'
        '"image":{"max_pixels":2000000}}',
    )

    asyncio.run(gateway._startup())

    assert calls["engine_args"] == (
        "/root/models/TeleOCR",
        {
            "dtype": "bfloat16",
            "gpu_memory_utilization": 0.9,
            "max_model_len": 8192,
            "max_num_seqs": 2,
            "max_num_batched_tokens": 4096,
        },
    )
    assert config.MAX_PIXELS == 2_000_000
    assert calls["client"]["backend"] == "vllm-async-engine"
    assert calls["client"]["vllm_async_llm"] == "vendor-async-engine"
    assert calls["client"]["model_path"] == "/root/models/TeleOCR"
    assert calls["client"]["max_concurrency"] == 2
