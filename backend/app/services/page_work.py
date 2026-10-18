"""Assegnazione e passaggio annotatore→revisore, con revisione esplicita della versione letta."""
from __future__ import annotations
from fastapi import HTTPException
from . import auth, audit, pages
from .i18n import msg


def fail(key: str, lang: str, status: int = 409):
    raise HTTPException(status_code=status, detail=msg(key, lang))


def managed(conn, project_id: int, user: dict) -> bool:
    project = conn.execute('SELECT owner_id FROM projects WHERE id=?', (project_id,)).fetchone()
    return auth.is_local(user) or auth.is_admin(user) or bool(project and project['owner_id'] == user.get('id'))


def candidates(conn, project_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT DISTINCT u.id, u.username FROM users u LEFT JOIN project_members m "
        "ON m.user_id=u.id AND m.project_id=? JOIN projects p ON p.id=? "
        "WHERE u.active=1 AND (u.role='admin' OR u.id=p.owner_id OR m.role='editor') ORDER BY u.username",
        (project_id, project_id))]


def project_work(conn, project_id: int, user: dict, page_id: int | None = None) -> dict[int, dict]:
    rows = conn.execute(
        'SELECT w.*, a.username AS assignee_name, r.username AS reviewer_name, p.annotation_revision '
        'FROM page_work w JOIN pages p ON p.id=w.page_id '
        'LEFT JOIN users a ON a.id=w.assignee_id LEFT JOIN users r ON r.id=w.reviewer_id '
        'WHERE p.project_id=?' + (' AND w.page_id=?' if page_id is not None else ''),
        (project_id, page_id) if page_id is not None else (project_id,)).fetchall()
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
    page = conn.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
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
    row = conn.execute('SELECT assignee_id,reviewer_id FROM page_work WHERE page_id=?', (page_id,)).fetchone()
    return bool(row and (row['assignee_id'] is not None or row['reviewer_id'] is not None))


def assign(conn, page_id: int, assignee_id: int | None, reviewer_id: int | None, expected: int, user: dict, lang: str):
    conn.execute('BEGIN IMMEDIATE')
    page = conn.execute('SELECT * FROM pages WHERE id=?', (page_id,)).fetchone()
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
    conn.execute("INSERT INTO page_work(page_id,assignee_id,reviewer_id) VALUES(?,?,?) "
                 "ON CONFLICT(page_id) DO UPDATE SET assignee_id=excluded.assignee_id, reviewer_id=excluded.reviewer_id, "
                 "state='annotation', version=page_work.version+1, submitted_by=NULL, submitted_revision=NULL, "
                 "approved_revision=NULL, note='', updated_at=datetime('now')", (page_id, assignee_id, reviewer_id))
    conn.execute("UPDATE pages SET status='annotated' WHERE id=? AND status IN ('qa','approved','exported')", (page_id,))
    audit.record(conn, user, 'page.assigned', resource_type='page', resource_id=page_id,
                 payload={'assignee_id': assignee_id, 'reviewer_id': reviewer_id})
    return get_work(conn, page_id, user)


def ready(conn, page_id: int, lang: str):
    result = pages.compute_readiness(conn, page_id, lang=lang)
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
    page = conn.execute('SELECT project_id FROM pages WHERE id=?', (page_id,)).fetchone()
    if item['reviewer_id'] not in {u['id'] for u in candidates(conn, page['project_id'])}:
        fail('work_member_required', lang)
    ready(conn, page_id, lang)
    conn.execute("UPDATE page_work SET state='pending', submitted_by=?, submitted_revision=?, approved_revision=NULL, "
                 "version=version+1, note='', updated_at=datetime('now') WHERE page_id=?", (user.get('id'), revision, page_id))
    conn.execute("UPDATE pages SET status='qa' WHERE id=?", (page_id,))
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
    if decision == 'approved':
        ready(conn, page_id, lang)
    conn.execute("INSERT INTO page_reviews(page_id,reviewer,status,notes) VALUES(?,?,?,?)",
                 (page_id, user['username'], 'pass' if decision == 'approved' else 'fail', note.strip()))
    conn.execute("UPDATE page_work SET state=?, note=?, approved_revision=?, version=version+1, updated_at=datetime('now') WHERE page_id=?",
                 (decision, note.strip(), revision if decision == 'approved' else None, page_id))
    conn.execute('UPDATE pages SET status=? WHERE id=?', ('approved' if decision == 'approved' else 'review', page_id))
    audit.record(conn, user, 'page.reviewed', resource_type='page', resource_id=page_id,
                 payload={'decision': decision, 'revision': revision, 'note': note.strip()})
    return get_work(conn, page_id, user)
