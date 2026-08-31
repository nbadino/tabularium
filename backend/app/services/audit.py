"""Audit append-only minimale per le operazioni collaborative e amministrative."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def record(
    conn: sqlite3.Connection,
    actor: dict | None,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_events "
        "(actor_id, action, resource_type, resource_id, payload_json) VALUES (?,?,?,?,?)",
        (
            actor.get("id") if actor and actor.get("id") is not None else None,
            action,
            resource_type,
            resource_id,
            json.dumps(payload or {}, ensure_ascii=False),
        ),
    )
