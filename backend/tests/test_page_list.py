"""A project's page list: light, uncapped, revalidatable."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.adapters import pages as pagestore
from app.main import app


def _project(tmp_path: Path, n: int) -> tuple[int, TestClient]:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    for i in range(n):
        # Distinct pixels: identical files would be one page (content dedup).
        Image.new("RGB", (60, 80), (255, i % 256, i // 256)).save(archive / f"p{i:03d}.png")
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "L", "archive_dir": str(archive)}).json()["id"]
    client.post(f"/api/projects/{pid}/scan")
    return pid, client


def test_list_leaves_out_server_paths_and_keeps_the_counts(tmp_path: Path):
    pid, client = _project(tmp_path / "slim", 3)
    with client:
        body = client.get(f"/api/projects/{pid}/pages").json()
    assert body["total"] == 3
    assert [p["rel_path"] for p in body["items"]] == ["p000.png", "p001.png", "p002.png"]
    item = body["items"][0]
    assert "abs_path" not in item and "thumb_path" not in item and "meta_json" not in item
    for field in ("id", "project_id", "width", "height", "status", "blocks", "drafts", "work", "collection_id"):
        assert field in item


def test_list_has_no_hidden_cap(tmp_path: Path, monkeypatch):
    """A project larger than any fixed cap is listed in full."""
    pid, client = _project(tmp_path / "cap", 4)
    seen = {}
    real = pagestore.list_for_project

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(pagestore, "list_for_project", spy)
    with client:
        assert len(client.get(f"/api/projects/{pid}/pages").json()["items"]) == 4
        assert seen["limit"] is None
        assert len(client.get(f"/api/projects/{pid}/pages?limit=2").json()["items"]) == 2


def test_after_id_returns_only_newer_pages(tmp_path: Path):
    pid, client = _project(tmp_path / "after", 4)
    with client:
        items = client.get(f"/api/projects/{pid}/pages").json()["items"]
        ids = sorted(p["id"] for p in items)
        out = client.get(f"/api/projects/{pid}/pages?after_id={ids[1]}").json()
    assert sorted(p["id"] for p in out["items"]) == ids[2:]
    assert out["total"] == 4


def test_unchanged_list_revalidates_with_304(tmp_path: Path):
    pid, client = _project(tmp_path / "etag", 2)
    with client:
        first = client.get(f"/api/projects/{pid}/pages")
        tag = first.headers["etag"]
        assert client.get(f"/api/projects/{pid}/pages", headers={"If-None-Match": tag}).status_code == 304
        # As a compressing proxy or a CDN sends it back.
        compressed = tag[:-1] + '-zstd"'
        assert client.get(f"/api/projects/{pid}/pages", headers={"If-None-Match": f"W/{compressed}"}).status_code == 304
        # A page changes: no 304.
        page = first.json()["items"][0]["id"]
        client.patch(f"/api/pages/{page}", json={"page_no": "7"})
        again = client.get(f"/api/projects/{pid}/pages", headers={"If-None-Match": tag})
    assert again.status_code == 200
    assert again.json()["items"][0]["page_no"] == "7"
