"""Page lookups shared by the HTTP layer (P7.1 of `docs/architecture/program.md`).

The API needs to know which project a page or a block belongs to before it can
authorize the request; that is a read, and it belongs here rather than in a router.
"""
from __future__ import annotations

import json
from typing import Any

from ..db import connect


def get(page_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    return dict(row) if row is not None else None


def list_for_project(
    project_id: int,
    *,
    page_type: str | None = None,
    status: str | None = None,
    limit: int = 10000,
) -> tuple[list[dict[str, Any]], int]:
    """A project's pages with their block/draft counts, and the total before the limit."""
    where = "project_id=?"
    params: list = [project_id]
    if page_type:
        where += " AND page_type=?"
        params.append(page_type)
    if status:
        where += " AND status=?"
        params.append(status)
    else:
        # A new scan marks removed sources as missing: they keep their annotations
        # and results but do not appear in the active work.
        where += " AND status != 'missing'"
    with connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM pages WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT pages.*, COALESCE(b.blocks, 0) AS blocks, "
            "COALESCE(b.drafts, 0) AS drafts FROM pages "
            "LEFT JOIN (SELECT page_id, COUNT(*) AS blocks, "
            "SUM(CASE WHEN confirmed=0 AND prefill_source IS NOT NULL THEN 1 ELSE 0 END) "
            "AS drafts FROM blocks GROUP BY page_id) b ON b.page_id = pages.id "
            f"WHERE pages.{where} ORDER BY rel_path, pdf_page LIMIT ?",
            (*params, limit),
        ).fetchall()
    return [dict(row) for row in rows], int(total)


def sum_failures_inputs(project_id: int) -> list[tuple]:
    """`(page_id, block_id, grid_json)` for every table of the project."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT b.page_id, t.block_id, t.grid_json FROM tables t "
            "JOIN blocks b ON b.id = t.block_id JOIN pages p ON p.id = b.page_id "
            "WHERE p.project_id = ?",
            (project_id,),
        ).fetchall()
    return [tuple(row) for row in rows]


def update_fields(page_id: int, updates: dict[str, Any]) -> None:
    assignments = ", ".join(f"{key}=?" for key in updates)
    with connect() as conn:
        conn.execute(
            f"UPDATE pages SET {assignments} WHERE id=?", (*updates.values(), page_id)
        )


def project_of_page(page_id: int) -> int | None:
    with connect() as conn:
        row = conn.execute("SELECT project_id FROM pages WHERE id=?", (page_id,)).fetchone()
    return int(row["project_id"]) if row is not None else None


def project_of_block(block_id: int) -> int | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT p.project_id FROM blocks b JOIN pages p ON p.id=b.page_id WHERE b.id=?",
            (block_id,),
        ).fetchone()
    return int(row["project_id"]) if row is not None else None


def reviews(page_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM page_reviews WHERE page_id=? ORDER BY id DESC", (page_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def add_review(
    page_id: int, *, reviewer: str, status: str, errors: list, notes: str | None
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO page_reviews (page_id, reviewer, status, errors_json, notes) "
            "VALUES (?,?,?,?,?)",
            (page_id, reviewer, status, json.dumps(errors, ensure_ascii=False), notes),
        )
        if status == "pass":
            conn.execute(
                "UPDATE pages SET status='qa' WHERE id=? AND status IN ('annotated','review')",
                (page_id,),
            )
        elif status == "fail":
            conn.execute(
                "UPDATE pages SET status='review' WHERE id=? AND status != 'approved'",
                (page_id,),
            )
        return int(cursor.lastrowid or 0)


def all_for_project(project_id: int) -> list[dict[str, Any]]:
    """Every page of a project, in reading order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pages WHERE project_id=? ORDER BY rel_path, pdf_page",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def existing_paths(project_id: int) -> list[dict[str, Any]]:
    """Pages that are still available, with their source path (for the missing check)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, abs_path FROM pages WHERE project_id=? AND status != 'missing'",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_missing(page_ids: list[int], conn=None) -> int:
    if not page_ids:
        return 0
    statement = "UPDATE pages SET status='missing' WHERE id=?"
    if conn is not None:
        conn.executemany(statement, ((page_id,) for page_id in page_ids))
        return len(page_ids)
    with connect() as own:
        own.executemany(statement, ((page_id,) for page_id in page_ids))
    return len(page_ids)


def insert_image_page(
    conn,
    *,
    project_id: int,
    rel_path: str,
    abs_path: str,
    width: int,
    height: int,
    issue_no: str | None,
    page_no: str | None,
    page_type: str | None,
    meta_json: str,
) -> int | None:
    """Insert an image page; `None` when the path is already there.

    An existing row whose status is missing is reactivated instead: a file restored
    after a mistaken deletion gets its annotations back, which is the whole reason the
    missing status exists.
    """
    cursor = conn.execute(
        "INSERT OR IGNORE INTO pages "
        "(project_id, rel_path, abs_path, source_kind, width, height, "
        "issue_no, page_no, page_type, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (project_id, rel_path, abs_path, "image", width, height, issue_no, page_no, page_type, meta_json),
    )
    if cursor.rowcount == 0:
        conn.execute(
            "UPDATE pages SET status='new' WHERE project_id=? AND abs_path=? AND status='missing'",
            (project_id, abs_path),
        )
        return None
    return int(cursor.lastrowid or 0)


def insert_pdf_page(
    conn,
    *,
    project_id: int,
    rel_path: str,
    abs_path: str,
    pdf_page: int,
    width: int,
    height: int,
    issue_no: str | None,
    page_no: str | None,
    page_type: str | None,
    meta_json: str,
) -> int | None:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO pages "
        "(project_id, rel_path, abs_path, source_kind, pdf_page, width, height, "
        "issue_no, page_no, page_type, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, rel_path, abs_path, "pdf", pdf_page, width, height, issue_no, page_no, page_type, meta_json),
    )
    if cursor.rowcount == 0:
        conn.execute(
            "UPDATE pages SET status='new' WHERE project_id=? AND abs_path=? "
            "AND pdf_page=? AND status='missing'",
            (project_id, abs_path, pdf_page),
        )
        return None
    return int(cursor.lastrowid or 0)


def set_thumb(conn, page_id: int, thumb: str) -> None:
    conn.execute("UPDATE pages SET thumb_path=? WHERE id=?", (str(thumb), page_id))


def set_meta(conn, page_id: int, meta_json: str) -> None:
    conn.execute("UPDATE pages SET meta_json=? WHERE id=?", (meta_json, page_id))


def ordered_blocks(page_id: int, conn=None) -> list[dict[str, Any]]:
    statement = "SELECT * FROM blocks WHERE page_id=? ORDER BY id"
    if conn is not None:
        rows = conn.execute(statement, (page_id,)).fetchall()
    else:
        with connect() as own:
            rows = own.execute(statement, (page_id,)).fetchall()
    return [dict(row) for row in rows]


def confirm_blocks(page_id: int, conn) -> int:
    """Accept every unconfirmed block of the page; returns how many."""
    return conn.execute(
        "UPDATE blocks SET confirmed=1 WHERE page_id=? AND confirmed=0", (page_id,)
    ).rowcount


def confirmed_tables(page_id: int, conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT b.id, b.prefill_source, t.grid_json FROM blocks b "
        "JOIN tables t ON t.block_id=b.id "
        "WHERE b.page_id=? AND b.label='Table' AND b.confirmed=1",
        (page_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def table_grid(block_id: int, conn=None) -> str | None:
    statement = "SELECT grid_json FROM tables WHERE block_id=?"
    if conn is not None:
        row = conn.execute(statement, (block_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (block_id,)).fetchone()
    return row["grid_json"] if row is not None else None


def set_table_grid(block_id: int, grid_json: str, conn) -> None:
    conn.execute(
        "UPDATE tables SET grid_json=?, updated_at=datetime('now') WHERE block_id=?",
        (grid_json, block_id),
    )


def bump_revision(page_id: int, conn) -> int:
    conn.execute(
        "UPDATE pages SET annotation_revision=annotation_revision+1 WHERE id=?", (page_id,)
    )
    return revision(page_id, conn)


def revision(page_id: int, conn=None) -> int:
    statement = "SELECT annotation_revision FROM pages WHERE id=?"
    if conn is not None:
        row = conn.execute(statement, (page_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (page_id,)).fetchone()
    return int(row[0]) if row is not None else 0


def has_passing_review(page_id: int, conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM page_reviews WHERE page_id=? AND status='pass' LIMIT 1", (page_id,)
    ).fetchone() is not None


def set_status(page_id: int, status: str, conn) -> None:
    conn.execute("UPDATE pages SET status=? WHERE id=?", (status, page_id))


def ids_of_project(project_id: int, conn) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM pages WHERE project_id=? AND status != 'missing' ORDER BY id",
        (project_id,),
    ).fetchall()
    return [int(row["id"]) for row in rows]

