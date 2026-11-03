"""The audit's open items, closed against the vendors' sources (docs/MODELS_AUDIT.md §2).

Each test names the source it pins. A vendor change updates the audit and the test in the
same commit (AGENTS.md R3, R15).
"""
from __future__ import annotations

import base64
import gzip
import re
import shutil
import subprocess

import pytest
from PIL import Image

from app import models as model_specs
from app.domain import inference as protocol
from app.services import inference as infmod
from app.services import model_adapters


# --- PaddleOCR-VL -------------------------------------------------------------------


def test_paddle_pipeline_runs_version_16_on_a_runtime_that_has_it():
    # vLLM recipe: 1.6 needs `paddleocr[doc-parser]>=3.6.0` and `pipeline_version="v1.6"`;
    # PaddleOCR's `_DEFAULT_PIPELINE_VERSION` is v1.6 only from 3.6.
    from app.services import paddle_official, paddle_runtime

    assert 'pipeline_version="v1.6"' in paddle_official._RUNNER
    assert paddle_runtime.PADDLEOCR_REQUIREMENT == "paddleocr[doc-parser]>=3.6.0"
    # An older runtime that can import PaddleOCRVL is not ready: it would run v1/v1.5.
    assert "(3, 6)" in paddle_runtime.READY_CHECK


def test_paddle_direct_path_asks_for_the_official_output_budget():
    # PaddleX vllm-server client: PADDLEOCR_VL_MAX_NEW_TOKENS = 8192.
    adapter = model_adapters.get_adapter("paddleocr-vl")
    assert adapter.text_max_tokens == 8192
    assert adapter.sampling_for("table")["max_tokens"] == 8192


# --- dots.mocr ----------------------------------------------------------------------


def test_smart_resize_matches_the_vendor_rounding():
    # dots_mocr/utils/image_utils.py::smart_resize (factor 28, 3136 … 11289600).
    assert protocol.smart_resize(1000, 750) == (1008, 756)
    height, width = protocol.smart_resize(4000, 3000)
    assert height % 28 == 0 and width % 28 == 0 and height * width <= 11_289_600
    assert protocol.smart_resize(20, 20) == (56, 56)


def test_dots_boxes_are_rescaled_from_the_server_side_resize():
    # layout_utils.py::post_process_cells: the model's pixels live in the smart_resize
    # space of the image the server received, not in its raw size.
    adapter = model_adapters.get_adapter("dots-ocr")
    assert adapter.end2end_resize_grid == (28, 3136, 11_289_600)
    bbox = protocol.pixel_bbox_to_normalized([0, 0, 756, 504], 750, 500, adapter.end2end_resize_grid)
    assert bbox == pytest.approx([0, 0, 1000, 1000])


# --- GLM-OCR ------------------------------------------------------------------------


def test_glm_ocr_uses_the_vendor_mtp_depth_and_html_tables():
    # README L130: `num_speculative_tokens: 3`; vendor test data returns
    # `<table border="1">` for tables.
    recipe = model_specs.spec_for("glm-ocr").value("serve")
    assert '"num_speculative_tokens": 3' in " ".join(recipe.serve_args)
    workflow = {f.key: f.default for f in model_specs.spec_for("glm-ocr").value("settings").workflow}
    assert workflow["speculative_tokens"] == 3
    adapter = model_adapters.get_adapter("glm-ocr")
    assert adapter.capabilities.table_format == "html"
    assert '"num_speculative_tokens": 3' in " ".join(adapter.serve_command("/m", 8888))


# --- MinerU2.5 ----------------------------------------------------------------------


def test_mineru_swaps_inverted_boxes_like_the_official_client():
    # mineru_vl_utils `_convert_bbox`: inverted pairs are swapped, not dropped.
    adapter = model_adapters.get_adapter("mineru2.5")
    raw = "<|box_start|>500 600 100 200<|box_end|><|ref_start|>text<|ref_end|>"
    items = adapter.parse_layout(raw)
    assert items and items[0]["bbox"] == [100, 200, 500, 600]


def test_mineru_client_is_the_current_release():
    # PyPI mineru-vl-utils 2.0.8 (2026-10-29): vllm >=0.19.1,<0.31; mlx-vlm >=0.7,<0.8.
    from app.services import mineru_mlx_runtime

    recipe = model_specs.spec_for("mineru2.5").value("serve")
    assert recipe.pip_extra == ("mineru-vl-utils[vllm]==2.0.8",)
    assert "mineru-vl-utils[mlx]==2.0.8" in mineru_mlx_runtime.PACKAGES


# --- DeepSeek-OCR-2 -----------------------------------------------------------------


def test_deepseek_ref_with_several_boxes_keeps_every_box_on_the_vendor_scale():
    # run_dpsk_ocr2_image.py: `eval(det)` is a list of boxes, scaled by /999.
    adapter = model_adapters.get_adapter("deepseek-ocr")
    raw = "<|ref|>text<|/ref|><|det|>[[0, 0, 999, 499], [0, 500, 999, 999]]<|/det|>\nHello"
    items = adapter.parse_layout(raw)
    assert [item["bbox"] for item in items] == [
        pytest.approx([0, 0, 1000, 499 * 1000 / 999]),
        pytest.approx([0, 500 * 1000 / 999, 1000, 1000]),
    ]
    assert items[0]["content"] == "Hello" and items[1]["content"] == ""


# --- LightOnOCR-3 -------------------------------------------------------------------


def test_lightonocr_parser_follows_the_vendor_marker_and_labels():
    # lightonocr/grounding.py: MARKER matches anywhere, any `[A-Za-z_][\w-]*` label;
    # LABELS includes `section`.
    adapter = model_adapters.get_adapter("lightonocr-3")
    raw = "intro ![section](10,10,900,60) Arrivals ![Text](10,70,900,200)Body"
    items = adapter.parse_layout(raw)
    assert [(item["label"], item["content"]) for item in items] == [
        ("Section-header", "Arrivals"),
        ("Text", "Body"),
    ]


# --- mlx-vlm ------------------------------------------------------------------------


def test_mlx_runtime_pins_the_measured_mlx_vlm():
    # The MLX paths were measured on mlx_vlm 0.7.1 (model_adapters comments, benchmarks.md).
    from app.services import mlx_runtime

    assert mlx_runtime.PACKAGES == ["mlx-vlm==0.7.1"]


# --- Gemini 4 Argon -----------------------------------------------------------------


def test_gemini_requests_wait_as_long_as_google_recommends(monkeypatch):
    # Google's Gemini 4 guide: raise client timeouts to at least 15 minutes.
    client = infmod.VllmClient(
        url="https://opencode.ai/inference/go/google/v1beta", model="gemini-4-argon", api_key="k",
        adapter=model_adapters.get_adapter("gemini-argon"), provider="opencode",
    )
    seen = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}

    def post(_url, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    client._generate_content(Image.new("RGB", (8, 8), "white"), "p", total_timeout=300)
    assert seen["timeout"][1] >= 900


# --- Modal --------------------------------------------------------------------------


class _Response:
    def __init__(self, status: int, *, location: str = "", body: dict | None = None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self._body = body or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def close(self):
        return None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise infmod.requests.HTTPError(str(self.status_code))

    def json(self):
        return self._body


def test_modal_long_requests_follow_the_303_to_the_result(monkeypatch):
    # Modal web endpoints: after 150 s the answer is a 303 to the same URL with a
    # result id; the result URL blocks until the call completes.
    calls = []
    done = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}

    def post(url, **kwargs):
        calls.append(("POST", url))
        return _Response(303, location="/v1/chat/completions?__modal_function_call_id=fc-1")

    def get(url, **kwargs):
        calls.append(("GET", url))
        assert kwargs["headers"]["Authorization"] == "Bearer k"
        return _Response(200, body=done)

    monkeypatch.setattr(infmod.requests, "post", post)
    monkeypatch.setattr(infmod.requests, "get", get)
    client = infmod.VllmClient(
        url="https://ws--dots.modal.run/v1", api_key="k", max_retries=0,
        adapter=model_adapters.get_adapter("dots-ocr"),
    )
    assert client._chat(Image.new("RGB", (8, 8), "white"), "p", task="end2end") == "{}"
    assert calls == [
        ("POST", "https://ws--dots.modal.run/v1/chat/completions"),
        ("GET", "https://ws--dots.modal.run/v1/chat/completions?__modal_function_call_id=fc-1"),
    ]


def test_a_redirect_to_another_host_is_not_followed_with_the_credentials(monkeypatch):
    def post(url, **kwargs):
        return _Response(303, location="https://elsewhere.example/steal")

    def get(url, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("credentials sent to another host")

    monkeypatch.setattr(infmod.requests, "post", post)
    monkeypatch.setattr(infmod.requests, "get", get)
    client = infmod.VllmClient(
        url="https://ws--dots.modal.run/v1", api_key="k", max_retries=0,
        adapter=model_adapters.get_adapter("dots-ocr"),
    )
    with pytest.raises(Exception):
        client._chat(Image.new("RGB", (8, 8), "white"), "p", task="end2end")


# --- Vast.ai ------------------------------------------------------------------------


def test_vast_instance_pages_use_after_token(monkeypatch):
    # vast-cli `vastai/api/instances.py`: the next page is requested with `after_token`.
    from app.services import cloud_manager

    seen = []

    def call(_key, _method, _path, *, params=None, **_):
        seen.append(dict(params or {}))
        if len(seen) == 1:
            return {"instances": [{"id": 1}], "next_token": "t2"}
        return {"instances": [{"id": 2}], "next_token": None}

    monkeypatch.setattr(cloud_manager, "_vast_call", call)
    assert [item["id"] for item in cloud_manager._fetch_vast_instances("k")] == [1, 2]
    assert seen[1].get("after_token") == "t2" and "next_token" not in seen[1]


@pytest.mark.parametrize("profile", ["monkey", "glm", "paddle", "swift"])
def test_vast_training_onstart_fits_the_documented_limit(profile):
    # Vast create-instance API: `onstart` is limited to 4048 characters.
    from app.services import auto_training

    onstart = auto_training._onstart(profile, "monkeyocrv2-parsing")
    assert len(onstart) <= 4048
    encoded = re.search(r"echo '?([A-Za-z0-9+/=]+)'? \| base64 -d \| gunzip", onstart).group(1)
    script = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    assert script.startswith("#!")
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n"], input=script, text=True).returncode == 0


# --- Xiaomi-OCR-0 -------------------------------------------------------------------


def test_xiaomi_offers_the_vendor_repetition_retry_off_by_default():
    # evaluation_pipeline/twostage_img2md.py: `--retry-repetitive` (off by default)
    # reruns suspicious regions with `--retry-presence-penalty` 1.5.
    fields = {f.key: f for f in model_specs.spec_for("xiaomi-ocr-0").value("settings").workflow}
    assert fields["retry_repetitive"].default is False
    assert fields["retry_presence_penalty"].default == 1.5


def test_repetition_detector_uses_the_vendor_thresholds():
    # postprocess/repetition_guard.py defaults: same-character run 128, 8 repeats,
    # 512 repeated characters, ratio 0.5.
    assert protocol.looks_repetitive("a" * 600)
    assert protocol.looks_repetitive("Ship arrived at Genoa\n" * 40)
    assert protocol.looks_repetitive("ok", finish_reason="length")
    assert not protocol.looks_repetitive("Ship arrived at Genoa\nBrig Maria sailed\n" * 3)


def test_xiaomi_retries_a_looping_region_when_asked(tmp_path, monkeypatch):
    from app import config
    from app.db import init_db
    from app.services import model_settings, paddle_official

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "xiaomi.db")
    init_db()
    model_settings.save_settings("xiaomi-ocr-0", {"workflow": {"retry_repetitive": True}})
    monkeypatch.setattr(paddle_official, "detect_layout", lambda page, threshold: [
        {"bbox": [0, 0, 1000, 1000], "label": "text"},
    ])
    client = infmod.VllmClient(url="https://example.invalid/v1", adapter=model_adapters.get_adapter("xiaomi-ocr-0"))
    calls = []

    def chat(_crop, _prompt, *, sampling=None, **_kwargs):
        calls.append(dict(sampling or {}))
        return "Brig Maria\n" * 80 if len(calls) == 1 else "Brig Maria"

    monkeypatch.setattr(client, "_chat", chat)
    blocks = client.xiaomi_native_page(Image.new("RGB", (64, 64), "white"))
    assert [b["content"] for b in blocks] == ["Brig Maria"]
    assert calls[1]["presence_penalty"] == 1.5


def test_a_saved_legacy_gemini_id_reaches_the_gateway_as_the_catalog_id():
    # The OpenCode Go catalog lists only `gemini-4-argon`; a configuration saved before
    # the audit may still say `gemini-4-argon-contributor`.
    client = infmod.VllmClient(
        url="https://opencode.ai/inference/go/google/v1beta", model="gemini-4-argon-contributor",
        api_key="k", adapter=model_adapters.get_adapter("gemini-argon"), provider="opencode",
    )
    assert client.model == "gemini-4-argon"


def test_teleocr_prompts_carry_the_vendor_leading_newline():
    # TeleOCR_client.py: every DEFAULT_PROMPTS and LAYOUT_PROMPTS entry starts with "\n".
    adapter = model_adapters.get_adapter("teleocr")
    assert adapter.prompt_for("text") == "\nPlease output the text content from the image."
    assert adapter.prompt_for("table") == "\nThis is the image of a table. Please output the table in OTSL format."
    assert adapter.prompt_for("layout") == "\nAnalyze the image layout."
