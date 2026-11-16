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


def eligible_page_status(project_id: int) -> dict[int, str]:
    """{page_id: status} of the pages `pages_with_blocks` would return, cheaply.

    Same rule: not missing, at least one block, and on an approved page an
    unconfirmed model draft does not count.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.status FROM pages p WHERE p.project_id=? AND p.status != 'missing' "
            "AND EXISTS (SELECT 1 FROM blocks b WHERE b.page_id=p.id AND NOT ("
            "p.status IN ('approved','exported') AND b.prefill_source IS NOT NULL AND b.confirmed=0))",
            (project_id,),
        ).fetchall()
    return {int(row["id"]): str(row["status"]) for row in rows}


def page_changes_since(project_id: int, since: str) -> dict[int, str]:
    """{page_id: latest change} for pages approved, reviewed or edited after `since`.

    `since` and the result are UTC `YYYY-MM-DD HH:MM:SS`, SQLite's `datetime('now')`.
    A change is a block or grid save, a review-workflow step (`page_work`,
    `page_reviews`) or a page-level audit event (a direct approval).
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT page_id, MAX(at) AS at FROM (
                SELECT b.page_id AS page_id, b.updated_at AS at FROM blocks b
                UNION ALL SELECT b.page_id, t.updated_at FROM tables t JOIN blocks b ON b.id=t.block_id
                UNION ALL SELECT page_id, updated_at FROM page_work
                UNION ALL SELECT page_id, created_at FROM page_reviews
                UNION ALL SELECT CAST(resource_id AS INTEGER), created_at FROM audit_events
                    WHERE resource_type='page'
            ) changes
            WHERE at > ? AND page_id IN (SELECT id FROM pages WHERE project_id=?)
            GROUP BY page_id
            """,
            (since, project_id),
        ).fetchall()
    return {int(row["page_id"]): str(row["at"]) for row in rows}
