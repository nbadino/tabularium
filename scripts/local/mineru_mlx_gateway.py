"""HTTP entry point for MinerU's official in-process Apple MLX pipeline."""
from __future__ import annotations

import argparse
import hmac
import io
import json
import os
import threading

from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError

_client = None
_defaults = None
_model_name = "opendatalab/MinerU2.5-Pro-2605-1.2B"
_lock = threading.Lock()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


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


def configure(model_path: str, workflow: dict, serving: dict | None = None) -> None:
    global _client, _defaults, _model_name
    from mineru_vl_utils import MinerUClient

    _model_name = model_path
    _client = MinerUClient(
        backend="mlx-engine",
        model_path=model_path,
        use_tqdm=False,
        max_concurrency=int((serving or {}).get("max_num_seqs") or 100),
        layout_image_size=tuple(workflow.get("layout_image_size") or (1036, 1036)),
        min_image_edge=int(workflow.get("min_image_edge") or 28),
        max_image_edge_ratio=float(workflow.get("max_image_edge_ratio") or 50),
        simple_post_process=bool(workflow.get("simple_post_process", False)),
        handle_equation_block=bool(workflow.get("handle_equation_block", True)),
        abandon_list=bool(workflow.get("abandon_list", False)),
        abandon_paratext=bool(workflow.get("abandon_paratext", False)),
        image_analysis=bool(workflow.get("image_analysis", False)),
        enable_table_formula_eq_wrap=bool(workflow.get("enable_table_formula_eq_wrap", False)),
    )
    _defaults = dict(_client.sampling_params)


def _apply_generation(overrides: dict) -> None:
    if not overrides:
        _client.sampling_params = dict(_defaults)
        return
    allowed = {
        "temperature", "top_p", "top_k", "max_tokens", "repetition_penalty",
        "presence_penalty", "frequency_penalty", "no_repeat_ngram_size",
    }
    if set(overrides) - allowed:
        raise ValueError("unsupported generation setting")
    from mineru_vl_utils.mineru_client import MinerUSamplingParams

    for task, current in _defaults.items():
        params = {
            key: getattr(current, key, None)
            for key in (
                "temperature", "top_p", "top_k", "presence_penalty", "frequency_penalty",
                "repetition_penalty", "no_repeat_ngram_size", "max_new_tokens",
            )
        }
        for key, value in overrides.items():
            params["max_new_tokens" if key == "max_tokens" else key] = value
        _client.sampling_params[task] = MinerUSamplingParams(**params)


@app.get("/v1/models")
async def models() -> dict:
    if _client is None:
        raise HTTPException(status_code=503, detail="MinerU MLX is loading")
    return {"object": "list", "data": [{"id": _model_name, "object": "model"}]}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ready" if _client is not None else "starting"}


@app.post("/parse")
async def parse_page(request: Request) -> dict:
    if _client is None:
        raise HTTPException(status_code=503, detail="MinerU MLX pipeline is loading")
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
        with _lock:
            _apply_generation(generation)
            blocks = _client.two_step_extract(image)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MinerU MLX inference failed: {exc}") from exc
    return {"blocks": [_plain(block) for block in blocks]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--settings", default="{}")
    args = parser.parse_args()
    settings = json.loads(args.settings)
    configure(args.model, settings.get("workflow", {}), settings.get("serving"))
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
