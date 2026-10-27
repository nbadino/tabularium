"""Where the inference endpoint is stored (P7.1 of `docs/architecture/program.md`).

Two tables hold one decision: the `inference_*` keys in `meta` (the original shape, still
read by scripts) and the active row of `compute_profiles` (the atomic shape since schema
v10). They are written in one transaction because a half-written pair is the bug this
whole area already paid for: a URL from one provider with the adapter of another.

The service keeps the meaning — which provider, which adapter, whether the identity
changed. This module keeps the two statements and the fallback rules.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..db import connect


def overrides() -> dict[str, str]:
    """The `inference_*` keys, tolerating a database that is not initialised yet."""
    try:
        with connect() as conn:
            return dict(
                conn.execute("SELECT key, value FROM meta WHERE key LIKE 'inference_%'").fetchall()
            )
    except sqlite3.Error:
        return {}


def active_profile() -> dict[str, Any] | None:
    """The active compute profile, or None on a database older than schema v10."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM compute_profiles WHERE active=1 LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row is not None else None


def save(
    items: list[tuple[str, str]],
    *,
    profile_update: dict[str, Any] | None = None,
    deactivate_profile: bool = False,
) -> None:
    """Upsert the legacy keys and, in the same transaction, the atomic profile."""
    with connect() as conn:
        for key, value in items:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        try:
            if profile_update is not None:
                # `credential_ref` is None when the caller has nothing new to say,
                # and COALESCE keeps the stored value in that case.
                conn.execute(
                    "UPDATE compute_profiles SET endpoint=?, served_model_name=?, "
                    "model_adapter_id=?, provider=?, hardware_profile_json=?, "
                    "credential_ref=COALESCE(?, credential_ref), updated_at=datetime('now') "
                    "WHERE id=?",
                    (
                        profile_update["endpoint"],
                        profile_update["served_model_name"],
                        profile_update["model_adapter_id"],
                        profile_update["provider"],
                        profile_update["hardware_profile_json"],
                        profile_update.get("credential_ref"),
                        profile_update["id"],
                    ),
                )
            elif deactivate_profile:
                conn.execute(
                    "UPDATE compute_profiles SET active=0, updated_at=datetime('now')"
                )
        except sqlite3.OperationalError:
            # Databases older than v10 keep working from the legacy keys.
            pass
