"""Persistence for recognition sessions: runs, their pages, and the reviewed blocks.

Phase P3b of `docs/architecture/program.md`. `services/recognition.py` decides when a
session starts, which page failed and how the state is derived; this module owns the
tables. The two are separate because the decisions are what the tests reason about, and
the queries are what changes when the schema does.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import connect


class ModelBusy(RuntimeError):
    """An inference session is already running; the id travels with the error."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"model run {run_id} is active")
        self.run_id = run_id


class PagesBusy(RuntimeError):
    """One of the requested pages is already in a queued or running session."""

    def __init__(self, run_id: int) -> None:
        super().__init__(f"pages already in run {run_id}")
        self.run_id = run_id


class MissingPages(RuntimeError):
    def __init__(self, ids: list[int]) -> None:
        super().__init__(f"pages not in project: {ids}")
        self.ids = ids


def active_provider() -> str | None:
    """The provider of the active compute profile, when the table is readable."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT provider FROM compute_profiles WHERE active=1 LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    return str(row["provider"]) if row is not None else None


def get(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM recognition_runs WHERE id=?", (run_id,)).fetchone()
    return dict(row) if row is not None else None


def list_runs(project_id: int | None, limit: int) -> list[dict[str, Any]]:
    with connect() as conn:
        if project_id is None:
            rows = conn.execute(
                "SELECT * FROM recognition_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recognition_runs WHERE project_id=? ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def items_for_run(run_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT i.*, p.rel_path, p.pdf_page, p.status AS page_status,
                      (SELECT COUNT(*) FROM blocks b
                        WHERE b.recognition_run_id=i.run_id AND b.page_id=i.page_id) AS blocks,
                      (SELECT COUNT(*) FROM blocks b
                        WHERE b.recognition_run_id=i.run_id AND b.page_id=i.page_id
                          AND b.confirmed=0) AS drafts
                 FROM recognition_run_items i
                 JOIN pages p ON p.id=i.page_id
                WHERE i.run_id=? ORDER BY i.id""",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def check(project_id: int, page_ids: list[int], *, engine: str) -> None:
    """The conditions that must hold before queueing a session, checked first.

    Split from `create` because the service probes the endpoint between the check and
    the insert: probing before knowing that the model is already busy would report the
    wrong problem.
    """
    placeholders = ",".join("?" for _ in page_ids)
    with connect() as conn:
        if engine == "model":
            active = conn.execute(
                "SELECT id FROM recognition_runs "
                "WHERE engine='model' AND state IN ('queued','running') "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if active is not None:
                raise ModelBusy(int(active["id"]))
        busy = conn.execute(
            "SELECT r.id FROM recognition_runs r JOIN recognition_run_items i ON i.run_id = r.id "
            f"WHERE r.state IN ('queued','running') AND i.page_id IN ({placeholders}) LIMIT 1",
            page_ids,
        ).fetchone()
        if busy is not None:
            raise PagesBusy(int(busy["id"]))
        rows = conn.execute(
            f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})",
            (project_id, *page_ids),
        ).fetchall()
    found = {int(row["id"]) for row in rows}
    missing = [value for value in page_ids if value not in found]
    if missing:
        raise MissingPages(missing)


def create(
    project_id: int,
    page_ids: list[int],
    *,
    engine: str,
    mode: str,
    model_mode: str,
    stop_policy: str,
    owner_id: int | None,
    model_name: str,
    adapter_id: str,
    provider: str,
    endpoint: str | None,
) -> int:
    """Insert the job, the session and its pages. Returns the session id."""
    placeholders = ",".join("?" for _ in page_ids)
    with connect() as conn:
        if engine == "model":
            active = conn.execute(
                "SELECT id FROM recognition_runs "
                "WHERE engine='model' AND state IN ('queued','running') "
                "ORDER BY id LIMIT 1"
            ).fetchone()
            if active is not None:
                raise ModelBusy(int(active["id"]))
        # Two sessions on the same page step on each other: each removes drafts at the
        # start and writes its own, so both would survive doubled.
        busy = conn.execute(
            "SELECT r.id FROM recognition_runs r JOIN recognition_run_items i ON i.run_id = r.id "
            f"WHERE r.state IN ('queued','running') AND i.page_id IN ({placeholders}) LIMIT 1",
            page_ids,
        ).fetchone()
        if busy is not None:
            raise PagesBusy(int(busy["id"]))
        rows = conn.execute(
            f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})",
            (project_id, *page_ids),
        ).fetchall()
        found = {int(row["id"]) for row in rows}
        missing = [value for value in page_ids if value not in found]
        if missing:
            raise MissingPages(missing)

        job = conn.execute(
            """INSERT INTO jobs
               (kind, owner_id, project_id, provider, state, heartbeat_at,
                command_json, recovery_strategy)
               VALUES ('recognition',?,?,?,?,?,?,?)""",
            (
                owner_id,
                project_id,
                provider,
                "queued",
                _now(),
                json.dumps({"page_ids": page_ids, "engine": engine}),
                "persisted-results-mark-interrupted",
            ),
        )
        run = conn.execute(
            """INSERT INTO recognition_runs
               (project_id, owner_id, job_id, state, engine, mode, model_mode,
                model_name, adapter_id, provider, endpoint, stop_policy, total_pages)
               VALUES (?,?,?,'queued',?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                owner_id,
                job.lastrowid,
                engine,
                mode,
                model_mode,
                model_name,
                adapter_id,
                provider,
                endpoint,
                stop_policy,
                len(page_ids),
            ),
        )
        run_id = int(run.lastrowid)
        conn.executemany(
            "INSERT INTO recognition_run_items (run_id,page_id,state) VALUES (?,?,'queued')",
            [(run_id, page_id) for page_id in page_ids],
        )
    return run_id


def start(run_id: int, started: str) -> dict[str, Any] | None:
    """Mark the session and its job as running; returns the session row."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM recognition_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE recognition_runs SET state='running',started_at=?,heartbeat_at=? WHERE id=?",
            (started, started, run_id),
        )
        conn.execute(
            "UPDATE jobs SET state='running',heartbeat_at=? WHERE id=?", (started, row["job_id"])
        )
    return dict(row)


def count_items(run_id: int) -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT state,COUNT(*) AS n FROM recognition_run_items WHERE run_id=? GROUP BY state",
            (run_id,),
        ).fetchall()
    return {str(row["state"]): int(row["n"]) for row in rows}


def finish(run_id: int, job_id: int | None, *, state: str, job_state: str, ended: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE recognition_runs SET state=?,ended_at=?,heartbeat_at=? WHERE id=?",
            (state, ended, ended, run_id),
        )
        conn.execute(
            "UPDATE jobs SET state=?,ended_at=?,heartbeat_at=? WHERE id=?",
            (job_state, ended, ended, job_id),
        )


def fail(run_id: int, job_id: int | None, *, error: str, ended: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE recognition_runs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
            (error, ended, ended, run_id),
        )
        conn.execute(
            "UPDATE jobs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
            (error, ended, ended, job_id),
        )


def set_error(run_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE recognition_runs SET state='finished_with_errors',error=? WHERE id=?",
            (error, run_id),
        )


def mark_item(item_id: int, state: str, **values: Any) -> None:
    fields = {"state": state, **values}
    assignments = ", ".join(f"{key}=?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE recognition_run_items SET {assignments} WHERE id=?",
            (*fields.values(), item_id),
        )


def refresh_counts(run_id: int) -> None:
    now = _now()
    with connect() as conn:
        counts = {
            row["state"]: int(row["n"])
            for row in conn.execute(
                "SELECT state,COUNT(*) AS n FROM recognition_run_items WHERE run_id=? GROUP BY state",
                (run_id,),
            ).fetchall()
        }
        completed = sum(counts.get(key, 0) for key in ("finished", "failed", "cancelled"))
        conn.execute(
            """UPDATE recognition_runs
                  SET completed_pages=?, succeeded_pages=?, failed_pages=?, heartbeat_at=?
                WHERE id=?""",
            (completed, counts.get("finished", 0), counts.get("failed", 0), now, run_id),
        )
        conn.execute(
            "UPDATE jobs SET heartbeat_at=? WHERE id=(SELECT job_id FROM recognition_runs WHERE id=?)",
            (now, run_id),
        )


def reconcile(active_ids: set[int], *, message: str, ended: str) -> None:
    """A restart keeps finished results: only the interrupted queue is marked."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,job_id FROM recognition_runs WHERE state IN ('queued','running')"
        ).fetchall()
        for row in rows:
            if int(row["id"]) in active_ids:
                continue
            conn.execute(
                "UPDATE recognition_runs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
                (message, ended, ended, row["id"]),
            )
            conn.execute(
                "UPDATE recognition_run_items SET state='failed',error=?,ended_at=? "
                "WHERE run_id=? AND state IN ('queued','running')",
                (message, ended, row["id"]),
            )
            if row["job_id"]:
                conn.execute(
                    "UPDATE jobs SET state='failed',error=?,ended_at=?,heartbeat_at=? WHERE id=?",
                    (message, ended, ended, row["job_id"]),
                )


def reviewed_blocks(page_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id,label,kind,points,content,order_idx,confirmed,prefill_source "
            "FROM blocks WHERE page_id=? ORDER BY COALESCE(order_idx,999999),id",
            (page_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
