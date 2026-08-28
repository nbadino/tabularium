#!/usr/bin/env python3
"""Confronta i motori geometrici sul corpus senza modificare Lloyds Lab.

Le misure OCR sono proxy (copertura e confidenza), non sostituiscono CER su
trascrizioni gold. Output e report restano nella directory indicata.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.services import dewarp  # noqa: E402
from app.services.ocr import OcrEngine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark lossless di deskew/dewarp")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=Path("dewarp-benchmark"))
    parser.add_argument(
        "--engines",
        nargs="+",
        choices=("raw", "deskew", "uvdoc", "docscanner"),
        default=("raw", "deskew", "uvdoc", "docscanner"),
    )
    parser.add_argument("--ocr", choices=("rapidocr", "paddleocr"), default="rapidocr")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ocr = OcrEngine(args.ocr)
    report = {"created_at": datetime.now(UTC).isoformat(), "items": []}
    for source in args.images:
        original = Image.open(source).convert("RGB")
        for engine in args.engines:
            if engine == "raw":
                image = original
                actual, angle, warnings, error = "raw", 0.0, [], None
            else:
                result = dewarp.run_transform(original, engine)
                image = result.image
                actual, angle = result.actual_engine, result.angle
                warnings, error = result.warnings, result.error
            destination = args.output / f"{source.stem}__{engine}.png"
            image.save(destination, "PNG", compress_level=4)
            detections = ocr.detect(image)
            scores = [float(item["score"]) for item in detections]
            report["items"].append(
                {
                    "source": str(source.resolve()),
                    "requested_engine": engine,
                    "actual_engine": actual,
                    "angle": angle,
                    "warnings": warnings,
                    "error": error,
                    "output": str(destination.resolve()),
                    "ocr": {
                        "engine": args.ocr,
                        "lines": len(detections),
                        "characters": sum(len(item["text"]) for item in detections),
                        "mean_confidence": sum(scores) / len(scores) if scores else 0.0,
                        "confidence_ge_0_7": sum(score >= 0.7 for score in scores),
                    },
                }
            )
            print(source.name, engine, actual, len(detections), flush=True)
    path = args.output / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
