"""ZIP import (docs/COLLECTIONS.md): chunked upload, safe extraction, layout.

The archive is inspected before anything is extracted. Unsafe entries (paths out
of the folder, links, encrypted or nested archives, non-images) are skipped and
reported; an archive over a global limit is refused whole.
"""
from __future__ import annotations

import io
import stat
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.db import init_db
from app.main import app
from app.services import zip_import


def _png(color: str = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (30, 40), color).save(buf, format="PNG")
    return buf.getvalue()


def _pdf(pages: int = 2) -> bytes:
    buf = io.BytesIO()
    imgs = [Image.new("RGB", (60, 80), "white") for _ in range(pages)]
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _zip(entries: dict[str, bytes], *, links: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for name, target in (links or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    return buf.getvalue()


@pytest.fixture()
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def _project(client) -> int:
    return client.post("/api/projects", json={"name": f"zip {time.time_ns()}"}).json()["id"]


def _upload(client, pid: int, data: bytes, name: str = "archive.zip", chunk: int | None = None) -> str:
    r = client.post(f"/api/projects/{pid}/zip-uploads", json={"filename": name, "size": len(data)})
    assert r.status_code == 201, r.text
    uid = r.json()["upload_id"]
    step = chunk or r.json()["chunk_size"]
    for offset in range(0, len(data), step):
        r = client.put(
            f"/api/projects/{pid}/zip-uploads/{uid}",
            params={"offset": offset},
            content=data[offset:offset + step],
            headers={"content-type": "application/octet-stream"},
        )
        assert r.status_code == 200, r.text
    return uid


def _import(client, pid: int, uid: str, **body) -> dict:
    r = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/import", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/api/projects/{pid}/zip-jobs/{job_id}").json()
        if job["status"] != "running":
            assert job["status"] == "done", job
            return job["report"]
        time.sleep(0.05)
    raise AssertionError("import did not finish")


# --- inspection: what the archive contains, before extracting ----------------
def test_loose_files_import_directly_without_a_question(client):
    pid = _project(client)
    uid = _upload(client, pid, _zip({"a.png": _png(), "b.jpg": _png()}))
    plan = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").json()
    assert plan["needs_choice"] is False
    assert plan["files"] == 2 and plan["folders"] == []


def test_single_wrapping_folder_is_unwrapped_then_folders_ask(client):
    pid = _project(client)
    data = _zip({
        "Lloyds/Registri/1890/p1.png": _png(),
        "Lloyds/Registri/p2.png": _png(),
        "Lloyds/Lettere/l1.png": _png(),
        "Lloyds/indice.png": _png(),
    })
    uid = _upload(client, pid, data, chunk=1000)  # several chunks
    plan = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").json()
    assert plan["root"] == "Lloyds"
    assert plan["needs_choice"] is True
    assert plan["folders"] == [{"name": "Lettere", "files": 1}, {"name": "Registri", "files": 2}]
    assert plan["loose_files"] == 1


def test_one_folder_only_needs_no_choice(client):
    pid = _project(client)
    uid = _upload(client, pid, _zip({"Registri/a.png": _png(), "Registri/sub/b.png": _png()}))
    plan = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").json()
    # One folder with a subfolder: unwrapped to Registri, whose only folder is `sub`
    # next to a loose file: still no choice.
    assert plan["needs_choice"] is False


# --- safety --------------------------------------------------------------------
def test_unsafe_entries_are_skipped_and_reported(client):
    pid = _project(client)
    data = _zip(
        {
            "../evil.png": _png(),
            "/abs.png": _png(),
            "C:/win.png": _png(),
            "ok/../../up.png": _png(),
            "notes.txt": b"hello",
            "inner.zip": b"PK",
            "__MACOSX/._a.png": b"x",
            ".DS_Store": b"x",
            "good.png": _png(),
        },
        links={"link.png": "/etc/passwd"},
    )
    uid = _upload(client, pid, data)
    plan = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").json()
    reasons = {item["name"]: item["reason"] for item in plan["skipped"]}
    assert reasons["../evil.png"] == "unsafe_path"
    assert reasons["/abs.png"] == "unsafe_path"
    assert reasons["C:/win.png"] == "unsafe_path"
    assert reasons["ok/../../up.png"] == "unsafe_path"
    assert reasons["link.png"] == "symlink"
    assert reasons["notes.txt"] == "not_image"
    assert reasons["inner.zip"] == "nested_archive"
    assert plan["ignored"] == 2
    assert plan["files"] == 1

    report = _import(client, pid, uid, layout="flat", collection_id=None)
    assert report["registered"] == 1
    assert report["skipped_total"] == 7
    root = config.DATA_DIR / str(pid)
    assert not any(p.name in {"evil.png", "up.png", "link.png"} for p in root.rglob("*"))
    assert not (config.DATA_DIR.parent / "evil.png").exists()


def test_bomb_ratio_and_size_limits(client, monkeypatch):
    pid = _project(client)
    bomb = b"\0" * (3 * 1024 * 1024)  # compresses far better than 100:1
    uid = _upload(client, pid, _zip({"bomb.png": bomb, "good.png": _png()}))
    plan = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").json()
    assert {i["name"]: i["reason"] for i in plan["skipped"]}["bomb.png"] == "suspicious_ratio"

    monkeypatch.setattr(config, "ZIP_MAX_ENTRIES", 2)
    uid = _upload(client, pid, _zip({"a.png": _png(), "b.png": _png(), "c.png": _png()}))
    r = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect")
    assert r.status_code == 413

    monkeypatch.setattr(config, "ZIP_MAX_ARCHIVE_BYTES", 10)
    r = client.post(f"/api/projects/{pid}/zip-uploads", json={"filename": "x.zip", "size": 11})
    assert r.status_code == 413


def test_lying_header_is_stopped_while_streaming(tmp_path, monkeypatch):
    target = tmp_path / "out.png"
    monkeypatch.setattr(config, "ZIP_MAX_FILE_BYTES", 100)
    with pytest.raises(zip_import.TooLarge):
        zip_import._copy_limited(io.BytesIO(b"x" * 1000), target, declared=10, budget=[10_000])
    assert not target.exists()


def test_chunks_must_arrive_in_order_and_not_overflow(client):
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/zip-uploads", json={"filename": "a.zip", "size": 10})
    uid = r.json()["upload_id"]
    bad = client.put(f"/api/projects/{pid}/zip-uploads/{uid}", params={"offset": 5}, content=b"12345")
    assert bad.status_code == 409
    over = client.put(f"/api/projects/{pid}/zip-uploads/{uid}", params={"offset": 0}, content=b"x" * 11)
    assert over.status_code == 413
    assert client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").status_code == 409
    assert client.put(f"/api/projects/{pid}/zip-uploads/not-an-id", params={"offset": 0}, content=b"x").status_code == 404
    assert client.post(f"/api/projects/{pid}/zip-uploads", json={"filename": "a.rar", "size": 10}).status_code == 400


def test_not_a_zip_is_refused(client):
    pid = _project(client)
    uid = _upload(client, pid, b"this is not a zip file at all")
    assert client.post(f"/api/projects/{pid}/zip-uploads/{uid}/inspect").status_code == 400


# --- import: where the pages go ------------------------------------------------
def test_one_collection_per_folder(client):
    pid = _project(client)
    existing = client.post(f"/api/projects/{pid}/collections", json={"name": "lettere"}).json()["id"]
    data = _zip({
        "Lloyds/Registri/1890/p1.png": _png(),
        "Lloyds/Registri/p2.png": _png(),
        "Lloyds/Lettere/l1.png": _png(),
        "Lloyds/indice.png": _png(),
    })
    uid = _upload(client, pid, data)
    report = _import(client, pid, uid, layout="collections")
    assert report["registered"] == 4
    by_name = {c["name"]: c for c in report["collections"]}
    assert by_name["lettere"]["created"] is False and by_name["lettere"]["id"] == existing
    assert by_name["lettere"]["pages"] == 1
    assert by_name["Registri"]["created"] is True and by_name["Registri"]["pages"] == 2
    assert report["unassigned_pages"] == 1

    pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
    rel = {p["rel_path"]: p["collection_id"] for p in pages}
    assert rel["Registri/1890/p1.png"] == by_name["Registri"]["id"]
    assert rel["indice.png"] is None
    # the archive itself is not kept once imported, and cannot be imported twice
    assert not any(p.name == "archive.zip" for p in (config.DATA_DIR / str(pid)).rglob("*"))
    assert client.post(f"/api/projects/{pid}/zip-uploads/{uid}/import", json={"layout": "flat"}).status_code == 409


def test_flat_into_a_chosen_collection(client):
    pid = _project(client)
    cid = client.post(f"/api/projects/{pid}/collections", json={"name": "Liste"}).json()["id"]
    uid = _upload(client, pid, _zip({"A/a.png": _png(), "B/b.png": _png()}))
    report = _import(client, pid, uid, layout="flat", collection_id=cid)
    assert report["registered"] == 2
    pages = client.get(f"/api/projects/{pid}/pages", params={"collection": cid}).json()["items"]
    assert len(pages) == 2

    other = _project(client)
    foreign = client.post(f"/api/projects/{other}/collections", json={"name": "X"}).json()["id"]
    uid = _upload(client, pid, _zip({"c.png": _png()}))
    r = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/import", json={"layout": "flat", "collection_id": foreign})
    assert r.status_code == 404


def test_stale_uploads_are_removed(client, monkeypatch):
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/zip-uploads", json={"filename": "a.zip", "size": 10})
    uid = r.json()["upload_id"]
    folder = zip_import.upload_dir(pid, uid)
    assert folder.is_dir()
    monkeypatch.setattr(zip_import, "STALE_SECONDS", -1)
    client.post(f"/api/projects/{pid}/zip-uploads", json={"filename": "b.zip", "size": 10})
    assert not folder.exists()


def test_pdfs_follow_the_pdf_handling(client):
    pytest.importorskip("pypdfium2")
    pid = _project(client)
    uid = _upload(client, pid, _zip({"A/doc.pdf": _pdf(2), "B/x.png": _png()}))
    report = _import(client, pid, uid, layout="collections")
    assert report["registered"] == 3
    assert {c["name"]: c["pages"] for c in report["collections"]} == {"A": 2, "B": 1}


def test_flat_into_a_new_named_collection(client):
    pid = _project(client)
    uid = _upload(client, pid, _zip({"LSI1950/a.png": _png(), "LSI1950/b.png": _png()}))
    report = _import(client, pid, uid, layout="flat", collection_name="  LSI1950 ")
    assert report["collections"] == [{"id": report["collections"][0]["id"], "name": "LSI1950", "created": True, "pages": 2}]
    assert report["unassigned_pages"] == 0
    cid = report["collections"][0]["id"]
    assert len(client.get(f"/api/projects/{pid}/pages", params={"collection": cid}).json()["items"]) == 2

    # a second archive with the same name goes into the same collection
    uid = _upload(client, pid, _zip({"c.png": _png()}))
    report = _import(client, pid, uid, layout="flat", collection_name="lsi1950")
    assert report["collections"][0]["id"] == cid and report["collections"][0]["created"] is False
    assert len(client.get(f"/api/projects/{pid}/pages", params={"collection": cid}).json()["items"]) == 3

    uid = _upload(client, pid, _zip({"d.png": _png()}))
    r = client.post(f"/api/projects/{pid}/zip-uploads/{uid}/import", json={"layout": "flat", "collection_id": cid, "collection_name": "X"})
    assert r.status_code == 422
