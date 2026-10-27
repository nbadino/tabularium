"""Post-training: guardia noleggio e comandi che non sono ms-swift."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app import config
from app.api import cloud
from app.services import custom_models, train_recipes


def test_rent_training_refuses_a_non_monkey_recipe(monkeypatch):
    monkeypatch.setattr(cloud, "_credential", lambda payload: "vast-key")
    with pytest.raises(HTTPException) as exc:
        cloud.rent_training({"adapter_id": "glm-ocr"}, _admin={"id": 1})
    assert exc.value.status_code == 400
    assert "MonkeyOCRv2" in exc.value.detail


def test_rent_training_uses_the_vault_key_by_default(monkeypatch):
    from app.services import vault

    monkeypatch.setattr(vault, "get", lambda ref: "vast-key")
    monkeypatch.setattr(cloud, "_credential", lambda payload: "")
    with pytest.raises(HTTPException) as exc:
        cloud.rent_training({"adapter_id": "glm-ocr"}, _admin={"id": 1})
    # Non è più "API Key obbligatoria": la chiave è stata risolta dal vault,
    # poi il noleggio manuale rifiuta il modello non-Monkey.
    assert exc.value.status_code == 400
    assert "API Key" not in exc.value.detail


def test_apple_swift_is_not_ms_swift(tmp_path, monkeypatch):
    binary = tmp_path / "swift"
    binary.write_bytes(b"\xcf\xfa\xed\xfe")  # Mach-O, non uno script pip
    monkeypatch.setattr(config, "TRAIN_PYTHON", "")
    monkeypatch.setattr(custom_models.shutil, "which", lambda command: str(binary) if command == "swift" else None)
    status = custom_models.toolchain_status(train_recipes.get_recipe("qwen3-vl-8b"))
    assert status["available"] is False
