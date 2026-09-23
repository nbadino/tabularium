#!/usr/bin/env python3
"""Confronto riproducibile dei workflow OCR nativi esposti via vLLM.

Il benchmark non modifica il DB né le annotazioni. Ogni target è un server
OpenAI-compatible già avviato: questo mantiene separati serving e misura e
permette di confrontare adapter diversi con la stessa immagine e lo stesso
task. Il default ``native`` usa il percorso completo dell'adapter: pipeline
layout+OCR del produttore, END2END oppure layout seguito dal riconoscimento
dei crop. Gli altri task isolano una singola fase per la diagnosi.

Esempio:
  PYTHONPATH=backend python scripts/benchmark_models.py \
    --image test/1502-a-BANCO-SAN-GIORGIO-originale.jpg \
    --target monkeyocrv2-parsing,http://127.0.0.1:8888/v1,MonkeyOCRv2 \
    --target mineru2.5,http://127.0.0.1:8889/v1,mineru2.5 \
    --repeat 2 --output data/benchmarks/run.json

I risultati contengono latenza, TTFT, throughput, token usage e controlli di
validità coerenti con il formato dichiarato dal modello; non diventano
annotazioni automaticamente.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser

from PIL import Image

from app.services import inference, model_adapters, otsl, paddle_official


def _target(value: str) -> tuple[str, str, str, str, str]:
    parts = value.split(",", 4)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise argparse.ArgumentTypeError(
            "target deve essere adapter_id,url[,served_model_name[,provider[,native_url]]]"
        )
    parts += [""] * (5 - len(parts))
    return tuple(parts)  # type: ignore[return-value]


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows = 0
        self.cells = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "tr":
            self.rows += 1
        elif tag.lower() in {"td", "th"}:
            self.cells += 1


def _valid_table_output(adapter, text: str) -> bool:
    table_format = adapter.capabilities.table_format
    if table_format == "otsl":
        try:
            return otsl.looks_like_otsl(text) and bool(otsl.otsl_to_grid(text).get("cells"))
        except Exception:  # noqa: BLE001
            return False
    if table_format == "html":
        parser = _HtmlTableParser()
        parser.feed(text)
        return parser.rows > 0 and parser.cells > 0
    if table_format == "markdown":
        return sum("|" in line for line in text.splitlines()) >= 2
    # No structured table serialization is declared for this adapter, so
    # measure extraction without imposing Tabularium's preferred format.
    return bool(text.strip())


def _run(client, image: Image.Image, task: str, timeout: float, max_pixels: int | None):
    started = time.perf_counter()
    if task == "native":
        output = _run_native(client, image, timeout, max_pixels)
        valid = sum(
            1 for item in output
            if item.get("label") and len(item.get("bbox", [])) == 4
            and item["bbox"][0] < item["bbox"][2]
            and item["bbox"][1] < item["bbox"][3]
        )
        summary = {
            "items": len(output),
            "valid_items": valid,
            "labels": dict(sorted({label: sum(x.get("label") == label for x in output)
                                   for label in {x.get("label", "") for x in output}}.items())),
            "recognized_chars": sum(len(str(x.get("content") or "")) for x in output),
            "protocol_valid": valid > 0,
        }
    elif task == "layout":
        output = client.layout(image, total_timeout=timeout)
        valid = sum(1 for item in output if item.get("label") and len(item.get("bbox", [])) == 4)
        summary = {"items": len(output), "valid_items": valid, "protocol_valid": valid > 0}
    elif task == "end2end":
        output = client.end2end(image, max_pixels=max_pixels, total_timeout=timeout)
        valid = sum(1 for item in output if item.get("label") and len(item.get("bbox", [])) == 4)
        summary = {"items": len(output), "valid_items": valid, "protocol_valid": valid > 0}
    elif task == "text":
        output = client.recognize(image, "Text", total_timeout=timeout)
        summary = {"chars": len(output), "non_empty": bool(output.strip()),
                   "protocol_valid": bool(output.strip())}
    else:
        output = client.recognize(image, "Table", total_timeout=timeout)
        text = output.strip()
        valid = _valid_table_output(client.adapter, text)
        summary = {
            "chars": len(text),
            "table_format": client.adapter.capabilities.table_format,
            "format_valid": valid,
            "protocol_valid": valid,
        }
    return summary, dict(client.last_trace), round(time.perf_counter() - started, 3), output


def _run_native(
    client, image: Image.Image, timeout: float, max_pixels: int | None = None,
) -> list[dict]:
    """Exercise the model's default page workflow, including auxiliary stacks."""
    from app.services.prefill import native_mode

    adapter = client.adapter
    mode = native_mode(adapter)
    if mode == "official":
        if adapter.adapter_id == "teleocr":
            return client.teleocr_native_page(image)
        if adapter.adapter_id == "glm-ocr":
            return client.glmocr_native_page(image)
        if adapter.adapter_id == "paddleocr-vl":
            backend = "vllm-server"
            if client.provider == "local":
                from app.services import hardware
                if hardware.pick_serve_runtime(adapter.capabilities) == hardware.RUNTIME_MLX:
                    backend = "mlx-vlm-server"
            items = paddle_official.parse_page(
                image, client.url, client.model, image.width, image.height,
                vl_rec_backend=backend,
            )
            for item in items:
                box = item.get("bbox") or []
                if len(box) == 4:
                    item["bbox"] = [
                        round(float(box[0]) / image.width * 1000),
                        round(float(box[1]) / image.height * 1000),
                        round(float(box[2]) / image.width * 1000),
                        round(float(box[3]) / image.height * 1000),
                    ]
            return items
        raise ValueError(f"workflow ufficiale non implementato per {adapter.adapter_id}")
    if mode == "end2end":
        return client.end2end(image, max_pixels=max_pixels, total_timeout=timeout)

    # Native two-stage models first detect the full page, then recognize each
    # supported region at its source resolution, as the application prefill does.
    items = client.layout(image, total_timeout=timeout)
    completed = []
    for item in items:
        result = dict(item)
        box = result.get("bbox") or []
        label = str(result.get("label") or "")
        try:
            prompt = adapter.prompt_for("table" if label == "Table" else "text", label)
        except NotImplementedError:
            prompt = None
        if prompt and len(box) == 4 and not result.get("content"):
            x1, y1, x2, y2 = [
                max(0, min(limit, round(float(value) * limit / 1000)))
                for value, limit in zip(box, (image.width, image.height, image.width, image.height))
            ]
            if x1 < x2 and y1 < y2:
                result["content"] = client.recognize(
                    image.crop((x1, y1, x2, y2)), label, total_timeout=timeout,
                )
        completed.append(result)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--target", type=_target, action="append", required=True,
                        help="adapter_id,url[,served_model_name[,provider[,native_url]]] (ripetibile)")
    parser.add_argument("--task", choices=("native", "layout", "end2end", "text", "table"), default="native",
                        help="native usa il workflow completo predefinito da ciascun adapter")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-pixels", type=int, default=None)
    parser.add_argument("--output", type=Path,
                        help="report JSON; default: data/benchmarks/bench_<UTC>/report.json")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat deve essere almeno 1")
    if not args.image.is_file():
        parser.error(f"immagine non trovata: {args.image}")
    image = Image.open(args.image).convert("RGB")
    report_path = args.output or Path("data/benchmarks") / (
        f"bench_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    ) / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "tabularium-vlm-benchmark-v1",
        "image": str(args.image),
        "image_size": [image.width, image.height],
        "task": args.task,
        "repeat": args.repeat,
        "results": [],
    }
    for adapter_id, url, model, provider, native_url in args.target:
        adapter = model_adapters.get_adapter(adapter_id)
        if not model:
            model = getattr(adapter.capabilities, "served_model_name", None) or adapter_id
        client_kwargs = {"url": url, "model": model, "adapter": adapter,
                         "timeout": max(5, int(args.timeout)), "max_retries": 0}
        if provider:
            client_kwargs["provider"] = provider
        if native_url:
            client_kwargs["native_url"] = native_url
        # None means "use the adapter/config default". Passing None explicitly
        # to VllmClient means "disable the cap", which is useful for an
        # experiment but must never be the benchmark default.
        if args.max_pixels is not None:
            client_kwargs["max_pixels"] = args.max_pixels
        client = inference.VllmClient(**client_kwargs)
        target_results = []
        for iteration in range(args.repeat):
            started = time.perf_counter()
            entry = {"adapter_id": adapter_id, "url": url, "model": model,
                     "iteration": iteration + 1}
            try:
                summary, trace, wall_s, output = _run(client, image, args.task, args.timeout, args.max_pixels)
                protocol_valid = bool(summary.get("protocol_valid", True))
                entry.update({"ok": protocol_valid, "summary": summary, "trace": trace,
                              "wall_s": wall_s})
                if not protocol_valid:
                    entry["error"] = "risposta ricevuta ma output non conforme al protocollo del task"
                raw_dir = report_path.parent / "outputs" / adapter_id
                raw_dir.mkdir(parents=True, exist_ok=True)
                raw_path = raw_dir / f"{args.task}-{iteration + 1:03d}.json"
                raw_path.write_text(json.dumps({"adapter_id": adapter_id, "model": model,
                                                 "task": args.task, "summary": summary,
                                                 "trace": trace, "output": output},
                                                ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
                entry["output_file"] = str(raw_path)
            except Exception as exc:  # noqa: BLE001
                entry.update({"ok": False, "error": str(exc), "trace": dict(client.last_trace),
                              "wall_s": round(time.perf_counter() - started, 3)})
                if getattr(client, "last_text", ""):
                    entry["raw_text"] = client.last_text
                raw_dir = report_path.parent / "outputs" / adapter_id
                raw_dir.mkdir(parents=True, exist_ok=True)
                error_path = raw_dir / f"{args.task}-{iteration + 1:03d}-error.json"
                error_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
                entry["output_file"] = str(error_path)
            target_results.append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        successful = [r for r in target_results if r["ok"]]
        elapsed = [r["wall_s"] for r in successful]
        report["results"].append({
            "adapter_id": adapter_id, "url": url, "model": model,
            "runs": target_results,
            "aggregate": {
                "ok": len(successful), "failed": len(target_results) - len(successful),
                "mean_wall_s": statistics.mean(elapsed) if elapsed else None,
                "p50_wall_s": statistics.median(elapsed) if elapsed else None,
            },
        })
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    report_path.write_text(encoded, encoding="utf-8")
    print(f"benchmark report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
