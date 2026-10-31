"""Upgrades keep the user's data: a backup before migrating, a clear refusal on a
newer database, page paths that follow a moved data root, a CLI backup and an
offline restore (docs/guide/upgrading.md)."""
from __future__ import annotations

import json
import sqlite3
import tarfile

import pytest

from app import cli, config, db
from app.services import backup


def _reset_db() -> None:
    config.ensure_dirs()
    for suffix in ("", "-wal", "-shm"):
        (config.DB_PATH.parent / (config.DB_PATH.name + suffix)).unlink(missing_ok=True)
    for old in backup.backup_dir().glob("tabularium-*.db"):
        old.unlink()


def _set_version(version: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (version,))


def test_a_database_behind_is_backed_up_before_migrating():
    _reset_db()
    db.init_db()
    _set_version("19")
    db.init_db()
    names = [item["name"] for item in backup.list_backups()]
    assert any(name.endswith("-pre-migration-v19.db") for name in names)
    copy = backup.backup_dir() / next(n for n in names if n.endswith("-pre-migration-v19.db"))
    with sqlite3.connect(copy) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "19"


def test_a_current_database_is_not_backed_up_at_every_start():
    _reset_db()
    db.init_db()
    db.init_db()
    assert backup.list_backups() == []


def test_a_newer_database_is_refused_with_a_clear_message():
    _reset_db()
    db.init_db()
    _set_version(str(int(db.SCHEMA_VERSION) + 1))
    with pytest.raises(RuntimeError, match="newer than this release"):
        db.init_db()
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(int(db.SCHEMA_VERSION) + 1)
    _set_version(db.SCHEMA_VERSION)


def test_page_paths_follow_a_moved_data_root():
    _reset_db()
    db.init_db()
    old = "/old/place/Tabularium"
    root = str(config.ROOT_DIR)
    with db.connect() as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='data_root'", (old,))
        pid = conn.execute(
            "INSERT INTO projects(name, root_dir) VALUES('p', ?)", (f"{old}/projects",)
        ).lastrowid
        conn.execute(
            "INSERT INTO pages(project_id, rel_path, abs_path, thumb_path, meta_json) VALUES(?,?,?,?,?)",
            (pid, "a.png", f"{old}/projects/{pid}/uploads/a.png", f"{old}/thumbs/p1.jpg",
             json.dumps({"extracted_path": f"{old}/thumbs/p1_source.png", "other": 1})),
        )
        conn.execute(
            "INSERT INTO pages(project_id, rel_path, abs_path) VALUES(?,?,?)",
            (pid, "b.png", "/elsewhere/scans/b.png"),
        )
        # A sibling folder sharing the prefix is not under the old root.
        conn.execute(
            "INSERT INTO pages(project_id, rel_path, abs_path) VALUES(?,?,?)",
            (pid, "c.png", f"{old}-other/c.png"),
        )
    db.init_db()
    with db.connect() as conn:
        assert conn.execute("SELECT root_dir FROM projects WHERE id=?", (pid,)).fetchone()[0] == f"{root}/projects"
        rows = {r["rel_path"]: r for r in conn.execute("SELECT * FROM pages WHERE project_id=?", (pid,))}
        assert rows["a.png"]["abs_path"] == f"{root}/projects/{pid}/uploads/a.png"
        assert rows["a.png"]["thumb_path"] == f"{root}/thumbs/p1.jpg"
        assert json.loads(rows["a.png"]["meta_json"]) == {"extracted_path": f"{root}/thumbs/p1_source.png", "other": 1}
        assert rows["b.png"]["abs_path"] == "/elsewhere/scans/b.png"
        assert rows["c.png"]["abs_path"] == f"{old}-other/c.png"
        assert conn.execute("SELECT value FROM meta WHERE key='data_root'").fetchone()[0] == root
    assert any(item["name"].endswith("-pre-relocation.db") for item in backup.list_backups())


def test_cli_backup_archives_data_and_vault_key(tmp_path, monkeypatch):
    _reset_db()
    db.init_db()
    env_file = tmp_path / "program.env"
    env_file.write_text("TABULARIUM_VAULT_KEY=k\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    (config.DATA_DIR / "1").mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "1" / "scan.png").write_bytes(b"png")
    (config.ROOT_DIR / "mlx-runtime").mkdir(exist_ok=True)
    (config.ROOT_DIR / "mlx-runtime" / "big.bin").write_bytes(b"x")
    out = tmp_path / "b.tar.gz"
    assert cli.main(["backup", "--out", str(out)]) == 0
    with tarfile.open(out) as tar:
        names = tar.getnames()
        assert ".env" in names
        assert "data/projects/1/scan.png" in names
        assert "data/tabularium.db" in names
        assert "data/tabularium.db-wal" not in names and "data/tabularium.db-shm" not in names
        assert not any(n.startswith("data/mlx-runtime") for n in names)
        snapshot = tmp_path / "snap.db"
        snapshot.write_bytes(tar.extractfile("data/tabularium.db").read())
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_cli_restore_db_sets_the_old_database_aside_without_migrating(monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda port: False)
    _reset_db()
    db.init_db()
    _set_version("19")
    name = backup.create_backup(reason="manual")["name"]
    _set_version(db.SCHEMA_VERSION)
    assert cli.main(["restore-db", name]) == 2  # no --yes: nothing happens
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == db.SCHEMA_VERSION
    assert cli.main(["restore-db", name, "--yes"]) == 0
    with sqlite3.connect(config.DB_PATH) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "19"
    aside = list(config.ROOT_DIR.glob("pre-restore-*/tabularium.db"))
    assert aside, "the replaced database must be kept"
    db.init_db()


def test_cli_restore_db_refuses_while_the_app_runs(monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda port: True)
    _reset_db()
    db.init_db()
    name = backup.create_backup(reason="manual")["name"]
    before = set(config.ROOT_DIR.glob("pre-restore-*"))
    assert cli.main(["restore-db", name, "--yes"]) == 2
    assert set(config.ROOT_DIR.glob("pre-restore-*")) == before
