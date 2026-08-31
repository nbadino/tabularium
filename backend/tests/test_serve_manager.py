"""Test per il serving locale pluggable (Fase 2 multi-modello).

Il sottoprocesso vero (`vllm serve`, `scripts/serve_model.sh`) non gira in
CI: sostituiamo `subprocess.Popen` con un finto processo di lunga vita
(`sleep`), così `serve_manager` viene esercitato per intero (avvio, stato,
stop-before-start, arresto) senza dipendere da pesi scaricati o GPU.
"""
from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import model_registry, serve_manager


@pytest.fixture(autouse=True)
def _stop_any_active_server():
    yield
    serve_manager.stop()


def _fake_installed(adapter_id: str) -> None:
    """Crea i marker minimi che `model_registry.is_installed` richiede."""
    d = model_registry.models_dir(adapter_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "model.safetensors").write_bytes(b"\x00")


def test_start_refuses_uninstalled_model():
    with pytest.raises(ValueError, match="non è installato"):
        serve_manager.start("mineru2.5", port=18888)


def test_start_refuses_adapter_without_serve_command(monkeypatch):
    # Tutti gli 8 adapter predefiniti hanno ormai un `serve_command` (locale
    # via vLLM/Docker): il caso "nessun comando di serving" resta comunque un
    # ramo reale di `serve_manager.start` (es. un adapter futuro di sola
    # ricetta) e va coperto forzandolo via monkeypatch.
    _fake_installed("glm-ocr")
    monkeypatch.setattr(
        "app.services.model_adapters.GlmOcrAdapter.serve_command",
        lambda self, model_path, port: None,
    )
    with pytest.raises(ValueError, match="comando di serving"):
        serve_manager.start("glm-ocr", port=18888)


def test_start_stop_lifecycle(monkeypatch):
    _fake_installed("mineru2.5")

    long_lived = ["python", "-c", "import time; time.sleep(30)"] if not shutil.which("sleep") else ["sleep", "30"]
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    status = serve_manager.start("mineru2.5", port=18888)
    assert status.running is True
    assert status.adapter_id == "mineru2.5"
    assert status.pid is not None

    live = serve_manager.get_status()
    assert live.running is True
    assert live.adapter_id == "mineru2.5"

    stopped = serve_manager.stop()
    assert stopped.running is False


def test_start_stops_previous_server_first(monkeypatch):
    _fake_installed("mineru2.5")
    _fake_installed("dots-ocr")
    long_lived = ["python", "-c", "import time; time.sleep(30)"] if not shutil.which("sleep") else ["sleep", "30"]
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )
    monkeypatch.setattr(
        "app.services.model_adapters.DotsOcrAdapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    first = serve_manager.start("mineru2.5", port=18888)
    first_pid = first.pid
    second = serve_manager.start("dots-ocr", port=18889)

    assert second.adapter_id == "dots-ocr"
    # Il primo processo deve essere stato terminato (stop-before-start), non
    # solo dimenticato: os.kill con segnale 0 solleva se il pid non esiste più.
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(first_pid, 0)


def test_start_endpoint_activates_inference_config(monkeypatch):
    _fake_installed("mineru2.5")
    long_lived = ["python", "-c", "import time; time.sleep(30)"] if not shutil.which("sleep") else ["sleep", "30"]
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: long_lived,
    )

    with TestClient(app) as client:
        res = client.post("/api/models/mineru2.5/serve/start", json={"port": 18890})
        assert res.status_code == 200
        data = res.json()
        assert data["running"] is True
        assert data["adapter_id"] == "mineru2.5"

        from app.services import inference as infmod

        cfg = infmod.get_inference_config()
        assert cfg["adapter_id"] == "mineru2.5"
        assert cfg["url"] == "http://127.0.0.1:18890/v1"
        assert cfg["model"] == "mineru2.5"

        status_res = client.get("/api/models/serve/status")
        assert status_res.status_code == 200
        assert status_res.json()["running"] is True

        stop_res = client.post("/api/models/serve/stop")
        assert stop_res.status_code == 200
        assert stop_res.json()["running"] is False


def test_generic_vllm_adapter_auto_provisions_runtime_when_missing(monkeypatch):
    # Nessun passaggio manuale: se vLLM non è sul PATH e non c'è un override
    # esplicito, `start()` deve preparare da sé l'ambiente condiviso prima
    # di lanciare il sottoprocesso — mai chiedere all'utente di installarlo.
    _fake_installed("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: ["vllm", "serve", model_path],
    )
    monkeypatch.setattr("app.services.serve_manager.shutil.which", lambda name: None)
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("ensure_ready"),
    )
    monkeypatch.setattr("app.services.serve_manager.local_runtime.is_ready", lambda: True)
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.bin_dir",
        lambda: __import__("pathlib").Path("/fake/vllm-runtime/bin"),
    )
    captured: dict = {}

    class FakeProc:
        def __init__(self, *args, **kwargs):
            captured["argv"] = args[0]
            captured["env"] = kwargs.get("env")
            self.pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("mineru2.5", port=18891)

    assert calls == ["ensure_ready"]
    assert captured["env"]["PATH"].startswith("/fake/vllm-runtime/bin")


def test_generic_vllm_adapter_skips_provisioning_when_already_on_path(monkeypatch):
    _fake_installed("mineru2.5")
    monkeypatch.setattr(
        "app.services.model_adapters.MinerU2_5Adapter.serve_command",
        lambda self, model_path, port: ["vllm", "serve", model_path],
    )
    monkeypatch.setattr("app.services.serve_manager.shutil.which", lambda name: "/usr/bin/vllm")
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("ensure_ready"),
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4243

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("mineru2.5", port=18892)

    assert calls == []


def test_monkeyocrv2_auto_prepares_repo_and_runtime_when_unset(monkeypatch):
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "")
    monkeypatch.setattr("app.services.serve_manager.config.SERVE_PYTHON", "")
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.vendor_repos.ensure_monkeyocrv2_repo",
        lambda: (calls.append("repo"), __import__("pathlib").Path("/fake/vendor/MonkeyOCRv2"))[1],
    )
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("runtime"),
    )
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.python_bin",
        lambda: __import__("pathlib").Path("/fake/vllm-runtime/bin/python"),
    )
    captured: dict = {}

    class FakeProc:
        def __init__(self, *args, **kwargs):
            captured["env"] = kwargs.get("env")
            self.pid = 4244

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18893)

    assert "repo" in calls and "runtime" in calls
    assert captured["env"]["TABULARIUM_TRAIN_REPO"] == "/fake/vendor/MonkeyOCRv2"
    assert captured["env"]["TABULARIUM_TRAIN_PYTHON"] == "/fake/vllm-runtime/bin/python"


def test_monkeyocrv2_respects_existing_manual_overrides(monkeypatch):
    # Chi ha già un checkout/ambiente proprio non deve vederselo sostituire.
    _fake_installed("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_REPO", "/my/own/MonkeyOCRv2")
    monkeypatch.setattr("app.services.serve_manager.config.TRAIN_PYTHON", "/my/own/env/bin/python")
    calls: list[str] = []
    monkeypatch.setattr(
        "app.services.serve_manager.vendor_repos.ensure_monkeyocrv2_repo",
        lambda: calls.append("repo"),
    )
    monkeypatch.setattr(
        "app.services.serve_manager.local_runtime.ensure_ready",
        lambda: calls.append("runtime"),
    )

    class FakeProc:
        def __init__(self, *args, **kwargs):
            self.pid = 4245

        def poll(self):
            return None

    monkeypatch.setattr("app.services.serve_manager.subprocess.Popen", FakeProc)

    serve_manager.start("monkeyocrv2-parsing", port=18894)

    assert calls == []
