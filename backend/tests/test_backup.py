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
