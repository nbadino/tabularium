"""HTTP bridge to MinerU's official MinerUClient two-step pipeline.

The upstream client performs its own layout resizing, region preparation,
concurrent text/table/formula recognition, and post-processing. vLLM only
serves the model; this bridge forwards a page into that client unchanged.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
import httpx
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

_lock = asyncio.Lock()
_client = None
_default_sampling_params = None


async def _startup() -> None:
    global _client, _default_sampling_params
    from mineru_vl_utils import MinerUClient

    settings = json.loads(os.environ.get("TABULARIUM_MINERU_SETTINGS", "{}"))
    serving = settings.get("serving") or {}
    workflow = settings.get("workflow") or {}
    options = {
        "backend": "http-client",
        "server_url": os.environ.get("TABULARIUM_MINERU_VLLM_URL", "http://127.0.0.1:8888"),
        "model_name": os.environ.get("TABULARIUM_MINERU_MODEL", "mineru2.5"),
        "server_headers": _server_headers(),
        "use_tqdm": False,
        "layout_image_size": tuple(workflow.get("layout_image_size") or (1036, 1036)),
        "min_image_edge": int(workflow.get("min_image_edge") or 28),
        "max_image_edge_ratio": float(workflow.get("max_image_edge_ratio") or 50),
        "simple_post_process": bool(workflow.get("simple_post_process", False)),
        "handle_equation_block": bool(workflow.get("handle_equation_block", True)),
        "abandon_list": bool(workflow.get("abandon_list", False)),
        "abandon_paratext": bool(workflow.get("abandon_paratext", False)),
        "image_analysis": bool(workflow.get("image_analysis", False)),
        "enable_table_formula_eq_wrap": bool(workflow.get("enable_table_formula_eq_wrap", False)),
    }
    if serving.get("max_num_seqs") is not None:
        options["max_concurrency"] = int(serving["max_num_seqs"])
    _client = MinerUClient(**options)
    _default_sampling_params = dict(_client.sampling_params)


def _apply_generation(overrides: dict) -> None:
    if not overrides:
        _client.sampling_params = dict(_default_sampling_params)
        return
    allowed = {
        "temperature", "top_p", "top_k", "max_tokens", "repetition_penalty",
        "presence_penalty", "frequency_penalty", "no_repeat_ngram_size",
    }
    if set(overrides) - allowed:
        raise ValueError("unsupported generation setting")
    from mineru_vl_utils.mineru_client import MinerUSamplingParams

    for task, current in _default_sampling_params.items():
        params = {
            key: getattr(current, key, None)
            for key in (
                "temperature", "top_p", "top_k", "presence_penalty",
                "frequency_penalty", "repetition_penalty", "no_repeat_ngram_size",
                "max_new_tokens",
            )
        }
        for key, value in overrides.items():
            params["max_new_tokens" if key == "max_tokens" else key] = value
        _client.sampling_params[task] = MinerUSamplingParams(**params)


def _server_headers() -> dict[str, str]:
    key = os.environ.get("TABULARIUM_SERVER_API_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


@asynccontextmanager
async def _lifespan(_app):
    await _startup()
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    if _client is None:
        raise HTTPException(status_code=503, detail="MinerU client is starting")
    url = os.environ.get("TABULARIUM_MINERU_VLLM_URL", "http://127.0.0.1:8888").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{url}/v1/models", headers=_server_headers())
            response.raise_for_status()
            models = response.json().get("data", [])
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=503, detail="vLLM model server is starting")
    served = os.environ.get("TABULARIUM_MINERU_MODEL", "mineru2.5")
    if not any(model.get("id") == served for model in models if isinstance(model, dict)):
        raise HTTPException(status_code=503, detail="MinerU model is not served by vLLM")
    return {"status": "ready"}


def _plain(block) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    if hasattr(block, "dict"):
        return block.dict()
    return {
        key: getattr(block, key)
        for key in ("type", "bbox", "angle", "content")
        if hasattr(block, key)
    }


@app.post("/parse")
async def parse_page(request: Request) -> dict:
    if _client is None:
        raise HTTPException(status_code=503, detail="MinerU pipeline is starting")
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
    try:
        generation = json.loads(request.headers.get("x-mineru-generation", "{}"))
        if not isinstance(generation, dict):
            raise ValueError("invalid generation settings")
        async with _lock:
            _apply_generation(generation)
            blocks = await _client.aio_two_step_extract(image)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface concise upstream diagnostics
        raise HTTPException(status_code=502, detail=f"MinerU inference failed: {exc}") from exc
    return {"blocks": [_plain(block) for block in blocks]}
