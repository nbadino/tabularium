"""System reads for the admin surfaces (P7.1 of `docs/architecture/program.md`)."""
from __future__ import annotations

from typing import Any

from ..db import connect


def jobs(limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, owner_id, project_id, provider, pid, remote_job_id, state, "
            "heartbeat_at, command_json, log_path, recovery_strategy, started_at, "
            "ended_at, exit_code, error FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def schema_version() -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
    return str(row["value"]) if row is not None else None
