"""Collections of a project: named groups of pages (docs/COLLECTIONS.md).

A page belongs to at most one collection. `pages.collection_id IS NULL` is the
declared state "no collection", which the API calls `none`.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..db import connect

# The filter key of the "no collection" state, in the API and in the UI.
UNASSIGNED = "none"


def get(project_id: int, collection_id: int, conn=None) -> dict[str, Any] | None:
    """The collection, only if it belongs to the project."""
    statement = "SELECT * FROM collections WHERE id=? AND project_id=?"
    if conn is not None:
        row = conn.execute(statement, (collection_id, project_id)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (collection_id, project_id)).fetchone()
    return dict(row) if row is not None else None


def create(project_id: int, name: str) -> dict[str, Any] | None:
    """A new collection; `None` when the name is already used in the project."""
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO collections (project_id, name) VALUES (?, ?)", (project_id, name)
            )
        except sqlite3.IntegrityError:
            return None
        return get(project_id, int(cur.lastrowid), conn)


def ensure(conn, project_id: int, name: str) -> tuple[int, str, bool]:
    """The collection with this name (any case), created if missing: `(id, name, created)`."""
    row = conn.execute(
        "SELECT id, name FROM collections WHERE project_id=? AND name=? COLLATE NOCASE",
        (project_id, name),
    ).fetchone()
    if row is not None:
        return int(row["id"]), str(row["name"]), False
    cur = conn.execute("INSERT INTO collections (project_id, name) VALUES (?, ?)", (project_id, name))
    return int(cur.lastrowid), name, True


def rename(project_id: int, collection_id: int, name: str) -> dict[str, Any] | None:
    """Rename; raises `sqlite3.IntegrityError` when the name is taken."""
    with connect() as conn:
        conn.execute(
            "UPDATE collections SET name=?, updated_at=datetime('now') WHERE id=? AND project_id=?",
            (name, collection_id, project_id),
        )
        return get(project_id, collection_id, conn)


def page_ids(project_id: int, collection_id: int, conn) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM pages WHERE project_id=? AND collection_id=?", (project_id, collection_id)
    ).fetchall()
    return [int(r["id"]) for r in rows]


def unassign_and_delete(conn, project_id: int, collection_id: int) -> int:
    """Move the pages to "no collection", then remove the collection."""
    cur = conn.execute(
        "UPDATE pages SET collection_id=NULL WHERE project_id=? AND collection_id=?",
        (project_id, collection_id),
    )
    conn.execute("DELETE FROM collections WHERE id=? AND project_id=?", (collection_id, project_id))
    return cur.rowcount


def delete(conn, project_id: int, collection_id: int) -> None:
    conn.execute("DELETE FROM collections WHERE id=? AND project_id=?", (collection_id, project_id))


def foreign_pages(project_id: int, page_ids: list[int]) -> list[int]:
    """The ids in the list that are not pages of the project."""
    if not page_ids:
        return []
    marks = ",".join("?" * len(page_ids))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM pages WHERE project_id=? AND id IN ({marks})", (project_id, *page_ids)
        ).fetchall()
    own = {int(r["id"]) for r in rows}
    return [pid for pid in page_ids if pid not in own]


def assign(project_id: int, page_ids: list[int], collection_id: int | None) -> int:
    """Put the pages in the collection (`None`: no collection).

    The project is checked in the same statement, so a page or a collection of
    another project is never touched even if the caller forgot to check.
    """
    if not page_ids:
        return 0
    marks = ",".join("?" * len(page_ids))
    with connect() as conn:
        if collection_id is None:
            cur = conn.execute(
                f"UPDATE pages SET collection_id=NULL WHERE project_id=? AND id IN ({marks})",
                (project_id, *page_ids),
            )
        else:
            cur = conn.execute(
                f"UPDATE pages SET collection_id=? WHERE project_id=? AND id IN ({marks}) "
                "AND EXISTS (SELECT 1 FROM collections WHERE id=? AND project_id=?)",
                (collection_id, project_id, *page_ids, collection_id, project_id),
            )
    return cur.rowcount


def counts(project_id: int) -> dict[str, Any]:
    """Collections with page counts by status, plus "no collection" and the total.

    Missing pages (their source left the archive) are not counted, as in the page
    list.
    """
    with connect() as conn:
        collections = conn.execute(
            "SELECT id, name, created_at, updated_at FROM collections WHERE project_id=? "
            "ORDER BY name COLLATE NOCASE",
            (project_id,),
        ).fetchall()
        rows = conn.execute(
            "SELECT collection_id, status, COUNT(*) AS n, "
            "SUM(CASE WHEN EXISTS (SELECT 1 FROM blocks b WHERE b.page_id=pages.id "
            "AND b.confirmed=0 AND b.prefill_source IS NOT NULL) THEN 1 ELSE 0 END) AS drafts "
            "FROM pages WHERE project_id=? AND status != 'missing' "
            "GROUP BY collection_id, status",
            (project_id,),
        ).fetchall()

    def empty() -> dict[str, Any]:
        return {"pages": 0, "with_drafts": 0, "by_status": {}}

    groups: dict[int | None, dict[str, Any]] = {}
    total = empty()
    for row in rows:
        for target in (groups.setdefault(row["collection_id"], empty()), total):
            target["pages"] += int(row["n"])
            target["with_drafts"] += int(row["drafts"] or 0)
            target["by_status"][row["status"]] = target["by_status"].get(row["status"], 0) + int(row["n"])
    return {
        "collections": [{**dict(c), **groups.get(int(c["id"]), empty())} for c in collections],
        "unassigned": {"key": UNASSIGNED, **groups.get(None, empty())},
        "total": total,
    }


def names(project_id: int) -> dict[int, str]:
    with connect() as conn:
        rows = conn.execute("SELECT id, name FROM collections WHERE project_id=?", (project_id,)).fetchall()
    return {int(r["id"]): str(r["name"]) for r in rows}


def pages_in_scope(project_id: int, collection_ids: list[int], include_unassigned: bool) -> set[int]:
    """Ids of the pages in the chosen collections, plus those without one if asked."""
    clauses: list[str] = []
    params: list = [project_id]
    if collection_ids:
        clauses.append(f"collection_id IN ({','.join('?' * len(collection_ids))})")
        params.extend(collection_ids)
    if include_unassigned:
        clauses.append("collection_id IS NULL")
    if not clauses:
        return set()
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM pages WHERE project_id=? AND ({' OR '.join(clauses)})", params
        ).fetchall()
    return {int(r["id"]) for r in rows}


def page_collections(project_id: int) -> dict[int, int | None]:
    """`{page_id: collection_id}` for every page of the project."""
    with connect() as conn:
        rows = conn.execute("SELECT id, collection_id FROM pages WHERE project_id=?", (project_id,)).fetchall()
    return {int(r["id"]): r["collection_id"] for r in rows}
