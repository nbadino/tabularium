"""Gestione utenti (CRUD + password), separata dalla rete API.

Validazioni condivise (nome utente, ruolo, password) così setup, registrazione
e pannello admin usano le stesse regole.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from ..adapters import users as accountstore
from . import security

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,40}$")
ROLES = {"admin", "editor", "viewer"}

# Durata dell'invito via link: l'invitato deve scegliere la password entro
# questo termine. Oltre, il link va rigenerato dall'admin.
INVITE_TTL_HOURS = 72


def normalize_username(username: str) -> str:
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise HTTPException(
            status_code=422,
            detail="nome utente non valido (3-40 caratteri, lettere, cifre, . _ -)",
        )
    return username


def validate_password(password: str) -> None:
    if not password or len(password) < 8:
        raise HTTPException(status_code=422, detail="password troppo corta (minimo 8 caratteri)")


def validate_role(role: str) -> str:
    role = (role or "editor").strip()
    if role not in ROLES:
        raise HTTPException(status_code=422, detail=f"ruolo non valido: {role}")
    return role


def user_out(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "active": bool(row["active"]),
        "must_change_password": bool(row["must_change_password"]) if "must_change_password" in keys else False,
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    email: str | None = None,
    role: str = "editor",
    active: bool = True,
    must_change_password: bool = False,
) -> dict:
    username = normalize_username(username)
    validate_password(password)
    role = validate_role(role)
    if accountstore.username_taken(username, conn):
        raise HTTPException(status_code=409, detail="nome utente già esistente")
    salt, hash_hex = security.hash_password(password)
    user_id = accountstore.insert(
        conn,
        username=username,
        email=email or None,
        password_hash=hash_hex,
        salt=salt,
        role=role,
        active=active,
        must_change_password=must_change_password,
    )
    return user_out(accountstore.get(user_id, conn))


def get_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    return accountstore.get(user_id, conn)


def list_users(conn: sqlite3.Connection) -> list[dict]:
    return [user_out(row) for row in accountstore.list_accounts(conn)]


def update_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    email: str | None = None,
    role: str | None = None,
    active: bool | None = None,
) -> dict:
    user = get_user(conn, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="utente non trovato")
    fields: dict = {}
    if role is not None:
        fields["role"] = validate_role(role)
    if active is not None:
        fields["active"] = 1 if active else 0
    if email is not None:
        fields["email"] = email or None
    if fields:
        accountstore.update_fields(conn, user_id, fields)
    return user_out(get_user(conn, user_id))


def set_password(
    conn: sqlite3.Connection,
    user_id: int,
    new_password: str,
    *,
    must_change: bool = False,
) -> None:
    validate_password(new_password)
    salt, hash_hex = security.hash_password(new_password)
    accountstore.set_password(
        conn, user_id, password_hash=hash_hex, salt=salt, must_change=must_change
    )


def generate_invite(conn: sqlite3.Connection, user_id: int, ttl_hours: int = INVITE_TTL_HOURS) -> tuple[str, str]:
    """Crea (o rinnova) il link d'invito di un utente. Restituisce il token in
    chiaro — l'unica volta che è visibile — e la sua scadenza ISO.

    L'hash sostituisce i campi password del primo accesso: chi riceve il link
    sceglie la propria password, senza che l'admin ne conosca una.
    """
    if get_user(conn, user_id) is None:
        raise HTTPException(status_code=404, detail="utente non trovato")
    token = security.new_token()
    expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
    accountstore.set_invite(
        conn, user_id, token_hash=security.hash_token(token), expires_at=expires, must_change=True
    )
    return token, expires


def find_by_invite(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Utente il cui invito è valido (token presente e non scaduto), o None."""
    if not token:
        return None
    row = accountstore.by_invite_hash(security.hash_token(token), conn)
    if row is None or not row["invite_expires_at"]:
        return None
    try:
        expires = datetime.fromisoformat(row["invite_expires_at"])
    except ValueError:
        return None
    if expires < datetime.now(timezone.utc):
        return None
    return row


def accept_invite(conn: sqlite3.Connection, token: str, new_password: str) -> dict:
    """Consuma l'invito: imposta la password scelta, attiva l'utente e invalida
    le sessioni. Il token d'invito muore qui (uso singolo)."""
    row = find_by_invite(conn, token)
    if row is None:
        raise HTTPException(status_code=400, detail="invito non valido o scaduto")
    set_password(conn, row["id"], new_password, must_change=False)
    accountstore.set_invite(conn, row["id"], token_hash=None, expires_at=None)
    accountstore.activate(conn, row["id"])
    accountstore.delete_sessions(row["id"], conn)
    return user_out(get_user(conn, row["id"]))


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    accountstore.delete(conn, user_id)


def count_users(conn: sqlite3.Connection) -> int:
    return accountstore.count(conn)


def count_admins(conn: sqlite3.Connection) -> int:
    return accountstore.count_admins(conn)


def ensure_oidc_user(
    conn: sqlite3.Connection,
    *,
    email: str,
    username: str,
    role: str,
) -> dict:
    """Return the account for an OIDC identity, creating it on first login.

    The link is the verified email: an administrator who already created the account
    keeps its role and its projects. A new account gets the instance's default role and
    a password nobody knows — it is a random token hashed and discarded, so the local
    login form cannot be used for it until someone resets the password on purpose.
    """
    email = email.strip().lower()
    existing = accountstore.by_email(email, conn)
    if existing is not None:
        if not existing["active"]:
            raise HTTPException(status_code=403, detail="account disattivato")
        return existing

    base = normalize_username(username) or "utente"
    candidate = base
    suffix = 2
    while accountstore.username_taken(candidate, conn):
        candidate = f"{base}{suffix}"
        suffix += 1
    created = create_user(
        conn,
        username=candidate,
        password=security.new_token(),
        email=email,
        role=role,
        active=True,
        must_change_password=False,
    )
    return accountstore.get(created["id"], conn) or {}
