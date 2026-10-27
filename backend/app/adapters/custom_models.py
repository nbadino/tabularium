"""The `custom_models` table: fine-tuned checkpoints and registered HF models (P7.1).

One row makes a model appear in the model list and in the picker. The service owns the
naming rules (unique id from the display name, `served_model_name`), the checkpoint merge
and the file work; here are the rows.
"""
from __future__ import annotations

from typing import Any

from ..db import connect

MERGED_COLUMNS = (
    "id", "display_name", "hf_repo", "hf_revision", "served_model_name",
    "trust_remote_code", "max_model_len", "gpu_memory_utilization", "extra_args",
    "source_path", "base_adapter_id", "source_run_id", "source_train_type",
)
PLAIN_COLUMNS = (
    "id", "display_name", "hf_repo", "hf_revision", "served_model_name",
    "trust_remote_code", "max_model_len", "gpu_memory_utilization", "extra_args",
)


def exists(adapter_id: str, conn=None) -> bool:
    if conn is not None:
        return conn.execute(
            "SELECT 1 FROM custom_models WHERE id=?", (adapter_id,)
        ).fetchone() is not None
    with connect() as own:
        return own.execute(
            "SELECT 1 FROM custom_models WHERE id=?", (adapter_id,)
        ).fetchone() is not None


def get(adapter_id: str, conn=None) -> dict[str, Any] | None:
    statement = "SELECT * FROM custom_models WHERE id=?"
    if conn is not None:
        row = conn.execute(statement, (adapter_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (adapter_id,)).fetchone()
    return dict(row) if row is not None else None


def by_source_run(run_id: str, conn=None) -> dict[str, Any] | None:
    statement = "SELECT * FROM custom_models WHERE source_run_id=?"
    if conn is not None:
        row = conn.execute(statement, (run_id,)).fetchone()
    else:
        with connect() as own:
            row = own.execute(statement, (run_id,)).fetchone()
    return dict(row) if row is not None else None


def insert(fields: dict[str, Any], *, merged: bool = False, conn=None) -> None:
    """Insert a row; `merged` selects the wider column set of a fine-tuned checkpoint."""
    columns = MERGED_COLUMNS if merged else PLAIN_COLUMNS
    placeholders = ",".join("?" for _ in columns)
    statement = (
        f"INSERT INTO custom_models({','.join(columns)}) VALUES({placeholders})"
    )
    values = tuple(fields.get(column) for column in columns)
    if conn is not None:
        conn.execute(statement, values)
        return
    with connect() as own:
        own.execute(statement, values)


def list_all() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM custom_models ORDER BY created_at DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def delete(adapter_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM custom_models WHERE id=?", (adapter_id,))
