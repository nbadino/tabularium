"""Migrazioni SQLite versionate.

Il contratto (AGENTS.md §6): versione corrente → applica N+1 → aggiorna meta.
Questi test simulano DB alle versioni precedenti e verificano l'upgrade, più
la difesa contro uno sviluppatore che aggiunge una migrazione senza
aggiornare SCHEMA_VERSION.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import db, config


def _fresh_conn() -> sqlite3.Connection:
    """Connessione a un DB pulito (la tmp root di conftest)."""
    config.ensure_dirs()
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    return db.connect()


def _version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    return row["value"] if row else None


def test_new_db_is_stamped_with_current_version():
    with _fresh_conn() as conn:
        db.init_db()
        assert _version(conn) == db.SCHEMA_VERSION


def test_legacy_db_without_version_gets_baseline_then_current():
    """Un DB senza ``schema_version`` registrata (pre-storia) non viene
    sovrascritto in silenzio: registra la baseline, poi le migrazioni."""
    with _fresh_conn() as conn:
        conn.executescript(db._SCHEMA)
        conn.commit()
        assert _version(conn) is None

    db.init_db()

    with db.connect() as conn:
        assert int(_version(conn)) == int(db.SCHEMA_VERSION)


def test_versioned_migration_applies_once_in_order(monkeypatch):
    """Un DB alla baseline riceve le migrazioni e registra la versione corrente."""
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
        conn.execute("ALTER TABLE pages DROP COLUMN annotation_revision")
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        assert _version(conn) == db.SCHEMA_VERSION
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
        assert "annotation_revision" in cols


def test_migration_not_reapplied_on_second_init(monkeypatch):
    """Le migrazioni sono una volta sola: se un secondo init le ri-applicasse,
    ALTER TABLE fallirebbe per colonna duplicata — quindi il successo del
    secondo init è la prova che non sono state rieseguite."""
    with _fresh_conn() as conn:
        db.init_db()

    db.init_db()  # prima applicazione

    # Un terzo init con lo stesso schema non ri-applica nulla.
    db.init_db()

    with db.connect() as conn:
        assert _version(conn) == db.SCHEMA_VERSION
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
        assert "annotation_revision" in cols


def test_incomplete_migration_chain_raises(monkeypatch):
    """Aggiungere una migrazione senza aggiornare SCHEMA_VERSION è un errore
    visibile all'avvio, non un DB silenziosamente indietro."""
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
        conn.execute("ALTER TABLE pages DROP COLUMN annotation_revision")
        conn.commit()

    monkeypatch.setattr(db, "_MIGRATIONS", [])  # dimenticata la migrazione 7/8

    with pytest.raises(RuntimeError, match="manca una migrazione"):
        db.init_db()


def test_projects_owner_id_has_foreign_key_and_restricts_delete():
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            ("owner", "hash", "salt"),
        )
        owner_id = conn.execute("SELECT id FROM users WHERE username='owner'").fetchone()[0]
        conn.execute(
            "INSERT INTO projects (name, root_dir, archive_dir, settings_json, owner_id) "
            "VALUES (?, ?, ?, ?, ?)",
            ("owned", "/tmp", "/tmp", "{}", owner_id),
        )
        foreign_keys = conn.execute("PRAGMA foreign_key_list(projects)").fetchall()
        assert any(row[2] == "users" and row[6] == "RESTRICT" for row in foreign_keys)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM users WHERE id=?", (owner_id,))
