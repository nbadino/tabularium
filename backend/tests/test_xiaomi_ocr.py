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

    def detect_layout(image, *, threshold, model_dir):
        seen_layout.append(image.size)
        assert threshold == 0.25
        assert model_dir is None  # the official weights by default
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

    # Layout runs on the 2048 px fitted page; crops come from the full-resolution scan
    # (capped per crop to XIAOMI_IMAGE_MAX_TOKENS * 1024 = 4,194,304 px, matching training
    # and gold_eval so dense tables never suffer double-downscale blur).
    assert seen_layout == [(2048, 1024)]
    assert items == [
        {"bbox": [0, 0, 1000, 100], "label": "Title", "content": "Extract out"},
        {"bbox": [0, 100, 500, 600], "label": "Table", "content": "Parse out"},
        {"bbox": [500, 100, 1000, 200], "label": "Formula", "content": "Identify out"},
        {"bbox": [500, 600, 1000, 1000], "label": "Picture", "content": ""},
    ]
    assert sorted(calls) == sorted([
        ((4096, 204), "Extract the text in the image."),
        ((2048, 1024), "Parse the table in the image into OTSL."),
        ((2048, 205), "Identify the formula in the image and represent it using LATEX format."),
    ])


def test_detect_layout_scales_detector_pixels_like_the_vendor(monkeypatch):
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda: True)
    monkeypatch.setattr(paddle_official.paddle_runtime, "cpu_unsupported_reason", lambda: None)

    def run(argv, **kwargs):
        assert argv[-2:] == ["0.3", ""]
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


def test_detect_layout_passes_a_fine_tuned_model_dir(monkeypatch):
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda: True)
    monkeypatch.setattr(paddle_official.paddle_runtime, "cpu_unsupported_reason", lambda: None)
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(paddle_official.subprocess, "run", run)
    paddle_official.detect_layout(Image.new("RGB", (20, 10)), threshold=0.3, model_dir="/runs/x/best_model/inference")
    assert seen[0][-1] == "/runs/x/best_model/inference"
    assert 'model_dir": model_dir' in paddle_official._LAYOUT_RUNNER  # noqa: SLF001


def test_layout_model_resolves_a_finished_paddlex_run_only(monkeypatch, tmp_path):
    from app import config
    from app.services import trainer

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    run_dir = tmp_path / "4" / "runs" / "run_1"
    inference = run_dir / "checkpoints" / "pp-doclayoutv3_full" / "best_model" / "inference"
    inference.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"toolchain": "paddlex", "state": "finished"}))
    (run_dir / "eval_trained.json").write_text(json.dumps({"metrics": {"AP": 0.9}}))
    assert trainer.layout_model_dir("official") is None
    with pytest.raises(ValueError):  # not exported yet: never a silent fall-back
        trainer.layout_model_dir("4/run_1")
    for name in ("inference.json", "inference.pdiparams", "inference.yml"):
        (inference / name).write_text("x")
    assert trainer.layout_model_dir("4/run_1") == str(inference)
    with pytest.raises(ValueError):
        trainer.layout_model_dir("../../etc/run_1")
    [item] = trainer.layout_checkpoints()
    assert item["id"] == "4/run_1"
    assert item["eval_trained"] == {"metrics": {"AP": 0.9}}
    assert item["eval_base"] is None


def test_the_paddle_runtime_follows_the_adapter_declaration():
    assert model_registry._needs_paddle_runtime("paddleocr-vl")
    assert model_registry._needs_paddle_runtime("xiaomi-ocr-0")
    assert not model_registry._needs_paddle_runtime("surya-ocr-2.5")
    assert not model_registry._needs_paddle_runtime("does-not-exist")


def _custom(base: str, **row):
    from app.services.model_adapters import CustomVllmAdapter

    return CustomVllmAdapter({
        "id": f"custom-{base}-run-1", "display_name": "fine-tuned", "served_model_name": "ft",
        "hf_repo": "xiaomi-research/Xiaomi-OCR-0", "hf_revision": None,
        "base_adapter_id": base, **row,
    })


def test_a_fine_tuned_xiaomi_checkpoint_serves_its_own_weights_on_mlx(tmp_path):
    from app.services import hardware

    adapter = _custom("xiaomi-ocr-0", source_path=str(tmp_path), source_train_type="full")
    cap = adapter.capabilities
    assert "mlx-vlm" in cap.local_runtimes
    # Never the base checkpoint: that would serve the vendor's weights under the fine-tune's name.
    assert cap.local_mlx_repo == str(tmp_path)
    assert cap.local_mlx_repo_choices == ()
    assert hardware.pick_mlx_repo(cap, {"apple_silicon": True, "memory_gb": 64, "available_gb": 32}) == str(tmp_path)


def test_a_fine_tune_without_local_mlx_weights_says_mlx_is_not_available(tmp_path):
    lora = _custom("xiaomi-ocr-0", source_path=str(tmp_path), source_train_type="lora")
    assert "mlx-vlm" not in lora.capabilities.local_runtimes
    assert lora.capabilities.local_mlx_repo == ""
    # PaddleOCR-VL's MLX checkpoint is a separate 4-bit port: fine-tuned weights are not it.
    paddle = _custom("paddleocr-vl", source_path=str(tmp_path), source_train_type="full")
    assert "mlx-vlm" not in paddle.capabilities.local_runtimes
    assert paddle.capabilities.local_mlx_repo == ""


def test_a_hub_repo_on_the_xiaomi_architecture_serves_that_repo_on_mlx():
    adapter = _custom("xiaomi-ocr-0", hf_repo="someone/xiaomi-ocr-0-finetune", source_path=None)
    assert adapter.capabilities.local_mlx_repo == "someone/xiaomi-ocr-0-finetune"


def test_apple_silicon_serves_xiaomi_and_finetunes_through_dedicated_turbo_server(monkeypatch, tmp_path):
    from pathlib import Path
    from app import config
    from app.services import custom_models, mlx_runtime, serve_manager, serve_recipes

    assert serve_recipes.recipe_for("xiaomi-ocr-0").local_serve == "mlx-xiaomi"
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")

    monkeypatch.setattr(
        serve_manager.hardware,
        "pick_serve_runtime",
        lambda capabilities, machine=None: serve_manager.hardware.RUNTIME_MLX,
    )
    monkeypatch.setattr(mlx_runtime, "ensure_ready", lambda: None)
    monkeypatch.setattr(mlx_runtime, "python_bin", lambda: Path("/tmp/mlx-python"))
    monkeypatch.setattr(
        serve_manager.hardware,
        "summary",
        lambda: {
            "apple_silicon": True, "cuda": {"devices": []}, "memory_gb": 24.0,
            "runtimes": {"vllm": {"supported": False}, "mlx-vlm": {"supported": True}},
            "usable_runtimes": ["mlx-vlm"],
        },
    )
    captured: list[list[str]] = []

    class FakeProc:
        pid = 4399

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        captured.append(list(argv))
        return FakeProc()

    monkeypatch.setattr(serve_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(serve_manager, "_reclaim_port", lambda port, settle=10.0: None)
    monkeypatch.setattr(serve_manager, "_set_phase", lambda *a, **k: None)
    monkeypatch.setattr(serve_manager, "is_installed", lambda adapter_id: False)
    monkeypatch.setattr(serve_manager, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(serve_manager.jobstore, "start_process_job", lambda **kw: 999)
    monkeypatch.setattr(serve_manager, "_mark_job", lambda *a, **k: None)

    serve_manager.start("xiaomi-ocr-0", port=18921)
    assert captured[-1][1].endswith("xiaomi_mlx_server.py"), captured[-1]
    assert captured[-1][captured[-1].index("--model") + 1] == "xiaomi-research/Xiaomi-OCR-0"
    assert captured[-1][captured[-1].index("--port") + 1] == "18921"

    # A fine-tuned full checkpoint inherits the base model's dedicated MLX runner too.
    ft_dir = tmp_path / "ft_ckpt"
    ft_dir.mkdir()
    ft_row = {
        "id": "custom-xiaomi-ocr-0-run-1",
        "display_name": "fine-tuned",
        "served_model_name": "ft",
        "hf_repo": "xiaomi-research/Xiaomi-OCR-0",
        "hf_revision": None,
        "base_adapter_id": "xiaomi-ocr-0",
        "source_path": str(ft_dir),
        "source_train_type": "full",
    }
    monkeypatch.setattr(
        custom_models, "get",
        lambda aid: ft_row if aid == ft_row["id"] else None,
    )
    serve_manager.start(ft_row["id"], port=18922)
    assert captured[-1][1].endswith("xiaomi_mlx_server.py"), captured[-1]
    assert captured[-1][captured[-1].index("--model") + 1] == str(ft_dir)
    serve_manager._ACTIVE_PROC = None
    serve_manager._ACTIVE_INFO = {}


def test_fine_tuned_xiaomi_adapter_runs_native_region_pipeline(monkeypatch, tmp_path):
    ft_adapter = _custom("xiaomi-ocr-0", source_path=str(tmp_path), source_train_type="full")
    assert ft_adapter.native_runner == "xiaomi"
    assert ft_adapter.official_runtime == "paddle"
    assert ft_adapter.region_max_tokens == 16_384
    assert ft_adapter.page_max_edge == 2048

    monkeypatch.setattr(
        paddle_official,
        "detect_layout",
        lambda image, *, threshold, model_dir=None: [{"label": "table", "bbox": [0, 0, 1000, 1000]}],
    )
    monkeypatch.setattr(
        infmod.VllmClient,
        "_chat",
        lambda self, image, prompt, **kw: "<fcel>OK<nl>",
    )
    client = infmod.VllmClient(
        url="http://127.0.0.1:8000/v1", model=str(tmp_path), adapter=ft_adapter,
    )
    items = client.xiaomi_native_page(Image.new("RGB", (1000, 800), "white"))
    assert items == [{"bbox": [0, 0, 1000, 1000], "label": "Table", "content": "<fcel>OK<nl>"}]


def test_xiaomi_stage1_recovers_misclassified_and_split_borderless_tables(monkeypatch):
    """PP-DocLayoutV3 occasionally labels borderless multi-column registers as `content`
    or splits one register across two stacked `table` boxes. Verify both are repaired when
    geometric multi-column structure holds, while prose and separated tables stay untouched."""
    monkeypatch.setattr(
        infmod,
        "_looks_like_borderless_table",
        lambda image, px_bbox: (px_bbox[2] - px_bbox[0]) >= 600,
    )

    # Case 1: `content` region passing multi-column check is promoted to `table`;
    # narrow `content` failing the check stays `Text`.
    repaired_1 = infmod._repair_xiaomi_layout_regions(
        Image.new("RGB", (1000, 1000), "white"),
        [
            {"label": "header", "bbox": [40, 20, 900, 50]},
            {"label": "content", "bbox": [30, 60, 970, 950]},
            {"label": "content", "bbox": [50, 955, 300, 990]},
        ],
    )
    assert repaired_1 == [
        {"label": "header", "bbox": [40, 20, 900, 50]},
        {"label": "table", "bbox": [30, 60, 970, 950]},
        {"label": "content", "bbox": [50, 955, 300, 990]},
    ]

    # Case 2: Two stacked `table` boxes with matching horizontal span and no intervening
    # block coalesce into one table; a third `table` below a `paragraph_title` stays separate.
    repaired_2 = infmod._repair_xiaomi_layout_regions(
        Image.new("RGB", (1000, 1000), "white"),
        [
            {"label": "table", "bbox": [25, 60, 955, 380]},
            {"label": "table", "bbox": [30, 435, 960, 700]},
            {"label": "paragraph_title", "bbox": [30, 720, 400, 750]},
            {"label": "table", "bbox": [30, 760, 960, 950]},
        ],
    )
    assert repaired_2 == [
        {"label": "table", "bbox": [25, 60, 960, 700]},
        {"label": "paragraph_title", "bbox": [30, 720, 400, 750]},
        {"label": "table", "bbox": [30, 760, 960, 950]},
    ]


def test_xiaomi_stage1_joins_register_split_side_by_side(monkeypatch):
    """A register cut into a left half (stacked in three pieces, none half the page wide) and
    a right half becomes one table, as on LDI_1920_Apr1_Page_057 (boxes from that page).
    The narrow strip of the facing page at the right edge stays out; two tables far apart
    stay two."""
    monkeypatch.setattr(
        infmod,
        "_looks_like_borderless_table",
        lambda image, px_bbox: (px_bbox[2] - px_bbox[0]) >= 600,
    )
    page = Image.new("RGB", (1000, 1000), "white")
    repaired = infmod._repair_xiaomi_layout_regions(
        page,
        [
            {"label": "header", "bbox": [360, 12, 682, 31]},
            {"label": "text", "bbox": [953, 20, 1000, 34]},
            {"label": "table", "bbox": [38, 41, 448, 439]},
            {"label": "table", "bbox": [47, 444, 459, 679]},
            {"label": "table", "bbox": [56, 683, 466, 961]},
            {"label": "table", "bbox": [433, 53, 983, 955]},
            {"label": "table", "bbox": [945, 311, 1000, 485]},
        ],
    )
    assert repaired == [
        {"label": "header", "bbox": [360, 12, 682, 31]},
        {"label": "text", "bbox": [953, 20, 1000, 34]},
        {"label": "table", "bbox": [38, 41, 983, 961]},
        {"label": "table", "bbox": [945, 311, 1000, 485]},
    ]

    # The facing page can be wider than a column (LDI_1920_Apr1_Page_063: 869-1000) and can
    # come as `text` (Page_123): it is never joined, and it does not block the halves.
    facing = infmod._repair_xiaomi_layout_regions(
        page,
        [
            {"label": "table", "bbox": [29, 60, 404, 355]},
            {"label": "table", "bbox": [392, 61, 975, 772]},
            {"label": "table", "bbox": [869, 30, 1000, 831]},
            {"label": "text", "bbox": [880, 64, 999, 627]},
        ],
    )
    assert facing == [
        {"label": "table", "bbox": [29, 60, 975, 772]},
        {"label": "table", "bbox": [869, 30, 1000, 831]},
        {"label": "text", "bbox": [880, 64, 999, 627]},
    ]

    apart = [
        {"label": "table", "bbox": [30, 60, 400, 950]},
        {"label": "table", "bbox": [600, 60, 970, 950]},
    ]
    assert infmod._repair_xiaomi_layout_regions(page, [dict(r) for r in apart]) == apart
    # Two short adjacent tables (an advert beside a box) are not a split register.
    short = [
        {"label": "table", "bbox": [30, 300, 480, 420]},
        {"label": "table", "bbox": [490, 300, 970, 420]},
    ]
    assert infmod._repair_xiaomi_layout_regions(page, [dict(r) for r in short]) == short

