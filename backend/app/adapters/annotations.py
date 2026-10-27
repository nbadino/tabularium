"""Reading and writing the annotation tables (P7 of `docs/architecture/program.md`).

The canvas autosaves on every change and the table grids hang off `blocks.id` with
`ON DELETE CASCADE`, so the order of these statements is part of the product: a block
updated in place keeps its grid, a block deleted takes it. The adapter owns the
statements and the transaction; `services/blocks.py` owns the decisions (which drafts
survive an autosave, when a conflict is a 409).
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from ..db import connect


class AnnotationWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- reads ---------------------------------------------------------
    def page(self, page_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        return dict(row) if row is not None else None

    def blocks(self, page_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM blocks WHERE page_id=? ORDER BY COALESCE(order_idx, 2147483647), id",
            (page_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def identities(self, page_id: int) -> list[dict[str, Any]]:
        """Every block id with the two flags the autosave reasons about."""
        rows = self.conn.execute(
            "SELECT id, prefill_source, confirmed FROM blocks WHERE page_id=?", (page_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def block(self, block_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM blocks WHERE id=?", (block_id,)).fetchone()
        return dict(row) if row is not None else None

    def count_blocks(self, page_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM blocks WHERE page_id=?", (page_id,)
        ).fetchone()
        return int(row[0]) if row is not None else 0

    def table(self, block_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM tables WHERE block_id=?", (block_id,)).fetchone()
        return dict(row) if row is not None else None

    def revision(self, page_id: int) -> int:
        row = self.conn.execute(
            "SELECT annotation_revision FROM pages WHERE id=?", (page_id,)
        ).fetchone()
        return int(row[0]) if row is not None else 0

    # --- writes --------------------------------------------------------
    def bump_revision(self, page_id: int, expected: int | None = None) -> int:
        """Increment the revision, optionally only if it still is `expected`.

        Returns the number of rows changed: 0 means another writer got there first.
        """
        if expected is None:
            self.conn.execute(
                "UPDATE pages SET annotation_revision=annotation_revision+1 WHERE id=?", (page_id,)
            )
            return 1
        return self.conn.execute(
            "UPDATE pages SET annotation_revision=annotation_revision+1 "
            "WHERE id=? AND annotation_revision=?",
            (page_id, expected),
        ).rowcount

    def update_block(
        self,
        block_id: int,
        *,
        label: str,
        kind: str,
        points: list,
        content: str,
        order_idx: int | None,
        confirmed: bool,
    ) -> None:
        self.conn.execute(
            "UPDATE blocks SET label=?, kind=?, points=?, content=?, "
            "order_idx=?, confirmed=?, updated_at=datetime('now') WHERE id=?",
            (label, kind, json.dumps(points), content, order_idx, 1 if confirmed else 0, block_id),
        )

    def insert_block(
        self,
        page_id: int,
        *,
        label: str,
        kind: str,
        points: list,
        content: str,
        order_idx: int | None,
        confirmed: bool,
    ) -> int:
        cursor = self.conn.execute(
            "INSERT INTO blocks (page_id, label, kind, points, content, order_idx, confirmed) "
            "VALUES (?,?,?,?,?,?,?)",
            (page_id, label, kind, json.dumps(points), content, order_idx, 1 if confirmed else 0),
        )
        return int(cursor.lastrowid or 0)

    def delete_blocks(self, ids: list[int]) -> None:
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM blocks WHERE id IN ({placeholders})", ids)

    def update_fields(self, block_id: int, updates: dict[str, Any]) -> None:
        """Patch the fields the caller already encoded (points as JSON, confirmed as 0/1)."""
        assignments = ", ".join(f"{key}=?" for key in updates)
        self.conn.execute(
            f"UPDATE blocks SET {assignments}, updated_at=datetime('now') WHERE id=?",
            (*updates.values(), block_id),
        )

    def delete_page_blocks(self, page_id: int) -> int:
        """Delete every block of a page (grids follow by cascade); returns how many."""
        count = self.count_blocks(page_id)
        self.conn.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
        return count

    def upsert_table(self, block_id: int, grid: dict) -> None:
        self.conn.execute(
            "INSERT INTO tables (block_id, grid_json) VALUES (?, ?) "
            "ON CONFLICT(block_id) DO UPDATE SET "
            "grid_json=excluded.grid_json, updated_at=datetime('now')",
            (block_id, json.dumps(grid)),
        )

    def delete_block(self, block_id: int) -> None:
        self.conn.execute("DELETE FROM blocks WHERE id=?", (block_id,))

    def mark_new(self, page_id: int) -> None:
        self.conn.execute("UPDATE pages SET status='new' WHERE id=?", (page_id,))

    def mark_annotated(self, page_id: int) -> None:
        self.conn.execute(
            "UPDATE pages SET status='annotated' WHERE id=? AND status='new'", (page_id,)
        )

    def commit(self) -> None:
        self.conn.commit()


@contextmanager
def writing() -> Iterator[AnnotationWriter]:
    with connect() as conn:
        yield AnnotationWriter(conn)
