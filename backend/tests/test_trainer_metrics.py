"""GPU and metrics readers must survive what real machines and crashed runs write."""
from __future__ import annotations

import json
import subprocess

from app.services import trainer_metrics


def test_gpu_snapshot_skips_a_gpu_that_reports_not_available(monkeypatch):
    out = "0, A100, 81920, 1024, 3, 40\n1, A100 MIG 3g.40gb, [N/A], [N/A], [N/A], [N/A]\n"
    monkeypatch.setattr(trainer_metrics, "_gpu_cache", None, raising=False)
    monkeypatch.setattr(
        trainer_metrics.subprocess, "run",
        lambda argv, **_k: subprocess.CompletedProcess(argv, 0, stdout=out, stderr=""),
    )
    gpus = trainer_metrics.gpu_snapshot()
    assert [g["index"] for g in gpus] == ["0"]


def test_a_truncated_metrics_line_does_not_hide_the_others(tmp_path):
    lines = [json.dumps({"step": 1, "loss": 2.0}), json.dumps({"step": 2, "loss": 1.5}), '{"step": 3, "lo', "[1, 2]"]
    (tmp_path / "metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert [m["step"] for m in trainer_metrics._metrics(tmp_path)] == [1, 2]


def test_training_stream_reads_the_status_off_the_event_loop(tmp_path, monkeypatch):
    """`_status` reads SQLite, files and nvidia-smi (8 s timeout): on the event
    loop it would freeze every other request while a run is watched."""
    import asyncio

    from fastapi.testclient import TestClient

    from app.api import training as training_api
    from app.main import app

    on_loop: list[bool] = []

    def fake_status(_project_id):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return {"run": {"state": "finished"}, "active": False, "log_tail": "", "metrics": [], "gpu": []}

    real_sleep = asyncio.sleep

    async def no_wait(_seconds):
        await real_sleep(0)

    monkeypatch.setattr(training_api.trainer, "_status", fake_status)
    monkeypatch.setattr(training_api.asyncio, "sleep", no_wait)
    archive = tmp_path / "a"
    archive.mkdir()
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "T", "archive_dir": str(archive)}).json()["id"]
        body = client.get(f"/api/projects/{pid}/training/stream").text
    assert '"done"' in body
    assert on_loop and not any(on_loop)


# ms-swift >= 4.3 quotes every value in its logging dict: the chart must still get the points.
SWIFT4_TRAIN = (
    "{'loss': '0.0542', 'grad_norm': '0.1916', 'learning_rate': '1e-05', 'token_acc': '0.9869', "
    "'epoch': '0.01515', 'global_step/max_steps': '1/198', 'elapsed_time': '24s', "
    "'remaining_time': '1h 18m 51s', 'memory(GiB)': '16.49', 'train_speed(s/it)': '24.01'}"
)
SWIFT4_EVAL = (
    "{'eval_loss': '0.0481', 'eval_token_acc': '0.9881', 'eval_runtime': '12.3', "
    "'eval_samples_per_second': '3.2', 'epoch': '0.5', 'global_step/max_steps': '99/198'}"
)


def test_ms_swift_4_quoted_train_line_keeps_every_value():
    m = trainer_metrics.parse_metrics_line(SWIFT4_TRAIN)
    assert m is not None
    assert m["loss"] == 0.0542
    assert m["lr"] == 1e-05
    assert m["grad_norm"] == 0.1916
    assert m["token_acc"] == 0.9869
    assert m["epoch"] == 0.01515
    assert m["memory_gib"] == 16.49
    assert (m["step"], m["total_steps"]) == (1, 198)
    assert m["remaining_time"] == "1h 18m 51s"
    assert "eval_loss" not in m


def test_ms_swift_4_quoted_eval_line_keeps_eval_loss():
    m = trainer_metrics.parse_metrics_line(SWIFT4_EVAL)
    assert m is not None
    assert m["eval_loss"] == 0.0481
    assert m["eval_token_acc"] == 0.9881
    assert m["epoch"] == 0.5
    assert m["step"] == 99
    assert "loss" not in m


def test_older_numeric_log_shapes_still_parse():
    # ms-swift 3.x / LLaMA-Factory (HF Trainer dict with numbers)
    m = trainer_metrics.parse_metrics_line(
        "{'loss': 0.4321, 'grad_norm': 1.2, 'learning_rate': 9.5e-05, 'epoch': 0.25, "
        "'global_step/max_steps': '10/40', 'remaining_time': '3m 2s'}"
    )
    assert (m["loss"], m["lr"], m["grad_norm"], m["epoch"], m["step"]) == (0.4321, 9.5e-05, 1.2, 0.25, 10)
    # LLaMA-Factory eval line without a step counter
    m = trainer_metrics.parse_metrics_line("{'eval_loss': 0.3, 'eval_runtime': 5.1, 'epoch': 1.0}")
    assert m is not None and m["eval_loss"] == 0.3 and "loss" not in m
    # PaddleFormers
    m = trainer_metrics.parse_metrics_line("global_step: 20, loss: 0.811, lr: 0.0001, epoch: 0.4")
    assert (m["step"], m["loss"], m["lr"], m["epoch"]) == (20, 0.811, 0.0001, 0.4)
    # Final summary must not become a training point named "loss"
    m = trainer_metrics.parse_metrics_line("{'train_runtime': '100.0', 'train_loss': '0.05'}")
    assert m is None or "loss" not in m
    assert trainer_metrics.parse_metrics_line("Loading checkpoint shards: 100%") is None
