"""Collegamento dell'account Hugging Face per i download del registro modelli.

Il token è gestito da ``huggingface_hub``, che lo conserva nella propria cache
(o lo legge da ``HF_TOKEN``): non torna mai al browser, al database o ai log di
download. Non esiste un device-code flow da usare qui — né la libreria lo
espone, né huggingface.co ha l'endpoint (``POST /oauth/device/code`` → 404,
verificato) — quindi il collegamento passa da un token creato dall'utente.
"""
from __future__ import annotations

import threading
from typing import Any

from huggingface_hub import get_token, login, logout, whoami

TOKEN_PAGE = "https://huggingface.co/settings/tokens"

_lock = threading.Lock()
_state: dict[str, Any] = {
    "state": "disconnected",
    "verification_uri_complete": None,
    "user_code": None,
    "username": None,
    "error": None,
}


def _set(**updates: Any) -> None:
    with _lock:
        _state.update(updates)


def _username() -> str | None:
    try:
        info = whoami()
    except Exception:  # noqa: BLE001 - offline o token revocato: non è un errore fatale
        return None
    if isinstance(info, dict):
        return info.get("name") or info.get("fullname")
    return None


def status() -> dict[str, Any]:
    """Stato del collegamento, dedotto dal token effettivamente disponibile."""
    with _lock:
        result = dict(_state)
    if get_token():
        result.update({
            "state": "connected",
            "username": result.get("username") or _username(),
            "error": None,
        })
    elif result["state"] == "connected":
        result.update({"state": "disconnected", "username": None})
    result["token_page"] = TOKEN_PAGE
    return result


def start() -> dict[str, Any]:
    """Avvia il collegamento indicando dove creare il token.

    Restituisce la pagina dei token come `verification_uri_complete` così la UI
    può aprirla nel popup che ha già riservato al click.
    """
    current = status()
    if current["state"] == "connected":
        return current
    _set(
        state="needs_token",
        verification_uri_complete=TOKEN_PAGE,
        user_code=None,
        error=(
            "Hugging Face non espone un login a codice: crea un token di lettura "
            "e incollalo qui, oppure impostalo in HF_TOKEN."
        ),
    )
    return status()


def connect(token: str) -> dict[str, Any]:
    """Registra il token utente nella cache di ``huggingface_hub``."""
    token = str(token or "").strip()
    if not token:
        raise ValueError("token Hugging Face vuoto")
    try:
        # add_to_git_credential=False: il token serve alle API del Hub, non a git.
        login(token=token, add_to_git_credential=False)
    except Exception as exc:  # noqa: BLE001 - l'errore del provider va mostrato in UI
        _set(state="error", error=str(exc), verification_uri_complete=None, user_code=None)
        return status()
    _set(state="connected", username=_username(), error=None, verification_uri_complete=None, user_code=None)
    return status()


def disconnect() -> dict[str, Any]:
    """Rimuove il token salvato: i download tornano ai soli modelli pubblici."""
    try:
        logout()
    except Exception as exc:  # noqa: BLE001
        _set(state="error", error=str(exc))
        return status()
    _set(state="disconnected", username=None, error=None, verification_uri_complete=None, user_code=None)
    return status()
