"""Dipendenze API condivise per l'accesso alle risorse.

`require_resource(write=...)` risolve dal path la risorsa toccata
(progetto, pagina o blocco) e garantisce l'accesso dell'utente corrente:
- nessuna risorsa nel path → solo autenticazione (router-level).
- `write=False` → basta leggere (owner/editor/viewer/membro).
- `write=True` → servono owner/editor/membro editor.

In modalità locale (`TABULARIUM_AUTH=off`) non controlla nulla.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..db import connect
from ..services import auth as authsvc


def _resolve_project(request: Request, params: dict) -> int | None:
    """Progetto di appartenenza della risorsa nel path, se ce n'è una."""
    if "project_id" in params:
        return int(params["project_id"])
    if "page_id" in params:
        with connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM pages WHERE id=?", (int(params["page_id"]),)
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="pagina non trovata")
        return row["project_id"]
    if "block_id" in params:
        with connect() as conn:
            row = conn.execute(
                "SELECT p.project_id FROM blocks b JOIN pages p ON p.id=b.page_id "
                "WHERE b.id=?",
                (int(params["block_id"]),),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="blocco non trovato")
        return row["project_id"]
    return None


def require_resource(write: bool = False):
    """Factory di dipendenza: accesso alla risorsa del path (lettura o scrittura)."""

    def dep(
        request: Request,
        user: dict = Depends(authsvc.get_current_user),
    ) -> dict:
        project_id = _resolve_project(request, request.path_params)
        if project_id is not None:
            authsvc.require_project_access(project_id, user, write=write)
        return user

    return dep
