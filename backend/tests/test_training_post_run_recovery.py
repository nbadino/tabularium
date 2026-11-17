"""A transient SSH failure after a finished training must not strand the model.

Prod, 2026-11-17: run_1794871543653 trained 210/210 steps, rsync brought the
checkpoints home, then the one SSH read of the remote manifest timed out after
20 s. The run was marked failed and prepare-checkpoint refused it.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services import checkpoint_prepare, trainer, training_executor
from app.services.artifacts import write_manifest
from app.services.training_executor import RemoteProcess, SshExecutor, TrainingRecipe

MANIFEST_CMD_ERROR = (
    "Command '['ssh', 'root@gpu', \"printf '__TABULARIUM_MANIFEST_BEGIN__\\\\n'; cat "
    "/tmp/tabularium-runs/run_x/artifacts.sha256\"]' timed out after 20 seconds"
)


def _process(tmp_path: Path) -> RemoteProcess:
    known = tmp_path / "known_hosts"
    known.write_text("gpu.example ssh-ed25519 AAAA\n", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    recipe = TrainingRecipe("run_7", run, run / "train.sh", run / "train.jsonl", run / "val.jsonl", "v7")
    process = RemoteProcess(SshExecutor("gpu.example", known_hosts=known), recipe, run / "train.log", 42)
    process._sleep = lambda seconds: None  # noqa: SLF001 - no real backoff in tests
    return process


def _fake_rsync(monkeypatch, payload: bytes = b"weights"):
    def run(argv, **kwargs):
        assert argv[0] == "rsync"
        dest = Path(argv[-1])
        (dest / "ckpt").mkdir(parents=True, exist_ok=True)
        (dest / "ckpt" / "model.safetensors").write_bytes(payload)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(training_executor.subprocess, "run", run)


def _manifest_reply(tmp_path: Path) -> str:
    dest = tmp_path / "local" / "checkpoints"
    manifest = write_manifest(dest, tmp_path / "expected.sha256")
    body = manifest.read_text(encoding="utf-8").strip()
    return f"banner\n__TABULARIUM_MANIFEST_BEGIN__\n{body}\n__TABULARIUM_MANIFEST_END__\n"


def test_manifest_read_retries_a_timeout_with_a_longer_timeout(tmp_path, monkeypatch):
    process = _process(tmp_path)
    _fake_rsync(monkeypatch)
    dest = tmp_path / "local" / "checkpoints"
    calls: list[float] = []

    def remote(command, *, timeout=20):
        calls.append(timeout)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(["ssh"], timeout)
        return subprocess.CompletedProcess(["ssh"], 0, _manifest_reply(tmp_path), "")

    monkeypatch.setattr(process, "_remote", remote)
    result = process.download_artifacts(dest)
    assert result["ok"] is True
    assert result["manifest"] == "verified"
    assert result["downloaded"] is True
    assert calls == [RemoteProcess.MANIFEST_TIMEOUT_S] * 2
    assert RemoteProcess.MANIFEST_TIMEOUT_S > 20


def test_manifest_unreachable_after_retries_is_reported_not_raised(tmp_path, monkeypatch):
    process = _process(tmp_path)
    _fake_rsync(monkeypatch)
    sleeps: list[float] = []
    process._sleep = sleeps.append  # noqa: SLF001

    def remote(command, *, timeout=20):
        raise subprocess.TimeoutExpired(["ssh"], timeout)

    monkeypatch.setattr(process, "_remote", remote)
    result = process.download_artifacts(tmp_path / "local" / "checkpoints")
    assert result["manifest"] == "unreachable"
    assert result["downloaded"] is True
    assert len(sleeps) == RemoteProcess.POST_TRAINING_ATTEMPTS - 1
    assert sleeps == sorted(sleeps) and sleeps[0] > 0


def test_a_remote_answer_is_not_retried(tmp_path, monkeypatch):
    process = _process(tmp_path)
    _fake_rsync(monkeypatch)
    calls = []

    def remote(command, *, timeout=20):
        calls.append(command)
        return subprocess.CompletedProcess(["ssh"], 1, "", "No such file")

    monkeypatch.setattr(process, "_remote", remote)
    result = process.download_artifacts(tmp_path / "local" / "checkpoints")
    assert result["manifest"] == "missing"
    assert result["ok"] is False
    assert len(calls) == 1


class _FakeProc:
    def __init__(self, code: int, result: dict):
        self.code, self.result = code, result

    def wait(self):
        return self.code

    def poll(self):
        return self.code

    def download_artifacts(self, destination: Path) -> dict:
        (destination / "ckpt").mkdir(parents=True, exist_ok=True)
        (destination / "ckpt" / "model.safetensors").write_bytes(b"weights")
        return self.result


def _monitor_run(project_id: int, proc: _FakeProc, monkeypatch) -> dict:
    run_dir = config.DATA_DIR / str(project_id) / "runs" / "run_1700000000001"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"run_id": run_dir.name, "state": "running", "config": {}}), encoding="utf-8")
    prepared: list[str] = []
    monkeypatch.setattr(trainer, "_auto_prepare", lambda pid, run_id: prepared.append(run_id))
    trainer._ACTIVE[project_id] = {"proc": proc, "run_dir": run_dir}  # noqa: SLF001
    trainer._monitor(project_id)  # noqa: SLF001
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    meta["_prepared"] = prepared
    return meta


def test_unreachable_manifest_after_a_good_download_finishes_the_run(monkeypatch):
    meta = _monitor_run(8811, _FakeProc(0, {
        "ok": False, "checked": 0, "errors": ["SSH unreachable after 4 attempts"],
        "downloaded": True, "manifest": "unreachable",
    }), monkeypatch)
    assert meta["state"] == "finished"
    assert meta["exit_code"] == 0
    assert "SSH unreachable" in meta["artifact_warning"]
    assert meta["post_training"] == {"training_exit_code": 0, "downloaded": True, "manifest": "unreachable"}
    assert meta["_prepared"] == ["run_1700000000001"]


def test_a_checksum_mismatch_still_fails_the_run(monkeypatch):
    meta = _monitor_run(8812, _FakeProc(0, {
        "ok": False, "checked": 1, "errors": ["ckpt/model.safetensors: sha256 diverso"],
        "downloaded": True, "manifest": "mismatch",
    }), monkeypatch)
    assert meta["state"] == "failed"
    assert meta["post_training"]["training_exit_code"] == 0
    assert meta["post_training"]["manifest"] == "mismatch"
    assert meta["_prepared"] == []


def _stranded_run(project_id: int, **overrides) -> Path:
    """The shape run_1794871543653 had on prod after the failure."""
    run_dir = config.DATA_DIR / str(project_id) / "runs" / "run_1794871543653"
    checkpoint = run_dir / "checkpoints" / "qwen3-vl-8b_lora" / "checkpoint-210"
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "adapter_model.safetensors").write_bytes(b"lora")
    manifest = write_manifest(run_dir / "checkpoints")
    meta = {
        "run_id": run_dir.name,
        "state": "failed",
        "exit_code": 1,
        "config": {"adapter_id": "qwen3-vl-8b", "train_type": "lora", "executor": "vast",
                   "ssh_host": "gpu", "auto_prepare": True},
        "adapter_id": "qwen3-vl-8b",
        "artifacts": {"ok": True, "checked": 2, "errors": []},
        "summary": {"steps": 210, "total_steps": 210},
        "error": MANIFEST_CMD_ERROR,
        "remote_cleaned_at": "2026-11-17T00:47:40+00:00",
        **overrides,
    }
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert manifest.is_file()
    return run_dir


def test_a_stranded_legacy_run_can_be_recovered_then_prepared(monkeypatch):
    run_dir = _stranded_run(8813)
    with pytest.raises(ValueError, match="terminata con successo"):
        checkpoint_prepare.prepare_plan(8813, run_dir.name)
    prepared: list[str] = []
    monkeypatch.setattr(trainer, "_auto_prepare", lambda pid, run_id: prepared.append(run_id))

    assert trainer._status(8813, run_id=run_dir.name)["run"]["recoverable"] is True  # noqa: SLF001
    run = trainer.recover_run(8813, run_dir.name, reason="manifest read timed out")

    assert run["state"] == "finished"
    assert run["recovery"]["previous_state"] == "failed"
    assert run["recovery"]["previous_error"] == MANIFEST_CMD_ERROR
    assert run["recovery"]["previous_exit_code"] == 1
    assert run["recovery"]["evidence"] == "legacy_manifest_read"
    assert run["recovery"]["reason"] == "manifest read timed out"
    assert "error" not in run
    assert prepared == [run_dir.name]
    plan = checkpoint_prepare.prepare_plan(8813, run_dir.name)
    assert plan["mode"] == "local"  # the instance is gone: merge here


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"summary": {"steps": 120, "total_steps": 210}}, "last step"),
        ({"error": "CUDA out of memory"}, "manifest read"),
        ({"post_training": {"training_exit_code": 1, "downloaded": True, "manifest": "unreachable"}}, "exit 0"),
        ({"post_training": {"training_exit_code": 0, "downloaded": False, "manifest": None}}, "downloaded"),
        ({"post_training": {"training_exit_code": 0, "downloaded": True, "manifest": "mismatch"}}, "remote manifest"),
        ({"state": "finished"}, "failed run"),
    ],
)
def test_recovery_refuses_a_run_without_the_evidence(overrides, reason):
    run_dir = _stranded_run(8814, **overrides)
    with pytest.raises(ValueError, match=reason):
        trainer.recover_run(8814, run_dir.name)


def test_recovery_refuses_checkpoints_that_changed_since_the_download():
    run_dir = _stranded_run(8815)
    (run_dir / "checkpoints" / "qwen3-vl-8b_lora" / "checkpoint-210" / "adapter_model.safetensors").write_bytes(b"x")
    with pytest.raises(ValueError, match="checksum"):
        trainer.recover_run(8815, run_dir.name)
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["state"] == "failed"


@pytest.fixture()
def client():
    from app.db import init_db
    from app.main import app

    init_db()
    with TestClient(app) as c:
        yield c


def test_recover_route_records_and_returns_the_run(client, monkeypatch, tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    project = client.post("/api/projects", json={"name": f"R{time.time_ns()}", "archive_dir": str(archive)}).json()
    run_dir = _stranded_run(int(project["id"]))
    monkeypatch.setattr(trainer, "_auto_prepare", lambda pid, run_id: None)
    res = client.post(f"/api/projects/{project['id']}/training/recover", json={"run_id": run_dir.name})
    assert res.status_code == 200, res.text
    assert res.json()["run"]["state"] == "finished"
    again = client.post(f"/api/projects/{project['id']}/training/recover", json={"run_id": run_dir.name})
    assert again.status_code == 400
