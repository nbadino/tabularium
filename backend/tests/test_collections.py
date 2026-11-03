"""Collections inside a project (docs/COLLECTIONS.md).

A page belongs to at most one collection; `collection_id IS NULL` is the declared
state "no collection" (`none` in the API). Deleting a collection always says what
happens to its pages.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db
from app.db import connect, init_db
from app.main import app
from app.services import pages as pagesvc
from app.services import users as usersvc

PASSWORD = "password123"


@pytest.fixture()
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def _project(client, tmp_path, n: int = 3) -> tuple[int, list[int]]:
    for i in range(n):
        Image.new("RGB", (40, 60), "white").save(tmp_path / f"p{i}.png")
    files = [("files", (f"p{i}.png", (tmp_path / f"p{i}.png").read_bytes(), "image/png")) for i in range(n)]
    pid = client.post("/api/projects", json={"name": f"Lloyds {tmp_path.name}"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/import-upload", files=files)
    assert r.status_code == 200, r.text
    pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
    return pid, [p["id"] for p in pages]


def test_migration_adds_collections_and_nullable_page_column():
    init_db()
    with connect() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "collections" in tables
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(pages)")}
        assert "collection_id" in cols and cols["collection_id"]["notnull"] == 0
    assert int(db.SCHEMA_VERSION) >= 21


def test_migration_from_v20_keeps_pages_without_collection():
    config.ensure_dirs()
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    init_db()
    with connect() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_pages_collection")
        conn.execute("ALTER TABLE pages DROP COLUMN collection_id")
        conn.execute("DROP TABLE collections")
        conn.execute("UPDATE meta SET value='20' WHERE key='schema_version'")
        cur = conn.execute("INSERT INTO projects (name, root_dir) VALUES ('old', '/tmp')")
        conn.execute(
            "INSERT INTO pages (project_id, rel_path, abs_path) VALUES (?, 'a.png', '/tmp/a.png')",
            (cur.lastrowid,),
        )
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT collection_id FROM pages WHERE rel_path='a.png'").fetchone()
        assert row["collection_id"] is None
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == db.SCHEMA_VERSION
    backups = list((config.ROOT_DIR / "backups").glob("*pre-migration-v20*"))
    assert backups, "the pre-migration backup must exist"


def test_create_rename_and_unique_names(client, tmp_path):
    pid, _ = _project(client, tmp_path, 1)
    r = client.post(f"/api/projects/{pid}/collections", json={"name": "  Registri "})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["name"] == "Registri"
    assert client.post(f"/api/projects/{pid}/collections", json={"name": "registri"}).status_code == 409
    assert client.post(f"/api/projects/{pid}/collections", json={"name": "   "}).status_code == 422
    r = client.patch(f"/api/projects/{pid}/collections/{cid}", json={"name": "Registri navali"})
    assert r.status_code == 200 and r.json()["name"] == "Registri navali"


def test_move_pages_filter_and_counts(client, tmp_path):
    pid, ids = _project(client, tmp_path, 3)
    cid = client.post(f"/api/projects/{pid}/collections", json={"name": "Lettere"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": ids[:2], "collection_id": cid})
    assert r.status_code == 200 and r.json()["moved"] == 2

    inside = client.get(f"/api/projects/{pid}/pages", params={"collection": cid}).json()
    assert sorted(p["id"] for p in inside["items"]) == sorted(ids[:2])
    assert all(p["collection_id"] == cid for p in inside["items"])
    outside = client.get(f"/api/projects/{pid}/pages", params={"collection": "none"}).json()
    assert [p["id"] for p in outside["items"]] == [ids[2]]
    assert client.get(f"/api/projects/{pid}/pages", params={"collection": "abc"}).status_code == 400

    listing = client.get(f"/api/projects/{pid}/collections").json()
    row = next(c for c in listing["collections"] if c["id"] == cid)
    assert row["pages"] == 2 and row["by_status"] == {"new": 2}
    assert listing["unassigned"]["pages"] == 1
    assert listing["total"]["pages"] == 3

    # back to "no collection"
    r = client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": [ids[0]], "collection_id": None})
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}/collections").json()["unassigned"]["pages"] == 2


def test_cannot_move_across_projects(client, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, ids_a = _project(client, tmp_path / "a", 1)
    b, ids_b = _project(client, tmp_path / "b", 1)
    cid_b = client.post(f"/api/projects/{b}/collections", json={"name": "X"}).json()["id"]
    # collection of another project
    assert client.post(f"/api/projects/{a}/pages/collection", json={"page_ids": ids_a, "collection_id": cid_b}).status_code == 404
    # page of another project
    cid_a = client.post(f"/api/projects/{a}/collections", json={"name": "X"}).json()["id"]
    assert client.post(f"/api/projects/{a}/pages/collection", json={"page_ids": ids_b, "collection_id": cid_a}).status_code == 404
    assert client.patch(f"/api/projects/{a}/collections/{cid_b}", json={"name": "Y"}).status_code == 404


def test_delete_requires_an_explicit_choice_for_pages(client, tmp_path):
    pid, ids = _project(client, tmp_path, 2)
    cid = client.post(f"/api/projects/{pid}/collections", json={"name": "Liste"}).json()["id"]
    client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": ids, "collection_id": cid})
    assert client.delete(f"/api/projects/{pid}/collections/{cid}").status_code == 422
    assert client.delete(f"/api/projects/{pid}/collections/{cid}", params={"pages": "bogus"}).status_code == 422

    r = client.delete(f"/api/projects/{pid}/collections/{cid}", params={"pages": "unassign"})
    assert r.status_code == 200 and r.json() == {"deleted": True, "pages_unassigned": 2, "pages_deleted": 0}
    pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
    assert len(pages) == 2 and all(p["collection_id"] is None for p in pages)


def test_delete_with_pages_needs_confirm_and_removes_files(client, tmp_path):
    pid, ids = _project(client, tmp_path, 3)
    cid = client.post(f"/api/projects/{pid}/collections", json={"name": "Scarti"}).json()["id"]
    client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": ids[:2], "collection_id": cid})
    for page_id in ids[:2]:
        assert pagesvc.thumb_path(page_id).exists()

    r = client.delete(f"/api/projects/{pid}/collections/{cid}", params={"pages": "delete"})
    assert r.status_code == 400
    assert len(client.get(f"/api/projects/{pid}/pages").json()["items"]) == 3

    r = client.delete(f"/api/projects/{pid}/collections/{cid}", params={"pages": "delete", "confirm": "true"})
    assert r.status_code == 200 and r.json()["pages_deleted"] == 2
    remaining = client.get(f"/api/projects/{pid}/pages").json()["items"]
    assert [p["id"] for p in remaining] == [ids[2]]
    for page_id in ids[:2]:
        assert not pagesvc.thumb_path(page_id).exists()
    with connect() as conn:
        events = [r["action"] for r in conn.execute("SELECT action FROM audit_events")]
    assert "collection.deleted" in events


def test_split_by_collection_groups_by_real_collection(client, tmp_path):
    from app.services import dataset_builder as builder

    pid, ids = _project(client, tmp_path, 4)
    a = client.post(f"/api/projects/{pid}/collections", json={"name": "A"}).json()["id"]
    b = client.post(f"/api/projects/{pid}/collections", json={"name": "B"}).json()["id"]
    client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": ids[:2], "collection_id": a})
    client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": ids[2:], "collection_id": b})
    with connect() as conn:
        for page_id in ids:
            conn.execute(
                "INSERT INTO blocks (page_id, label, points, content) VALUES (?, 'Text', '[0,0,10,10]', 'x')",
                (page_id,),
            )
    train, val = builder.compute_split(pid, 0.5, 1, "collection")
    assert {frozenset(train), frozenset(val)} == {frozenset(ids[:2]), frozenset(ids[2:])}


# --- permissions (R12) --------------------------------------------------------
def _mkuser(username: str, role: str = "editor") -> dict:
    with connect() as conn:
        return usersvc.create_user(conn, username, PASSWORD, role=role)


def _login(client, username):
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text


def test_permissions(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM project_members")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM users")
    owner = _mkuser("owner", "admin")
    editor = _mkuser("edi")
    viewer = _mkuser("vie")
    stranger = _mkuser("str")
    with connect() as conn:
        pid = conn.execute(
            "INSERT INTO projects (name, root_dir, owner_id) VALUES ('p', '/tmp', ?)", (owner["id"],)
        ).lastrowid
        conn.execute("INSERT INTO project_members VALUES (?, ?, 'editor', datetime('now'))", (pid, editor["id"]))
        conn.execute("INSERT INTO project_members VALUES (?, ?, 'viewer', datetime('now'))", (pid, viewer["id"]))
        cid = conn.execute("INSERT INTO collections (project_id, name) VALUES (?, 'C')", (pid,)).lastrowid

    with TestClient(app) as c:
        _login(c, "str")
        assert c.get(f"/api/projects/{pid}/collections").status_code == 404
    with TestClient(app) as c:
        _login(c, "vie")
        assert c.get(f"/api/projects/{pid}/collections").status_code == 200
        assert c.post(f"/api/projects/{pid}/collections", json={"name": "N"}).status_code == 403
    with TestClient(app) as c:
        _login(c, "edi")
        assert c.post(f"/api/projects/{pid}/collections", json={"name": "N"}).status_code == 201
        r = c.delete(f"/api/projects/{pid}/collections/{cid}", params={"pages": "delete", "confirm": "true"})
        assert r.status_code == 403
        assert c.delete(f"/api/projects/{pid}/collections/{cid}", params={"pages": "unassign"}).status_code == 200
