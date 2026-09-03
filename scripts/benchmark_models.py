#!/usr/bin/env python3
"""Confronto riproducibile dei VLM locali esposti via vLLM.

Il benchmark non modifica il DB né le annotazioni. Ogni target è un server
OpenAI-compatible già avviato: questo mantiene separati serving e misura e
permette di confrontare adapter diversi con la stessa immagine e lo stesso
task.

Esempio:
  PYTHONPATH=backend python scripts/benchmark_models.py \
    --image test/1502-a-BANCO-SAN-GIORGIO-originale.jpg \
    --target monkeyocrv2-parsing,http://127.0.0.1:8888/v1,MonkeyOCRv2 \
    --target mineru2.5,http://127.0.0.1:8889/v1,mineru2.5 \
    --task layout --repeat 2 --output data/benchmarks/run.json

Per i modelli che non hanno ancora un protocollo layout verificato si può
usare ``--task end2end``. I risultati contengono latenza, TTFT, throughput,
token usage e controlli di validità dell'output; non vengono trasformati in
annotazioni automaticamente.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from app.services import inference, model_adapters, otsl


def _target(value: str) -> tuple[str, str, str]:
    parts = value.split(",", 2)
    if len(parts) not in (2, 3) or not all(parts):
        raise argparse.ArgumentTypeError("target deve essere adapter_id,url[,served_model_name]")
    return (parts[0], parts[1], parts[2] if len(parts) == 3 else "")


def _run(client, image: Image.Image, task: str, timeout: float, max_pixels: int | None):
    started = time.perf_counter()
    if task == "layout":
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
        valid = False
        try:
            valid = otsl.looks_like_otsl(text) and bool(otsl.otsl_to_grid(text).get("cells"))
        except Exception:  # noqa: BLE001
            valid = False
        summary = {"chars": len(text), "valid_otsl": valid, "protocol_valid": valid}
    return summary, dict(client.last_trace), round(time.perf_counter() - started, 3), output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--target", type=_target, action="append", required=True,
                        help="adapter_id,url[,served_model_name] (ripetibile; nome automatico se omesso)")
    parser.add_argument("--task", choices=("layout", "end2end", "text", "table"), default="layout")
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
    for adapter_id, url, model in args.target:
        adapter = model_adapters.get_adapter(adapter_id)
        if not model:
            model = getattr(adapter.capabilities, "served_model_name", None) or adapter_id
        client_kwargs = {"url": url, "model": model, "adapter": adapter,
                         "timeout": max(5, int(args.timeout)), "max_retries": 0}
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
