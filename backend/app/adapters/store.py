"""Key/value storage used for settings and short-lived state.

The `meta` table is a bag of small JSON documents: instance settings, per-model
overrides, the OIDC state rows. They shared no owner and each caller built its own
SQL; this module is the owner. A caller asks for a key and gets a value, so moving
that state (to a file, to a different database) does not touch the services.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import connect


def get(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def get_or(key: str, default: Any = None) -> Any:
    """Like `get`, but a database that does not exist yet yields the default.

    Building an inference client can happen before the lifespan created the schema
    (utilities, tests, scripts). A missing table is "no overrides", not an error; a
    corrupt value is "no overrides" too.
    """
    try:
        return get(key, default)
    except sqlite3.OperationalError:
        return default


def put(key: str, value: Any, conn=None) -> None:
    """Write a value. Pass the caller's connection to stay in its transaction.

    Opening a second writer from inside an open transaction is a deadlock on SQLite,
    not a slowdown: the two connections wait for each other until the timeout.
    """
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    statement = (
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    if conn is not None:
        conn.execute(statement, (key, payload))
        return
    with connect() as own:
        own.execute(statement, (key, payload))


def put_raw(key: str, value: str, conn=None) -> None:
    """Write a value that is already a string, without JSON encoding.

    The `inference_*` keys are read as raw strings by the client and by scripts that
    predate the JSON convention; encoding them would turn `1` into `"1"`.
    """
    statement = (
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    if conn is not None:
        conn.execute(statement, (key, value))
        return
    with connect() as own:
        own.execute(statement, (key, value))


def delete(key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM meta WHERE key=?", (key,))


def pop(key: str) -> Any:
    """Read a value and delete it in one transaction: the state rows are single use."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        conn.execute("DELETE FROM meta WHERE key=?", (key,))
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return None


def items_with_prefix(prefix: str) -> list[tuple[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", (f"{prefix}%",)).fetchall()
    out = []
    for row in rows:
        try:
            out.append((row["key"], json.loads(row["value"])))
        except (TypeError, ValueError):
            out.append((row["key"], None))
    return out


def delete_prefix(prefix: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM meta WHERE key LIKE ?", (f"{prefix}%",))
