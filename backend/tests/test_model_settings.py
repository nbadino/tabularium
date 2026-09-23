from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import config
from app.db import init_db
from app.services import model_settings, serve_recipes


def test_model_settings_persist_validate_and_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "settings.db")
    init_db()

    defaults = model_settings.get_settings("teleocr")
    assert defaults["recommended"]["serving"]["max_model_len"] == 16384
    assert defaults["recommended"]["serving"]["gpu_memory_utilization"] == 0.95
    assert defaults["recommended"]["image"]["max_pixels"] == 64_000_000
    assert defaults["recommended"]["workflow"]["layout_mode"] == "Detection"
    assert defaults["overrides"] == {}

    saved = model_settings.save_settings("teleocr", {
        "serving": {"max_num_seqs": 2},
        "generation": {"temperature": 0.1},
        "image": {"max_pixels": 2_000_000},
        "workflow": {"layout_mode": "Segmentation"},
    })
    assert saved["effective"]["serving"]["max_num_seqs"] == 2
    assert saved["effective"]["generation"]["temperature"] == 0.1
    assert saved["effective"]["image"]["max_pixels"] == 2_000_000
    assert saved["effective"]["workflow"]["layout_mode"] == "Segmentation"
    assert saved["restart_required"] is True

    with pytest.raises(HTTPException, match="max_model_len supera"):
        model_settings.save_settings("teleocr", {"serving": {"max_model_len": 32768}})
    with pytest.raises(HTTPException, match="tra 0.2 e 0.98"):
        model_settings.save_settings("teleocr", {"serving": {"gpu_memory_utilization": 1.0}})
    with pytest.raises(HTTPException, match="lasciare spazio per l'immagine"):
        model_settings.save_settings("teleocr", {
            "serving": {"max_model_len": 4096},
            "generation": {"max_tokens": 4096},
        })
    with pytest.raises(HTTPException, match="Detection o Segmentation"):
        model_settings.save_settings("teleocr", {"workflow": {"layout_mode": "automatic"}})
    with pytest.raises(HTTPException, match="limite ufficiale TeleOCR"):
        model_settings.save_settings("teleocr", {"image": {"max_pixels": 71_372_800}})
    with pytest.raises(HTTPException, match="workflow PaddleOCR-VL non riconosciuto"):
        model_settings.save_settings("paddleocr-vl", {"workflow": {"layout_mode": "Segmentation"}})
    paddle_defaults = model_settings.get_settings("paddleocr-vl")["recommended"]["workflow"]
    assert paddle_defaults["layout_threshold"] is None
    assert paddle_defaults["layout_merge_bboxes_mode"] is None
    paddle = model_settings.save_settings("paddleocr-vl", {
        "workflow": {
            "layout_threshold": 0.42,
            "layout_nms": True,
            "layout_merge_bboxes_mode": "union",
        },
    })
    assert paddle["effective"]["workflow"]["layout_threshold"] == 0.42
    assert paddle["effective"]["workflow"]["layout_nms"] is True
    assert paddle["effective"]["workflow"]["layout_merge_bboxes_mode"] == "union"
    with pytest.raises(HTTPException, match="layout_threshold deve essere tra"):
        model_settings.save_settings("paddleocr-vl", {"workflow": {"layout_threshold": 1.1}})
    with pytest.raises(HTTPException, match="layout_merge_bboxes_mode non valido"):
        model_settings.save_settings("paddleocr-vl", {"workflow": {"layout_merge_bboxes_mode": "auto"}})
    with pytest.raises(HTTPException, match="image.min_pixels è disponibile solo"):
        model_settings.save_settings("teleocr", {"image": {"min_pixels": 1000}})
    with pytest.raises(HTTPException, match="richiede un logits processor"):
        model_settings.save_settings("paddleocr-vl", {"generation": {"no_repeat_ngram_size": 100}})

    reset = model_settings.save_settings("teleocr", {})
    assert reset["overrides"] == {}
    assert reset["effective"] == reset["recommended"]


def test_serve_recipe_applies_only_explicit_supported_overrides():
    argv = serve_recipes.serve_argv(
        serve_recipes.recipe_for("teleocr"),
        model_path="StarDoc-AI/TeleOCR", port=8000,
        settings={"serving": {"max_num_seqs": 2, "max_model_len": 8192}},
    )
    assert argv[argv.index("--max-num-seqs") + 1] == "2"
    assert argv[argv.index("--max-model-len") + 1] == "8192"
    assert "--trust-remote-code" in argv
    assert "--dtype" in argv and "bfloat16" in argv


def test_glm_batched_token_budget_can_exceed_single_sequence_context(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "glm-settings.db")
    init_db()

    defaults = model_settings.get_settings("glm-ocr")["recommended"]["serving"]
    # The cloud recipe delegates max_model_len to vLLM; the local command's
    # conservative 16k cap is not a Vast recommendation.
    assert defaults["max_model_len"] is None
    assert defaults["max_num_batched_tokens"] == 32768
    glm = model_settings.get_settings("glm-ocr")["recommended"]
    assert glm["generation"] == {
        "max_tokens": 8192,
        "temperature": 0.0,
        "top_p": 0.00001,
        "top_k": 1,
        "repetition_penalty": 1.1,
    }
    assert glm["image"]["max_pixels"] == 71_372_800

    saved = model_settings.save_settings("glm-ocr", {
        "serving": {"max_num_batched_tokens": 32768},
        "generation": {"top_p": 0.00001},
    })
    assert saved["effective"]["serving"]["max_num_batched_tokens"] == 32768
    assert saved["effective"]["generation"]["top_p"] == 0.00001


def test_model_settings_use_official_qwen_generation_and_deepseek_n_gram_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "official-generation.db")
    init_db()

    qwen = model_settings.get_settings("qwen3-vl-8b")
    assert qwen["recommended"]["generation"] == {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "repetition_penalty": 1.0,
        "presence_penalty": 1.5,
    }

    deepseek = model_settings.get_settings("deepseek-ocr")
    assert deepseek["recommended"]["workflow"] == {"ngram_size": 30, "window_size": 90}
    saved = model_settings.save_settings("deepseek-ocr", {"workflow": {"ngram_size": 40, "window_size": 120}})
    assert saved["effective"]["workflow"] == {"ngram_size": 40, "window_size": 120}
    with pytest.raises(HTTPException, match="intero tra 1 e 256"):
        model_settings.save_settings("deepseek-ocr", {"workflow": {"ngram_size": 0}})
    with pytest.raises(HTTPException, match="logits processor previsto"):
        model_settings.save_settings("deepseek-ocr", {"generation": {"no_repeat_ngram_size": 30}})


def test_glm_mtp_tokens_are_model_specific_and_applied_to_cloud_recipe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "glm-mtp.db")
    init_db()
    defaults = model_settings.get_settings("glm-ocr")
    assert defaults["recommended"]["workflow"]["speculative_tokens"] == 1

    saved = model_settings.save_settings("glm-ocr", {
        "workflow": {"speculative_tokens": 5},
    })
    assert saved["effective"]["workflow"]["speculative_tokens"] == 5
    assert saved["restart_required"] is True
    argv = serve_recipes.serve_argv(
        serve_recipes.recipe_for("glm-ocr"), model_path="zai-org/GLM-OCR",
        port=8000, settings=saved["overrides"],
    )
    config_json = argv[argv.index("--speculative-config") + 1]
    assert '"num_speculative_tokens":5' in config_json
    with pytest.raises(HTTPException, match="intero tra 1 e 16"):
        model_settings.save_settings("glm-ocr", {"workflow": {"speculative_tokens": 0}})
    with pytest.raises(HTTPException, match="non riconosciuto"):
        model_settings.save_settings("teleocr", {"workflow": {"speculative_tokens": 5}})


def test_mlx_settings_are_validated_and_applied_to_local_server():
    from app.services import mlx_runtime

    settings = {"kv_bits": 4, "kv_group_size": 32, "max_kv_size": 4096,
                "vision_cache_size": 8, "kv_quant_scheme": "uniform", "log_level": "INFO"}
    argv = mlx_runtime.serve_argv("mlx-community/model", port=8080, settings=settings)
    for flag, value in (("--kv-bits", "4"), ("--kv-group-size", "32"),
                        ("--max-kv-size", "4096"), ("--vision-cache-size", "8"),
                        ("--kv-quant-scheme", "uniform"), ("--log-level", "INFO")):
        assert argv[argv.index(flag) + 1] == value
    assert "--kv-bits" not in mlx_runtime.serve_argv("mlx-community/model", port=8080)


def test_mlx_model_settings_reject_unsupported_models_and_values(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mlx-settings.db")
    init_db()
    settings = model_settings.get_settings("paddleocr-vl")
    assert set(settings["recommended"]["mlx"]) == {
        "kv_bits", "kv_group_size", "max_kv_size", "vision_cache_size", "kv_quant_scheme", "log_level"
    }
    saved = model_settings.save_settings("paddleocr-vl", {"mlx": {"kv_bits": 4, "kv_quant_scheme": "uniform"}})
    assert saved["restart_required"] is True
    assert saved["effective"]["mlx"]["kv_bits"] == 4
    with pytest.raises(HTTPException, match="non disponibili"):
        model_settings.save_settings("teleocr", {"mlx": {"kv_bits": 4}})
    with pytest.raises(HTTPException, match="mlx.kv_bits"):
        model_settings.save_settings("paddleocr-vl", {"mlx": {"kv_bits": 9}})


def test_teleocr_sampling_preserves_upstream_task_defaults():
    from app.services.model_adapters import get_adapter

    adapter = get_adapter("teleocr")
    text = adapter.sampling_for("text")
    table = adapter.sampling_for("table")
    formula = adapter.sampling_for("formula")
    assert text["temperature"] == table["temperature"] == 0.0
    assert text["top_p"] == table["top_p"] == 0.01
    assert text["top_k"] == table["top_k"] == 1
    assert text["presence_penalty"] == table["presence_penalty"] == 1.0
    assert text["frequency_penalty"] == 0.05
    assert table["frequency_penalty"] == 0.005
    assert formula["frequency_penalty"] == 0.05
    assert table["no_repeat_ngram_size"] == 100


def test_teleocr_layout_prompt_obeys_saved_official_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "teleocr-workflow.db")
    init_db()
    from app.services.model_adapters import get_adapter

    adapter = get_adapter("teleocr")
    assert adapter.prompt_for("layout") == "Analyze the image layout."
    model_settings.save_settings("teleocr", {"workflow": {"layout_mode": "Segmentation"}})
    assert adapter.prompt_for("layout") == "Multi-point Layout Segmentation Analysis."


def test_paddle_predict_options_reach_the_official_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "paddle-workflow.db")
    init_db()
    model_settings.save_settings("paddleocr-vl", {
        "workflow": {
            "use_layout_detection": True, "use_queues": False,
            "layout_threshold": 0.4, "layout_nms": True,
            "layout_merge_bboxes_mode": "union",
        },
        "generation": {"temperature": 0.1, "top_p": 0.8, "max_tokens": 5000, "top_k": 3},
        "image": {"min_pixels": 1000, "max_pixels": 2_000_000},
    })

    from app.services.paddle_official import _predict_options
    from app.services.paddle_official import _RUNNER

    assert _predict_options() == {
        "use_layout_detection": True,
        "use_queues": False,
        "layout_threshold": 0.4,
        "layout_nms": True,
        "layout_merge_bboxes_mode": "union",
        "max_pixels": 2_000_000,
        "min_pixels": 1000,
        "temperature": 0.1,
        "top_p": 0.8,
        "max_new_tokens": 5000,
        "vlm_extra_args": {"top_k": 3},
    }
    mlx_options = _predict_options("mlx-vlm-server")
    assert "min_pixels" not in mlx_options
    assert "max_pixels" not in mlx_options
    assert mlx_options["layout_threshold"] == 0.4
    compile(_RUNNER, "paddle_official_runner", "exec")
    assert "pipeline.predict(image, **predict_options)" in _RUNNER


def test_recommended_serving_values_only_come_from_remote_recipes():
    for adapter_id in serve_recipes.RECIPES:
        settings = model_settings.get_settings(adapter_id)
        recipe_args = list(serve_recipes.RECIPES[adapter_id].serve_args)
        for flag, key, cast in (
            ("--gpu-memory-utilization", "gpu_memory_utilization", float),
            ("--max-model-len", "max_model_len", int),
            ("--max-num-seqs", "max_num_seqs", int),
            ("--max-num-batched-tokens", "max_num_batched_tokens", int),
        ):
            if flag in recipe_args:
                assert settings["recommended"]["serving"][key] == cast(recipe_args[recipe_args.index(flag) + 1])
            else:
                assert settings["recommended"]["serving"][key] is None

    # These adapters have local hardware caps that the cloud recipe omits.
    # The Settings panel must leave those fields automatic for Vast.
    for adapter_id in ("mineru2.5", "dots-ocr", "glm-ocr", "deepseek-ocr", "paddleocr-vl"):
        recommended = model_settings.get_settings(adapter_id)["recommended"]["serving"]
        if "--gpu-memory-utilization" not in serve_recipes.recipe_for(adapter_id).serve_args:
            assert recommended["gpu_memory_utilization"] is None
        if "--max-model-len" not in serve_recipes.recipe_for(adapter_id).serve_args:
            assert recommended["max_model_len"] is None
