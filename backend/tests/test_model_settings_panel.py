"""What the parameter panel needs from the backend, declared once (docs/MODELS_UX.md)."""
from __future__ import annotations

import pytest

from app import config
from app.db import init_db
from app.services import model_settings


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "panel.db")
    init_db()


def test_the_panel_learns_which_image_fields_a_model_honours():
    # The view used to name models (`mineru2.5`, `qwen3-vl-8b`) to decide this.
    assert model_settings.get_settings("qwen3-vl-8b")["image_fields"] == ["min_pixels", "max_pixels"]
    assert model_settings.get_settings("monkeyocrv2-parsing")["image_fields"] == ["max_pixels"]


def test_the_panel_shows_the_official_sampling_each_task_sends():
    # "Automatic" hid the truth: the adapter sends per-task values.
    glm = model_settings.get_settings("glm-ocr")["task_sampling"]
    assert glm["text"]["temperature"] == 0.0
    assert glm["text"]["repetition_penalty"] == 1.1
    mineru = model_settings.get_settings("mineru2.5")["task_sampling"]
    assert mineru["text"]["presence_penalty"] == 1.0
    assert "presence_penalty" not in mineru["layout"]
    # Only generation knobs, never transport fields like vllm_xargs.
    assert "vllm_xargs" not in mineru["layout"]
    assert "skip_special_tokens" not in mineru["layout"]
    # A task the model has no prompt for is not listed.
    assert "layout" not in model_settings.get_settings("paddleocr-vl")["task_sampling"]
    monkey = model_settings.get_settings("monkeyocrv2-parsing")["task_sampling"]
    assert monkey["layout"] == {"temperature": 0.0}


def test_essential_workflow_fields_are_declared_by_the_spec():
    fields = {
        field["key"]: field["essential"]
        for field in model_settings.get_settings("monkeyocrv2-parsing")["workflow_fields"]
    }
    assert fields == {"dflash_enabled": True, "dflash_num_speculative_tokens": False}
    unlimited = {f["key"]: f["essential"] for f in model_settings.get_settings("unlimited-ocr")["workflow_fields"]}
    assert unlimited["image_mode"] is True and unlimited["ngram_size"] is False


def test_task_sampling_survives_adapters_whose_prompts_read_the_settings():
    # TeleOCR's prompt depends on its saved layout mode: computing the per-task
    # sampling must not recurse through get_settings.
    sampling = model_settings.get_settings("teleocr")["task_sampling"]
    assert sampling["text"]["temperature"] == 0.0
