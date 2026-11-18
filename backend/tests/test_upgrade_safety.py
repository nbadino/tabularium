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


def test_an_empty_database_file_is_not_backed_up_as_a_legacy_one():
    # Something may create the file before init_db runs (a first connection, an
    # interrupted first start): with no tables there is nothing to protect.
    _reset_db()
    config.DB_PATH.touch()
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


def test_cli_backup_keeps_one_copy_of_a_vault_key_inside_the_data_root(tmp_path, monkeypatch):
    _reset_db()
    db.init_db()
    env_file = config.ROOT_DIR / ".env"
    env_file.write_text("TABULARIUM_VAULT_KEY=k\n")
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    out = tmp_path / "b.tar.gz"
    try:
        assert cli.main(["backup", "--out", str(out)]) == 0
    finally:
        env_file.unlink()
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert names.count(".env") == 1
    assert "data/.env" not in names


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


def test_fine_tuned_model_paths_follow_a_moved_data_root():
    _reset_db()
    db.init_db()
    old = "/old/place/Tabularium"
    root = str(config.ROOT_DIR)
    with db.connect() as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='data_root'", (old,))
        conn.execute(
            "INSERT INTO custom_models(id, display_name, hf_repo, served_model_name, source_path) VALUES('custom-moved', 'm', 'r', 's', ?)",
            (f"{old}/projects/4/runs/run_1/checkpoints/checkpoint-315-merged",),
        )
    db.init_db()
    with db.connect() as conn:
        path = conn.execute("SELECT source_path FROM custom_models WHERE id='custom-moved'").fetchone()[0]
        conn.execute("DELETE FROM custom_models WHERE id='custom-moved'")
    assert path == f"{root}/projects/4/runs/run_1/checkpoints/checkpoint-315-merged"


def _make_run(project: str, run_id: str) -> None:
    run = config.DATA_DIR / project / "runs" / run_id
    (run / "checkpoints" / "checkpoint-1-merged").mkdir(parents=True, exist_ok=True)
    (run / "checkpoints" / "checkpoint-1-merged" / "model.safetensors").write_bytes(b"w")
    (run / "metrics.jsonl").write_text("{}\n")


def test_cli_backup_keeps_only_the_checkpoints_of_the_chosen_runs(tmp_path):
    _reset_db()
    db.init_db()
    _make_run("7", "run_keep")
    _make_run("7", "run_drop")
    out = tmp_path / "b.tar.gz"
    assert cli.main(["backup", "--out", str(out), "--runs", "run_keep"]) == 0
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert "data/projects/7/runs/run_keep/checkpoints/checkpoint-1-merged/model.safetensors" in names
    assert "data/projects/7/runs/run_drop/metrics.jsonl" in names
    assert not any(n.startswith("data/projects/7/runs/run_drop/checkpoints") for n in names)


def test_cli_backup_refuses_an_unknown_run(tmp_path):
    _reset_db()
    db.init_db()
    out = tmp_path / "b.tar.gz"
    assert cli.main(["backup", "--out", str(out), "--runs", "run_typo"]) == 2
    assert not out.exists()


def test_cli_backup_warns_about_pages_outside_the_data_root(tmp_path, capsys):
    _reset_db()
    db.init_db()
    with db.connect() as conn:
        pid = conn.execute("INSERT INTO projects(name, root_dir) VALUES('ext', '/scans')").lastrowid
        conn.execute(
            "INSERT INTO pages(project_id, rel_path, abs_path) VALUES(?, 'x.png', '/scans/x.png')", (pid,)
        )
    assert cli.main(["backup", "--out", str(tmp_path / "b.tar.gz")]) == 0
    assert "/scans/x.png" in capsys.readouterr().err


def _use_root(monkeypatch, root):
    monkeypatch.setattr(config, "ROOT_DIR", root)
    monkeypatch.setattr(config, "DATA_DIR", root / "projects")
    monkeypatch.setattr(config, "DB_PATH", root / "tabularium.db")


def _server_archive(tmp_path, monkeypatch) -> "Path":
    """An archive written on a 'server' with its own data root and vault key."""
    server = tmp_path / "server"
    _use_root(monkeypatch, server)
    db.init_db()
    _make_run("4", "run_1")
    with db.connect() as conn:
        conn.execute("INSERT INTO meta(key, value) VALUES('origin', 'server')")
        conn.execute(
            "INSERT INTO custom_models(id, display_name, hf_repo, served_model_name, source_path) VALUES('custom-run-1', 'm', 'r', 's', ?)",
            (str(server / "projects/4/runs/run_1/checkpoints/checkpoint-1-merged"),),
        )
    env = tmp_path / "server.env"
    env.write_text("TABULARIUM_VAULT_KEY=server-key\n")
    monkeypatch.setattr(config, "ENV_FILE", env)
    out = tmp_path / "server.tar.gz"
    assert cli.main(["backup", "--out", str(out)]) == 0
    return out


def _local_root(tmp_path, monkeypatch):
    local = tmp_path / "local"
    _use_root(monkeypatch, local)
    db.init_db()
    (local / "projects" / "1").mkdir(parents=True, exist_ok=True)
    (local / "projects" / "1" / "mine.png").write_bytes(b"local")
    (local / "mlx-runtime").mkdir()
    (local / "mlx-runtime" / "python").write_bytes(b"rt")
    env = tmp_path / "local.env"
    env.write_text("OTHER=1\nTABULARIUM_VAULT_KEY=local-key\n")
    monkeypatch.setattr(config, "ENV_FILE", env)
    return local, env


def test_cli_restore_replace_swaps_the_data_folder_and_keeps_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda port: False)
    archive = _server_archive(tmp_path, monkeypatch)
    local, env = _local_root(tmp_path, monkeypatch)
    assert cli.main(["restore", str(archive), "--replace"]) == 2  # no --yes: nothing happens
    assert (local / "projects" / "1" / "mine.png").is_file()
    assert cli.main(["restore", str(archive), "--replace", "--yes"]) == 0
    with db.connect() as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='origin'").fetchone()[0] == "server"
        assert conn.execute("SELECT value FROM meta WHERE key='data_root'").fetchone()[0] == str(local)
        source = conn.execute("SELECT source_path FROM custom_models WHERE id='custom-run-1'").fetchone()[0]
    assert source == str(local / "projects/4/runs/run_1/checkpoints/checkpoint-1-merged")
    assert (local / "projects/4/runs/run_1/checkpoints/checkpoint-1-merged/model.safetensors").is_file()
    assert (local / "mlx-runtime" / "python").is_file(), "this computer's runtimes are carried over"
    (aside,) = tmp_path.glob("local.pre-restore-*")
    assert (aside / "projects" / "1" / "mine.png").read_bytes() == b"local"
    assert env.read_text().splitlines() == ["OTHER=1", "TABULARIUM_VAULT_KEY=server-key"]
    assert "local-key" in (aside / "env.pre-restore").read_text()
    assert not list(tmp_path.glob(".local.restore-*")), "the staging folder is cleaned up"


def test_cli_restore_needs_a_mode_and_refuses_while_the_app_runs(tmp_path, monkeypatch):
    archive = _server_archive(tmp_path, monkeypatch)
    local, _ = _local_root(tmp_path, monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["restore", str(archive), "--yes"])
    monkeypatch.setattr(cli, "_app_running", lambda port: True)
    assert cli.main(["restore", str(archive), "--replace", "--yes"]) == 2
    assert not list(tmp_path.glob("local.pre-restore-*"))


def test_cli_restore_refuses_an_archive_from_a_newer_release(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda port: False)
    server = tmp_path / "server"
    _use_root(monkeypatch, server)
    db.init_db()
    _set_version(str(int(db.SCHEMA_VERSION) + 1))
    out = tmp_path / "new.tar.gz"
    assert cli.main(["backup", "--out", str(out)]) == 0
    local, _ = _local_root(tmp_path, monkeypatch)
    assert cli.main(["restore", str(out), "--replace", "--yes"]) == 2
    assert (local / "projects" / "1" / "mine.png").is_file()
    assert not list(tmp_path.glob(".local.restore-*"))


def test_cli_restore_rejects_entries_outside_a_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda port: False)
    local, _ = _local_root(tmp_path, monkeypatch)
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "p.txt"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../escape.txt")
    assert cli.main(["restore", str(evil), "--replace", "--yes"]) == 2
    assert not (tmp_path.parent / "escape.txt").exists()
    assert (local / "projects" / "1" / "mine.png").is_file()
