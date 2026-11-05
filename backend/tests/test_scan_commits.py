"""A scan must not hold SQLite's write lock for its whole length: autosave, page
edits and run heartbeats wait 30 s and then fail with "database is locked"."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

from app.db import connect
from app.main import app
from app.services import scan


def test_each_registered_file_is_committed_before_the_next(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for name in ("a.png", "b.png", "c.png"):
        Image.new("RGB", (300, 400), "white").save(archive / name)
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "C", "archive_dir": str(archive)}).json()["id"]
    visible: list[int] = []

    def progress(index, _candidate, _report):
        # Another connection, as another request would be: it sees only committed rows.
        with connect() as other:
            visible.append(other.execute("SELECT COUNT(*) FROM pages WHERE project_id=?", (pid,)).fetchone()[0])

    scan.run_scan(pid, archive, on_progress=progress)
    assert visible == [1, 2, 3]
