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


def delete_collection(project_id: int, collection_id: int, *, pages: str, actor: dict | None) -> dict:
    """Delete the collection; `pages` is `unassign` or `delete`."""
    with connect() as conn:
        ids = collectionstore.page_ids(project_id, collection_id, conn)
    if pages == "delete" and pagestore.in_active_recognition(ids):
        raise PagesBusy()
    deleted: list[dict] = []
    unassigned = 0
    with connect() as conn:
        collection = collectionstore.get(project_id, collection_id, conn)
        if pages == "delete":
            deleted = pagestore.delete_rows(conn, project_id, ids)
            collectionstore.delete(conn, project_id, collection_id)
        else:
            unassigned = collectionstore.unassign_and_delete(conn, project_id, collection_id)
        auditsvc.record(
            conn, actor, "collection.deleted", resource_type="project", resource_id=project_id,
            payload={
                "collection_id": collection_id,
                "name": collection["name"] if collection else None,
                "pages": pages,
                "page_ids": [row["id"] for row in deleted],
                "unassigned": unassigned,
            },
        )
        orphans = {
            row["abs_path"] for row in deleted
            if _owned_upload(project_id, row["abs_path"]) and not pagestore.path_in_use(conn, row["abs_path"])
        }
    for row in deleted:
        _remove_page_files(int(row["id"]))
    for path in orphans:
        Path(path).unlink(missing_ok=True)
    return {"deleted": True, "pages_unassigned": unassigned, "pages_deleted": len(deleted)}
