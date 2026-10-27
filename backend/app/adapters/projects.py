"""Project-level reads that more than one service needs."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator
from typing import Any

from ..db import connect


def get(project_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row is not None else None


def settings_json(project_id: int) -> dict[str, Any]:
    """The project's `settings_json`, decoded. Empty dict when the project is gone.

    A database that does not exist yet (a utility context, a test that evaluates
    without a project) also yields an empty dict: the caller is asking for project
    preferences, and "none" is a valid answer.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT settings_json FROM projects WHERE id=?", (project_id,)
            ).fetchone()
    except sqlite3.OperationalError:
        return {}
    if row is None:
        return {}
    try:
        value = json.loads(row["settings_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def settings_json_or_none(project_id: int) -> dict[str, Any] | None:
    """Like `settings_json`, but `None` when the project does not exist.

    Callers that must answer 404 need the difference; callers that only want the
    preferences should not.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT settings_json FROM projects WHERE id=?", (project_id,)
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        value = json.loads(row["settings_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def put_settings(project_id: int, settings: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE projects SET settings_json=? WHERE id=?",
            (json.dumps(settings, ensure_ascii=False), project_id),
        )


class MemberWriter:
    """Project membership and ownership: one transaction, statements only."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def members(self, project_id: int, owner_id: int | None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT u.id AS user_id, u.username, u.email, u.active, 'owner' AS role
               FROM users u WHERE u.id=?
               UNION ALL
               SELECT u.id, u.username, u.email, u.active, pm.role
               FROM project_members pm JOIN users u ON u.id=pm.user_id
               WHERE pm.project_id=? AND u.id != COALESCE(?, -1)
               ORDER BY role, username""",
            (owner_id, project_id, owner_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def candidates(self, project_id: int, owner_id: int | None) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id AS user_id, username, email, active, 'viewer' AS role FROM users "
            "WHERE active=1 AND id != ? AND id NOT IN "
            "(SELECT user_id FROM project_members WHERE project_id=?) ORDER BY username",
            (owner_id, project_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def user(self, user_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT id, username, email, active FROM users WHERE id=?", (user_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def put_member(self, project_id: int, user_id: int, role: str) -> None:
        self.conn.execute(
            "INSERT INTO project_members(project_id,user_id,role) VALUES(?,?,?) "
            "ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role",
            (project_id, user_id, role),
        )

    def delete_member(self, project_id: int, user_id: int) -> int:
        return self.conn.execute(
            "DELETE FROM project_members WHERE project_id=? AND user_id=?",
            (project_id, user_id),
        ).rowcount

    def set_owner(self, project_id: int, user_id: int) -> None:
        self.conn.execute(
            "UPDATE projects SET owner_id=? WHERE id=?", (user_id, project_id)
        )


@contextmanager
def writing() -> Iterator[MemberWriter]:
    with connect() as conn:
        yield MemberWriter(conn)


def list_for_user(user_id: int | None, *, privileged: bool) -> list[dict[str, Any]]:
    """Every project the caller may see, newest first.

    `privileged` covers the local and admin modes, which see everything. The
    membership rule is the same as before: owner or listed member.
    """
    with connect() as conn:
        if privileged:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT p.* FROM projects p "
                "LEFT JOIN project_members pm ON pm.project_id = p.id "
                "WHERE p.owner_id = ? OR pm.user_id = ? "
                "ORDER BY p.created_at DESC",
                (user_id, user_id),
            ).fetchall()
    return [dict(row) for row in rows]


def create(
    name: str, *, root_dir: str, archive_dir: str | None, owner_id: int | None
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO projects (name, root_dir, archive_dir, settings_json, owner_id) "
            "VALUES (?, ?, ?, '{}', ?)",
            (name, root_dir, archive_dir, owner_id),
        )
        return int(cursor.lastrowid or 0)


def summaries(project_ids: list[int], user_id: int | None) -> dict[int, dict[str, Any]]:
    """Counts and role for a page of projects, in one statement instead of four each.

    The list used to run four queries per project (pages, membership, owner, active
    members): a screen with thirty projects meant a hundred and twenty round trips.
    """
    if not project_ids:
        return {}
    placeholders = ",".join("?" for _ in project_ids)
    with connect() as conn:
        rows = conn.execute(
            f"""SELECT p.id,
                       (SELECT COUNT(*) FROM pages
                         WHERE project_id=p.id AND status != 'missing') AS pages_count,
                       (SELECT COUNT(*) FROM users
                         WHERE active=1 AND (id=p.owner_id OR id IN
                           (SELECT user_id FROM project_members WHERE project_id=p.id)))
                         AS members_count,
                       (SELECT username FROM users WHERE id=p.owner_id) AS owner_name,
                       (SELECT role FROM project_members
                         WHERE project_id=p.id AND user_id=?) AS role
                  FROM projects p WHERE p.id IN ({placeholders})""",
            (user_id, *project_ids),
        ).fetchall()
    return {int(row["id"]): dict(row) for row in rows}


def workflow_stats(project_id: int) -> tuple[dict[str, int], dict[str, Any] | None]:
    """Status counts and the next page to work on, as the workflow screen shows them."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM pages "
            "WHERE project_id=? AND status != 'missing' GROUP BY status",
            (project_id,),
        ).fetchall()
        next_page = conn.execute(
            "SELECT p.id, p.rel_path, p.status, p.issue_date, p.issue_no, p.page_no, "
            "COUNT(b.id) AS blocks FROM pages p LEFT JOIN blocks b ON b.page_id=p.id "
            "WHERE p.project_id=? AND p.status != 'missing' GROUP BY p.id "
            "ORDER BY CASE p.status WHEN 'new' THEN 0 WHEN 'annotated' THEN 1 "
            "WHEN 'qa' THEN 2 WHEN 'review' THEN 3 ELSE 4 END, p.rel_path LIMIT 1",
            (project_id,),
        ).fetchone()
    return (
        {str(row["status"]): int(row["n"]) for row in rows},
        dict(next_page) if next_page is not None else None,
    )


def activity(project_id: int, limit: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT a.id, a.action, a.resource_type, a.resource_id, a.payload_json, "
            "a.created_at, u.username FROM audit_events a "
            "LEFT JOIN users u ON u.id=a.actor_id "
            "WHERE (a.resource_type='project' AND a.resource_id=?) OR "
            "(a.resource_type='page' AND a.resource_id IN "
            "(SELECT id FROM pages WHERE project_id=?)) "
            "ORDER BY a.id DESC LIMIT ?",
            (project_id, project_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def annotation_queue(project_id: int, limit: int) -> list[dict[str, Any]]:
    """Pages in working order, with their block and unconfirmed counts."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.rel_path, p.status, p.issue_date, p.issue_no, p.page_no, "
            "COUNT(b.id) AS blocks, SUM(CASE WHEN b.confirmed=0 THEN 1 ELSE 0 END) AS unconfirmed "
            "FROM pages p LEFT JOIN blocks b ON b.page_id=p.id WHERE p.project_id=? "
            "GROUP BY p.id ORDER BY CASE p.status WHEN 'new' THEN 0 WHEN 'annotated' THEN 1 "
            "WHEN 'qa' THEN 2 WHEN 'review' THEN 3 ELSE 4 END, unconfirmed DESC, "
            "p.rel_path LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def prefill_sources(page_id: int) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT prefill_source FROM blocks WHERE page_id=?", (page_id,)
        ).fetchall()
    return [str(row["prefill_source"] or "") for row in rows]


def valid_page_ids(project_id: int, page_ids: list[int]) -> list[int]:
    """The requested ids that really belong to the project, sorted."""
    placeholders = ",".join("?" for _ in page_ids) or "NULL"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})",
            [project_id, *page_ids],
        ).fetchall()
    return sorted({int(row["id"]) for row in rows})


def page_ids(project_id: int) -> list[int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM pages WHERE project_id=?", (project_id,)
        ).fetchall()
    return [int(row["id"]) for row in rows]


def delete(project_id: int) -> None:
    """Delete a project (its pages, blocks and grids follow by cascade)."""
    with connect() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def review_inputs(project_id: int) -> list[dict[str, Any]]:
    """`(status, errors_json)` for every review of the project's pages."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, errors_json FROM page_reviews r JOIN pages p ON p.id=r.page_id "
            "WHERE p.project_id=?",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]

