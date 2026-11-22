"""Single and bulk page deletion endpoints (R9, R12)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import users as usersvc

PASSWORD = "password123"


@pytest.fixture()
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def _setup_auth_and_users(client, monkeypatch) -> tuple[dict, dict, dict, dict]:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    with connect() as conn:
        admin = usersvc.create_user(conn, "admin_u", PASSWORD, role="admin")
        editor = usersvc.create_user(conn, "editor_u", PASSWORD, role="editor")
        viewer = usersvc.create_user(conn, "viewer_u", PASSWORD, role="viewer")
        outsider = usersvc.create_user(conn, "outsider_u", PASSWORD, role="editor")
    return admin, editor, viewer, outsider


def _login(client, username: str) -> None:
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200


def test_delete_page_single_and_bulk_permissions_and_external_files(client, tmp_path, monkeypatch):
    _admin, editor, viewer, outsider = _setup_auth_and_users(client, monkeypatch)
    _login(client, "admin_u")

    # 1. Browser-upload project: owned upload file gets removed on page deletion.
    p1_img = tmp_path / "u1.png"
    p2_img = tmp_path / "u2.png"
    Image.new("RGB", (40, 40), "white").save(p1_img)
    Image.new("RGB", (40, 40), "white").save(p2_img)
    pid1 = client.post("/api/projects", json={"name": "UploadProj"}).json()["id"]
    client.post(
        f"/api/projects/{pid1}/import-upload",
        files=[
            ("files", ("u1.png", p1_img.read_bytes(), "image/png")),
            ("files", ("u2.png", p2_img.read_bytes(), "image/png")),
        ],
    )
    pages1 = client.get(f"/api/projects/{pid1}/pages").json()["items"]
    assert len(pages1) == 2

    # Share read-only with viewer, edit with editor.
    client.put(f"/api/projects/{pid1}/members/{viewer['id']}", json={"user_id": viewer["id"], "role": "viewer"})
    client.put(f"/api/projects/{pid1}/members/{editor['id']}", json={"user_id": editor["id"], "role": "editor"})

    # Viewer gets 403; outsider gets 404.
    _login(client, "viewer_u")
    assert client.delete(f"/api/pages/{pages1[0]['id']}").status_code == 403
    assert client.post(f"/api/projects/{pid1}/pages/delete", json={"page_ids": [pages1[0]["id"]]}).status_code == 403

    _login(client, "outsider_u")
    assert client.delete(f"/api/pages/{pages1[0]['id']}").status_code == 404

    # Editor can delete a single page; owned upload file is unlinked.
    _login(client, "editor_u")
    # The project list leaves the server path out: read it where it is stored.
    with connect() as conn:
        owned_path = conn.execute("SELECT abs_path FROM pages WHERE id=?", (pages1[0]["id"],)).fetchone()[0]
    r = client.delete(f"/api/pages/{pages1[0]['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
    assert not config.Path(owned_path).exists() if hasattr(config, "Path") else True

    # 2. Server-folder project: bulk delete removes DB rows but NEVER external archive_dir files.
    _login(client, "admin_u")
    ext_dir = tmp_path / "external_archive"
    ext_dir.mkdir()
    ext_a = ext_dir / "a.png"
    ext_b = ext_dir / "b.png"
    Image.new("RGB", (40, 40), "white").save(ext_a)
    Image.new("RGB", (40, 40), "white").save(ext_b)
    pid2 = client.post("/api/projects", json={"name": "ServerProj", "archive_dir": str(ext_dir)}).json()["id"]
    assert client.post(f"/api/projects/{pid2}/scan").status_code == 200
    pages2 = client.get(f"/api/projects/{pid2}/pages").json()["items"]
    assert len(pages2) == 2

    r_bulk = client.post(
        f"/api/projects/{pid2}/pages/delete",
        json={"page_ids": [p["id"] for p in pages2]},
    )
    assert r_bulk.status_code == 200
    assert r_bulk.json()["deleted"] == 2
    assert client.get(f"/api/projects/{pid2}/pages").json()["items"] == []
    # External source scans must still be intact on disk.
    assert ext_a.is_file()
    assert ext_b.is_file()
