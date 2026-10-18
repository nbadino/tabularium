"""Onboarding utenti: invito via link, password forzata al primo accesso, profilo.

Come `test_auth.py`, questi test attivano l'auth a runtime e ripartono da un DB
pulito.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import users as usersvc

PASSWORD = "password123"


@pytest.fixture(autouse=True)
def _auth_on_and_clean_db(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM project_members")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM users")
    yield


def _client() -> TestClient:
    return TestClient(app)


def _mkuser(username: str, role: str = "editor", active: bool = True) -> dict:
    with connect() as conn:
        return usersvc.create_user(conn, username, PASSWORD, role=role, active=active)


def _login(client: TestClient, username: str, password: str = PASSWORD):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r


def _token_of(invite_url: str) -> str:
    match = re.search(r"token=([^&]+)", invite_url)
    assert match, invite_url
    return match.group(1)


# --- invito via link -----------------------------------------------------------
def test_admin_invite_creates_account_and_link():
    _mkuser("boss", role="admin")
    with _client() as client:
        _login(client, "boss")
        r = client.post("/api/users", json={"username": "newbie", "role": "editor", "invite": True})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["username"] == "newbie"
        assert body["must_change_password"] is True
        assert body["invite_url"] and "/invito?token=" in body["invite_url"]

        # Senza password e senza `invite` esplicito: stesso esito (invito).
        r2 = client.post("/api/users", json={"username": "other", "role": "viewer"})
        assert r2.status_code == 201
        assert r2.json()["invite_url"]


def test_invite_status_and_accept_sets_password():
    _mkuser("boss", role="admin")
    with _client() as client:
        _login(client, "boss")
        invite_url = client.post(
            "/api/users", json={"username": "newbie", "role": "editor", "invite": True}
        ).json()["invite_url"]
    token = _token_of(invite_url)

    with _client() as public:
        status = public.get(f"/api/auth/invite/{token}")
        assert status.status_code == 200
        assert status.json()["valid"] is True
        assert status.json()["username"] == "newbie"

        # Accettazione: imposta la password e lascia la sessione attiva.
        r = public.post("/api/auth/invite/accept", json={"token": token, "password": "scelta-da-me-1"})
        assert r.status_code == 200, r.text
        assert r.json()["must_change_password"] is False
        assert public.get("/api/auth/me").status_code == 200

    # Il token è a uso singolo.
    with _client() as again:
        assert again.get(f"/api/auth/invite/{token}").json()["valid"] is False

    with _client() as fresh:
        _login(fresh, "newbie", "scelta-da-me-1")


def test_accept_invite_rejects_expired_token():
    admin = _mkuser("boss", role="admin")
    with connect() as conn:
        user = usersvc.create_user(conn, "late", "placeholder-123", must_change_password=True)
        token, _ = usersvc.generate_invite(conn, user["id"])
        conn.execute(
            "UPDATE users SET invite_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (user["id"],),
        )
    with _client() as client:
        r = client.post("/api/auth/invite/accept", json={"token": token, "password": "nuova-password"})
        assert r.status_code == 400
    assert admin  # admin only used to keep the fixture intent explicit


# --- password forzata al primo accesso -----------------------------------------
def test_must_change_password_gates_api_until_changed():
    admin = _mkuser("boss", role="admin")
    with connect() as conn:
        usersvc.create_user(conn, "temp", PASSWORD, must_change_password=True)
    with _client() as client:
        login = _login(client, "temp")
        assert login.json()["must_change_password"] is True
        # Le API non auth sono bloccate finché si cambia la password.
        assert client.get("/api/projects").status_code == 403
        # Il cambio invece passa e sblocca.
        r = client.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "password-nuova-1"},
        )
        assert r.status_code == 200, r.text
        assert client.get("/api/projects").status_code == 200
        assert client.get("/api/auth/me").json()["must_change_password"] is False
    assert admin


def test_admin_reset_marks_password_to_change():
    admin = _mkuser("boss", role="admin")
    target = _mkuser("anna", role="editor")
    with _client() as client:
        _login(client, "boss")
        r = client.post(f"/api/users/{target['id']}/reset-password", json={"password": "tmp-pass-123"})
        assert r.status_code == 200
        assert r.json()["must_change_password"] is True
    with _client() as anna:
        _login(anna, "anna", "tmp-pass-123")
        assert anna.get("/api/projects").status_code == 403
    assert admin


def test_update_own_profile_email():
    _mkuser("anna", role="editor")
    with _client() as client:
        _login(client, "anna")
        r = client.patch("/api/auth/me", json={"email": "anna@example.org"})
        assert r.status_code == 200
        assert r.json()["email"] == "anna@example.org"
        assert client.get("/api/auth/me").json()["email"] == "anna@example.org"
