#!/usr/bin/env python3
"""Benchmark non distruttivo END2END su pagine già registrate.

Non scrive annotazioni, blocchi o griglie. Serve a confrontare risoluzioni e
limiti di output prima di rendere una modalità il default del prefill.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app import config
from app.db import connect, init_db
from app.services import inference, otsl, pages


def _page_rows(project_id: int, ids: list[int] | None):
    with connect() as conn:
        if ids:
            marks = ",".join("?" for _ in ids)
            return conn.execute(
                f"SELECT * FROM pages WHERE project_id=? AND id IN ({marks}) ORDER BY id",
                [project_id, *ids],
            ).fetchall()
        return conn.execute(
            "SELECT * FROM pages WHERE project_id=? ORDER BY id", (project_id,)
        ).fetchall()


def _table_shape(item: dict) -> list[int] | None:
    if item.get("label") != "Table" or not item.get("content"):
        return None
    try:
        content = item["content"]
        content = (
            otsl.html_to_otsl(content)
            if "<table" in content.lower()
            else content
        )
        grid = otsl.otsl_to_grid(content)
    except Exception:  # noqa: BLE001
        return None
    return [grid.get("rows", 0), grid.get("cols", 0)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", type=int)
    parser.add_argument("page_ids", nargs="*", type=int)
    parser.add_argument(
        "--max-pixels",
        nargs="+",
        type=int,
        default=[2_000_000],
        help="tetti da confrontare (default: 2000000)",
    )
    parser.add_argument("--max-tokens", type=int, default=inference.END2END_MAX_TOKENS)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    init_db()
    rows = _page_rows(args.project_id, args.page_ids or None)
    if not rows:
        parser.error("nessuna pagina trovata nel progetto")
    client = inference.VllmClient(timeout=min(30, max(5, int(args.timeout))))
    report = {
        "project_id": args.project_id,
        "model": client.model,
        "url": client.url,
        "max_tokens": args.max_tokens,
        "results": [],
    }
    for page in rows:
        image = pages.load_source_image(page)
        if image is None:
            report["results"].append({"page_id": page["id"], "error": "immagine non disponibile"})
            continue
        for max_pixels in args.max_pixels:
            started = time.perf_counter()
            entry = {
                "page_id": page["id"],
                "path": page["rel_path"],
                "source_pixels": image.width * image.height,
                "max_pixels": max_pixels,
            }
            try:
                items = client.end2end(
                    image,
                    max_tokens=args.max_tokens,
                    max_pixels=max_pixels,
                    total_timeout=args.timeout,
                )
                entry["items"] = [
                    {
                        "label": item["label"],
                        "bbox": item["bbox"],
                        "content_chars": len(item.get("content") or ""),
                        "content_head": (item.get("content") or "")[:500]
                        if item["label"] == "Table"
                        else None,
                        "table_shape": _table_shape(item),
                    }
                    for item in items
                ]
                entry["trace"] = dict(client.last_trace)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
                entry["trace"] = dict(client.last_trace)
                entry["content_chars"] = len(client.last_text)
                entry["content_head"] = client.last_text[:500]
            entry["wall_s"] = round(time.perf_counter() - started, 3)
            report["results"].append(entry)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
