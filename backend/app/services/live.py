"""Canale live dello Studio: presenza e notifica delle modifiche.

Il WebSocket è **solo push**. Le scritture restano sulla REST: validazioni,
audit e revisione ottimistica vivono lì, e questo modulo non conosce il
database. Serve a due cose:

- dire chi è collegato alla pagina (presenza, "chi sta scrivendo");
- avvisare gli *altri* che la pagina è cambiata, così il loro canvas si
  riallinea senza che debbano ricaricare o sovrascrivere.

Gira in memoria, quindi vale per il **singolo processo** del backend — che è
lo scenario supportato (un processo dashboard). Con più worker servirebbe un
broker esterno: il confine è qui, in `publish`.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any

# Colori distinti, leggibili sul fondo chiaro dello Studio. Il colore di un
# utente è stabile (derivato dall'identità), così la stessa persona ha sempre
# la stessa tinta fra una sessione e l'altra.
_PALETTE = (
    "#2563eb",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#db2777",
    "#0891b2",
    "#65a30d",
    "#dc2626",
    "#4f46e5",
    "#0d9488",
)


def color_for(seed: str | None) -> str:
    """Colore stabile per un'identità (id o username)."""
    if not seed:
        return _PALETTE[0]
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return _PALETTE[int(digest[:8], 16) % len(_PALETTE)]


@dataclass
class Connection:
    """Una connessione WebSocket aperta su una pagina."""

    conn_id: str
    page_id: int
    user_id: int | None
    username: str
    can_write: bool
    color: str
    ws: Any
    editing: bool = False
    cursor: dict | None = field(default=None)

    def public(self) -> dict:
        """Rappresentazione condivisa: mai il socket."""
        return {
            "conn_id": self.conn_id,
            "user_id": self.user_id,
            "username": self.username,
            "color": self.color,
            "editing": self.editing,
            "can_write": self.can_write,
        }


class LiveHub:
    """Stanze per pagina. Tutte le mutazioni girano sul loop; `publish` è
    l'unica porta pensata per essere chiamata da un thread di lavoro."""

    def __init__(self) -> None:
        self._rooms: dict[int, dict[str, Connection]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _room(self, page_id: int) -> dict[str, Connection]:
        return self._rooms.setdefault(page_id, {})

    def participants(self, page_id: int) -> list[dict]:
        return [conn.public() for conn in self._room(page_id).values()]

    def active_rooms(self) -> int:
        return len(self._rooms)

    def presence_message(self, page_id: int) -> dict:
        return {
            "type": "presence",
            "page_id": page_id,
            "participants": self.participants(page_id),
        }

    async def register(self, conn: Connection) -> list[dict]:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        self._room(conn.page_id)[conn.conn_id] = conn
        return self.participants(conn.page_id)

    async def unregister(self, page_id: int, conn_id: str) -> list[dict] | None:
        room = self._rooms.get(page_id)
        if room is None:
            return None
        room.pop(conn_id, None)
        if not room:
            self._rooms.pop(page_id, None)
            return None
        return self.participants(page_id)

    async def broadcast(self, page_id: int, message: dict, exclude: str | None = None) -> None:
        room = self._rooms.get(page_id)
        if not room:
            return
        dead: list[str] = []
        for conn_id, conn in list(room.items()):
            if conn_id == exclude:
                continue
            try:
                await conn.ws.send_json(message)
            except Exception:  # noqa: BLE001 — un socket morto non ferma la stanza
                dead.append(conn_id)
        for conn_id in dead:
            room.pop(conn_id, None)
        if not room:
            self._rooms.pop(page_id, None)

    async def announce_presence(self, page_id: int, exclude: str | None = None) -> None:
        await self.broadcast(page_id, self.presence_message(page_id), exclude=exclude)

    def publish(self, page_id: int, message: dict) -> None:
        """Consegna un messaggio da un thread qualsiasi (endpoint sincrono).

        Non solleva mai: un evento perso è preferibile a un 500 sulla scrittura
        che l'ha generato.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._dispatch, page_id, message)
        except RuntimeError:
            pass

    def _dispatch(self, page_id: int, message: dict) -> None:
        try:
            asyncio.ensure_future(self.broadcast(page_id, message))
        except RuntimeError:
            pass


hub = LiveHub()
