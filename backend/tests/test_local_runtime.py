"""Test per l'ambiente vLLM auto-provisionato (nessun passaggio manuale)."""
from __future__ import annotations

from app.services import local_runtime


def test_ensure_ready_is_a_noop_when_already_ready(monkeypatch):
    monkeypatch.setattr(local_runtime, "is_ready", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(local_runtime.venv, "EnvBuilder", lambda **kw: (_ for _ in ()).throw(
        AssertionError("non doveva creare un venv: is_ready() era già True")
    ))
    local_runtime.ensure_ready()  # non deve alzare né chiamare EnvBuilder


def test_ensure_ready_creates_venv_and_installs_packages(monkeypatch, tmp_path):
    monkeypatch.setattr(local_runtime.config, "ROOT_DIR", tmp_path)
    ready_calls = {"n": 0}

    def fake_is_ready():
        # Falso finché non è "installato" (dopo la creazione simulata).
        ready_calls["n"] += 1
        return ready_calls["n"] > 1

    monkeypatch.setattr(local_runtime, "is_ready", fake_is_ready)

    created: list[str] = []

    class FakeBuilder:
        def __init__(self, **kwargs):
            created.append("builder")

        def create(self, target):
            created.append(target)

    monkeypatch.setattr(local_runtime.venv, "EnvBuilder", FakeBuilder)

    installed: list[list[str]] = []

    def fake_run(argv, **kwargs):
        installed.append(argv)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(local_runtime.subprocess, "run", fake_run)

    local_runtime.ensure_ready()

    assert created  # il venv è stato "creato"
    assert len(installed) == 2  # upgrade pip + install pacchetti
    assert "vllm" in installed[1]
    state = local_runtime.install_state()
    assert state["error"] is None


def test_ensure_ready_records_failure_state(monkeypatch, tmp_path):
    monkeypatch.setattr(local_runtime.config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(local_runtime, "is_ready", lambda: False)
    monkeypatch.setattr(local_runtime.venv, "EnvBuilder", lambda **kw: type(
        "B", (), {"create": lambda self, target: None}
    )())

    def failing_run(*args, **kwargs):
        raise RuntimeError("pip fallito")

    monkeypatch.setattr(local_runtime.subprocess, "run", failing_run)

    try:
        local_runtime.ensure_ready()
        raise AssertionError("doveva sollevare RuntimeError")
    except RuntimeError as exc:
        assert "installazione automatica" in str(exc)

    state = local_runtime.install_state()
    assert state["state"] == "failed"
    assert state["error"]
