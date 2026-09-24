"""Test per inferenza remota/cloud, persistenza configurazione e endpoint di sistema."""
from __future__ import annotations

import shlex

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.db import connect, init_db
from app.main import app
from app.services import inference as infmod
from app.services import cloud_manager
from app.services import process_probe
from app.services import vault


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


def test_teleocr_sampling_reaches_vllm_openai_request(monkeypatch):
    from PIL import Image

    adapter = infmod.get_adapter("teleocr")
    captured = {}

    class StreamResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def raise_for_status(self): pass
        def iter_lines(self, decode_unicode=True):
            yield 'data: {"choices":[{"delta":{"content":"<fcel>x</fcel>"}}]}'
            yield "data: [DONE]"

    def post(_url, **kwargs):
        captured.update(kwargs["json"])
        return StreamResponse()

    monkeypatch.setattr(infmod.requests, "post", post)
    client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1",
        model="StarDoc-AI/TeleOCR",
        adapter=adapter,
        max_retries=0,
    )
    output = client._chat(
        Image.new("RGB", (8, 8), "white"),
        adapter.prompt_for("table"),
        max_tokens=4096,
        sampling=client._sampling_for("table"),
        task="table",
    )
    assert output == "<fcel>x</fcel>"
    assert captured["temperature"] == 0.0
    assert captured["top_p"] == 0.01
    assert captured["top_k"] == 1
    assert captured["presence_penalty"] == 1.0
    assert captured["frequency_penalty"] == 0.005
    assert captured["extra_args"]["no_repeat_ngram_size"] == 100


def _stub_public_dns(monkeypatch):
    """Gli host fittizi del contratto non devono dipendere dal DNS esterno."""
    import socket

    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host in {"custom-gpu.vast.ai", "fast-cloud-node.vast.ai"}:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


@pytest.mark.skipif(vault.Fernet is None, reason="cryptography non installata nel virtualenv")
def test_inference_config_persistence_and_api(monkeypatch):
    _stub_public_dns(monkeypatch)
    # Il test esercita la cifratura delle credenziali persistite: la chiave deve
    # essere esplicita e usa un valore effimero, mai un segreto del repository.
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", vault.Fernet.generate_key().decode("ascii"))
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
        # Gli header possono contenere token e sono server-only nella risposta.
        assert put_data["extra_headers"] == {}
        assert put_data["timeout"] == 120

        # 3. Verifica persistenza tramite get_inference_config
        saved_cfg = infmod.get_inference_config()
        assert saved_cfg["url"] == "https://custom-gpu.vast.ai:34567/v1"
        assert saved_cfg["api_key"] == "my-vast-key"
        assert saved_cfg["model"] == "MonkeyOCRv2-B-Parsing"

        # 4. Ripristino a default locale per non inquinare altri test
        client.put("/api/system/inference", json={"url": "http://127.0.0.1:8888/v1", "api_key": "", "model": "MonkeyOCRv2"})


def test_inference_test_endpoint_mocked(monkeypatch):
    _stub_public_dns(monkeypatch)
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
    """La query va sotto `q`: al primo livello l'API risponde bad_request."""
    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("PUT", "/search/asks/"): {"success": True, "offers": [{"id": 7, "gpu_name": "RTX 4090"}]}},
        calls,
    )
    result = cloud_manager.search_vast_offers("token", instance_type="on-demand", disk_gb=40)
    assert result[0]["id"] == 7
    assert calls[0]["path"] == "/search/asks/"
    body = calls[0]["json"]
    # `select_cols: ["*"]` non passa più la validazione dei nomi colonna.
    assert set(body) == {"q"}
    assert body["q"]["type"] == "on-demand"
    assert body["q"]["order"] == [["dph_total", "asc"]]
    assert body["q"]["allocated_storage"] == 40.0
    assert body["q"]["rentable"] == {"eq": True}


def test_vast_search_maps_interruptible_to_bid(monkeypatch):
    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/search/asks/"): {"offers": []}}, calls)
    cloud_manager.search_vast_offers("token", instance_type="interruptible")
    assert calls[0]["json"]["q"]["type"] == "bid"


def test_vast_prepare_requires_pinned_ref_and_quotes_onstart(monkeypatch):
    with pytest.raises(ValueError, match="monkeyocr_ref"):
        cloud_manager.rent_vast_instance("token", 7, prepare_server=True)
    with pytest.raises(ValueError, match="tabularium_ref"):
        cloud_manager.rent_vast_instance("token", 7, prepare_server=True, monkeyocr_ref="v1.2.3")

    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/asks/7/"): {"new_contract": 12}}, calls)
    result = cloud_manager.rent_vast_instance(
        "token", 7, prepare_server=True, monkeyocr_ref="v1.2.3",
        tabularium_ref="2026-08-31",
        api_key_for_server="server-secret",
    )
    assert result["contract_id"] == 12
    body = calls[0]["json"]
    assert "--ref v1.2.3" in body["onstart"]
    assert "raw.githubusercontent.com/nbadino/tabularium/2026-08-31/" in body["onstart"]
    assert "server-secret" not in body["onstart"]
    assert "TABULARIUM_SERVER_API_KEY" in body["env"]


def test_provider_refresh_persists_rate_and_live_state(monkeypatch):
    init_db()
    resource_id = 987654
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='cloud_resource' AND remote_job_id=?", (str(resource_id),))

    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("GET", "/instances/"): {"instances": [{
            "id": resource_id, "actual_status": "running", "gpu_name": "RTX 4090",
            "num_gpus": 1, "dph_total": 0.42,
        }]}},
        calls,
    )
    items = cloud_manager.list_vast_instances("token")
    assert items[0]["is_running"] is True
    assert items[0]["cost_estimate"]["hourly_rate"] == 0.42
    with connect() as conn:
        row = conn.execute(
            "SELECT provider, state FROM jobs WHERE kind='cloud_resource' AND remote_job_id=?",
            (str(resource_id),),
        ).fetchone()
        conn.execute("DELETE FROM jobs WHERE kind='cloud_resource' AND remote_job_id=?", (str(resource_id),))
    assert row["provider"] == "vast"
    assert row["state"] == "running"


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


def test_reconcile_tunnel_reopens_tunnel_dead_after_restart(monkeypatch):
    """Riavvio dell'app con il job tunnel ancora 'running': la strada va riaperta."""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")
        conn.execute(
            "INSERT INTO jobs(kind, provider, pid, process_group, state, command_json) "
            "VALUES('ssh_tunnel', 'ssh', 4242, 4242, 'running', ?)",
            ('{"host":"gpu.example","port":2222,"user":"root","local_port":8888,'
             '"remote_port":8888,"key_path":"/tmp/chiave","owner_id":7}',),
        )
    # Nessun SSH vivo
    monkeypatch.setattr(cloud_manager, "_pid_alive", lambda pid: False)

    started = {}

    def fake_start(host, port, *, user="root", key_path=None, local_port=8888,
                   remote_port=8888, owner_id=None):
        started.update(host=host, port=port, user=user, key_path=key_path,
                       local_port=local_port, remote_port=remote_port, owner_id=owner_id)
        return cloud_manager.TunnelStatus(running=True, host=host, port=port)

    monkeypatch.setattr(cloud_manager, "start_ssh_tunnel", fake_start)
    cloud_manager.reconcile_tunnel()

    assert started == {
        "host": "gpu.example", "port": 2222, "user": "root",
        "key_path": "/tmp/chiave", "local_port": 8888, "remote_port": 8888, "owner_id": 7,
    }
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")


def test_reconcile_tunnel_skips_when_ssh_survived(monkeypatch):
    """Se l'SSH è vivo il riavvio non deve ucciderlo e ricrearlo."""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")
        conn.execute(
            "INSERT INTO jobs(kind, provider, pid, process_group, state, command_json) "
            "VALUES('ssh_tunnel', 'ssh', 4242, 4242, 'running', ?)",
            ('{"host":"gpu.example","port":2222,"user":"root","local_port":8888,'
             '"remote_port":8888}',),
        )
    monkeypatch.setattr(cloud_manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(cloud_manager, "_is_ssh_process", lambda pid: True)

    def unexpected_start(*args, **kwargs):
        raise AssertionError("start_ssh_tunnel non dovrebbe essere chiamato")

    monkeypatch.setattr(cloud_manager, "start_ssh_tunnel", unexpected_start)
    cloud_manager.reconcile_tunnel()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")


def test_reconcile_tunnel_does_not_resume_explicit_stop(monkeypatch):
    """Un tunnel fermato dall'utente ('stopped') non deve ripartire al riavvio."""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")
        conn.execute(
            "INSERT INTO jobs(kind, provider, pid, process_group, state, command_json) "
            "VALUES('ssh_tunnel', 'ssh', 4242, 4242, 'stopped', ?)",
            ('{"host":"gpu.example","port":2222,"user":"root"}',),
        )

    def unexpected_start(*args, **kwargs):
        raise AssertionError("start_ssh_tunnel non dovrebbe essere chiamato")

    monkeypatch.setattr(cloud_manager, "start_ssh_tunnel", unexpected_start)
    cloud_manager.reconcile_tunnel()
    with connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='ssh_tunnel'")


def test_tunnel_without_key_uses_dedicated_app_key(monkeypatch, tmp_path):
    """Il tunnel aperto dalla UI non manda key_path: deve andare la chiave
    dedicata dell'app, non sperare nelle chiavi personali dell'utente."""
    chiave = tmp_path / "tabularium_vast_ed25519"
    chiave.write_text("chiave-finta")
    monkeypatch.setattr(cloud_manager, "ssh_key_path", lambda: chiave)

    cmd_catturato = {}

    class Proc:
        def poll(self):
            return None

        pid = 4242

    def fake_popen(cmd, **kwargs):
        cmd_catturato.update(cmd=cmd)
        return Proc()

    monkeypatch.setattr(cloud_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda s: None)
    monkeypatch.setattr(cloud_manager, "_persist_tunnel_job", lambda info, pid: 1)

    cloud_manager.start_ssh_tunnel("gpu.example", 2222)

    assert "-i" in cmd_catturato["cmd"]
    assert cmd_catturato["cmd"][cmd_catturato["cmd"].index("-i") + 1] == str(chiave)


def test_tunnel_can_choose_a_free_local_port(monkeypatch, tmp_path):
    chiave = tmp_path / "tabularium_vast_ed25519"
    chiave.write_text("chiave-finta")
    monkeypatch.setattr(cloud_manager, "ssh_key_path", lambda: chiave)
    captured = {}

    class Proc:
        pid = 4243

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return Proc()

    monkeypatch.setattr(cloud_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cloud_manager, "_persist_tunnel_job", lambda info, pid: 2)

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, _address):
            return None

        def getsockname(self):
            return ("127.0.0.1", 43123)

    monkeypatch.setattr(cloud_manager.socket, "socket", lambda *_args: FakeSocket())

    status = cloud_manager.start_ssh_tunnel("gpu.example", 2222, local_port=0)
    assert status.local_port == 43123
    forward = captured["cmd"][captured["cmd"].index("-L") + 1]
    assert forward == f"{status.local_port}:127.0.0.1:8888"


def test_tunnel_can_forward_the_model_native_sidecar(monkeypatch, tmp_path):
    chiave = tmp_path / "tabularium_vast_ed25519"
    chiave.write_text("chiave-finta")
    monkeypatch.setattr(cloud_manager, "ssh_key_path", lambda: chiave)
    captured = {}

    class Proc:
        pid = 4244
        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return Proc()

    class FakeSocket:
        next_port = 43000
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def bind(self, _address): return None
        def getsockname(self):
            port = FakeSocket.next_port
            FakeSocket.next_port += 1
            return ("127.0.0.1", port)

    monkeypatch.setattr(cloud_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cloud_manager, "_persist_tunnel_job", lambda info, pid: 3)
    monkeypatch.setattr(cloud_manager.socket, "socket", lambda *_args: FakeSocket())
    status = cloud_manager.start_ssh_tunnel(
        "gpu.example", 2222, local_port=0, native_remote_port=8889,
    )
    forwards = [captured["cmd"][i + 1] for i, token in enumerate(captured["cmd"][:-1]) if token == "-L"]
    assert status.native_local_port == 43001
    assert forwards == [
        "43000:127.0.0.1:8888",
        "43001:127.0.0.1:8889",
    ]


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


class _VastResponse:
    """Risposta minima compatibile con il client httpx usato da cloud_manager."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"{}"
        self.text = ""

    def json(self):
        return self._payload


def _fake_vast_client(monkeypatch, routes: dict, calls: list):
    """Instrada (metodo, path) → payload registrando ogni chiamata."""

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, **kwargs):
            path = url.replace(cloud_manager.VAST_API_V1, "").replace(cloud_manager.VAST_API_BASE, "")
            calls.append({
                "method": method, "path": path, "url": url,
                "json": kwargs.get("json"), "params": kwargs.get("params"),
            })
            if (method, path) not in routes:
                raise AssertionError(f"rotta non attesa: {method} {path}")
            return _VastResponse(routes[(method, path)])

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)


def test_vast_account_preflight_reports_balance(monkeypatch):
    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("GET", "/users/current/"): {"id": 42, "email": "a@b.c", "balance": "3.5"}},
        calls,
    )
    account = cloud_manager.vast_account("token")
    assert account == {"id": 42, "email": "a@b.c", "balance": 3.5, "balance_ok": True}
    assert calls[0]["path"] == "/users/current/"


def test_vast_account_rejects_bad_key(monkeypatch):
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def request(self, method, url, **kwargs):
            return _VastResponse({"success": False}, status_code=401)

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    with pytest.raises(RuntimeError, match="401"):
        cloud_manager.vast_account("wrong")


def test_ensure_vast_ssh_key_registers_once(monkeypatch):
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1 tabularium-vast"
    monkeypatch.setattr(
        cloud_manager, "local_ssh_key",
        lambda create=False: {
            "exists": True, "key_path": "/tmp/k", "public_key": public_key, "fingerprint": "SHA256:xyz",
        },
    )

    # Account senza chiavi: viene registrata.
    calls: list = []
    _fake_vast_client(monkeypatch, {("GET", "/ssh/"): [], ("POST", "/ssh/"): {"success": True}}, calls)
    result = cloud_manager.ensure_vast_ssh_key("token")
    assert result["already_registered"] is False
    assert calls[-1]["method"] == "POST" and calls[-1]["path"] == "/ssh/"
    assert calls[-1]["json"] == {"ssh_key": public_key}

    # Stessa chiave già presente (commento diverso): nessun duplicato.
    calls.clear()
    _fake_vast_client(
        monkeypatch,
        {("GET", "/ssh/"): [{"id": 1, "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1 altro-commento"}]},
        calls,
    )
    result = cloud_manager.ensure_vast_ssh_key("token")
    assert result["already_registered"] is True
    assert [c["method"] for c in calls] == ["GET"]


def test_get_vast_instance_flags_ssh_readiness(monkeypatch):
    init_db()
    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("GET", "/instances/99/"): {"instances": {
            "id": 99, "actual_status": "running", "gpu_name": "RTX 4090",
            "ssh_host": "ssh5.vast.ai", "ssh_port": 34567, "dph_total": 0.21,
        }}},
        calls,
    )
    item = cloud_manager.get_vast_instance("token", 99)
    assert item["ssh_ready"] is True
    assert item["ssh_host"] == "ssh5.vast.ai"

    calls.clear()
    _fake_vast_client(
        monkeypatch,
        {("GET", "/instances/99/"): {"instances": {"id": 99, "actual_status": "loading"}}},
        calls,
    )
    assert cloud_manager.get_vast_instance("token", 99)["ssh_ready"] is False


def test_vast_instance_uses_official_ports_fallback(monkeypatch):
    """La v1 può omettere ssh_host/ssh_port e usare la forma consumata dalla
    CLI ufficiale: public_ipaddr + ports['22/tcp'][0].HostPort."""
    init_db()
    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("GET", "/instances/99/"): {"instances": {
            "id": 99,
            "actual_status": "running",
            "public_ipaddr": "203.0.113.42",
            "ports": {"22/tcp": [{"HostPort": "34567"}]},
        }}},
        calls,
    )
    item = cloud_manager.get_vast_instance("token", 99)
    assert item["ssh_ready"] is True
    assert item["ssh_host"] == "203.0.113.42"
    assert item["ssh_port"] == 34567


def test_vast_instance_rejects_invalid_ports_fallback():
    host, port = cloud_manager._vast_ssh_endpoint({
        "public_ipaddr": "203.0.113.42",
        "ports": {"22/tcp": [{"HostPort": "not-a-port"}]},
    })
    assert host == "203.0.113.42"
    assert port is None


def test_vast_endpoint_prefers_the_direct_address(monkeypatch):
    """Con l'IP pubblico si passa dalla macchina, non dal forwarder.

    È l'ordine di `vastai ssh-url --direct`: i proxy ssh*.vast.ai accettano solo
    le chiavi propagate dall'account e possono rifiutare la nostra su
    un'istanza già accesa, mentre la porta diretta risponde.
    """
    host, port = cloud_manager._vast_ssh_endpoint({
        "ssh_host": "ssh7.vast.ai",
        "ssh_port": 34880,
        "public_ipaddr": "173.239.92.155",
        "ports": {"22/tcp": [{"HostIp": "0.0.0.0", "HostPort": "41934"}]},
    })
    assert (host, port) == ("173.239.92.155", 41934)


def test_vast_endpoint_falls_back_to_the_proxy_without_public_ip(monkeypatch):
    """Caso reale (istanza 50434880): Vast.ai non pubblica `public_ipaddr`.

    La 22 è mappata su una porta diretta, ma senza indirizzo non è compilabile
    un endpoint: si resta sul proxy — e l'IP lo dovrà indicare l'utente, che è
    l'unico posto in cui è scritto (riga «Direct SSH Connect» della console).
    """
    inst = {
        "id": 50434880,
        "actual_status": "running",
        "ssh_host": "ssh7.vast.ai",
        "ssh_port": 34880,
        "public_ipaddr": None,
        "ports": {"22/tcp": [{"HostIp": "0.0.0.0", "HostPort": "41934"}]},
    }
    assert cloud_manager._vast_ssh_endpoint(inst) == ("ssh7.vast.ai", 34880)
    endpoints = cloud_manager.vast_ssh_endpoints(inst)
    # La porta diretta resta esposta: serve a precompilare l'override della UI,
    # dove all'utente manca solo l'host.
    assert endpoints["direct"] == (None, 41934)
    assert endpoints["proxy"] == ("ssh7.vast.ai", 34880)

    init_db()
    calls: list = []
    _fake_vast_client(monkeypatch, {("GET", "/instances/50434880/"): {"instances": inst}}, calls)
    item = cloud_manager.get_vast_instance("token", 50434880)
    assert item["ssh_via"] == "proxy"
    assert item["ssh_direct_port"] == 41934
    assert item["ssh_direct_host"] is None


def test_vast_endpoint_never_mixes_the_two_routes():
    """Host del proxy + porta diretta = un endpoint che non esiste."""
    host, port = cloud_manager._vast_ssh_endpoint({
        "ssh_host": "ssh7.vast.ai",
        "ports": {"22/tcp": [{"HostPort": "41934"}]},
    })
    assert (host, port) == ("ssh7.vast.ai", None)


def test_ssh_preflight_names_a_refused_key(monkeypatch, tmp_path):
    """Il caso che costava un noleggio per essere diagnosticato."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)

    class Result:
        returncode = 255
        stdout = ""
        stderr = "root@ssh7.vast.ai: Permission denied (publickey).\n"

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    access = cloud_manager.check_ssh_access("ssh7.vast.ai", 34880)
    assert access["ok"] is False
    assert access["reason"] == "auth"
    assert "Direct SSH Connect" in access["message"]


def test_ssh_preflight_separates_the_host_key_from_the_credentials(monkeypatch, tmp_path):
    """Host key cambiata e chiave rifiutata chiedono due azioni diverse."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)
    attempts = []

    class Result:
        returncode = 255
        stdout = ""
        stderr = "Host key verification failed.\n"

    def fake_run(*_a, **_k):
        attempts.append(1)
        return Result()

    monkeypatch.setattr(cloud_manager.subprocess, "run", fake_run)
    access = cloud_manager.check_ssh_access("ssh7.vast.ai", 34880, attempts=3)
    assert access["reason"] == "hostkey"
    # Una host key sbagliata non cambia riprovando: un solo tentativo.
    assert len(attempts) == 1


def test_provision_stops_on_a_refused_key_without_sending_the_script(monkeypatch, tmp_path):
    """La chiave rifiutata non deve travestirsi da errore di provisioning."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)
    sent = []

    class Result:
        returncode = 255
        stdout = ""
        stderr = "Permission denied (publickey).\n"

    def fake_run(cmd, stdin=None, **_kwargs):
        if stdin is not None:
            sent.append(cmd)
        return Result()

    monkeypatch.setattr(cloud_manager.subprocess, "run", fake_run)
    with pytest.raises(cloud_manager.VastSshError) as err:
        cloud_manager.provision_vast_server("ssh7.vast.ai", 34880, monkeyocr_ref="abc123")
    assert err.value.reason == "auth"
    assert sent == []


def test_provision_ssh_refusal_answers_409_not_502(monkeypatch, tmp_path):
    """In UI un 502 generico era indistinguibile da un setup andato male."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)

    class Result:
        returncode = 255
        stdout = ""
        stderr = "Permission denied (publickey).\n"

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    with TestClient(app) as client:
        res = client.post(
            "/api/system/cloud/vast/provision",
            json={"host": "ssh7.vast.ai", "port": 34880, "monkeyocr_ref": "abc123"},
        )
    assert res.status_code == 409
    assert "chiave SSH" in res.json()["detail"]


def test_ssh_check_route_reports_the_cause(monkeypatch, tmp_path):
    """Il preflight è interrogabile da solo: l'override si prova senza noleggi."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    with TestClient(app) as client:
        res = client.post(
            "/api/system/cloud/vast/ssh-check",
            json={"host": "173.239.92.155", "port": 41934},
        )
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_pin_ssh_host_key_writes_known_hosts(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", known_hosts)
    executed: list = []

    class Result:
        returncode = 0
        stdout = "[ssh5.vast.ai]:34567 ssh-ed25519 AAAAC3Nza\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        executed.append(cmd)
        return Result()

    monkeypatch.setattr(cloud_manager, "_run", fake_run)
    result = cloud_manager.pin_ssh_host_key("ssh5.vast.ai", 34567)
    assert result["key_types"] == ["ssh-ed25519"]
    assert "[ssh5.vast.ai]:34567 ssh-ed25519" in known_hosts.read_text()
    assert ["ssh-keyscan", "-T", "10", "-p", "34567", "ssh5.vast.ai"] in executed
    # L'host arriva dall'API del provider ma resta input esterno: niente shell.
    with pytest.raises(ValueError):
        cloud_manager.pin_ssh_host_key("ssh5.vast.ai; rm -rf /", 34567)


def test_pin_ssh_host_key_fails_when_ssh_not_up(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "connection refused"

    monkeypatch.setattr(cloud_manager, "_run", lambda cmd, **kwargs: Result())
    with pytest.raises(RuntimeError, match="Host key non ottenibile"):
        cloud_manager.pin_ssh_host_key("ssh5.vast.ai", 34567)


def test_provision_sends_local_script_over_ssh(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    captured: dict = {}

    class Result:
        returncode = 0
        stdout = "tabularium-provision-started\n"
        stderr = ""

    def fake_run(cmd, stdin=None, **kwargs):
        captured["cmd"] = cmd
        captured["script"] = stdin.read().decode("utf-8") if stdin is not None else ""
        return Result()

    monkeypatch.setattr(cloud_manager.subprocess, "run", fake_run)
    result = cloud_manager.provision_vast_server(
        "ssh5.vast.ai", 34567, monkeyocr_ref="e4491a26", server_api_key="server-secret",
    )
    assert result["monkeyocr_ref"] == "e4491a26"
    # Lo script viaggia su stdin dal checkout locale: nessun download da GitHub.
    assert "Tabularium — Cloud GPU Inference Setup" in captured["script"]
    remote = captured["cmd"][-1]
    assert "raw.githubusercontent.com" not in remote
    # Il trasferimento deve concludersi prima dell'avvio: solo il lancio è in
    # background, altrimenti la sessione SSH chiude su uno script troncato.
    assert "cat > /root/tabularium_setup_cloud_vllm.sh;" in remote
    assert remote.index("cat >") < remote.index("nohup")
    assert "if [ ! -s /root/tabularium_setup_cloud_vllm.sh ]" in remote
    assert "--ref e4491a26" in remote
    assert "TABULARIUM_SERVER_API_KEY=server-secret" in remote
    # La verifica della host key resta attiva anche nel provisioning.
    assert "StrictHostKeyChecking=yes" in captured["cmd"]
    # Il percorso e' citato per il parser di ssh, che altrimenti leggerebbe il
    # valore come una lista di file separati da spazi: si verifica quindi che
    # dopo la divisione resti quel singolo file, non la stringa esatta.
    khs = [a for a in captured["cmd"] if a.startswith("UserKnownHostsFile=")]
    assert len(khs) == 1
    assert shlex.split(khs[0].split("=", 1)[1]) == [str(tmp_path / "known_hosts")]


def test_provision_retries_transient_ssh_authentication(monkeypatch, tmp_path):
    """Dopo `attach ssh` la chiave impiega qualche secondo a propagarsi.

    Il preflight assorbe quell'attesa: i primi rifiuti non devono diventare un
    errore mostrato all'utente.
    """
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    attempts = []

    class Result:
        def __init__(self, returncode):
            self.returncode = returncode
            self.stderr = "Permission denied (publickey)." if returncode else ""
            self.stdout = "" if returncode else "tabularium-provision-started\n"

    def fake_run(cmd, stdin=None, **kwargs):
        attempts.append(cmd[-1])
        if stdin is not None:
            stdin.read()
        return Result(255 if len(attempts) < 3 else 0)

    monkeypatch.setattr(cloud_manager.subprocess, "run", fake_run)
    monkeypatch.setattr(cloud_manager.time, "sleep", lambda _seconds: None)
    result = cloud_manager.provision_vast_server(
        "ssh5.vast.ai", 34567, monkeyocr_ref="abc123",
    )
    assert result["ok"] is True
    # Due rifiuti assorbiti dal preflight, poi la connessione passa: da lì in
    # poi si va avanti senza altri tentativi di autenticazione.
    assert attempts[:3] == ["true", "true", "true"]


def test_provision_rejects_injection_in_ref_and_host(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: pytest.fail("ssh non deve partire"))
    with pytest.raises(ValueError, match="monkeyocr_ref"):
        cloud_manager.provision_vast_server("ssh5.vast.ai", 34567, monkeyocr_ref="abc; rm -rf /")
    with pytest.raises(ValueError, match="Host"):
        cloud_manager.provision_vast_server("ssh5.vast.ai && curl evil", 34567, monkeyocr_ref="abc")
    with pytest.raises(ValueError, match="Utente"):
        cloud_manager.provision_vast_server("ssh5.vast.ai", 34567, user="root; id", monkeyocr_ref="abc")


def test_provision_log_reports_readiness(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stdout = ">> download pesi\nINFO: Application startup complete.\n"
        stderr = ""

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["ready"] is True
    assert out["lines"][0] == ">> download pesi"


def test_resolve_monkeyocr_ref_falls_back_to_head(monkeypatch):
    class Response:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, url, **kwargs):
            if url.endswith("/tags"):
                return Response([])
            return Response({"sha": "e4491a261b420090b58cee293a975141d7f1e8d4"})

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    ref = cloud_manager.resolve_monkeyocr_ref()
    assert ref["kind"] == "commit"
    assert ref["ref"].startswith("e4491a26")


def test_vast_account_reads_credit_field(monkeypatch):
    """Il saldo sta in `credit`: leggere solo `balance` mostrava $0 a credito pieno."""
    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("GET", "/users/current/"): {"id": 1, "email": "a@b.c", "credit": 9.87, "balance": 0}},
        calls,
    )
    account = cloud_manager.vast_account("token")
    assert account["balance"] == 9.87
    assert account["balance_ok"] is True


def test_list_vast_instances_uses_v1_endpoint(monkeypatch):
    """La collection v0 risponde 410 deprecated_endpoint da settembre 2026."""
    init_db()
    calls: list = []
    _fake_vast_client(
        monkeypatch,
        {("GET", "/instances/"): {"instances": [{
            "id": 7, "actual_status": "running", "gpu_name": "RTX 4090", "dph_total": 0.3,
            "ssh_host": "ssh5.vast.ai", "ssh_port": 34567,
        }]}},
        calls,
    )
    items = cloud_manager.list_vast_instances("token")
    assert items[0]["id"] == 7
    assert calls[0]["url"].startswith(cloud_manager.VAST_API_V1)
    assert calls[0]["params"] == {"limit": 25}


def test_get_vast_instance_falls_back_to_v1_list(monkeypatch):
    """Se anche il dettaglio v0 sparisce, l'istanza si ritrova nella lista v1."""
    init_db()

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def request(self, method, url, **kwargs):
            if url.startswith(cloud_manager.VAST_API_BASE):
                return _VastResponse({}, status_code=410)
            return _VastResponse({"instances": [{
                "id": 7, "actual_status": "running", "ssh_host": "ssh5.vast.ai",
                "ssh_port": 34567, "dph_total": 0.3,
            }]})

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    item = cloud_manager.get_vast_instance("token", 7)
    assert item["ssh_ready"] is True
    assert item["ssh_host"] == "ssh5.vast.ai"


def test_deprecated_endpoint_raises_dedicated_error(monkeypatch):
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def request(self, method, url, **kwargs):
            return _VastResponse({}, status_code=410)

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    with pytest.raises(cloud_manager.VastDeprecatedEndpoint):
        cloud_manager.list_vast_instances("token")


def test_vast_search_translates_hardware_filters(monkeypatch):
    """VRAM in GB verso `gpu_ram` in MB, più disco, banda, CUDA e verificate."""
    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/search/asks/"): {"offers": []}}, calls)
    cloud_manager.search_vast_offers(
        "token", gpu_name="RTX 4090", max_dph=0.5, disk_gb=60,
        min_gpu_ram_gb=24, min_cpu_ram_gb=32, min_compute_capability=7.5,
        min_inet_down=200, min_cuda=12.4, verified_only=True,
    )
    q = calls[0]["json"]["q"]
    assert q["gpu_ram"] == {"gte": 24 * 1024.0}
    assert q["cpu_ram"] == {"gte": 32_000.0}
    assert q["compute_cap"] == {"gte": 750}
    assert q["disk_space"] == {"gte": 60.0}
    assert q["allocated_storage"] == 60.0
    assert q["inet_down"] == {"gte": 200.0}
    assert q["cuda_max_good"] == {"gte": 12.4}
    assert q["verified"] == {"eq": True}
    assert q["dph_total"] == {"lte": 0.5}
    assert q["gpu_name"] == {"eq": "RTX 4090"}


def test_vast_search_omits_unset_filters(monkeypatch):
    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/search/asks/"): {"offers": []}}, calls)
    cloud_manager.search_vast_offers("token")
    q = calls[0]["json"]["q"]
    for absent in ("gpu_ram", "cpu_ram", "compute_cap", "inet_down", "cuda_max_good", "verified", "gpu_name", "dph_total"):
        assert absent not in q, absent


def test_vast_search_applies_per_architecture_cuda_floor(monkeypatch):
    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/search/asks/"): {"offers": [
        {"id": 1, "compute_cap": 1200, "cuda_max_good": 12.8},
        {"id": 2, "compute_cap": 1200, "cuda_max_good": 12.9},
        {"id": 3, "compute_cap": 750, "cuda_max_good": 12.8},
        {"id": 4, "compute_cap": 700, "cuda_max_good": 13.0},
    ]}}, calls)
    result = cloud_manager.search_vast_offers(
        "token", min_compute_capability=7.5, min_cuda=12.8,
    )
    assert [item["id"] for item in result] == [2, 3]


def test_provision_rejects_a_truncated_transfer(monkeypatch, tmp_path):
    """Script arrivato vuoto: meglio un errore che un log muto per ore."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        def __init__(self, returncode, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, stdin=None, **kwargs):
        if stdin is not None:
            stdin.read()
        # Il preflight passa (l'accesso c'è): a fallire è la consegna.
        if cmd[-1] == "true":
            return Result(0)
        return Result(1, "tabularium-provision-empty\n")

    monkeypatch.setattr(cloud_manager.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="vuoto"):
        cloud_manager.provision_vast_server("ssh5.vast.ai", 34567, monkeyocr_ref="abc")


def test_provision_log_phase_survives_a_flooded_tail(monkeypatch, tmp_path):
    """vLLM stampa centinaia di righe: la fase non deve regredire per questo."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            cloud_manager._ALIVE_MARKER,
            cloud_manager._PHASE_SECTION,
            ">> Installazione dipendenze di sistema...",
            ">> Clonazione repository MonkeyOCRv2 in /root/MonkeyOCRv2...",
            ">> Installazione dipendenze Python (vLLM, PyTorch, Transformers)...",
            ">> Download pesi modello zenosai/MonkeyOCRv2-B-Parsing...",
            ">> [Tabularium Cloud Server] Avvio vLLM su 0.0.0.0:8888...",
            cloud_manager._TAIL_SECTION,
            "(EngineCore) INFO kv_cache_utils.py:1869 GPU KV cache size: 91,632 tokens",
            "(EngineCore) INFO autotuner.py:829 Autotuning process starts ...",
        ])

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["phase"] == "serving"
    assert out["ready"] is False
    # Il pannello mostra la coda vera, non i marcatori usati per la fase.
    assert out["lines"][0].startswith("(EngineCore)")
    assert cloud_manager._PHASE_SECTION not in out["lines"]


def test_provision_log_names_the_model_being_prepared(monkeypatch, tmp_path):
    """Il log è uno per istanza: dice di quale modello è, o la UI attribuisce
    a un modello lo stato (e il fallimento) di un altro."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            cloud_manager._DEAD_MARKER,
            cloud_manager._PHASE_SECTION,
            ">> Ricetta ufficiale: qwen3-vl-8b · vLLM 0.11.0 · vllm",
            ">> [Tabularium Cloud Server] Avvio vLLM su 0.0.0.0:8888...",
            cloud_manager._TAIL_SECTION,
            "RuntimeError: Engine core initialization failed.",
        ])

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["adapter_id"] == "qwen3-vl-8b"
    assert out["failed"] is True


def test_provision_log_reports_ready_from_the_whole_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            cloud_manager._ALIVE_MARKER,
            cloud_manager._PHASE_SECTION,
            ">> [Tabularium Cloud Server] Avvio vLLM su 0.0.0.0:8888...",
            "INFO:     Application startup complete.",
            cloud_manager._TAIL_SECTION,
            "INFO 09-02 routine log line",
        ])

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["ready"] is True and out["phase"] == "ready"


def test_provision_log_marks_an_unprepared_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stderr = ""
        stdout = "tabularium-log-missing\n"

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["present"] is False and out["phase"] == "absent"


def test_vast_rent_defaults_to_a_cuda_image_recent_enough(monkeypatch):
    """sm_120 richiede CUDA >= 12.9: un default più vecchio rompe le GPU nuove."""
    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/asks/7/"): {"new_contract": 12}}, calls)
    cloud_manager.rent_vast_instance("token", 7)
    image = calls[0]["json"]["image"]
    version = image.rsplit("cuda-", 1)[1].split("-")[0]
    major, minor = (int(part) for part in version.split(".")[:2])
    assert (major, minor) >= (12, 9), image


def test_provision_log_calls_a_dead_process_failed(monkeypatch, tmp_path):
    """`serve.py` muore con un traceback, non con le diagnostiche "!!" dello
    script: senza la sonda di liveness la UI resterebbe su "in preparazione"."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            cloud_manager._DEAD_MARKER,
            cloud_manager._PHASE_SECTION,
            ">> [Tabularium Cloud Server] Avvio vLLM su 0.0.0.0:8888...",
            cloud_manager._TAIL_SECTION,
            "(APIServer pid=4148) RuntimeError: Engine core initialization failed.",
        ])

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["alive"] is False
    assert out["failed"] is True
    assert out["phase"] == "failed"
    assert "Engine core initialization failed" in out["error"]


def test_provision_log_keeps_a_live_setup_in_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")

    class Result:
        returncode = 0
        stderr = ""
        stdout = "\n".join([
            cloud_manager._ALIVE_MARKER,
            cloud_manager._PHASE_SECTION,
            ">> Download pesi modello zenosai/MonkeyOCRv2-B-Parsing...",
            cloud_manager._TAIL_SECTION,
            "Downloading model.safetensors: 42%",
        ])

    monkeypatch.setattr(cloud_manager.subprocess, "run", lambda *a, **k: Result())
    out = cloud_manager.provision_log("ssh5.vast.ai", 34567)
    assert out["failed"] is False and out["phase"] == "weights"


def test_liveness_probe_cannot_match_itself(monkeypatch, tmp_path):
    """`pgrep -f` confronta le righe di comando: senza le parentesi quadre la
    sonda troverebbe sé stessa e ogni processo risulterebbe vivo."""
    monkeypatch.setattr(cloud_manager.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    captured: dict = {}

    class Result:
        returncode = 0
        stdout = cloud_manager._DEAD_MARKER
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr(cloud_manager.subprocess, "run", fake_run)
    cloud_manager.provision_log("ssh5.vast.ai", 34567)
    probe = captured["cmd"][-1]
    assert "[t]abularium_setup_cloud_vllm.sh" in probe
    assert "[s]erve[.]py" in probe


def test_vast_image_follows_the_offer_cuda():
    """`cuda_max_good` è noto prima del noleggio: l'immagine si sceglie da lì."""
    assert cloud_manager.vast_image_for(13.0).endswith("cuda-13.0.3-auto")
    assert cloud_manager.vast_image_for(12.9).endswith("cuda-12.9.2-auto")
    # Senza informazione si resta sul minimo che fa funzionare le GPU nuove.
    assert cloud_manager.vast_image_for(None).endswith("cuda-12.9.2-auto")
    assert cloud_manager.vast_image_for("non-numerico").endswith("cuda-12.9.2-auto")
    # Host che non regge il minimo: si prende la sua massima, il preflight dello
    # script dirà che quella GPU non è servibile.
    assert cloud_manager.vast_image_for(12.4).endswith("cuda-12.4.1-auto")


def test_vast_rent_picks_the_image_from_the_offer(monkeypatch):
    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/asks/7/"): {"new_contract": 12}}, calls)
    cloud_manager.rent_vast_instance("token", 7, cuda_max_good=13.0)
    assert calls[0]["json"]["image"].endswith("cuda-13.0.3-auto")

    calls.clear()
    _fake_vast_client(monkeypatch, {("PUT", "/asks/7/"): {"new_contract": 12}}, calls)
    cloud_manager.rent_vast_instance("token", 7, image="utente/immagine:custom")
    assert calls[0]["json"]["image"] == "utente/immagine:custom"


def test_is_ssh_process_reads_a_nul_separated_cmdline(monkeypatch, tmp_path):
    """`/proc/<pid>/cmdline` separa con NUL: sostituire la stringa letterale
    "\\x00" non riconosceva nessun processo e bloccava lo stop del tunnel."""
    proc_dir = tmp_path / "12345"
    proc_dir.mkdir()
    (proc_dir / "cmdline").write_bytes(b"ssh\x00-N\x00-L\x008888:127.0.0.1:8888\x00root@ssh3.vast.ai\x00")

    real_path = process_probe.Path

    class FakePath(type(real_path())):
        def __new__(cls, value):
            text = str(value)
            if text.startswith("/proc/"):
                return real_path(str(tmp_path / text.split("/proc/")[1]))
            return real_path(text)

    monkeypatch.setattr(process_probe.sys, "platform", "linux")
    monkeypatch.setattr(process_probe, "Path", FakePath)
    assert cloud_manager._is_ssh_process(12345) is True


def test_pid_alive_rejects_zombie_process(monkeypatch, tmp_path):
    proc_dir = tmp_path / "33338"
    proc_dir.mkdir()
    (proc_dir / "stat").write_text("33338 (ssh) Z 1 2 3\n", encoding="utf-8")
    real_path = process_probe.Path

    class FakePath(type(real_path())):
        def __new__(cls, value):
            text = str(value)
            if text.startswith("/proc/"):
                return real_path(str(tmp_path / text.split("/proc/")[1]))
            return real_path(text)

    monkeypatch.setattr(process_probe.sys, "platform", "linux")
    monkeypatch.setattr(process_probe, "Path", FakePath)
    monkeypatch.setattr(cloud_manager.os, "kill", lambda _pid, _sig: None)
    assert cloud_manager._pid_alive(33338) is False


def test_macos_falls_back_to_ps_instead_of_proc(monkeypatch):
    """Su macOS `/proc` non esiste: senza il fallback `ps`, `process_cmdline`
    tornava sempre None e ogni processo risultava non attribuibile — il tunnel
    diventava impossibile da fermare e quindi da riaprire."""
    calls: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "ssh -N -L 8888:127.0.0.1:8888 root@ssh3.vast.ai\n"

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return Done()

    monkeypatch.setattr(process_probe.sys, "platform", "darwin")
    monkeypatch.setattr(process_probe.subprocess, "run", fake_run)

    assert cloud_manager._is_ssh_process(12345) is True
    # `-ww` evita il troncamento alla larghezza del terminale: senza, le righe
    # di comando lunghe arriverebbero tagliate proprio dove sta l'attribuzione.
    assert "-ww" in calls[0]


def test_macos_zombie_is_detected_through_ps(monkeypatch):
    """`ps` riporta lo stato con flag posizionali (`Z`, `S+`): conta la prima
    lettera, altrimenti uno zombie passa per vivo e il job resta appeso."""

    class Done:
        returncode = 0
        stdout = "Z+\n"

    monkeypatch.setattr(process_probe.sys, "platform", "darwin")
    monkeypatch.setattr(process_probe.subprocess, "run", lambda *_a, **_k: Done())
    monkeypatch.setattr(cloud_manager.os, "kill", lambda _pid, _sig: None)

    assert process_probe.is_zombie(33338) is True
    assert cloud_manager._pid_alive(33338) is False


def test_serve_recipes_match_the_verified_modal_templates():
    """Le template Modal sono la fonte già verificata contro i README ufficiali:
    se le due liste divergono, un modello verrebbe servito con i flag sbagliati
    su un percorso e giusti sull'altro — e la differenza si vedrebbe solo nella
    qualità dell'output."""
    from pathlib import Path

    from app.services import serve_recipes

    templates = {
        "mineru2.5": "modal_mineru.py",
        "dots-ocr": "modal_dots_ocr.py",
        "glm-ocr": "modal_glm_ocr.py",
        "deepseek-ocr": "modal_deepseek_ocr.py",
        "paddleocr-vl": "modal_paddleocr_vl.py",
        "qwen3-vl-8b": "modal_qwen3_vl.py",
        "teleocr": "modal_teleocr.py",
    }
    root = Path(__file__).resolve().parents[2] / "scripts" / "cloud"
    for adapter_id, filename in templates.items():
        source = (root / filename).read_text(encoding="utf-8")
        recipe = serve_recipes.recipe_for(adapter_id)
        assert recipe.hf_repo in source, adapter_id
        assert f'"{recipe.served_model_name}"' in source, adapter_id
        assert f'VLLM_VERSION", "{recipe.vllm_version}"' in source, adapter_id
        # Ogni flag della ricetta compare nell'argv della template.
        for token in recipe.serve_args:
            if token.startswith("--"):
                assert f'"{token}"' in source, f"{adapter_id}: {token} assente dalla template"


def test_provision_recipe_carries_the_official_flags():
    from app.services import cloud_manager as cm

    recipe = cm.build_provision_recipe("deepseek-ocr")
    assert recipe["vllm_version"] == "0.12.0"
    assert "--logits-processors" in recipe["argv"]
    assert "--no-enable-prefix-caching" in recipe["argv"]
    assert recipe["argv"][:3] == ["-m", "vllm.entrypoints.cli.main", "serve"]
    assert recipe["served_model_name"] == "deepseek-ocr-2"
    assert recipe["min_free_disk_gb"] == 27
    assert recipe["min_free_vram_gb"] == 12


def test_existing_shared_vllm_environment_uses_model_only_disk_headroom():
    from app.services import cloud_manager as cm

    recipe = cm.build_provision_recipe("dots-ocr")
    assert recipe["min_free_disk_gb"] == 28
    assert recipe["min_free_disk_gb_reuse"] == 12
    assert recipe["min_free_disk_gb_cached"] == 4
    assert recipe["argv"][recipe["argv"].index("--gpu-memory-utilization") + 1] == "0.9"


def test_vast_configuration_signature_changes_when_serving_settings_change(tmp_path, monkeypatch):
    from app import config
    from app.db import init_db
    from app.services import cloud_manager as cm, model_settings

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dots-recipe.db")
    init_db()
    default = cm.build_provision_recipe("dots-ocr")
    model_settings.save_settings("dots-ocr", {"serving": {"gpu_memory_utilization": 0.8}})
    tuned = cm.build_provision_recipe("dots-ocr")

    assert default["configuration_signature"] != tuned["configuration_signature"]
    assert tuned["argv"][tuned["argv"].index("--gpu-memory-utilization") + 1] == "0.8"


def test_teleocr_cloud_provision_installs_official_architecture_plugin():
    from app.services import cloud_manager as cm

    recipe = cm.build_provision_recipe("teleocr")
    assert recipe["hf_repo"] == "StarDoc-AI/TeleOCR"
    assert recipe["runtime"] == "teleocr-native"
    assert recipe["settings"]["serving"]["max_model_len"] == 16384
    assert recipe["vllm_version"] == "0.11.0"
    assert recipe["transformers_version"] == "4.57.1"
    assert recipe["pip_extra"] == [
        "git+https://github.com/caipeng328/TeleOCR.git@main",
    ]
    # TeleOCR is launched by its native async-engine gateway, not `vllm serve`.
    assert recipe["argv"] == []
    assert recipe["served_model_name"] == "StarDoc-AI/TeleOCR"
    assert recipe["native_remote_port"] == 8889
    assert recipe["native_gateway_b64"]
    import base64
    assert b"aio_batch_two_step_extract" in base64.b64decode(recipe["native_gateway_b64"])
    assert b"vllm-async-engine" in base64.b64decode(recipe["native_gateway_b64"])

    glm = cm.build_provision_recipe("glm-ocr")
    assert glm["vllm_version"] == "0.19.0"
    assert glm["transformers_version"] == "5.3.1"
    assert "glmocr[selfhosted]" in glm["pip_extra"]
    assert glm["native_remote_port"] == 8890
    assert glm["native_gateway_b64"]
    assert b"from glmocr import GlmOcr" in base64.b64decode(glm["native_gateway_b64"])

    monkey = cm.build_provision_recipe("monkeyocrv2-parsing")
    assert monkey["runtime"] == "monkeyocr" and monkey["needs_monkeyocr_repo"] is True
    assert monkey["argv"][0] == "serve.py"
    assert monkey["draft_hf_repo"] == "zenosai/MonkeyOCRv2-B-Parsing-DFlash"
    assert monkey["draft_model_dir"].endswith("/MonkeyOCRv2-B-Parsing-DFlash")
    assert monkey["argv"][monkey["argv"].index("-d") + 1] == monkey["draft_model_dir"]

    mineru = cm.build_provision_recipe("mineru2.5")
    assert mineru["pip_extra"] == ["mineru-vl-utils[vllm]==2.0.5"]
    assert mineru["runtime"] == "mineru-native"
    assert mineru["native_remote_port"] == 8891
    assert b"MinerUClient" in base64.b64decode(mineru["native_gateway_b64"])


def test_monkey_dflash_is_skipped_for_custom_or_lora_checkpoints_when_disabled(tmp_path, monkeypatch):
    from app import config
    from app.db import init_db
    from app.services import cloud_manager as cm, model_settings

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dflash-cloud.db")
    init_db()
    with pytest.raises(ValueError, match="compatibile solo con MonkeyOCRv2-B-Parsing"):
        cm.build_provision_recipe("monkeyocrv2-parsing", model="org/custom-parser")
    with pytest.raises(ValueError, match="non viene abilitato per un checkpoint LoRA"):
        cm.build_provision_recipe(
            "monkeyocrv2-parsing", lora_path="/models/fine-tuned-adapter",
        )
    with pytest.raises(ValueError, match="disattivalo in Settings se specifichi un percorso pesi"):
        cm.build_provision_recipe(
            "monkeyocrv2-parsing", model_dir="/models/fine-tuned-full-checkpoint",
        )

    model_settings.save_settings("monkeyocrv2-parsing", {
        "workflow": {"dflash_enabled": False},
    })
    custom = cm.build_provision_recipe("monkeyocrv2-parsing", model="org/custom-parser")
    assert custom["draft_hf_repo"] == ""
    assert "-d" not in custom["argv"]


def test_docker_only_model_rents_its_own_image():
    """Su Vast.ai l'immagine si sceglie al noleggio: un modello che vive in
    un'immagine dedicata è servibile, purché l'istanza nasca con quella e
    nessuno tenti di reinstallare vLLM sopra."""
    from app.services import cloud_manager as cm

    recipe = cm.build_provision_recipe("unlimited-ocr")
    assert recipe["docker_image"] == "vllm/vllm-openai:unlimited-ocr"
    assert recipe["install_vllm"] is False
    assert recipe["argv"][:3] == ["-m", "vllm.entrypoints.cli.main", "serve"]
    assert "--logits_processors" in recipe["argv"]


def test_rent_uses_the_image_the_model_requires(monkeypatch):
    from app.services import cloud_manager as cm

    calls: list = []
    _fake_vast_client(monkeypatch, {("PUT", "/asks/7/"): {"new_contract": 12}}, calls)
    cm.rent_vast_instance("token", 7, adapter_id="unlimited-ocr", cuda_max_good=13.0)
    assert calls[0]["json"]["image"] == "vllm/vllm-openai:unlimited-ocr"

    # Un modello senza immagine propria resta sulla scelta guidata dal CUDA.
    calls.clear()
    _fake_vast_client(monkeypatch, {("PUT", "/asks/7/"): {"new_contract": 12}}, calls)
    cm.rent_vast_instance("token", 7, adapter_id="paddleocr-vl", cuda_max_good=13.0)
    assert calls[0]["json"]["image"].endswith("cuda-13.0.3-auto")


def test_provision_sends_the_recipe_to_the_instance(monkeypatch, tmp_path):
    import base64
    import json as jsonlib

    from app.services import cloud_manager as cm

    monkeypatch.setattr(cm.config, "SSH_KNOWN_HOSTS", tmp_path / "known_hosts")
    captured: dict = {}

    class Result:
        returncode = 0
        stdout = "tabularium-provision-started\n"
        stderr = ""

    def fake_run(cmd, stdin=None, **kwargs):
        captured["cmd"] = cmd
        if stdin is not None:
            stdin.read()
        return Result()

    monkeypatch.setattr(cm.subprocess, "run", fake_run)
    result = cm.provision_vast_server(
        "ssh5.vast.ai", 34567, monkeyocr_ref="abc123", adapter_id="paddleocr-vl",
    )
    assert result["served_model_name"] == "PaddleOCR-VL-1.6"
    remote = captured["cmd"][-1]
    payload = remote.split("RECIPE_B64=")[1].split(" ")[0].strip("'")
    recipe = jsonlib.loads(base64.b64decode(payload))
    assert recipe["adapter_id"] == "paddleocr-vl"
    assert "--no-enable-prefix-caching" in recipe["argv"]


def test_recipes_share_only_compatible_framework_environments():
    """Identical framework pins share disk; plugins and version pins stay isolated."""
    from app.services import cloud_manager as cm

    envs = {
        adapter: cm.build_provision_recipe(adapter)["venv_dir"]
        for adapter in (
            "monkeyocrv2-parsing", "deepseek-ocr", "mineru2.5", "glm-ocr",
            "dots-ocr", "paddleocr-vl", "qwen3-vl-8b",
        )
    }
    assert len({envs[a] for a in ("dots-ocr", "paddleocr-vl", "qwen3-vl-8b")}) == 1
    assert len({envs[a] for a in ("monkeyocrv2-parsing", "deepseek-ocr", "mineru2.5", "glm-ocr")}) == 4
    assert envs["mineru2.5"] != envs["dots-ocr"]
    assert envs["glm-ocr"] != envs["dots-ocr"]
    # I pesi vivono in una radice comune: sono file, non ambienti.
    assert cm.build_provision_recipe("deepseek-ocr")["model_dir"].startswith(cm.REMOTE_MODEL_ROOT)


def test_setup_script_replaces_the_running_server():
    """Sostituire un modello libera la VRAM prima del preflight, ma tocca
    soltanto il server verificato che ascolta sulla porta configurata."""
    script = (Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()
    assert "--replace-running-server" in script
    assert 'ss -H -ltnp "sport = :$PORT"' in script
    assert "kill -TERM" in script
    assert "pkill -f" not in script
    assert script.index("Arresto controllato del server Tabularium") < script.index("Preflight VRAM: ${GPU_FREE_VRAM_GB}")
    assert "Ambiente già presente" in script


def test_manifest_does_not_require_a_git_checkout():
    """`git rev-parse` fuori da un repo esce 128 e con `set -e` uccide il setup:
    i modelli non-MonkeyOCR non hanno alcun checkout da interrogare."""
    script = (Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()
    assert 'subprocess.check_output(["git", "rev-parse", "HEAD"]' not in script
    assert 'RECIPE_RUNTIME", "monkeyocr") == "monkeyocr"' in script


def test_setup_checks_the_driver_cuda_before_downloading():
    """La compute capability non basta: conta anche la CUDA del driver.

    Una 3060 ha capability 8.6 e supera il controllo esistente, ma con un
    driver sotto CUDA 12.8 non può eseguire le wheel PyTorch richieste da
    Blackwell — dopo aver scaricato i wheel e consumato minuti di noleggio a
    pagamento. Il controllo deve stare *prima* dell'installazione.
    """
    script = (Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()

    assert "MIN_CUDA_DRIVER" in script
    # The pinned vLLM wheel uses CUDA 12.9 by default. The cu128 torch index is
    # used only for a targeted Blackwell compatibility reinstall and does not
    # lower the driver floor for the vLLM runtime itself.
    assert 'TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"' in script
    assert 'MIN_CUDA_DRIVER="${MIN_CUDA_DRIVER:-12.9}"' in script
    # Deve precedere l'installazione, altrimenti non risparmia nulla: il
    # messaggio d'errore compare prima del primo pip install pesante.
    assert script.index("Max CUDA") < script.index("Installazione dipendenze Python")


def test_native_gateway_setup_has_a_default_log_path():
    script = (Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()
    assert 'REMOTE_LOG_PATH="${REMOTE_LOG_PATH:-/var/log/tabularium_setup.log}"' in script


def test_setup_preflights_recipe_disk_budget_before_mutating_the_instance():
    """Il disco Vast del container è una quota, non la capacità fisica
    dell'SSD. Fermarsi prima di apt/pip/pesi evita installazioni parziali e
    non rimuove cache dell'utente per far spazio di nascosto."""
    script = (Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()

    assert "min_free_disk_gb" in script
    assert "min_free_vram_gb" in script
    assert "Preflight disco fallito" in script
    assert "Preflight VRAM fallito" in script
    assert "Preflight RAM fallito" in script
    assert "Preflight rete fallito" in script
    assert "Python 3.10 o superiore" in script
    assert "nvidia-smi non trovato" in script
    assert "Nessun pacchetto o peso è stato scaricato" in script
    assert script.index("Preflight disco fallito") < script.index("Installazione dipendenze di sistema")
    assert script.index("Preflight disco fallito") < script.index("Installazione dipendenze Python")
    assert script.index("Preflight disco fallito") < script.index("Test egress before large installs")
    assert "pip cache purge" not in script
    assert 'rm -rf "$HOME/.cache/pip"' not in script


def test_driver_cuda_is_read_from_the_nvidia_smi_header():
    """`nvidia-smi` non espone la CUDA del driver fra i campi `--query-gpu`:
    va letta dall'intestazione, che riporta `CUDA Version: 12.2`."""
    import re
    import subprocess

    script = (Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_vllm.sh").read_text()
    match = re.search(r"DRIVER_CUDA=\$\(nvidia-smi[^\n]*\)", script)
    assert match, "manca la lettura della CUDA del driver"

    # La stessa riga sed della macchina reale, contro l'intestazione reale.
    header = (
        "| NVIDIA-SMI 535.161.07             Driver Version: 535.161.07   "
        "CUDA Version: 12.2        |"
    )
    got = subprocess.run(
        ["sed", "-n", r"s/.*CUDA Version: *\([0-9][0-9.]*\).*/\1/p"],
        input=header, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert got == "12.2"
