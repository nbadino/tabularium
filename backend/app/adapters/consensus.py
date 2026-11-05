"""Persistence for consensus runs: several models on the same pages.

`services/consensus.py` decides which profiles run, when a model failed and how the
run's state is derived; this module owns `consensus_runs` and `consensus_outputs`.
Nothing here touches `blocks` or `tables`: a comparison never edits the annotations.
"""
from __future__ import annotations

import json
from typing import Any

from ..db import connect


class MissingPages(RuntimeError):
    def __init__(self, ids: list[int]) -> None:
        super().__init__(f"pages not in project: {ids}")
        self.ids = ids


def create(
    project_id: int,
    page_ids: list[int],
    models: list[dict[str, Any]],
    *,
    owner_id: int | None,
) -> int:
    """Insert a queued run and one queued output per page and model."""
    with connect() as conn:
        placeholders = ",".join("?" for _ in page_ids)
        found = {
            int(row["id"])
            for row in conn.execute(
                f"SELECT id FROM pages WHERE project_id=? AND id IN ({placeholders})",
                (project_id, *page_ids),
            )
        }
        missing = [pid for pid in page_ids if pid not in found]
        if missing:
            raise MissingPages(missing)
        cursor = conn.execute(
            "INSERT INTO consensus_runs (project_id, owner_id, state, models_json) "
            "VALUES (?,?, 'queued', ?)",
            (project_id, owner_id, json.dumps(models, ensure_ascii=False)),
        )
        run_id = int(cursor.lastrowid or 0)
        conn.executemany(
            "INSERT INTO consensus_outputs (run_id, page_id, model_key, state) "
            "VALUES (?,?,?, 'queued')",
            [(run_id, pid, model["key"]) for pid in page_ids for model in models],
        )
        conn.commit()
    return run_id


def _run_out(row) -> dict[str, Any]:
    out = dict(row)
    out["models"] = json.loads(out.pop("models_json") or "[]")
    return out


def get(run_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM consensus_runs WHERE id=?", (run_id,)).fetchone()
    return _run_out(row) if row is not None else None


def list_runs(project_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM consensus_runs WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return [_run_out(row) for row in rows]


def counts(run_id: int) -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM consensus_outputs WHERE run_id=? GROUP BY state",
            (run_id,),
        ).fetchall()
    return {str(row["state"]): int(row["n"]) for row in rows}


def outputs_for_model(run_id: int, model_key: str) -> list[dict[str, Any]]:
    """The queued work of one model, in page order."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, page_id, state FROM consensus_outputs "
            "WHERE run_id=? AND model_key=? ORDER BY id",
            (run_id, model_key),
        ).fetchall()
    return [dict(row) for row in rows]


def set_state(run_id: int, state: str, *, started: str | None = None, ended: str | None = None,
              error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE consensus_runs SET state=?, started_at=COALESCE(?, started_at), "
            "ended_at=COALESCE(?, ended_at), error=COALESCE(?, error) WHERE id=?",
            (state, started, ended, error, run_id),
        )
        conn.commit()


def mark_output(output_id: int, state: str, **values: Any) -> None:
    allowed = {"blocks_json", "error", "started_at", "ended_at"}
    fields = {key: value for key, value in values.items() if key in allowed}
    assignments = "".join(f", {key}=?" for key in fields)
    with connect() as conn:
        conn.execute(
            f"UPDATE consensus_outputs SET state=?{assignments} WHERE id=?",
            (state, *fields.values(), output_id),
        )
        conn.commit()


def requeue_failed(run_id: int) -> int:
    """Put the failed and cancelled outputs back in the queue; how many there were."""
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE consensus_outputs SET state='queued', error=NULL, started_at=NULL, "
            "ended_at=NULL WHERE run_id=? AND state IN ('failed','cancelled')",
            (run_id,),
        )
        count = int(cursor.rowcount or 0)
        if count:
            conn.execute(
                "UPDATE consensus_runs SET state='queued', error=NULL, ended_at=NULL WHERE id=?",
                (run_id,),
            )
        conn.commit()
    return count


def latest_run_for_page(page_id: int) -> int | None:
    """The most recent run that has at least one finished output for the page."""
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(run_id) AS run_id FROM consensus_outputs "
            "WHERE page_id=? AND state='finished'",
            (page_id,),
        ).fetchone()
    return int(row["run_id"]) if row is not None and row["run_id"] is not None else None


def page_outputs(run_id: int, page_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT model_key, state, blocks_json, error FROM consensus_outputs "
            "WHERE run_id=? AND page_id=? ORDER BY id",
            (run_id, page_id),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["blocks"] = json.loads(item.pop("blocks_json") or "[]")
        out.append(item)
    return out


def finished_outputs(run_id: int, model_key: str, page_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """One model's finished pages of a run (all, or those in `page_ids`), with their blocks."""
    query = (
        "SELECT page_id, blocks_json FROM consensus_outputs "
        "WHERE run_id=? AND model_key=? AND state='finished'"
    )
    params: list[Any] = [run_id, model_key]
    if page_ids is not None:
        query += f" AND page_id IN ({','.join('?' for _ in page_ids)})"
        params.extend(page_ids)
    with connect() as conn:
        rows = conn.execute(query + " ORDER BY page_id", params).fetchall()
    return [{"page_id": int(row["page_id"]), "blocks": json.loads(row["blocks_json"] or "[]")} for row in rows]


def block_geometry(block_id: int) -> dict[str, Any] | None:
    """Page and rectangle of an annotation block (read only)."""
    with connect() as conn:
        row = conn.execute("SELECT page_id, points FROM blocks WHERE id=?", (block_id,)).fetchone()
    if row is None:
        return None
    return {"page_id": int(row["page_id"]), "points": json.loads(row["points"] or "[]")}


def reconcile(active_ids: set[int], *, message: str, ended: str) -> None:
    """After a restart, close the runs no thread is working on any more."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM consensus_runs WHERE state IN ('queued','running')"
        ).fetchall()
        for row in rows:
            run_id = int(row["id"])
            if run_id in active_ids:
                continue
            conn.execute(
                "UPDATE consensus_outputs SET state='failed', error=?, ended_at=? "
                "WHERE run_id=? AND state IN ('queued','running')",
                (message, ended, run_id),
            )
            conn.execute(
                "UPDATE consensus_runs SET state='failed', error=?, ended_at=? WHERE id=?",
                (message, ended, run_id),
            )
        conn.commit()
