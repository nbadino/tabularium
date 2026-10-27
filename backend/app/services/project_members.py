"""Who can see a project, and who owns it (P7.1 of `docs/architecture/program.md`).

The rules are the reason this is a service and not an adapter call: the owner cannot
also be a member, a member cannot be promoted to owner without ceasing to be a member,
and only an active account can be added. They used to live in three router functions
that each opened their own connection.
"""
from __future__ import annotations

from fastapi import HTTPException

from ..adapters import projects as projectstore
from . import audit as auditsvc


def members(project_id: int) -> list[dict]:
    project = projectstore.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    with projectstore.writing() as writer:
        return writer.members(project_id, project["owner_id"])


def candidates(project_id: int) -> list[dict]:
    project = projectstore.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    with projectstore.writing() as writer:
        return writer.candidates(project_id, project["owner_id"])


def set_member(project_id: int, user_id: int, role: str, *, actor: dict) -> dict:
    project = projectstore.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    with projectstore.writing() as writer:
        target = writer.user(user_id)
        if target is None or not target["active"]:
            raise HTTPException(status_code=404, detail="utente attivo non trovato")
        if project["owner_id"] == user_id:
            raise HTTPException(status_code=400, detail="il proprietario non può essere membro")
        writer.put_member(project_id, user_id, role)
        auditsvc.record(
            writer.conn, actor, "project.member_set",
            resource_type="project", resource_id=project_id,
            payload={"user_id": user_id, "role": role},
        )
    return {
        "user_id": user_id,
        "username": target["username"],
        "email": target["email"],
        "active": bool(target["active"]),
        "role": role,
    }


def remove_member(project_id: int, user_id: int, *, actor: dict) -> None:
    if projectstore.get(project_id) is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    with projectstore.writing() as writer:
        if not writer.delete_member(project_id, user_id):
            raise HTTPException(status_code=404, detail="membro non trovato")
        auditsvc.record(
            writer.conn, actor, "project.member_removed",
            resource_type="project", resource_id=project_id, payload={"user_id": user_id},
        )


def transfer_owner(project_id: int, user_id: int, *, actor: dict) -> dict:
    """Move ownership; the previous owner stays on the project as an editor."""
    project = projectstore.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    with projectstore.writing() as writer:
        target = writer.user(user_id)
        if target is None or not target["active"]:
            raise HTTPException(status_code=404, detail="utente attivo non trovato")
        old_owner = project["owner_id"]
        if old_owner != user_id:
            writer.set_owner(project_id, user_id)
            if old_owner is not None:
                writer.put_member(project_id, old_owner, "editor")
            writer.delete_member(project_id, user_id)
            auditsvc.record(
                writer.conn, actor, "project.owner_transferred",
                resource_type="project", resource_id=project_id,
                payload={"from_user_id": old_owner, "to_user_id": user_id},
            )
    return {
        "user_id": target["id"],
        "username": target["username"],
        "email": target["email"],
        "active": True,
        "role": "owner",
    }
