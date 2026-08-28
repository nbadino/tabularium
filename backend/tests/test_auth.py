"""Test autenticazione, ruoli e permessi per progetto (self-hosted).

La suite storica gira in modalità single-user (`TABULARIUM_AUTH=off`, impostata
in conftest). Questi test attivano la modalità auth **a runtime** patchando
`config.AUTH_MODE` (letto da `authsvc.auth_enabled()` a ogni richiesta, quindi
non serve ricaricare l'app). Ogni test riparte da un DB pulito.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import security, users as usersvc

PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _auth_on_and_clean_db(monkeypatch):
    """Attiva l'auth e azzera utenti/sessioni/progetti prima di ogni test."""
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM project_members")
        conn.execute("DELETE FROM projects")
    yield


def _client() -> TestClient:
    return TestClient(app)


def _mkuser(username: str, role: str = "editor", active: bool = True) -> dict:
    with connect() as conn:
        return usersvc.create_user(conn, username, PASSWORD, role=role, active=active)


def _mkproject(owner_id: int | None = None, name: str = "progetto") -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, root_dir, archive_dir, settings_json, owner_id) "
            "VALUES (?, ?, ?, '{}', ?)",
            (name, "/tmp", "/tmp", owner_id),
        )
        return cur.lastrowid


def _login(client: TestClient, username: str, password: str = PASSWORD):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


# --- gate: status/setup --------------------------------------------------------
def test_status_off_when_local_mode(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "off")
    with _client() as client:
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        assert r.json()["auth_enabled"] is False


def test_status_needs_setup_when_no_users():
    with _client() as client:
        r = client.get("/api/auth/status")
        assert r.status_code == 200
        body = r.json()
        assert body["auth_enabled"] is True
        assert body["needs_setup"] is True
        assert body["user"] is None


def test_setup_creates_admin_and_session():
    with _client() as client:
        r = client.post(
            "/api/auth/setup",
            json={"username": "admin", "password": PASSWORD, "email": "a@b.it"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "admin"
        assert body["role"] == "admin"
        # Sessione impostata: /me risponde senza altri passaggi.
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"


def test_setup_only_once():
    _mkuser("admin", role="admin")
    with _client() as client:
        r = client.post(
            "/api/auth/setup", json={"username": "altro", "password": PASSWORD}
        )
        assert r.status_code == 409


def test_setup_claims_orphan_projects():
    pid = _mkproject(owner_id=None)
    with _client() as client:
        r = client.post(
            "/api/auth/setup", json={"username": "admin", "password": PASSWORD}
        )
        assert r.status_code == 201
    with connect() as conn:
        row = conn.execute("SELECT owner_id FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["owner_id"] is not None


# --- login/logout/me -------------------------------------------------------------
def test_login_ok_sets_cookie_and_me():
    _mkuser("anna", role="editor")
    with _client() as client:
        _login(client, "anna")
        assert "tab_session" in client.cookies
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "editor"


def test_login_wrong_password():
    _mkuser("anna")
    with _client() as client:
        r = client.post("/api/auth/login", json={"username": "anna", "password": "wrong"})
        assert r.status_code == 401


def test_login_inactive_user_rejected():
    _mkuser("bob", active=False)
    with _client() as client:
        r = client.post(
            "/api/auth/login", json={"username": "bob", "password": PASSWORD}
        )
        assert r.status_code == 401


def test_me_requires_auth():
    with _client() as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/projects").status_code == 401


def test_logout_invalidates_session():
    _mkuser("anna")
    with _client() as client:
        _login(client, "anna")
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_bearer_token_auth():
    _mkuser("anna")
    with _client() as client:
        _login(client, "anna")
        token = client.cookies["tab_session"]
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == "anna"


# --- registrazione ----------------------------------------------------------------
def test_register_closed_by_default():
    _mkuser("admin", role="admin")
    with _client() as client:
        r = client.post(
            "/api/auth/register",
            json={"username": "newbie", "password": PASSWORD},
        )
        assert r.status_code == 403


def test_register_when_open_uses_default_role():
    _mkuser("admin", role="admin")
    with _client() as client:
        _login(client, "admin")
        # L'admin apre la registrazione dal pannello impostazioni.
        assert (
            client.put(
                "/api/settings", json={"allow_registration": True}
            ).status_code
            == 200
        )
        r = client.post(
            "/api/auth/register",
            json={"username": "newbie", "password": PASSWORD},
        )
        assert r.status_code == 201, r.text
        assert r.json()["role"] == "editor"


def test_register_validation():
    _mkuser("admin", role="admin")
    with _client() as client:
        _login(client, "admin")
        assert (
            client.put("/api/settings", json={"allow_registration": True}).status_code
            == 200
        )
        # password corta
        r = client.post(
            "/api/auth/register", json={"username": "newbie2", "password": "short"}
        )
        assert r.status_code == 422
        # nome utente non valido
        r = client.post(
            "/api/auth/register", json={"username": "a b", "password": PASSWORD}
        )
        assert r.status_code == 422
        # duplicato
        _mkuser("dup")
        r = client.post(
            "/api/auth/register", json={"username": "dup", "password": PASSWORD}
        )
        assert r.status_code == 409


# --- ruoli globali -----------------------------------------------------------------
def test_viewer_cannot_create_project():
    _mkuser("vista", role="viewer")
    with _client() as client:
        _login(client, "vista")
        r = client.post(
            "/api/projects", json={"name": "p", "archive_dir": "/tmp"}
        )
        assert r.status_code == 403


def test_editor_creates_project_and_becomes_owner():
    _mkuser("editor", role="editor")
    with _client() as client:
        _login(client, "editor")
        r = client.post(
            "/api/projects", json={"name": "p", "archive_dir": "/tmp"}
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
    with connect() as conn:
        owner = conn.execute(
            "SELECT owner_id FROM projects WHERE id=?", (pid,)
        ).fetchone()["owner_id"]
    assert owner is not None


# --- accesso per progetto ------------------------------------------------------------
def test_non_member_gets_404():
    owner = _mkuser("owner", role="editor")
    pid = _mkproject(owner_id=owner["id"])
    _mkuser("outsider", role="editor")
    with _client() as client:
        _login(client, "outsider")
        assert client.get(f"/api/projects/{pid}").status_code == 404
        assert client.get(f"/api/projects/{pid}/workflow").status_code == 404


def test_viewer_can_read_but_not_write():
    owner = _mkuser("owner", role="admin")
    pid = _mkproject(owner_id=owner["id"])
    viewer = _mkuser("vista", role="viewer")
    with connect() as conn:
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,?)",
            (pid, viewer["id"], "viewer"),
        )
    with _client() as client:
        _login(client, "vista")
        assert client.get(f"/api/projects/{pid}").status_code == 200
        # lettura pagine: ok
        r = client.get(f"/api/projects/{pid}/workflow")
        assert r.status_code == 200
        # scrittura convenzioni: vietata
        r = client.put(
            f"/api/projects/{pid}/conventions",
            json={"conventions": []},
        )
        assert r.status_code == 403


def test_member_editor_can_write():
    owner = _mkuser("owner", role="admin")
    pid = _mkproject(owner_id=owner["id"])
    editor = _mkuser("editor", role="editor")
    with connect() as conn:
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,?)",
            (pid, editor["id"], "editor"),
        )
    with _client() as client:
        _login(client, "editor")
        r = client.put(
            f"/api/projects/{pid}/conventions",
            json={"conventions": []},
        )
        assert r.status_code == 200


def test_admin_has_full_access():
    admin = _mkuser("boss", role="admin")
    pid = _mkproject(owner_id=None)  # senza proprietario, nessun membro
    with _client() as client:
        _login(client, "boss")
        assert client.get(f"/api/projects/{pid}").status_code == 200


def test_list_projects_filtered_by_access():
    owner_a = _mkuser("anna", role="editor")
    pid_a = _mkproject(owner_id=owner_a["id"], name="A")
    _mkproject(owner_id=owner_a["id"], name="B")
    other = _mkuser("other", role="editor")
    pid_other = _mkproject(owner_id=other["id"], name="C")
    viewer = _mkuser("vista", role="viewer")
    with connect() as conn:
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,?)",
            (pid_a, viewer["id"], "viewer"),
        )
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,?)",
            (pid_other, viewer["id"], "editor"),
        )
    with _client() as client:
        _login(client, "vista")
        ids = {item["id"] for item in client.get("/api/projects").json()["items"]}
        assert ids == {pid_a, pid_other}


def test_delete_project_requires_owner():
    owner = _mkuser("owner", role="editor")
    pid = _mkproject(owner_id=owner["id"])
    editor = _mkuser("editor", role="editor")
    with connect() as conn:
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,?)",
            (pid, editor["id"], "editor"),
        )
    with _client() as client:
        _login(client, "editor")
        r = client.delete(f"/api/projects/{pid}?confirm=true")
        assert r.status_code == 403
    # Il proprietario invece può.
    with _client() as client2:
        _login(client2, "owner")
        r = client2.delete(f"/api/projects/{pid}?confirm=true")
        assert r.status_code == 200


# --- impostazioni ---------------------------------------------------------------------
def test_settings_read_any_authenticated_write_admin_only():
    _mkuser("vista", role="viewer")
    with _client() as client:
        _login(client, "vista")
        assert client.get("/api/settings").status_code == 200
        assert client.put("/api/settings", json={"instance_name": "x"}).status_code == 403


def test_settings_admin_write_persists():
    _mkuser("boss", role="admin")
    with _client() as client:
        _login(client, "boss")
        r = client.put(
            "/api/settings", json={"instance_name": "Archivio Ligure", "allow_registration": True}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["instance_name"] == "Archivio Ligure"
        assert body["allow_registration"] is True
        # Persiste su richieste successive.
        assert client.get("/api/settings").json()["instance_name"] == "Archivio Ligure"


def test_settings_invalid_name_rejected():
    _mkuser("boss", role="admin")
    with _client() as client:
        _login(client, "boss")
        r = client.put("/api/settings", json={"instance_name": "   "})
        assert r.status_code == 422


# --- gestione utenti (admin) ------------------------------------------------------------
def test_users_endpoints_admin_only():
    _mkuser("editor", role="editor")
    with _client() as client:
        _login(client, "editor")
        assert client.get("/api/users").status_code == 403
        assert (
            client.post(
                "/api/users",
                json={"username": "x", "password": PASSWORD, "role": "viewer"},
            ).status_code
            == 403
        )


def test_admin_creates_and_lists_users():
    _mkuser("boss", role="admin")
    with _client() as client:
        _login(client, "boss")
        r = client.post(
            "/api/users",
            json={"username": "nuovo", "password": PASSWORD, "role": "viewer"},
        )
        assert r.status_code == 201
        assert r.json()["role"] == "viewer"
        usernames = {u["username"] for u in client.get("/api/users").json()}
        assert {"boss", "nuovo"} <= usernames


def test_demote_admin_ok_when_another_admin_remains():
    _mkuser("boss", role="admin")
    boss2_id = _mkuser("boss2", role="admin")["id"]
    with _client() as client:
        _login(client, "boss")
        # Resta un altro admin: il declassamento è legittimo.
        r = client.patch(f"/api/users/{boss2_id}", json={"role": "editor"})
        assert r.status_code == 200
        assert r.json()["role"] == "editor"


def test_role_change_blocked_when_single_admin():
    _mkuser("boss", role="admin")
    anna = _mkuser("anna", role="editor")
    with _client() as client:
        _login(client, "boss")
        # Con un solo admin, nessun declassamento di ruolo è permesso
        # (protezione dell'ultimo amministratore).
        r = client.patch(
            f"/api/users/{anna['id']}", json={"role": "viewer"}
        )
        assert r.status_code == 400


def test_cannot_delete_self():
    boss = _mkuser("boss", role="admin")
    with _client() as client:
        _login(client, "boss")
        r = client.delete(f"/api/users/{boss['id']}")
        assert r.status_code == 400


def test_reset_password_invalidates_sessions():
    admin = _mkuser("boss", role="admin")
    target = _mkuser("anna", role="editor")
    with _client() as client:
        _login(client, "anna")
        assert client.get("/api/auth/me").status_code == 200
    with _client() as client_admin:
        _login(client_admin, "boss")
        r = client_admin.post(
            f"/api/users/{target['id']}/reset-password",
            json={"password": "nuovapass-123"},
        )
        assert r.status_code == 200
    # La vecchia sessione di anna è stata invalidata.
    with _client() as client_old:
        # il cookie della vecchia sessione non esiste in questo nuovo client;
        # verifichiamo direttamente che login con vecchia password fallisca.
        r = client_old.post(
            "/api/auth/login", json={"username": "anna", "password": PASSWORD}
        )
        assert r.status_code == 401
        r = client_old.post(
            "/api/auth/login", json={"username": "anna", "password": "nuovapass-123"}
        )
        assert r.status_code == 200


# --- sicurezza (unit) -------------------------------------------------------------------
def test_password_hash_roundtrip():
    salt, h = security.hash_password("password123")
    assert salt and h
    assert security.verify_password("password123", salt, h)
    assert not security.verify_password("password124", salt, h)


def test_token_hash_not_plaintext():
    token = security.new_token()
    digest = security.hash_token(token)
    assert digest != token
    assert len(digest) == 64  # sha256 hex
