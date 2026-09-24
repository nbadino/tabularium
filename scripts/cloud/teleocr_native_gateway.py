"""Small cloud-side HTTP bridge to TeleOCR's official asynchronous client.

It runs beside vLLM inside the TeleOCR recipe environment. The only image
processing/inference pipeline is ``TeleOCRClient.aio_batch_two_step_extract``;
this module only transports one page across the SSH tunnel and returns the
vendor's post-processed ContentBlock objects.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError

_lock = asyncio.Lock()
_client = None


async def _startup() -> None:
    global _client
    import json
    import TeleOCR.config as CONFIG
    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.v1.engine.async_llm import AsyncLLM
    from TeleOCR.vlm_utils.TeleOCR_client import TeleOCRClient

    try:
        settings = json.loads(os.environ.get("TABULARIUM_TELEOCR_SETTINGS", "{}"))
    except ValueError as exc:
        raise RuntimeError("invalid TeleOCR runner settings") from exc
    serving = settings.get("serving") or {}
    image = settings.get("image") or {}
    engine_args = {
        "dtype": serving.get("dtype", "bfloat16"),
        "gpu_memory_utilization": serving.get("gpu_memory_utilization", 0.95),
        "max_model_len": serving.get("max_model_len", 16384),
    }
    for key in ("max_num_seqs", "max_num_batched_tokens"):
        if serving.get(key) is not None:
            engine_args[key] = serving[key]
    model_path = os.environ.get("TABULARIUM_TELEOCR_MODEL_PATH")
    if not model_path:
        raise RuntimeError("TABULARIUM_TELEOCR_MODEL_PATH is required")
    CONFIG.MAX_PIXELS = int(image.get("max_pixels") or 64_000_000)
    async_llm = AsyncLLM.from_engine_args(AsyncEngineArgs(model_path, **engine_args))
    _client = TeleOCRClient(
        # This is the producer's documented inference path (README `infer.py`),
        # not a second OpenAI server plus an HTTP client shim.
        backend="vllm-async-engine",
        model_name=os.environ.get("TABULARIUM_TELEOCR_MODEL", "StarDoc-AI/TeleOCR"),
        model_path=model_path,
        vllm_async_llm=async_llm,
        max_concurrency=int(serving.get("max_num_seqs") or 100),
        use_tqdm=False,
    )


@asynccontextmanager
async def _lifespan(_app):
    await _startup()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ready" if _client is not None else "starting"}


@app.post("/parse")
async def parse_page(request: Request) -> dict:
    if _client is None:
        raise HTTPException(status_code=503, detail="TeleOCR runner is starting")
    expected = os.environ.get("TABULARIUM_SERVER_API_KEY", "")
    if expected and not hmac.compare_digest(
        request.headers.get("authorization", "").removeprefix("Bearer "), expected
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")
    body = await request.body()
    if not body or len(body) > 128 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Invalid image payload size")
    try:
        image = Image.open(io.BytesIO(body)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=415, detail="Unsupported image payload") from exc
    mode = request.headers.get("x-teleocr-layout-mode", "Detection")
    if mode not in {"Detection", "Segmentation"}:
        raise HTTPException(status_code=422, detail="Invalid TeleOCR layout mode")

    # TeleOCR reads LAYOUT_MODE from its config module global. Serialize page
    # runs so concurrent requests cannot change the mode mid-inference; its
    # own aio_batch_two_step_extract still batches all crop requests internally.
    async with _lock:
        import TeleOCR.config as CONFIG
        from TeleOCR.vlm_utils.TeleOCR_client import TeleOCRSamplingParams

        CONFIG.LAYOUT_MODE = mode
        try:
            max_pixels = int(request.headers.get("x-teleocr-max-pixels", "64000000"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid max-pixels value") from exc
        if max_pixels > 0:
            CONFIG.MAX_PIXELS = min(max_pixels, 64_000_000)
        try:
            overrides = json.loads(request.headers.get("x-teleocr-generation", "{}"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid generation settings") from exc
        if not isinstance(overrides, dict) or set(overrides) - {
            "temperature", "top_p", "top_k", "max_tokens", "repetition_penalty",
            "presence_penalty", "frequency_penalty", "no_repeat_ngram_size",
        }:
            raise HTTPException(status_code=422, detail="Unsupported generation setting")
        parameter_names = (
            "temperature", "top_p", "top_k", "presence_penalty", "frequency_penalty",
            "repetition_penalty", "no_repeat_ngram_size", "max_new_tokens",
        )
        for task, current in list(_client.sampling_params.items()):
            params = {name: getattr(current, name, None) for name in parameter_names}
            for key, value in overrides.items():
                params["max_new_tokens" if key == "max_tokens" else key] = value
            _client.sampling_params[task] = TeleOCRSamplingParams(**params)
        try:
            result = await _client.aio_batch_two_step_extract([image])
        except Exception as exc:  # noqa: BLE001 - return a concise upstream error
            raise HTTPException(status_code=502, detail=f"TeleOCR inference failed: {exc}") from exc
    blocks = result[0] if result else []
    return {"blocks": [dict(block) for block in blocks]}
