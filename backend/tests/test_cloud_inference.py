"""Test per inferenza remota/cloud, persistenza configurazione e endpoint di sistema."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import inference as infmod


def test_vllm_client_headers_and_cloud_detection():
    # Local client
    local_client = infmod.VllmClient(url="http://127.0.0.1:8888/v1", api_key="")
    assert not local_client.is_cloud
    assert local_client._headers() == {"Content-Type": "application/json"}

    # Cloud client with API key and custom headers
    cloud_client = infmod.VllmClient(
        url="https://mypod-8888.proxy.runpod.net/v1",
        api_key="secret-token-123",
        extra_headers={"ngrok-skip-browser-warning": "1"},
    )
    assert cloud_client.is_cloud
    headers = cloud_client._headers()
    assert headers["Authorization"] == "Bearer secret-token-123"
    assert headers["ngrok-skip-browser-warning"] == "1"
    assert headers["Content-Type"] == "application/json"


def test_inference_config_persistence_and_api():
    with TestClient(app) as client:
        # 1. Recupero config iniziale
        res = client.get("/api/system/inference")
        assert res.status_code == 200
        data = res.json()
        assert "url" in data
        assert "model" in data
        assert "is_cloud" in data

        # 2. Aggiornamento config (es. puntamento a Vast.ai / RunPod)
        update_payload = {
            "url": "https://custom-gpu.vast.ai:34567/v1",
            "model": "MonkeyOCRv2-B-Parsing",
            "api_key": "my-vast-key",
            "extra_headers": {"X-Custom-Header": "value"},
            "timeout": 120,
        }
        res_put = client.put("/api/system/inference", json=update_payload)
        assert res_put.status_code == 200
        put_data = res_put.json()
        assert put_data["url"] == "https://custom-gpu.vast.ai:34567/v1"
        assert put_data["model"] == "MonkeyOCRv2-B-Parsing"
        assert put_data["has_api_key"] is True
        assert put_data["is_cloud"] is True
        assert put_data["extra_headers"] == {"X-Custom-Header": "value"}
        assert put_data["timeout"] == 120

        # 3. Verifica persistenza tramite get_inference_config
        saved_cfg = infmod.get_inference_config()
        assert saved_cfg["url"] == "https://custom-gpu.vast.ai:34567/v1"
        assert saved_cfg["api_key"] == "my-vast-key"
        assert saved_cfg["model"] == "MonkeyOCRv2-B-Parsing"

        # 4. Ripristino a default locale per non inquinare altri test
        client.put("/api/system/inference", json={"url": "http://127.0.0.1:8888/v1", "api_key": "", "model": "MonkeyOCRv2"})


def test_inference_test_endpoint_mocked(monkeypatch):
    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "MonkeyOCRv2"}, {"id": "MonkeyOCRv2-B-Parsing"}]}

    import requests

    def mock_get(url, *args, **kwargs):
        if url.endswith("/models"):
            return MockResponse()
        raise ValueError(f"Unexpected url: {url}")

    monkeypatch.setattr(requests, "get", mock_get)

    with TestClient(app) as client:
        res = client.post(
            "/api/system/inference/test",
            json={
                "url": "https://fast-cloud-node.vast.ai:8000/v1",
                "model": "MonkeyOCRv2",
                "api_key": "vast-token",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["is_cloud"] is True
        assert "MonkeyOCRv2" in data["models_available"]
        assert data["latency_ms"] is not None
