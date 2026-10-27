"""The `compute_profiles` table (P7.1 of `docs/architecture/program.md`).

One row describes one way of reaching a model: endpoint, adapter, served name, credential
reference, hardware profile. The row can be active, and exactly one may be. The service
decides when a profile is valid and whether it is healthy; this module owns the table, the
activation switch and its transaction.
"""
from __future__ import annotations

import json
from typing import Any

from ..db import connect

INSERT_COLUMNS = (
    "name", "provider", "purpose", "model_adapter_id", "model_revision",
    "served_model_name", "endpoint", "credential_ref", "runtime_recipe_id",
    "generation_profile_id", "image_profile_id", "hardware_profile_json",
)


def any_exists(conn=None) -> bool:
    if conn is not None:
        return conn.execute("SELECT 1 FROM compute_profiles LIMIT 1").fetchone() is not None
    with connect() as own:
        return own.execute("SELECT 1 FROM compute_profiles LIMIT 1").fetchone() is not None


def insert(fields: dict[str, Any], conn=None) -> int:
    """Insert a profile; missing columns get their schema default (NULL or '')."""
    values = {
        column: (
            json.dumps(fields.get("hardware_profile") or {})
            if column == "hardware_profile_json"
            else fields.get(column)
        )
        for column in INSERT_COLUMNS
    }
    placeholders = ",".join("?" for _ in INSERT_COLUMNS)
    statement = (
        f"INSERT INTO compute_profiles({','.join(INSERT_COLUMNS)}) VALUES({placeholders})"
    )
    if conn is not None:
        cursor = conn.execute(statement, tuple(values[column] for column in INSERT_COLUMNS))
        return int(cursor.lastrowid or 0)
    with connect() as own:
        cursor = own.execute(statement, tuple(values[column] for column in INSERT_COLUMNS))
        return int(cursor.lastrowid or 0)


def activate_only(profile_id: int, conn=None) -> None:
    """Make `profile_id` the only active profile."""
    statement = "UPDATE compute_profiles SET active=0 WHERE id != ?"
    if conn is not None:
        conn.execute(statement, (profile_id,))
        conn.execute("UPDATE compute_profiles SET active=1 WHERE id=?", (profile_id,))
        return
    with connect() as own:
        own.execute(statement, (profile_id,))
        own.execute("UPDATE compute_profiles SET active=1 WHERE id=?", (profile_id,))


def activate_all(conn=None) -> None:
    statement = "UPDATE compute_profiles SET active=1"
    if conn is not None:
        conn.execute(statement)
        return
    with connect() as own:
        own.execute(statement)


def list_all() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM compute_profiles ORDER BY active DESC, name"
        ).fetchall()
    return [dict(row) for row in rows]


def get(profile_id: int, conn=None) -> dict[str, Any] | None:
    statement = "SELECT * FROM compute_profiles WHERE id=?"
    if conn is not None:
        row = conn.execute(statement, (profile_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (profile_id,)).fetchone()
    return dict(row) if row is not None else None


def mark_unhealthy(profile_id: int, *, at: str, error: str, conn=None) -> None:
    statement = (
        "UPDATE compute_profiles SET last_health_check=?, last_health_ok=0, "
        "last_health_error=?, updated_at=? WHERE id=?"
    )
    if conn is not None:
        conn.execute(statement, (at, error, at, profile_id))
        return
    with connect() as own:
        own.execute(statement, (at, error, at, profile_id))


def mark_healthy(profile_id: int, *, at: str, conn=None) -> None:
    statement = (
        "UPDATE compute_profiles SET last_health_check=?, last_health_ok=1, "
        "last_health_error=NULL, updated_at=? WHERE id=?"
    )
    if conn is not None:
        conn.execute(statement, (at, at, profile_id))
        return
    with connect() as own:
        own.execute(statement, (at, at, profile_id))


def deactivate_all(*, at: str, conn=None) -> None:
    statement = "UPDATE compute_profiles SET active=0, updated_at=?"
    if conn is not None:
        conn.execute(statement, (at,))
        return
    with connect() as own:
        own.execute(statement, (at,))
