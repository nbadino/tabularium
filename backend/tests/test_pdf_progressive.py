"""PDF pages appear while the import goes on, and a half-imported PDF is completed."""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

from app.db import connect
from app.main import app
from app.services import scan


def _project(archive) -> int:
    with TestClient(app) as client:
        return client.post("/api/projects", json={"name": "P", "archive_dir": str(archive)}).json()["id"]


def _pages_in_db(pid: int) -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM pages WHERE project_id=?", (pid,)).fetchone()[0]


def _register(pid, archive, report, **kw):
    with connect() as conn:
        scan.register_candidate(conn, pid, scan.Candidate(archive / "big.pdf", "pdf"), archive, report, **kw)
        conn.commit()


def test_pdf_pages_are_committed_in_blocks_with_page_progress(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "big.pdf").write_bytes(b"%PDF-1.4 fake")

    def pages(_path, skip=frozenset()):
        for _ in range(5):
            yield Image.new("RGB", (30, 40), "white"), (30, 40)

    monkeypatch.setattr(scan, "PDF_COMMIT_PAGES", 2)
    monkeypatch.setattr(scan, "pdf_page_count", lambda _path: 5)
    monkeypatch.setattr(scan, "iter_pdf_pages", pages)
    pid = _project(archive)
    seen: list[tuple[int, int, int]] = []
    report = scan.ScanReport()
    # Another connection (the page list) sees the committed blocks mid-import.
    _register(pid, archive, report, on_page=lambda done, total: seen.append((done, total, _pages_in_db(pid))))
    assert [(d, t) for d, t, _ in seen] == [(1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]
    assert [visible for *_, visible in seen] == [0, 2, 2, 4, 4]
    assert report.registered == 5 and _pages_in_db(pid) == 5


def test_a_failure_keeps_committed_blocks_and_a_rescan_completes_the_pdf(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "big.pdf").write_bytes(b"%PDF-1.4 fake")
    renders: list[int] = []
    fail_at = [3]

    def pages(_path, skip=frozenset()):
        for index in range(5):
            if index in skip:
                yield None
                continue
            if index == fail_at[0]:
                raise RuntimeError(f"page {index + 1} cannot be rendered")
            renders.append(index)
            yield Image.new("RGB", (30, 40), "white"), (30, 40)

    monkeypatch.setattr(scan, "PDF_COMMIT_PAGES", 2)
    monkeypatch.setattr(scan, "pdf_page_count", lambda _path: 5)
    monkeypatch.setattr(scan, "iter_pdf_pages", pages)
    pid = _project(archive)
    first = scan.ScanReport()
    _register(pid, archive, first)
    # Pages 1-2 were committed; page 3 was in the block that failed.
    assert first.registered == 2 and _pages_in_db(pid) == 2
    assert any("big.pdf" in e and "page 4" in e for e in first.errors)

    fail_at[0] = -1
    renders.clear()
    again = scan.ScanReport()
    _register(pid, archive, again)
    assert renders == [2, 3, 4], "only the missing pages are rendered"
    assert (again.registered, again.duplicates) == (3, 2)
    assert _pages_in_db(pid) == 5
