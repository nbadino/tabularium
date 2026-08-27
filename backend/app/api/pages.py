"""API pagine: listing, aggiornamento metadati, thumbnail e preview."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .. import config
from ..db import connect
from ..schemas import PageList, PageOut, PageUpdate
from ..services import pages as pagesvc
from ..services import otsl as otslmod
from ..services.i18n import msg, parse_lang
import json
from pydantic import BaseModel, Field

router = APIRouter(tags=["pages"])


class ReviewIn(BaseModel):
    reviewer: str = Field(default="local", min_length=1, max_length=120)
    status: str = Field(pattern="^(pending|pass|fail)$")
    errors: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=4000)

PAGE_STATUSES = ["new", "annotated", "qa", "review", "approved", "exported"]
MANUAL_PAGE_STATUSES = {"new", "annotated", "review"}


def _get_page_or_404(conn, page_id: int):
    row = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="pagina non trovata")
    return row


@router.get("/api/projects/{project_id}/pages", response_model=PageList)
def list_pages(
    project_id: int,
    page_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
) -> PageList:
    sql = "SELECT * FROM pages WHERE project_id=?"
    params: list = [project_id]
    if page_type:
        sql += " AND page_type=?"
        params.append(page_type)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY rel_path, pdf_page LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        items = [
            PageOut(
                id=r["id"],
                project_id=r["project_id"],
                rel_path=r["rel_path"],
                abs_path=r["abs_path"],
                source_kind=r["source_kind"],
                pdf_page=r["pdf_page"],
                width=r["width"],
                height=r["height"],
                issue_date=r["issue_date"],
                issue_no=r["issue_no"],
                page_no=r["page_no"],
                page_type=r["page_type"],
                status=r["status"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
        return PageList(items=items)


@router.patch("/api/pages/{page_id}", response_model=PageOut)
def update_page(page_id: int, payload: PageUpdate) -> PageOut:
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
    sets = ", ".join(f"{k}=?" for k in updates)
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        conn.execute(f"UPDATE pages SET {sets} WHERE id=?", (*updates.values(), page_id))
        row = _get_page_or_404(conn, page_id)
        return PageOut(
            id=row["id"], project_id=row["project_id"], rel_path=row["rel_path"],
            abs_path=row["abs_path"], source_kind=row["source_kind"],
            pdf_page=row["pdf_page"], width=row["width"], height=row["height"],
            issue_date=row["issue_date"], issue_no=row["issue_no"],
            page_no=row["page_no"], page_type=row["page_type"],
            status=row["status"], created_at=row["created_at"],
        )


def _readiness(conn, page_id: int, lang: str = "it") -> dict:
    page = _get_page_or_404(conn, page_id)
    blocks = conn.execute("SELECT * FROM blocks WHERE page_id=? ORDER BY id", (page_id,)).fetchall()
    errors: list[str] = []
    warnings: list[str] = []
    content_ok = True
    tables_ok = True
    geometry_ok = True
    if not blocks:
        errors.append(msg("no_blocks", lang))
    for block in blocks:
        try:
            points = json.loads(block["points"] or "[]")
        except (TypeError, ValueError):
            points = []
        if len(points) < 2:
            geometry_ok = False
            errors.append(msg("geometry_missing", lang, id=block["id"]))
        if not block["confirmed"]:
            # Una pseudo-label non confermata non è ground truth. Lasciarla
            # come semplice warning consentiva di approvare e poi esportare
            # direttamente predizioni grezze del modello/OCR.
            errors.append(msg("not_confirmed", lang, id=block["id"]))
        label = block["label"]
        if label in {"Text", "Headline", "Byline", "Issue-header", "Advertisement", "Note/Adder"} and not (block["content"] or "").strip():
            content_ok = False
            errors.append(msg("empty_transcription", lang, id=block["id"]))
        if label == "Table":
            row = conn.execute("SELECT grid_json FROM tables WHERE block_id=?", (block["id"],)).fetchone()
            if not row:
                tables_ok = False
                errors.append(msg("table_grid_missing", lang, id=block["id"]))
            else:
                try:
                    grid = json.loads(row["grid_json"] or "{}")
                    otslmod.grid_to_otsl(grid)
                except (TypeError, ValueError) as exc:
                    tables_ok = False
                    errors.append(msg("table_grid_invalid", lang, id=block["id"], exc=exc))
    structure_ok = bool(blocks) and geometry_ok
    return {
        "page_id": page_id, "status": page["status"], "ready": not errors,
        "errors": errors, "warnings": warnings,
        "stages": {
            "structure": structure_ok,
            "content": structure_ok and content_ok,
            "table": structure_ok and tables_ok,
            "review": not errors and not warnings,
        },
    }


@router.get("/api/pages/{page_id}/readiness")
def page_readiness(page_id: int, request: Request) -> dict:
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        return _readiness(conn, page_id, lang=lang)


@router.post("/api/pages/{page_id}/approve")
def approve_page(page_id: int, request: Request) -> dict:
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        readiness = _readiness(conn, page_id, lang=lang)
        page = _get_page_or_404(conn, page_id)
        project = conn.execute("SELECT settings_json FROM projects WHERE id=?", (page["project_id"],)).fetchone()
        try:
            gold_pages = set(json.loads(project["settings_json"] or "{}").get("study_protocol", {}).get("gold_pages", [])) if project else set()
        except (TypeError, ValueError):
            gold_pages = set()
        if page_id in {int(value) for value in gold_pages}:
            passed = conn.execute("SELECT 1 FROM page_reviews WHERE page_id=? AND status='pass' LIMIT 1", (page_id,)).fetchone()
            if not passed:
                readiness["errors"].append(msg("gold_review", lang))
                readiness["ready"] = False
        if not readiness["ready"]:
            raise HTTPException(status_code=409, detail={"message": msg("page_not_ready", lang), **readiness})
        conn.execute("UPDATE pages SET status='approved' WHERE id=?", (page_id,))
        readiness["status"] = "approved"
        return readiness


@router.get("/api/pages/{page_id}/reviews")
def page_reviews(page_id: int) -> dict:
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        rows = conn.execute("SELECT * FROM page_reviews WHERE page_id=? ORDER BY id DESC", (page_id,)).fetchall()
    return {"page_id": page_id, "items": [dict(row) | {"errors": json.loads(row["errors_json"] or "[]")} for row in rows]}


@router.post("/api/pages/{page_id}/reviews")
def add_page_review(page_id: int, payload: ReviewIn) -> dict:
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        cur = conn.execute("INSERT INTO page_reviews (page_id, reviewer, status, errors_json, notes) VALUES (?,?,?,?,?)", (page_id, payload.reviewer, payload.status, json.dumps(payload.errors, ensure_ascii=False), payload.notes))
        if payload.status == "pass":
            conn.execute("UPDATE pages SET status='qa' WHERE id=? AND status IN ('annotated','review')", (page_id,))
        elif payload.status == "fail":
            conn.execute("UPDATE pages SET status='review' WHERE id=? AND status != 'approved'", (page_id,))
        return {"id": cur.lastrowid, "page_id": page_id, **payload.model_dump()}


@router.get("/api/pages/{page_id}/thumbnail")
def page_thumbnail(page_id: int) -> FileResponse:
    with connect() as conn:
        page = _get_page_or_404(conn, page_id)
        thumb = pagesvc.ensure_thumbnail(conn, page)
    return FileResponse(thumb, media_type="image/jpeg")


@router.get("/api/pages/{page_id}/preview")
def page_preview(page_id: int) -> FileResponse:
    with connect() as conn:
        page = _get_page_or_404(conn, page_id)
        preview = pagesvc.ensure_preview(conn, page)
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


@router.post("/api/pages/{page_id}/deskew")
def deskew_page(
    page_id: int,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
) -> dict:
    """Raddrizza la pagina (text deskew). Cambia le coordinate: richiede pagina
    senza blocchi, oppure ?confirm=true per cancellarli e ripartire."""
    from ..services import images as imgmod

    with connect() as conn:
        page = _get_page_or_404(conn, page_id)
        n_blocks = conn.execute(
            "SELECT COUNT(*) AS n FROM blocks WHERE page_id=?", (page_id,)
        ).fetchone()["n"]
        if n_blocks and not confirm:
            raise HTTPException(
                status_code=409,
                detail=f"la pagina ha {n_blocks} blocchi: il deskew cambia le coordinate. "
                "Passa ?confirm=true per eliminarli e ripartire.",
            )

    img = pagesvc.load_original_source_image(page)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    out, angle = imgmod.deskew(img)

    if confirm:
        with connect() as conn:
            conn.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
    _invalidate_page_cache(page_id)

    desk = pagesvc.deskew_path(page_id)
    if abs(angle) < 0.3:
        desk.unlink(missing_ok=True)
        return {"page_id": page_id, "deskewed": False, "angle": 0.0}
    desk.parent.mkdir(parents=True, exist_ok=True)
    out.convert("RGB").save(desk, "JPEG", quality=92)
    pagesvc.mark_transform(page_id, "basic", out.size)
    return {"page_id": page_id, "deskewed": True, "angle": angle}


@router.delete("/api/pages/{page_id}/deskew")
def deskew_remove(
    page_id: int,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
) -> dict:
    """Rimuove ogni trasformazione salvata e torna all'immagine originale."""
    with connect() as conn:
        _get_page_or_404(conn, page_id)
        n_blocks = conn.execute(
            "SELECT COUNT(*) AS n FROM blocks WHERE page_id=?", (page_id,)
        ).fetchone()["n"]
        if n_blocks and not confirm:
            raise HTTPException(
                status_code=409,
                detail="la pagina ha blocchi annotati: il reset cambia le coordinate. "
                "Passa ?confirm=true per eliminarli e ripartire.",
            )
        if confirm:
            conn.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
    pagesvc.deskew_path(page_id).unlink(missing_ok=True)
    pagesvc.transform_meta_path(page_id).unlink(missing_ok=True)
    _invalidate_page_cache(page_id)
    return {"page_id": page_id, "deskewed": False, "reset": True}


class AlignRequest(BaseModel):
    level: str = Field(default="medium", pattern="^(basic|medium|high)$")
    strength: float = Field(default=1.0, ge=0.0, le=2.0)


@router.post("/api/pages/{page_id}/align")
def align_page_endpoint(
    page_id: int,
    payload: AlignRequest,
    confirm: bool = Query(default=False, description="elimina i blocchi già annotati"),
) -> dict:
    """Allinea la pagina con livello di qualità scelto:
    basic = solo rotazione, medium/high = UVDoc con validazione anti-crop.
    Cambia le coordinate: richiede pagina senza blocchi o ?confirm=true."""
    from ..services import dewarp

    with connect() as conn:
        page = _get_page_or_404(conn, page_id)
        n_blocks = conn.execute(
            "SELECT COUNT(*) AS n FROM blocks WHERE page_id=?", (page_id,)
        ).fetchone()["n"]
        if n_blocks and not confirm:
            raise HTTPException(
                status_code=409,
                detail=f"la pagina ha {n_blocks} blocchi: l'allineamento cambia le coordinate. Passa ?confirm=true.",
            )

    img = pagesvc.load_original_source_image(page)
    if img is None:
        raise HTTPException(status_code=404, detail="file sorgente non presente")
    aligned, angle = dewarp.align_page(img, payload.level, payload.strength)

    if confirm:
        with connect() as conn:
            conn.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
    _invalidate_page_cache(page_id)

    desk = pagesvc.deskew_path(page_id)
    if payload.level == "basic" and abs(angle) < 0.05:
        desk.unlink(missing_ok=True)
        return {"page_id": page_id, "applied": False, "angle": 0.0, "level": "basic"}
    desk.parent.mkdir(parents=True, exist_ok=True)
    aligned.convert("RGB").save(desk, "JPEG", quality=92)
    pagesvc.mark_transform(page_id, payload.level, aligned.size)
    return {"page_id": page_id, "applied": True, "angle": angle, "level": payload.level, "engine": dewarp.last_engine()}


@router.get("/api/pages/{page_id}/tile/{level}/{x}/{y}")
def page_tile(page_id: int, level: int, x: int, y: int) -> FileResponse:
    with connect() as conn:
        page = _get_page_or_404(conn, page_id)
        tile = pagesvc.ensure_tile(page, level, x, y)
    return FileResponse(tile, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})
