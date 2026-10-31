from __future__ import annotations

from app import db
from app.services import backup


def test_online_backup_is_openable_and_verified(tmp_path):
    db.init_db()
    with db.connect() as conn:
        conn.execute("INSERT INTO meta(key,value) VALUES('backup_test','ok')")
    result = backup.create_backup(reason="test")
    assert result["ok"] is True
    assert result["size"] > 0
    listed = backup.list_backups()
    assert any(item["name"] == result["name"] for item in listed)


def test_restoring_the_oldest_backup_does_not_prune_it(monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.main import app

    db.init_db()
    monkeypatch.setattr(config, "BACKUP_RETENTION", 2)
    for old in backup.backup_dir().glob("tabularium-*.db"):
        old.unlink()
    oldest = backup.create_backup(reason="a")["name"]
    backup.create_backup(reason="b")
    with TestClient(app) as client:
        r = client.post(f"/api/system/backup/{oldest}/restore", params={"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["restored"] == oldest
