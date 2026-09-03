#!/usr/bin/env python3
"""Benchmark CER/WER su un JSONL annotato fornito dall'utente.

È uno strumento interno di verifica: non modifica il progetto, il database o
le annotazioni e non viene esposto nella UX. Il file passato deve essere un
validation set realmente revisionato: il runner non certifica la qualità del
dataset. Ogni target è un endpoint OpenAI-compatible già avviato.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from app.services import evaluate, inference, model_adapters


def _target(value: str) -> tuple[str, str, str]:
    parts = value.split(",", 2)
    if len(parts) not in (2, 3) or not all(parts):
        raise argparse.ArgumentTypeError("target deve essere adapter_id,url[,served_model_name]")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else ""


def _load_samples(path: Path, limit: int | None) -> list[dict]:
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        content = next(
            (m.get("content", "") for m in row.get("messages", []) if m.get("role") == "assistant"),
            "",
        )
        images = row.get("images") or []
        if not content or not images:
            continue
        samples.append({"image": Path(images[0]), "reference": str(content)})
        if limit and len(samples) >= limit:
            break
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--target", type=_target, action="append", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.dataset.is_file():
        parser.error(f"dataset non trovato: {args.dataset}")
    samples = _load_samples(args.dataset, args.limit)
    if not samples:
        parser.error("dataset gold senza campioni utilizzabili")
    missing = [str(s["image"]) for s in samples if not s["image"].is_file()]
    if missing:
        parser.error(f"immagini gold non trovate: {missing[0]}")

    report_path = args.output or Path("data/benchmarks") / (
        f"labeled_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    ) / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "tabularium-vlm-labeled-benchmark-v1",
        "dataset": str(args.dataset),
        "samples": len(samples),
        "task": "text",
        "results": [],
    }

    for adapter_id, url, model in args.target:
        adapter = model_adapters.get_adapter(adapter_id)
        model = model or getattr(adapter.capabilities, "served_model_name", None) or adapter_id
        client = inference.VllmClient(
            url=url,
            model=model,
            adapter=adapter,
            timeout=max(5, int(args.timeout)),
            max_retries=0,
        )
        runs = []
        for index, sample in enumerate(samples, 1):
            started = time.perf_counter()
            reference = sample["reference"]
            entry = {"adapter_id": adapter_id, "model": model, "image": str(sample["image"]), "sample": index}
            try:
                with Image.open(sample["image"]) as source:
                    hypothesis = client.recognize(source.convert("RGB"), "Text", total_timeout=args.timeout)
                entry.update({
                    "ok": bool(hypothesis.strip()),
                    "reference": reference,
                    "hypothesis": hypothesis,
                    "cer": evaluate.cer(reference, hypothesis),
                    "wer": evaluate.wer(reference, hypothesis),
                    "trace": dict(client.last_trace),
                    "wall_s": round(time.perf_counter() - started, 3),
                })
            except Exception as exc:  # noqa: BLE001
                entry.update({"ok": False, "error": str(exc), "trace": dict(client.last_trace),
                              "wall_s": round(time.perf_counter() - started, 3)})
            raw_dir = report_path.parent / "outputs" / adapter_id
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / f"text-{index:03d}.json"
            raw_path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            entry["output_file"] = str(raw_path)
            runs.append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        valid = [r for r in runs if r.get("ok")]
        report["results"].append({
            "adapter_id": adapter_id,
            "model": model,
            "runs": runs,
            "aggregate": {
                "ok": len(valid),
                "failed": len(runs) - len(valid),
                "mean_cer": statistics.mean(r["cer"] for r in valid) if valid else None,
                "mean_wer": statistics.mean(r["wer"] for r in valid) if valid else None,
                "mean_wall_s": statistics.mean(r["wall_s"] for r in valid) if valid else None,
            },
        })
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"labeled benchmark report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
