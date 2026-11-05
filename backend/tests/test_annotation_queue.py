"""The Studio asks the queue for the next page: never one whose file is gone."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

from app.db import connect
from app.main import app


def test_the_queue_leaves_out_pages_whose_source_is_missing(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for name in ("a.png", "b.png"):
        Image.new("RGB", (300, 400), "white").save(archive / name)
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "Q", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        pages = {p["rel_path"]: p["id"] for p in client.get(f"/api/projects/{pid}/pages").json()["items"]}
        with connect() as conn:
            conn.execute("UPDATE pages SET status='missing' WHERE id=?", (pages["a.png"],))
        r = client.get(f"/api/projects/{pid}/annotation-queue", params={"limit": 10})
        assert r.status_code == 200, r.text
        ids = [item["page_id"] if "page_id" in item else item["id"] for item in r.json()["items"]]
    assert ids == [pages["b.png"]]
