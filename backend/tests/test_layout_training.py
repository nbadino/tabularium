"""PP-DocLayoutV3 layout fine-tuning: recipe, PaddleX view, script, preflight, checkpoint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.models import pp_doclayoutv3 as detector
from app.services import auto_training, checkpoint_prepare, train_recipes, trainer, trainer_script
from app.services.dataset_builder import _project_dir

CFG = {"adapter_id": detector.DETECTOR_ID, "train_type": "full"}


def _snapshot(project_id: int, tmp_path: Path, labels=("Title", "Table")) -> Path:
    base = _project_dir(project_id) / "dataset"
    base.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val"):
        image = tmp_path / f"{project_id}_{split}.png"
        Image.new("RGB", (2000, 1000), "white").save(image)
        items = [
            {"bbox": [0, 0, 500, 100], "label": labels[0]},
            {"bbox": [0, 100, 1000, 1000], "label": labels[1]},
        ]
        row = {"messages": [{"role": "user", "content": "<image>layout"},
                            {"role": "assistant", "content": json.dumps(items)}],
               "images": [str(image)]}
        (base / f"layout_{split}.jsonl").write_text((json.dumps(row) + "\n") * 2, encoding="utf-8")
    return base


def test_the_detector_recipe_is_declared_and_runnable():
    recipe = train_recipes.get_recipe(detector.DETECTOR_ID)
    assert recipe.toolchain == "paddlex" and recipe.data_view == "paddlex-layout"
    assert recipe.train_types == ("full",) and recipe.families == ("layout",)
    assert recipe.trainable and recipe.script_ready and not recipe.merge_supported
    assert recipe.flags["num_classes"] == 25 == len(detector.CATEGORIES)
    assert auto_training.profile_for(detector.DETECTOR_ID) == "layout"
    assert auto_training.PREBUILT_IMAGES["layout"].endswith(":layout-v1")


def test_the_view_is_a_paddlex_layout_dataset(tmp_path):
    _snapshot(990, tmp_path)
    run = tmp_path / "run" / "dataset"
    train, val = trainer.prepare_training_files(990, run, adapter_id=detector.DETECTOR_ID, train_type="full")
    assert train == run / "annotations" / "instance_train.json"
    assert val == run / "annotations" / "instance_val.json"
    coco = json.loads(train.read_text(encoding="utf-8"))
    image = coco["images"][0]
    assert (run / "images" / image["file_name"]).is_file()
    assert (run / "images_mask" / image["file_name"]).is_file()
    assert (image["width"], image["height"]) == (2000, 1000)
    title, table = [a for a in coco["annotations"] if a["image_id"] == image["id"]]
    # 0-1000 grid back to pixels of the page image.
    assert title["bbox"] == [0.0, 0.0, 1000.0, 100.0]
    assert title["category_id"] == detector.CATEGORIES.index("doc_title")
    assert table["category_id"] == detector.CATEGORIES.index("table")
    assert [title["read_order"], table["read_order"]] == [0, 1]
    assert json.loads((run / "view.json").read_text())["counts"]["layout"] == {"train": 2, "val": 2}


def test_a_label_without_category_stops_the_view(tmp_path):
    _snapshot(991, tmp_path, labels=("Title", "Marginalia"))
    with pytest.raises(ValueError, match="Marginalia"):
        trainer.prepare_training_files(991, tmp_path / "run", adapter_id=detector.DETECTOR_ID, train_type="full")


def test_the_script_runs_the_vendor_modes_and_keeps_both_evaluations(tmp_path):
    run = tmp_path / "run_1"
    train = run / "dataset" / "annotations" / "instance_train.json"
    script = trainer_script.generate_script(CFG, run, train, train.with_name("instance_val.json"))
    assert f"CONFIG={detector.PADDLEX_CONFIG}" in script
    order = [script.index(mode) for mode in (
        "Global.mode=check_dataset", "pdx_eval eval_base", "Global.mode=train", "pdx_eval eval_trained")]
    assert order == sorted(order)
    assert "Train.num_classes=25" in script
    # Fetched once into the shared PaddleX cache: evaluate needs a local file.
    assert detector.PRETRAINED_WEIGHTS in script
    assert 'WEIGHTS="${PADDLE_PDX_CACHE_HOME:-$HOME/.paddlex}/official_pretrained_model/' in script
    assert 'Evaluate.weight_path="$WEIGHTS"' in script and 'Train.pretrain_weight_path="$WEIGHTS"' in script
    assert "Train.epochs_iters=100" in script and "Train.learning_rate=0.0001" in script
    assert f"DATA={run / 'dataset'}" in script
    assert "is_compiled_with_cuda" in script  # GPU when available, else the registered CPU
    remote = trainer_script.generate_script(
        {**CFG, "executor": "ssh", "ssh_train_repo": "/opt/tabularium/PaddleX"}, run, train, train)
    assert 'DATA="$RUN_DIR/dataset"' in remote
    assert 'OUT="$RUN_DIR/checkpoints/pp-doclayoutv3_full"' in remote
    with pytest.raises(NotImplementedError):
        trainer_script.generate_script({**CFG, "resume_run_id": "run_0"}, run, train, train)


def test_preflight_needs_paddlex_but_not_a_gpu(monkeypatch, tmp_path):
    from app.services import hardware

    _snapshot(992, tmp_path)
    monkeypatch.setattr(trainer, "gpu_snapshot", lambda: [])
    monkeypatch.setattr(hardware, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(trainer.config, "TRAIN_PYTHON", "")
    monkeypatch.setattr(trainer.config, "PADDLEX_DIR", "")
    check = trainer.preflight(992, CFG, lang="en")
    assert any("TABULARIUM_PADDLEX_DIR" in error for error in check["errors"])
    (tmp_path / "PaddleX").mkdir()
    (tmp_path / "PaddleX" / "main.py").write_text("")
    monkeypatch.setattr(trainer.config, "PADDLEX_DIR", str(tmp_path / "PaddleX"))
    check = trainer.preflight(992, CFG, lang="en")
    assert check["ready"], check["errors"]
    assert any("CPU" in warning for warning in check["warnings"])
    # Apple Silicon CPU: measured to diverge at the first step, refused rather than warned.
    monkeypatch.setattr(hardware, "is_apple_silicon", lambda: True)
    check = trainer.preflight(992, CFG, lang="en")
    assert not check["ready"]
    assert any("Apple Silicon" in error for error in check["errors"])


def test_a_layout_run_has_nothing_to_prepare(tmp_path):
    run_dir = trainer.runs_dir(993) / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"state": "finished", "adapter_id": detector.DETECTOR_ID,
                                                  "toolchain": "paddlex", "config": CFG}))
    with pytest.raises(ValueError, match="Xiaomi-OCR-0"):
        checkpoint_prepare.prepare_plan(993, "run_1")
    assert trainer.checkpoint_info(run_dir) is None  # not exported yet
