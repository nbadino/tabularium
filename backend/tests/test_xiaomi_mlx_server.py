"""Unit tests for the dedicated Xiaomi-OCR-0 MLX server (`xiaomi_mlx_server.py`).

Tests pure protocol, concurrency, image security, greedy-vs-sampling routing, and
local MTP drafter source resolution without requiring `mlx` in the dashboard venv.
"""
from __future__ import annotations

import base64
import io
import json
import struct
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from app.services import xiaomi_mlx_server as server


def _write_minimal_safetensors(path: Path, tensor_names: list[str]) -> None:
    header: dict = {}
    offset = 0
    for name in tensor_names:
        header[name] = {
            "dtype": "BF16",
            "shape": [1],
            "data_offsets": [offset, offset + 2],
        }
        offset += 2
    header_bytes = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(header_bytes)) + header_bytes + (b"\x00\x00" * len(tensor_names)))


def _png_data_uri(size: tuple[int, int] = (24, 16), color: str = "white") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def test_generations_on_the_shared_model_do_not_overlap():
    first_started, release_first = threading.Event(), threading.Event()
    order: list[str] = []

    def slow():
        order.append("first:start")
        first_started.set()
        release_first.wait(5)
        yield "a"
        order.append("first:end")

    def fast():
        order.append("second:start")
        yield "b"

    first = server._exclusive(slow, lambda: order.append("first:released"))
    worker = threading.Thread(target=lambda: list(first))
    worker.start()
    first_started.wait(5)
    second = threading.Thread(target=lambda: list(server._exclusive(fast, lambda: None)))
    second.start()
    second.join(0.3)
    assert "second:start" not in order, "the second generation started on a busy model"
    release_first.set()
    worker.join(5)
    second.join(5)
    assert order == ["first:start", "first:end", "first:released", "second:start"]


def test_a_slow_reader_does_not_hold_the_model():
    stalled = server._exclusive(lambda: iter("abc"), lambda: None)
    assert next(stalled) == "a"

    done = threading.Event()
    threading.Thread(
        target=lambda: (list(server._exclusive(lambda: iter("x"), lambda: None)), done.set()),
        daemon=True,
    ).start()
    assert done.wait(2), "the model stayed locked while the first reader was idle"
    assert list(stalled) == ["b", "c"]


def test_a_closed_reader_stops_the_generation():
    produced: list[int] = []

    def endless():
        while True:
            produced.append(1)
            yield "t"

    stream = server._exclusive(endless, lambda: None)
    next(stream)
    stream.close()
    time.sleep(0.2)
    count = len(produced)
    time.sleep(0.2)
    assert len(produced) == count, "the generation kept running after the reader left"


def test_a_failed_generation_still_frees_memory_and_reports_the_error():
    released: list[bool] = []

    def broken():
        yield "a"
        raise RuntimeError("metal error")

    stream = server._exclusive(broken, lambda: released.append(True))
    assert next(stream) == "a"
    with pytest.raises(RuntimeError, match="metal"):
        next(stream)
    assert released == [True]


def test_decode_image_accepts_inline_data_uri_and_rejects_paths_or_remote_urls():
    uri = _png_data_uri((32, 20), "red")
    img, digest = server._decode_image(uri)
    assert img.size == (32, 20)
    assert img.mode == "RGB"
    assert len(digest) == 64

    for forbidden in ("http://example.com/a.png", "https://example.com/a.png", "file:///etc/passwd", "/tmp/local.png"):
        with pytest.raises(ValueError, match="data:"):
            server._decode_image(forbidden)


def test_extract_handles_multimodal_messages_and_ignores_malformed_parts():
    uri = _png_data_uri((16, 16))
    messages = [
        {"role": "system", "content": None},
        {
            "role": "user",
            "content": [
                "not-a-dict",
                {"type": "image_url", "image_url": {"url": uri}},
                {"type": "text", "text": "<image>Parse the table in the image into OTSL."},
            ],
        },
    ]
    text, images = server._extract(messages)
    assert server._normalize_text(text) == "Parse the table in the image into OTSL."
    assert len(images) == 1
    assert images[0][0].size == (16, 16)


def test_greedy_neutral_detection_routes_turbo_vs_sampling():
    assert server._is_greedy_neutral({"temperature": 0.0})
    assert server._is_greedy_neutral({"temperature": 0.0, "top_p": 0.8, "presence_penalty": 0.0, "repetition_penalty": 1.0})
    assert not server._is_greedy_neutral({"temperature": 0.2})
    assert not server._is_greedy_neutral({"temperature": 0.0, "presence_penalty": 1.5})
    assert not server._is_greedy_neutral({"temperature": 0.0, "repetition_penalty": 1.1})
    assert not server._is_greedy_neutral({"temperature": 0.0, "frequency_penalty": 0.1})


def test_resolve_mtp_source_finds_self_or_base_cache_without_network(tmp_path):
    # 1. Base checkpoint directory already contains mtp.* tensors -> returns itself.
    base_dir = tmp_path / "base_xiaomi"
    base_dir.mkdir()
    _write_minimal_safetensors(base_dir / "model.safetensors", ["model.embed_tokens.weight", "mtp.fc.weight"])
    assert server._has_mtp_weights(base_dir) is True
    assert server._resolve_mtp_source_dir(str(base_dir)) == str(base_dir)

    # 2. Fine-tuned checkpoint directory had mtp.* stripped by ms-swift -> grafts from cached base!
    ft_dir = tmp_path / "ft_xiaomi"
    ft_dir.mkdir()
    _write_minimal_safetensors(ft_dir / "model.safetensors", ["model.embed_tokens.weight", "model.layers.0.mlp.down_proj.weight"])
    assert server._has_mtp_weights(ft_dir) is False
    assert server._resolve_mtp_source_dir(
        str(ft_dir),
        local_snapshot_lookup=lambda repo_id: str(base_dir) if repo_id == "xiaomi-research/Xiaomi-OCR-0" else None,
    ) == str(base_dir)

    # 3. Offline machine without cached base -> returns None cleanly (falls back to S=1 decode).
    assert server._resolve_mtp_source_dir(
        str(ft_dir),
        local_snapshot_lookup=lambda _repo_id: (_ for _ in ()).throw(FileNotFoundError("not cached")),
    ) is None


def test_standalone_cli_entrypoint_does_not_shadow_stdlib_collections():
    """Executing `xiaomi_mlx_server.py` as a script must not let `services/collections.py`
    shadow Python's stdlib `collections` module."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(Path(server.__file__)), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--model" in proc.stdout


def test_default_local_snapshot_lookup_handles_partial_mlx_vlm_cache(monkeypatch, tmp_path):
    """`mlx-vlm` caches only `*.json`/`*.safetensors`, causing bare `snapshot_download`
    with `local_files_only=True` to fail with `IncompleteSnapshotError`. `_default_local_snapshot_lookup`
    must resolve the snapshot via `try_to_load_from_cache` without requiring `README.md`."""
    import huggingface_hub

    snap = tmp_path / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    cfg = snap / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    _write_minimal_safetensors(snap / "model.safetensors", ["mtp.fc.weight"])

    monkeypatch.setattr(
        huggingface_hub,
        "try_to_load_from_cache",
        lambda repo_id, filename: str(cfg) if filename == "config.json" else None,
    )
    monkeypatch.setattr(
        huggingface_hub,
        "snapshot_download",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bare snapshot_download should not be needed")),
    )
    resolved = server._default_local_snapshot_lookup("xiaomi-research/Xiaomi-OCR-0")
    assert resolved == str(snap)
    assert server._has_mtp_weights(Path(resolved)) is True


def test_adaptive_dispatcher_routes_solo_to_mtp_and_burst_to_continuous_batch():
    """A single isolated request takes the low-latency solo MTP path; concurrent
    arrivals coalesce into the continuous batch handler without blocking each other."""
    routes: list[tuple[str, int]] = []

    def solo_handler(item):
        routes.append(("solo", 1))
        item.out_queue.put("solo:" + item.payload)
        item.out_queue.put(server._DONE)

    def batch_handler(initial_items, inbox):
        batch = list(initial_items)
        while not inbox.empty():
            batch.append(inbox.get_nowait())
        routes.append(("batch", len(batch)))
        for it in batch:
            if not it.stop_event.is_set():
                it.out_queue.put("batch:" + it.payload)
            it.out_queue.put(server._DONE)

    dispatcher = server._AdaptiveDispatcher(
        solo_handler=solo_handler,
        batch_handler=batch_handler,
        coalesce_window_s=0.04,
    )
    try:
        # 1. Isolated request -> solo fastpath
        assert list(dispatcher.submit("one")) == ["solo:one"]
        assert routes == [("solo", 1)]

        # 2. Three concurrent requests arriving together -> coalesced batch path
        results: dict[str, list[str]] = {}

        def call(tag: str) -> None:
            results[tag] = list(dispatcher.submit(tag))

        threads = [threading.Thread(target=call, args=(t,)) for t in ("p1", "p2", "p3")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)

        assert results == {"p1": ["batch:p1"], "p2": ["batch:p2"], "p3": ["batch:p3"]}
        assert any(kind == "batch" and n >= 2 for kind, n in routes), routes
    finally:
        dispatcher.shutdown()


def test_compute_mlx_memory_budget_bounds_wired_cache_and_batch_by_ram():
    gib = 1024 ** 3
    mib = 1024 ** 2

    b24 = server._compute_mlx_memory_budget(24 * gib)
    assert b24["cache_limit_bytes"] <= 256 * mib
    assert b24["wired_limit_bytes"] <= 6 * gib
    assert b24["memory_limit_bytes"] <= 11 * gib
    assert b24["max_batch_size"] == 8
    assert b24["low_memory"] is False

    b16 = server._compute_mlx_memory_budget(16 * gib)
    assert b16["cache_limit_bytes"] == 0
    assert b16["wired_limit_bytes"] <= 4 * gib
    assert b16["max_batch_size"] == 2
    assert b16["low_memory"] is True

    b36 = server._compute_mlx_memory_budget(36 * gib)
    assert b36["cache_limit_bytes"] <= 512 * mib
    assert b36["wired_limit_bytes"] <= 12 * gib
    assert b36["max_batch_size"] == 8
    assert b36["low_memory"] is False

    b64 = server._compute_mlx_memory_budget(64 * gib)
    assert b64["cache_limit_bytes"] <= 1024 * mib
    assert b64["max_batch_size"] == 8
    assert b64["low_memory"] is False

    b24_req = server._compute_mlx_memory_budget(24 * gib, requested_cache_limit_gb=0.25)
    assert b24_req["cache_limit_bytes"] == 256 * mib
    assert b24_req["max_batch_size"] == 8
    assert b24_req["low_memory"] is False

    b24_zero = server._compute_mlx_memory_budget(24 * gib, requested_cache_limit_gb=0.0)
    assert b24_zero["low_memory"] is True
    assert b24_zero["max_batch_size"] == 2


def test_has_token_loop_catches_runaway_repetition_without_false_positives():
    # Short repeating sequences are normal and ignored below `min_tokens=4096`.
    assert server._has_token_loop([10] * 3000) is False
    # Long diverse output is clean.
    assert server._has_token_loop(list(range(5000))) is False
    # 1-token loop at 4200 tokens.
    assert server._has_token_loop(list(range(3900)) + [10] * 300) is True
    # 32-token cycle repeated 10 times at 4200 tokens.
    pattern = list(range(5000, 5032))
    assert server._has_token_loop(list(range(3900)) + pattern * 10) is True

