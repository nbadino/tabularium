"""API progetti e pagine: CRUD, scansione archivio, metadati, anteprime."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import uuid
import threading

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from .. import config
from ..adapters import projects as projectstore
from ..db import connect  # noqa: F401  (scan, membership, delete: P7.1)
from ..schemas import (
    ConventionItem,
    ConventionsIn,
    ConventionsOut,
    KnownTableHeaderIn,
    KnownTableHeaderOut,
    KnownTableHeadersOut,
    ProjectCreate,
    ProjectMemberIn,
    ProjectMemberOut,
    ProjectOwnerIn,
    ProjectList,
    ProjectOut,
    ScanReportOut,
    StudyProtocolIn,
    StudyProtocolOut,
    TableHintIn,
    TableHintOut,
)
from ..services import pages as pagesvc
from ..services import scan as scanmod
from ..services import corpus as corpusmod
from ..services import pilot as pilotmod
from ..services import auth as authsvc
from ..services import audit as auditsvc
from ..services import project_members as members
from ..services import domain_profiles
from ..services import table_hint as table_hint_svc
from ..services.i18n import msg, parse_lang
from .deps import require_resource

router = APIRouter(
    tags=["projects"],
    dependencies=[Depends(authsvc.get_current_user)],
)

_scan_jobs: dict[str, dict] = {}
_scan_jobs_lock = threading.Lock()


def _run_scan_job(job_id: str, project_id: int, lang: str, confirm_missing: bool) -> None:
    try:
        project = projectstore.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="progetto non trovato")
        archive_dir = project["archive_dir"]

        def progress(index: int, candidate, report) -> None:
            with _scan_jobs_lock:
                _scan_jobs[job_id].update(
                    current=index, current_file=candidate.path.name, report=report.to_dict()
                )

        with _scan_jobs_lock:
            _scan_jobs[job_id].update(phase="files")
        report = scanmod.run_scan(
            project_id,
            archive_dir,
            confirm_missing=confirm_missing,
            lang=lang,
            on_progress=progress,
        )
        with _scan_jobs_lock:
            _scan_jobs[job_id].update(status="done", total=report.found_files, report=report.to_dict())
    except Exception as exc:  # noqa: BLE001
        with _scan_jobs_lock:
            _scan_jobs[job_id].update(status="failed", error=str(exc))


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


def _settings_or_404(project_id: int) -> dict:
    settings = projectstore.settings_json_or_none(project_id)
    if settings is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    return settings


def _get_project_or_404(_conn, project_id: int):
    row = projectstore.get(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    return row


def _project_out(row, summary: dict, user: dict) -> ProjectOut:
    try:
        settings = json.loads(row["settings_json"] or "{}")
    except (TypeError, ValueError):
        settings = {}
    if authsvc.is_local(user) or authsvc.is_admin(user) or row["owner_id"] == user.get("id"):
        level = "owner"
    else:
        level = summary.get("role")
    source = (
        "local" if authsvc.is_local(user)
        else "admin" if authsvc.is_admin(user)
        else "owner" if row["owner_id"] == user.get("id")
        else "member"
    )
    return ProjectOut(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        root_dir=row["root_dir"],
        archive_dir=row["archive_dir"],
        settings_json=settings if isinstance(settings, dict) else {},
        pages_count=int(summary.get("pages_count") or 0),
        created_at=row["created_at"],
        owner_name=summary.get("owner_name"),
        members_count=int(summary.get("members_count") or 0),
        access={
            "role": level,
            "source": source,
            "can_edit": level in ("owner", "editor"),
            "can_manage": level == "owner",
        },
    )


def _project_outs(rows: list, user: dict) -> list[ProjectOut]:
    summaries = projectstore.summaries([int(row["id"]) for row in rows], user.get("id"))
    return [_project_out(row, summaries.get(int(row["id"]), {}), user) for row in rows]


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
    # A server path exposes whatever the process can read, so it is an admin
    # decision, like browsing the server's folders (`/api/system/fs/browse`).
    if payload.archive_dir:
        authsvc.require_role(user, "admin")
    archive_dir = str(Path(payload.archive_dir).expanduser().resolve()) if payload.archive_dir else None
    if archive_dir and not Path(archive_dir).is_dir():
        raise HTTPException(status_code=400, detail="la cartella archivio non esiste")
    project_id = projectstore.create(
        payload.name,
        root_dir=str(config.DATA_DIR),
        archive_dir=archive_dir,
        owner_id=user.get("id"),
    )
    # directory dati del progetto (crops/runs arriveranno nelle milestone successive)
    (config.DATA_DIR / str(project_id)).mkdir(parents=True, exist_ok=True)
    row = _get_project_or_404(None, project_id)
    return _project_outs([row], user)[0]


@router.get("/api/projects", response_model=ProjectList)
def list_projects(user: dict = Depends(authsvc.get_current_user)) -> ProjectList:
    rows = projectstore.list_for_user(
        user.get("id"),
        privileged=authsvc.is_local(user) or authsvc.is_admin(user),
    )
    return ProjectList(items=_project_outs(rows, user))


@router.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> ProjectOut:
    return _project_outs([_get_project_or_404(None, project_id)], _auth)[0]


def _require_project_manager(project_id: int, user: dict) -> str:
    level = authsvc.require_project_access(project_id, user, write=True)
    if level != "owner" and not authsvc.is_admin(user):
        raise HTTPException(status_code=403, detail="solo il proprietario può gestire l'accesso")
    return level


@router.get("/api/projects/{project_id}/members", response_model=list[ProjectMemberOut])
def list_project_members(project_id: int, user: dict = Depends(authsvc.get_current_user)) -> list[ProjectMemberOut]:
    authsvc.require_project_access(project_id, user, write=False)
    rows = members.members(project_id)
    return [ProjectMemberOut(**dict(row)) for row in rows]


@router.get("/api/projects/{project_id}/member-candidates", response_model=list[ProjectMemberOut])
def project_member_candidates(project_id: int, user: dict = Depends(authsvc.get_current_user)) -> list[ProjectMemberOut]:
    _require_project_manager(project_id, user)
    rows = members.candidates(project_id)
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
    member = members.set_member(project_id, user_id, payload.role, actor=user)
    return ProjectMemberOut(**member)


@router.delete("/api/projects/{project_id}/members/{user_id}", status_code=204)
def remove_project_member(project_id: int, user_id: int, user: dict = Depends(authsvc.get_current_user)) -> None:
    _require_project_manager(project_id, user)
    members.remove_member(project_id, user_id, actor=user)



@router.post("/api/projects/{project_id}/owner", response_model=ProjectMemberOut)
def transfer_project_owner(project_id: int, payload: ProjectOwnerIn, user: dict = Depends(authsvc.get_current_user)) -> ProjectMemberOut:
    _require_project_manager(project_id, user)
    member = members.transfer_owner(project_id, payload.user_id, actor=user)
    return ProjectMemberOut(**member)


@router.get("/api/projects/{project_id}/workflow")
def project_workflow(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> dict:
    """Stato operativo e prossimo compito consigliato per il ricercatore."""
    _get_project_or_404(None, project_id)
    counts, next_page = projectstore.workflow_stats(project_id)
    total = sum(counts.values())
    approved = sum(counts.get(s, 0) for s in ("qa", "review", "exported", "approved"))
    return {
        "project_id": project_id,
        "counts": counts,
        "total_pages": total,
        "approved_pages": approved,
        "progress": approved / total if total else 0.0,
        "next_page": next_page,
    }


@router.get("/api/projects/{project_id}/activity")
def project_activity(project_id: int, limit: int = Query(default=30, ge=1, le=200), _auth: dict = Depends(require_resource())) -> dict:
    _get_project_or_404(None, project_id)
    return {"project_id": project_id, "items": projectstore.activity(project_id, limit)}


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
        _get_project_or_404(None, project_id)
        eval_actions: dict[int, list[str]] = {}
        eval_root = config.DATA_DIR / str(project_id) / "eval"
        reports = sorted(eval_root.glob("eval_*/report.json"), reverse=True) if eval_root.exists() else []
        if reports:
            try:
                latest = json.loads(reports[0].read_text(encoding="utf-8"))
                eval_actions = {int(p["page_id"]): list(p.get("actions", [])) for p in latest.get("pages", []) if p.get("actions")}
            except (OSError, TypeError, ValueError, KeyError):
                eval_actions = {}
        rows = projectstore.annotation_queue(project_id, limit)
        items = []
        for row in rows:
            status = row["status"]
            low_confidence = 0
            for source in projectstore.prefill_sources(row["id"]):
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
    _settings_or_404(project_id)
    return corpusmod.corpus_map(project_id)


@router.get("/api/projects/{project_id}/study-protocol", response_model=StudyProtocolOut)
def get_study_protocol(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> StudyProtocolOut:
    settings = _settings_or_404(project_id)
    protocol = settings.get("study_protocol") or {}
    if protocol.get("domain_profile"):
        protocol = {**protocol, "domain_profile": domain_profiles.canonical_id(protocol["domain_profile"])}
    return StudyProtocolOut(**protocol) if protocol else StudyProtocolOut(updated_at=datetime.now(timezone.utc).isoformat())


@router.put("/api/projects/{project_id}/study-protocol", response_model=StudyProtocolOut)
def put_study_protocol(
    project_id: int,
    payload: StudyProtocolIn,
    _auth: dict = Depends(require_resource(write=True)),
) -> StudyProtocolOut:
    now = datetime.now(timezone.utc).isoformat()
    settings = _settings_or_404(project_id)
    previous = settings.get("study_protocol") or {}
    version = int(previous.get("version", 0)) + 1
    protocol = {**payload.model_dump(), "version": version, "updated_at": now}
    history = settings.setdefault("study_protocol_history", [])
    history.append(protocol)
    settings["study_protocol"] = protocol
    projectstore.put_settings(project_id, settings)
    return StudyProtocolOut(**protocol)


@router.post("/api/projects/{project_id}/gold-set")
def set_gold_set(
    project_id: int,
    page_ids: list[int],
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    """Protegge un campione di pagine invisibile al tuning."""
    valid = projectstore.valid_page_ids(project_id, page_ids)
    settings = _settings_or_404(project_id)
    protocol = settings.setdefault("study_protocol", {})
    protocol["gold_pages"] = valid
    # Un campione già salvato non può diventare contemporaneamente tuning
    # e gold: proteggere una pagina la rimuove dal pilot persistito.
    pilot_pages = {
        int(value) for value in protocol.get("pilot_pages", [])
        if str(value).lstrip("-").isdigit()
    }
    protocol["pilot_pages"] = sorted(pilot_pages - set(valid))
    protocol["version"] = int(protocol.get("version", 0)) + 1
    protocol["updated_at"] = datetime.now(timezone.utc).isoformat()
    projectstore.put_settings(project_id, settings)
    return {
        "project_id": project_id,
        "gold_pages": valid,
        "pilot_pages": protocol["pilot_pages"],
        "count": len(valid),
        "protected": True,
    }


@router.get("/api/projects/{project_id}/qa-report")
def qa_report(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> dict:
    """Riepilogo revisioni e conteggio degli errori ricorrenti."""
    _settings_or_404(project_id)
    rows = projectstore.review_inputs(project_id)
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
    target: int = Query(default=40, ge=30, le=50),
    seed: int = Query(default=42, ge=0),
    _auth: dict = Depends(require_resource()),
) -> dict:
    with connect() as conn:
        _get_project_or_404(None, project_id)
    return pilotmod.sample_pilot(project_id, target, seed)


@router.post("/api/projects/{project_id}/pilot-sample/save")
def save_pilot_sample(
    project_id: int,
    page_ids: list[int],
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    valid = projectstore.valid_page_ids(project_id, page_ids)
    settings = _settings_or_404(project_id)
    protocol = settings.setdefault("study_protocol", {})
    gold_pages = {
        int(value) for value in protocol.get("gold_pages", [])
        if str(value).lstrip("-").isdigit()
    }
    gold_excluded = len(set(valid) & gold_pages)
    valid = [page_id for page_id in valid if page_id not in gold_pages]
    protocol["pilot_pages"] = valid
    protocol["updated_at"] = datetime.now(timezone.utc).isoformat()
    projectstore.put_settings(project_id, settings)
    return {
        "project_id": project_id,
        "pilot_pages": valid,
        "gold_excluded": gold_excluded,
        "count": len(valid),
    }


@router.get("/api/projects/{project_id}/conventions", response_model=ConventionsOut)
def get_conventions(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> ConventionsOut:
    settings = _settings_or_404(project_id)
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
    settings = _settings_or_404(project_id)
    settings["conventions"] = dumped
    projectstore.put_settings(project_id, settings)
    return ConventionsOut(conventions=payload.conventions)


@router.get("/api/projects/{project_id}/table-hint", response_model=TableHintOut)
def get_table_hint(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> TableHintOut:
    """Hint di schema tabella del progetto e se il modello attivo lo accetta."""
    return _table_hint_out(_settings_or_404(project_id))


@router.put("/api/projects/{project_id}/table-hint", response_model=TableHintOut)
def put_table_hint(
    project_id: int,
    payload: TableHintIn,
    _auth: dict = Depends(require_resource(write=True)),
) -> TableHintOut:
    hint = table_hint_svc.clean_table_hint(payload.hint)
    settings = _settings_or_404(project_id)
    if hint:
        settings["table_hint"] = hint
    else:
        # Vuoto = nessun hint: si cancella la chiave invece di salvare "".
        settings.pop("table_hint", None)
    projectstore.put_settings(project_id, settings)
    return _table_hint_out(settings)


def _table_hint_out(settings: dict) -> TableHintOut:
    """Risposta condivisa da GET e PUT: lo stato del modello attivo aiuta la UI
    a dire che il hint è ignorato dai motori a prompt di protocollo."""
    from ..services import inference as inference_svc  # noqa: PLC0415 - evita import ciclico

    adapter_id = inference_svc.get_inference_config().get("adapter_id")
    display: str | None = None
    accepts = False
    if adapter_id:
        try:
            from ..services.model_adapters import get_adapter  # noqa: PLC0415

            capabilities = get_adapter(adapter_id).capabilities
            display = capabilities.display_name
            accepts = bool(capabilities.accepts_prompt_hint)
        except ValueError:
            display = None
    return TableHintOut(
        hint=table_hint_svc.get_table_hint(settings),
        active_model=display,
        accepts_hint=accepts,
    )


@router.get("/api/projects/{project_id}/table-headers", response_model=KnownTableHeadersOut)
def get_table_headers(
    project_id: int,
    _auth: dict = Depends(require_resource()),
) -> KnownTableHeadersOut:
    settings = _settings_or_404(project_id)
    items = settings.get("known_table_headers") or []
    valid = [KnownTableHeaderOut(**item) for item in items if isinstance(item, dict)]
    return KnownTableHeadersOut(items=valid)


@router.post(
    "/api/projects/{project_id}/table-headers",
    response_model=KnownTableHeaderOut,
    status_code=201,
)
def save_table_header(
    project_id: int,
    payload: KnownTableHeaderIn,
    _auth: dict = Depends(require_resource(write=True)),
) -> KnownTableHeaderOut:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="nome intestazione vuoto")
    now = datetime.now(timezone.utc).isoformat()
    item = KnownTableHeaderOut(
        id=uuid.uuid4().hex,
        name=name,
        rows=payload.rows,
        cols=payload.cols,
        cells=payload.cells,
        created_at=now,
        updated_at=now,
    )
    settings = _settings_or_404(project_id)
    items = [entry for entry in settings.get("known_table_headers", []) if isinstance(entry, dict)]
    # Una libreria di progetto deve restare leggera e facile da consultare.
    items = [entry for entry in items if entry.get("name", "").casefold() != name.casefold()]
    items.append(item.model_dump())
    settings["known_table_headers"] = items[-50:]
    projectstore.put_settings(project_id, settings)
    return item


@router.delete("/api/projects/{project_id}/table-headers/{header_id}")
def delete_table_header(
    project_id: int,
    header_id: str,
    _auth: dict = Depends(require_resource(write=True)),
) -> dict:
    settings = _settings_or_404(project_id)
    items = [entry for entry in settings.get("known_table_headers", []) if isinstance(entry, dict)]
    kept = [entry for entry in items if entry.get("id") != header_id]
    if len(kept) == len(items):
        raise HTTPException(status_code=404, detail="intestazione non trovata")
    settings["known_table_headers"] = kept
    projectstore.put_settings(project_id, settings)
    return {"deleted": True, "id": header_id}


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
    # Cancellare un progetto è irreversibile: solo il proprietario (o un admin,
    # che ha livello "owner") può farlo, non un editor invitato.
    if authsvc.require_project_access(project_id, _auth, write=True) != "owner":
        raise HTTPException(
            status_code=403,
            detail="solo il proprietario del progetto può eliminarlo",
        )
    project = _get_project_or_404(None, project_id)
    # remove the page thumbnails before the rows that name them
    for page_id in projectstore.page_ids(project_id):
        for path in (pagesvc.thumb_path(page_id), pagesvc.preview_path(page_id)):
            if path.exists():
                path.unlink()
    projectstore.delete(project_id)
    # directory di progetto (se vuota o creata da noi)
    project_dir = config.DATA_DIR / str(project_id)
    if project_dir.exists() and project_dir.is_dir():
        try:
            project_dir.rmdir()  # solo se vuota
        except OSError:
            pass
    return {"deleted": True, "project": project["name"]}


@router.post("/api/projects/{project_id}/scan/start")
def start_scan(project_id: int, request: Request, confirm_missing: bool = Query(default=False), _auth: dict = Depends(require_resource(write=True))):
    lang = parse_lang(request.headers.get("accept-language"))
    with connect() as conn:
        project = _get_project_or_404(None, project_id)
        if not project["archive_dir"] or not Path(project["archive_dir"]).is_dir():
            raise HTTPException(status_code=400, detail="archive_dir non valida")
    job_id = uuid.uuid4().hex
    with _scan_jobs_lock:
        _scan_jobs[job_id] = {"id": job_id, "project_id": project_id, "status": "running", "current": 0, "total": 0, "phase": "discovering", "report": None}
    threading.Thread(target=_run_scan_job, args=(job_id, project_id, lang, confirm_missing), daemon=True).start()
    return {"job_id": job_id}


@router.get("/api/projects/{project_id}/scan/jobs/{job_id}")
def scan_job(project_id: int, job_id: str, _auth: dict = Depends(require_resource(write=False))):
    with _scan_jobs_lock:
        job = _scan_jobs.get(job_id)
    # The job must belong to the project the caller was authorised on.
    if not job or job.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="scan job non trovato")
    return job


@router.post("/api/projects/{project_id}/scan", response_model=ScanReportOut)
def scan_project(
    project_id: int,
    request: Request,
    confirm_missing: bool = Query(default=False),
    _auth: dict = Depends(require_resource(write=True)),
) -> ScanReportOut:
    lang = parse_lang(request.headers.get("accept-language"))
    project = _get_project_or_404(None, project_id)
    archive_dir = project["archive_dir"]
    if not archive_dir or not Path(archive_dir).is_dir():
        raise HTTPException(status_code=400, detail="archive_dir non valida")
    report = scanmod.run_scan(
        project_id, archive_dir, confirm_missing=confirm_missing, lang=lang
    )
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
        _get_project_or_404(None, project_id)
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
    scanmod.register_uploads(project_id, session_dir, report, lang=lang)
    return ScanReportOut(**report.to_dict())
