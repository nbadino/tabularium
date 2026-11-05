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
