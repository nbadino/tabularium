"""Test per inferenza remota/cloud, persistenza configurazione e endpoint di sistema."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.main import app
from app.services import inference as infmod
from app.services import cloud_manager


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

    modal_client = infmod.VllmClient(url="https://workspace--app.modal.run/v1")
    assert modal_client.is_modal
    assert not local_client.is_modal


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


def test_connection_rejects_unserved_model(monkeypatch):
    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "served-model"}]}

    monkeypatch.setattr(infmod.requests, "get", lambda *args, **kwargs: MockResponse())
    result = infmod.VllmClient(
        url="https://workspace--app.modal.run/v1", model="wrong-model"
    ).test_connection(timeout=1)
    assert result["ok"] is False
    assert result["models_available"] == ["served-model"]


def test_vast_search_uses_current_endpoint_and_payload(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"success": True, "offers": [{"id": 7, "gpu_name": "RTX 4090"}]}

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def put(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return Response()

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    result = cloud_manager.search_vast_offers("token", instance_type="on-demand")
    assert result[0]["id"] == 7
    assert captured["url"].endswith("/search/asks/")
    assert captured["json"]["type"] == "ondemand"
    assert captured["headers"]["Accept"] == "application/json"


def test_runpod_create_uses_persistent_pod_schema(monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        content = b'{"id":"pod-1"}'
        text = ""

        def json(self):
            return {"id": "pod-1"}

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            captured.update(url=url, **kwargs)
            return Response()

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    result = cloud_manager.create_runpod_pod(
        "token", gpu_type_ids=["NVIDIA RTX A5000"], volume_gb=50,
        env={"TABULARIUM_REF": "run-1"}, interruptible=True,
    )
    assert result["pod"]["id"] == "pod-1"
    assert captured["url"].endswith("/pods")
    assert captured["json"]["imageName"].startswith("runpod/")
    assert captured["json"]["ports"] == ["8888/http", "22/tcp"]
    assert captured["json"]["env"]["TABULARIUM_REF"] == "run-1"


def test_ssh_tunnel_status_recovers_persisted_job(monkeypatch):
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")
        cur = conn.execute(
            "INSERT INTO jobs(kind, provider, pid, process_group, state, command_json) "
            "VALUES('ssh_tunnel', 'ssh', 4242, 4242, 'running', ?)",
            ('{"host":"gpu.example","port":2222,"user":"root","local_port":8888,"remote_port":8888}',),
        )
        job_id = cur.lastrowid
    monkeypatch.setattr(cloud_manager, "_pid_alive", lambda pid: pid == 4242)
    monkeypatch.setattr(cloud_manager, "_ACTIVE_TUNNEL_PROC", None)
    monkeypatch.setattr(cloud_manager, "_ACTIVE_TUNNEL_INFO", {})
    monkeypatch.setattr(cloud_manager, "_ACTIVE_TUNNEL_JOB_ID", None)

    status = cloud_manager.get_tunnel_status()

    assert status.running is True
    assert status.host == "gpu.example"
    assert status.port == 2222
    assert status.pid == 4242
    with connect() as conn:
        assert conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "running"
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    cloud_manager._ACTIVE_TUNNEL_JOB_ID = None


def test_cloud_resource_cost_is_persisted_and_closed():
    init_db()
    resource_id = "test-resource-cost"
    with connect() as conn:
        conn.execute(
            "DELETE FROM jobs WHERE kind='cloud_resource' AND remote_job_id=?",
            (resource_id,),
        )
    cloud_manager.track_cloud_resource("vast", resource_id, hourly_rate=0.25, state="running")
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET started_at=datetime('now', '-2 hours') "
            "WHERE kind='cloud_resource' AND remote_job_id=?",
            (resource_id,),
        )
    estimate = cloud_manager.cloud_resource_cost("vast", resource_id)
    assert estimate is not None
    assert estimate["hourly_rate"] == 0.25
    assert 0.49 <= estimate["estimated_usd"] <= 0.51

    cloud_manager.track_cloud_resource("vast", resource_id, state="stopped")
    with connect() as conn:
        row = conn.execute(
            "SELECT state, ended_at FROM jobs WHERE kind='cloud_resource' AND remote_job_id=?",
            (resource_id,),
        ).fetchone()
        conn.execute("DELETE FROM jobs WHERE kind='cloud_resource' AND remote_job_id=?", (resource_id,))
    assert row["state"] == "stopped"
    assert row["ended_at"] is not None
