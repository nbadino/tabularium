"""Test del canale live: hub di presenza, broadcast e pubblicazione thread-safe.

La suite usa il client ASGI di ``conftest`` che non espone ``websocket_connect``;
il hub e l'autorizzazione sono perciò verificati direttamente, con socket finti
per il trasporto.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from app import config
from app.api import live as liveapi
from app.db import connect, init_db
from app.services import live


class FakeWS:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("socket chiuso")
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _clean_db():
    """DB pulito prima di ogni test: evita collisioni di username/progetti."""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM project_members")
        conn.execute("DELETE FROM projects")  # cascade su pages → blocks → tables
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
    yield


def _conn(conn_id: str, ws: FakeWS, user_id: int | None = 1, page_id: int = 1) -> live.Connection:
    return live.Connection(
        conn_id=conn_id,
        page_id=page_id,
        user_id=user_id,
        username=conn_id,
        can_write=True,
        color="#000000",
        ws=ws,
    )


# --- colore stabile ------------------------------------------------------------
def test_color_for_is_stable_and_identity_dependent():
    assert live.color_for("42") == live.color_for("42")
    assert live.color_for(None) == live.color_for("")
    assert live.color_for("alice") != live.color_for("bob")


# --- hub ------------------------------------------------------------------------
def test_hub_register_broadcast_unregister():
    async def scenario() -> None:
        hub = live.LiveHub()
        a_ws, b_ws = FakeWS(), FakeWS()
        await hub.register(_conn("a", a_ws))
        parts = await hub.register(_conn("b", b_ws))
        assert {p["conn_id"] for p in parts} == {"a", "b"}

        await hub.broadcast(1, {"type": "x"})
        assert len(a_ws.sent) == 1 and len(b_ws.sent) == 1

        # `exclude` non tocca il socket escluso
        await hub.broadcast(1, {"type": "y"}, exclude="a")
        assert len(a_ws.sent) == 1 and len(b_ws.sent) == 2

        remaining = await hub.unregister(1, "a")
        assert [p["conn_id"] for p in remaining] == ["b"]
        assert await hub.unregister(1, "b") is None
        assert hub.active_rooms() == 0

    asyncio.run(scenario())


def test_hub_drops_dead_sockets():
    async def scenario() -> None:
        hub = live.LiveHub()
        good, dead = FakeWS(), FakeWS(fail=True)
        await hub.register(_conn("good", good))
        await hub.register(_conn("dead", dead))
        await hub.broadcast(1, {"type": "x"})
        assert [p["conn_id"] for p in hub.participants(1)] == ["good"]

    asyncio.run(scenario())


def test_publish_is_threadsafe():
    async def scenario() -> None:
        hub = live.LiveHub()
        hub.bind_loop(asyncio.get_running_loop())
        conn = _conn("a", FakeWS())
        await hub.register(conn)

        worker = threading.Thread(target=lambda: hub.publish(1, {"type": "n"}))
        worker.start()
        worker.join()
        await asyncio.sleep(0.05)
        assert conn.ws.sent == [{"type": "n"}]

    asyncio.run(scenario())


def test_publish_without_loop_is_noop():
    # Nessun loop legato: la chiamata non deve sollevare.
    live.LiveHub().publish(1, {"type": "n"})


# --- autorizzazione della pagina ------------------------------------------------
def _seed_page(owner_id: int | None, project_name: str = "p") -> tuple[int, int]:
    with connect() as conn:
        project = conn.execute(
            "INSERT INTO projects (name, root_dir, archive_dir, settings_json, owner_id) "
            "VALUES (?, '/tmp', '/tmp', '{}', ?)",
            (project_name, owner_id),
        ).lastrowid
        page = conn.execute(
            "INSERT INTO pages (project_id, rel_path, abs_path, width, height) "
            "VALUES (?, 'a.jpg', '/tmp/a.jpg', 100, 100)",
            (project,),
        ).lastrowid
    return int(project), int(page)


def test_authorize_page_local_mode_allows():
    init_db()
    _, page = _seed_page(None)
    project_id, revision, can_write = liveapi.authorize_page(page, {"is_local": True})
    assert can_write is True
    assert revision == 0
    assert project_id > 0


def test_authorize_page_missing_page_is_404():
    init_db()
    with pytest.raises(Exception) as exc:
        liveapi.authorize_page(999999, {"is_local": True})
    assert getattr(exc.value, "status_code", None) == 404


def test_authorize_page_hides_foreign_project(monkeypatch):
    init_db()
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    with connect() as conn:
        owner = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role) VALUES ('owner', 'h', 's', 'editor')"
        ).lastrowid
        other = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role) VALUES ('other', 'h', 's', 'editor')"
        ).lastrowid
    _, page = _seed_page(int(owner))

    # Il proprietario entra e può scrivere.
    project_id, _, can_write = liveapi.authorize_page(page, {"id": int(owner), "username": "owner", "role": "editor"})
    assert can_write is True

    # Un estraneo vede 404: l'esistenza del progetto resta nascosta.
    with pytest.raises(Exception) as exc:
        liveapi.authorize_page(page, {"id": int(other), "username": "other", "role": "editor"})
    assert getattr(exc.value, "status_code", None) == 404


def test_authorize_page_viewer_cannot_write(monkeypatch):
    init_db()
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    with connect() as conn:
        owner = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role) VALUES ('owner2', 'h', 's', 'editor')"
        ).lastrowid
        viewer = conn.execute(
            "INSERT INTO users (username, password_hash, salt, role) VALUES ('viewer2', 'h', 's', 'viewer')"
        ).lastrowid
    project, page = _seed_page(int(owner))
    with connect() as conn:
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, 'viewer')",
            (project, int(viewer)),
        )
    _, _, can_write = liveapi.authorize_page(page, {"id": int(viewer), "username": "viewer2", "role": "viewer"})
    assert can_write is False
