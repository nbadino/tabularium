"""API del lavoro assegnato; identità, permessi e concorrenza verificati lato server."""
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from ..db import connect
from ..services import page_work as work
from ..services.i18n import parse_lang
from ..services import live
from .deps import require_resource

router = APIRouter(tags=['page-work'])

def notify(out: dict, user: dict):
    # Dopo il commit: il client può rileggere subito lo stato aggiornato.
    try:
        live.hub.publish(out['page_id'], {'type': 'annotations_changed', 'page_id': out['page_id'],
            'revision': out['annotation_revision'], 'action': 'workflow',
            'author': {'id': user.get('id'), 'username': user.get('username')}})
    except Exception:  # Il commit resta valido anche se la notifica live fallisce.
        pass
    return out


class AssignmentIn(BaseModel):
    assignee_id: int | None = None
    reviewer_id: int | None = None
    expected_version: int = Field(ge=0)

class SubmissionIn(BaseModel):
    expected_version: int = Field(ge=0)
    annotation_revision: int = Field(ge=0)

class ReviewIn(SubmissionIn):
    decision: Literal['approved', 'changes_requested']
    note: str = Field(default='', max_length=4000)

@router.get('/api/projects/{project_id}/work-members')
def work_members(project_id: int, user: dict = Depends(require_resource())):
    with connect() as conn:
        return {'items': work.candidates(conn, project_id)}

@router.get('/api/pages/{page_id}/work')
def get_work(page_id: int, user: dict = Depends(require_resource())):
    with connect() as conn:
        return work.get_work(conn, page_id, user)

@router.put('/api/pages/{page_id}/work')
def assign(page_id: int, payload: AssignmentIn, request: Request, user: dict = Depends(require_resource(write=True))):
    with connect() as conn:
        out = work.assign(conn, page_id, payload.assignee_id, payload.reviewer_id, payload.expected_version, user,
                           parse_lang(request.headers.get('accept-language')))
    return notify(out, user)

@router.post('/api/pages/{page_id}/work/submit')
def submit(page_id: int, payload: SubmissionIn, request: Request, user: dict = Depends(require_resource(write=True))):
    with connect() as conn:
        out = work.submit(conn, page_id, payload.expected_version, payload.annotation_revision, user,
                           parse_lang(request.headers.get('accept-language')))
    return notify(out, user)

@router.post('/api/pages/{page_id}/work/review')
def review(page_id: int, payload: ReviewIn, request: Request, user: dict = Depends(require_resource(write=True))):
    with connect() as conn:
        out = work.review(conn, page_id, payload.expected_version, payload.annotation_revision, payload.decision,
                           payload.note, user, parse_lang(request.headers.get('accept-language')))
    return notify(out, user)
