"""Who annotates and who reviews a page: the `page_work` table (P7.1).

The handoff is the most stateful part of the product — annotation, submission, review,
changes requested — and every transition touches two tables in one transaction. The
service decides *when* a transition is allowed (version match, role, readiness); this
module owns the rows and the pair of statements each transition needs.
"""
from __future__ import annotations

from typing import Any

from ..db import connect


def project_owner(project_id: int, conn=None) -> int | None:
    statement = "SELECT owner_id FROM projects WHERE id=?"
    if conn is not None:
        row = conn.execute(statement, (project_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (project_id,)).fetchone()
    return int(row["owner_id"]) if row is not None and row["owner_id"] is not None else None


def candidates(project_id: int, conn=None) -> list[dict[str, Any]]:
    """Active accounts that may take part: admins, the owner, editors of the project."""
    statement = (
        "SELECT DISTINCT u.id, u.username FROM users u "
        "LEFT JOIN project_members m ON m.user_id=u.id AND m.project_id=? "
        "JOIN projects p ON p.id=? "
        "WHERE u.active=1 AND (u.role='admin' OR u.id=p.owner_id OR m.role='editor') "
        "ORDER BY u.username"
    )
    if conn is not None:
        rows = conn.execute(statement, (project_id, project_id)).fetchall()
    else:
        with connect() as own:
            rows = own.execute(statement, (project_id, project_id)).fetchall()
    return [dict(row) for row in rows]


def work_rows(project_id: int, *, page_id: int | None = None, conn=None) -> list[dict[str, Any]]:
    statement = (
        "SELECT w.*, a.username AS assignee_name, r.username AS reviewer_name, "
        "p.annotation_revision FROM page_work w JOIN pages p ON p.id=w.page_id "
        "LEFT JOIN users a ON a.id=w.assignee_id LEFT JOIN users r ON r.id=w.reviewer_id "
        "WHERE p.project_id=?"
    )
    params: list[Any] = [project_id]
    if page_id is not None:
        statement += " AND w.page_id=?"
        params.append(page_id)
    if conn is not None:
        rows = conn.execute(statement, params).fetchall()
    else:
        with connect() as own:
            rows = own.execute(statement, params).fetchall()
    return [dict(row) for row in rows]


def page(page_id: int, conn=None) -> dict[str, Any] | None:
    statement = "SELECT * FROM pages WHERE id=?"
    if conn is not None:
        row = conn.execute(statement, (page_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (page_id,)).fetchone()
    return dict(row) if row is not None else None


def roles(page_id: int, conn=None) -> dict[str, Any] | None:
    """The two people named on a page, if the page has a work row."""
    statement = "SELECT assignee_id, reviewer_id FROM page_work WHERE page_id=?"
    if conn is not None:
        row = conn.execute(statement, (page_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (page_id,)).fetchone()
    return dict(row) if row is not None else None


def upsert(conn, page_id: int, assignee_id: int | None, reviewer_id: int | None) -> None:
    """(Re)assign the page, resetting the handoff state to annotation."""
    conn.execute(
        "INSERT INTO page_work(page_id,assignee_id,reviewer_id) VALUES(?,?,?) "
        "ON CONFLICT(page_id) DO UPDATE SET assignee_id=excluded.assignee_id, "
        "reviewer_id=excluded.reviewer_id, state='annotation', version=page_work.version+1, "
        "submitted_by=NULL, submitted_revision=NULL, approved_revision=NULL, note='', "
        "updated_at=datetime('now')",
        (page_id, assignee_id, reviewer_id),
    )


def reopen_if_settled(conn, page_id: int) -> None:
    """A reassignment on an approved page sends it back to annotated."""
    conn.execute(
        "UPDATE pages SET status='annotated' WHERE id=? "
        "AND status IN ('qa','approved','exported')",
        (page_id,),
    )


def mark_pending(conn, page_id: int, *, submitted_by: int | None, revision: int) -> None:
    conn.execute(
        "UPDATE page_work SET state='pending', submitted_by=?, submitted_revision=?, "
        "approved_revision=NULL, version=version+1, note='', updated_at=datetime('now') "
        "WHERE page_id=?",
        (submitted_by, revision, page_id),
    )
    conn.execute("UPDATE pages SET status='qa' WHERE id=?", (page_id,))


def add_review(conn, page_id: int, *, reviewer: str, passed: bool, note: str) -> None:
    conn.execute(
        "INSERT INTO page_reviews(page_id,reviewer,status,notes) VALUES(?,?,?,?)",
        (page_id, reviewer, "pass" if passed else "fail", note),
    )


def settle_review(
    conn,
    page_id: int,
    *,
    state: str,
    note: str,
    approved_revision: int | None,
) -> None:
    """Close the review and move the page: approved, or back to review."""
    conn.execute(
        "UPDATE page_work SET state=?, note=?, approved_revision=?, version=version+1, "
        "updated_at=datetime('now') WHERE page_id=?",
        (state, note, approved_revision, page_id),
    )
    conn.execute(
        "UPDATE pages SET status=? WHERE id=?",
        ("approved" if state == "approved" else "review", page_id),
    )
