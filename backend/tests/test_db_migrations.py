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
        conn.execute("DROP TRIGGER IF EXISTS invalidate_team_approval")
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
        conn.execute("DROP TRIGGER IF EXISTS invalidate_team_approval")
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


def test_additive_migrations_have_no_semicolon_in_comments():
    """Additive migrations are split on ';' before comments are stripped: a ';' in a
    comment cuts a statement in half (it broke migration 22 once)."""
    import re as _re

    additive = {"16", "17", "18", "21", "22", "25"}
    for version, ddl in db._MIGRATIONS:
        if version not in additive:
            continue
        for line in ddl.splitlines():
            if _re.match(r"\s*--", line):
                assert ";" not in line, f"migration {version}: ';' in a comment: {line.strip()}"


def test_prerelease_consensus_db_at_23_gets_email_confirmed():
    """A pre-release build used version 23 for the consensus tables, and 0.7.0
    used it for ``users.email_confirmed``. A database stamped 23 by the former
    lacks the column; migration 24 must add it and keep the consensus rows."""
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("ALTER TABLE users DROP COLUMN email_confirmed")
        conn.execute("UPDATE meta SET value='23' WHERE key='schema_version'")
        conn.execute(
            "INSERT INTO projects (name, root_dir) VALUES ('p', '/tmp/p')"
        )
        conn.execute(
            "INSERT INTO consensus_runs (project_id, state) VALUES (1, 'finished')"
        )
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        assert _version(conn) == db.SCHEMA_VERSION
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        assert "email_confirmed" in cols
        assert conn.execute("SELECT count(*) FROM consensus_runs").fetchone()[0] == 1


def test_db_from_070_at_23_gets_consensus_tables():
    """A database upgraded by 0.7.0 (version 23, no consensus tables) gets them."""
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("DROP TABLE consensus_outputs")
        conn.execute("DROP TABLE consensus_runs")
        conn.execute("UPDATE meta SET value='23' WHERE key='schema_version'")
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"consensus_runs", "consensus_outputs"} <= tables


def test_db_at_24_gets_the_shared_consensus_decisions():
    """Migration 25: the decisions taken in a comparison move from each browser to the
    database, so a database at 24 gets the table and keeps its runs."""
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("DROP TABLE consensus_decisions")
        conn.execute("UPDATE meta SET value='24' WHERE key='schema_version'")
        conn.execute("INSERT INTO projects (name, root_dir) VALUES ('p', '/tmp/p')")
        conn.execute("INSERT INTO consensus_runs (project_id, state) VALUES (1, 'finished')")
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        assert _version(conn) == db.SCHEMA_VERSION
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(consensus_decisions)")}
        assert {
            "run_id", "page_id", "cluster_id", "block_id", "kind", "model_key", "before_json",
            "after_json", "comment", "user_id", "created_at", "about_id", "reverted_by",
        } <= cols
        assert conn.execute("SELECT count(*) FROM consensus_runs").fetchone()[0] == 1


def test_db_at_25_moves_saved_model_keys_to_their_admin():
    """Migration 26: a saved model's key belongs to the administrator who saved it.

    The key leaves the profile (where every user could spend it) for that admin's own
    row in `user_model_keys`, still encrypted, byte for byte: the migration never
    decrypts. Without a creator in the audit trail, the first active admin gets it; a
    database with no user at all (local mode) keeps the key on the profile.
    """
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("DROP TABLE user_model_keys")
        conn.execute("ALTER TABLE compute_profiles DROP COLUMN key_required")
        conn.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
        conn.execute("DELETE FROM compute_profiles")
        conn.execute("DELETE FROM users")
        for uid, name, role in ((10, "first", "admin"), (11, "creator", "admin"), (12, "ed", "editor")):
            conn.execute(
                "INSERT INTO users (id, username, password_hash, salt, role) VALUES (?,?,'h','s',?)",
                (uid, name, role),
            )
        profiles = (
            (1, "Argon", "gemini-argon", "custom", "vault:consensus_profile_aaa"),
            (2, "OCRia", "llamaindex-ocria-1", "llamaindex", "vault:consensus_profile_bbb"),
            (3, "Local GPU", "monkeyocrv2-parsing", "local", None),
            (4, "legacy-active", "monkeyocrv2-parsing", "local", "vault:inference"),
        )
        for pid, name, adapter, provider, ref in profiles:
            conn.execute(
                "INSERT INTO compute_profiles (id, name, provider, purpose, model_adapter_id, "
                "served_model_name, endpoint, credential_ref) VALUES (?,?,?,'inference',?,'m','https://x',?)",
                (pid, name, provider, adapter, ref),
            )
        for name, cipher in (("consensus_profile_aaa", "CIPHER-A"), ("consensus_profile_bbb", "CIPHER-B"), ("inference", "CIPHER-I")):
            conn.execute("INSERT INTO secrets (name, ciphertext) VALUES (?,?)", (name, cipher))
        conn.execute(
            "INSERT INTO audit_events (actor_id, action, resource_type, resource_id) "
            "VALUES (11, 'compute_profile.created', 'compute_profile', 1)"
        )
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        assert _version(conn) == db.SCHEMA_VERSION
        keys = {
            (r["user_id"], r["profile_id"]): r["ciphertext"]
            for r in conn.execute("SELECT * FROM user_model_keys")
        }
        assert keys == {(11, 1): "CIPHER-A", (10, 2): "CIPHER-B"}
        rows = {r["id"]: r for r in conn.execute("SELECT id, credential_ref, key_required FROM compute_profiles")}
        assert rows[1]["credential_ref"] is None and rows[2]["credential_ref"] is None
        assert rows[1]["key_required"] == 1 and rows[2]["key_required"] == 1
        assert rows[3]["key_required"] == 0
        # The global configuration's key (the active model) is not a saved model's key.
        assert rows[4]["credential_ref"] == "vault:inference"
        secrets = {r["name"] for r in conn.execute("SELECT name FROM secrets")}
        assert secrets == {"inference"}


def test_db_at_25_without_users_keeps_the_key_on_the_profile():
    with _fresh_conn() as conn:
        db.init_db()
        conn.execute("DROP TABLE user_model_keys")
        conn.execute("ALTER TABLE compute_profiles DROP COLUMN key_required")
        conn.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
        conn.execute("DELETE FROM compute_profiles")
        conn.execute("DELETE FROM users")
        conn.execute(
            "INSERT INTO compute_profiles (id, name, provider, purpose, model_adapter_id, "
            "served_model_name, endpoint, credential_ref) VALUES (1,'OCRia','llamaindex','inference',"
            "'llamaindex-ocria-1','m','https://x','vault:consensus_profile_ccc')"
        )
        conn.execute("INSERT INTO secrets (name, ciphertext) VALUES ('consensus_profile_ccc','C')")
        conn.commit()

    db.init_db()

    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM user_model_keys").fetchone()[0] == 0
        row = conn.execute("SELECT credential_ref, key_required FROM compute_profiles").fetchone()
        assert row["credential_ref"] == "vault:consensus_profile_ccc" and row["key_required"] == 1
        assert conn.execute("SELECT count(*) FROM secrets").fetchone()[0] == 1
