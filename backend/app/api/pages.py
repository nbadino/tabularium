"""API pagine: listing, aggiornamento metadati, thumbnail e preview."""
from __future__ import annotations

import hashlib
import threading
import zlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from .. import config
from ..adapters import annotations as annotationstore
from ..adapters import pages as pagestore
from ..db import connect  # noqa: F401  (approvals, reviews, deskew: P7.1)
from ..schemas import PageList, PageOut, PageSums, PageUpdate, ProjectSumsOut
from ..services import collections as collectionsvc
from ..services import pages as pagesvc
from ..services import images, table_checks
from ..domain import otsl as otslmod
from ..services.i18n import msg, parse_lang
from ..services import auth as authsvc
from ..services import audit as auditsvc
from ..services import page_work as worksvc
from .deps import require_resource
import json
from pydantic import BaseModel, Field

router = APIRouter(
    tags=["pages"],
    dependencies=[Depends(authsvc.get_current_user)],
)


class ReviewIn(BaseModel):
    reviewer: str = Field(default="local", min_length=1, max_length=120)
    status: str = Field(pattern="^(pending|pass|fail)$")
    errors: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=4000)

PAGE_STATUSES = ["new", "annotated", "qa", "review", "approved", "exported"]
MANUAL_PAGE_STATUSES = {"new", "annotated", "review"}


def _get_page_or_404(page_id: int):
    row = pagestore.get(page_id)
    if row is None:
        raise HTTPException(status_code=404, detail="pagina non trovata")
    return row


@router.get("/api/projects/{project_id}/pages", response_model=PageList)
def list_pages(
    project_id: int,
    request: Request,
    page_type: str | None = None,
    status: str | None = None,
    # A collection id, or `none` for the pages without a collection.
    collection: str | None = None,
    # No default cap: a fixed one (200, then 10,000) silently truncated large
    # corpora, and the pages beyond it could not be seen anywhere. Callers
    # that want fewer pages ask for them.
    limit: int | None = Query(default=None, ge=1),
    # Only the pages created after this id: the poll during an import fetches
    # the new ones, not the whole project again every 3 seconds.
    after_id: int | None = Query(default=None, ge=0),
    _auth: dict = Depends(require_resource(write=False)),
) -> Response:
    if collection is not None and collection != "none" and not collection.isdigit():
        lang = parse_lang(request.headers.get("accept-language"))
        raise HTTPException(status_code=400, detail=msg("collection_filter_invalid", lang))
    rows, total = pagestore.list_for_project(
        project_id, page_type=page_type, status=status, collection=collection,
        limit=limit, after_id=after_id,
    )
    with connect() as conn:
        work_items = worksvc.project_work(conn, project_id, _auth)
    for row in rows:
        row["work"] = work_items.get(row["id"])
    # JSON written directly: the rows already have the shape of `PageListItem`
    # (the query picks the columns), and validating them one by one with
    # pydantic was half the request time on a 15,000-page project.
    body = json.dumps(
        {"items": rows, "total": total}, ensure_ascii=False, separators=(",", ":")
    ).encode()
    # Reopening an unchanged project does not download the list again: the
    # browser revalidates with the ETag and gets an empty 304.
    tag = hashlib.blake2b(body, digest_size=16).hexdigest()
    headers = {"ETag": f'"{tag}"', "Cache-Control": "private, no-cache"}
    if tag in _etag_values(request.headers.get("if-none-match")):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _etag_values(header: str | None) -> set[str]:
    """The tags of `If-None-Match`, without `W/`, quotes or the encoding suffix
    proxies and CDNs add when they compress (`"abc-zstd"`, `W/"abc"`)."""
    out = set()
    for part in (header or "").split(","):
        value = part.strip().removeprefix("W/").strip('"')
        if value:
            out.add(value.split("-", 1)[0])
    return out


# Result per table, keyed by a fingerprint of its content: only what changed
# is recomputed. ponytail: grows by one entry per saved table, never emptied;
# an LRU if volumes reach hundreds of thousands of tables.
#
# Also saved to disk, with a stable fingerprint (Python's `hash()` changes at
# every start): kept in memory only, after every restart or deploy the first
# Studio opening recomputed every table of the project (~6 s of CPU for 468
# tables), and meanwhile the server answered almost no other request.
_SUMS_CACHE: dict[int, tuple[str, PageSums]] = {}
_SUMS_STATE = {"loaded": False}
_SUMS_LOCK = threading.Lock()
_SUMS_COMPUTE = threading.Lock()


def _sums_file() -> Path:
    return config.ROOT_DIR / "cache" / "table_sums.json"


def _load_sums() -> None:
    with _SUMS_LOCK:
        if _SUMS_STATE["loaded"]:
            return
        _SUMS_STATE["loaded"] = True
        try:
            data = json.loads(_sums_file().read_text(encoding="utf-8"))
            for block_id, (key, checks, failed, rows, cols) in data.items():
                _SUMS_CACHE.setdefault(int(block_id), (str(key), PageSums(
                    checks=checks, failed=failed, rows=rows, cols=cols)))
        except (OSError, TypeError, ValueError):
            pass  # No file, or an unreadable one: recompute, as the first time.


def _save_sums() -> None:
    with _SUMS_LOCK:
        data = {
            str(block_id): [key, v.checks, v.failed, v.rows, v.cols]
            for block_id, (key, v) in _SUMS_CACHE.items()
        }
        path = _sums_file()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # Only a cache: without it, the next start recomputes.


@router.get("/api/projects/{project_id}/sum-failures", response_model=ProjectSumsOut)
def project_sum_failures(
    project_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> ProjectSumsOut:
    """How many sums do not add up, page by page: the page list shows it next
    to the status, so one knows where to look without opening every table.
    Separate from the list because it costs ~8 ms per table the first time."""
    _load_sums()
    # One computation at a time: the Studio asks for the sums twice in a row
    # when it opens, and two full recomputations in parallel doubled the CPU
    # exactly when the server is busiest. The second waits and finds the cache.
    with _SUMS_COMPUTE:
        return _project_sums(project_id)


def _project_sums(project_id: int) -> ProjectSumsOut:
    rows = pagestore.sum_failures_inputs(project_id)
    pages: dict[int, PageSums] = {}
    changed = False
    for page_id, block_id, grid_json in rows:
        # Stable across restarts and cheap: it is computed over every table of
        # the project on each call (tens of MB).
        raw = (grid_json or "").encode()
        key = f"{len(raw)}:{zlib.crc32(raw):08x}"
        cached = _SUMS_CACHE.get(block_id)
        if cached is None or cached[0] != key:
            try:
                out = table_checks.check_grid(json.loads(grid_json or "{}"))
            except (TypeError, ValueError):
                continue
            cached = _SUMS_CACHE[block_id] = (key, PageSums(
                checks=len(out["checks"]),
                failed=out["failed"],
                rows=sum(c["kind"] == "row" for c in out["checks"]),
                cols=sum(c["kind"] == "col" for c in out["checks"]),
            ))
            changed = True
        table = cached[1]
        if table.checks:
            prev = pages.get(page_id) or PageSums(checks=0, failed=0)
            pages[page_id] = PageSums(
                checks=prev.checks + table.checks, failed=prev.failed + table.failed,
                rows=prev.rows + table.rows, cols=prev.cols + table.cols,
            )
    if changed:
        _save_sums()
    return ProjectSumsOut(pages=pages)


@router.patch("/api/pages/{page_id}", response_model=PageOut)
def update_page(
    page_id: int,
    payload: PageUpdate,
    user: dict = Depends(require_resource(write=True)),
) -> PageOut:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="nessun campo da aggiornare")
    if "status" in updates:
        if updates["status"] not in PAGE_STATUSES:
            raise HTTPException(status_code=400, detail=f"status non valido: {updates['status']}")
        if updates["status"] not in MANUAL_PAGE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    "transizione di stato protetta: usa la revisione QA o "
                    "l'endpoint /approve"
                ),
            )
    _get_page_or_404(page_id)
    pagestore.update_fields(page_id, updates)
    if True:
        row = _get_page_or_404(page_id)
        return PageOut(
            id=row["id"], project_id=row["project_id"], rel_path=row["rel_path"],
            abs_path=row["abs_path"], source_kind=row["source_kind"],
            pdf_page=row["pdf_page"], width=row["width"], height=row["height"],
            issue_date=row["issue_date"], issue_no=row["issue_no"],
            page_no=row["page_no"], page_type=row["page_type"],
            status=row["status"], created_at=row["created_at"],
            annotation_revision=row["annotation_revision"],
        )


# Il calcolo di «pagina pronta» è logica di dominio condivisa: vive in
# ``services/pages.py::compute_readiness``. Il router lo espone via HTTP.


@router.get("/api/pages/{page_id}/readiness")
def page_readiness(
    page_id: int,
    request: Request,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    lang = parse_lang(request.headers.get("accept-language"))
    return pagesvc.compute_readiness(page_id, lang=lang)


@router.post("/api/pages/{page_id}/approve")
def approve_page(
    page_id: int,
    request: Request,
    payload: BulkApproveIn | None = None,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    lang = parse_lang(request.headers.get("accept-language"))
    confirm_drafts = bool(payload and payload.confirm_drafts)
    readiness = pagesvc.approve_page(
        page_id, confirm_drafts=confirm_drafts, lang=lang, actor=user
    )
    accepted = {
        key: readiness[key] for key in ("confirmed_blocks", "confirmed_cells", "annotation_revision")
        if key in readiness
    }
    if accepted:
        from .blocks import _notify_live
        _notify_live(page_id, accepted.get('annotation_revision'), 'approval', user)
    return readiness


class BulkApproveIn(BaseModel):
    # `None` = tutte le pagine del progetto; una lista = solo quelle.
    page_ids: list[int] | None = None
    # Conferma anche le bozze non verificate (le rende verità): esplicito,
    # perché insegna al modello il proprio output. Senza, si approvano solo le
    # pagine già pronte e le altre si dichiarano saltate.
    confirm_drafts: bool = False


@router.post("/api/projects/{project_id}/pages/approve")
def approve_pages_bulk(
    project_id: int,
    payload: BulkApproveIn,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Approva in blocco le pagine del progetto (con `confirm_drafts` anche le bozze)."""
    lang = parse_lang(request.headers.get("accept-language"))
    report = pagesvc.approve_pages(
        project_id,
        payload.page_ids,
        confirm_drafts=payload.confirm_drafts,
        lang=lang,
        actor=user,
    )
    accepted_revisions = report.pop("accepted_revisions")
    from .blocks import _notify_live
    for pid, revision in accepted_revisions.items():
        _notify_live(pid, revision, 'approval', user)
    return report


@router.get("/api/pages/{page_id}/reviews")
def page_reviews(
    page_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    _get_page_or_404(page_id)
    return {
        "page_id": page_id,
        "items": [
            row | {"errors": json.loads(row.get("errors_json") or "[]")}
            for row in pagestore.reviews(page_id)
        ],
    }


@router.post("/api/pages/{page_id}/reviews")
def add_page_review(
    page_id: int,
    payload: ReviewIn,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    _get_page_or_404(page_id)
    with connect() as conn:
        if worksvc.configured(conn, page_id):
            worksvc.fail("work_use_review", "it")
    review_id = pagestore.add_review(
        page_id,
        reviewer=payload.reviewer,
        status=payload.status,
        errors=payload.errors,
        notes=payload.notes,
    )
    return {"id": review_id, "page_id": page_id, **payload.model_dump()}


@router.get("/api/pages/{page_id}/thumbnail")
def page_thumbnail(
    page_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> FileResponse:
    page = _get_page_or_404(page_id)
    thumb = pagesvc.ensure_thumbnail(page)
    # Archivio, Riconosci e Studio mostrano le stesse centinaia di miniature:
    # senza una cache anche breve il browser le rivalidava una per una a ogni
    # cambio di schermata. Un minuto basta a un giro fra le schermate, e dopo un nuovo master la
    # ETag cambia comunque alla prima rivalidazione.
    return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=60"})


@router.get("/api/pages/{page_id}/preview")
def page_preview(
    page_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> FileResponse:
    page = _get_page_or_404(page_id)
    preview = pagesvc.ensure_preview(page)
    return FileResponse(preview, media_type="image/jpeg")


def _invalidate_page_cache(page_id: int) -> None:
    """Elimina thumbnails/preview/tiles cachate così si rigenerano dal deskew."""
    for p in (pagesvc.thumb_path(page_id), pagesvc.preview_path(page_id)):
        if p.exists():
            p.unlink(missing_ok=True)
    tiles = config.ROOT_DIR / "tiles" / f"p{page_id}"
    if tiles.exists():
        import shutil

        shutil.rmtree(tiles, ignore_errors=True)


def _drop_transform(page_id: int) -> None:
    """Back to the original image: the file and the metadata that says it is active."""
    pagesvc.deskew_path(page_id).unlink(missing_ok=True)
    pagesvc.legacy_deskew_path(page_id).unlink(missing_ok=True)
    pagesvc.transform_meta_path(page_id).unlink(missing_ok=True)


@router.post("/api/pages/{page_id}/deskew")
def deskew_page(
    page_id: int,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Raddrizza la pagina (text deskew). Cambia le coordinate: richiede pagina
    senza blocchi, oppure ?confirm=true per cancellarli e ripartire."""
    from ..services import images as imgmod

    page = _get_page_or_404(page_id)
    with annotationstore.writing() as writer:
        n_blocks = writer.count_blocks(page_id)
    if n_blocks and not confirm:
        raise HTTPException(
            status_code=409,
            detail=f"la pagina ha {n_blocks} blocchi: il deskew cambia le coordinate. "
            "Passa ?confirm=true per eliminarli e ripartire.",
        )

    img = pagesvc.load_original_source_image(page)
    if img is None:
        raise pagesvc.source_missing(page)
    out, angle = imgmod.deskew(img)
    noop = abs(angle) < 0.3
    if noop and pagesvc._active_transform_path(page_id) is None:  # noqa: SLF001
        return {"page_id": page_id, "deskewed": False, "angle": 0.0}

    if confirm:
        with annotationstore.writing() as writer:
            writer.delete_page_blocks(page_id)
    _invalidate_page_cache(page_id)

    desk = pagesvc.deskew_path(page_id)
    if noop:
        _drop_transform(page_id)
        return {"page_id": page_id, "deskewed": False, "angle": 0.0}
    desk.parent.mkdir(parents=True, exist_ok=True)
    out.convert("RGB").save(desk, "PNG", compress_level=4)
    pagesvc.legacy_deskew_path(page_id).unlink(missing_ok=True)
    pagesvc.mark_transform(
        page_id,
        "deskew",
        out.size,
        details={"requested_engine": "deskew", "actual_engine": "deskew", "angle": angle},
    )
    return {"page_id": page_id, "deskewed": True, "angle": angle}


@router.delete("/api/pages/{page_id}/deskew")
def deskew_remove(
    page_id: int,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Rimuove ogni trasformazione salvata e torna all'immagine originale."""
    _get_page_or_404(page_id)
    with annotationstore.writing() as writer:
        n_blocks = writer.count_blocks(page_id)
        if n_blocks and not confirm:
            raise HTTPException(
                status_code=409,
                detail="la pagina ha blocchi annotati: il reset cambia le coordinate. "
                "Passa ?confirm=true per eliminarli e ripartire.",
            )
        if confirm:
            writer.delete_page_blocks(page_id)
    _drop_transform(page_id)
    pagesvc.clear_candidate(page_id)
    _invalidate_page_cache(page_id)
    return {"page_id": page_id, "deskewed": False, "reset": True}


class AlignRequest(BaseModel):
    level: str = Field(default="medium", pattern="^(basic|medium|high)$")
    strength: float = Field(default=1.0, ge=0.0, le=2.0)


class TransformCandidateRequest(BaseModel):
    engine: str = Field(pattern="^(deskew|monkeyocr|perspective|mesh)$")
    perspective_points: list[list[float]] | None = None
    mesh_grid: list[list[list[float]]] | None = None


@router.post("/api/pages/{page_id}/align")
def align_page_endpoint(
    page_id: int,
    payload: AlignRequest,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Allinea la pagina con livello di qualità scelto: basic = solo rotazione,
    medium/high = preprocessore ufficiale MonkeyOCRv2.
    Cambia le coordinate: richiede pagina senza blocchi o ?confirm=true."""
    from ..services import dewarp

    page = _get_page_or_404(page_id)
    with annotationstore.writing() as writer:
        n_blocks = writer.count_blocks(page_id)
    if n_blocks and not confirm:
        raise HTTPException(
            status_code=409,
            detail=f"la pagina ha {n_blocks} blocchi: l'allineamento cambia le coordinate. Passa ?confirm=true.",
        )

    img = pagesvc.load_original_source_image(page)
    if img is None:
        raise pagesvc.source_missing(page)
    # Engine and diagnostics are module state: read them under the same lock as the run.
    with dewarp._transform_lock:  # noqa: SLF001
        aligned, angle = dewarp.align_page(img, payload.level, payload.strength)
        engine, diagnostics = dewarp.last_engine(), dewarp.last_diagnostics()
    noop = payload.level == "basic" and abs(angle) < 0.05
    if noop and pagesvc._active_transform_path(page_id) is None:  # noqa: SLF001
        return {"page_id": page_id, "applied": False, "angle": 0.0, "level": "basic"}

    if confirm:
        with annotationstore.writing() as writer:
            writer.delete_page_blocks(page_id)
    _invalidate_page_cache(page_id)

    desk = pagesvc.deskew_path(page_id)
    if noop:
        _drop_transform(page_id)
        return {"page_id": page_id, "applied": False, "angle": 0.0, "level": "basic"}
    desk.parent.mkdir(parents=True, exist_ok=True)
    aligned.convert("RGB").save(desk, "PNG", compress_level=4)
    pagesvc.legacy_deskew_path(page_id).unlink(missing_ok=True)
    pagesvc.mark_transform(
        page_id,
        payload.level,
        aligned.size,
        details={
            "requested_engine": payload.level,
            "actual_engine": engine,
            "angle": angle,
            "diagnostics": diagnostics,
        },
    )
    return {"page_id": page_id, "applied": True, "angle": angle, "level": payload.level, "engine": engine}


def _transform_state(page_id: int) -> dict:
    import importlib.util

    from ..services import monkey_preprocess

    active = pagesvc.read_transform_meta(page_id)
    candidate = pagesvc.read_transform_meta(page_id, candidate=True)
    return {
        "page_id": page_id,
        "active": active,
        "candidate": candidate,
        "original_preview_url": f"/api/pages/{page_id}/transform-preview/original",
        "active_preview_url": f"/api/pages/{page_id}/preview" if active else None,
        "candidate_preview_url": (
            f"/api/pages/{page_id}/transform-preview/candidate" if candidate else None
        ),
        "engines": {
            "deskew": {"available": images.cv2_available()},
            "perspective": {"available": images.cv2_available()},
            "mesh": {"available": True},
            # Unica rettifica neurale: quella del modello stesso.
            "monkeyocr": {"available": monkey_preprocess.available()},
        },
    }


@router.get("/api/pages/{page_id}/transform")
def transform_state(
    page_id: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> dict:
    with connect() as conn:
        _get_page_or_404(page_id)
    return _transform_state(page_id)


@router.get("/api/pages/{page_id}/transform-preview/{kind}")
def transform_preview(
    page_id: int,
    kind: str,
    _auth: dict = Depends(require_resource(write=False)),
) -> FileResponse:
    with connect() as conn:
        page = _get_page_or_404(page_id)
        preview = pagesvc.ensure_transform_preview(page, kind)
    return FileResponse(preview, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/api/pages/{page_id}/transform/candidate")
def generate_transform_candidate(
    page_id: int,
    payload: TransformCandidateRequest,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Genera una proposta lossless senza cambiare canvas, crop o annotazioni."""
    from ..services import dewarp

    with connect() as conn:
        page = _get_page_or_404(page_id)
    image = pagesvc.load_original_source_image(page)
    if image is None:
        raise pagesvc.source_missing(page)
    result = dewarp.run_transform(
        image.convert("RGB"),
        payload.engine,
        perspective_points=payload.perspective_points,
        mesh_grid=payload.mesh_grid,
    )
    if result.actual_engine == "none":
        raise HTTPException(
            status_code=422,
            detail={
                "message": "transform_candidate_invalid",
                "engine": payload.engine,
                "error": result.error,
                "diagnostics": result.diagnostics,
            },
        )
    pagesvc.clear_candidate(page_id)
    candidate = pagesvc.candidate_path(page_id)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    result.image.convert("RGB").save(candidate, "PNG", compress_level=4)
    metadata = pagesvc.mark_transform(
        page_id,
        result.actual_engine,
        result.image.size,
        path=pagesvc.candidate_meta_path(page_id),
        details={
            "requested_engine": result.requested_engine,
            "actual_engine": result.actual_engine,
            "angle": result.angle,
            "warnings": result.warnings,
            "error": result.error,
            "diagnostics": result.diagnostics,
        },
    )
    return _transform_state(page_id) | {"generated": metadata}


@router.post("/api/pages/{page_id}/transform/accept")
def accept_transform_candidate(
    page_id: int,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Promuove atomicamente la proposta a master dopo conferma esplicita."""
    candidate = pagesvc.candidate_path(page_id)
    metadata = pagesvc.read_transform_meta(page_id, candidate=True)
    if not candidate.exists() or metadata is None:
        raise HTTPException(status_code=404, detail="transform_candidate_missing")
    try:
        from PIL import Image

        with Image.open(candidate) as image:
            candidate_size = image.size
            image.verify()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"transform_candidate_corrupt: {exc}") from exc
    page = _get_page_or_404(page_id)
    if candidate_size != (int(page["width"]), int(page["height"])):
        raise HTTPException(status_code=422, detail="transform_candidate_size_mismatch")
    with annotationstore.writing() as writer:
        n_blocks = writer.count_blocks(page_id)
        if n_blocks and not confirm:
            raise HTTPException(
                status_code=409,
                detail=f"la pagina ha {n_blocks} blocchi: accettare la trasformazione cambia le coordinate. Passa ?confirm=true.",
            )
        if confirm:
            writer.delete_page_blocks(page_id)
    active = pagesvc.deskew_path(page_id)
    active.parent.mkdir(parents=True, exist_ok=True)
    candidate.replace(active)
    pagesvc.legacy_deskew_path(page_id).unlink(missing_ok=True)
    pagesvc.mark_transform(
        page_id,
        str(metadata.get("actual_engine") or metadata.get("engine") or "unknown"),
        candidate_size,
        details={**metadata, "accepted": True},
    )
    pagesvc.clear_candidate(page_id)
    _invalidate_page_cache(page_id)
    return _transform_state(page_id) | {"accepted": True}


@router.delete("/api/pages/{page_id}/transform/candidate")
def reject_transform_candidate(
    page_id: int,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    with connect() as conn:
        _get_page_or_404(page_id)
    pagesvc.clear_candidate(page_id)
    return _transform_state(page_id) | {"rejected": True}


class DeletePagesIn(BaseModel):
    page_ids: list[int] = Field(..., min_length=1, max_length=10000)


@router.delete("/api/pages/{page_id}")
def delete_page(
    page_id: int,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Delete a single page from its project (never touches external source files)."""
    row = _get_page_or_404(page_id)
    lang = parse_lang(request.headers.get("accept-language"))
    try:
        deleted = collectionsvc.delete_pages_for_project(
            int(row["project_id"]), [page_id], actor=user, action="page.deleted",
        )
    except collectionsvc.PagesBusy as exc:
        raise HTTPException(status_code=409, detail=msg("collection_pages_busy", lang)) from exc
    return {"deleted": len(deleted)}


@router.post("/api/projects/{project_id}/pages/delete")
def delete_project_pages(
    project_id: int,
    payload: DeletePagesIn,
    request: Request,
    user: dict = Depends(require_resource(write=True)),
) -> dict:
    """Delete selected pages from a project (never touches external source files)."""
    lang = parse_lang(request.headers.get("accept-language"))
    try:
        deleted = collectionsvc.delete_pages_for_project(
            project_id, payload.page_ids, actor=user, action="pages.deleted",
        )
    except collectionsvc.PagesBusy as exc:
        raise HTTPException(status_code=409, detail=msg("collection_pages_busy", lang)) from exc
    return {"deleted": len(deleted), "deleted_ids": [int(r["id"]) for r in deleted]}


@router.get("/api/pages/{page_id}/tile/{level}/{x}/{y}")
def page_tile(
    page_id: int,
    level: int,
    x: int,
    y: int,
    _auth: dict = Depends(require_resource(write=False)),
) -> FileResponse:
    with connect() as conn:
        page = _get_page_or_404(page_id)
        tile = pagesvc.ensure_tile(page, level, x, y)
    return FileResponse(tile, media_type="image/jpeg", headers={"Cache-Control": "private, no-cache"})
