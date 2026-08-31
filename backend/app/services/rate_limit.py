"""Rate limiting persistente per gli endpoint di autenticazione."""
from __future__ import annotations

import time
import sqlite3


def allow(conn: sqlite3.Connection, key: str, *, limit: int = 10, window: float = 60.0) -> bool:
    now = time.time()
    row = conn.execute("SELECT window_started, attempts FROM rate_limits WHERE key=?", (key,)).fetchone()
    if row is None or now - float(row["window_started"]) >= window:
        conn.execute("INSERT INTO rate_limits(key,window_started,attempts) VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET window_started=excluded.window_started, attempts=1", (key, now))
        return True
    if int(row["attempts"]) >= limit:
        return False
    conn.execute("UPDATE rate_limits SET attempts=attempts+1 WHERE key=?", (key,))
    return True


def reset(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM rate_limits WHERE key=?", (key,))
