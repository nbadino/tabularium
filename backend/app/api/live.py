"""WebSocket di presenza e notifiche live per lo Studio.

Un canale per pagina: `/api/live/pages/{page_id}`. Serve solo a *leggere* lo
stato della collaborazione (chi è collegato) e a ricevere l'avviso che la
pagina è cambiata; le scritture restano sulla REST (`api/blocks.py`).

L'autenticazione passa dal cookie di sessione (o `Authorization: Bearer`),
come le altre API. L'accesso al progetto è verificato prima di accettare:
chi non può vedere la pagina non entra nella stanza.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..adapters import pages as pagestore
from ..services import auth as authsvc
from ..services import live

router = APIRouter(tags=["live"])

# Codici di chiusura applicativi (4000-4999 è la fascia libera del protocollo).
WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403
WS_NOT_FOUND = 4404


def page_info(page_id: int) -> tuple[int, int] | None:
    """(project_id, annotation_revision) della pagina, o None se non esiste."""
    row = pagestore.get(page_id)
    if row is None:
        return None
    return int(row["project_id"]), int(row["annotation_revision"])


def authorize_page(page_id: int, user: dict | None) -> tuple[int, int, bool]:
    """(project_id, revision, can_write) — solleva 404/403 se negato.

    Estrazione pura dell'autorizzazione, così è verificabile senza aprire un
    socket: il WebSocket la richiama e traduce l'eccezione in un codice di
    chiusura.
    """
    info = page_info(page_id)
    if info is None:
        raise HTTPException(status_code=404, detail="pagina non trovata")
    project_id, revision = info
    level = authsvc.require_project_access(project_id, user, write=False)
    return project_id, revision, level in ("owner", "editor")


def _resolve_user(websocket: WebSocket) -> dict | None:
    if not authsvc.auth_enabled():
        return authsvc.local_user()
    # `resolve_user` legge solo cookie e header, entrambi presenti sul WebSocket.
    user = authsvc.resolve_user(websocket)  # type: ignore[arg-type]
    # Same gate as the HTTP routes: a password still to change opens nothing else.
    if user is not None and user.get("must_change_password"):
        return None
    return user


@router.websocket("/api/live/pages/{page_id}")
async def page_live(websocket: WebSocket, page_id: int) -> None:
    await websocket.accept()
    user = _resolve_user(websocket)
    if user is None:
        await websocket.close(code=WS_UNAUTHORIZED, reason="auth required")
        return
    try:
        project_id, revision, can_write = authorize_page(page_id, user)
    except HTTPException as exc:
        code = WS_NOT_FOUND if exc.status_code == 404 else WS_FORBIDDEN
        await websocket.close(code=code, reason="forbidden")
        return

    identity = str(user.get("id") or user.get("username") or "local")
    conn = live.Connection(
        conn_id=uuid.uuid4().hex,
        page_id=page_id,
        user_id=user.get("id"),
        username=user.get("username") or "local",
        can_write=can_write,
        color=live.color_for(identity),
        ws=websocket,
    )
    participants = await live.hub.register(conn)
    await websocket.send_json(
        {
            "type": "hello",
            "page_id": page_id,
            "project_id": project_id,
            "self": conn.public(),
            "can_write": can_write,
            "revision": revision,
            "participants": participants,
        }
    )
    # Gli altri vedono arrivare il nuovo collega; il nuovo arrivato ha già la
    # lista completa nell'`hello`.
    await live.hub.announce_presence(page_id, exclude=conn.conn_id)

    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except (ValueError, RuntimeError):
                # Payload non JSON o socket già chiuso: si chiude la stanza.
                break
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong", "t": msg.get("t")})
            elif kind == "editing":
                conn.editing = conn.can_write and bool(msg.get("value"))
                await live.hub.broadcast(
                    page_id,
                    {
                        "type": "peer",
                        "conn_id": conn.conn_id,
                        "user_id": conn.user_id,
                        "username": conn.username,
                        "editing": conn.editing,
                    },
                    exclude=conn.conn_id,
                )
            elif kind == "cursor":
                x, y = msg.get("x"), msg.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                    conn.cursor = {"x": float(x), "y": float(y)}
                    await live.hub.broadcast(
                        page_id,
                        {
                            "type": "peer",
                            "conn_id": conn.conn_id,
                            "user_id": conn.user_id,
                            "username": conn.username,
                            "cursor": conn.cursor,
                        },
                        exclude=conn.conn_id,
                    )
    finally:
        remaining = await live.hub.unregister(page_id, conn.conn_id)
        if remaining is not None:
            await live.hub.broadcast(page_id, live.hub.presence_message(page_id))
