"""One unreadable PDF page must not cost the rest of the scan."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import scan


def test_a_pdf_that_fails_midway_is_reported_and_the_rest_is_kept(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    Image.new("RGB", (400, 600), "white").save(archive / "a.png")
    (archive / "b.pdf").write_bytes(b"%PDF-1.4 fake")

    def broken_pages(_path, skip=frozenset()):
        yield Image.new("RGB", (400, 600), "white"), (400, 600)
        raise RuntimeError("page 2 cannot be rendered")

    monkeypatch.setattr(scan, "pdf_page_count", lambda _path: 2)
    monkeypatch.setattr(scan, "iter_pdf_pages", broken_pages)
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "S", "archive_dir": str(archive)}).json()["id"]
        report = scan.run_scan(pid, archive)
        pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
    assert [p["rel_path"] for p in pages] == ["a.png"], "half a PDF must not stay registered"
    assert report.registered == 1
    assert any("b.pdf" in e and "page 2" in e for e in report.errors)
