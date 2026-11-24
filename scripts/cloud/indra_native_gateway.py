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

With `--line-reader FILE` the page is read by a line-reader package instead of the
vendor pipeline: `FILE` is a module inside `--model-dir` (next to its fine-tuned
`rec.onnx`) that exposes `load(model_dir, providers=..., threads=...)` and whose reader
returns the page's table as OTSL from `read(gray)`. The reply keeps the same shape:
one `Table` block whose text is the OTSL, or the reader's own `blocks` when it reads
the whole page (it then gets the stock models through `--stock-model-dir`).

It needs `indra-ocr`, `fastapi` and `uvicorn`; on a GPU, `onnxruntime-gpu` in place of
`onnxruntime`. Nothing here is Tabularium code: the file runs in its own environment.
"""
# No `from __future__ import annotations`: FastAPI reads the route annotations
# (`request: Request`) at runtime, and `Request` is imported inside `create_app`.
import argparse
import hmac
import importlib.util
import inspect
import io
import logging
import os
import queue
import sys
import threading
from pathlib import Path
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


def load_line_reader(model_dir: str, filename: str):
    """Import the line-reader module of an installed package folder."""
    path = Path(model_dir) / filename
    if not path.is_file():
        raise FileNotFoundError(f"the model folder {model_dir} lacks {filename}")
    # A reader may be a package next to its entry module: the folder goes on the path.
    folder = str(Path(model_dir).resolve())
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location(f"tabularium_line_reader_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lines_payload(out: dict, width: int, height: int) -> dict:
    """A line reader's result in the gateway's reply shape: the table as one block.

    The reader gives the table's horizontal extent and the centre of every line it
    read (`ys_input`: in the image it received); the block spans them, half a line
    pitch above the first and below the last, inside the page. A page where no line
    was found has no block.
    """
    otsl = str(out.get("otsl") or "")
    centres = [float(y) for y in (out.get("ys_input") or out.get("ys") or [])]
    blocks = []
    if isinstance(out.get("blocks"), list):
        # A whole-page reader places every block itself (title, date, table, adverts...):
        # they pass as they are; one without a usable box is dropped, never guessed.
        for block in out["blocks"]:
            box = block.get("bbox") if isinstance(block, dict) else None
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            blocks.append({
                "label": str(block.get("label") or "Text"),
                "bbox": [float(value) for value in box],
                "text": str(block.get("text") or ""),
            })
    elif otsl and centres:
        half = float(out.get("pitch") or 0.0) / 2
        bbox = [
            max(0.0, float(out.get("x0") or 0.0)),
            max(0.0, min(centres) - half),
            min(float(width), float(out.get("x1") or width)),
            min(float(height), max(centres) + half),
        ]
        blocks.append({"label": "Table", "bbox": bbox, "text": otsl})
    return {
        "width": int(width),
        "height": int(height),
        "blocks": blocks,
        "markdown": "",
        "lines": [{"text": str(line[0])} for line in (out.get("lines") or []) if line],
        "source": "line-reader",
        "timings": dict(out.get("seconds") or {}),
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
        self.reader_module = None

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
            if self.args.line_reader:
                self.reader_module = load_line_reader(self.args.model_dir, self.args.line_reader)

                options = {"providers": self.providers, "threads": self.args.threads}
                # The stock Indra models, for a reader that also reads outside the table.
                # A table-only reader's `load` does not take them and is not given them.
                if self.args.stock_model_dir and "stock_dir" in inspect.signature(
                    self.reader_module.load
                ).parameters:
                    options["stock_dir"] = self.args.stock_model_dir

                def factory():
                    return self.reader_module.load(self.args.model_dir, **options)

                self.pool = Pool(factory, self.args.workers)
                log.info(
                    "Line reader %s ready: providers=%s workers=%d threads=%d",
                    self.args.line_reader, self.providers, self.args.workers, self.args.threads,
                )
                return
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
        if self.args.line_reader:
            return lines_payload(self.pool.run(lambda reader: reader.read(gray)), width, height)
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
    # A module inside --model-dir that reads the page instead of the vendor pipeline.
    parser.add_argument("--line-reader", default="")
    # The stock Indra model folder, handed to a line reader that asks for it.
    parser.add_argument("--stock-model-dir", default="")
    parser.add_argument("--api-key", default=os.environ.get("TABULARIUM_SERVER_API_KEY", ""))
    args = parser.parse_args(argv)
    if args.threads < 1 or args.workers < 1:
        parser.error("--threads and --workers must be at least 1")
    if args.line_reader and (Path(args.line_reader).name != args.line_reader or not args.line_reader.endswith(".py")):
        parser.error("--line-reader is the name of a .py file inside --model-dir, not a path")
    if args.line_reader and args.math:
        parser.error("--math belongs to the vendor pipeline, not to a line reader")
    return args


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args(argv)
    import uvicorn

    uvicorn.run(create_app(args), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
