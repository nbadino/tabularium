from __future__ import annotations

from pathlib import Path

import pytest

from app.services.training_executor import RunPodExecutor, SshExecutor, TrainingRecipe, VastExecutor, executor_from_config
from app.services import trainer
from app import db


def _recipe(tmp_path: Path) -> TrainingRecipe:
    run = tmp_path / "run"
    run.mkdir()
    for name in ("train.sh", "train.jsonl", "val.jsonl"):
        (run / name).write_text(name, encoding="utf-8")
    return TrainingRecipe("run_7", run, run / "train.sh", run / "train.jsonl", run / "val.jsonl", "v7")


def test_ssh_executor_requires_known_hosts(tmp_path: Path):
    executor = SshExecutor("gpu.example", known_hosts=tmp_path / "missing")
    with pytest.raises(ValueError, match="known_hosts"):
        executor.launch_argv(_recipe(tmp_path))


def test_ssh_executor_uses_checksum_and_strict_host_key(tmp_path: Path):
    known = tmp_path / "known_hosts"
    known.write_text("gpu.example ssh-ed25519 AAAA\n", encoding="utf-8")
    executor = SshExecutor("gpu.example", user="runner", known_hosts=known)
    recipe = _recipe(tmp_path)
    sync = executor.sync_argv(recipe)
    launch = executor.launch_argv(recipe)
    assert "--checksum" in sync
    assert "StrictHostKeyChecking=yes" in sync[sync.index("-e") + 1]
    assert any("UserKnownHostsFile=" in item for item in launch)
    assert "bash train.sh" in launch[-1]


def test_executor_factory_rejects_unknown_provider(tmp_path: Path):
    with pytest.raises(ValueError, match="non supportato"):
        executor_from_config({"executor": "unknown"}, known_hosts=tmp_path)


def test_cloud_executors_reuse_ssh_transport(tmp_path: Path):
    known = tmp_path / "known_hosts"
    known.write_text("gpu.example ssh-ed25519 AAAA\n", encoding="utf-8")
    vast = executor_from_config({"executor": "vast", "ssh_host": "gpu.example"}, known_hosts=known)
    runpod = executor_from_config({"executor": "runpod", "ssh_host": "gpu.example"}, known_hosts=known)
    assert isinstance(vast, VastExecutor)
    assert isinstance(runpod, RunPodExecutor)
    assert vast.provider == "vast"
    assert runpod.provider == "runpod"


def test_reconcile_does_not_kill_remote_job_on_backend_restart():
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(kind, provider, remote_job_id, state, command_json) VALUES(?,?,?,?,?)",
            ("training", "ssh", "4242", "running", "{}"),
        )
    trainer.reconcile_jobs()
    with db.connect() as conn:
        state = conn.execute("SELECT state FROM jobs WHERE remote_job_id='4242'").fetchone()[0]
    assert state == "running"
