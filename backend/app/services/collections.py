"""Collections of a project: deletion and page files (docs/COLLECTIONS.md).

Deleting a collection always says what happens to its pages (R9): they move
to "no collection", or they are deleted with the collection.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from ..adapters import collections as collectionstore
from ..adapters import pages as pagestore
from ..db import connect
from . import audit as auditsvc
from . import pages as pagesvc


class PagesBusy(Exception):
    """A recognition is still working on one of the pages."""


def _remove_page_files(page_id: int) -> None:
    """Thumbnails, previews, transforms and tiles derived from the page."""
    for path in pagesvc.thumb_dir().glob(f"p{page_id}.*"):
        path.unlink(missing_ok=True)
    for path in pagesvc.thumb_dir().glob(f"p{page_id}_*"):
        path.unlink(missing_ok=True)
    shutil.rmtree(config.ROOT_DIR / "tiles" / f"p{page_id}", ignore_errors=True)


def _owned_upload(project_id: int, abs_path: str) -> bool:
    """A source file Tabularium stored for this project's uploads.

    Files in a server folder (`archive_dir`) belong to the user and are never
    removed.
    """
    uploads = (config.DATA_DIR / str(project_id) / "uploads").resolve()
    try:
        Path(abs_path).resolve().relative_to(uploads)
    except ValueError:
        return False
    return True


def delete_pages_for_project(
    project_id: int,
    page_ids: list[int],
    *,
    actor: dict | None,
    action: str = "pages.deleted",
    audit_extra: dict | None = None,
) -> list[dict]:
    """Delete pages belonging to `project_id` and clean up owned upload/cache files.

    Never deletes external source files in a server `archive_dir`.
    """
    unique_ids = sorted({int(pid) for pid in page_ids})
    if not unique_ids:
        return []
    if pagestore.in_active_recognition(unique_ids):
        raise PagesBusy()
    deleted: list[dict] = []
    with connect() as conn:
        deleted = pagestore.delete_rows(conn, project_id, unique_ids)
        if deleted:
            auditsvc.record(
                conn, actor, action, resource_type="project", resource_id=project_id,
                payload={"page_ids": [row["id"] for row in deleted], **(audit_extra or {})},
            )
        orphans = {
            row["abs_path"] for row in deleted
            if _owned_upload(project_id, row["abs_path"]) and not pagestore.path_in_use(conn, row["abs_path"])
        }
    for row in deleted:
        _remove_page_files(int(row["id"]))
    for path in orphans:
        Path(path).unlink(missing_ok=True)
    return deleted


def delete_collection(project_id: int, collection_id: int, *, pages: str, actor: dict | None) -> dict:
    """Delete the collection; `pages` is `unassign` or `delete`."""
    with connect() as conn:
        ids = collectionstore.page_ids(project_id, collection_id, conn)
        collection = collectionstore.get(project_id, collection_id, conn)
    if pages == "delete":
        if pagestore.in_active_recognition(ids):
            raise PagesBusy()
        deleted = delete_pages_for_project(
            project_id,
            ids,
            actor=actor,
            action="collection.deleted",
            audit_extra={
                "collection_id": collection_id,
                "name": collection["name"] if collection else None,
                "pages": pages,
                "unassigned": 0,
            },
        )
        with connect() as conn:
            collectionstore.delete(conn, project_id, collection_id)
            if not deleted:
                auditsvc.record(
                    conn, actor, "collection.deleted", resource_type="project", resource_id=project_id,
                    payload={
                        "collection_id": collection_id,
                        "name": collection["name"] if collection else None,
                        "pages": pages,
                        "page_ids": [],
                        "unassigned": 0,
                    },
                )
        return {"deleted": True, "pages_unassigned": 0, "pages_deleted": len(deleted)}

    with connect() as conn:
        unassigned = collectionstore.unassign_and_delete(conn, project_id, collection_id)
        auditsvc.record(
            conn, actor, "collection.deleted", resource_type="project", resource_id=project_id,
            payload={
                "collection_id": collection_id,
                "name": collection["name"] if collection else None,
                "pages": pages,
                "page_ids": [],
                "unassigned": unassigned,
            },
        )
    return {"deleted": True, "pages_unassigned": unassigned, "pages_deleted": 0}


class ScopeError(Exception):
    """A scope the project cannot satisfy: an empty choice or another project's collection."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


PROJECT_SCOPE = {"kind": "project", "collections": [], "include_unassigned": True}


def resolve_scope(project_id: int, scope: dict | None) -> tuple[dict, set[int] | None]:
    """The scope as recorded (names at this moment) and its page ids.

    `None` page ids means the whole project. The record keeps the collection
    names of the build, so a later rename does not rewrite what a model was
    trained on.
    """
    if not scope or scope.get("kind", "project") == "project":
        return dict(PROJECT_SCOPE), None
    ids = sorted({int(i) for i in scope.get("collection_ids") or []})
    include_unassigned = bool(scope.get("include_unassigned"))
    if not ids and not include_unassigned:
        raise ScopeError("scope_empty", 400)
    names = collectionstore.names(project_id)
    if any(i not in names for i in ids):
        raise ScopeError("collection_not_found", 404)
    record = {
        "kind": "collections",
        "collections": [{"id": i, "name": names[i]} for i in ids],
        "include_unassigned": include_unassigned,
    }
    return record, collectionstore.pages_in_scope(project_id, ids, include_unassigned)


def scope_pages(project_id: int, record: dict | None) -> set[int] | None:
    """Page ids of a recorded scope, today; `None` for the whole project."""
    if not record or record.get("kind") != "collections":
        return None
    ids = [int(c["id"]) for c in record.get("collections") or []]
    return collectionstore.pages_in_scope(project_id, ids, bool(record.get("include_unassigned")))
