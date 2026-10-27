"""Persisted rate-limit windows (P7.1 of `docs/architecture/program.md`).

One row per key. The service owns the window arithmetic (when a window ends, whether an
attempt is allowed); this module owns the two statements and the row shape.
"""
from __future__ import annotations

from ..db import connect


def window(key: str, conn=None) -> tuple[float, int] | None:
    if conn is not None:
        row = conn.execute(
            "SELECT window_started, attempts FROM rate_limits WHERE key=?", (key,)
        ).fetchone()
    else:
        with connect() as own:
            row = own.execute(
                "SELECT window_started, attempts FROM rate_limits WHERE key=?", (key,)
            ).fetchone()
    if row is None:
        return None
    return float(row["window_started"]), int(row["attempts"])


def start(key: str, now: float, conn=None) -> None:
    statement = (
        "INSERT INTO rate_limits(key,window_started,attempts) VALUES(?,?,1) "
        "ON CONFLICT(key) DO UPDATE SET window_started=excluded.window_started, attempts=1"
    )
    if conn is not None:
        conn.execute(statement, (key, now))
        return
    with connect() as own:
        own.execute(statement, (key, now))


def bump(key: str, conn=None) -> None:
    if conn is not None:
        conn.execute("UPDATE rate_limits SET attempts=attempts+1 WHERE key=?", (key,))
        return
    with connect() as own:
        own.execute("UPDATE rate_limits SET attempts=attempts+1 WHERE key=?", (key,))


def clear(key: str, conn=None) -> None:
    if conn is not None:
        conn.execute("DELETE FROM rate_limits WHERE key=?", (key,))
        return
    with connect() as own:
        own.execute("DELETE FROM rate_limits WHERE key=?", (key,))
