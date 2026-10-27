from __future__ import annotations

from pathlib import Path

import pytest

from app.services.training_executor import RunPodExecutor, SshExecutor, TrainingRecipe, VastExecutor, RemoteProcess, executor_from_config
from app.services import trainer
from app import db
from app.schemas import TrainConfig


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
    # rsync non crea le directory padre: il launch deve fare mkdir prima.
    assert "mkdir -p" in executor.mkdir_argv(recipe)[-1]
    assert any("UserKnownHostsFile=" in item for item in launch)
    assert "bash train.sh" in launch[-1]
    # I fd del processo in background devono essere chiusi, altrimenti ssh
    # resta appeso e start_run non ritorna mai.
    assert "> /dev/null 2>&1 < /dev/null" in launch[-1]


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


def test_remote_cleanup_uses_only_validated_run_directory(tmp_path: Path, monkeypatch):
    known = tmp_path / "known_hosts"
    known.write_text("gpu.example ssh-ed25519 AAAA\n", encoding="utf-8")
    executor = SshExecutor("gpu.example", known_hosts=known, remote_root="/srv/tabularium-runs")
    recipe = _recipe(tmp_path)
    commands: list[str] = []

    def fake_remote(command: str):
        commands.append(command)
        return __import__("subprocess").CompletedProcess([], 0, "", "")

    process = RemoteProcess(executor, recipe, tmp_path / "train.log", 4242)
    monkeypatch.setattr(process, "_remote", fake_remote)
    process.cleanup()

    assert commands == ["rm -rf -- /srv/tabularium-runs/run_7"]


def test_training_schema_keeps_remote_runtime_fields():
    cfg = TrainConfig(
        executor="vast",
        ssh_host="gpu.example",
        ssh_train_repo="/opt/MonkeyOCRv2/parsing/train",
        ssh_python="/opt/venv/bin/python",
    )
    assert cfg.model_dump(exclude_unset=True)["ssh_train_repo"] == "/opt/MonkeyOCRv2/parsing/train"
    assert cfg.model_dump(exclude_unset=True)["ssh_python"] == "/opt/venv/bin/python"


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


def test_launch_recovers_the_remote_pid_when_ssh_does_not_close(tmp_path: Path, monkeypatch):
    import subprocess

    known = tmp_path / "known_hosts"
    known.write_text("gpu.example ssh-ed25519 AAAA\n", encoding="utf-8")
    executor = SshExecutor("gpu.example", known_hosts=known)
    recipe = _recipe(tmp_path)
    synced: list[list[str]] = []

    # The sync is a stream of file progress; what this test is about is what happens
    # after it. Standing in for `_stream` keeps the test on one question.
    monkeypatch.setattr(
        SshExecutor,
        "_stream",
        lambda self, command, stream, on_line: synced.append(command) or 0,
    )

    def fake_run(argv, **kwargs):
        if "printf" not in " ".join(argv):
            return subprocess.CompletedProcess(argv, 0, "", "")
        # ssh stays attached after printing the PID; the training has started anyway.
        raise subprocess.TimeoutExpired(argv, 60, output="4242\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    proc = executor.launch(recipe, tmp_path / "train.log")

    assert proc.pid == 4242
    assert synced and synced[0][0] == "rsync"


def test_the_sync_stream_reports_each_file_to_the_activity_file(tmp_path: Path, monkeypatch):
    import json
    import subprocess

    known = tmp_path / "known_hosts"
    known.write_text("gpu.example ssh-ed25519 AAAA\n", encoding="utf-8")
    executor = SshExecutor("gpu.example", known_hosts=known)
    recipe = _recipe(tmp_path)

    def fake_stream(self, command, stream, on_line):
        on_line("TABULARIUM_FILE:120:12:epoch_1.jsonl")
        on_line("TABULARIUM_FILE:80:8:checkpoints/")
        return 0

    monkeypatch.setattr(SshExecutor, "_stream", fake_stream)
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "4242\n", ""),
    )
    executor.launch(recipe, tmp_path / "train.log")

    activity = json.loads((recipe.run_dir / "activity.json").read_text(encoding="utf-8"))
    # A directory announcement is not a finished file; only the file counts.
    assert activity["files_done"] == 1
    assert activity["bytes_done"] == 120
    assert activity["stage"] == "model_loading"
