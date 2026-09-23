"""HTTP bridge to GLM-OCR's own self-hosted document parsing SDK.

The SDK owns page loading, PP-DocLayout detection, region OCR, reading order,
and result formatting. This bridge only transports one scanned page through
the SSH tunnel and forwards supported sampling/image overrides.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

_lock = asyncio.Lock()
_parser = None


async def _startup() -> None:
    global _parser
    from glmocr import GlmOcr

    _parser = GlmOcr(
        mode="selfhosted",
        api_key=os.environ.get("TABULARIUM_SERVER_API_KEY") or None,
        ocr_api_host="127.0.0.1",
        ocr_api_port=int(os.environ.get("TABULARIUM_GLMOCR_VLLM_PORT", "8888")),
        layout_device=os.environ.get("TABULARIUM_GLMOCR_LAYOUT_DEVICE") or None,
    )


async def _shutdown() -> None:
    global _parser
    if _parser is not None:
        _parser.close()
        _parser = None


@asynccontextmanager
async def _lifespan(_app):
    await _startup()
    try:
        yield
    finally:
        await _shutdown()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ready" if _parser is not None else "starting"}


def _parse(body: bytes, generation: dict, max_pixels: int | None) -> dict:
    parser = _parser
    if parser is None:
        raise RuntimeError("GLM-OCR SDK is not initialized")
    page_loader = parser.config_model.pipeline.page_loader
    previous = {}
    options = {
        "max_tokens": "max_tokens",
        "temperature": "temperature",
        "top_p": "top_p",
        "top_k": "top_k",
        "repetition_penalty": "repetition_penalty",
    }
    try:
        for request_key, config_key in options.items():
            if request_key in generation:
                previous[config_key] = getattr(page_loader, config_key)
                setattr(page_loader, config_key, generation[request_key])
        if max_pixels is not None:
            previous["max_pixels"] = page_loader.max_pixels
            page_loader.max_pixels = max_pixels
        result = parser.parse(body, save_layout_visualization=False)
        return {"json_result": result.json_result, "markdown_result": result.markdown_result or ""}
    finally:
        for key, value in previous.items():
            setattr(page_loader, key, value)


@app.post("/parse")
async def parse_page(request: Request) -> dict:
    if _parser is None:
        raise HTTPException(status_code=503, detail="GLM-OCR pipeline is starting")
    expected = os.environ.get("TABULARIUM_SERVER_API_KEY", "")
    if expected and not hmac.compare_digest(
        request.headers.get("authorization", "").removeprefix("Bearer "), expected
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")
    body = await request.body()
    if not body or len(body) > 128 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Invalid image payload size")
    try:
        generation = json.loads(request.headers.get("x-glmocr-generation", "{}"))
        if not isinstance(generation, dict) or set(generation) - {
            "max_tokens", "temperature", "top_p", "top_k", "repetition_penalty",
        }:
            raise ValueError("unsupported generation settings")
        raw_max_pixels = request.headers.get("x-glmocr-max-pixels")
        max_pixels = int(raw_max_pixels) if raw_max_pixels else None
        if max_pixels == 0:
            max_pixels = None
        if max_pixels is not None and not 1 <= max_pixels <= 128_000_000:
            raise ValueError("max_pixels out of range")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid GLM-OCR settings") from exc
    async with _lock:
        try:
            result = await run_in_threadpool(_parse, body, generation, max_pixels)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"GLM-OCR inference failed: {exc}") from exc
    return result
