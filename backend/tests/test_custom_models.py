"""Test per i modelli 'a piacere' (repo Hugging Face libero, come LM Studio):
nessun blocco per dimensione, solo un avviso (v. model_registry.vram_warning)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import custom_models, model_adapters, model_registry


def setup_module(_module) -> None:
    db.init_db()


def test_create_validates_hf_repo_format():
    with pytest.raises(ValueError, match="hf_repo"):
        custom_models.create({"hf_repo": "not-a-valid-repo"})


def test_create_then_resolves_as_a_generic_adapter():
    row = custom_models.create({
        "display_name": "Il mio modello",
        "hf_repo": "someorg/some-vlm",
        "trust_remote_code": True,
        "max_model_len": 8192,
        "gpu_memory_utilization": 0.8,
        "extra_args": "--dtype bfloat16",
    })
    assert row["id"].startswith("custom-")

    adapter = model_adapters.get_adapter(row["id"])
    assert adapter.capabilities.hf_repo == "someorg/some-vlm"
    assert adapter.capabilities.display_name == "Il mio modello"

    cmd = adapter.serve_command("/weights/x", 8888)
    assert cmd[:3] == ["vllm", "serve", "/weights/x"]
    assert "--trust-remote-code" in cmd
    assert "--max-model-len" in cmd and "8192" in cmd
    assert "--dtype" in cmd and "bfloat16" in cmd

    # Nessun protocollo OCR verificato: prompt/parsing restano non implementati.
    with pytest.raises(NotImplementedError):
        adapter.prompt_for("text")

    custom_models.delete(row["id"])


def test_duplicate_display_name_gets_a_unique_id():
    first = custom_models.create({"display_name": "Stesso Nome", "hf_repo": "a/b"})
    second = custom_models.create({"display_name": "Stesso Nome", "hf_repo": "c/d"})
    assert first["id"] != second["id"]
    custom_models.delete(first["id"])
    custom_models.delete(second["id"])


def test_unknown_custom_model_raises_on_delete():
    with pytest.raises(ValueError):
        custom_models.delete("custom-does-not-exist")


def test_list_models_api_includes_custom_models():
    row = custom_models.create({"display_name": "Custom Visibile", "hf_repo": "org/repo"})
    try:
        with TestClient(app) as client:
            res = client.get("/api/models")
            assert res.status_code == 200
            items = {item["adapter_id"]: item for item in res.json()["items"]}
            assert row["id"] in items
            assert items[row["id"]]["download_only"] is False
            assert items[row["id"]]["local_serve_ready"] is True
    finally:
        custom_models.delete(row["id"])


def test_add_and_remove_custom_model_via_api():
    with TestClient(app) as client:
        res = client.post("/api/models/custom", json={"display_name": "API Model", "hf_repo": "org/api-model"})
        assert res.status_code == 200
        adapter_id = res.json()["id"]

        listed = client.get("/api/models").json()["items"]
        assert any(item["adapter_id"] == adapter_id for item in listed)

        res = client.delete(f"/api/models/custom/{adapter_id}")
        assert res.status_code == 200

        listed_after = client.get("/api/models").json()["items"]
        assert not any(item["adapter_id"] == adapter_id for item in listed_after)


def test_add_custom_model_rejects_invalid_repo_via_api():
    with TestClient(app) as client:
        res = client.post("/api/models/custom", json={"hf_repo": "invalid"})
        assert res.status_code == 400


def test_vram_warning_flags_undersized_gpu(monkeypatch):
    adapter = model_adapters.get_adapter("monkeyocrv2-parsing")
    monkeypatch.setattr(
        "app.services.trainer_metrics.gpu_snapshot",
        lambda: [{"memory_total": 8192, "memory_used": 7000}],
    )
    # MonkeyOCRv2 pesa ~1.5 GB dichiarati: entra comodamente anche in 1.2 GB liberi? No:
    # 1.5 * 1024 * 1.35 ≈ 2073 MiB > 1192 MiB liberi -> warning atteso.
    warning = model_registry.vram_warning(adapter)
    assert warning is not None
    assert "GB" in warning


def test_vram_warning_silent_when_plenty_of_room(monkeypatch):
    adapter = model_adapters.get_adapter("monkeyocrv2-parsing")
    monkeypatch.setattr(
        "app.services.trainer_metrics.gpu_snapshot",
        lambda: [{"memory_total": 24576, "memory_used": 0}],
    )
    assert model_registry.vram_warning(adapter) is None


def test_vram_warning_silent_without_gpu_telemetry(monkeypatch):
    adapter = model_adapters.get_adapter("monkeyocrv2-parsing")
    monkeypatch.setattr("app.services.trainer_metrics.gpu_snapshot", lambda: [])
    assert model_registry.vram_warning(adapter) is None
