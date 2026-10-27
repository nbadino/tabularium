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


def record_event(
    action: str,
    actor: dict | None = None,
    *,
    resource_type: str | None = None,
    resource_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record an event outside an existing transaction.

    Services that write a single key-value document (model settings) have no
    connection of their own anymore; this keeps the audit trail in one call without
    handing them a database handle.
    """
    from ..db import connect

    with connect() as conn:
        record(
            conn,
            actor,
            action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
        )
