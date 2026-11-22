"""Uploading a folder again is safe: files already in the project are skipped.

A page remembers the SHA-256 and the byte size of its source file. An upload, a zip
import or an archive scan that brings a file whose content is already a page of the
project skips it and names it in the report (`already_present`). A browser can ask
which files of a folder are not in the project yet before sending anything.
"""
from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import content_identity


@pytest.fixture()
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def _png(color: str, size=(40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _project(client, name: str) -> int:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def _upload(client, pid: int, files: list[tuple[str, bytes]], **params):
    r = client.post(
        f"/api/projects/{pid}/import-upload",
        params=params,
        files=[("files", (n, b, "image/png")) for n, b in files],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _pages(client, pid: int) -> list[dict]:
    return client.get(f"/api/projects/{pid}/pages").json()["items"]


def test_migration_adds_content_hash_and_size_columns():
    init_db()
    with connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
        indexes = {r["name"] for r in conn.execute("PRAGMA index_list(pages)")}
    assert {"content_sha256", "file_size"} <= cols
    assert "idx_pages_content_sha256" in indexes


def test_reupload_of_the_same_files_skips_them_and_names_them(client):
    pid = _project(client, "Dedup reupload")
    red, blue = _png("red"), _png("blue")
    first = _upload(client, pid, [("LSI_5294_001.png", red), ("LSI_5294_002.png", blue)])
    assert first["registered"] == 2 and first["already_present"] == []

    green = _png("green")
    again = _upload(client, pid, [("LSI_5294_001.png", red), ("LSI_5294_002.png", blue), ("LSI_5294_003.png", green)])
    assert again["registered"] == 1
    assert again["duplicates"] == 2
    assert sorted(again["already_present"]) == ["LSI_5294_001.png", "LSI_5294_002.png"]
    assert len(_pages(client, pid)) == 3


def test_a_skipped_upload_leaves_no_copy_on_disk(client):
    pid = _project(client, "Dedup disk")
    red = _png("red")
    _upload(client, pid, [("a.png", red)])
    uploads = config.DATA_DIR / str(pid) / "uploads"
    before = {p for p in uploads.rglob("*") if p.is_file()}
    _upload(client, pid, [("a.png", red)])
    assert {p for p in uploads.rglob("*") if p.is_file()} == before


def test_identical_content_under_another_name_is_skipped(client):
    pid = _project(client, "Dedup renamed")
    red = _png("red")
    _upload(client, pid, [("first.png", red)])
    again = _upload(client, pid, [("renamed.png", red)])
    assert again["registered"] == 0
    assert again["already_present"] == ["renamed.png"]


def test_identical_files_in_one_request_register_once(client):
    pid = _project(client, "Dedup same batch")
    red = _png("red")
    rep = _upload(client, pid, [("x.png", red), ("y.png", red)])
    assert rep["registered"] == 1
    assert rep["already_present"] == ["y.png"]


def test_same_name_different_content_is_a_new_page(client):
    pid = _project(client, "Dedup same name")
    _upload(client, pid, [("p.png", _png("red"))])
    rep = _upload(client, pid, [("p.png", _png("blue"))])
    assert rep["registered"] == 1
    assert rep["already_present"] == []


def test_dedup_is_per_project(client):
    red = _png("red")
    a, b = _project(client, "Dedup A"), _project(client, "Dedup B")
    _upload(client, a, [("p.png", red)])
    assert _upload(client, b, [("p.png", red)])["registered"] == 1


def test_uploaded_page_keeps_its_own_name_and_stores_hash_and_size(client):
    pid = _project(client, "Dedup names")
    red = _png("red")
    _upload(client, pid, [("LSI_6186_001.png", red)])
    page = _pages(client, pid)[0]
    # No upload counter in front of the name: the list sorts by the real file name.
    assert page["rel_path"] == "LSI_6186_001.png"
    with connect() as conn:
        row = conn.execute("SELECT content_sha256, file_size FROM pages WHERE id=?", (page["id"],)).fetchone()
    assert row["content_sha256"] == hashlib.sha256(red).hexdigest()
    assert row["file_size"] == len(red)


def test_two_files_with_one_name_in_one_request_both_land(client):
    pid = _project(client, "Dedup clash")
    rep = _upload(client, pid, [("p.png", _png("red")), ("p.png", _png("blue"))])
    assert rep["registered"] == 2
    names = {p["rel_path"] for p in _pages(client, pid)}
    assert names == {"p.png", "000001_p.png"}


def test_pages_registered_before_the_hash_existed_are_still_found(client):
    """Old pages have no hash until the backfill reaches them: name + size finds them."""
    pid = _project(client, "Dedup legacy")
    red = _png("red")
    _upload(client, pid, [("old.png", red)])
    with connect() as conn:
        conn.execute("UPDATE pages SET content_sha256=NULL, file_size=NULL, rel_path='000000_old.png' WHERE project_id=?", (pid,))
        conn.commit()
    rep = _upload(client, pid, [("old.png", red)])
    assert rep["registered"] == 0 and rep["already_present"] == ["old.png"]
    with connect() as conn:
        row = conn.execute("SELECT content_sha256 FROM pages WHERE project_id=?", (pid,)).fetchone()
    assert row["content_sha256"] == hashlib.sha256(red).hexdigest()


def test_backfill_hashes_pages_without_one(client):
    pid = _project(client, "Dedup backfill")
    red = _png("red")
    _upload(client, pid, [("b.png", red)])
    with connect() as conn:
        conn.execute("UPDATE pages SET content_sha256=NULL, file_size=NULL WHERE project_id=?", (pid,))
        conn.commit()
    assert content_identity.backfill() >= 1
    with connect() as conn:
        row = conn.execute("SELECT content_sha256, file_size FROM pages WHERE project_id=?", (pid,)).fetchone()
    assert row["content_sha256"] == hashlib.sha256(red).hexdigest()
    assert row["file_size"] == len(red)
    assert content_identity.backfill() == 0


def test_check_lists_the_files_of_a_folder_not_in_the_project(client):
    pid = _project(client, "Dedup check")
    red, blue = _png("red"), _png("blue")
    _upload(client, pid, [("LSI_1_001.png", red), ("LSI_1_002.png", blue)])
    r = client.post(f"/api/projects/{pid}/import-upload/check", json={"files": [
        {"name": "LSI_1_001.png", "size": len(red)},
        {"name": "LSI_1_002.png", "size": len(blue) + 1},  # same name, other content
        {"name": "LSI_1_003.png", "size": 10},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["present"] == ["LSI_1_001.png"]
    assert body["missing"] == ["LSI_1_002.png", "LSI_1_003.png"]
    assert body["present_indexes"] == [0]


def test_check_finds_pages_uploaded_with_the_old_counter_prefix(client):
    pid = _project(client, "Dedup check legacy")
    red = _png("red")
    _upload(client, pid, [("LSI_1_001.png", red)])
    with connect() as conn:
        conn.execute("UPDATE pages SET rel_path='000004_LSI_1_001.png', file_size=NULL WHERE project_id=?", (pid,))
        conn.commit()
    body = client.post(f"/api/projects/{pid}/import-upload/check", json={"files": [
        {"name": "LSI_1_001.png", "size": len(red)},
    ]}).json()
    assert body["present"] == ["LSI_1_001.png"]


def test_archive_scan_skips_a_copy_of_a_page(client, tmp_path):
    archive = tmp_path / "archive"
    (archive / "a").mkdir(parents=True)
    (archive / "b").mkdir()
    red = _png("red")
    (archive / "a" / "p.png").write_bytes(red)
    (archive / "b" / "p.png").write_bytes(red)
    (archive / "b" / "q.png").write_bytes(_png("blue"))
    pid = client.post("/api/projects", json={"name": "Dedup scan", "archive_dir": str(archive)}).json()["id"]
    rep = client.post(f"/api/projects/{pid}/scan").json()
    assert rep["registered"] == 2
    assert rep["already_present"] == ["p.png"]
    # A rescan finds every file by path: nothing new, nothing deleted from the archive.
    again = client.post(f"/api/projects/{pid}/scan").json()
    assert again["registered"] == 0
    assert (archive / "b" / "p.png").exists()
