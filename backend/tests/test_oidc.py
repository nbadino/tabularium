"""OIDC login: the flow, the refusals, and the admin configuration.

The provider is faked at the HTTP boundary (`httpx.post`/`httpx.get`), so the test
exercises everything Tabularium owns: state, PKCE, claim mapping, provisioning, the
session and the redirects.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import oidc, settings as settingssvc

ISSUER = "https://idp.example.org"


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("errore", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        return self._payload


@pytest.fixture(autouse=True)
def _auth_on_and_clean() -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM project_members")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM meta WHERE key LIKE 'oidc_state:%' OR key='app_settings'")
        conn.execute("DELETE FROM secrets WHERE name='oidc'")
    yield


def _configure(**overrides) -> dict:
    payload = {
        "oidc_enabled": True,
        "oidc_issuer": ISSUER,
        "oidc_client_id": "client-id",
        "oidc_client_secret": "client-secret",
        "oidc_allowed_domains": [],
        "oidc_button_label": "Accedi con l'ateneo",
    }
    payload.update(overrides)
    return settingssvc.save_app_settings(payload)


def _discovery(setup: oidc.OidcSetup) -> dict:
    return {
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
    }


def _fake_provider(monkeypatch, claims: dict) -> None:
    monkeypatch.setattr(oidc, "_discovery", _discovery)
    monkeypatch.setattr(oidc.httpx, "post", lambda url, data, timeout: _Response({"access_token": "token-123"}))
    monkeypatch.setattr(oidc.httpx, "get", lambda url, headers, timeout: _Response(claims))


def _start(client: TestClient) -> str:
    response = client.get("/api/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    return location, query["state"][0]


def test_the_admin_configures_oidc_and_the_secret_never_comes_back(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    saved = _configure()
    assert saved["oidc_enabled"] is True
    assert saved["oidc_client_secret_set"] is True
    assert "oidc_client_secret" not in saved
    with connect() as conn:
        stored = json.loads(conn.execute("SELECT value FROM meta WHERE key='app_settings'").fetchone()[0])
    assert "client-secret" not in json.dumps(stored)

    cleared = settingssvc.save_app_settings({"oidc_client_secret_clear": True})
    assert cleared["oidc_client_secret_set"] is False


def test_enabling_without_an_issuer_is_refused() -> None:
    with pytest.raises(ValueError, match="issuer"):
        settingssvc.save_app_settings({"oidc_enabled": True, "oidc_issuer": "", "oidc_client_id": "x"})


def test_only_https_issuers_are_accepted() -> None:
    with pytest.raises(ValueError, match="https"):
        settingssvc.save_app_settings({"oidc_issuer": "ftp://idp.example.org"})


def test_the_status_advertises_the_button_only_when_ready(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    client = TestClient(app)
    assert client.get("/api/auth/status").json()["oidc_enabled"] is False
    _configure()
    body = client.get("/api/auth/status").json()
    assert body["oidc_enabled"] is True
    assert body["oidc_button_label"] == "Accedi con l'ateneo"


def test_the_login_creates_the_account_and_the_session(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    _configure()
    _fake_provider(monkeypatch, {"sub": "42", "email": "Ada@Uni.it", "email_verified": True, "name": "Ada"})
    client = TestClient(app)

    location, state = _start(client)
    assert location.startswith(f"{ISSUER}/authorize?")
    query = parse_qs(urlparse(location).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["client-id"]
    assert "code_challenge" in query

    response = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert config.SESSION_COOKIE in response.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "ada@uni.it"

    # A second login reuses the account instead of creating another one.
    _, state = _start(client)
    client.get(f"/api/auth/oidc/callback?code=def&state={state}", follow_redirects=False)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_an_unknown_state_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    _configure()
    _fake_provider(monkeypatch, {"sub": "1", "email": "a@uni.it"})
    client = TestClient(app)
    response = client.get("/api/auth/oidc/callback?code=abc&state=forged", follow_redirects=False)
    assert response.status_code == 303
    assert "auth_error=oidc_state" in response.headers["location"]
    assert config.SESSION_COOKIE not in response.cookies


def test_a_state_cannot_be_used_twice(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    _configure()
    _fake_provider(monkeypatch, {"sub": "1", "email": "a@uni.it"})
    client = TestClient(app)
    _, state = _start(client)
    first = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert first.headers["location"] == "/"
    second = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert "auth_error=oidc_state" in second.headers["location"]


def test_a_domain_outside_the_allowlist_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    _configure(oidc_allowed_domains=["uni.it"])
    _fake_provider(monkeypatch, {"sub": "1", "email": "someone@gmail.com", "email_verified": True})
    client = TestClient(app)
    _, state = _start(client)
    response = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert "auth_error=oidc_domain" in response.headers["location"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_an_unverified_email_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    _configure()
    _fake_provider(monkeypatch, {"sub": "1", "email": "a@uni.it", "email_verified": False})
    client = TestClient(app)
    _, state = _start(client)
    response = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert "auth_error=oidc_email" in response.headers["location"]


def test_the_default_role_of_the_instance_is_applied(monkeypatch) -> None:
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    _configure(default_new_user_role="viewer")
    _fake_provider(monkeypatch, {"sub": "1", "email": "a@uni.it", "email_verified": True})
    client = TestClient(app)
    _, state = _start(client)
    client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert client.get("/api/auth/me").json()["role"] == "viewer"
