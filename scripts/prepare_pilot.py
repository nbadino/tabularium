"""Prepara un pilot riproducibile senza avviare training o modificare le scansioni."""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", type=int)
    parser.add_argument("--target", type=int, default=40, choices=range(30, 51))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--url", default=os.environ.get("TABULARIUM_E2E_URL", "http://127.0.0.1:8787"))
    args = parser.parse_args()
    base = args.url.rstrip("/")
    try:
        sample = requests.get(f"{base}/api/projects/{args.project_id}/pilot-sample", params={"target": args.target, "seed": args.seed}, timeout=20)
        sample.raise_for_status()
        pages = sample.json().get("pages", [])
        if len(pages) < 30:
            raise RuntimeError(f"campione insufficiente: {len(pages)} pagine disponibili (servono almeno 30)")
        saved = requests.post(f"{base}/api/projects/{args.project_id}/pilot-sample/save", json=[p["id"] for p in pages], timeout=20)
        saved.raise_for_status()
        built = requests.post(
            f"{base}/api/projects/{args.project_id}/datasets/build",
            json={"split_ratio": 0.9, "seed": args.seed, "split_strategy": "issue", "approved_only": True, "pilot_only": True},
            timeout=120,
        )
        built.raise_for_status()
        preflight = requests.post(f"{base}/api/projects/{args.project_id}/training/preflight", json={"train_type": "lora"}, timeout=30)
        preflight.raise_for_status()
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"pilot preparation failed: {exc}", file=sys.stderr)
        return 1
    report = built.json()
    print(json.dumps({"sample": sample.json(), "saved": saved.json(), "dataset": report, "preflight": preflight.json()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
