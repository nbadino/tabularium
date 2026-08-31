from __future__ import annotations

import json
from pathlib import Path

from app import db
from app.services.training_executor import TrainingRecipe


def test_recipe_manifest_hashes_script_and_dataset(tmp_path: Path):
    db.init_db()
    run = tmp_path / "run"
    run.mkdir()
    script = run / "train.sh"
    train = run / "train.jsonl"
    val = run / "val.jsonl"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    train.write_text('{"x":1}\n', encoding="utf-8")
    val.write_text('{"x":2}\n', encoding="utf-8")

    recipe = TrainingRecipe("run_1", run, script, train, val, "v0003", {"epochs": 1})
    manifest_path = recipe.write_manifest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["dataset_snapshot_id"] == "v0003"
    assert set(manifest["files"]) == {"train.sh", "train.jsonl", "val.jsonl"}
    assert len(manifest["files"]["train.jsonl"]["sha256"]) == 64
