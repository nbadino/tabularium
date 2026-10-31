"""Xiaomi-OCR-0: the vendor's region mode, PP-DocLayoutV3 then one prompt per crop."""
from __future__ import annotations

import json
import subprocess

import pytest
from PIL import Image

from app.services import inference as infmod
from app.services import model_registry, paddle_official
from app.services.model_adapters import get_adapter, supported_prefill_modes
from app.services.prefill import native_mode


def test_xiaomi_adapter_speaks_the_vendor_protocol():
    adapter = get_adapter("xiaomi-ocr-0")
    assert adapter.prompt_for("text") == "Extract the text in the image."
    assert adapter.prompt_for("table") == "Parse the table in the image into OTSL."
    assert adapter.prompt_for("formula") == (
        "Identify the formula in the image and represent it using LATEX format."
    )
    # The model has no box output: layout comes from the detector, never from a prompt.
    for task in ("layout", "end2end"):
        with pytest.raises(NotImplementedError):
            adapter.prompt_for(task)
    assert adapter.request_overrides("table") == {"chat_template_kwargs": {"enable_thinking": False}}
    assert native_mode(adapter) == "official"
    assert supported_prefill_modes(adapter)["supports_native"]
    # Apple Silicon: mlx-vlm loads the original weights (verified with mlx_vlm 0.7.1).
    assert adapter.capabilities.local_runtimes == ("vllm", "mlx-vlm")
    assert adapter.capabilities.local_mlx_repo == "xiaomi-research/Xiaomi-OCR-0"


def test_xiaomi_native_page_crops_each_region_with_its_task_prompt(monkeypatch):
    seen_layout: list[tuple[int, int]] = []

    def detect_layout(image, *, threshold):
        seen_layout.append(image.size)
        assert threshold == 0.25
        return [
            {"label": "doc_title", "bbox": [0, 0, 1000, 100]},
            {"label": "table", "bbox": [0, 100, 500, 600]},
            {"label": "display_formula", "bbox": [500, 100, 1000, 200]},
            {"label": "image", "bbox": [500, 600, 1000, 1000]},
            {"label": "not-a-vendor-label", "bbox": [0, 900, 100, 1000]},
        ]

    calls: list[tuple[tuple[int, int], str]] = []

    def chat(self, image, prompt, max_tokens=4096, min_pixels=None, max_pixels=None, **kwargs):
        calls.append((image.size, prompt))
        assert max_pixels is None
        return f"  {prompt.split()[0]} out  "

    monkeypatch.setattr(paddle_official, "detect_layout", detect_layout)
    monkeypatch.setattr(infmod.VllmClient, "_chat", chat)
    client = infmod.VllmClient(
        url="http://127.0.0.1:8000", model="xiaomi-ocr-0", adapter=get_adapter("xiaomi-ocr-0"),
    )

    items = client.xiaomi_native_page(Image.new("RGB", (4096, 2048), "white"))

    # The page is fitted to the vendor's 2048 px edge before layout and cropping.
    assert seen_layout == [(2048, 1024)]
    assert items == [
        {"bbox": [0, 0, 1000, 100], "label": "Title", "content": "Extract out"},
        {"bbox": [0, 100, 500, 600], "label": "Table", "content": "Parse out"},
        {"bbox": [500, 100, 1000, 200], "label": "Formula", "content": "Identify out"},
        {"bbox": [500, 600, 1000, 1000], "label": "Picture", "content": ""},
    ]
    assert sorted(calls) == sorted([
        ((2048, 102), "Extract the text in the image."),
        ((1024, 512), "Parse the table in the image into OTSL."),
        ((1024, 102), "Identify the formula in the image and represent it using LATEX format."),
    ])


def test_detect_layout_scales_detector_pixels_like_the_vendor(monkeypatch):
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda: True)

    def run(argv, **kwargs):
        assert argv[-1] == "0.3"
        boxes = [
            {"label": "text", "coordinate": [10.0, 20.0, 190.0, 80.0]},
            {"label": "table", "coordinate": [-5.0, 50.0, 400.0, 90.0]},  # clamped to the page
            {"label": "seal", "coordinate": [50.0, 50.0, 50.0, 60.0]},  # empty: dropped
        ]
        return subprocess.CompletedProcess(argv, 0, stdout="log\n" + json.dumps(boxes), stderr="")

    monkeypatch.setattr(paddle_official.subprocess, "run", run)
    regions = paddle_official.detect_layout(Image.new("RGB", (200, 100)), threshold=0.3)
    assert regions == [
        {"label": "text", "bbox": [50, 200, 950, 800]},
        {"label": "table", "bbox": [0, 500, 1000, 900]},
    ]


def test_the_paddle_runtime_follows_the_adapter_declaration():
    assert model_registry._needs_paddle_runtime("paddleocr-vl")
    assert model_registry._needs_paddle_runtime("xiaomi-ocr-0")
    assert not model_registry._needs_paddle_runtime("surya-ocr-2.5")
    assert not model_registry._needs_paddle_runtime("does-not-exist")
