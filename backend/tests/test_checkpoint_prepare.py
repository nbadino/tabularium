"""Preparazione del checkpoint: piano, comando remoto, storico delle run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config
from app.services import checkpoint_prepare, custom_models, trainer, training_executor


def _run(project_id: int, run_id: str = "run_1700000000000", **cfg) -> Path:
    run_dir = config.DATA_DIR / str(project_id) / "runs" / run_id
    (run_dir / "checkpoints" / "qwen3-vl-8b_lora" / "checkpoint-3").mkdir(parents=True, exist_ok=True)
    checkpoint = run_dir / "checkpoints" / "qwen3-vl-8b_lora" / "checkpoint-3"
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "model.safetensors").write_bytes(b"weights")
    meta = {
        "run_id": run_id,
        "state": "finished",
        "started_at": "2026-10-20T09:33:29+00:00",
        "ended_at": "2026-10-20T09:36:12+00:00",
        "config": {
            "adapter_id": "qwen3-vl-8b",
            "train_type": "lora",
            "model": "Qwen/Qwen3-VL-8B-Instruct",
            **cfg,
        },
        "adapter_id": "qwen3-vl-8b",
        "toolchain": "ms-swift",
    }
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "train.log").write_text(
        "{'loss': '2.1', 'global_step/max_steps': '1/3'}\n"
        "{'eval_loss': '0.693', 'global_step/max_steps': '3/3'}\n"
        "{'train_runtime': '35.2', 'train_loss': '1.476', 'global_step/max_steps': '3/3'}\n",
        encoding="utf-8",
    )
    (run_dir / "metrics.jsonl").write_text('{"loss": 1.476, "step": 3}\n', encoding="utf-8")
    return run_dir


def test_checkpoint_info_and_run_history(monkeypatch):
    run_dir = _run(8801)
    info = trainer.checkpoint_info(run_dir)
    assert info is not None
    assert info["name"] == "checkpoint-3"
    assert info["bytes"] > 0
    assert info["needs_merge"] is True

    st = trainer._status(8801, run_id="run_1700000000000")  # noqa: SLF001
    assert st["active"] is False
    assert st["run"]["summary"]["train_loss"] == 1.476
    assert st["run"]["summary"]["eval_loss"] == 0.693
    assert st["run"]["checkpoint"]["name"] == "checkpoint-3"

    runs = trainer.list_runs(8801)
    assert runs[0]["run_id"] == "run_1700000000000"
    assert runs[0]["summary"]["eval_loss"] == 0.693


def test_prepare_plan_prefers_remote_and_blocks_when_no_toolchain(monkeypatch):
    config.SSH_KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    config.SSH_KNOWN_HOSTS.touch()
    _run(8802, executor="vast", ssh_host="gpu.example")
    monkeypatch.setattr(
        custom_models,
        "toolchain_status",
        lambda recipe: {"toolchain": recipe.toolchain, "available": False, "command": "swift", "hint": "manca swift"},
    )
    plan = checkpoint_prepare.prepare_plan(8802, "run_1700000000000")
    assert plan["mode"] == "remote"
    assert plan["ready"] is True
    assert plan["needs_merge"] is True

    _run(8803, executor="local")
    monkeypatch.setattr(
        custom_models,
        "toolchain_status",
        lambda recipe: {"toolchain": recipe.toolchain, "available": False, "command": "swift", "hint": "manca swift"},
    )
    plan = checkpoint_prepare.prepare_plan(8803, "run_1700000000000")
    assert plan["ready"] is False
    assert plan["mode"] == "local"
    assert "manca swift" in plan["blockers"]


def test_remote_merge_command_per_toolchain():
    ms = checkpoint_prepare._merge_script(  # noqa: SLF001
        {
            "adapter_id": "qwen3-vl-8b",
            "toolchain": "ms-swift",
            "checkpoint": {"relative_path": "checkpoints/a/checkpoint-1", "name": "checkpoint-1"},
            "base_model": "Qwen/Qwen3-VL-8B-Instruct",
        },
        {"ssh_train_repo": "/root/ms-swift", "ssh_python": "/root/envs/swift/bin/python", "use_hf": True},
        "/tmp/tabularium-runs/r1",
    )
    assert "swift export" in ms[0]
    assert 'CKPT="$RUN_DIR/checkpoints/a/checkpoint-1"' in ms[0]
    assert "USE_HF=1" in ms[0]
    assert ms[1] == "checkpoints/checkpoint-1-merged"

    llama = checkpoint_prepare._merge_script(  # noqa: SLF001
        {
            "adapter_id": "glm-ocr",
            "toolchain": "llama-factory",
            "checkpoint": {"relative_path": "checkpoints/a/checkpoint-2", "name": "checkpoint-2"},
            "base_model": "zai-org/GLM-OCR",
        },
        {"ssh_train_repo": "/root/LLaMA-Factory"},
        "/tmp/tabularium-runs/r2",
    )
    assert "llamafactory-cli export" in llama[0]
    assert "--template glm_ocr" in llama[0]

    paddle = checkpoint_prepare._merge_script(  # noqa: SLF001
        {
            "adapter_id": "paddleocr-vl",
            "toolchain": "paddleformers",
            "checkpoint": {"relative_path": "checkpoints/a/checkpoint-3", "name": "checkpoint-3"},
            "base_model": "PaddlePaddle/PaddleOCR-VL-1.6",
        },
        {"ssh_train_repo": "/root/PaddleFormers"},
        "/tmp/tabularium-runs/r3",
    )
    assert "paddleformers-cli export" in paddle[0]
    assert paddle[1] == "checkpoints/checkpoint-3-merged/export"


def test_remote_helpers_validate_the_run_path():
    executor = training_executor.SshExecutor(host="gpu.example", known_hosts=config.SSH_KNOWN_HOSTS)
    with pytest.raises(ValueError):
        executor.remote_dir("../escape")


def test_local_prepare_job_registers_in_background(monkeypatch):
    run_dir = _run(8804, executor="local")
    monkeypatch.setattr(
        custom_models,
        "toolchain_status",
        lambda recipe: {"toolchain": recipe.toolchain, "available": True, "command": "swift", "hint": ""},
    )
    monkeypatch.setattr(
        custom_models,
        "register_training_checkpoint",
        lambda project_id, run_id, **kwargs: {"id": "custom-test", "display_name": "Test model"},
    )
    checkpoint_prepare.start_prepare(8804, "run_1700000000000")
    import time
    for _ in range(40):
        status = checkpoint_prepare._job_status(8804, "run_1700000000000")  # noqa: SLF001
        if status["job"].get("state") != "running":
            break
        time.sleep(0.05)
    assert status["job"]["state"] == "finished"
    assert status["job"]["model_id"] == "custom-test"
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["prepare_job"]["state"] == "finished"
    assert "registrato" in (run_dir / "prepare.log").read_text(encoding="utf-8")


class _FakeStream:
    def __init__(self, log: Path):
        self.log = log

    def wait(self) -> int:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("ab") as stream:
            stream.write(b"merge ok\n")
        return 0


class _FakeExecutor(training_executor.SshExecutor):
    def __init__(self, known_hosts: Path):
        super().__init__(host="gpu.example", known_hosts=known_hosts)

    def run(self, command: str, *, timeout: float | None = None):
        import subprocess
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    def stream(self, command: str, log_file: Path):
        return _FakeStream(log_file)

    def download(self, remote_path: str, local_dir: Path, *, log_file: Path | None = None) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        (local_dir / "config.json").write_text("{}", encoding="utf-8")
        (local_dir / "model.safetensors").write_bytes(b"merged")


def test_remote_prepare_downloads_and_registers(monkeypatch, tmp_path):
    run_dir = _run(8805, executor="vast", ssh_host="gpu.example")
    plan = checkpoint_prepare.prepare_plan(8805, "run_1700000000000")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("", encoding="utf-8")
    fake = _FakeExecutor(known_hosts)
    monkeypatch.setattr(checkpoint_prepare, "executor_from_config", lambda cfg, known_hosts=None: fake)
    monkeypatch.setattr(
        custom_models,
        "register_training_checkpoint",
        lambda project_id, run_id, **kwargs: {
            "id": "custom-remote",
            "display_name": "Remote merged",
            "_merged": str(kwargs.get("merged_path")),
        },
    )
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    job = {"state": "running", "phase": "merge"}
    model = checkpoint_prepare._prepare_remote(  # noqa: SLF001
        8805, "run_1700000000000", plan, run_dir, meta, job
    )
    assert model["id"] == "custom-remote"
    merged = run_dir / "checkpoints" / "checkpoint-3-merged"
    assert (merged / "model.safetensors").read_bytes() == b"merged"
    assert "merge ok" in (run_dir / "prepare.log").read_text(encoding="utf-8")


def test_finished_run_auto_prepares_when_plan_is_ready(monkeypatch, tmp_path):
    called: list[tuple[int, str]] = []
    monkeypatch.setattr(checkpoint_prepare, "prepare_plan", lambda pid, rid: {"ready": True})
    monkeypatch.setattr(
        checkpoint_prepare,
        "start_prepare",
        lambda pid, rid: called.append((pid, rid)) or {"job": {"state": "running"}},
    )
    trainer._auto_prepare(8809, "run_1")  # noqa: SLF001
    assert called == [(8809, "run_1")]


def test_auto_prepare_skips_a_blocked_plan(monkeypatch):
    called: list[tuple[int, str]] = []
    monkeypatch.setattr(checkpoint_prepare, "prepare_plan", lambda pid, rid: {"ready": False})
    monkeypatch.setattr(
        checkpoint_prepare,
        "start_prepare",
        lambda pid, rid: called.append((pid, rid)),
    )
    trainer._auto_prepare(8809, "run_1")  # noqa: SLF001
    assert called == []
