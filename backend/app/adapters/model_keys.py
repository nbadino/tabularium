"""The `user_model_keys` table: each user's own API key for a saved model.

One row per (user, compute profile), holding the vault ciphertext of the key. The
service (`services/model_keys.py`) encrypts, decrypts and decides who may use what;
this module only reads and writes rows. Deleting a user or a profile deletes its rows
(ON DELETE CASCADE).
"""
from __future__ import annotations

from ..db import connect


def get(user_id: int, profile_id: int) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT ciphertext FROM user_model_keys WHERE user_id=? AND profile_id=?",
            (user_id, profile_id),
        ).fetchone()
    return str(row["ciphertext"]) if row is not None else None


def profiles_with_key(user_id: int) -> set[int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT profile_id FROM user_model_keys WHERE user_id=?", (user_id,)
        ).fetchall()
    return {int(row["profile_id"]) for row in rows}


def put(user_id: int, profile_id: int, ciphertext: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO user_model_keys (user_id, profile_id, ciphertext) VALUES (?,?,?) "
            "ON CONFLICT(user_id, profile_id) DO UPDATE SET ciphertext=excluded.ciphertext, "
            "updated_at=datetime('now')",
            (user_id, profile_id, ciphertext),
        )


def delete(user_id: int, profile_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM user_model_keys WHERE user_id=? AND profile_id=?", (user_id, profile_id)
        )
    return cursor.rowcount > 0
