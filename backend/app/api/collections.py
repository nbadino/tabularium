"""API collections: named groups of pages inside a project (docs/COLLECTIONS.md)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints

from ..adapters import collections as collectionstore
from ..services import auth as authsvc
from ..services import collections as collectionsvc
from ..services.i18n import msg, parse_lang
from .deps import require_resource

router = APIRouter(
    tags=["collections"],
    dependencies=[Depends(authsvc.get_current_user)],
)

CollectionName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class CollectionIn(BaseModel):
    name: CollectionName


class MovePagesIn(BaseModel):
    page_ids: list[int] = Field(min_length=1, max_length=10000)
    # `null` is the declared state "no collection", not a missing value.
    collection_id: int | None


def _lang(request: Request) -> str:
    return parse_lang(request.headers.get("accept-language"))


def _collection_or_404(project_id: int, collection_id: int, lang: str) -> dict:
    collection = collectionstore.get(project_id, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=msg("collection_not_found", lang))
    return collection


def _project_or_404(project_id: int) -> None:
    from .projects import _get_project_or_404  # noqa: PLC0415

    _get_project_or_404(None, project_id)


@router.get("/api/projects/{project_id}/collections")
def list_collections(project_id: int, _auth: dict = Depends(require_resource(write=False))) -> dict:
    _project_or_404(project_id)
    return collectionstore.counts(project_id)


@router.post("/api/projects/{project_id}/collections", status_code=201)
def create_collection(
    project_id: int,
    payload: CollectionIn,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    _project_or_404(project_id)
    created = collectionstore.create(project_id, payload.name)
    if created is None:
        raise HTTPException(status_code=409, detail=msg("collection_name_taken", _lang(request), name=payload.name))
    return created


@router.patch("/api/projects/{project_id}/collections/{collection_id}")
def rename_collection(
    project_id: int,
    collection_id: int,
    payload: CollectionIn,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    lang = _lang(request)
    _collection_or_404(project_id, collection_id, lang)
    try:
        return collectionstore.rename(project_id, collection_id, payload.name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=msg("collection_name_taken", lang, name=payload.name)) from exc


@router.delete("/api/projects/{project_id}/collections/{collection_id}")
def delete_collection(
    project_id: int,
    collection_id: int,
    request: Request,
    # Required, with no default: the user always says what happens to the pages.
    pages: Literal["unassign", "delete"] = Query(...),
    confirm: bool = Query(default=False),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    lang = _lang(request)
    _collection_or_404(project_id, collection_id, lang)
    if pages == "delete":
        if authsvc.require_project_access(project_id, _auth, write=True) != "owner":
            raise HTTPException(status_code=403, detail=msg("collection_delete_owner", lang))
        if not confirm:
            raise HTTPException(status_code=400, detail=msg("collection_delete_confirm", lang))
    try:
        return collectionsvc.delete_collection(project_id, collection_id, pages=pages, actor=_auth)
    except collectionsvc.PagesBusy as exc:
        raise HTTPException(status_code=409, detail=msg("collection_pages_busy", lang)) from exc


@router.post("/api/projects/{project_id}/pages/collection")
def move_pages(
    project_id: int,
    payload: MovePagesIn,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    lang = _lang(request)
    if payload.collection_id is not None:
        _collection_or_404(project_id, payload.collection_id, lang)
    page_ids = sorted(set(payload.page_ids))
    if collectionstore.foreign_pages(project_id, page_ids):
        raise HTTPException(status_code=404, detail=msg("collection_pages_not_found", lang))
    moved = collectionstore.assign(project_id, page_ids, payload.collection_id)
    return {"moved": moved, "collection_id": payload.collection_id}
