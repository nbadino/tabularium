"""Reading the annotated corpus for export: pages, their blocks, their grids.

Phase P3b of `docs/architecture/program.md`. The builder decides which pages belong to
which family, how the split is made and what a report says; this module answers "what is
annotated". The difference matters because the second is what changes when the schema
does, and because a dataset built from a wrong read is wrong in a way nobody notices
until a model has learned it.
"""
from __future__ import annotations

import json
from typing import Any

from ..db import connect


def pages_with_blocks(project_id: int) -> dict[int, dict[str, Any]]:
    """Pages with at least one block: {page_id: {"page", "blocks", "tables"}}.

    `tables` is `{block_id: grid}` as stored; the caller decides what provenance to
    stamp on the cells.
    """
    page_data: dict[int, dict[str, Any]] = {}
    with connect() as conn:
        pages = conn.execute(
            "SELECT * FROM pages WHERE project_id=? AND status != 'missing' ORDER BY id",
            (project_id,),
        ).fetchall()
        for page in pages:
            # On an approved page the truth is what a person approved: a model draft
            # that arrived later (a recognition re-run) is not confirmed and is not
            # part of it. Otherwise evaluation and the dataset would take it for gold,
            # and the model would be measured and trained on its own output.
            gold = page["status"] in {"approved", "exported"}
            blocks = conn.execute(
                "SELECT * FROM blocks WHERE page_id=? "
                + ("AND NOT (prefill_source IS NOT NULL AND confirmed=0) " if gold else "")
                + "ORDER BY COALESCE(order_idx, 2147483647), id",
                (page["id"],),
            ).fetchall()
            if not blocks:
                continue
            placeholders = ",".join("?" for _ in blocks)
            tables: dict[int, dict[str, Any]] = {}
            for row in conn.execute(
                "SELECT * FROM tables WHERE block_id IN (" + placeholders + ")",
                [block["id"] for block in blocks],
            ).fetchall():
                try:
                    tables[row["block_id"]] = json.loads(row["grid_json"] or "{}")
                except (TypeError, ValueError):
                    tables[row["block_id"]] = {}
            page_data[page["id"]] = {
                "page": dict(page),
                "blocks": [dict(block) for block in blocks],
                "tables": tables,
            }
    return page_data


def page_count(project_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE project_id=? AND status != 'missing'",
            (project_id,),
        ).fetchone()
    return int(row["n"]) if row is not None else 0
