"""Il token Hugging Face non deve mai tornare al client.

Il device-code flow non esiste (né in `huggingface_hub`, né su huggingface.co:
`POST /oauth/device/code` risponde 404): il collegamento passa da un token
utente, e questi test fissano il contratto di quel percorso.
"""
from __future__ import annotations

import pytest

from app.services import huggingface_auth


@pytest.fixture(autouse=True)
def _reset_state():
    huggingface_auth._state.update(  # noqa: SLF001 - reset dello stato in memoria
        state="disconnected",
        verification_uri_complete=None,
        user_code=None,
        username=None,
        error=None,
    )


def test_start_points_to_the_token_page_without_secrets(monkeypatch):
    monkeypatch.setattr(huggingface_auth, "get_token", lambda: None)
    result = huggingface_auth.start()
    assert result["state"] == "needs_token"
    assert result["verification_uri_complete"] == huggingface_auth.TOKEN_PAGE
    assert result["error"]
    assert "access_token" not in result and "token" not in result


def test_connect_stores_the_token_and_never_echoes_it(monkeypatch):
    saved: dict = {}

    def fake_login(token, add_to_git_credential=True):
        saved["token"] = token
        saved["git"] = add_to_git_credential

    monkeypatch.setattr(huggingface_auth, "login", fake_login)
    monkeypatch.setattr(huggingface_auth, "whoami", lambda: {"name": "tester"})
    monkeypatch.setattr(huggingface_auth, "get_token", lambda: "hf_secret")

    result = huggingface_auth.connect("hf_secret")
    assert saved == {"token": "hf_secret", "git": False}
    assert result["state"] == "connected"
    assert result["username"] == "tester"
    assert "hf_secret" not in str(result)


def test_connect_rejects_empty_token():
    with pytest.raises(ValueError):
        huggingface_auth.connect("   ")


def test_status_follows_the_stored_token(monkeypatch):
    monkeypatch.setattr(huggingface_auth, "whoami", lambda: {"name": "tester"})
    monkeypatch.setattr(huggingface_auth, "get_token", lambda: "hf_secret")
    assert huggingface_auth.status()["state"] == "connected"

    # Token revocato fuori dall'app: lo stato non resta bloccato su "connected".
    monkeypatch.setattr(huggingface_auth, "get_token", lambda: None)
    assert huggingface_auth.status()["state"] == "disconnected"


def test_disconnect_clears_the_session(monkeypatch):
    calls: list = []
    monkeypatch.setattr(huggingface_auth, "logout", lambda: calls.append("logout"))
    monkeypatch.setattr(huggingface_auth, "get_token", lambda: None)
    result = huggingface_auth.disconnect()
    assert calls == ["logout"]
    assert result["state"] == "disconnected" and result["username"] is None
