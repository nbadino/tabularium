"""Assegnazione e passaggio annotatore→revisore, con revisione esplicita della versione letta."""
from __future__ import annotations
from fastapi import HTTPException
from ..adapters import assignment as workstore
from . import auth, audit, pages
from .i18n import msg


def fail(key: str, lang: str, status: int = 409):
    raise HTTPException(status_code=status, detail=msg(key, lang))


def managed(conn, project_id: int, user: dict) -> bool:
    owner_id = workstore.project_owner(project_id, conn)
    return auth.is_local(user) or auth.is_admin(user) or owner_id == user.get("id")


def candidates(conn, project_id: int) -> list[dict]:
    return workstore.candidates(project_id, conn)


def project_work(conn, project_id: int, user: dict, page_id: int | None = None) -> dict[int, dict]:
    rows = workstore.work_rows(project_id, page_id=page_id, conn=conn)
    can_assign = managed(conn, project_id, user)
    uid = user.get('id')
    out = {}
    for row in rows:
        item = dict(row)
        # Un account disattivato o rimosso dal progetto non può essere usato
        # per aggirare l'accesso: le azioni richiedono sempre require_resource.
        item.update(can_assign=can_assign,
                    can_submit=uid is not None and uid == row['assignee_id'] and row['reviewer_id'] is not None and row['state'] in ('annotation', 'changes_requested'),
                    can_review=uid is not None and uid == row['reviewer_id'] and uid != row['submitted_by'] and uid != row['assignee_id'] and row['state'] == 'pending',
                    changed_since_submission=row['submitted_revision'] is not None and row['annotation_revision'] != row['submitted_revision'])
        out[row['page_id']] = item
    return out


def get_work(conn, page_id: int, user: dict) -> dict:
    page = workstore.page(page_id, conn)
    if page is None:
        raise HTTPException(404, 'pagina non trovata')
    item = project_work(conn, page['project_id'], user, page_id).get(page_id)
    return item or dict(page_id=page_id, assignee_id=None, reviewer_id=None, assignee_name=None,
                       reviewer_name=None, state='annotation', version=0, note='', submitted_revision=None,
                       annotation_revision=page['annotation_revision'], changed_since_submission=False,
                       can_assign=managed(conn, page['project_id'], user), can_submit=False, can_review=False)


def check_version(item: dict, expected: int, lang: str):
    if item['version'] != expected:
        fail('work_conflict', lang)


def configured(conn, page_id: int) -> bool:
    row = workstore.roles(page_id, conn)
    return bool(row and (row["assignee_id"] is not None or row["reviewer_id"] is not None))


def assign(conn, page_id: int, assignee_id: int | None, reviewer_id: int | None, expected: int, user: dict, lang: str):
    conn.execute('BEGIN IMMEDIATE')
    page = workstore.page(page_id, conn)
    item = get_work(conn, page_id, user)
    if not item['can_assign']:
        fail('work_manager_only', lang, 403)
    check_version(item, expected, lang)
    if assignee_id is not None and assignee_id == reviewer_id:
        fail('work_distinct', lang, 422)
    eligible = {u['id'] for u in candidates(conn, page['project_id'])}
    if any(uid is not None and uid not in eligible for uid in (assignee_id, reviewer_id)):
        fail('work_member_required', lang, 422)
    # Non si abbandona una revisione approvata/in corso cambiando i nomi.
    # Riassegnare a stessi nomi è idempotente; cambiare revisore richiede un nuovo invio.
    if item['assignee_id'] == assignee_id and item['reviewer_id'] == reviewer_id:
        return item
    workstore.upsert(conn, page_id, assignee_id, reviewer_id)
    workstore.reopen_if_settled(conn, page_id)
    audit.record(conn, user, 'page.assigned', resource_type='page', resource_id=page_id,
                 payload={'assignee_id': assignee_id, 'reviewer_id': reviewer_id})
    return get_work(conn, page_id, user)


def ready(conn, page_id: int, lang: str, *, require_verified_cells: bool = True):
    result = pages.compute_readiness(page_id, lang=lang, require_verified_cells=require_verified_cells, conn=conn)
    if not result['ready']:
        raise HTTPException(409, {'message': msg('page_not_ready', lang), **result})


def submit(conn, page_id: int, expected: int, revision: int, user: dict, lang: str):
    conn.execute('BEGIN IMMEDIATE')
    item = get_work(conn, page_id, user)
    check_version(item, expected, lang)
    if not item['can_submit']:
        fail('work_annotator_only', lang, 403)
    if item['annotation_revision'] != revision:
        fail('work_revision_changed', lang)
    page = workstore.page(page_id, conn)
    if item['reviewer_id'] not in {u['id'] for u in candidates(conn, page['project_id'])}:
        fail('work_member_required', lang)
    ready(conn, page_id, lang, require_verified_cells=False)
    workstore.mark_pending(conn, page_id, submitted_by=user.get("id"), revision=revision)
    audit.record(conn, user, 'page.submitted', resource_type='page', resource_id=page_id, payload={'revision': revision})
    return get_work(conn, page_id, user)


def review(conn, page_id: int, expected: int, revision: int, decision: str, note: str, user: dict, lang: str):
    conn.execute('BEGIN IMMEDIATE')
    item = get_work(conn, page_id, user)
    check_version(item, expected, lang)
    if not item['can_review']:
        fail('work_reviewer_only', lang, 403)
    if item['annotation_revision'] != revision:
        fail('work_revision_changed', lang)
    if decision == 'changes_requested' and not note.strip():
        fail('work_note_required', lang, 422)
    accepted = {}
    if decision == 'approved':
        accepted = pages.confirm_page_content(page_id, confirmed_only=True, conn=conn)
        revision = accepted['annotation_revision']
        ready(conn, page_id, lang)
    workstore.add_review(
        conn, page_id, reviewer=user["username"], passed=decision == "approved", note=note.strip()
    )
    workstore.settle_review(
        conn,
        page_id,
        state=decision,
        note=note.strip(),
        approved_revision=revision if decision == "approved" else None,
    )
    audit.record(conn, user, 'page.reviewed', resource_type='page', resource_id=page_id,
                 payload={'decision': decision, 'revision': revision, 'note': note.strip(), **accepted})
    return get_work(conn, page_id, user)
