"""Tabularium's HTTP gateway around Indra OCR (github.com/datalab-to/indra).

Indra is not a chat model: one call, `indra_ocr.load(...).ocr(gray)`, runs line
detection, recognition, layout and reading order on a page and returns a `Page`. This
module only carries one page in and that `Page` out, so the same pipeline serves the
local machine, a rented Vast.ai GPU and any server started by hand:

    python indra_native_gateway.py --model-dir <snapshot of datalab-to/indra-ocr> \\
        --host 127.0.0.1 --port 8889 [--device auto|cpu|cuda] [--threads 4] \\
        [--workers 1] [--math] [--api-key SECRET]

Routes: `GET /health` ({"status": "ready"|"starting"|"failed"}), `GET /v1/models` (the
served model name, so the readiness probes written for vLLM work unchanged) and
`POST /parse` (raw image bytes; the reply is the page's blocks in pixels, the markdown,
the lines and the image size).

It needs `indra-ocr`, `fastapi` and `uvicorn`; on a GPU, `onnxruntime-gpu` in place of
`onnxruntime`. Nothing here is Tabularium code: the file runs in its own environment.
"""
# No `from __future__ import annotations`: FastAPI reads the route annotations
# (`request: Request`) at runtime, and `Request` is imported inside `create_app`.
import argparse
import hmac
import io
import logging
import os
import queue
import threading
from typing import Any

log = logging.getLogger("tabularium.indra")

MAX_BODY_BYTES = 128 * 1024 * 1024
DEVICES = ("auto", "cpu", "cuda")


def pick_providers(device: str, available: list[str]) -> list[str]:
    """ONNX Runtime execution providers for `device`, as `OrtOCR(providers=...)` takes them.

    `auto` takes CUDA when this ONNX Runtime build has it and falls back to the CPU;
    `cuda` refuses to start without it, so a GPU instance never silently runs on CPU.
    """
    if device not in DEVICES:
        raise ValueError(f"device must be one of {DEVICES}, not {device!r}")
    has_cuda = "CUDAExecutionProvider" in available
    if device == "cuda" and not has_cuda:
        raise RuntimeError(
            "device=cuda, but this ONNX Runtime has no CUDAExecutionProvider "
            f"(available: {', '.join(available) or 'none'}): install onnxruntime-gpu"
        )
    if device != "cpu" and has_cuda:
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def page_payload(page: Any, width: int, height: int) -> dict:
    """An `indra_ocr.Page` as JSON: what Tabularium's adapter reads, nothing reshaped."""
    return {
        "width": int(width),
        "height": int(height),
        "blocks": [
            {"label": b.get("label"), "bbox": list(b.get("bbox") or []), "text": b.get("text") or ""}
            for b in (page.blocks or [])
        ],
        "markdown": page.markdown or "",
        "lines": [
            {"bbox": list(line.box), "text": line.text, "conf": float(line.conf)}
            for line in (page.lines or [])
        ],
        "source": getattr(page, "source", "ocr"),
        "rotation": int(getattr(page, "rotation", 0) or 0),
        "skew": float(getattr(page, "skew", 0.0) or 0.0),
        "vertical": bool(getattr(page, "vertical", False)),
        "timings": dict(getattr(page, "timings", {}) or {}),
    }


class Pool:
    """`workers` converters, one page each at a time.

    ONNX Runtime releases the GIL while a session runs, so pages read in parallel
    threads; each converter keeps its own sessions, as `convert_many` keeps one per
    worker process.
    """

    def __init__(self, factory, workers: int) -> None:
        self._idle: queue.Queue = queue.Queue()
        for _ in range(max(1, int(workers))):
            self._idle.put(factory())

    def run(self, fn):
        converter = self._idle.get()
        try:
            return fn(converter)
        finally:
            self._idle.put(converter)


class Gateway:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.pool: Pool | None = None
        self.error = ""
        self.providers: list[str] = []

    def load(self) -> None:
        try:
            import onnxruntime as ort

            preload = getattr(ort, "preload_dlls", None)
            if preload is not None and self.args.device != "cpu":
                # onnxruntime-gpu >= 1.21: load CUDA/cuDNN from the nvidia-* wheels
                # its `cuda`/`cudnn` extras install, so no system CUDA is needed.
                try:
                    preload()
                except Exception as exc:  # noqa: BLE001 - CPU still works
                    log.warning("onnxruntime.preload_dlls failed: %s", exc)
            self.providers = pick_providers(self.args.device, list(ort.get_available_providers()))
            import indra_ocr

            def factory():
                return indra_ocr.load(
                    path=self.args.model_dir,
                    threads=self.args.threads,
                    mode="ocr",
                    providers=self.providers,
                    math=self.args.math,
                )

            self.pool = Pool(factory, self.args.workers)
            log.info(
                "Indra OCR ready: providers=%s workers=%d threads=%d math=%s",
                self.providers, self.args.workers, self.args.threads, self.args.math,
            )
        except Exception as exc:  # noqa: BLE001 - reported on /health
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("Indra OCR failed to load")

    def status(self) -> str:
        if self.pool is not None:
            return "ready"
        return "failed" if self.error else "starting"

    def parse(self, body: bytes) -> dict:
        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(body))
        image.load()
        gray = np.ascontiguousarray(np.asarray(image.convert("L"), dtype=np.uint8))
        height, width = gray.shape[:2]
        page = self.pool.run(lambda converter: converter.ocr(gray))
        return page_payload(page, width, height)


def create_app(args: argparse.Namespace):
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.concurrency import run_in_threadpool
    from PIL import UnidentifiedImageError

    gateway = Gateway(args)
    threading.Thread(target=gateway.load, name="indra-load", daemon=True).start()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.gateway = gateway

    def check_key(request: Request) -> None:
        expected = args.api_key or ""
        given = request.headers.get("authorization", "").removeprefix("Bearer ")
        if expected and not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/health")
    async def health() -> dict:
        reply = {"status": gateway.status(), "providers": gateway.providers}
        if gateway.error:
            reply["error"] = gateway.error
        return reply

    @app.get("/v1/models")
    async def models(request: Request) -> dict:
        check_key(request)
        if gateway.status() != "ready":
            # vLLM answers only once the weights are loaded: the same here.
            raise HTTPException(status_code=503, detail=gateway.error or "Indra OCR is loading")
        return {"object": "list", "data": [
            {"id": args.served_model_name, "object": "model", "owned_by": "datalab-to"},
        ]}

    @app.post("/parse")
    async def parse(request: Request) -> dict:
        check_key(request)
        if gateway.status() != "ready":
            raise HTTPException(status_code=503, detail=gateway.error or "Indra OCR is loading")
        body = await request.body()
        if not body or len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Invalid image payload size")
        try:
            return await run_in_threadpool(gateway.parse, body)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise HTTPException(status_code=415, detail=f"Unsupported image payload: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - a concise upstream error
            raise HTTPException(status_code=502, detail=f"Indra OCR failed: {exc}") from exc

    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8889)
    parser.add_argument("--device", choices=DEVICES, default="auto")
    # `indra_ocr.load` defaults: four threads per converter, math off.
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--math", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--served-model-name", default="indra-ocr")
    parser.add_argument("--api-key", default=os.environ.get("TABULARIUM_SERVER_API_KEY", ""))
    args = parser.parse_args(argv)
    if args.threads < 1 or args.workers < 1:
        parser.error("--threads and --workers must be at least 1")
    return args


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args(argv)
    import uvicorn

    uvicorn.run(create_app(args), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
