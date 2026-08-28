"""Gestione utenti (CRUD + password), separata dalla rete API.

Validazioni condivise (nome utente, ruolo, password) così setup, registrazione
e pannello admin usano le stesse regole.
"""
from __future__ import annotations

import re
import sqlite3

from fastapi import HTTPException

from . import security

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,40}$")
ROLES = {"admin", "editor", "viewer"}


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
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "role": row["role"],
        "active": bool(row["active"]),
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
) -> dict:
    username = normalize_username(username)
    validate_password(password)
    role = validate_role(role)
    existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail="nome utente già esistente")
    salt, hash_hex = security.hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (username, email, password_hash, salt, role, active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, email or None, hash_hex, salt, role, 1 if active else 0),
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


def set_password(conn: sqlite3.Connection, user_id: int, new_password: str) -> None:
    validate_password(new_password)
    salt, hash_hex = security.hash_password(new_password)
    conn.execute(
        "UPDATE users SET password_hash=?, salt=? WHERE id=?",
        (hash_hex, salt, user_id),
    )


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def count_admins(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role='admin'"
    ).fetchone()["n"]
