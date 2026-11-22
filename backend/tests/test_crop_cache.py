"""Block crops: the page is decoded once, not once per crop.

The Studio asks at once for the crop of every block of the open page (hundreds
at times). Each crop used to read and decode the whole TIFF again, and one page
saturated the server's CPUs for tens of seconds.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.db import connect
from app.main import app
from app.services import pages as pagesvc


def _page(tmp_path: Path, mode: str = "L") -> tuple[dict, Path]:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    img = Image.new(mode, (900, 1200), "white")
    d = ImageDraw.Draw(img)
    for i in range(10):
        d.rectangle((50 + i * 80, 100, 90 + i * 80, 140), fill="black")
    src = archive / "page.tif"
    img.save(src, compression="tiff_lzw")
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "CC", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page_id = client.get(f"/api/projects/{pid}/pages").json()["items"][0]["id"]
    with connect() as conn:
        row = conn.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
    return row, src


def _count_opens(monkeypatch, target: Path) -> list[int]:
    calls = [0]
    real_open = pagesvc.Image.open

    def counting(fp, *a, **kw):
        if Path(str(fp)) == target:
            calls[0] += 1
        return real_open(fp, *a, **kw)

    monkeypatch.setattr(pagesvc.Image, "open", counting)
    return calls


def test_many_crops_decode_the_page_once(tmp_path: Path, monkeypatch):
    page, src = _page(tmp_path / "once")
    calls = _count_opens(monkeypatch, src)
    for i in range(30):
        data = pagesvc.crop_block_jpeg(page, (10 + i, 10, 200 + i, 200))
        assert data[:2] == b"\xff\xd8"
    assert calls[0] == 1


def test_concurrent_crops_share_one_decode(tmp_path: Path, monkeypatch):
    page, src = _page(tmp_path / "conc")
    calls = _count_opens(monkeypatch, src)
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            pagesvc.crop_block_jpeg(page, (i, i, 300, 300))
        except BaseException as e:  # pragma: no cover - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert calls[0] == 1


def test_crop_matches_full_page_conversion(tmp_path: Path):
    page, src = _page(tmp_path / "same")
    bbox = (40, 90, 420, 160)
    expected = Image.open(src).convert("RGB").crop(bbox)
    got = pagesvc.crop_block_image(page, bbox)
    assert got.mode == "RGB"
    assert got.size == expected.size
    assert got.tobytes() == expected.tobytes()


def test_new_source_file_is_not_served_from_cache(tmp_path: Path):
    page, src = _page(tmp_path / "fresh")
    before = pagesvc.crop_block_image(page, (0, 0, 20, 20)).getpixel((5, 5))
    assert before == (255, 255, 255)
    Image.new("L", (900, 1200), "black").save(src, compression="tiff_lzw")
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    after = pagesvc.crop_block_image(page, (0, 0, 20, 20)).getpixel((5, 5))
    assert after == (0, 0, 0)


def test_cache_holds_a_few_pages_only(tmp_path: Path):
    rows = [_page(tmp_path / f"p{i}")[0] for i in range(pagesvc._DECODED_MAX + 2)]
    for row in rows:
        pagesvc.crop_block_image(row, (0, 0, 10, 10))
    assert len(pagesvc._decoded) <= pagesvc._DECODED_MAX
    assert not pagesvc._decoding
