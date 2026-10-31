"""Unlimited-OCR-2: the vendor's block protocol, its SGLang request and its recipe."""
from __future__ import annotations

import json
import pickle
import sys
import types

from app.services import auto_training, serve_recipes, sglang_runtime, train_recipes
from app.services.model_adapters import get_adapter
from app.services.prefill import native_mode


def test_unlimited2_parses_the_vendor_block_format():
    # Shapes from `utils/README.md` and `process_unlimitedOCR2.BLOCK_RE`.
    adapter = get_adapter("unlimited-ocr-2")
    items = adapter.parse_layout(
        "<PAGE><BLOCK>title<LEVEL1>[100,40,900,90]<FORK>HISTORIC SHIPPING INDEX"
        "<BLOCK>header[10,5,300,30]<FORK>Lloyd's"
        "<BLOCK>text[100,100,900,200]<LEVEL2><FORK>Arrived at <|ref|>Genoa<|/ref|>"
        '<BLOCK>cross_table[50,210,950,1003]<FORK><table><tr><td colspan="2x">Ship</td></tr></table>'
        "<MERGE><BLOCK>image[0,0,120,120]<FORK>a ship"
        "\nequation[200,600,800,650]<FORK>x^2"
        "<BLOCK>marginalia[1,1,2,2]<FORK>unknown label"
        "<BLOCK>text[500,500,400,600]<FORK>inverted box"
        "<BLOCK>text<FORK>no box"
    )
    assert items == [
        {"bbox": [100, 40, 900, 90], "label": "Title", "content": "HISTORIC SHIPPING INDEX"},
        {"bbox": [10, 5, 300, 30], "label": "Page-header", "content": "Lloyd's"},
        {"bbox": [100, 100, 900, 200], "label": "Text", "content": "Arrived at Genoa"},
        # Clamped to 0-1000 like the vendor's `_scale`; malformed span values repaired.
        {"bbox": [50, 210, 950, 1000], "label": "Table",
         "content": '<table><tr><td colspan="2">Ship</td></tr></table>'},
        {"bbox": [0, 0, 120, 120], "label": "Picture", "content": ""},
        # A bare block (no `<BLOCK>`) with a known label is recovered.
        {"bbox": [200, 600, 800, 650], "label": "Formula", "content": "x^2"},
    ]


def test_unlimited2_request_follows_the_sglang_example():
    adapter = get_adapter("unlimited-ocr-2")
    assert native_mode(adapter) == "end2end"
    assert adapter.prompt_for("end2end") == "<PAGE><image>Multi page parsing."
    overrides = adapter.request_overrides("end2end")
    assert overrides["skip_special_tokens"] is False
    assert overrides["images_config"] == {"crop_mode": True}
    assert overrides["custom_params"] == {
        "ngram_size": 35, "window_size": 128, "page_token_id": 128827,
        "eos_token_id": 1, "max_page_tokens": 20000,
    }
    # `to_str()` is JSON around a hex pickle; ours must resolve to SGLang's class.
    module = types.ModuleType("sglang.srt.sampling.custom_logit_processor")

    class DeepseekOCRNoRepeatNGramLogitProcessor:  # stand-in for the server's class
        pass

    module.DeepseekOCRNoRepeatNGramLogitProcessor = DeepseekOCRNoRepeatNGramLogitProcessor
    names = ("sglang", "sglang.srt", "sglang.srt.sampling", module.__name__)
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules.update({name: types.ModuleType(name) for name in names[:-1]})
    sys.modules[module.__name__] = module
    try:
        payload = bytes.fromhex(json.loads(overrides["custom_logit_processor"])["callable"])
        assert pickle.loads(payload) is DeepseekOCRNoRepeatNGramLogitProcessor
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def test_unlimited2_is_served_by_sglang_with_the_vendor_environment():
    recipe = serve_recipes.recipe_for("unlimited-ocr-2")
    assert recipe.runtime == "sglang"
    assert not recipe.installs_vllm
    argv = serve_recipes.serve_argv(recipe, model_path="/m", port=8000, api_key="k")
    assert argv[:4] == ["-m", "sglang.launch_server", "--model-path", "/m"]
    assert argv[argv.index("--chat-template") + 1] == "unlimited-ocr"
    assert "--enable-custom-logit-processor" in argv
    assert argv[-4:] == ["--served-model-name", "Unlimited-OCR-2", "--api-key", "k"]
    assert dict(recipe.serve_env)["SGLANG_UOCR_MAX_PAGE_TOKENS"] == "20000"

    local = get_adapter("unlimited-ocr-2").serve_command("/m", 8001)
    assert local[0] == str(sglang_runtime.python_bin("unlimited-ocr-2"))
    assert local[1:3] == ["-m", "sglang.launch_server"]


def test_unlimited2_vast_recipe_carries_interpreter_and_environment():
    from app.services import cloud_manager

    recipe = cloud_manager.build_provision_recipe("unlimited-ocr-2")
    assert recipe["runtime"] == "sglang"
    assert recipe["install_vllm"] is False
    assert recipe["python_version"] == "3.10"
    assert recipe["serve_env"]["SGLANG_UOCR_EANABLE_DOC_LIMIT"] == "1"
    assert any(item.endswith("py3-none-any.whl") for item in recipe["pip_extra"])
    assert recipe["argv"][:2] == ["-m", "sglang.launch_server"]


def test_unlimited2_trains_with_ms_swift_main_and_its_own_profile():
    recipe = train_recipes.get_recipe("unlimited-ocr-2")
    assert recipe.script_ready
    assert recipe.swift_model_type == "unlimited-ocr"
    assert recipe.swift_template == "unlimited_ocr"
    assert recipe.defaults_for("lora")["grad_accum"] == 16
    assert recipe.environment["transformers"] == "==4.52.1"
    assert auto_training.profile_for("unlimited-ocr-2") == "uocr2"
