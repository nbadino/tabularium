"""A rescan must not redo work for files that are already registered."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import scan


def test_rescanning_a_registered_pdf_does_not_render_it_again(tmp_path, monkeypatch):
    """Every rescan rendered every PDF page at 200 DPI only to find it was known."""
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "issue.pdf").write_bytes(b"%PDF-1.4 fake")
    renders: list[int] = []

    def pages(_path):
        for index in range(2):
            renders.append(index)
            yield Image.new("RGB", (300, 400), "white"), (300, 400)

    monkeypatch.setattr(scan, "pdf_page_count", lambda _path: 2)
    monkeypatch.setattr(scan, "iter_pdf_pages", pages)
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "R", "archive_dir": str(archive)}).json()["id"]
    first = scan.run_scan(pid, archive)
    assert (first.registered, renders) == (2, [0, 1])
    again = scan.run_scan(pid, archive)
    assert (again.registered, again.duplicates) == (0, 2)
    assert renders == [0, 1], "a known PDF must not be rendered again"
