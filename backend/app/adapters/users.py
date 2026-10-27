"""User and session lookups the HTTP layer needs (P7.1 of `docs/architecture/program.md`).

The rules — you cannot demote the last administrator, you cannot delete your own
account, a user who owns projects must transfer them first — stay in the router or in
`services/users.py`. What moves here are the three counts and the session purge.
"""
from __future__ import annotations

from typing import Any

from ..db import connect


def owned_projects(user_id: int, conn=None) -> int:
    """Projects that would be orphaned. Pass the caller's connection inside a transaction."""
    if conn is not None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id=?", (user_id,)
        ).fetchone()
        return int(row["n"]) if row is not None else 0
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE owner_id=?", (user_id,)
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def count_admins(conn=None) -> int:
    """Every admin account, active or not: the guard is about not losing the last one."""
    statement = "SELECT COUNT(*) AS n FROM users WHERE role='admin'"
    if conn is not None:
        row = conn.execute(statement).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement).fetchone()
    return int(row["n"]) if row is not None else 0


def delete_sessions(user_id: int, conn=None) -> None:
    """A changed password invalidates the sessions that existed before it.

    Same rule as `owned_projects`: inside the caller's transaction, take its connection,
    otherwise a second writer waits on the first and the request fails with a lock.
    """
    if conn is not None:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def count_active_admins() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role='admin' AND active=1"
        ).fetchone()
    return int(row["n"]) if row is not None else 0


def count_users() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row is not None else 0


# --- accounts -------------------------------------------------------------
# The users service keeps validation (password rules, role vocabulary, the guards on the
# last admin) and the token generation; these functions own the rows. Most accept the
# caller's connection because registration and invites must be atomic with their audit
# entry, and a second writer inside that transaction would deadlock.

def by_username(username: str, conn=None) -> dict[str, Any] | None:
    return _one("SELECT * FROM users WHERE username=?", (username,), conn)


def username_taken(username: str, conn) -> bool:
    return conn.execute(
        "SELECT 1 FROM users WHERE username=?", (username,)
    ).fetchone() is not None


def by_email(email: str, conn=None) -> dict[str, Any] | None:
    return _one("SELECT * FROM users WHERE lower(email)=?", (email,), conn)


def by_invite_hash(token_hash: str, conn=None) -> dict[str, Any] | None:
    return _one("SELECT * FROM users WHERE invite_token_hash=?", (token_hash,), conn)


def get(user_id: int, conn=None) -> dict[str, Any] | None:
    return _one("SELECT * FROM users WHERE id=?", (user_id,), conn)


def list_accounts(conn=None) -> list[dict[str, Any]]:
    rows = _all("SELECT * FROM users ORDER BY username", (), conn)
    return rows


def insert(conn, *, username: str, email: str | None, password_hash: str, salt: str,
           role: str, active: bool, must_change_password: bool) -> int:
    cursor = conn.execute(
        "INSERT INTO users (username, email, password_hash, salt, role, active, "
        "must_change_password) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (username, email, password_hash, salt, role, 1 if active else 0,
         1 if must_change_password else 0),
    )
    return int(cursor.lastrowid or 0)


def update_fields(conn, user_id: int, fields: dict[str, Any]) -> None:
    """Partial update; the caller decides which columns change."""
    assignments = ", ".join(f"{key}=?" for key in fields)
    conn.execute(
        f"UPDATE users SET {assignments} WHERE id=?",
        (*fields.values(), user_id),
    )


def set_password(conn, user_id: int, *, password_hash: str, salt: str, must_change: bool) -> None:
    conn.execute(
        "UPDATE users SET password_hash=?, salt=?, must_change_password=? WHERE id=?",
        (password_hash, salt, 1 if must_change else 0, user_id),
    )


def set_invite(conn, user_id: int, *, token_hash: str | None, expires_at: str | None,
               must_change: bool | None = None) -> None:
    if must_change is None:
        conn.execute(
            "UPDATE users SET invite_token_hash=?, invite_expires_at=? WHERE id=?",
            (token_hash, expires_at, user_id),
        )
    else:
        conn.execute(
            "UPDATE users SET invite_token_hash=?, invite_expires_at=?, must_change_password=? "
            "WHERE id=?",
            (token_hash, expires_at, 1 if must_change else 0, user_id),
        )


def activate(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET active=1 WHERE id=?", (user_id,))


def delete(conn, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))


def count(conn=None) -> int:
    rows = _all("SELECT COUNT(*) AS n FROM users", (), conn)
    return int(rows[0]["n"]) if rows else 0


def _one(statement: str, params: tuple, conn) -> dict[str, Any] | None:
    if conn is not None:
        row = conn.execute(statement, params).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, params).fetchone()
    return dict(row) if row is not None else None


def _all(statement: str, params: tuple, conn) -> list[dict[str, Any]]:
    if conn is not None:
        rows = conn.execute(statement, params).fetchall()
    else:
        with connect() as own:
            rows = own.execute(statement, params).fetchall()
    return [dict(row) for row in rows]

