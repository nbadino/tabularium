"""Dedicated high-throughput OpenAI-compatible MLX server for Xiaomi-OCR-0.

Why a dedicated server instead of generic `mlx_vlm.server`:
1. `Xiaomi-OCR-0` (`Qwen3_5ForConditionalGeneration`, 0.8B) ships a native 1-layer
   Multi-Token Prediction (`mtp.*`) module in `model.safetensors` that generic
   `mlx_vlm.load` strips during weight sanitization, and fine-tuned checkpoints
   exported by `ms-swift` drop `mtp.*` on save while keeping the exact same hidden
   space (`mtp_num_hidden_layers=1`).
2. Generic `Qwen3_5` greedy decoding with untied heads invokes a scalar GEMV kernel
   (`Qwen3_5ExactSpeculativeVerifier`) that bottlenecks multi-page batches, while
   `processor_config.json` leaves `longest_edge=16777216` instead of the trained
   `4096`-token (`4,194,304` px) visual budget.

This server runs inside `<root>/mlx-runtime` and combines two adaptive execution
engines behind one continuous GPU dispatcher (`_AdaptiveDispatcher`):
- **Solo Interactive Fastpath (`B = 1`)**: when a single greedy request arrives
  alone (e.g. interactive Studio prefill or single-block click), runs algebraically
  fused surgical Q8 projections + the 54 MB SLC-resident `mtp.*` drafter (`K=2`,
  Zero-Sync single command buffer) at `~340 tok/s` (`~12.4 s` per 68-row table);
- **Continuous Ragged Batch Engine (`B = 2..8`)**: when multiple requests arrive
  concurrently (bulk recognition runs, multi-page prefill, or multi-region pages),
  dynamically coalesces and streams sequences on the fly through `BatchGenerator`
  with Qwen3.5's ragged Metal SDPA kernel, `ArraysCache` GatedDeltaNet state,
  exact `bf16` input embeddings, SIMD-tiled Q8 `lm_head`, and `KV=q8` cache,
  sustaining `~504+ tok/s` (`7.2–8.3 pages/min` at full `4096`-token sharpness).
"""
from __future__ import annotations

import sys as _sys

if __name__ == "__main__" and _sys.path:
    # `backend/app/services/collections.py` shadows Python's stdlib `collections`
    # when this module is launched directly as a script (`sys.path[0]`).
    _script_dir = __file__.replace("\\", "/").rsplit("/", 1)[0]
    _sys.path[:] = [p for p in _sys.path if p.replace("\\", "/").rstrip("/") != _script_dir]

import argparse
import base64
from collections import OrderedDict, deque
from dataclasses import dataclass
import gc
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import struct
import tempfile
import threading
import time
from typing import Any, Callable
import uuid


BASE_XIAOMI_REPO = "xiaomi-research/Xiaomi-OCR-0"
# Matches `XIAOMI_IMAGE_MAX_TOKENS = 4096` of 32x32 px in `models/xiaomi_ocr.py`.
XIAOMI_TRAINED_MAX_PIXELS = 4096 * 32 * 32
_BATCH_RECYCLE_COMPLETIONS = 24

_GPU_LOCK = threading.Lock()
_DONE = object()
_SHUTDOWN = object()


def _compute_mlx_memory_budget(
    total_ram_bytes: int,
    requested_cache_limit_gb: float | None = None,
    requested_max_batch: int | None = None,
) -> dict[str, int | bool]:
    """Compute bounded cache, wired, GPU memory, and batch limits from host RAM.

    macOS panics with a watchdog timeout if MLX wires ~80% of unified memory
    plus keeps gigabytes of buffer cache alongside concurrent host workers.
    """
    gib = 1024 ** 3
    mib = 1024 ** 2
    ram_bytes = int(total_ram_bytes) if total_ram_bytes > 0 else 24 * gib
    ram_gb = ram_bytes / gib
    low_memory = (ram_gb <= 16.0) or (
        requested_cache_limit_gb is not None and float(requested_cache_limit_gb) <= 0.0
    )

    if requested_cache_limit_gb is not None:
        cache_limit_bytes = max(0, int(float(requested_cache_limit_gb) * gib))
    elif ram_gb <= 16.0:
        cache_limit_bytes = 0
    elif ram_gb <= 32.0:
        cache_limit_bytes = 256 * mib
    elif ram_gb <= 48.0:
        cache_limit_bytes = 512 * mib
    else:
        cache_limit_bytes = 1024 * mib

    if ram_gb <= 16.0:
        wired_limit_bytes = min(int(ram_bytes * 0.20), 3584 * mib)
        memory_limit_bytes = min(int(ram_bytes * 0.45), 7500 * mib)
        default_batch = 2
    elif ram_gb <= 32.0:
        wired_limit_bytes = min(int(ram_bytes * 0.25), 6 * gib)
        memory_limit_bytes = min(int(ram_bytes * 0.45), 11 * gib)
        default_batch = 8
    elif ram_gb <= 48.0:
        wired_limit_bytes = min(int(ram_bytes * 0.30), 12 * gib)
        memory_limit_bytes = min(int(ram_bytes * 0.55), 22 * gib)
        default_batch = 8
    else:
        wired_limit_bytes = min(int(ram_bytes * 0.35), 24 * gib)
        memory_limit_bytes = min(int(ram_bytes * 0.65), 48 * gib)
        default_batch = 8

    if low_memory:
        default_batch = 2

    if requested_max_batch is not None:
        cap = 2 if low_memory else 8
        max_batch_size = max(1, min(int(requested_max_batch), cap))
    else:
        max_batch_size = default_batch

    active_recycle_bytes = int(memory_limit_bytes * 0.68)
    return {
        "low_memory": low_memory,
        "cache_limit_bytes": cache_limit_bytes,
        "wired_limit_bytes": wired_limit_bytes,
        "memory_limit_bytes": memory_limit_bytes,
        "active_recycle_bytes": active_recycle_bytes,
        "max_batch_size": max_batch_size,
    }


def _has_token_loop(
    tokens: list[int],
    *,
    min_tokens: int = 4096,
    window: int = 384,
    max_period: int = 96,
    min_repeat_tokens: int = 256,
) -> bool:
    """Detect degenerate repetitive token cycles before a table blows out to 16k tokens."""
    if len(tokens) < min_tokens or len(tokens) < window:
        return False
    tail = tokens[-window:]
    max_p = min(max_period, window // 4)
    for period in range(1, max_p + 1):
        span = max(min_repeat_tokens, period * 4)
        if span <= window and tail[-span:-period] == tail[-span + period :]:
            return True
    return False


class _Failed:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


@dataclass
class GenerationChunk:
    text: str
    prompt_tokens: int
    generation_tokens: int
    finish_reason: str | None = None


@dataclass
class _WorkItem:
    payload: Any
    out_queue: queue.Queue
    stop_event: threading.Event


class _AdaptiveDispatcher:
    """Single-GPU adaptive dispatcher: isolated requests take `solo_handler`,
    concurrent bursts coalesce into `batch_handler` without blocking readers."""

    def __init__(
        self,
        *,
        solo_handler: Callable[[_WorkItem], None],
        batch_handler: Callable[[list[_WorkItem], queue.Queue], None],
        can_solo: Callable[[Any], bool] | None = None,
        after_idle: Callable[[], None] | None = None,
        coalesce_window_s: float = 0.015,
    ) -> None:
        self._solo_handler = solo_handler
        self._batch_handler = batch_handler
        self._can_solo = can_solo
        self._after_idle = after_idle
        self._coalesce_window_s = max(0.0, float(coalesce_window_s))
        self._inbox: queue.Queue = queue.Queue()
        self._worker = threading.Thread(
            target=self._loop, name="xiaomi-mlx-dispatcher", daemon=True,
        )
        self._worker.start()

    def submit(self, payload: Any):
        item = _WorkItem(
            payload=payload,
            out_queue=queue.Queue(),
            stop_event=threading.Event(),
        )
        self._inbox.put(item)
        try:
            while (msg := item.out_queue.get()) is not _DONE:
                if isinstance(msg, _Failed):
                    raise msg.exc
                yield msg
        finally:
            item.stop_event.set()

    def shutdown(self) -> None:
        self._inbox.put(_SHUTDOWN)
        self._worker.join(timeout=5.0)

    def _loop(self) -> None:
        while True:
            first = self._inbox.get()
            if first is _SHUTDOWN:
                return
            batch: list[_WorkItem] = [first]
            deadline = time.perf_counter() + self._coalesce_window_s
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    nxt = self._inbox.get(timeout=remaining)
                except queue.Empty:
                    break
                if nxt is _SHUTDOWN:
                    self._inbox.put(_SHUTDOWN)
                    break
                batch.append(nxt)

            live = [it for it in batch if not it.stop_event.is_set()]
            for cancelled in batch:
                if cancelled.stop_event.is_set():
                    cancelled.out_queue.put(_DONE)
            if not live:
                continue

            with _GPU_LOCK:
                try:
                    if (
                        len(live) == 1
                        and self._inbox.empty()
                        and (self._can_solo is None or self._can_solo(live[0].payload))
                    ):
                        self._solo_handler(live[0])
                    else:
                        self._batch_handler(live, self._inbox)
                finally:
                    if self._inbox.empty() and self._after_idle is not None:
                        self._after_idle()


def _decode_image(url: str) -> tuple[Any, str]:
    """Decode an inline `data:` image URI into `(PIL.Image, sha256_hex)`.

    Local file paths and remote `http(s)://` URLs are rejected so the loopback
    inference server cannot be used as an SSRF or local-file oracle.
    """
    if not isinstance(url, str) or not url.startswith("data:"):
        raise ValueError("only inline data: image URIs are accepted")
    from PIL import Image

    _header, sep, payload = url.partition(",")
    if not sep or not payload:
        raise ValueError("malformed data: image URI")
    raw = base64.b64decode(payload)
    digest = hashlib.sha256(raw).hexdigest()
    return Image.open(io.BytesIO(raw)).convert("RGB"), digest


def _extract(messages: list[dict]) -> tuple[str, list[tuple[Any, str]]]:
    """Extract concatenated prompt text and `(PIL.Image, sha256)` pairs from OpenAI messages."""
    texts: list[str] = []
    images: list[tuple[Any, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                texts.append(str(part["text"]))
            elif kind == "image_url":
                value = part.get("image_url")
                url = value.get("url") if isinstance(value, dict) else value
                if url:
                    images.append(_decode_image(str(url)))
            elif kind == "input_image" and part.get("image_url"):
                images.append(_decode_image(str(part["image_url"])))
    return "\n".join(texts).strip(), images


def _normalize_text(text: str) -> str:
    """Strip explicit `<image>` placeholders added for vLLM."""
    return text.replace("<image>", " ").strip()


def _is_greedy_neutral(kwargs: dict[str, Any]) -> bool:
    """True when greedy argmax is exact (temperature 0 and neutral token penalties)."""
    temp = float(kwargs.get("temperature") or 0.0)
    if temp > 0.0:
        return False
    if float(kwargs.get("presence_penalty") or 0.0) != 0.0:
        return False
    if float(kwargs.get("frequency_penalty") or 0.0) != 0.0:
        return False
    rep = kwargs.get("repetition_penalty")
    if rep is not None and float(rep) != 1.0:
        return False
    if kwargs.get("logit_bias"):
        return False
    return True


def _safetensors_file_has_mtp(path: Path) -> bool:
    """Inspect only the JSON header of a `.safetensors` file (`< 1 ms`, zero tensor reads)."""
    try:
        with path.open("rb") as fh:
            raw_len = fh.read(8)
            if len(raw_len) != 8:
                return False
            (header_len,) = struct.unpack("<Q", raw_len)
            if header_len <= 0 or header_len > 64 * 1024 * 1024:
                return False
            header = json.loads(fh.read(header_len).decode("utf-8"))
        return any(isinstance(k, str) and k.startswith("mtp.") for k in header)
    except (OSError, ValueError, struct.error):
        return False


def _has_mtp_weights(model_dir: Path) -> bool:
    """True when `model_dir` contains native Qwen3.5 `mtp.*` safetensor weights."""
    if not model_dir.is_dir():
        return False
    index_file = model_dir / "model.safetensors.index.json"
    if index_file.is_file():
        try:
            weight_map = json.loads(index_file.read_text(encoding="utf-8")).get("weight_map") or {}
            return any(isinstance(k, str) and k.startswith("mtp.") for k in weight_map)
        except (OSError, ValueError):
            return False
    return any(_safetensors_file_has_mtp(shard) for shard in sorted(model_dir.glob("*.safetensors")))


def _default_local_snapshot_lookup(repo_id: str) -> str | None:
    """Locate a cached Hugging Face snapshot even when `mlx-vlm` fetched only weight/config files."""
    from huggingface_hub import snapshot_download, try_to_load_from_cache

    cached_cfg = try_to_load_from_cache(repo_id, "config.json")
    if isinstance(cached_cfg, str) and cached_cfg:
        snap_dir = Path(cached_cfg).parent
        if snap_dir.is_dir():
            return str(snap_dir)
    return snapshot_download(
        repo_id,
        local_files_only=True,
        allow_patterns=["*.json", "*.safetensors"],
    )


def _resolve_mtp_source_dir(
    model_id: str,
    *,
    base_repo: str = BASE_XIAOMI_REPO,
    local_snapshot_lookup: Callable[[str], str | None] | None = None,
) -> str | None:
    """Return a local directory containing `mtp.*` weights, or `None` without network I/O."""
    lookup = local_snapshot_lookup or _default_local_snapshot_lookup
    candidate_path = Path(model_id)
    if candidate_path.is_dir():
        if _has_mtp_weights(candidate_path):
            return str(candidate_path)
    else:
        try:
            snap = lookup(model_id)
            if snap and _has_mtp_weights(Path(snap)):
                return str(snap)
        except Exception:  # noqa: BLE001
            pass

    if model_id != base_repo:
        try:
            base_snap = lookup(base_repo)
            if base_snap and _has_mtp_weights(Path(base_snap)):
                return str(base_snap)
        except Exception:  # noqa: BLE001
            pass
    return None


def _build_dispatcher(
    model_id: str,
    *,
    vision_cache_size: int = 4,
    max_batch_size: int = 4,
    low_memory: bool = False,
    wired_limit_bytes: int | None = None,
    active_recycle_bytes: int | None = None,
) -> _AdaptiveDispatcher:
    """Load `Xiaomi-OCR-0` and return an `_AdaptiveDispatcher` driving both engines."""
    import numpy as np
    from PIL import Image

    import mlx.core as mx
    import mlx.nn as nn
    from mlx_vlm import load
    from mlx_vlm.generate import stream_generate
    from mlx_vlm.generate.ar import BatchGenerator
    from mlx_vlm.models.activations import swiglu
    from mlx_vlm.models.base import ensure_fused_sdpa, scaled_dot_product_attention
    from mlx_vlm.models.cache import KVCache
    from mlx_vlm.models.qwen3_5.gated_delta import (
        gated_delta_kernel,
        gated_delta_update_with_states,
    )
    from mlx_vlm.models.qwen3_vl.processing_qwen3_vl import _smart_resize_image as smart_resize
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.sample_utils import make_logits_processors, make_sampler
    from mlx_vlm.speculative.drafters import load_drafter
    from mlx_vlm.speculative.drafters.qwen3_5_mtp.split import split_qwen3_5_mtp
    from mlx_vlm.utils import load_config

    model, processor = load(model_id)
    config = load_config(model_id)

    def _reassert_wired_limit() -> None:
        if wired_limit_bytes is not None:
            mx.set_wired_limit(wired_limit_bytes)

    def _release_gpu() -> None:
        gc.collect()
        mx.clear_cache()
        mx.reset_peak_memory()
        _reassert_wired_limit()

    def _handle_metal_fault(exc: BaseException) -> None:
        if isinstance(exc, RuntimeError) and "[METAL]" in str(exc):
            print(f"gpu_fault: {exc}; exiting so the server can be restarted", flush=True)
            threading.Timer(0.5, lambda: os._exit(75)).start()

    def _is_compatible_qwen3_5_08b(m) -> bool:
        try:
            lm = m.language_model
            vt = m.vision_tower
            if len(lm.model.layers) != 24:
                return False
            if vt.patch_embed.proj.weight.shape != (768, 2, 16, 16, 3):
                return False
            for lyr in lm.model.layers:
                if lyr.is_linear:
                    la = lyr.linear_attn
                    if la.in_proj_qkv.weight.shape != (6144, 1024) or la.conv1d.weight.shape[1] != 4:
                        return False
                else:
                    sa = lyr.self_attn
                    if sa.q_proj.weight.shape != (4096, 1024) or sa.k_proj.weight.shape != (512, 1024):
                        return False
            return True
        except AttributeError:
            return False

    if not _is_compatible_qwen3_5_08b(model):
        print(f"xiaomi_mlx_server: non-standard architecture for {model_id}; using generic stream_generate", flush=True)

        def fallback_solo(item: _WorkItem) -> None:
            try:
                prompt_text, images, kwargs = item.payload
                kw = dict(kwargs)
                enable_thinking = bool(kw.pop("enable_thinking", False))
                skip_special = bool(kw.pop("skip_special_tokens", True))
                pil_images = [img for img, _ in images]
                prompt = apply_chat_template(
                    processor, config, _normalize_text(prompt_text),
                    num_images=len(pil_images), enable_thinking=enable_thinking,
                )
                for res in stream_generate(model, processor, prompt, pil_images, skip_special_tokens=skip_special, **kw):
                    item.out_queue.put(GenerationChunk(
                        text=res.text,
                        prompt_tokens=res.prompt_tokens,
                        generation_tokens=res.generation_tokens,
                        finish_reason=getattr(res, "finish_reason", None),
                    ))
                    if item.stop_event.is_set():
                        break
            except BaseException as exc:  # noqa: BLE001
                _handle_metal_fault(exc)
                item.out_queue.put(_Failed(exc))
            finally:
                item.out_queue.put(_DONE)

        def fallback_batch(items: list[_WorkItem], inbox: queue.Queue) -> None:
            q = deque(items)
            while q:
                fallback_solo(q.popleft())
                while not inbox.empty():
                    nxt = inbox.get_nowait()
                    if nxt is _SHUTDOWN:
                        inbox.put(_SHUTDOWN)
                        break
                    q.append(nxt)

        return _AdaptiveDispatcher(
            solo_handler=fallback_solo,
            batch_handler=fallback_batch,
            after_idle=_release_gpu,
        )

    @mx.compile
    def _vision_apply_rope(q: mx.array, k: mx.array, cos: mx.array, sin: mx.array):
        orig = q.dtype
        qf = q.astype(mx.float32)
        kf = k.astype(mx.float32)
        q1, q2 = mx.split(qf, 2, axis=-1)
        k1, k2 = mx.split(kf, 2, axis=-1)
        q_rot = mx.concatenate([-q2, q1], axis=-1)
        k_rot = mx.concatenate([-k2, k1], axis=-1)
        return (qf * cos + q_rot * sin).astype(orig), (kf * cos + k_rot * sin).astype(orig)

    @mx.compile
    def _compiled_gdn_verify_prologue_q8(
        h: mx.array, norm_w: mx.array, q_w: mx.array, q_s: mx.array, q_b: mx.array,
        conv_state: mx.array, w0: mx.array, w1: mx.array, w2: mx.array, w3: mx.array,
    ):
        B, S, _ = h.shape
        normed = mx.fast.rms_norm(h, norm_w, 1e-6)
        proj = mx.quantized_matmul(normed, q_w, scales=q_s, biases=q_b, transpose=True, group_size=64, bits=8)
        mixed_qkv, z_flat, b, a = mx.split(proj, [6144, 8192, 8208], axis=-1)
        z = z_flat.reshape(B, S, 16, 128)
        conv_input = mx.concatenate([conv_state, mixed_qkv], axis=1)
        ci_f = conv_input.astype(mx.float32)
        conv_sum = (
            ci_f[:, 0:S, :] * w0
            + ci_f[:, 1 : S + 1, :] * w1
            + ci_f[:, 2 : S + 2, :] * w2
            + ci_f[:, 3 : S + 3, :] * w3
        )
        conv_out = nn.silu(conv_sum.astype(h.dtype))
        q_raw, k_raw, v_raw = mx.split(conv_out, [2048, 4096], axis=-1)
        inv_scale = 128 ** -0.5
        q = (inv_scale ** 2) * mx.fast.rms_norm(q_raw.reshape(B, S, 16, 128), None, 1e-6)
        k = inv_scale * mx.fast.rms_norm(k_raw.reshape(B, S, 16, 128), None, 1e-6)
        v = v_raw.reshape(B, S, 16, 128)
        return q, k, v, z, a, b, conv_input

    @mx.compile
    def _compiled_gdn_step_prologue_q8(
        h: mx.array, norm_w: mx.array, q_w: mx.array, q_s: mx.array, q_b: mx.array,
        conv_state: mx.array, w0: mx.array, w1: mx.array, w2: mx.array, w3: mx.array,
        neg_exp_A: mx.array, dt_bias_f32: mx.array,
    ):
        B, S, _ = h.shape
        normed = mx.fast.rms_norm(h, norm_w, 1e-6)
        proj = mx.quantized_matmul(normed, q_w, scales=q_s, biases=q_b, transpose=True, group_size=64, bits=8)
        mixed_qkv, z_flat, b, a = mx.split(proj, [6144, 8192, 8208], axis=-1)
        z = z_flat.reshape(B, S, 16, 128)
        conv_input = mx.concatenate([conv_state, mixed_qkv], axis=1)
        ci_f = conv_input.astype(mx.float32)
        conv_sum = (
            ci_f[:, 0:S, :] * w0
            + ci_f[:, 1 : S + 1, :] * w1
            + ci_f[:, 2 : S + 2, :] * w2
            + ci_f[:, 3 : S + 3, :] * w3
        )
        conv_out = nn.silu(conv_sum.astype(h.dtype))
        q_raw, k_raw, v_raw = mx.split(conv_out, [2048, 4096], axis=-1)
        inv_scale = 128 ** -0.5
        q = (inv_scale ** 2) * mx.fast.rms_norm(q_raw.reshape(B, S, 16, 128), None, 1e-6)
        k = inv_scale * mx.fast.rms_norm(k_raw.reshape(B, S, 16, 128), None, 1e-6)
        v = v_raw.reshape(B, S, 16, 128)
        g = mx.exp(neg_exp_A * nn.softplus(a.astype(mx.float32) + dt_bias_f32))
        beta = mx.sigmoid(b)
        return q, k, v, z, g, beta, conv_input

    @mx.compile
    def _compiled_attn_prologue_q8(
        h: mx.array, norm_w: mx.array,
        qkv_qw: mx.array, qkv_qs: mx.array, qkv_qb: mx.array,
        qn_w: mx.array, kn_w: mx.array,
    ):
        B, S, _ = h.shape
        normed = mx.fast.rms_norm(h, norm_w, 1e-6)
        qkv = mx.quantized_matmul(normed, qkv_qw, scales=qkv_qs, biases=qkv_qb, transpose=True, group_size=64, bits=8)
        q_proj_out, keys, values = mx.split(qkv, [4096, 4608], axis=-1)
        queries, gate = mx.split(q_proj_out.reshape(B, S, 8, 512), 2, axis=-1)
        gate = gate.reshape(B, S, 2048)
        queries = mx.fast.rms_norm(queries, qn_w, 1e-6).transpose(0, 2, 1, 3)
        keys = mx.fast.rms_norm(keys.reshape(B, S, 2, 256), kn_w, 1e-6).transpose(0, 2, 1, 3)
        values = values.reshape(B, S, 2, 256).transpose(0, 2, 1, 3)
        return queries, keys, values, gate

    @mx.compile
    def _compiled_gdn_epilogue_mlp_q8(
        x: mx.array, gdn_out: mx.array, z: mx.array,
        gdn_norm_w: mx.array,
        out_qw: mx.array, out_qs: mx.array, out_qb: mx.array,
        post_norm_w: mx.array,
        gu_qw: mx.array, gu_qs: mx.array, gu_qb: mx.array,
        dn_qw: mx.array, dn_qs: mx.array, dn_qb: mx.array,
    ):
        normed_out = mx.fast.rms_norm(gdn_out, gdn_norm_w, 1e-6)
        gated = (nn.silu(z.astype(mx.float32)) * normed_out.astype(mx.float32)).astype(x.dtype)
        h = x + mx.quantized_matmul(
            gated.reshape(x.shape[0], x.shape[1], 2048),
            out_qw, scales=out_qs, biases=out_qb, transpose=True, group_size=64, bits=8,
        )
        mlp_in = mx.fast.rms_norm(h, post_norm_w, 1e-6)
        gu = mx.quantized_matmul(mlp_in, gu_qw, scales=gu_qs, biases=gu_qb, transpose=True, group_size=64, bits=8)
        gate, up = mx.split(gu, 2, axis=-1)
        return h + mx.quantized_matmul(
            swiglu(gate, up), dn_qw, scales=dn_qs, biases=dn_qb, transpose=True, group_size=64, bits=8,
        )

    @mx.compile
    def _compiled_attn_epilogue_mlp_q8(
        x: mx.array, attn_out: mx.array, gate: mx.array,
        o_qw: mx.array, o_qs: mx.array, o_qb: mx.array,
        post_norm_w: mx.array,
        gu_qw: mx.array, gu_qs: mx.array, gu_qb: mx.array,
        dn_qw: mx.array, dn_qs: mx.array, dn_qb: mx.array,
    ):
        h = x + mx.quantized_matmul(
            attn_out * mx.sigmoid(gate),
            o_qw, scales=o_qs, biases=o_qb, transpose=True, group_size=64, bits=8,
        )
        mlp_in = mx.fast.rms_norm(h, post_norm_w, 1e-6)
        gu = mx.quantized_matmul(mlp_in, gu_qw, scales=gu_qs, biases=gu_qb, transpose=True, group_size=64, bits=8)
        g, u = mx.split(gu, 2, axis=-1)
        return h + mx.quantized_matmul(
            swiglu(g, u), dn_qw, scales=dn_qs, biases=dn_qb, transpose=True, group_size=64, bits=8,
        )

    @mx.compile
    def _compiled_target_head_argmax_q8(normed_h: mx.array, h_qw: mx.array, h_qs: mx.array, h_qb: mx.array):
        logits = mx.quantized_matmul(normed_h, h_qw, scales=h_qs, biases=h_qb, transpose=True, group_size=64, bits=8)
        return mx.argmax(logits, axis=-1).astype(mx.int32)

    @mx.compile
    def _compiled_mtp_prologue_q8(
        tok_emb: mx.array, target_hidden: mx.array,
        pfn_e_w: mx.array, pfn_h_w: mx.array,
        fc_qw: mx.array, fc_qs: mx.array, fc_qb: mx.array,
        in_norm_w: mx.array,
        qkv_qw: mx.array, qkv_qs: mx.array, qkv_qb: mx.array,
        qn_w: mx.array, kn_w: mx.array,
    ):
        B, S, _ = tok_emb.shape
        cat = mx.concatenate([
            mx.fast.rms_norm(tok_emb, pfn_e_w, 1e-6),
            mx.fast.rms_norm(target_hidden, pfn_h_w, 1e-6),
        ], axis=-1)
        h = mx.quantized_matmul(cat, fc_qw, scales=fc_qs, biases=fc_qb, transpose=True, group_size=64, bits=8)
        normed = mx.fast.rms_norm(h, in_norm_w, 1e-6)
        qkv = mx.quantized_matmul(normed, qkv_qw, scales=qkv_qs, biases=qkv_qb, transpose=True, group_size=64, bits=8)
        q_proj_out, keys, values = mx.split(qkv, [4096, 4608], axis=-1)
        queries, gate = mx.split(q_proj_out.reshape(B, S, 8, 512), 2, axis=-1)
        gate = gate.reshape(B, S, 2048)
        queries = mx.fast.rms_norm(queries, qn_w, 1e-6).transpose(0, 2, 1, 3)
        keys = mx.fast.rms_norm(keys.reshape(B, S, 2, 256), kn_w, 1e-6).transpose(0, 2, 1, 3)
        values = values.reshape(B, S, 2, 256).transpose(0, 2, 1, 3)
        return h, queries, keys, values, gate

    @mx.compile
    def _compiled_shortlist_q4_argmax(
        h_last: mx.array, qw: mx.array, qs: mx.array, qb: mx.array, shortlist_ids: mx.array,
    ):
        logits = mx.quantized_matmul(h_last, qw, scales=qs, biases=qb, transpose=True, group_size=64, bits=4)
        idx = mx.argmax(logits, axis=-1)
        return shortlist_ids[idx]

    class FastVisionWrapper:
        """Exact VisionTower with fp32-folded 2D PatchEmbed, 4096-token cap, and SHA-256 LRU."""

        def __init__(self, vt, image_processor, max_entries: int = 24) -> None:
            self.vt = vt
            self.min_pixels = int(image_processor.min_pixels)
            self.max_pixels = min(int(image_processor.max_pixels), XIAOMI_TRAINED_MAX_PIXELS)
            self.max_entries = max(0, int(max_entries))
            w5d = vt.patch_embed.proj.weight
            w_folded_f32 = (w5d[:, 0].astype(mx.float32) + w5d[:, 1].astype(mx.float32)).reshape(w5d.shape[0], -1)
            self.patch_w_T = mx.contiguous(w_folded_f32.astype(w5d.dtype).T)
            self.patch_b = vt.patch_embed.proj.bias
            self.dtype = w5d.dtype
            mx.eval(self.patch_w_T, self.patch_b)
            self._geom_cache: dict[tuple[int, int], tuple[mx.array, mx.array, mx.array]] = {}
            self._feat_cache: OrderedDict[tuple[str, int, int], tuple[mx.array, int, int]] = OrderedDict()

        def _get_geom(self, h_patches: int, w_patches: int):
            key = (h_patches, w_patches)
            cached = self._geom_cache.get(key)
            if cached is not None:
                return cached
            vt = self.vt
            merge = vt.spatial_merge_size
            num_side = vt.num_grid_per_side

            h_idxs = mx.linspace(0, num_side - 1, h_patches)
            w_idxs = mx.linspace(0, num_side - 1, w_patches)
            h_fl = h_idxs.astype(mx.int32)
            w_fl = w_idxs.astype(mx.int32)
            h_cl = mx.minimum(h_fl + 1, num_side - 1)
            w_cl = mx.minimum(w_fl + 1, num_side - 1)
            dh = h_idxs - h_fl.astype(mx.float32)
            dw = w_idxs - w_fl.astype(mx.float32)
            base_h = h_fl * num_side
            base_hc = h_cl * num_side

            idx_tensor = mx.stack([
                (base_h[:, None] + w_fl[None, :]).reshape(-1),
                (base_h[:, None] + w_cl[None, :]).reshape(-1),
                (base_hc[:, None] + w_fl[None, :]).reshape(-1),
                (base_hc[:, None] + w_cl[None, :]).reshape(-1),
            ], axis=0)
            weight_tensor = mx.stack([
                ((1 - dh)[:, None] * (1 - dw)[None, :]).reshape(-1),
                ((1 - dh)[:, None] * dw[None, :]).reshape(-1),
                (dh[:, None] * (1 - dw)[None, :]).reshape(-1),
                (dh[:, None] * dw[None, :]).reshape(-1),
            ], axis=0).astype(vt.pos_embed.weight.dtype)

            pos_embeds = vt.pos_embed(idx_tensor) * weight_tensor[:, :, None]
            pos_embeds = pos_embeds[0] + pos_embeds[1] + pos_embeds[2] + pos_embeds[3]
            feature_dim = pos_embeds.shape[-1]
            pos_embeds = (
                pos_embeds.reshape(h_patches // merge, merge, w_patches // merge, merge, feature_dim)
                .transpose(0, 2, 1, 3, 4)
                .reshape(-1, feature_dim)
            )

            grid_thw = mx.array([[1, h_patches, w_patches]], dtype=mx.int32)
            freqs = vt.rot_pos_emb(grid_thw)
            cos = mx.tile(mx.expand_dims(mx.cos(freqs), axis=1), (1, 1, 2))[None, ...]
            sin = mx.tile(mx.expand_dims(mx.sin(freqs), axis=1), (1, 1, 2))[None, ...]
            mx.eval(pos_embeds, cos, sin)
            self._geom_cache[key] = (pos_embeds, cos, sin)
            return pos_embeds, cos, sin

        def encode_pil(self, img: Image.Image, *, digest: str | None = None) -> tuple[mx.array, int, int]:
            cache_key = (digest, self.min_pixels, self.max_pixels) if (digest and self.max_entries > 0) else None
            if cache_key is not None and cache_key in self._feat_cache:
                self._feat_cache.move_to_end(cache_key)
                return self._feat_cache[cache_key]

            w, h = img.size
            rh, rw = smart_resize(h, w, factor=32, min_pixels=self.min_pixels, max_pixels=self.max_pixels)
            if (rw, rh) != (w, h):
                img = img.resize((rw, rh), Image.Resampling.BICUBIC)
            u8 = mx.array(np.asarray(img, dtype=np.uint8))
            hp, wp = rh // 16, rw // 16
            merge = self.vt.spatial_merge_size
            x = (u8.astype(mx.float32) * (2.0 / 255.0) - 1.0).astype(self.dtype)
            patches = (
                x.reshape(hp // merge, merge, 16, wp // merge, merge, 16, 3)
                .transpose(0, 3, 1, 4, 2, 5, 6)
                .reshape(hp * wp, 768)
            )
            pos_embeds, cos, sin = self._get_geom(hp, wp)
            hidden = (patches @ self.patch_w_T + self.patch_b) + pos_embeds

            seq_len = hidden.shape[0]
            for blk in self.vt.blocks:
                norm1 = blk.norm1(hidden)
                qkv = blk.attn.qkv(norm1).reshape(seq_len, 3, blk.attn.num_heads, blk.attn.head_dim).transpose(1, 0, 2, 3)
                q, k, v = mx.split(qkv, 3, axis=0)
                q, k = _vision_apply_rope(q, k, cos, sin)
                q = q.transpose(0, 2, 1, 3)
                k = k.transpose(0, 2, 1, 3)
                v = v.transpose(0, 2, 1, 3)
                attn_out = ensure_fused_sdpa(q, k, v, blk.attn.scale)
                attn_out = attn_out.transpose(0, 2, 1, 3).reshape(seq_len, -1)
                hidden = hidden + blk.attn.proj(attn_out)
                hidden = hidden + blk.mlp(blk.norm2(hidden))

            out = self.vt.merger(hidden)
            if cache_key is not None:
                mx.eval(out)
                self._feat_cache[cache_key] = (out, hp, wp)
                while len(self._feat_cache) > self.max_entries:
                    self._feat_cache.popitem(last=False)
            return out, hp, wp

    def _build_slc_shortlist_ids(tokenizer, max_vocab: int = 65536) -> mx.array:
        vocab = tokenizer.get_vocab()
        vocab_size = max(len(vocab), 248320)
        must_have: set[int] = set()
        for tok_str, tid in vocab.items():
            if tok_str.startswith("<") and tok_str.endswith(">"):
                must_have.add(tid)
        for tok_str, tid in sorted(vocab.items(), key=lambda kv: kv[1]):
            if tid in must_have:
                continue
            decoded = tokenizer.convert_tokens_to_string([tok_str])
            if all(ord(ch) < 0x2E80 for ch in decoded):
                must_have.add(tid)
                if len(must_have) >= max_vocab:
                    break
        if len(must_have) < max_vocab:
            for tid in range(vocab_size):
                if tid not in must_have:
                    must_have.add(tid)
                    if len(must_have) >= max_vocab:
                        break
        return mx.array(sorted(must_have), dtype=mx.int32)

    def _prepare_dual_engine_weights(lm, *, build_fused_solo: bool) -> None:
        to_eval = []
        if build_fused_solo:
            for layer in lm.model.layers:
                mlp = layer.mlp
                w_gu = mx.contiguous(mx.concatenate([mlp.gate_proj.weight, mlp.up_proj.weight], axis=0))
                mlp._gu_q = mx.quantize(w_gu, group_size=64, bits=8)
                mlp._dn_q = mx.quantize(mlp.down_proj.weight, group_size=64, bits=8)
                to_eval.extend([*mlp._gu_q, *mlp._dn_q])

                if layer.is_linear:
                    la = layer.linear_attn
                    w_all = mx.contiguous(mx.concatenate([
                        la.in_proj_qkv.weight, la.in_proj_z.weight, la.in_proj_b.weight, la.in_proj_a.weight,
                    ], axis=0))
                    cw = la.conv1d.weight[:, :, 0].T.astype(mx.float32)
                    la._w0 = mx.contiguous(cw[0:1, :][None, ...])
                    la._w1 = mx.contiguous(cw[1:2, :][None, ...])
                    la._w2 = mx.contiguous(cw[2:3, :][None, ...])
                    la._w3 = mx.contiguous(cw[3:4, :][None, ...])
                    la._neg_exp_A = -mx.exp(la.A_log.astype(mx.float32))
                    la._dt_bias_f32 = la.dt_bias.astype(mx.float32)
                    la._in_q = mx.quantize(w_all, group_size=64, bits=8)
                    la._out_q = mx.quantize(la.out_proj.weight, group_size=64, bits=8)
                    to_eval.extend([
                        la._w0, la._w1, la._w2, la._w3,
                        la._neg_exp_A, la._dt_bias_f32,
                        *la._in_q, *la._out_q,
                    ])
                else:
                    sa = layer.self_attn
                    w_qkv = mx.contiguous(mx.concatenate([sa.q_proj.weight, sa.k_proj.weight, sa.v_proj.weight], axis=0))
                    sa._qkv_q = mx.quantize(w_qkv, group_size=64, bits=8)
                    sa._o_q = mx.quantize(sa.o_proj.weight, group_size=64, bits=8)
                    to_eval.extend([*sa._qkv_q, *sa._o_q])

        # Untie Q8 lm_head while keeping `embed_tokens` in exact bf16 for input lookups,
        # and disable the scalar GEMV `fused_greedy_decode` so `BatchGenerator` uses
        # Apple's SIMD-tiled `qmm` across concurrent batch items.
        head = nn.Linear(1024, int(lm.model.embed_tokens.weight.shape[0]), bias=False)
        head.weight = lm.model.embed_tokens.weight
        lm.lm_head = head
        lm.args.tie_word_embeddings = False
        lm.fused_greedy_decode = lambda *a, **kw: None
        nn.quantize(
            lm,
            group_size=64,
            bits=8,
            class_predicate=lambda _p, mod: isinstance(mod, nn.Linear),
        )
        lm._head_q = (lm.lm_head.weight, lm.lm_head.scales, lm.lm_head.biases)
        mx.eval(lm.parameters(), *to_eval)

    class SurgicalQwenRunner:
        def __init__(self, lm) -> None:
            self.lm = lm
            self.model = lm.model
            self.head_q = lm._head_q

        def make_cache(self) -> list:
            return [[None, None] if lyr.is_linear else KVCache() for lyr in self.model.layers]

        def target_argmax(self, normed_h: mx.array) -> mx.array:
            return _compiled_target_head_argmax_q8(normed_h, *self.head_q)

        def target_logits(self, normed_h: mx.array) -> mx.array:
            return mx.quantized_matmul(
                normed_h, self.head_q[0], scales=self.head_q[1], biases=self.head_q[2],
                transpose=True, group_size=64, bits=8,
            )

        def forward(
            self,
            inputs: mx.array,
            cache: list,
            *,
            inputs_embeds: mx.array | None = None,
            position_ids: mx.array | None = None,
            rope_delta: int = 0,
            record_gdn_states: bool = False,
        ):
            h = self.model.embed_tokens(inputs) if inputs_embeds is None else inputs_embeds
            B, S, _ = h.shape
            fa_cache = cache[self.model.fa_idx]
            cache_offset = fa_cache.offset
            if position_ids is None:
                pos = mx.arange(cache_offset + rope_delta, cache_offset + rope_delta + S, dtype=mx.int32)
                position_ids = mx.broadcast_to(pos[None, None, :], (3, B, S))

            fa_mask = "causal" if S > 1 else None
            gdn_snapshots = [] if record_gdn_states else None

            for layer, c in zip(self.model.layers, cache):
                mlp = layer.mlp
                if layer.is_linear:
                    la = layer.linear_attn
                    conv_state, ssm_state = c[0], c[1]
                    if conv_state is None:
                        conv_state = mx.zeros((B, 3, 6144), dtype=h.dtype)
                    if ssm_state is None:
                        ssm_state = mx.zeros((B, 16, 128, 128), dtype=mx.float32)

                    if record_gdn_states:
                        q, k, v, z, a, b, conv_input = _compiled_gdn_verify_prologue_q8(
                            h, layer.input_layernorm.weight, *la._in_q,
                            conv_state, la._w0, la._w1, la._w2, la._w3,
                        )
                        gdn_out, final_ssm, step_ssms = gated_delta_update_with_states(
                            q, k, v, a, b, la.A_log, la.dt_bias,
                            state=ssm_state, mask=None, use_kernel=True, state_steps=max(1, S - 1),
                        )
                        gdn_snapshots.append((conv_input, step_ssms))
                        c[0], c[1] = conv_input[:, -3:, :], final_ssm
                    elif S <= 4:
                        q, k, v, z, g, beta, conv_input = _compiled_gdn_step_prologue_q8(
                            h, layer.input_layernorm.weight, *la._in_q,
                            conv_state, la._w0, la._w1, la._w2, la._w3,
                            la._neg_exp_A, la._dt_bias_f32,
                        )
                        gdn_out, new_ssm = gated_delta_kernel(q, k, v, g, beta, ssm_state, None)
                        c[0], c[1] = conv_input[:, -3:, :], new_ssm
                    else:
                        normed = mx.fast.rms_norm(h, layer.input_layernorm.weight, 1e-6)
                        proj = mx.quantized_matmul(
                            normed, la._in_q[0], scales=la._in_q[1], biases=la._in_q[2],
                            transpose=True, group_size=64, bits=8,
                        )
                        mixed_qkv, z_flat, b, a = mx.split(proj, [6144, 8192, 8208], axis=-1)
                        z = z_flat.reshape(B, S, 16, 128)
                        conv_input = mx.concatenate([conv_state, mixed_qkv], axis=1)
                        conv_out = nn.silu(la.conv1d(conv_input))
                        q_raw, k_raw, v_raw = mx.split(conv_out, [2048, 4096], axis=-1)
                        inv_scale = 128 ** -0.5
                        q = (inv_scale ** 2) * mx.fast.rms_norm(q_raw.reshape(B, S, 16, 128), None, 1e-6)
                        k = inv_scale * mx.fast.rms_norm(k_raw.reshape(B, S, 16, 128), None, 1e-6)
                        v = v_raw.reshape(B, S, 16, 128)
                        g = mx.exp(la._neg_exp_A * nn.softplus(a.astype(mx.float32) + la._dt_bias_f32))
                        beta = mx.sigmoid(b)
                        gdn_out, new_ssm = gated_delta_kernel(q, k, v, g, beta, ssm_state, None)
                        c[0], c[1] = conv_input[:, -3:, :], new_ssm

                    h = _compiled_gdn_epilogue_mlp_q8(
                        h, gdn_out, z, la.norm.weight, *la._out_q,
                        layer.post_attention_layernorm.weight, *mlp._gu_q, *mlp._dn_q,
                    )
                else:
                    sa = layer.self_attn
                    queries, keys, values, gate = _compiled_attn_prologue_q8(
                        h, layer.input_layernorm.weight, *sa._qkv_q, sa.q_norm.weight, sa.k_norm.weight,
                    )
                    queries, keys = sa.rotary_emb.apply_rotary(queries, keys, position_ids, unsqueeze_dim=1)
                    keys, values = c.update_and_fetch(keys, values)
                    attn_out = scaled_dot_product_attention(queries, keys, values, cache=c, scale=sa.scale, mask=fa_mask)
                    attn_out = attn_out.transpose(0, 2, 1, 3).reshape(B, S, 2048)
                    h = _compiled_attn_epilogue_mlp_q8(
                        h, attn_out, gate, *sa._o_q,
                        layer.post_attention_layernorm.weight, *mlp._gu_q, *mlp._dn_q,
                    )

            return self.model.norm(h), gdn_snapshots

        def rollback_to(self, cache: list, gdn_snapshots: list, accepted_count: int, total_verified: int) -> None:
            keep = accepted_count + 1
            trim = total_verified - keep
            if trim <= 0:
                return
            snap_idx = 0
            for layer, c in zip(self.model.layers, cache):
                if layer.is_linear:
                    conv_input, step_ssms = gdn_snapshots[snap_idx]
                    snap_idx += 1
                    c[0] = conv_input[:, keep : keep + 3, :]
                    c[1] = step_ssms[:, keep - 1, ...]
                else:
                    c.trim(trim)

    class SLCResidentMTPDrafter:
        """54 MB total footprint (22 MB Q8 MTP layer + 32 MB Q4 64k shortlist head)."""

        def __init__(self, drafter, embed_tokens: nn.Embedding, shortlist_ids: mx.array) -> None:
            self.drafter = drafter
            self.embed_tokens = embed_tokens
            self.shortlist_ids = shortlist_ids
            short_w = mx.contiguous(embed_tokens.weight[shortlist_ids])
            self.short_q4 = mx.quantize(short_w, group_size=64, bits=4)

            self.fc_q8 = mx.quantize(drafter.fc.weight, group_size=64, bits=8)
            layer = drafter.layers[0]
            sa = layer.self_attn
            mlp = layer.mlp
            w_qkv = mx.contiguous(mx.concatenate([sa.q_proj.weight, sa.k_proj.weight, sa.v_proj.weight], axis=0))
            w_gu = mx.contiguous(mx.concatenate([mlp.gate_proj.weight, mlp.up_proj.weight], axis=0))
            self.qkv_q8 = mx.quantize(w_qkv, group_size=64, bits=8)
            self.o_q8 = mx.quantize(sa.o_proj.weight, group_size=64, bits=8)
            self.gu_q8 = mx.quantize(w_gu, group_size=64, bits=8)
            self.dn_q8 = mx.quantize(mlp.down_proj.weight, group_size=64, bits=8)
            mx.eval(*self.short_q4, *self.fc_q8, *self.qkv_q8, *self.o_q8, *self.gu_q8, *self.dn_q8)

            self.kv_cache = KVCache()
            self.next_pos = 0
            self.seed_tok_arr: mx.array | None = None
            self.seed_hidden: mx.array | None = None

        def _forward_layers(self, tok_ids: mx.array, target_hidden: mx.array) -> mx.array:
            B, S = tok_ids.shape
            tok_emb = self.embed_tokens(tok_ids)
            layer = self.drafter.layers[0]
            sa = layer.self_attn
            h, queries, keys, values, gate = _compiled_mtp_prologue_q8(
                tok_emb, target_hidden,
                self.drafter.pre_fc_norm_embedding.weight,
                self.drafter.pre_fc_norm_hidden.weight,
                *self.fc_q8,
                layer.input_layernorm.weight,
                *self.qkv_q8,
                sa.q_norm.weight, sa.k_norm.weight,
            )
            pos = mx.arange(self.next_pos, self.next_pos + S, dtype=mx.int32)
            pos_ids = mx.broadcast_to(pos[None, None, :], (3, B, S))
            queries, keys = sa.rotary_emb.apply_rotary(queries, keys, pos_ids, unsqueeze_dim=1)
            keys, values = self.kv_cache.update_and_fetch(keys, values)
            mask = "causal" if S > 1 else None
            attn_out = scaled_dot_product_attention(queries, keys, values, cache=self.kv_cache, scale=sa.scale, mask=mask)
            attn_out = attn_out.transpose(0, 2, 1, 3).reshape(B, S, 2048)
            h = _compiled_attn_epilogue_mlp_q8(
                h, attn_out, gate, *self.o_q8,
                layer.post_attention_layernorm.weight, *self.gu_q8, *self.dn_q8,
            )
            self.next_pos += S
            return mx.fast.rms_norm(h, self.drafter.norm.weight, 1e-6)

        def _draft_argmax(self, h_last: mx.array) -> mx.array:
            return _compiled_shortlist_q4_argmax(h_last, *self.short_q4, self.shortlist_ids)

        def prefill(self, input_ids: mx.array, target_hidden: mx.array, bonus_tok_arr: mx.array) -> None:
            self.kv_cache = KVCache()
            self.next_pos = 0
            shifted = mx.concatenate([input_ids[:, 1:].astype(mx.int32), bonus_tok_arr.astype(mx.int32)], axis=1)
            h = self._forward_layers(shifted, target_hidden[:, : shifted.shape[1], :])
            h_last = h[:, -1:, :]
            self.seed_tok_arr = self._draft_argmax(h_last)
            self.seed_hidden = h_last

        def draft_k_lazy(self, k_draft: int) -> mx.array:
            tok = self.seed_tok_arr
            h_prev = self.seed_hidden
            toks = [tok]
            for _ in range(k_draft - 1):
                h_prev = self._forward_layers(tok, h_prev)
                tok = self._draft_argmax(h_prev)
                toks.append(tok)
            return mx.concatenate(toks, axis=1)

        def sync_after_verify_lazy(
            self,
            verify_hidden: mx.array,
            draft_tokens: list[int],
            accepted: int,
            new_bonus: int,
            appended: int,
        ) -> None:
            keep_appended = min(accepted, appended)
            trim = appended - keep_appended
            if trim > 0:
                self.kv_cache.trim(trim)
                self.next_pos -= trim

            toks_to_feed = []
            hids_to_feed = []
            for idx in range(keep_appended, accepted):
                toks_to_feed.append(draft_tokens[idx])
                hids_to_feed.append(verify_hidden[:, idx : idx + 1, :])
            toks_to_feed.append(new_bonus)
            hids_to_feed.append(verify_hidden[:, accepted : accepted + 1, :])

            tok_arr = mx.array([toks_to_feed], dtype=mx.int32)
            hid_arr = mx.concatenate(hids_to_feed, axis=1)
            h = self._forward_layers(tok_arr, hid_arr)
            h_last = h[:, -1:, :]
            self.seed_tok_arr = self._draft_argmax(h_last)
            self.seed_hidden = h_last

    build_solo = not low_memory
    fast_vis = FastVisionWrapper(model.vision_tower, processor.image_processor, max_entries=vision_cache_size)
    _prepare_dual_engine_weights(model.language_model, build_fused_solo=build_solo)
    runner = SurgicalQwenRunner(model.language_model) if build_solo else None

    mtp: SLCResidentMTPDrafter | None = None
    if build_solo:
        mtp_src = _resolve_mtp_source_dir(model_id)
        if mtp_src is not None:
            try:
                with tempfile.TemporaryDirectory(prefix="xiaomi-mtp-") as tmp:
                    mtp_dir = str(Path(tmp) / "mtp")
                    split_qwen3_5_mtp(mtp_src, mtp_dir)
                    drafter_raw, _ = load_drafter(mtp_dir)
                    shortlist_ids = _build_slc_shortlist_ids(processor.tokenizer, max_vocab=65536)
                    mtp = SLCResidentMTPDrafter(drafter_raw, model.language_model.model.embed_tokens, shortlist_ids)
                    del drafter_raw
                print(f"xiaomi_mlx_server: dual-engine ready (SLC MTP from {mtp_src} + Ragged BatchGenerator B={max_batch_size})", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"xiaomi_mlx_server: MTP drafter skipped ({exc}); using BatchGenerator + S=1", flush=True)
                mtp = None
        else:
            print(f"xiaomi_mlx_server: no local MTP weights; using Ragged BatchGenerator B={max_batch_size}", flush=True)

    eos_ids: set[int] = set()
    for e in (model.config.eos_token_id, model.config.text_config.eos_token_id):
        if isinstance(e, (list, tuple, set)):
            eos_ids.update(int(x) for x in e)
        elif e is not None:
            eos_ids.add(int(e))

    tokenizer = processor.tokenizer
    img_tok_id = int(model.config.image_token_id)

    def _prepare_multimodal_inputs(prompt_text: str, images: list[tuple[Any, str]], enable_thinking: bool):
        vis_feats_list = []
        thw_list = []
        for img, digest in images:
            vf, hp, wp = fast_vis.encode_pil(img, digest=digest)
            vis_feats_list.append(vf)
            thw_list.append([1, hp, wp])

        chat_str = apply_chat_template(
            processor, config, _normalize_text(prompt_text),
            num_images=len(images), enable_thinking=enable_thinking,
        )
        raw_ids = tokenizer.encode(chat_str, add_special_tokens=False)

        expanded_ids: list[int] = []
        splice_spans: list[tuple[int, int, mx.array]] = []
        img_idx = 0
        for tok in raw_ids:
            if tok == img_tok_id and img_idx < len(vis_feats_list):
                vf = vis_feats_list[img_idx]
                n_img = int(vf.shape[0])
                start = len(expanded_ids)
                expanded_ids.extend([img_tok_id] * n_img)
                splice_spans.append((start, start + n_img, vf))
                img_idx += 1
            else:
                expanded_ids.append(tok)

        input_ids = mx.array([expanded_ids], dtype=mx.int32)
        base_embeds = model.language_model.model.embed_tokens(input_ids)
        segments: list[mx.array] = []
        cursor = 0
        for start, end, vf in splice_spans:
            if start > cursor:
                segments.append(base_embeds[:, cursor:start, :])
            segments.append(vf[None, :, :])
            cursor = end
        if cursor < len(expanded_ids):
            segments.append(base_embeds[:, cursor:, :])
        inputs_embeds = mx.concatenate(segments, axis=1) if len(segments) > 1 else segments[0]

        grid_thw = mx.array(thw_list, dtype=mx.int32)
        pos_ids, rope_deltas = model.language_model.get_rope_index(input_ids, image_grid_thw=grid_thw)
        mx.eval(inputs_embeds, pos_ids, rope_deltas)
        return expanded_ids, input_ids, inputs_embeds, pos_ids, rope_deltas

    def _make_req_logits_processors(kwargs: dict[str, Any]):
        if _is_greedy_neutral(kwargs):
            return []
        pres_pen = kwargs.get("presence_penalty")
        pres_ctx = int(kwargs.get("presence_context_size") or (256 if pres_pen else 20))
        rep_ctx = int(kwargs.get("repetition_context_size") or 128)
        freq_ctx = int(kwargs.get("frequency_context_size") or 128)
        return make_logits_processors(
            logit_bias=kwargs.get("logit_bias"),
            repetition_penalty=kwargs.get("repetition_penalty"),
            repetition_context_size=rep_ctx,
            presence_penalty=pres_pen,
            presence_context_size=pres_ctx,
            frequency_penalty=kwargs.get("frequency_penalty"),
            frequency_context_size=freq_ctx,
        )

    def _run_solo_stream(
        prompt_text: str,
        images: list[tuple[Any, str]],
        kwargs: dict[str, Any],
        stop_event: threading.Event,
    ):
        assert runner is not None
        max_tokens = max(1, int(kwargs.get("max_tokens") or 8192))
        enable_thinking = bool(kwargs.get("enable_thinking", False))
        skip_special = bool(kwargs.get("skip_special_tokens", True))
        use_turbo = _is_greedy_neutral(kwargs) and mtp is not None

        _expanded, input_ids, inputs_embeds, pos_ids, rope_deltas = _prepare_multimodal_inputs(
            prompt_text, images, enable_thinking,
        )
        for im, _ in images:
            try:
                im.close()
            except Exception:  # noqa: BLE001
                pass
        del images
        rope_delta = int(rope_deltas.item())
        prompt_tokens = int(input_ids.shape[1])
        cache = runner.make_cache()
        normed_pf, _ = runner.forward(input_ids, cache, inputs_embeds=inputs_embeds, position_ids=pos_ids)

        if use_turbo:
            k_draft = 2
            first_tok_arr = runner.target_argmax(normed_pf[:, -1:, :])
            mtp.prefill(input_ids, normed_pf, first_tok_arr)
            mx.eval(first_tok_arr, mtp.seed_tok_arr, mtp.seed_hidden)
            bonus_tok = int(first_tok_arr.item())

            if bonus_tok in eos_ids:
                yield GenerationChunk(text="", prompt_tokens=prompt_tokens, generation_tokens=1, finish_reason="stop")
                return
            if max_tokens <= 1:
                text = tokenizer.decode([bonus_tok], skip_special_tokens=skip_special)
                yield GenerationChunk(text=text, prompt_tokens=prompt_tokens, generation_tokens=1, finish_reason="length")
                return

            generated: list[int] = [bonus_tok]
            emitted_chars = 0
            bonus_arr = first_tok_arr
            rounds = 0
            hit_eos_token = False

            while len(generated) < max_tokens and not stop_event.is_set():
                rounds += 1
                draft_arr = mtp.draft_k_lazy(k_draft)
                appended = k_draft - 1

                verify_in = mx.concatenate([bonus_arr, draft_arr], axis=1)
                v_hidden, gdn_snaps = runner.forward(
                    verify_in, cache, rope_delta=rope_delta, record_gdn_states=True,
                )
                target_toks_arr = runner.target_argmax(v_hidden)
                mx.eval(draft_arr, target_toks_arr, v_hidden)

                draft_toks = draft_arr[0].tolist()
                target_toks = target_toks_arr[0].tolist()

                accepted = 0
                done = False
                new_emitted: list[int] = []
                for d_tok, t_tok in zip(draft_toks, target_toks[:-1]):
                    if d_tok == t_tok:
                        accepted += 1
                        if d_tok in eos_ids:
                            hit_eos_token = True
                            done = True
                            break
                        new_emitted.append(d_tok)
                        if len(generated) + len(new_emitted) >= max_tokens:
                            done = True
                            break
                    else:
                        break

                if not done:
                    next_bonus = target_toks[accepted]
                    if next_bonus in eos_ids:
                        hit_eos_token = True
                        done = True
                    else:
                        new_emitted.append(next_bonus)
                        if len(generated) + len(new_emitted) >= max_tokens:
                            done = True

                generated.extend(new_emitted)
                if not done and rounds % 32 == 0 and _has_token_loop(generated):
                    hit_eos_token = False
                    done = True
                if done:
                    break

                bonus_tok = new_emitted[-1]
                bonus_arr = mx.array([[bonus_tok]], dtype=mx.int32)
                runner.rollback_to(cache, gdn_snaps, accepted, k_draft + 1)
                mtp.sync_after_verify_lazy(v_hidden, draft_toks, accepted, bonus_tok, appended)

                if kwargs.get("stream") and rounds % 12 == 0:
                    full_text = tokenizer.decode(generated, skip_special_tokens=skip_special)
                    if not full_text.endswith("\ufffd") and len(full_text) > emitted_chars:
                        delta = full_text[emitted_chars:]
                        emitted_chars = len(full_text)
                        yield GenerationChunk(
                            text=delta,
                            prompt_tokens=prompt_tokens,
                            generation_tokens=len(generated),
                        )

            full_text = tokenizer.decode(generated, skip_special_tokens=skip_special)
            delta = full_text[emitted_chars:]
            finish_reason = "stop" if (hit_eos_token or len(generated) < max_tokens) else "length"
            gen_count = len(generated) + (1 if hit_eos_token else 0)
            yield GenerationChunk(
                text=delta,
                prompt_tokens=prompt_tokens,
                generation_tokens=gen_count,
                finish_reason=finish_reason,
            )
            return

        # S=1 path (temperature > 0 or token penalties on a solo request).
        greedy_s1 = _is_greedy_neutral(kwargs)
        logits_processors = _make_req_logits_processors(kwargs)
        sampler = None if greedy_s1 else make_sampler(
            temp=float(kwargs.get("temperature") or 0.0),
            top_p=float(kwargs.get("top_p") if kwargs.get("top_p") is not None else 1.0),
            top_k=int(kwargs.get("top_k") or 0),
        )
        history_arr = input_ids[0]

        def _sample_from_hidden(h_last: mx.array) -> mx.array:
            if greedy_s1:
                return runner.target_argmax(h_last)[:, 0]
            logits = runner.target_logits(h_last)[:, 0, :]
            for proc_fn in logits_processors:
                logits = proc_fn(history_arr, logits)
            return sampler(logits).astype(mx.int32)

        tok_arr = _sample_from_hidden(normed_pf[:, -1:, :])
        mx.eval(tok_arr)
        cur_tok = int(tok_arr.item())
        if cur_tok in eos_ids:
            yield GenerationChunk(text="", prompt_tokens=prompt_tokens, generation_tokens=1, finish_reason="stop")
            return

        generated = [cur_tok]
        if not greedy_s1:
            history_arr = mx.concatenate([history_arr, tok_arr], axis=0)
        emitted_chars = 0
        hit_eos_token = False

        while len(generated) < max_tokens and not stop_event.is_set():
            step_in = mx.array([[cur_tok]], dtype=mx.int32)
            normed_h, _ = runner.forward(step_in, cache, rope_delta=rope_delta, record_gdn_states=False)
            tok_arr = _sample_from_hidden(normed_h)
            mx.eval(tok_arr)
            cur_tok = int(tok_arr.item())
            if cur_tok in eos_ids:
                hit_eos_token = True
                break
            generated.append(cur_tok)
            if len(generated) % 64 == 0 and _has_token_loop(generated):
                hit_eos_token = False
                break
            if not greedy_s1:
                history_arr = mx.concatenate([history_arr, tok_arr], axis=0)
            if kwargs.get("stream") and len(generated) % 32 == 0:
                full_text = tokenizer.decode(generated, skip_special_tokens=skip_special)
                if not full_text.endswith("\ufffd") and len(full_text) > emitted_chars:
                    delta = full_text[emitted_chars:]
                    emitted_chars = len(full_text)
                    yield GenerationChunk(
                        text=delta,
                        prompt_tokens=prompt_tokens,
                        generation_tokens=len(generated),
                    )

        full_text = tokenizer.decode(generated, skip_special_tokens=skip_special)
        delta = full_text[emitted_chars:]
        finish_reason = "stop" if (hit_eos_token or len(generated) < max_tokens) else "length"
        gen_count = len(generated) + (1 if hit_eos_token else 0)
        yield GenerationChunk(
            text=delta,
            prompt_tokens=prompt_tokens,
            generation_tokens=gen_count,
            finish_reason=finish_reason,
        )
        mx.clear_cache()

    def can_solo(payload: tuple[str, list, dict]) -> bool:
        if runner is None:
            return False
        _prompt, _imgs, kw = payload
        temp = float(kw.get("temperature") or 0.0)
        if kw.get("batch_hint") and temp == 0.0:
            return False
        # Either greedy-neutral with MTP, or temperature > 0 (which needs its own sampler).
        return mtp is not None or temp > 0.0

    def solo_handler(item: _WorkItem) -> None:
        try:
            prompt_text, images, kwargs = item.payload
            for chunk in _run_solo_stream(prompt_text, images, kwargs, item.stop_event):
                item.out_queue.put(chunk)
        except BaseException as exc:  # noqa: BLE001
            _handle_metal_fault(exc)
            item.out_queue.put(_Failed(exc))
        finally:
            item.out_queue.put(_DONE)

    @dataclass
    class _BatchSlotState:
        item: _WorkItem
        prompt_tokens: int
        skip_special: bool
        generated: list[int]
        want_stream: bool = False
        emitted_chars: int = 0

    def batch_handler(initial_items: list[_WorkItem], inbox: queue.Queue) -> None:
        """Continuous ragged batching via `BatchGenerator` with periodic KV/cache recycling."""
        pending: deque[_WorkItem] = deque(initial_items)
        deferred_sampling: list[_WorkItem] = []

        def _drain_inbox() -> None:
            while True:
                try:
                    nxt = inbox.get_nowait()
                except queue.Empty:
                    break
                if nxt is _SHUTDOWN:
                    inbox.put(_SHUTDOWN)
                    break
                if nxt.stop_event.is_set():
                    nxt.out_queue.put(_DONE)
                else:
                    pending.append(nxt)

        def _run_wave() -> None:
            bgen = BatchGenerator(
                model.language_model,
                processor,
                max_tokens=16384,
                completion_batch_size=max_batch_size,
                prefill_batch_size=1,
                kv_bits=8,
                compute_logprobs=False,
                greedy_sampling=True,
            )
            # Undo BatchGenerator's `wired_limit(...)` (which locks 80% of all RAM as
            # non-swappable Metal wired memory) and re-apply our bounded wired limit.
            bgen.close()
            _reassert_wired_limit()
            uid_map: dict[int, _BatchSlotState] = {}
            completed_in_wave = 0
            draining = False

            def _admit_pending() -> None:
                nonlocal draining
                while pending and not draining and len(uid_map) < max_batch_size:
                    if uid_map and (
                        completed_in_wave >= _BATCH_RECYCLE_COMPLETIONS
                        or (active_recycle_bytes is not None and mx.get_active_memory() >= active_recycle_bytes)
                    ):
                        draining = True
                        break
                    cand = pending.popleft()
                    if cand.stop_event.is_set():
                        cand.out_queue.put(_DONE)
                        continue
                    payload = cand.payload
                    if payload is None:
                        cand.out_queue.put(_DONE)
                        continue
                    prompt_text, images, kwargs = payload
                    if float(kwargs.get("temperature") or 0.0) > 0.0 and runner is not None:
                        deferred_sampling.append(cand)
                        continue
                    try:
                        max_tok = max(1, int(kwargs.get("max_tokens") or 8192))
                        enable_thinking = bool(kwargs.get("enable_thinking", False))
                        skip_special = bool(kwargs.get("skip_special_tokens", True))
                        expanded_ids, _inp, inputs_embeds, pos_ids, rope_deltas = _prepare_multimodal_inputs(
                            prompt_text, images, enable_thinking,
                        )
                        cand.payload = None
                        for im, _ in images:
                            try:
                                im.close()
                            except Exception:  # noqa: BLE001
                                pass
                        del images
                        req_lps = _make_req_logits_processors(kwargs)
                        uids = bgen.insert(
                            [expanded_ids],
                            max_tokens=[max_tok],
                            prompt_kwargs=[{
                                "inputs_embeds": inputs_embeds,
                                "position_ids": pos_ids,
                                "rope_deltas": rope_deltas,
                            }],
                            logits_processors=[req_lps],
                        )
                        uid_map[uids[0]] = _BatchSlotState(
                            item=cand,
                            prompt_tokens=len(expanded_ids),
                            skip_special=skip_special,
                            generated=[],
                            want_stream=bool(kwargs.get("stream", False)),
                        )
                    except BaseException as exc:  # noqa: BLE001
                        _handle_metal_fault(exc)
                        cand.out_queue.put(_Failed(exc))
                        cand.out_queue.put(_DONE)

            try:
                _drain_inbox()
                _admit_pending()
                while bgen.has_work or (pending and not draining):
                    _drain_inbox()
                    if not draining:
                        _admit_pending()

                    for uid in list(uid_map):
                        st = uid_map[uid]
                        if st.item.stop_event.is_set():
                            bgen.remove(uid)
                            st.item.out_queue.put(_DONE)
                            del uid_map[uid]
                            completed_in_wave += 1
                            mx.clear_cache()

                    if not bgen.has_work:
                        continue

                    _prompt_resps, gen_resps = bgen.next()
                    for r in gen_resps:
                        st = uid_map.get(r.uid)
                        if st is None:
                            continue
                        tok_id = int(r.token)
                        if r.finish_reason != "stop" and tok_id not in eos_ids:
                            st.generated.append(tok_id)
                        loop_broken = (
                            r.finish_reason is None
                            and len(st.generated) >= 4096
                            and len(st.generated) % 128 == 0
                            and _has_token_loop(st.generated)
                        )
                        if loop_broken:
                            bgen.remove(r.uid)
                        finish_reason = "length" if loop_broken else r.finish_reason
                        if finish_reason is not None:
                            full_text = tokenizer.decode(st.generated, skip_special_tokens=st.skip_special)
                            delta = full_text[st.emitted_chars:]
                            gen_count = len(st.generated) + (1 if finish_reason == "stop" else 0)
                            st.item.out_queue.put(GenerationChunk(
                                text=delta,
                                prompt_tokens=st.prompt_tokens,
                                generation_tokens=gen_count,
                                finish_reason=finish_reason,
                            ))
                            st.item.out_queue.put(_DONE)
                            del uid_map[r.uid]
                            completed_in_wave += 1
                            mx.clear_cache()
                        elif st.want_stream and len(st.generated) > 0 and len(st.generated) % 48 == 0:
                            full_text = tokenizer.decode(st.generated, skip_special_tokens=st.skip_special)
                            if not full_text.endswith("\ufffd") and len(full_text) > st.emitted_chars:
                                delta = full_text[st.emitted_chars:]
                                st.emitted_chars = len(full_text)
                                st.item.out_queue.put(GenerationChunk(
                                    text=delta,
                                    prompt_tokens=st.prompt_tokens,
                                    generation_tokens=len(st.generated),
                                ))
            except BaseException as exc:  # noqa: BLE001
                _handle_metal_fault(exc)
                for st in list(uid_map.values()):
                    st.item.out_queue.put(_Failed(exc))
                    st.item.out_queue.put(_DONE)
                uid_map.clear()
                while pending:
                    it = pending.popleft()
                    it.out_queue.put(_Failed(exc))
                    it.out_queue.put(_DONE)
            finally:
                bgen.close()

        while True:
            _drain_inbox()
            if not pending and not deferred_sampling:
                break
            if pending:
                _run_wave()
                gc.collect()
                mx.clear_cache()
                _reassert_wired_limit()
            while deferred_sampling:
                samp_item = deferred_sampling.pop(0)
                if samp_item.stop_event.is_set():
                    samp_item.out_queue.put(_DONE)
                else:
                    solo_handler(samp_item)

    # Pre-compile Metal shaders for BOTH solo MTP and Ragged BatchGenerator before serving.
    dummy_img = Image.new("RGB", (64, 64), "white")
    noop_stop = threading.Event()
    if runner is not None and mtp is not None:
        for _ in _run_solo_stream("Extract the text in the image.", [(dummy_img.copy(), "")], {"max_tokens": 6, "temperature": 0.0}, noop_stop):
            pass
    w_items = [
        _WorkItem(("Extract the text in the image.", [(dummy_img.copy(), "")], {"max_tokens": 3, "temperature": 0.0}), queue.Queue(), noop_stop)
        for _ in range(2)
    ]
    dummy_img.close()
    batch_handler(w_items, queue.Queue())
    _release_gpu()

    return _AdaptiveDispatcher(
        solo_handler=solo_handler,
        batch_handler=batch_handler,
        can_solo=can_solo,
        after_idle=_release_gpu,
        coalesce_window_s=0.015,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Xiaomi-OCR-0 Dedicated MLX OpenAI Server")
    parser.add_argument("--model", required=True, help="HF repo or local checkpoint directory to serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--max-kv-size", type=int, default=None)
    parser.add_argument("--max-batch-size", type=int, default=None)
    parser.add_argument("--vision-cache-size", type=int, default=4)
    parser.add_argument("--cache-limit-gb", type=float, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    import mlx.core as mx
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    globals()["Request"] = Request

    total_ram_bytes = int((mx.device_info() or {}).get("memory_size") or 0)
    budget = _compute_mlx_memory_budget(
        total_ram_bytes,
        args.cache_limit_gb,
        args.max_batch_size,
    )
    mx.set_cache_limit(int(budget["cache_limit_bytes"]))
    mx.set_memory_limit(int(budget["memory_limit_bytes"]))
    mx.set_wired_limit(int(budget["wired_limit_bytes"]))
    print(
        f"xiaomi_mlx_server: memory budget "
        f"cache_limit={budget['cache_limit_bytes'] / 1024**3:.2f}GB, "
        f"wired_limit={budget['wired_limit_bytes'] / 1024**3:.2f}GB, "
        f"memory_limit={budget['memory_limit_bytes'] / 1024**3:.2f}GB, "
        f"B={budget['max_batch_size']}",
        flush=True,
    )

    model_id = args.model
    dispatcher = _build_dispatcher(
        model_id,
        vision_cache_size=args.vision_cache_size,
        max_batch_size=int(budget["max_batch_size"]),
        low_memory=bool(budget["low_memory"]),
        wired_limit_bytes=int(budget["wired_limit_bytes"]),
        active_recycle_bytes=int(budget["active_recycle_bytes"]),
    )

    app = FastAPI(title="Tabularium Xiaomi-OCR-0 MLX")

    def gen_kwargs(body: dict) -> dict[str, Any]:
        chat_kw = body.get("chat_template_kwargs")
        enable_thinking = (
            bool(chat_kw.get("enable_thinking"))
            if isinstance(chat_kw, dict) and "enable_thinking" in chat_kw
            else False
        )
        kwargs: dict[str, Any] = {
            "max_tokens": int(body.get("max_tokens") or args.max_tokens),
            "temperature": float(body.get("temperature") if body.get("temperature") is not None else 0.0),
            "enable_thinking": enable_thinking,
            "skip_special_tokens": True,
        }
        for key in (
            "top_p", "top_k", "repetition_penalty", "presence_penalty",
            "frequency_penalty", "repetition_context_size", "presence_context_size",
            "frequency_context_size", "logit_bias",
        ):
            if body.get(key) is not None:
                kwargs[key] = body[key]
        return kwargs

    @app.get("/v1/models")
    def list_models() -> dict:
        return {
            "object": "list",
            "data": [{"id": model_id, "object": "model", "created": 0, "max_model_len": 16384}],
        }

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "model": model_id}

    def completion_id() -> str:
        return "chatcmpl-" + uuid.uuid4().hex

    @app.post("/v1/chat/completions")
    def chat_completions(body: dict, request: Request):
        try:
            text, images = _extract(body.get("messages") or [])
        except (ValueError, OSError) as exc:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": str(exc), "type": "invalid_request_error"}},
            )
        if not images:
            return JSONResponse(
                status_code=400,
                content={"error": {"message": "no image provided in messages", "type": "invalid_request_error"}},
            )
        kwargs = gen_kwargs(body)
        kwargs["stream"] = bool(body.get("stream"))
        if request.headers.get("x-xiaomi-batch-hint") == "1" or bool(body.get("batch_hint")):
            kwargs["batch_hint"] = True
        created = int(time.time())
        cid = completion_id()
        stream_opts = body.get("stream_options")
        include_usage = bool(stream_opts.get("include_usage")) if isinstance(stream_opts, dict) else False

        if not body.get("stream"):
            text_out = ""
            prompt_tokens = completion_tokens = 0
            finish = "stop"
            for result in dispatcher.submit((text, images, kwargs)):
                text_out += result.text
                prompt_tokens = result.prompt_tokens
                completion_tokens = result.generation_tokens
                finish = result.finish_reason or finish
            return {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": model_id,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text_out},
                    "finish_reason": finish,
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }

        def chunk(delta: dict, finish: str | None = None) -> dict:
            return {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }

        def event_stream():
            yield _sse(chunk({"role": "assistant", "content": ""}))
            prompt_tokens = completion_tokens = 0
            finish: str | None = None
            for result in dispatcher.submit((text, images, kwargs)):
                prompt_tokens = result.prompt_tokens
                completion_tokens = result.generation_tokens
                finish = result.finish_reason or finish
                if result.text:
                    yield _sse(chunk({"content": result.text}))
            yield _sse(chunk({}, finish or "stop"))
            if include_usage:
                yield _sse({
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_id,
                    "choices": [],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                })
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    uvicorn.run(app, host=args.host, port=args.port, log_level=str(args.log_level).lower())


def _exclusive(generation, after):
    """Run one generation on the shared Metal model, then `after`, serialized by `_GPU_LOCK`."""
    import inspect

    out: queue.Queue = queue.Queue()
    stop = threading.Event()
    takes_stop = False
    try:
        takes_stop = len(inspect.signature(generation).parameters) >= 1
    except (TypeError, ValueError):
        takes_stop = False

    def run() -> None:
        try:
            with _GPU_LOCK:
                try:
                    iterator = generation(stop) if takes_stop else generation()
                    for item in iterator:
                        out.put(item)
                        if stop.is_set():
                            break
                finally:
                    after()
        except BaseException as exc:  # noqa: BLE001
            out.put(_Failed(exc))
        finally:
            out.put(_DONE)

    threading.Thread(target=run, name="xiaomi-mlx-generation", daemon=True).start()
    try:
        while (item := out.get()) is not _DONE:
            if isinstance(item, _Failed):
                raise item.exc
            yield item
    finally:
        stop.set()


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


if __name__ == "__main__":
    main()
