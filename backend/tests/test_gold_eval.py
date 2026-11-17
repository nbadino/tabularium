"""Gold-set evaluation on the training GPU: metric, protocol, remote run, failure isolation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.db import connect
from app.domain.otsl import grid_to_otsl
from app.main import app
from app.services import (auto_training, checkpoint_prepare, cloud_manager, custom_models, dataset_builder,
                          evaluate, gold_eval, trainer, training_executor)
from app.services import pages as pagesvc

GRID = {
    "rows": 4, "cols": 3,
    "cells": [
        {"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "Name"},
        {"r": 0, "c": 1, "rowspan": 1, "colspan": 1, "text": "Built"},
        {"r": 0, "c": 2, "rowspan": 1, "colspan": 1, "text": "Tons"},
        {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "Abana"},
        {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "1912"},
        {"r": 1, "c": 2, "rowspan": 1, "colspan": 1, "text": "2310"},
        {"r": 2, "c": 0, "rowspan": 1, "colspan": 1, "text": "Accra"},
        {"r": 2, "c": 1, "rowspan": 1, "colspan": 1, "text": "1926"},
        {"r": 2, "c": 2, "rowspan": 1, "colspan": 1, "text": "9337"},
        {"r": 3, "c": 0, "rowspan": 1, "colspan": 1, "text": "Adda"},
        {"r": 3, "c": 1, "rowspan": 1, "colspan": 1, "text": "1922"},
        {"r": 3, "c": 2, "rowspan": 1, "colspan": 1, "text": "7816"},
    ],
}


def _without_row(grid: dict, row: int) -> dict:
    cells = [dict(c, r=c["r"] - (c["r"] > row)) for c in grid["cells"] if c["r"] != row]
    return {"rows": grid["rows"] - 1, "cols": grid["cols"], "cells": cells}


# --- metric (evaluate.table_metrics is the one owner) --------------------------


def test_identical_table_is_exact():
    metrics = evaluate.table_metrics(GRID, grid_to_otsl(GRID))
    assert metrics["cell_exact"] == 1.0 and metrics["cer"] == 0.0
    assert metrics["missing_rows"] == 0 and metrics["extra_rows"] == 0
    assert metrics["header_structure_ok"] is True and metrics["header_rows"] == 1


def test_a_dropped_row_costs_that_row_only():
    """Rows are aligned before cells are compared: one missing row is three missed cells."""
    metrics = evaluate.table_metrics(GRID, grid_to_otsl(_without_row(GRID, 1)))
    assert metrics["missing_rows"] == 1 and metrics["extra_rows"] == 0
    assert metrics["exact_cells"] == 9 and metrics["reference_cells"] == 12
    assert metrics["character_errors"] == len("Abana") + len("1912") + len("2310")


def test_a_row_shifted_by_one_column_is_wrong():
    shifted = json.loads(json.dumps(GRID))
    for cell in shifted["cells"]:
        if cell["r"] == 2:
            cell["text"] = {0: "", 1: "Accra", 2: "1926"}[cell["c"]]
    metrics = evaluate.table_metrics(GRID, grid_to_otsl(shifted))
    assert metrics["exact_cells"] == 9
    assert metrics["missing_rows"] == 0


def test_header_structure_compares_the_header_cells():
    merged = json.loads(json.dumps(GRID))
    merged["cells"] = [c for c in merged["cells"] if not (c["r"] == 0 and c["c"] == 2)]
    merged["cells"][1]["colspan"] = 2
    assert evaluate.table_metrics(GRID, grid_to_otsl(merged))["header_structure_ok"] is False


# --- scoring: no repair, failures score zero -----------------------------------


def _sample(grid: dict = GRID) -> dict:
    return {"id": "p1_b2", "page_id": 1, "block_id": 2, "image_sha256": "abc",
            "reference_otsl": grid_to_otsl(grid)}


def _output(prediction: str, **extra) -> dict:
    return {"id": "p1_b2", "image_sha256": "abc", "state": "finished", "prediction": prediction,
            "eos_reached": True, "truncated": False, "generated_tokens": 40, "seconds": 2.0, **extra}


@pytest.mark.parametrize(("output", "failure"), [
    (None, "not_run"),
    (_output(grid_to_otsl(GRID), eos_reached=False, truncated=True), "truncated"),
    (_output("I cannot read this table."), "unparsable"),
    ({"id": "p1_b2", "image_sha256": "abc", "state": "failed", "error": "CUDA OOM"}, "execution_failure"),
    (_output(grid_to_otsl(GRID), image_sha256="other"), "image_mismatch"),
])
def test_a_failed_output_scores_zero_on_every_reference_cell(output, failure):
    item = gold_eval.score_sample(_sample(), output)
    assert item["failure"].split(":")[0] == failure
    assert item["metrics"]["exact_cells"] == 0
    assert item["metrics"]["character_errors"] == item["metrics"]["reference_characters"]
    assert item["metrics"]["missing_rows"] == GRID["rows"]
    assert item["metrics"]["header_structure_ok"] is False


def test_only_the_wrapping_of_the_answer_is_removed():
    wrapped = "<think>\n\n</think>\n\n```otsl\n" + grid_to_otsl(GRID) + "\n```"
    item = gold_eval.score_sample(_sample(), _output(wrapped))
    assert "failure" not in item and item["metrics"]["cell_exact"] == 1.0


def test_aggregate_is_micro_over_cells_and_counts_failures():
    good = gold_eval.score_sample(_sample(), _output(grid_to_otsl(GRID)))
    bad = gold_eval.score_sample(_sample(), _output("no table", ))
    total = gold_eval.aggregate([good, bad])
    assert total["exact_cells"] == 12 and total["reference_cells"] == 24
    assert total["cell_exact"] == 0.5
    assert total["failures"] == {"unparsable": 1} and total["failed"] == 1
    assert total["header_structure_ok"] == 1


def test_a_different_stack_is_refused():
    declared = gold_eval.environment("xiaomi-ocr-0")
    gold_eval.check_environment(declared, dict(declared["stack"]))
    with pytest.raises(RuntimeError, match="transformers"):
        gold_eval.check_environment(declared, {**declared["stack"], "transformers": "5.20.2"})


def test_xiaomi_declares_the_measured_protocol():
    """The bounds and prompt are the training recipe's, not new constants."""
    protocol = gold_eval.protocol("xiaomi-ocr-0")
    assert protocol["prompt"] == "Parse the table in the image into OTSL."
    assert protocol["image_size"] == {"shortest_edge": 64 * 32 * 32, "longest_edge": 4096 * 32 * 32}
    assert protocol["chat_template_kwargs"] == {"enable_thinking": False}
    assert protocol["decoding"] == "greedy" and protocol["max_new_tokens"] >= 8192
    env = gold_eval.environment("xiaomi-ocr-0")
    assert env["image_digest"].startswith("sha256:") and env["min_cuda"] == 13.0
    assert gold_eval.environment("monkeyocrv2-parsing") is None


def test_remote_script_is_packaged():
    assert (config.RESOURCE_DIR / "scripts" / "cloud" / gold_eval.SCRIPT).is_file()


# --- gold set from a project ----------------------------------------------------


def _gold_project(tmp_path: Path, *, approve: bool = True, gold: bool = True) -> tuple[int, int]:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    Image.new("RGB", (1000, 1400), (200, 200, 200)).save(archive / "000.png")
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "G", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page_id = client.get(f"/api/projects/{pid}/pages").json()["items"][0]["id"]
        client.put(f"/api/pages/{page_id}/annotations", json={"items": [
            {"label": "Table", "kind": "rect", "points": [[10, 300], [900, 1200]], "content": "", "order_idx": 1},
        ]})
        block = client.get(f"/api/pages/{page_id}/annotations").json()["items"][0]
        client.put(f"/api/blocks/{block['id']}/table", json={**GRID, "phantom_cols": []})
        if gold:
            assert client.post(f"/api/projects/{pid}/gold-set", json=[page_id]).status_code == 200
    if approve:
        with connect() as conn:
            conn.execute("UPDATE pages SET status='approved' WHERE id=?", (page_id,))
    return pid, page_id


def test_plan_says_why_a_run_is_not_evaluated(tmp_path):
    pid, _page = _gold_project(tmp_path / "a")
    assert gold_eval.plan(pid, "xiaomi-ocr-0")["state"] == "planned"
    assert gold_eval.plan(pid, "xiaomi-ocr-0", enabled=False)["reason"] == gold_eval.DISABLED
    assert gold_eval.plan(pid, "monkeyocrv2-parsing")["reason"] == gold_eval.NOT_DECLARED
    unapproved, _ = _gold_project(tmp_path / "b", approve=False)
    assert gold_eval.plan(unapproved, "xiaomi-ocr-0")["reason"] == gold_eval.NO_GOLD_TABLES
    no_gold, _ = _gold_project(tmp_path / "c", gold=False)
    assert gold_eval.plan(no_gold, "xiaomi-ocr-0")["reason"] == gold_eval.NO_GOLD_TABLES
    assert gold_eval.plan(pid, "xiaomi-ocr-0", max_minutes=100000)["max_minutes"] == gold_eval.MAX_MINUTES[1]


def test_gold_crop_is_the_training_crop(tmp_path):
    """Same bytes as the dataset builder's crop of the same block."""
    pid, page_id = _gold_project(tmp_path / "crop")
    run_dir = tmp_path / "run"
    manifest = gold_eval.build_bundle(pid, "xiaomi-ocr-0", run_dir)
    sample = manifest["samples"][0]
    page = dataset_builder.collect_pages_with_blocks(pid)[page_id]["page"]
    (tmp_path / "crops").mkdir()
    training_crop = dataset_builder._write_crop(  # noqa: SLF001
        page, sample["block_id"], [[10, 300], [900, 1200]], tmp_path / "crops", [])
    assert Path(training_crop).read_bytes() == (run_dir / "gold" / "remote" / sample["image"]).read_bytes()
    assert sample["image_sha256"] == hashlib.sha256(Path(training_crop).read_bytes()).hexdigest()
    assert Path(run_dir / "gold" / "remote" / gold_eval.SCRIPT).is_file()
    requests = json.loads((run_dir / "gold" / "remote" / "requests.json").read_text())
    assert "reference_otsl" not in requests["samples"][0]
    assert pagesvc.crop_block_jpeg(page, (10, 300, 900, 1200)) == Path(training_crop).read_bytes()


class _GpuHost(training_executor.SshExecutor):
    """A training host that runs `gold_infer.py` by writing what it would write."""

    def __init__(self, known_hosts: Path, *, stack: dict | None = None, answer: str = ""):
        super().__init__(host="gpu.example", known_hosts=known_hosts)
        self.stack = stack
        self.answer = answer
        self.commands: list[str] = []
        self.uploaded: dict[str, Path] = {}

    def run(self, command, *, timeout=None):
        self.commands.append(command)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="1\n", stderr="")

    def upload(self, local_dir, remote_path, *, log_file=None):
        self.uploaded[remote_path] = local_dir

    def stream(self, command, log_file):
        self.commands.append(command)

        class Done:
            def poll(self):
                return 0

            def wait(self):
                return 0
        return Done()

    def download(self, remote_path, local_dir, *, log_file=None):
        local_dir.mkdir(parents=True, exist_ok=True)
        if remote_path.endswith("/gold"):
            source = self.uploaded[remote_path]
            requests = json.loads((source / "requests.json").read_text())
            (local_dir / "environment.json").write_text(json.dumps({**self.stack, "gpu": "A100"}))
            with (local_dir / "outputs.jsonl").open("w") as out:
                for sample in requests["samples"]:
                    out.write(json.dumps(_output(self.answer, id=sample["id"],
                                                 image_sha256=sample["image_sha256"])) + "\n")
            return
        (local_dir / "config.json").write_text("{}", encoding="utf-8")
        (local_dir / "model.safetensors").write_bytes(b"merged")


def test_evaluate_on_remote_runs_the_declared_interpreter_and_records_everything(tmp_path):
    pid, _page = _gold_project(tmp_path / "remote")
    known = tmp_path / "known_hosts"
    known.write_text("")
    declared = gold_eval.environment("xiaomi-ocr-0")
    host = _GpuHost(known, stack=declared["stack"], answer=grid_to_otsl(GRID))
    run_dir = tmp_path / "run"
    report = gold_eval.evaluate_on_remote(
        executor=host, project_id=pid, adapter_id="xiaomi-ocr-0", run_dir=run_dir,
        remote_run="/tmp/tabularium-runs/run_1", model_remote="/tmp/tabularium-runs/run_1/merged",
        max_minutes=30, log_file=run_dir / "gold_eval.log")
    launch = next(c for c in host.commands if "gold_infer.py" in c)
    assert declared["python"] in launch and "--budget-s 1800" in launch and "timeout -k 30" in launch
    assert report["aggregate"]["cell_exact"] == 1.0 and report["complete"] is True
    assert report["environment"]["image_digest"] == declared["image_digest"]
    assert report["environment"]["torch"] == declared["stack"]["torch"]
    full = json.loads((run_dir / "gold_eval.json").read_text())
    assert full["outputs"][0]["prediction"] == grid_to_otsl(GRID)


def test_evaluate_on_remote_refuses_another_stack(tmp_path):
    pid, _page = _gold_project(tmp_path / "stack")
    known = tmp_path / "known_hosts"
    known.write_text("")
    host = _GpuHost(known, stack={"torch": "2.6.0+cu124", "transformers": "5.20.2"}, answer=grid_to_otsl(GRID))
    with pytest.raises(RuntimeError, match="differs from the declared"):
        gold_eval.evaluate_on_remote(
            executor=host, project_id=pid, adapter_id="xiaomi-ocr-0", run_dir=tmp_path / "run",
            remote_run="/r/run_1", model_remote="/r/run_1/merged", max_minutes=30,
            log_file=tmp_path / "gold.log")


# --- prepare: evaluation before download, never at the model's expense ---------


def _xiaomi_run(project_id: int, **cfg) -> Path:
    run_dir = config.DATA_DIR / str(project_id) / "runs" / "run_1700000000001"
    checkpoint = run_dir / "checkpoints" / "xiaomi-ocr-0_lora" / "checkpoint-3"
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / "adapter_config.json").write_text("{}", encoding="utf-8")
    (checkpoint / "adapter_model.safetensors").write_bytes(b"lora")
    meta = {"run_id": run_dir.name, "state": "finished", "adapter_id": "xiaomi-ocr-0", "toolchain": "ms-swift",
            "config": {"adapter_id": "xiaomi-ocr-0", "train_type": "lora", "executor": "vast",
                       "ssh_host": "gpu.example", **cfg}}
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    return run_dir


@pytest.mark.parametrize("outcome", ["finished", "failed"])
def test_prepare_evaluates_before_download_and_keeps_the_model_when_eval_fails(monkeypatch, tmp_path, outcome):
    pid, _page = _gold_project(tmp_path / outcome)
    run_dir = _xiaomi_run(pid, gold_eval={"enabled": True, "max_minutes": 20})
    known = tmp_path / "known_hosts"
    known.write_text("")
    host = _GpuHost(known, stack=gold_eval.environment("xiaomi-ocr-0")["stack"], answer=grid_to_otsl(GRID))
    monkeypatch.setattr(checkpoint_prepare, "executor_from_config", lambda cfg, known_hosts=None: host)
    monkeypatch.setattr(config, "SSH_KNOWN_HOSTS", known)
    order: list[str] = []
    real = gold_eval.evaluate_on_remote

    def evaluate(**kwargs):
        order.append("gold")
        if outcome == "failed":
            raise RuntimeError("CUDA driver too old")
        return real(**kwargs)

    monkeypatch.setattr(gold_eval, "evaluate_on_remote", evaluate)
    monkeypatch.setattr(custom_models, "register_training_checkpoint",
                        lambda *a, **k: order.append("register") or {"id": "m1", "display_name": "M1"})
    plan = checkpoint_prepare.prepare_plan(pid, run_dir.name)
    meta = json.loads((run_dir / "run.json").read_text())
    model = checkpoint_prepare._prepare_remote(pid, run_dir.name, plan, run_dir, meta, {"state": "running"})  # noqa: SLF001
    assert model["id"] == "m1" and order == ["gold", "register"]
    saved = json.loads((run_dir / "run.json").read_text())["gold_eval"]
    assert saved["state"] == outcome
    if outcome == "failed":
        assert "CUDA driver too old" in saved["error"]
    else:
        assert saved["aggregate"]["cell_exact"] == 1.0 and saved["pages"][0]["page_id"] == _page
        summary = trainer.list_runs(pid)[0]["gold_eval"]
        assert summary["aggregate"]["tables"] == 1 and "pages" not in summary


def test_prepare_without_the_option_does_not_evaluate(monkeypatch, tmp_path):
    pid, _page = _gold_project(tmp_path / "off")
    run_dir = _xiaomi_run(pid)
    known = tmp_path / "known_hosts"
    known.write_text("")
    host = _GpuHost(known, stack={}, answer="")
    monkeypatch.setattr(checkpoint_prepare, "executor_from_config", lambda cfg, known_hosts=None: host)
    monkeypatch.setattr(config, "SSH_KNOWN_HOSTS", known)
    monkeypatch.setattr(gold_eval, "evaluate_on_remote", lambda **k: pytest.fail("evaluated"))
    monkeypatch.setattr(custom_models, "register_training_checkpoint", lambda *a, **k: {"id": "m1"})
    plan = checkpoint_prepare.prepare_plan(pid, run_dir.name)
    meta = json.loads((run_dir / "run.json").read_text())
    checkpoint_prepare._prepare_remote(pid, run_dir.name, plan, run_dir, meta, {"state": "running"})  # noqa: SLF001
    assert "gold_eval" not in json.loads((run_dir / "run.json").read_text())


# --- one-click job: the rental boots the evaluation image ----------------------


def _rent(monkeypatch, gold: dict) -> tuple[dict, dict]:
    seen: dict = {}
    rented: dict = {}

    def search(*_a, **kwargs):
        seen.update(kwargs)
        return [{"id": 5, "gpu_name": "A100", "dph_total": 0.5, "cuda_max_good": 13.0, "compute_cap": 800}]

    monkeypatch.setattr(cloud_manager, "search_vast_offers", search)
    monkeypatch.setattr(cloud_manager, "rent_vast_instance",
                        lambda *a, **k: rented.update(k) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(auto_training, "_save", lambda _job: None)
    job = {"id": "auto_9", "project_id": 4, "adapter_id": "xiaomi-ocr-0", "state": "queued", "phase": "rent",
           "message": "", "offer": None, "instance_id": None, "gold_eval": gold}
    auto_training._run(  # noqa: SLF001
        job, {"adapter_id": "xiaomi-ocr-0", "train_type": "lora"}, "key", "qwen", 1, "it", 1.5, 40, 120,
        threading.Event(), gold=gold)
    return seen, rented


def test_a_gold_evaluated_job_rents_the_evaluation_image(monkeypatch):
    seen, rented = _rent(monkeypatch, {"state": "planned", "max_minutes": 60})
    env = gold_eval.environment("xiaomi-ocr-0")
    assert rented["image"] == env["image"]
    assert seen["min_cuda"] == env["min_cuda"]
    assert seen["blackwell_ready"] is False  # training still runs the qwen image's torch


def test_a_job_without_gold_evaluation_rents_the_training_image(monkeypatch):
    seen, rented = _rent(monkeypatch, {"state": "skipped", "reason": gold_eval.DISABLED})
    assert rented["image"] == auto_training.PREBUILT_IMAGES["qwen"]
    assert seen["min_cuda"] == auto_training.PREBUILT_CUDA["qwen"]


def test_auto_api_passes_the_option_and_the_cap(monkeypatch, tmp_path):
    from app.services import vault

    captured: dict = {}
    monkeypatch.setattr(vault, "get", lambda ref: "key")
    monkeypatch.setattr(auto_training, "start_auto", lambda *a, **k: captured.update(k) or {"id": "auto_1", "adapter_id": "xiaomi-ocr-0", "profile": "qwen"})
    pid, _page = _gold_project(tmp_path / "api")
    with TestClient(app) as client:
        body = {"config": {"adapter_id": "xiaomi-ocr-0"}, "gold_eval": False, "gold_eval_max_minutes": 15}
        assert client.post(f"/api/projects/{pid}/training/auto", json=body).status_code == 200
        assert captured["gold_eval_enabled"] is False and captured["gold_eval_max_minutes"] == 15
        client.post(f"/api/projects/{pid}/training/auto", json={"config": {"adapter_id": "xiaomi-ocr-0"}})
        assert captured["gold_eval_enabled"] is True
