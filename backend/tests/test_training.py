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
    # costruisce una finta cartella dataset: progetto 999 sotto TABULARIUM_ROOT test
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


# --------------------------------------------------------------------------
# Stima VRAM: il preflight deve sapere se la configurazione entra nella GPU
# --------------------------------------------------------------------------

from app.services import vram  # noqa: E402


def test_the_dominant_term_is_the_vocabulary_not_the_weights():
    """Il modello è da 0,7B, ma non sono i pesi a riempire la scheda.

    `MonkeyOCRv2-B-Parsing` ha `vocab_size` 151936 su `hidden_size` 1024: la
    matrice dei logit è 148 volte più larga dello stato nascosto, quindi a
    lunghezza piena è lei a dominare. È il motivo per cui «è un modello
    piccolo, ci sta comodo» è un'intuizione sbagliata.
    """
    estimate = vram.estimate({"batch_size": 1, "max_length": 16384})
    assert estimate.terms["logits"] > estimate.terms["weights"]
    assert estimate.terms["logits"] > estimate.terms["activations_saved"]


def test_the_official_preset_does_not_fit_an_eight_gigabyte_card():
    """`batch_size 4` + `max_length 16384` chiede oltre 20 GB: non è un preset da 8 GB."""
    official = {"batch_size": 4, "max_length": 16384, "train_type": "lora"}
    estimate = vram.estimate(official)
    assert estimate.total_mib > 20 * 1024
    assert not vram.fits(estimate.total_mib, 8188)


def test_batch_one_at_half_length_fits_an_eight_gigabyte_card():
    """La configurazione che ci sta esiste, ed è quella da consigliare."""
    estimate = vram.estimate({"batch_size": 1, "max_length": 8192})
    assert vram.fits(estimate.total_mib, 8188)


def test_full_sft_does_not_fit_an_eight_gigabyte_card_even_short():
    """Full-SFT non è una strada su 8 GB, e non lo diventa accorciando."""
    estimate = vram.estimate(
        {"batch_size": 1, "max_length": 2048, "train_type": "full"}
    )
    assert not vram.fits(estimate.total_mib, 8188)


def test_the_suggested_length_is_the_largest_that_actually_fits():
    """Il consiglio non può contraddire la stima che lo ha prodotto."""
    free = 8188
    suggested = vram.largest_fitting_length(
        {"batch_size": 4, "max_length": 16384}, free
    )
    assert suggested > 0
    fitting = vram.estimate({"batch_size": 1, "max_length": suggested})
    assert vram.fits(fitting.total_mib, free)
    # E il gradino successivo non deve entrare, altrimenti è un consiglio timido.
    larger = vram.estimate({"batch_size": 1, "max_length": suggested + 512})
    assert not vram.fits(larger.total_mib, free)


def test_estimate_scales_linearly_with_the_tokens_of_a_step():
    """Raddoppiando i token di uno step, i termini che contano raddoppiano."""
    one = vram.estimate({"batch_size": 1, "max_length": 4096})
    two = vram.estimate({"batch_size": 2, "max_length": 4096})
    assert two.terms["logits"] == 2 * one.terms["logits"]
    assert two.terms["activations_saved"] == 2 * one.terms["activations_saved"]
    # I pesi no: sono gli stessi.
    assert two.terms["weights"] == one.terms["weights"]


def _free_gpu(monkeypatch, used_mib: int = 0) -> None:
    """Finge una 4060 da 8 GB con `used_mib` occupati."""
    monkeypatch.setattr(
        trainer,
        "gpu_snapshot",
        lambda: [
            {
                "index": "0",
                "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
                "memory_total": 8188,
                "memory_used": used_mib,
                "utilization": 0,
                "temp": 45,
            }
        ],
    )


def test_preflight_blocks_the_official_preset_on_an_eight_gigabyte_card(monkeypatch):
    """Il costo dell'errore è mezz'ora di download: va detto prima, non in OOM."""
    _free_gpu(monkeypatch)
    out = trainer.preflight(
        1, {"batch_size": 4, "max_length": 16384, "train_type": "lora", "gpus": "0"}, "it"
    )
    blocking = [e for e in out["errors"] if "VRAM" in e]
    assert blocking, out["errors"]
    # Deve dire *cosa fare*, non solo che non ci sta.
    assert "batch_size 1" in blocking[0]
    assert "max_length" in blocking[0]


def test_preflight_lets_the_eight_gigabyte_preset_through(monkeypatch):
    """E non deve bloccare la configurazione che invece ci sta."""
    _free_gpu(monkeypatch)
    out = trainer.preflight(
        1,
        {"batch_size": 1, "max_length": 8192, "grad_accum": 4, "train_type": "lora", "gpus": "0"},
        "it",
    )
    assert not [e for e in out["errors"] if "VRAM" in e]


def test_preflight_says_free_the_gpu_when_something_else_holds_it(monkeypatch):
    """Con vLLM acceso il consiglio non è «accorcia», è «libera la scheda»."""
    _free_gpu(monkeypatch, used_mib=7266)
    out = trainer.preflight(
        1, {"batch_size": 1, "max_length": 8192, "train_type": "lora", "gpus": "0"}, "it"
    )
    assert [w for w in out["warnings"] if "vLLM" in w]
