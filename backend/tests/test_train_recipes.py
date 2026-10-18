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


def test_every_toolchain_uses_its_vendor_documented_recipe():
    """Il toolchain non è un dettaglio: è quello che la doc del modello usa."""
    assert train_recipes.get_recipe("monkeyocrv2-parsing").toolchain == "ms-swift"
    # GLM-OCR: il vendor documenta LLaMA-Factory, non ms-swift.
    assert train_recipes.get_recipe("glm-ocr").toolchain == "llama-factory"
    # PaddleOCR-VL: PaddleFormers (full + LoRA), non più ERNIEKit full-only.
    paddle = train_recipes.get_recipe("paddleocr-vl")
    assert paddle.toolchain == "paddleformers"
    assert set(paddle.train_types) == {"lora", "full"}
    # MinerU: il vendor vieta `swift sft`; il config ufficiale non è pubblico.
    mineru = train_recipes.get_recipe("mineru2.5")
    assert mineru.toolchain == "mineru-sft"
    assert not mineru.script_ready
    # Nessun modello senza codice di training viene dichiarato addestrabile.
    for adapter_id in ("teleocr", "lightonocr-3"):
        recipe = train_recipes.get_recipe(adapter_id)
        assert recipe is not None and not recipe.trainable


def test_end2end_only_adapters_get_a_coherent_training_prompt():
    """Unlimited-OCR e dots.mocr non hanno prompt per-task: il builder non deve esplodere."""
    from app.services.dataset_builder import _training_prompt
    from app.services.model_adapters import get_adapter

    for adapter_id in ("unlimited-ocr", "dots-ocr"):
        adapter = get_adapter(adapter_id)
        recipe = train_recipes.get_recipe(adapter_id)
        prompt = _training_prompt(recipe, adapter, "table")
        assert prompt.startswith("Please extract the table")
    # Qwen3-VL: il preset full-page `qwenvl html` non è un prompt di training
    # per un ritaglio con target OTSL.
    qwen = get_adapter("qwen3-vl-8b")
    assert _training_prompt(train_recipes.get_recipe("qwen3-vl-8b"), qwen, "table").startswith(
        "Please extract the table"
    )


def test_official_hyperparameters_are_per_type():
    """GLM e PaddleFormers documentano lr diversi per full e LoRA."""
    glm = train_recipes.get_recipe("glm-ocr")
    assert glm.defaults_for("lora")["learning_rate"] == 1e-4
    assert glm.defaults_for("full")["learning_rate"] == 1e-5
    paddle = train_recipes.get_recipe("paddleocr-vl")
    assert paddle.defaults_for("lora")["learning_rate"] == 5e-4
    assert paddle.defaults_for("full")["learning_rate"] == 5e-6
    # I default non si mescolano: MonkeyOCRv2 non prende i valori di Qwen.
    monkey = train_recipes.get_recipe("monkeyocrv2-parsing")
    assert monkey.defaults_for("lora")["learning_rate"] == 1e-5
    assert monkey.defaults_for("lora")["max_pixels"] == 1003520


def test_monkey_uses_the_bundled_fork_flag():
    """Il fork ufficiale chiama il flag `--train_type`; l'upstream `--tuner_type`."""
    monkey = train_recipes.get_recipe("monkeyocrv2-parsing")
    qwen = train_recipes.get_recipe("qwen3-vl-8b")
    assert monkey.tuner_flag == "--train_type"
    assert qwen.tuner_flag == "--tuner_type"
    # Geometria distinta: prima la stima VRAM usava sempre MonkeyOCRv2.
    assert monkey.architecture["hidden_size"] != qwen.architecture["hidden_size"]


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

    # PaddleOCR-VL: PaddleFormers, non più un toolchain esterno non eseguito.
    script = trainer_script.generate_script(
        cfg("paddleocr-vl"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl"
    )
    assert "paddleformers-cli train" in script
    assert "paddleocr-vl_lora_16k_config.yaml" in script

    # GLM-OCR: LLaMA-Factory, con il template ufficiale.
    script = trainer_script.generate_script(
        cfg("glm-ocr"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl"
    )
    assert "llamafactory-cli train" in script
    assert "template: glm_ocr" in script

    # Ricetta documentata ma non eseguibile (bundle ufficiale assente).
    with pytest.raises(NotImplementedError):
        trainer_script.generate_script(
            cfg("mineru2.5"), tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl"
        )
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
