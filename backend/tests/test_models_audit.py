"""Official values pinned by the model audit (`docs/MODELS_AUDIT.md`).

Each test names the vendor source it was read from. When a vendor changes a value,
update the audit table and the test in the same commit (AGENTS.md R3, R15).
"""
from __future__ import annotations

from app import models as model_specs
from app.services import inference as infmod
from app.services import model_adapters


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def test_deepseek_ocr2_pins_a_vllm_that_contains_the_architecture():
    # `vllm/model_executor/models/deepseek_ocr2.py` first ships in v0.16.0 (404 at
    # v0.12.0 and v0.15.0); the vLLM recipe declares `min_vllm_version: "0.16.0"`.
    recipe = model_specs.spec_for("deepseek-ocr").value("serve")
    assert _version(recipe.vllm_version) >= (0, 16, 0)


def test_deepseek_ocr2_asks_for_the_official_output_budget():
    # Vendor `run_dpsk_ocr2_*.py` and the recipe example use max_tokens=8192; the
    # client still clamps it to the served context (`_output_budget`).
    adapter = model_adapters.get_adapter("deepseek-ocr")
    assert adapter.end2end_max_tokens == 8192
    client = infmod.VllmClient(url="https://example.invalid/v1", adapter=adapter)
    assert client._output_budget(adapter.end2end_max_tokens) == adapter.capabilities.max_model_len - 2048


def test_unlimited_ocr_decodes_greedily():
    # vLLM `unlimited_ocr.py` example and the recipe use temperature=0.0; the
    # checkpoint's generation_config.json has none, so vLLM would default to 1.0.
    adapter = model_adapters.get_adapter("unlimited-ocr")
    client = infmod.VllmClient(url="https://example.invalid/v1", adapter=adapter)
    assert client._sampling_for("end2end")["temperature"] == 0.0


def test_unlimited_ocr_image_mode_reaches_the_vllm_processor():
    # vLLM's processor reads `crop_mode` from the multimodal kwargs
    # (`mm_kwargs.get("crop_mode", CROP_MODE)`); `images_config` is the SGLang field.
    adapter = model_adapters.get_adapter("unlimited-ocr")
    overrides = adapter.request_overrides("end2end")
    assert overrides["mm_processor_kwargs"] == {"crop_mode": True}
    assert overrides["images_config"] == {"image_mode": "gundam"}


def test_lightonocr3_pins_the_transformers_its_vllm_needs():
    # Vendor `benchmarks/docker/lightonocr-1b.Dockerfile`: vLLM 0.30.0's pixtral.py
    # breaks on transformers 5.17, so the image pins 5.16.1.
    recipe = model_specs.spec_for("lightonocr-3").value("serve")
    assert recipe.vllm_version == "0.30.0"
    assert recipe.transformers_version == "5.16.1"


def test_qwen3_vl_applies_the_official_min_pixels_without_an_override(tmp_path, monkeypatch):
    # Qwen3-VL cookbook (`qwenvl html`): min_pixels = 512*32*32.
    from app import config
    from app.db import init_db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "qwen-min.db")
    init_db()
    client = infmod.VllmClient(
        url="https://example.invalid/v1", adapter=model_adapters.get_adapter("qwen3-vl-8b"),
    )
    assert client.min_pixels == 512 * 32 * 32


def test_monkeyocrv2_decodes_greedily_on_every_task():
    # `parsing/core_runner.py::_chat_completion` sends `"temperature": 0` unless a
    # retry raises it; the checkpoint's generation_config would sample at 0.6.
    adapter = model_adapters.get_adapter("monkeyocrv2-parsing")
    client = infmod.VllmClient(url="https://example.invalid/v1", adapter=adapter)
    for task in ("layout", "text", "table", "formula", "end2end"):
        assert client._sampling_for(task)["temperature"] == 0.0, task


def test_monkeyocrv2_recognition_uses_the_official_token_budget():
    # `batch_inference_with_repeat_retry(..., max_tokens=5000)` in core_runner.py.
    assert model_adapters.get_adapter("monkeyocrv2-parsing").text_max_tokens == 5000


def test_paddleocr_vl_direct_path_decodes_greedily():
    # PaddleX vllm-server client and the vLLM recipe: temperature 0.
    adapter = model_adapters.get_adapter("paddleocr-vl")
    for task in ("text", "table", "formula"):
        assert adapter.sampling_for(task)["temperature"] == 0.0, task


def test_mineru_direct_path_sends_the_official_temperature():
    # `MinerUSamplingParams` in mineru_client.py: temperature 0.0.
    adapter = model_adapters.get_adapter("mineru2.5")
    for task in ("layout", "text", "table"):
        assert adapter.sampling_for(task)["temperature"] == 0.0, task


def test_glm_ocr_direct_path_uses_the_official_prompts():
    # Model card: only "Text Recognition:", "Table Recognition:" and
    # "Formula Recognition:" are supported; layout comes from the SDK's PP-DocLayout.
    adapter = model_adapters.get_adapter("glm-ocr")
    assert adapter.prompt_for("text") == "Text Recognition:"
    assert adapter.prompt_for("table") == "Table Recognition:"
    assert adapter.prompt_for("formula") == "Formula Recognition:"


def test_glm_ocr_native_result_keeps_the_layout_class():
    # `glmocr/postprocess/result_formatter.py` collapses `label` to
    # text/table/formula/image and keeps the PP-DocLayoutV3 class in `native_label`,
    # prefixing titles with "# " / "## ".
    adapter = model_adapters.get_adapter("glm-ocr")
    blocks = adapter.parse_native_result([[
        {"bbox_2d": [10, 10, 900, 60], "label": "text", "native_label": "doc_title", "content": "# Lloyd's List"},
        {"bbox_2d": [10, 80, 900, 120], "label": "text", "native_label": "paragraph_title", "content": "## Arrivals"},
        {"bbox_2d": [10, 130, 900, 500], "label": "table", "native_label": "table", "content": "<table></table>"},
        {"bbox_2d": [10, 900, 200, 950], "label": "text", "native_label": "number", "content": "12"},
    ]])
    assert [(b["label"], b["content"]) for b in blocks] == [
        ("Title", "Lloyd's List"),
        ("Section-header", "Arrivals"),
        ("Table", "<table></table>"),
        ("Page-footer", "12"),
    ]


def test_dots_mocr_prompt_is_the_vendor_text_byte_for_byte():
    # `dots_mocr/utils/prompts.py` → prompt_layout_all_en.
    prompt = model_adapters.get_adapter("dots-ocr").prompt_for("end2end")
    assert "3. Text Extraction & Formatting Rules:\n    - Picture:" in prompt
    assert "4. Constraints:\n    - The output text" in prompt
    assert prompt.endswith("5. Final Output: The entire output must be a single JSON object.\n")


def test_teleocr_local_server_loads_the_vendor_ngram_guard():
    argv = model_adapters.get_adapter("teleocr").serve_command("/models/teleocr", 8888)
    recipe = model_specs.spec_for("teleocr").value("serve")
    processor = recipe.serve_args[recipe.serve_args.index("--logits-processors") + 1]
    assert argv[argv.index("--logits-processors") + 1] == processor


def test_llamaparse_languages_use_the_vendor_enum(tmp_path, monkeypatch):
    # LlamaParse `ParsingLanguages` (API reference, parsing/methods/create): `en`,
    # `it`, `la`… — the Tesseract codes Tabularium used (`eng`, `ita`) are not in it.
    from app import config
    from app.adapters import store
    from app.db import init_db
    from app.domain import inference as protocol
    from app.models.types import LLAMAINDEX_OCR_LANGUAGES, llamaindex_workflow_defaults
    from app.services import model_settings

    assert {"en", "it", "la", "fr", "de", "ch_sim"} <= LLAMAINDEX_OCR_LANGUAGES
    assert "eng" not in LLAMAINDEX_OCR_LANGUAGES
    assert llamaindex_workflow_defaults()["languages"] == ["en"]

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "llama-lang.db")
    init_db()
    # A setting saved with the old codes is read in the vendor's vocabulary.
    store.put("model_settings:llamaindex-ocria-1", {"workflow": {"languages": ["ita", "lat", "eng"]}})
    effective = model_settings.get_settings("llamaindex-ocria-1")["effective"]["workflow"]
    assert effective["languages"] == ["it", "la", "en"]
    payload, _ = protocol.llamaindex_configuration({"languages": ["ita", "zzz"]})
    assert payload["processing_options"]["ocr_parameters"]["languages"] == ["it"]


def test_gemini_argon_uses_the_catalog_id_and_a_thinking_sized_budget():
    # OpenCode Go catalog (`GET /inference/go/v1/models`, 2026-11-03) lists only
    # `gemini-4-argon`; Google: maxOutputTokens covers thinking + answer, keep it
    # at least 32,768.
    from app.services import opencode_gateway

    assert opencode_gateway.DEFAULT_MODEL == "gemini-4-argon"
    assert model_adapters.get_adapter("gemini-argon").capabilities.served_model_name == "gemini-4-argon"
    assert infmod.GEMINI_MIN_OUTPUT_TOKENS >= 32_768


def test_llamaparse_timeouts_respect_the_vendor_maximums():
    # API reference `processing_control.timeouts`: base max 7200 s, per page max 300 s.
    from app.models.types import LLAMAINDEX_WORKFLOW_LIMITS

    assert LLAMAINDEX_WORKFLOW_LIMITS["job_timeout_in_seconds"][1] == 7_200
    assert LLAMAINDEX_WORKFLOW_LIMITS["job_timeout_extra_time_per_page_in_seconds"][1] == 300


def test_every_model_declares_its_public_benchmark_or_why_not():
    from app.models import SPECS
    from app.models.benchmarks import PUBLIC_SCORES

    assert set(PUBLIC_SCORES) == set(SPECS)


def test_public_scores_match_the_omnidocbench_v16_leaderboard():
    # github.com/opendatalab/OmniDocBench README, table "v1.6_full", Overall,
    # read on 2026-11-03; vendor rows are labelled as such.
    from app.models.benchmarks import public_benchmark

    leaderboard = {
        "teleocr": 96.91, "paddleocr-vl": 96.34, "mineru2.5": 95.75, "glm-ocr": 95.22,
        "unlimited-ocr": 94.00, "monkeyocrv2-parsing": 91.80, "deepseek-ocr": 90.25,
    }
    for adapter_id, value in leaderboard.items():
        score = public_benchmark(adapter_id)
        assert (score["value"], score["reported_by"]) == (value, "leaderboard"), adapter_id
    assert public_benchmark("xiaomi-ocr-0")["reported_by"] == "vendor"
    assert public_benchmark("unlimited-ocr-2")["value"] == 94.02
    unpublished = public_benchmark("lightonocr-3")
    assert unpublished["value"] is None and unpublished["note"]
    assert public_benchmark("custom-anything")["value"] is None


def test_the_catalog_carries_each_models_public_benchmark():
    from app.services import model_registry

    rows = {item["adapter_id"]: item for item in model_registry.list_models()}
    assert rows["teleocr"]["benchmark"]["value"] == 96.91
    assert rows["teleocr"]["benchmark"]["benchmark"] == "OmniDocBench v1.6"
    assert rows["surya-ocr-2.5"]["benchmark"]["value"] is None
