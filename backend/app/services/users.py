"""Gestione utenti (CRUD + password), separata dalla rete API.

Validazioni condivise (nome utente, ruolo, password) così setup, registrazione
e pannello admin usano le stesse regole.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

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
    existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="nome utente già esistente")
    salt, hash_hex = security.hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, email, password_hash, salt, role, active, must_change_password) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (username, email or None, hash_hex, salt, role, 1 if active else 0, 1 if must_change_password else 0),
    )
    return user_out(conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [user_out(r) for r in rows]


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
    if role is not None:
        role = validate_role(role)
        conn.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
    if active is not None:
        conn.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, user_id))
    if email is not None:
        conn.execute("UPDATE users SET email=? WHERE id=?", (email or None, user_id))
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
    conn.execute(
        "UPDATE users SET password_hash=?, salt=?, must_change_password=? WHERE id=?",
        (hash_hex, salt, 1 if must_change else 0, user_id),
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
    conn.execute(
        "UPDATE users SET invite_token_hash=?, invite_expires_at=?, must_change_password=1 WHERE id=?",
        (security.hash_token(token), expires, user_id),
    )
    return token, expires


def find_by_invite(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Utente il cui invito è valido (token presente e non scaduto), o None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT * FROM users WHERE invite_token_hash=?", (security.hash_token(token),)
    ).fetchone()
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
    conn.execute(
        "UPDATE users SET invite_token_hash=NULL, invite_expires_at=NULL, active=1 WHERE id=?",
        (row["id"],),
    )
    conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    return user_out(get_user(conn, row["id"]))


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def count_admins(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role='admin'"
    ).fetchone()["n"]
