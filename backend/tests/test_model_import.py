"""Offline installation: a checkpoint from a directory must look like a download."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app import config
from app.services import model_registry

ADAPTER = "glm-ocr"


def _checkpoint(parent: Path, name: str = "checkpoint") -> Path:
    source = parent / name
    source.mkdir(parents=True, exist_ok=True)
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "model.safetensors").write_bytes(b"weights")
    return source


@pytest.fixture(autouse=True)
def _clean_installed_model():
    """Keep the shared test data root clean: this suite installs a real model directory."""
    yield
    target = config.MODELS_DIR / ADAPTER
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def test_import_installs_a_checkpoint(tmp_path: Path) -> None:
    state = model_registry.import_local(ADAPTER, _checkpoint(tmp_path))
    assert state["installed"] is True
    assert (config.MODELS_DIR / ADAPTER / "config.json").is_file()
    assert (config.MODELS_DIR / ADAPTER / "model.safetensors").is_file()


def test_a_second_import_needs_force(tmp_path: Path) -> None:
    source = _checkpoint(tmp_path)
    model_registry.import_local(ADAPTER, source)
    with pytest.raises(ValueError, match="--force"):
        model_registry.import_local(ADAPTER, source)
    assert model_registry.import_local(ADAPTER, source, force=True)["installed"] is True


def test_a_directory_without_config_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-checkpoint"
    empty.mkdir()
    with pytest.raises(ValueError, match="config.json"):
        model_registry.import_local(ADAPTER, empty)


def test_a_directory_without_weights_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "config-only"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="weights"):
        model_registry.import_local(ADAPTER, source)


def test_an_unknown_adapter_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        model_registry.import_local("no-such-model", _checkpoint(tmp_path))


def test_a_failed_import_leaves_no_partial_installation(tmp_path: Path, monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(model_registry.shutil, "copytree", explode)
    with pytest.raises(OSError):
        model_registry.import_local(ADAPTER, _checkpoint(tmp_path))
    assert not model_registry.is_installed(ADAPTER)


def test_the_cli_imports_and_lists(tmp_path: Path, capsys) -> None:
    from app.cli import main

    source = _checkpoint(tmp_path, "for-the-cli")
    assert main(["models", "import", str(source), "--adapter", ADAPTER]) == 0
    assert "installed" in capsys.readouterr().out

    assert main(["models", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(row["adapter_id"] == ADAPTER and row["installed"] for row in payload)
