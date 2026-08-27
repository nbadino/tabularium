"""Layer di persistenza SQLite.

M1: schema con progetti + pagine. Versioning e micro-migrazioni per colonne
aggiunte in corso d'opera (il DB può essere già stato creato da una milestone
precedente).
"""
from __future__ import annotations

import sqlite3

from . import config

SCHEMA_VERSION = "5"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    root_dir      TEXT    NOT NULL,
    archive_dir   TEXT,
    settings_json TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rel_path    TEXT    NOT NULL,
    abs_path    TEXT    NOT NULL,
    source_kind TEXT    NOT NULL DEFAULT 'image',
    pdf_page    INTEGER,
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    issue_date  TEXT,
    issue_no    TEXT,
    page_no     TEXT,
    page_type   TEXT,
    status      TEXT    NOT NULL DEFAULT 'new',
    thumb_path  TEXT,
    meta_json   TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pages_project ON pages(project_id);

CREATE TABLE IF NOT EXISTS blocks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id        INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    label          TEXT    NOT NULL,
    kind           TEXT    NOT NULL DEFAULT 'rect',
    points         TEXT    NOT NULL DEFAULT '[]',
    content        TEXT    NOT NULL DEFAULT '',
    order_idx      INTEGER,
    prefill_source TEXT,
    confirmed      INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_blocks_page  ON blocks(page_id);
CREATE INDEX IF NOT EXISTS idx_blocks_order ON blocks(page_id, order_idx);

CREATE TABLE IF NOT EXISTS tables (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id   INTEGER NOT NULL UNIQUE REFERENCES blocks(id) ON DELETE CASCADE,
    grid_json  TEXT    NOT NULL DEFAULT '{}',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS page_reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id    INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    reviewer   TEXT NOT NULL DEFAULT 'local',
    status     TEXT NOT NULL CHECK(status IN ('pending','pass','fail')),
    errors_json TEXT NOT NULL DEFAULT '[]',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_page_reviews_page ON page_reviews(page_id);
"""

# Migrazioni additive: (tabella, check_colonna, DDL)
_MIGRATIONS = [
    ("projects", "archive_dir", "ALTER TABLE projects ADD COLUMN archive_dir TEXT"),
]


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, ddl in _MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(ddl)


def init_db() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _ensure_columns(conn)
        # Unicità per progetto su (abs_path, pagina). Con COALESCE perché NULL
        # non collide nelle UNIQUE index di SQLite (per le immagini pdf_page = NULL).
        conn.execute("DROP INDEX IF EXISTS idx_pages_unique")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_unique "
            "ON pages(project_id, abs_path, COALESCE(pdf_page, -1))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
