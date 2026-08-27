"""Test M5: generazione script training, parsing metriche, file split, stato."""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.services import trainer


def test_generate_script_lora(tmp_path: Path):
    train_file = tmp_path / "train.jsonl"
    val_file = tmp_path / "val.jsonl"
    script = trainer.generate_script(
        {"model_path": "/mnt/model", "train_type": "lora", "gpus": "1", "nproc": 2},
        tmp_path / "run",
        train_file,
        val_file,
    )
    assert "--model \"/mnt/model\"" in script
    assert f'--dataset "{train_file}"' in script
    assert f'--val_dataset "{val_file}"' in script
    assert "--train_type 'lora'" in script
    assert "--lora_rank 8" in script
    assert "--freeze_vit true" in script
    assert "CUDA_VISIBLE_DEVICES=\"1\"" in script
    assert "NPROC_PER_NODE=2" in script


def test_generate_script_full_omits_lora(tmp_path: Path):
    script = trainer.generate_script(
        {"model": "zenosai/MonkeyOCRv2-S-Parsing", "train_type": "full", "freeze_vit": False, "learning_rate": 2e-5},
        tmp_path / "run",
        tmp_path / "train.jsonl",
        tmp_path / "val.jsonl",
    )
    assert "--train_type 'full'" in script
    assert "--lora_rank" not in script
    assert "--freeze_vit false" in script
    assert "--learning_rate 2e-05" in script


def test_parse_metrics_line():
    m = trainer.parse_metrics_line("{'loss': 1.2345, 'lr': 1e-05, 'step': 42}")
    assert m is not None
    assert abs(m["loss"] - 1.2345) < 1e-9
    assert abs(m["lr"] - 1e-5) < 1e-12
    assert m["step"] == 42
    assert trainer.parse_metrics_line("downloading model weights...") is None


def test_prepare_training_files(tmp_path: Path):
    # costruisce una finta cartella dataset: progetto 999 sotto LLOYDS_ROOT test
    from app.services.dataset_builder import _project_dir

    base = _project_dir(999) / "dataset"
    base.mkdir(parents=True, exist_ok=True)
    (base / "layout_train.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (base / "layout_val.jsonl").write_text('{"a":2}\n', encoding="utf-8")
    (base / "table_train.jsonl").write_text('{"b":3}\n', encoding="utf-8")

    train_f, val_f = trainer.prepare_training_files(999)
    assert train_f == base / "train.jsonl"
    assert val_f == base / "val.jsonl"
    assert len(train_f.read_text(encoding="utf-8").splitlines()) == 2  # layout+table
    assert len(val_f.read_text(encoding="utf-8").splitlines()) == 1


def test_prepare_training_files_uses_immutable_snapshot(tmp_path: Path):
    from app.services.dataset_builder import _project_dir

    base = _project_dir(997) / "dataset"
    snapshot = base / "snapshots" / "v1"
    snapshot.mkdir(parents=True)
    (snapshot / "layout_train.jsonl").write_text('{"snapshot":1}\n', encoding="utf-8")
    (snapshot / "layout_val.jsonl").write_text('{"snapshot":2}\n', encoding="utf-8")
    (base / "layout_train.jsonl").write_text('{"mutable":1}\n', encoding="utf-8")
    (base / "report.json").write_text(
        json.dumps({"snapshot_id": "v1", "snapshot_dir": str(snapshot)}),
        encoding="utf-8",
    )

    run_dataset = tmp_path / "run" / "dataset"
    train_f, val_f = trainer.prepare_training_files(997, run_dataset)
    assert train_f.parent == run_dataset
    assert train_f.read_text(encoding="utf-8") == '{"snapshot":1}\n'
    assert val_f.read_text(encoding="utf-8") == '{"snapshot":2}\n'


def test_preflight_counts_families_and_blocks_busy_gpu(monkeypatch):
    from app.services.dataset_builder import _project_dir

    base = _project_dir(996) / "dataset"
    base.mkdir(parents=True)
    (base / "layout_train.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (base / "text_rec_train.jsonl").write_text('{"b":1}\n', encoding="utf-8")
    (base / "layout_val.jsonl").write_text('{"c":1}\n', encoding="utf-8")
    monkeypatch.setattr(trainer.config, "TRAIN_REPO", str(Path(__file__).parents[2]))
    monkeypatch.setattr(trainer.config, "TRAIN_PYTHON", os.sys.executable)
    monkeypatch.setattr(
        trainer,
        "gpu_snapshot",
        lambda: [
            {
                "index": "0",
                "name": "RTX 4060",
                "memory_total": 8188,
                "memory_used": 7600,
                "utilization": 0,
                "temp": 50,
            }
        ],
    )

    result = trainer.preflight(996, {"gpus": "0"})
    assert result["dataset"]["counts"] == {"train.jsonl": 2, "val.jsonl": 1}
    assert result["dataset"]["families"]["table"]["train"] == 0
    assert result["gpus"][0]["memory_free"] == 588
    assert result["ready"] is False
    assert any("GPU 0" in error for error in result["errors"])


def test_status_empty_and_gpu(tmp_path: Path):
    # nessuna run attiva -> struttura stabile
    st = trainer._status(999)  # noqa: SLF001
    assert st["active"] is False
    assert isinstance(st["metrics"], list)
    assert isinstance(st["gpu"], list)
    assert "log_tail" in st


def test_start_requires_dataset():
    # progetto senza dataset -> FileNotFoundError propagato
    import pytest

    with pytest.raises((FileNotFoundError, ValueError)):
        trainer.start_run(9980, {})
