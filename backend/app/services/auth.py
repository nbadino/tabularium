"""Autenticazione e autorizzazione (self-hosted).

Due modalità, lette da `config.AUTH_MODE`:

- **off** — modalità locale storica a utente singolo: nessun controllo, gli
  helper restituiscono un utente locale fittizio (`is_local=True`) che aggira
  ogni permesso. È la modalità usata dai test e dall'uso single-user.
- **on** (default) — login richiesto: sessione portata in un cookie HttpOnly
  `SameSite=Strict` (la UI è same-origin, quindi nessun CSRF cross-site) oppure
  nell'header `Authorization: Bearer <token>` per i client API. Il token è
  salvato come SHA-256 nella tabella `sessions`.

Ruoli globali: `admin` (tutto + gestione utenti/impostazioni), `editor`
(crea ed edita i progetti cui ha accesso), `viewer` (sola lettura).
Accesso per progetto: `projects.owner_id` (pieno) + `project_members`
(`editor` scrive, `viewer` legge).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, Response

from .. import config
from ..db import connect
from . import security

ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2}
PROJECT_ROLES = {"owner", "editor", "viewer"}


def auth_enabled() -> bool:
    return config.auth_enabled()


# --- utente locale (modalità off) ----------------------------------------------
def local_user() -> dict:
    """Utente fittizio della modalità single-user: aggira i permessi."""
    return {"id": None, "username": "local", "role": "admin", "is_local": True}


def is_local(user: dict | None) -> bool:
    return bool(user and user.get("is_local"))


# --- ruoli ----------------------------------------------------------------------
def is_admin(user: dict | None) -> bool:
    if is_local(user):
        return True
    return bool(user and user.get("role") == "admin")


def role_ge(role: str | None, minimum: str) -> bool:
    if role is None:
        return False
    return ROLE_ORDER.get(role, -1) >= ROLE_ORDER.get(minimum, 0)


def require_admin(user: dict | None) -> dict:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="servono privilegi di amministratore")
    return user


def require_role(user: dict | None, minimum: str) -> dict:
    if is_local(user):
        return user  # type: ignore[return-value]
    if not role_ge((user or {}).get("role"), minimum):
        raise HTTPException(status_code=403, detail="permessi insufficienti")
    return user  # type: ignore[return-value]


def check_password(user_row: dict, password: str) -> bool:
    """Verifica la password di una riga utente (`users`)."""
    return security.verify_password(password, user_row["salt"], user_row["password_hash"])


# --- sessioni --------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def create_session(user_id: int, request: Request | None = None) -> tuple[str, str]:
    """Crea una sessione e restituisce `(token, expires_at_iso)`."""
    token = security.new_token()
    expires = _now() + timedelta(days=config.SESSION_TTL_DAYS)
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, ip, user_agent) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                security.hash_token(token),
                user_id,
                _iso(expires),
                request.client.host if request else None,
                request.headers.get("user-agent") if request else None,
            ),
        )
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (_iso(_now()), user_id))
    return token, _iso(expires)


def set_session_cookie(response: Response, token: str, expires_at: str | None = None) -> None:
    """Scrive il cookie di sessione (HttpOnly, SameSite=Strict)."""
    max_age = config.SESSION_TTL_DAYS * 86400
    response.set_cookie(
        key=config.SESSION_COOKIE,
        value=token,
        max_age=max_age,
        expires=expires_at,
        httponly=True,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=config.SESSION_COOKIE, path="/")


def _delete_session(token_hash: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def resolve_user(request: Request) -> dict | None:
    """Risolve l'utente dalla richiesta, se autenticato. None altrimenti."""
    token = request.cookies.get(config.SESSION_COOKIE)
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if not token:
        return None
    token_hash = security.hash_token(token)
    with connect() as conn:
        row = conn.execute(
            """SELECT u.*, s.expires_at AS session_expires
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ?""",
            (token_hash,),
        ).fetchone()
    if row is None:
        return None
    if _iso(_now()) > row["session_expires"]:
        _delete_session(token_hash)
        return None
    if not row["active"]:
        return None
    return dict(row)


# --- dipendenze FastAPI -----------------------------------------------------------
def get_current_user(request: Request) -> dict:
    """Dipende da `Depends`. In modalità off restituisce l'utente locale."""
    if not auth_enabled():
        return local_user()
    user = resolve_user(request)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="autenticazione richiesta",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_optional_user(request: Request) -> dict | None:
    """Come sopra ma non fallisce se manca il login (utente None)."""
    if not auth_enabled():
        return local_user()
    return resolve_user(request)


# --- accesso per progetto -----------------------------------------------------------
def get_project_access(project_id: int, user: dict) -> str | None:
    """Livello di accesso dell'utente sul progetto: owner|editor|viewer|None."""
    if is_local(user):
        return "owner"
    if is_admin(user):
        return "owner"
    with connect() as conn:
        row = conn.execute(
            "SELECT owner_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    if row is None:
        return None
    if row["owner_id"] == user["id"]:
        return "owner"
    with connect() as conn:
        member = conn.execute(
            "SELECT role FROM project_members WHERE project_id=? AND user_id=?",
            (project_id, user["id"]),
        ).fetchone()
    return member["role"] if member else None


def require_project_access(project_id: int, user: dict, write: bool = False) -> str:
    """Garantisce l'accesso al progetto; alza 404 (esistenza nascosta) o 403.

    - `write=False` → basta un qualsiasi accesso (lettura).
    - `write=True` → serve owner/editor (i viewer non scrivono).
    In modalità off non controlla nulla.
    """
    if is_local(user):
        return "owner"
    level = get_project_access(project_id, user)
    if level is None:
        raise HTTPException(status_code=404, detail="progetto non trovato")
    if write and level not in ("owner", "editor"):
        raise HTTPException(status_code=403, detail="permessi insufficienti sul progetto")
    return level
