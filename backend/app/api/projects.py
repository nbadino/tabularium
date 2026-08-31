"""API progetti e pagine: CRUD, scansione archivio, metadati, anteprime."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from .. import config
from ..db import connect
from ..schemas import (
    ConventionItem,
    ConventionsIn,
    ConventionsOut,
    ProjectCreate,
    ProjectMemberIn,
    ProjectMemberOut,
    ProjectOwnerIn,
    ProjectList,
    ProjectOut,
    ScanReportOut,
    StudyProtocolIn,
    StudyProtocolOut,
)
from ..services import pages as pagesvc
from ..services import scan as scanmod
from ..services import corpus as corpusmod
from ..services import pilot as pilotmod
from ..services import auth as authsvc
from ..services import audit as auditsvc
from ..services.i18n import msg, parse_lang
from .deps import require_resource

router = APIRouter(
    tags=["projects"],
    dependencies=[Depends(authsvc.get_current_user)],
)

# Page type ammessi (estensione della tassonomia giornale) — usati nel frontend.
PAGE_TYPES = [
    "front",
    "editorial",
    "shipping",
    "casualties",
    "adverts",
    "misc",
]

# Convenzioni di trascrizione predefinite (checklist dell'annotatore).
CONVENTIONS_DEFAULT = [
    ConventionItem(
        id="soft_hyphen",
        label="Ricomporre le parole spezzate a fine riga (soft hyphen), senza trattino",
        checked=True,
    ),
    ConventionItem(
        id="sigle",
        label="Conservare grafia e sigle originali (inst., ult., barq., psgr. stmr., maiuscoletto)",
        checked=True,
    ),
    ConventionItem(
        id="corsivi",
        label="Nomi di navi in corsivo → *nave*",
        checked=True,
    ),
    ConventionItem(
        id="colonne_fantasma",
        label="Le colonne fantasma si annotano nella griglia, mai nel testo",
        checked=True,
    ),
    ConventionItem(
        id="filetti",
        label="Filetti e linee di riga appartengono alla griglia, mai al testo",
        checked=True,
    ),
]


# --- helper ------------------------------------------------------------------
def _get_project_or_404(conn, project_id: int):
    row = conn.execute(
        "SELECT * FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    return row


def _project_out(conn, row) -> ProjectOut:
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM pages WHERE project_id=?", (row["id"],)
    ).fetchone()["n"]
    try:
        settings = json.loads(row["settings_json"] or "{}")
    except (TypeError, ValueError):
        settings = {}
    return ProjectOut(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        root_dir=row["root_dir"],
        archive_dir=row["archive_dir"],
        settings_json=settings if isinstance(settings, dict) else {},
        pages_count=count,
        created_at=row["created_at"],
    )


# La registrazione dei candidati (immagini e PDF) è logica di dominio:
# vive in ``services/scan.py::register_candidate``, condivisa da scansione
# e import manuale. Qui restano solo gli endpoint HTTP.


# --- endpoints ---------------------------------------------------------------
@router.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    user: dict = Depends(authsvc.get_current_user),
) -> ProjectOut:
    # Chi crea il progetto ne diventa il proprietario. In modalità off
    # `owner_id` resta NULL (non esistono utenti).
    authsvc.require_role(user, "editor")
    archive_dir = str(Path(payload.archive_dir).expanduser().resolve())
    if not Path(archive_dir).is_dir():
        raise HTTPException(status_code=400, detail="la cartella archivio non esiste")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO projects (name, root_dir, archive_dir, settings_json, owner_id)
               VALUES (?, ?, ?, '{}', ?)""",
            (payload.name, str(config.DATA_DIR), archive_dir, user.get("id")),
        )
        project_id = cur.lastrowid
        # directory dati del progetto (crops/runs arriveranno nelle milestone successive)
        (config.DATA_DIR / str(project_id)).mkdir(parents=True, exist_ok=True)
        row = _get_project_or_404(conn, project_id)
        return _project_out(conn, row)


@router.get("/api/projects", response_model=ProjectList)
def list_projects(user: dict = Depends(authsvc.get_current_user)) -> ProjectList:
    with connect() as conn:
        if authsvc.is_local(user) or authsvc.is_admin(user):
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY created_at DESC"
            ).fetchall()
        else:
            # Solo i progetti di cui l'utente è proprietario o membro.
            rows = conn.execute(
                """SELECT DISTINCT p.* FROM projects p
                   LEFT JOIN project_members pm ON pm.project_id = p.id
                   WHERE p.owner_id = ? OR pm.user_id = ?
                   ORDER BY p.created_at DESC""",
                (user["id"], user["id"]),
            ).fetchall()
        return ProjectList(items=[_project_out(conn, r) for r in rows])


@router.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> ProjectOut:
    with connect() as conn:
        return _project_out(conn, _get_project_or_404(conn, project_id))


def _require_project_manager(project_id: int, user: dict) -> str:
    level = authsvc.require_project_access(project_id, user, write=True)
    if level != "owner" and not authsvc.is_admin(user):
        raise HTTPException(status_code=403, detail="solo il proprietario può gestire l'accesso")
    return level


@router.get("/api/projects/{project_id}/members", response_model=list[ProjectMemberOut])
def list_project_members(project_id: int, user: dict = Depends(authsvc.get_current_user)) -> list[ProjectMemberOut]:
    authsvc.require_project_access(project_id, user, write=False)
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        rows = conn.execute(
            """SELECT u.id AS user_id, u.username, u.email, u.active, 'owner' AS role
               FROM users u WHERE u.id=?
               UNION ALL
               SELECT u.id, u.username, u.email, u.active, pm.role
               FROM project_members pm JOIN users u ON u.id=pm.user_id
               WHERE pm.project_id=? AND u.id != COALESCE(?, -1)
               ORDER BY role, username""",
            (project["owner_id"], project_id, project["owner_id"]),
        ).fetchall()
    return [ProjectMemberOut(**dict(row)) for row in rows]


@router.get("/api/projects/{project_id}/member-candidates", response_model=list[ProjectMemberOut])
def project_member_candidates(project_id: int, user: dict = Depends(authsvc.get_current_user)) -> list[ProjectMemberOut]:
    _require_project_manager(project_id, user)
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT id AS user_id, username, email, active, 'viewer' AS role FROM users "
            "WHERE active=1 AND id != ? AND id NOT IN "
            "(SELECT user_id FROM project_members WHERE project_id=?) ORDER BY username",
            (project["owner_id"], project_id),
        ).fetchall()
    return [ProjectMemberOut(**dict(row)) for row in rows]


@router.put("/api/projects/{project_id}/members/{user_id}", response_model=ProjectMemberOut)
def set_project_member(
    project_id: int,
    user_id: int,
    payload: ProjectMemberIn,
    user: dict = Depends(authsvc.get_current_user),
) -> ProjectMemberOut:
    _require_project_manager(project_id, user)
    if payload.user_id != user_id:
        raise HTTPException(status_code=400, detail="user_id incoerente")
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        target = conn.execute("SELECT id, username, email, active FROM users WHERE id=?", (user_id,)).fetchone()
        if target is None or not target["active"]:
            raise HTTPException(status_code=404, detail="utente attivo non trovato")
        if project["owner_id"] == user_id:
            raise HTTPException(status_code=400, detail="il proprietario non può essere membro")
        conn.execute(
            "INSERT INTO project_members(project_id,user_id,role) VALUES(?,?,?) "
            "ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role",
            (project_id, user_id, payload.role),
        )
        auditsvc.record(conn, user, "project.member_set", resource_type="project", resource_id=project_id, payload={"user_id": user_id, "role": payload.role})
    return ProjectMemberOut(user_id=user_id, username=target["username"], email=target["email"], active=bool(target["active"]), role=payload.role)


@router.delete("/api/projects/{project_id}/members/{user_id}", status_code=204)
def remove_project_member(project_id: int, user_id: int, user: dict = Depends(authsvc.get_current_user)) -> None:
    _require_project_manager(project_id, user)
    with connect() as conn:
        _get_project_or_404(conn, project_id)
        deleted = conn.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?", (project_id, user_id)).rowcount
        if not deleted:
            raise HTTPException(status_code=404, detail="membro non trovato")
        auditsvc.record(conn, user, "project.member_removed", resource_type="project", resource_id=project_id, payload={"user_id": user_id})


@router.post("/api/projects/{project_id}/owner", response_model=ProjectMemberOut)
def transfer_project_owner(project_id: int, payload: ProjectOwnerIn, user: dict = Depends(authsvc.get_current_user)) -> ProjectMemberOut:
    _require_project_manager(project_id, user)
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        target = conn.execute("SELECT id, username, email, active FROM users WHERE id=?", (payload.user_id,)).fetchone()
        if target is None or not target["active"]:
            raise HTTPException(status_code=404, detail="utente attivo non trovato")
        old_owner = project["owner_id"]
        if old_owner == payload.user_id:
            return ProjectMemberOut(user_id=target["id"], username=target["username"], email=target["email"], active=True, role="owner")
        conn.execute("UPDATE projects SET owner_id=? WHERE id=?", (payload.user_id, project_id))
        if old_owner is not None:
            conn.execute(
                "INSERT INTO project_members(project_id,user_id,role) VALUES(?,?, 'editor') "
                "ON CONFLICT(project_id,user_id) DO UPDATE SET role='editor'",
                (project_id, old_owner),
            )
        conn.execute("DELETE FROM project_members WHERE project_id=? AND user_id=?", (project_id, payload.user_id))
        auditsvc.record(conn, user, "project.owner_transferred", resource_type="project", resource_id=project_id, payload={"from_user_id": old_owner, "to_user_id": payload.user_id})
    return ProjectMemberOut(user_id=target["id"], username=target["username"], email=target["email"], active=True, role="owner")


@router.get("/api/projects/{project_id}/workflow")
def project_workflow(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> dict:
    """Stato operativo e prossimo compito consigliato per il ricercatore."""
    with connect() as conn:
        _get_project_or_404(conn, project_id)
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM pages WHERE project_id=? GROUP BY status",
            (project_id,),
        ).fetchall()
        counts = {row["status"]: row["n"] for row in status_rows}
        next_page = conn.execute(
            "SELECT p.id, p.rel_path, p.status, p.issue_date, p.issue_no, p.page_no, "
            "COUNT(b.id) AS blocks FROM pages p LEFT JOIN blocks b ON b.page_id=p.id "
            "WHERE p.project_id=? GROUP BY p.id "
            "ORDER BY CASE p.status WHEN 'new' THEN 0 WHEN 'annotated' THEN 1 "
            "WHEN 'qa' THEN 2 WHEN 'review' THEN 3 ELSE 4 END, p.rel_path LIMIT 1",
            (project_id,),
        ).fetchone()
        total = sum(counts.values())
        approved = sum(counts.get(s, 0) for s in ("qa", "review", "exported", "approved"))
        return {
            "project_id": project_id,
            "counts": counts,
            "total_pages": total,
            "approved_pages": approved,
            "progress": approved / total if total else 0.0,
            "next_page": dict(next_page) if next_page else None,
        }


@router.get("/api/projects/{project_id}/activity")
def project_activity(project_id: int, limit: int = Query(default=30, ge=1, le=200), _auth: dict = Depends(require_resource())) -> dict:
    with connect() as conn:
        _get_project_or_404(conn, project_id)
        rows = conn.execute(
            "SELECT a.id, a.action, a.resource_type, a.resource_id, a.payload_json, a.created_at, u.username "
            "FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id "
            "WHERE (a.resource_type='project' AND a.resource_id=?) OR "
            "(a.resource_type='page' AND a.resource_id IN (SELECT id FROM pages WHERE project_id=?)) "
            "ORDER BY a.id DESC LIMIT ?",
            (project_id, project_id, limit),
        ).fetchall()
    return {"project_id": project_id, "items": [dict(row) for row in rows]}


@router.get("/api/projects/{project_id}/annotation-queue")
def annotation_queue(
    project_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    _auth: dict = Depends(require_resource()),
) -> dict:
    """Coda deterministica: prima pagine nuove/incomplete, poi QA e revisione."""
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        _get_project_or_404(conn, project_id)
        eval_actions: dict[int, list[str]] = {}
        eval_root = config.DATA_DIR / str(project_id) / "eval"
        reports = sorted(eval_root.glob("eval_*/report.json"), reverse=True) if eval_root.exists() else []
        if reports:
            try:
                latest = json.loads(reports[0].read_text(encoding="utf-8"))
                eval_actions = {int(p["page_id"]): list(p.get("actions", [])) for p in latest.get("pages", []) if p.get("actions")}
            except (OSError, TypeError, ValueError, KeyError):
                eval_actions = {}
        rows = conn.execute(
            "SELECT p.id, p.rel_path, p.status, p.issue_date, p.issue_no, p.page_no, "
            "COUNT(b.id) AS blocks, SUM(CASE WHEN b.confirmed=0 THEN 1 ELSE 0 END) AS unconfirmed "
            "FROM pages p LEFT JOIN blocks b ON b.page_id=p.id WHERE p.project_id=? "
            "GROUP BY p.id ORDER BY CASE p.status WHEN 'new' THEN 0 WHEN 'annotated' THEN 1 "
            "WHEN 'qa' THEN 2 WHEN 'review' THEN 3 ELSE 4 END, unconfirmed DESC, p.rel_path LIMIT ?",
            (project_id, limit),
        ).fetchall()
        items = []
        for row in rows:
            status = row["status"]
            low_confidence = 0
            for block in conn.execute("SELECT prefill_source FROM blocks WHERE page_id=?", (row["id"],)).fetchall():
                source = str(block["prefill_source"] or "")
                try:
                    if ":" in source and float(source.rsplit(":", 1)[1]) < 0.75:
                        low_confidence += 1
                except ValueError:
                    pass
            page_actions = eval_actions.get(int(row["id"]), [])
            if page_actions:
                reason = page_actions[0]
            elif low_confidence:
                reason = msg("review_low_conf", lang, n=low_confidence)
            elif row["blocks"] and row["unconfirmed"]:
                reason = msg("confirm_ocr", lang)
            elif status == "new" or not row["blocks"]:
                reason = msg("annotate_structure", lang)
            elif status in {"qa", "review"}:
                reason = msg("quality_check", lang)
            else:
                reason = msg("already_worked", lang)
            items.append({**dict(row), "low_confidence": low_confidence, "evaluation_actions": page_actions, "reason": reason})
        return {"project_id": project_id, "items": items}


@router.get("/api/projects/{project_id}/corpus-map")
def corpus_map(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> dict:
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return corpusmod.corpus_map(project_id)


@router.get("/api/projects/{project_id}/study-protocol", response_model=StudyProtocolOut)
def get_study_protocol(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> StudyProtocolOut:
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        settings = json.loads(project["settings_json"] or "{}")
    protocol = settings.get("study_protocol") or {}
    return StudyProtocolOut(**protocol) if protocol else StudyProtocolOut(updated_at=datetime.now(timezone.utc).isoformat())


@router.put("/api/projects/{project_id}/study-protocol", response_model=StudyProtocolOut)
def put_study_protocol(
    project_id: int,
    payload: StudyProtocolIn,
    _auth: dict = Depends(require_resource(write=True)),
) -> StudyProtocolOut:
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        settings = json.loads(project["settings_json"] or "{}")
        previous = settings.get("study_protocol") or {}
        version = int(previous.get("version", 0)) + 1
        protocol = {**payload.model_dump(), "version": version, "updated_at": now}
        history = settings.setdefault("study_protocol_history", [])
        history.append(protocol)
        settings["study_protocol"] = protocol
        conn.execute("UPDATE projects SET settings_json=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), project_id))
    return StudyProtocolOut(**protocol)


@router.post("/api/projects/{project_id}/gold-set")
def set_gold_set(
    project_id: int,
    page_ids: list[int],
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Protegge un campione di pagine invisibile al tuning."""
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        placeholders = ",".join("?" for _ in page_ids) or "NULL"
        rows = conn.execute(f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})", [project_id, *page_ids]).fetchall()
        valid = sorted({int(row["id"]) for row in rows})
        settings = json.loads(project["settings_json"] or "{}")
        protocol = settings.setdefault("study_protocol", {})
        protocol["gold_pages"] = valid
        protocol["version"] = int(protocol.get("version", 0)) + 1
        protocol["updated_at"] = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE projects SET settings_json=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), project_id))
    return {"project_id": project_id, "gold_pages": valid, "count": len(valid), "protected": True}


@router.get("/api/projects/{project_id}/qa-report")
def qa_report(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> dict:
    """Riepilogo revisioni e conteggio degli errori ricorrenti."""
    with connect() as conn:
        _get_project_or_404(conn, project_id)
        rows = conn.execute("SELECT status, errors_json FROM page_reviews r JOIN pages p ON p.id=r.page_id WHERE p.project_id=?", (project_id,)).fetchall()
    errors: dict[str, int] = {}
    for row in rows:
        try:
            values = json.loads(row["errors_json"] or "[]")
        except (TypeError, ValueError):
            values = []
        for value in values:
            errors[str(value)] = errors.get(str(value), 0) + 1
    return {"project_id": project_id, "reviews": {status: sum(1 for row in rows if row["status"] == status) for status in ("pending", "pass", "fail")}, "recurring_errors": sorted(({"error": key, "count": value} for key, value in errors.items()), key=lambda item: (-item["count"], item["error"]))}


@router.get("/api/projects/{project_id}/pilot-sample")
def pilot_sample(
    project_id: int,
    target: int = Query(default=40, ge=1, le=50),
    seed: int = Query(default=42, ge=0),
    _auth: dict = Depends(require_resource()),
) -> dict:
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    return pilotmod.sample_pilot(project_id, target, seed)


@router.post("/api/projects/{project_id}/pilot-sample/save")
def save_pilot_sample(
    project_id: int,
    page_ids: list[int],
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        placeholders = ",".join("?" for _ in page_ids) or "NULL"
        rows = conn.execute(f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})", [project_id, *page_ids]).fetchall()
        valid = sorted({int(row["id"]) for row in rows})
        settings = json.loads(project["settings_json"] or "{}")
        protocol = settings.setdefault("study_protocol", {})
        protocol["pilot_pages"] = valid
        protocol["updated_at"] = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE projects SET settings_json=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), project_id))
    return {"project_id": project_id, "pilot_pages": valid, "count": len(valid)}


@router.get("/api/projects/{project_id}/conventions", response_model=ConventionsOut)
def get_conventions(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> ConventionsOut:
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        settings = json.loads(project["settings_json"] or "{}")
        conv = settings.get("conventions")
        if not conv:
            return ConventionsOut(conventions=CONVENTIONS_DEFAULT)
        return ConventionsOut(
            conventions=[ConventionItem(**c) for c in conv],
        )


@router.put("/api/projects/{project_id}/conventions", response_model=ConventionsOut)
def put_conventions(
    project_id: int,
    payload: ConventionsIn,
    _auth: dict = Depends(require_resource(write=True)),
) -> ConventionsOut:
    dumped = [c.model_dump() for c in payload.conventions]
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        settings = json.loads(project["settings_json"] or "{}")
        settings["conventions"] = dumped
        conn.execute(
            "UPDATE projects SET settings_json=? WHERE id=?",
            (json.dumps(settings), project_id),
        )
    return ConventionsOut(conventions=payload.conventions)


@router.delete("/api/projects/{project_id}")
def delete_project(
    project_id: int,
    confirm: bool = Query(default=False, description="conferma esplicita richiesta"),
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="operazione distruttiva: passa ?confirm=true per eliminare il progetto",
        )
    with connect() as conn:
        # Cancellare un progetto è irreversibile: solo il proprietario (o un
        # admin, che ha livello "owner") può farlo, non un editor invitato.
        if authsvc.require_project_access(project_id, _auth, write=True) != "owner":
            raise HTTPException(
                status_code=403,
                detail="solo il proprietario del progetto può eliminarlo",
            )
        project = _get_project_or_404(conn, project_id)
        # rimuove le thumbnails delle pagine del progetto
        for row in conn.execute(
            "SELECT id FROM pages WHERE project_id=?", (project_id,)
        ).fetchall():
            for p in (
                pagesvc.thumb_path(row["id"]),
                pagesvc.preview_path(row["id"]),
            ):
                if p.exists():
                    p.unlink()
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        # directory di progetto (se vuota o creata da noi)
        project_dir = config.DATA_DIR / str(project_id)
        if project_dir.exists() and project_dir.is_dir():
            try:
                project_dir.rmdir()  # solo se vuota
            except OSError:
                pass
    return {"deleted": True, "project": project["name"]}


@router.post("/api/projects/{project_id}/scan", response_model=ScanReportOut)
def scan_project(
    project_id: int,
    request: Request,
    _auth: dict = Depends(require_resource(write=True)),
) -> ScanReportOut:
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        project = _get_project_or_404(conn, project_id)
        archive_dir = project["archive_dir"]
        if not archive_dir or not Path(archive_dir).is_dir():
            raise HTTPException(status_code=400, detail="archive_dir non valida")
        candidates = scanmod.scan_archive(archive_dir)
        report = scanmod.ScanReport(found_files=len(candidates))
        for cand in candidates:
            scanmod.register_candidate(conn, project_id, cand, Path(archive_dir), report, lang=lang)
        return ScanReportOut(**report.to_dict())


@router.post("/api/projects/{project_id}/import-upload", response_model=ScanReportOut)
async def import_uploaded_folder(
    project_id: int,
    request: Request,
    files: list[UploadFile] = File(...),
    _auth: dict = Depends(require_resource(write=True)),
) -> ScanReportOut:
    """Importa una cartella scelta dal browser senza esporre percorsi locali."""
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        _get_project_or_404(conn, project_id)
    if not files:
        raise HTTPException(status_code=400, detail=msg("no_file_selected", lang))
    session_dir = config.DATA_DIR / str(project_id) / "uploads" / uuid.uuid4().hex
    session_dir.mkdir(parents=True, exist_ok=False)
    saved = 0
    errors: list[str] = []
    allowed = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".pdf"}
    for upload in files:
        name = Path(upload.filename or "").name
        if not name or Path(name).suffix.lower() not in allowed:
            errors.append(msg("file_unsupported_fmt", lang, name=upload.filename or name))
            continue
        destination = session_dir / f"{saved:06d}_{name}"
        try:
            destination.write_bytes(await upload.read())
            saved += 1
        except OSError as exc:
            errors.append(f"{name}: {exc}")
    report = scanmod.ScanReport(found_files=saved)
    report.errors.extend(errors)
    with connect() as conn:
        for candidate in scanmod.scan_archive(session_dir):
            scanmod.register_candidate(conn, project_id, candidate, session_dir, report, lang=lang)
    return ScanReportOut(**report.to_dict())
