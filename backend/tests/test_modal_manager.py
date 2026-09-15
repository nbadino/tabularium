from __future__ import annotations

import json

from app import db
from app.services import custom_models, modal_manager


def test_modal_status_recovers_persisted_task_after_backend_restart(tmp_path, monkeypatch):
    db.init_db()
    log = tmp_path / "modal.log"
    log.write_text("deploying\nloading weights\n", encoding="utf-8")
    with db.connect() as conn:
        conn.execute("DELETE FROM jobs WHERE kind='modal'")
        owner = conn.execute(
            "INSERT INTO users(username, password_hash, salt, role) VALUES(?, ?, ?, 'admin')",
            ("modal-owner", "hash", "salt"),
        )
        owner_id = owner.lastrowid
        cur = conn.execute(
            "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, command_json, log_path) "
            "VALUES('modal', ?, 'modal', 4242, 4242, 'running', ?, ?)",
            (owner_id, json.dumps({"kind": "deploy", "template_id": "monkeyocrv2", "args": []}), str(log)),
        )
        job_id = cur.lastrowid

    monkeypatch.setattr(modal_manager.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(modal_manager, "_task", None)
    monkeypatch.setattr(modal_manager, "_query_modal_cli", lambda template_id: {
        "workspace": None, "app_state": None, "endpoint": None,
    })
    monkeypatch.setattr(modal_manager, "token_configured", lambda: False)
    monkeypatch.setattr(modal_manager, "_find_modal", lambda: None)

    status = modal_manager.status("monkeyocrv2")

    assert status["task"] == {
        "kind": "deploy",
        "done": False,
        "ok": None,
        "log": ["deploying", "loading weights"],
    }
    assert modal_manager._persisted_running()["owner_id"] == owner_id
    with db.connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def test_modal_stop_cancels_recovered_deploy(monkeypatch):
    db.init_db()
    with db.connect() as conn:
        owner = conn.execute(
            "INSERT INTO users(username, password_hash, salt, role) VALUES(?, ?, ?, 'admin')",
            ("modal-stop-owner", "hash", "salt"),
        )
        cur = conn.execute(
            "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, command_json) "
            "VALUES('modal', ?, 'modal', 5151, 5151, 'running', ?)",
            (owner.lastrowid, json.dumps({"kind": "deploy", "template_id": "monkeyocrv2"})),
        )
        job_id = cur.lastrowid

    killed = []
    monkeypatch.setattr(modal_manager.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(modal_manager.os, "killpg", lambda group, sig: killed.append((group, sig)))

    modal_manager._cancel_deploy_if_running("monkeyocrv2")

    assert killed == [(5151, 15)]
    with db.connect() as conn:
        assert conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0] == "stopped"
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def test_modal_checkpoint_deploy_uploads_complete_model_to_named_volume(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    checkpoint = data_dir / "1" / "runs" / "run-1" / "checkpoints" / "checkpoint-500"
    checkpoint.mkdir(parents=True)
    (checkpoint / "config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "model.safetensors").write_bytes(b"weights")

    monkeypatch.setattr(modal_manager.config, "DATA_DIR", data_dir)
    monkeypatch.setattr(custom_models, "get", lambda adapter_id: {
        "id": adapter_id,
        "source_path": str(checkpoint),
        "served_model_name": "tabularium-hsi-tuned",
    })
    monkeypatch.setattr(modal_manager, "_find_modal", lambda: "/venv/bin/modal")
    captured = {}

    def fake_start(kind, args, **kwargs):
        captured.update(kind=kind, args=args, kwargs=kwargs)

    monkeypatch.setattr(modal_manager, "_start", fake_start)

    modal_manager.start_checkpoint_deploy("custom-hsi-tuned", keep_warm=True, owner_id=7)

    assert captured["kind"] == "deploy"
    assert captured["args"][0].endswith("modal_publish_checkpoint.sh")
    assert captured["args"][1:] == [
        "monkeyocrv2-weights",
        str(checkpoint),
        "checkpoints/custom-hsi-tuned",
        str(modal_manager.REPO_ROOT / "scripts" / "cloud" / "modal_vllm.py"),
    ]
    assert captured["kwargs"]["executable"] == "/bin/bash"
    assert captured["kwargs"]["owner_id"] == 7
