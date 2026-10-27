"""The `jobs` table: the record of work that outlives a request (P7.1).

Modal, the serve manager, the cloud manager and recognition each wrote their own four or
five `UPDATE jobs …` with slightly different columns and timestamps. The table has one
owner now; the services keep the decisions (when a job is done, what a failure means).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import connect


def finish(job_id: int, state: str, exit_code: int | None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, ended_at=datetime('now'), exit_code=?, "
            "heartbeat_at=datetime('now') WHERE id=?",
            (state, exit_code, job_id),
        )


def running(kind: str) -> dict[str, Any] | None:
    """The most recent job of a kind still marked running, if the table exists."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE kind=? AND state='running' ORDER BY id DESC LIMIT 1",
                (kind,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row is not None else None


def fail(job_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state='failed', ended_at=datetime('now'), error=?, "
            "heartbeat_at=datetime('now') WHERE id=?",
            (error, job_id),
        )


def stop(job_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state='stopped', ended_at=datetime('now'), heartbeat_at=datetime('now'), "
            "error=? WHERE id=?",
            (error, job_id),
        )


def start_modal_job(
    *,
    owner_id: int | None,
    pid: int,
    process_group: int,
    command: dict[str, Any],
    log_path: str,
) -> int:
    """Record a launched Modal task; returns the job id."""
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, "
            "heartbeat_at, command_json, log_path, recovery_strategy) "
            "VALUES('modal', ?, 'modal', ?, ?, 'running', datetime('now'), ?, ?, 'pid-process-group')",
            (owner_id, pid, process_group, json.dumps(command), log_path),
        )
        return int(cursor.lastrowid or 0)


def start_process_job(
    *,
    kind: str,
    owner_id: int | None,
    provider: str,
    pid: int,
    process_group: int,
    state: str,
    command: dict[str, Any],
    log_path: str,
    recovery_strategy: str = "pid-process-group",
) -> int:
    """Record a process this backend launched (serve, modal, training)."""
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, "
            "heartbeat_at, command_json, log_path, recovery_strategy) "
            "VALUES(?,?,?,?,?,?,datetime('now'),?,?,?)",
            (kind, owner_id, provider, pid, process_group, state, json.dumps(command), log_path, recovery_strategy),
        )
        return int(cursor.lastrowid or 0)


def mark(job_id: int, state: str, *, error: str | None = None, exit_code: int | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, ended_at=datetime('now'), exit_code=?, error=?, "
            "heartbeat_at=datetime('now') WHERE id=?",
            (state, exit_code, error, job_id),
        )


def start_training_job(
    *,
    owner_id: int | None,
    project_id: int,
    provider: str,
    pid: int | None,
    remote_job_id: str | None,
    command: dict[str, Any],
    log_path: str,
    recovery_strategy: str,
    heartbeat_at: str,
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO jobs(kind, owner_id, project_id, provider, pid, process_group, "
            "remote_job_id, state, heartbeat_at, command_json, log_path, recovery_strategy) "
            "VALUES('training', ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?)",
            (
                owner_id, project_id, provider, pid, pid, remote_job_id,
                heartbeat_at, json.dumps(command), log_path, recovery_strategy,
            ),
        )
        return int(cursor.lastrowid or 0)


def of_project(project_id: int, kind: str, *, state: str | None = None) -> list[dict[str, Any]]:
    """Every job of a kind for a project, newest first."""
    statement = "SELECT * FROM jobs WHERE project_id=? AND kind=?"
    params: list[Any] = [project_id, kind]
    if state is not None:
        statement += " AND state=?"
        params.append(state)
    statement += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(statement, params).fetchall()
    return [dict(row) for row in rows]


def latest(project_id: int, kind: str) -> dict[str, Any] | None:
    """The most recent job of a kind for a project, whatever its state.

    Tolerates a database created before the first migration: the caller answers
    "no run yet" instead of failing.
    """
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                (project_id, kind),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table: jobs" not in str(exc):
            raise
        return None
    return dict(row) if row is not None else None


def of_kind_state(kind: str, state: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, pid, provider FROM jobs WHERE kind=? AND state=?",
            (kind, state),
        ).fetchall()
    return [dict(row) for row in rows]


def settle(job_id: int, state: str, *, ended: str, exit_code: int | None = None) -> None:
    """Close a job: state, end time, exit code and heartbeat in one statement."""
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, ended_at=?, exit_code=?, heartbeat_at=? WHERE id=?",
            (state, ended, exit_code, ended, job_id),
        )


def mark_state(job_id: int, state: str, *, ended: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, ended_at=?, error=?, heartbeat_at=? WHERE id=?",
            (state, ended, error, ended, job_id),
        )


def stop_running(kind: str) -> None:
    """Mark every running job of a kind as stopped (a new tunnel replaces the old one)."""
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state='stopped', ended_at=datetime('now'), heartbeat_at=datetime('now') "
            "WHERE kind=? AND state='running'",
            (kind,),
        )


def start_tunnel_job(
    *, owner_id: int | None, pid: int, process_group: int, command: dict[str, Any]
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, "
            "heartbeat_at, command_json, recovery_strategy) "
            "VALUES('ssh_tunnel', ?, 'ssh', ?, ?, 'running', datetime('now'), ?, 'pid-process-group')",
            (owner_id, pid, process_group, json.dumps(command)),
        )
        return int(cursor.lastrowid or 0)


def touch(job_id: int, state: str, *, error: str | None = None) -> None:
    """Update the state and, for a terminal state, stamp the end; otherwise keep it."""
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, heartbeat_at=datetime('now'), "
            "ended_at=CASE WHEN ? IN ('stopped','failed') THEN datetime('now') ELSE ended_at END, "
            "error=? WHERE id=?",
            (state, state, error, job_id),
        )


def latest_running(kind: str, *, columns: str = "*") -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {columns} FROM jobs WHERE kind=? AND state='running' ORDER BY id DESC LIMIT 1",
            (kind,),
        ).fetchone()
    return dict(row) if row is not None else None


def latest_of_kind(kind: str, *, columns: str = "*") -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT {columns} FROM jobs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
    return dict(row) if row is not None else None


def cloud_upsert(
    *,
    owner_id: int | None,
    provider: str,
    remote_id: str,
    state: str,
    cost: dict[str, Any],
    terminal: bool,
) -> None:
    """Record a provider-side resource, or refresh the row that already tracks it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE kind='cloud_resource' AND provider=? AND remote_job_id=? "
            "ORDER BY id DESC LIMIT 1",
            (provider, remote_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO jobs(kind, owner_id, provider, remote_job_id, state, heartbeat_at, "
                "cost_json, recovery_strategy, ended_at) "
                "VALUES('cloud_resource', ?, ?, ?, ?, datetime('now'), ?, 'provider-api', "
                "CASE WHEN ? THEN datetime('now') ELSE NULL END)",
                (owner_id, provider, remote_id, state, json.dumps(cost), terminal),
            )
            return
        conn.execute(
            "UPDATE jobs SET state=?, heartbeat_at=datetime('now'), cost_json=?, "
            "ended_at=CASE WHEN ? THEN COALESCE(ended_at, datetime('now')) ELSE NULL END "
            "WHERE id=?",
            (state, json.dumps(cost), terminal, row["id"]),
        )


def cloud_usage(provider: str, remote_id: str) -> tuple[float, float] | None:
    """`(hours, hourly_rate)` for the provider's resource.

    None when there is no row, no hourly rate, or the jobs table does not exist yet —
    a cost that cannot be estimated is not zero.
    """
    statement = (
        "SELECT started_at, ended_at, cost_json, "
        "(julianday(COALESCE(ended_at, datetime('now'))) - julianday(started_at)) * 24.0 AS hours "
        "FROM jobs WHERE kind='cloud_resource' AND provider=? AND remote_job_id=? "
        "ORDER BY id DESC LIMIT 1"
    )
    try:
        with connect() as conn:
            row = conn.execute(statement, (provider, str(remote_id).strip())).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    try:
        rate = float(json.loads(row["cost_json"] or "{}").get("hourly_rate"))
    except (TypeError, ValueError, KeyError):
        return None
    return max(0.0, float(row["hours"] or 0.0)), rate

