"""A migration and its version number land together, or neither does."""
from __future__ import annotations

import sqlite3

import pytest

from app import db


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.commit()
    return conn


def test_a_failing_migration_leaves_no_partial_schema_and_no_version() -> None:
    conn = _conn()
    with pytest.raises(sqlite3.Error):
        db._run_migration(conn, "2", "CREATE TABLE half (x); INSERT INTO missing VALUES (1);")
    assert conn.execute("SELECT value FROM meta").fetchone()[0] == "1"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='half'").fetchone() is None


def test_the_foreign_key_pragma_still_applies_around_the_transaction() -> None:
    conn = _conn()
    conn.execute("PRAGMA foreign_keys = ON")
    db._run_migration(conn, "2", "PRAGMA foreign_keys = OFF;\nCREATE TABLE t (x);\nPRAGMA foreign_keys = ON;")
    assert conn.execute("SELECT value FROM meta").fetchone()[0] == "2"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_table_rebuild_does_not_cascade_into_its_children() -> None:
    conn = _conn()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (id INTEGER PRIMARY KEY, p INTEGER REFERENCES parent(id) ON DELETE CASCADE);"
        "INSERT INTO parent VALUES (1); INSERT INTO child VALUES (1, 1);"
    )
    db._run_migration(conn, "2", """
        PRAGMA foreign_keys = OFF;
        CREATE TABLE parent_new (id INTEGER PRIMARY KEY, extra TEXT);
        INSERT INTO parent_new (id) SELECT id FROM parent;
        DROP TABLE parent;
        ALTER TABLE parent_new RENAME TO parent;
        PRAGMA foreign_keys = ON;
    """)
    assert conn.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 1
