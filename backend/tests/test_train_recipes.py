"""Test M18: registro delle ricette di training per adapter."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import train_recipes


def test_every_trainable_adapter_has_a_recipe_with_doc_and_data_view():
    for recipe in train_recipes.TRAIN_RECIPES.values():
        if not recipe.trainable:
            continue
        assert recipe.official_doc, f"{recipe.adapter_id}: ricetta senza doc ufficiale"
        assert recipe.data_view != "none", f"{recipe.adapter_id}: vista dati assente"
        assert recipe.train_types, f"{recipe.adapter_id}: nessun tipo di training"


def test_closed_model_is_not_trainable():
    # Gemini 4 Argon non ha pesi: non compare fra gli addestrabili.
    assert train_recipes.get_recipe("gemini-argon") is None
    assert "gemini-argon" not in train_recipes.trainable_adapters()


def test_known_trainable_set():
    trainable = set(train_recipes.trainable_adapters())
    assert {
        "monkeyocrv2-parsing", "unlimited-ocr", "qwen3-vl-8b", "paddleocr-vl",
        "deepseek-ocr", "glm-ocr", "mineru2.5", "dots-ocr",
    } <= trainable
    # Chiusi: niente ricetta.
    assert "gemini-argon" not in trainable


def test_monkeyocrv2_is_the_verified_reference():
    recipe = train_recipes.get_recipe("monkeyocrv2-parsing")
    assert recipe is not None and recipe.maturity == "verified"
    assert recipe.toolchain == "ms-swift" and recipe.data_view == "ms-swift-jsonl"


def test_generate_script_dispatches_by_recipe(tmp_path):
    import pytest

    from app.services import trainer_script

    def cfg(adapter_id):
        return {"adapter_id": adapter_id, "train_type": "lora", "model": "m", "model_path": ""}

    script = trainer_script.generate_script(cfg("monkeyocrv2-parsing"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl")
    assert "--model_type monkeyocrv2" in script and "--template monkeyocrv2" in script

    script = trainer_script.generate_script(cfg("qwen3-vl-8b"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl")
    assert "--model_type qwen3_vl" in script

    # Unlimited-OCR: ms-swift ufficiale (model_type/template `unlimited_ocr`).
    script = trainer_script.generate_script(cfg("unlimited-ocr"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl")
    assert "--model_type unlimited_ocr" in script and "--template unlimited_ocr" in script

    # Toolchain non eseguito da questo generatore: fallisce esplicito.
    with pytest.raises(NotImplementedError):
        trainer_script.generate_script(cfg("paddleocr-vl"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl")
    # Modello chiuso: nessuna ricetta.
    with pytest.raises(ValueError):
        trainer_script.generate_script(cfg("gemini-argon"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl")


def test_train_recipes_endpoint():
    with TestClient(app) as client:
        res = client.get("/api/system/train-recipes")
        assert res.status_code == 200
        body = res.json()
        assert "monkeyocrv2-parsing" in body["trainable"]
        assert any(item["adapter_id"] == "monkeyocrv2-parsing" for item in body["items"])
