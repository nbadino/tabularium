"""Reading and writing the annotation tables, for the prefill generators.

The generators commit after every block on purpose: the event they emit says "what the
UI shows already exists on the server", and an uncommitted row is invisible to the other
connections. That is a transaction decision, so it belongs with the queries, not in the
service that decides which block to keep.

`writing()` is a small unit of work: the caller keeps the scope (one page) and the order
of operations, the adapter owns the SQL.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from ..db import connect


class PageWriter:
    """One write scope: a page being prefilled."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- reads ---------------------------------------------------------
    def page(self, page_id: int, project_id: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pages WHERE id=? AND project_id=?", (page_id, project_id)
        ).fetchone()
        return dict(row) if row is not None else None

    # --- writes --------------------------------------------------------
    def replace_blocks(self, page_id: int, mode: str) -> dict[str, int]:
        """Delete the blocks the chosen mode allows, and say how many went."""
        if mode == "merge":
            return {"blocks": 0, "grids": 0}
        if mode == "replace_drafts":
            # Only prefill drafts: human work (hand-drawn or confirmed blocks) is
            # never a draft, whatever its source.
            rows = self._conn.execute(
                "SELECT id FROM blocks WHERE page_id=? AND prefill_source IS NOT NULL "
                "AND confirmed=0",
                (page_id,),
            ).fetchall()
        else:  # replace / replace_all
            rows = self._conn.execute(
                "SELECT id FROM blocks WHERE page_id=?", (page_id,)
            ).fetchall()
        ids = [row["id"] for row in rows]
        if not ids:
            return {"blocks": 0, "grids": 0}
        placeholders = ",".join("?" for _ in ids)
        grids = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM tables WHERE block_id IN ({placeholders})", ids
        ).fetchone()["n"]
        self._conn.execute(f"DELETE FROM blocks WHERE id IN ({placeholders})", ids)
        return {"blocks": len(ids), "grids": int(grids or 0)}

    def insert_block(
        self,
        page_id: int,
        *,
        label: str,
        points: list[list[float]],
        content: str,
        order_idx: int,
        prefill_source: str,
        recognition_run_id: int | None = None,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO blocks (page_id, label, kind, points, content, order_idx, "
            "prefill_source, confirmed, recognition_run_id) VALUES (?,?,?,?,?,?,?,0,?)",
            (
                page_id,
                label,
                "rect",
                json.dumps(points),
                content,
                order_idx,
                prefill_source,
                recognition_run_id,
            ),
        )
        return int(cursor.lastrowid or 0)

    def upsert_grid(self, block_id: int, grid: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
            "ON CONFLICT(block_id) DO UPDATE SET grid_json=excluded.grid_json, "
            "updated_at=datetime('now')",
            (block_id, json.dumps(grid)),
        )

    def bump_revision(self, page_id: int) -> int:
        """Advance the page revision after a prefill wrote to it, and return it.

        Without this, an approved page kept its approval (the trigger watches the
        revision) and another editor's autosave could overwrite the drafts unawares.
        """
        self._conn.execute(
            "UPDATE pages SET annotation_revision=annotation_revision+1 WHERE id=?", (page_id,)
        )
        row = self._conn.execute("SELECT annotation_revision FROM pages WHERE id=?", (page_id,)).fetchone()
        self._conn.commit()
        return int(row[0]) if row is not None else 0

    def commit(self) -> None:
        """Make the rows so far visible to the other connections."""
        self._conn.commit()


@contextmanager
def writing() -> Iterator[PageWriter]:
    with connect() as conn:
        yield PageWriter(conn)
