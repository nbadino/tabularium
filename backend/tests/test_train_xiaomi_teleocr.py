"""Training recipes for Xiaomi-OCR-0 (ms-swift `qwen3_5`) and TeleOCR (still inference-only).

Facts pinned here come from primary sources read on 2026-11-16:
- ms-swift `docs/source_en/BestPractices/Qwen3_8-Best-Practice.md` (Qwen3.5 dense LoRA) and
  `swift/model/models/qwen.py` (`qwen3_5` registers `Qwen3_5ForConditionalGeneration`, from 4.0.0);
- Xiaomi-OCR-0 `evaluation_pipeline/twostage_img2md.py` (`PROMPTS`, 2048 px input bound) and the
  HF `processor_config.json` (`shortest_edge` 65536 px = 64 tokens of 32x32 px);
- TeleOCR's HF `config.json`/`modeling_naviocr.py` and its vLLM plugin: a Qwen2.5-VL vision tower
  with a `Qwen3ForCausalLM` decoder (head_dim 128, q/k norm, no qkv bias), which the stock
  `Qwen2_5_VLForConditionalGeneration` that ms-swift's `qwen2_5_vl` loads cannot hold.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.domain import otsl as otslmod
from app.models.types import FAMILY_FORMULA, FAMILY_TABLE, FAMILY_TEXT, RECOGNITION_FAMILIES
from app.services import auto_training, train_recipes, trainer_script
from app.services.model_adapters import get_adapter
from app.services.training_views import build_view


def test_xiaomi_is_an_experimental_ms_swift_qwen3_5_recipe():
    recipe = train_recipes.get_recipe("xiaomi-ocr-0")
    assert recipe is not None and recipe.trainable and recipe.script_ready
    # Not `verified`: no real run has promoted it yet.
    assert recipe.maturity == "experimental"
    assert not recipe.verified_on
    assert recipe.toolchain == "ms-swift"
    assert recipe.tuner_flag == "--tuner_type"
    assert set(recipe.train_types) == {"lora", "full"}
    assert (recipe.swift_model_type, recipe.swift_template) == ("qwen3_5", "qwen3_5")
    assert "Qwen3_8-Best-Practice" in recipe.official_doc
    # The model emits no boxes: layout comes from PP-DocLayoutV3, never from training.
    assert recipe.families == RECOGNITION_FAMILIES
    # Best Practice: transformers >= 5.9; ms-swift >= 4.3.1 appends `ignore_empty_think` itself.
    assert recipe.environment == {"ms-swift": ">=4.3.1", "transformers": ">=5.9"}
    # ms-swift's default lr per tuner (1e-4 LoRA, 1e-5 full); the BP's LoRA values.
    assert recipe.defaults_for("lora")["learning_rate"] == 1e-4
    assert recipe.defaults_for("full")["learning_rate"] == 1e-5
    assert (recipe.defaults["lora_rank"], recipe.defaults["lora_alpha"]) == (8, 32)
    # Geometry read from the checkpoint's `config.json` (`text_config`).
    assert recipe.architecture["vocab_size"] == 248320
    assert recipe.architecture["hidden_size"] == 1024


def test_xiaomi_trains_on_the_vendor_task_prompts_it_is_served_with():
    recipe = train_recipes.get_recipe("xiaomi-ocr-0")
    adapter = get_adapter("xiaomi-ocr-0")
    assert recipe.prompts == {
        FAMILY_TEXT: adapter.prompt_for("text"),
        FAMILY_TABLE: adapter.prompt_for("table"),
        FAMILY_FORMULA: adapter.prompt_for("formula"),
    }
    assert recipe.prompts[FAMILY_TABLE] == "Parse the table in the image into OTSL."


def test_xiaomi_script_matches_the_serving_image_bounds_and_skips_packing(tmp_path: Path):
    script = trainer_script.generate_script(
        {"adapter_id": "xiaomi-ocr-0", "train_type": "lora", "model": "xiaomi-research/Xiaomi-OCR-0"},
        tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl",
    )
    assert "--model_type qwen3_5" in script and "--template qwen3_5" in script
    assert "--tuner_type lora" in script
    # Serving: crops of a page fitted to 2048 px (<= 4096 tokens), and the processor's
    # 65536 px floor (64 tokens). Training must see the same token range.
    assert "export IMAGE_MAX_TOKEN_NUM=4096" in script
    assert "export IMAGE_MIN_TOKEN_NUM=64" in script
    # ms-swift FAQ: Qwen3.5's linear attention has no var_len, packing is not recommended.
    assert "--packing" not in script and "--padding_free" not in script
    assert "--freeze_vit True" in script and "--freeze_aligner True" in script
    assert "--max_pixels" not in script


def test_image_min_token_flag_is_written_only_when_declared(tmp_path: Path):
    script = trainer_script.generate_script(
        {"adapter_id": "qwen3-vl-8b", "train_type": "lora", "model": "m"},
        tmp_path, tmp_path / "t.jsonl", tmp_path / "v.jsonl",
    )
    assert "IMAGE_MIN_TOKEN_NUM" not in script


def test_xiaomi_view_keeps_the_otsl_target_with_spanned_headers(tmp_path: Path):
    # Two-row header: "Ship" spans both rows, "Tons" spans Gross|Net.
    grid = {
        "rows": 3, "cols": 3,
        "cells": [
            {"r": 0, "c": 0, "rowspan": 2, "colspan": 1, "text": "Ship"},
            {"r": 0, "c": 1, "rowspan": 1, "colspan": 2, "text": "Tons"},
            {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "Gross"},
            {"r": 1, "c": 2, "rowspan": 1, "colspan": 1, "text": "Net"},
            {"r": 2, "c": 0, "rowspan": 1, "colspan": 1, "text": "Abava"},
            {"r": 2, "c": 1, "rowspan": 1, "colspan": 1, "text": "71"},
            {"r": 2, "c": 2, "rowspan": 1, "colspan": 1, "text": "40"},
        ],
    }
    target = otslmod.grid_to_otsl(grid)
    assert target == (
        "<fcel>Ship<fcel>Tons<lcel><nl>"
        "<ucel><fcel>Gross<fcel>Net<nl>"
        "<fcel>Abava<fcel>71<fcel>40"
    )
    assert otslmod.otsl_to_grid(target)["cells"] == grid["cells"]

    source = tmp_path / "canonical"
    source.mkdir()
    image = source / "crop-table.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nnot-really")
    rows = {
        "layout": "[]",
        "text_rec": "Abava",
        "table": target,
        "formula": "$C_{0}$",
    }
    for family, content in rows.items():
        row = {
            "messages": [
                {"role": "user", "content": "<image>Please output the text content from the image."},
                {"role": "assistant", "content": content},
            ],
            "images": [str(image)],
        }
        for split in ("train", "val"):
            (source / f"{family}_{split}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    view = build_view(train_recipes.get_recipe("xiaomi-ocr-0"), source, tmp_path / "view")
    out = [json.loads(line) for line in view.train.read_text(encoding="utf-8").splitlines()]
    assert "layout" not in view.counts
    table = next(row for row in out if row["messages"][0]["content"].endswith("into OTSL."))
    assert table["messages"][0]["content"] == "<image>Parse the table in the image into OTSL."
    assert table["messages"][1]["content"] == target


def test_xiaomi_uses_the_qwen_training_profile():
    # ms-swift 4.6.0 + transformers 5.20.2 satisfy the Qwen3.5 Best Practice's pins.
    assert auto_training.profile_for("xiaomi-ocr-0") == "qwen"


def test_teleocr_stays_inference_only_with_the_architecture_reason():
    recipe = train_recipes.get_recipe("teleocr")
    assert recipe is not None and not recipe.trainable
    assert recipe.maturity == "inference-only"
    assert get_adapter("teleocr").capabilities.training_types == ()
    text = " ".join((recipe.note, *recipe.official_notes))
    # The reason is the architecture, not only the missing vendor code.
    assert "Qwen3ForCausalLM" in text
    assert "qwen2_5_vl" in text
