"""Markdown and searchable PDF exports of a recognition run."""
from __future__ import annotations

import re
import time
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import run_documents, searchable_pdf


# ── Markdown ────────────────────────────────────────────────────────────────


def test_labels_shape_the_markdown():
    assert run_documents.block_markdown({"label": "Title", "content": "SAILING\nVESSELS."}) == "# SAILING VESSELS."
    assert run_documents.block_markdown({"label": "Section-header", "content": "Arrivals"}) == "## Arrivals"
    assert run_documents.block_markdown({"label": "List-item", "content": "• Pamir, from Port Victoria"}) == "- Pamir, from Port Victoria"
    assert run_documents.block_markdown({"label": "List-item", "content": "1. first"}) == "1. first"
    assert run_documents.block_markdown({"label": "Formula", "content": "E = mc^2"}) == "$$\nE = mc^2\n$$"
    # A custom label is a paragraph, never dropped.
    assert run_documents.block_markdown({"label": "Ship-entry", "content": "Pamir"}) == "Pamir"
    assert run_documents.block_markdown({"label": "Picture", "content": ""}) == ""


def test_markdown_from_a_model_is_kept():
    assert run_documents.block_markdown({"label": "Title", "content": "## Already marked"}) == "## Already marked"
    assert run_documents.block_markdown({"label": "Text", "content": "Some **bold** text"}) == "Some **bold** text"


def test_html_table_becomes_a_pipe_table():
    html = (
        "<table><thead><tr><th>VESSEL</th><th>Flg</th></tr></thead>"
        "<tbody><tr><td>Pamir</td><td>Fi</td></tr>"
        "<tr><td colspan='2'>Port Victoria &amp; Falmouth</td></tr>"
        "<tr><td>A|B</td><td>x</td></tr></tbody></table>"
    )
    assert run_documents.block_markdown({"label": "Table", "content": html}) == (
        "| VESSEL | Flg |\n"
        "| --- | --- |\n"
        "| Pamir | Fi |\n"
        "| Port Victoria & Falmouth |   |\n"
        "| A\\|B | x |"
    )


def test_saved_grid_wins_and_drops_phantom_columns():
    grid = {
        "rows": 2, "cols": 3, "phantom_cols": [1], "header_rows": 1,
        "cells": [
            {"r": 0, "c": 0, "text": "Ship"}, {"r": 0, "c": 1, "text": ""}, {"r": 0, "c": 2, "text": "Port"},
            {"r": 1, "c": 0, "text": "Pamir"}, {"r": 1, "c": 1, "text": ""}, {"r": 1, "c": 2, "text": "Falmouth"},
        ],
    }
    block = {"id": 7, "label": "Table", "content": "<table><tr><td>stale</td></tr></table>"}
    assert run_documents.block_markdown(block, grid) == "| Ship | Port |\n| --- | --- |\n| Pamir | Falmouth |"


def test_run_markdown_marks_each_page_and_keeps_order():
    exported = {
        "scope": "reviewed",
        "run": {"id": 3},
        "pages": [
            {"page_id": 1, "rel_path": "volume.pdf", "pdf_page": 0,
             "blocks": [{"id": 1, "label": "Title", "content": "Index"}, {"id": 2, "label": "Text", "content": "First."}]},
            {"page_id": 2, "rel_path": "volume.pdf", "pdf_page": 1,
             "blocks": [{"id": 3, "label": "Text", "content": "Second."}]},
        ],
    }
    assert run_documents.run_markdown(exported, {}) == (
        "<!-- p. 1 · volume.pdf -->\n\n# Index\n\nFirst.\n\n<!-- p. 2 · volume.pdf -->\n\nSecond.\n"
    )
    assert run_documents.download_stem(exported) == "volume"
    assert run_documents.download_stem({**exported, "scope": "raw"}) == "volume-raw"
    mixed = {**exported, "pages": [{"rel_path": "a.png"}, {"rel_path": "b.png"}]}
    assert run_documents.download_stem(mixed) == "tabularium-run-3"


def test_plain_text_strips_markup():
    assert run_documents.plain_text("## Title") == "Title"
    assert run_documents.plain_text("a <b>bold</b><br>next &amp; last") == "a bold\nnext & last"


# ── Searchable PDF ──────────────────────────────────────────────────────────


def _objects(pdf: bytes) -> dict[int, bytes]:
    """Check the cross-reference table and return every object by number."""
    startxref = int(re.search(rb"startxref\n(\d+)\n%%EOF\n$", pdf).group(1))
    assert pdf[startxref:].startswith(b"xref\n")
    header, *rows = pdf[startxref:].split(b"\ntrailer")[0].decode().split("\n")[1:]
    first, count = map(int, header.split())
    assert first == 0 and len(rows) == count
    objects = {}
    for number, row in enumerate(rows):
        assert len(row) == 19  # 20 bytes with the newline
        if row.endswith("n "):
            offset = int(row[:10])
            assert pdf[offset:].startswith(f"{number} 0 obj\n".encode()), number
            end = pdf.index(b"\nendobj\n", offset)
            objects[number] = pdf[offset:end]
    return objects


def _stream(obj: bytes) -> bytes:
    data = obj[obj.index(b"stream\n") + 7 : obj.rindex(b"\nendstream")]
    length = int(re.search(rb"/Length (\d+)", obj).group(1))
    assert len(data) == length
    return zlib.decompress(data) if b"/FlateDecode" in obj.split(b"stream\n")[0] else data


def _shown_text(pdf: bytes) -> list[str]:
    out = []
    for obj in _objects(pdf).values():
        if b"stream\n" not in obj or b"/Subtype" in obj.split(b"stream\n")[0]:
            continue
        try:
            content = _stream(obj).decode("ascii")
        except (UnicodeDecodeError, zlib.error):
            continue
        out += [bytes.fromhex(h).decode("utf-16-be") for h in re.findall(r"<([0-9A-F]+)> Tj", content)]
    return out


def _page(image: Image.Image, blocks, grids=None, dpi=200):
    return searchable_pdf.PdfPageInput(image=image, dpi=dpi, blocks=blocks, grids=grids or {})


def test_glyphless_font_is_a_valid_truetype_file():
    font = searchable_pdf.glyphless_font()
    assert font[:4] == b"\x00\x01\x00\x00"
    count = int.from_bytes(font[4:6], "big")
    tags = [font[12 + 16 * i : 16 + 16 * i] for i in range(count)]
    assert tags == sorted(tags)
    assert {b"cmap", b"glyf", b"head", b"hhea", b"hmtx", b"loca", b"maxp", b"name", b"post", b"OS/2"} == set(tags)
    # The whole-font checksum, with checkSumAdjustment in place, is the magic number.
    assert searchable_pdf._checksum(font) == 0xB1B0AFBA


def test_pdf_is_well_formed_and_carries_the_text(tmp_path: Path):
    image = Image.new("RGB", (1700, 2200), "white")
    blocks = [
        {"id": 1, "label": "Title", "content": "SAILING VESSELS.", "points": [[200, 180], [900, 260]]},
        {"id": 2, "label": "Text", "content": "Città di Genova — arrivi", "points": [[200, 300], [1500, 360]]},
        {"id": 3, "label": "Text", "content": "no box", "points": []},
    ]
    pdf = b"".join(searchable_pdf.write_pdf([_page(image, blocks), _page(None, [])], title="Prova"))
    assert pdf.startswith(b"%PDF-1.5")
    objects = _objects(pdf)
    pages = [obj for obj in objects.values() if b"/Type /Page " in obj]
    assert len(pages) == 2
    # 1700 px at 200 DPI is 612 pt: the page keeps the size of the original sheet.
    assert b"/MediaBox [0 0 612.000 792.000]" in pages[0]
    assert b"/Count 2" in next(obj for obj in objects.values() if b"/Type /Pages" in obj)
    assert _shown_text(pdf) == ["SAILING VESSELS.", "Città di Genova — arrivi", "no box"]
    content = next(_stream(o).decode() for o in objects.values() if b"stream\n" in o and b"BT 3 Tr" in _stream(o))
    assert "BT 3 Tr /F1 1 Tf" in content  # invisible text over the image
    assert "/Im0 Do" in content


def test_paragraph_is_spread_over_the_lines_of_its_box():
    text = "one two three four five six seven eight nine ten eleven twelve"
    lines = searchable_pdf.text_lines(text, (0, 0, 400, 240))
    assert len(lines) == 3
    assert " ".join(line.text for line in lines) == text
    assert [round(line.y0) for line in lines] == [0, 80, 160]
    # A single recognised line (measured on a real scan) stays one line.
    line = "En San Felices de Buelna o en Corrales de Buelna (Santander). Incluido"
    assert len(searchable_pdf.text_lines(line, (0, 0, 741, 55))) == 1
    # A content that already says where lines break is kept as it is.
    assert [l.text for l in searchable_pdf.text_lines("a\nb", (0, 0, 400, 120))] == ["a", "b"]


def test_table_cells_go_where_the_grid_puts_them():
    grid = {
        "rows": 2, "cols": 2, "vlines": [0, 0.5, 1], "hlines": [0, 0.5, 1],
        "cells": [{"r": 0, "c": 0, "text": "Ship"}, {"r": 0, "c": 1, "text": "Port"},
                  {"r": 1, "c": 0, "text": "Pamir"}, {"r": 1, "c": 1, "text": ""}],
    }
    block = {"id": 9, "label": "Table", "content": "", "points": [[100, 100], [300, 300]]}
    lines = searchable_pdf.block_lines(block, grid, (1000, 1000))
    assert [(l.text, l.x0, l.y0, l.x1, l.y1) for l in lines] == [
        ("Ship ", 100, 100, 200, 200), ("Port ", 200, 100, 300, 200), ("Pamir ", 100, 200, 200, 300),
    ]


def test_jpeg_scan_is_embedded_untouched(tmp_path: Path):
    path = tmp_path / "scan.jpg"
    Image.new("L", (300, 400), 200).save(path, quality=70, dpi=(300, 300))
    with Image.open(path) as image:
        assert searchable_pdf.page_dpi({"source_kind": "image"}, image) == 300
        pdf = b"".join(searchable_pdf.write_pdf([_page(image, [], dpi=300)]))
    assert path.read_bytes() in pdf


def test_text_is_extracted_by_a_real_pdf_engine(tmp_path: Path):
    pdfium = pytest.importorskip("pypdfium2")
    image = Image.new("RGB", (1700, 2200), "white")
    blocks = [
        {"id": 1, "label": "Title", "content": "SAILING VESSELS.", "points": [[200, 180], [900, 260]]},
        {"id": 2, "label": "Table", "content": "<table><tr><td>Pamir</td><td>Fi</td></tr></table>",
         "points": [[200, 600], [1500, 700]]},
    ]
    path = tmp_path / "out.pdf"
    path.write_bytes(b"".join(searchable_pdf.write_pdf([_page(image, blocks)])))
    document = pdfium.PdfDocument(str(path))
    text = document[0].get_textpage().get_text_bounded()
    assert "SAILING VESSELS." in text
    assert "Pamir Fi" in text


# ── HTTP ────────────────────────────────────────────────────────────────────


class FakeOcr:
    available = True
    name = "fake-ocr"

    def detect(self, _image):
        return [{"bbox": [10, 20, 180, 50], "text": "Recognized text", "score": 0.99}]


def test_run_exports_markdown_and_searchable_pdf(tmp_path, monkeypatch):
    from app.services import recognition

    monkeypatch.setattr(recognition.ocrmod, "OcrEngine", FakeOcr)
    archive = tmp_path / "archive"
    archive.mkdir()
    Image.new("RGB", (200, 300), "white").save(archive / "registro.png")
    with TestClient(app) as client:
        project_id = client.post("/api/projects", json={"name": "Docs", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{project_id}/scan")
        page_ids = [p["id"] for p in client.get(f"/api/projects/{project_id}/pages").json()["items"]]
        run_id = client.post(
            f"/api/projects/{project_id}/recognition-runs",
            json={"page_ids": page_ids, "engine": "ocr", "mode": "replace_drafts", "stop_policy": "none"},
        ).json()["id"]
        for _ in range(200):
            state = client.get(f"/api/projects/{project_id}/recognition-runs/{run_id}").json()["state"]
            if state not in {"queued", "running"}:
                break
            time.sleep(0.02)
        base = f"/api/projects/{project_id}/recognition-runs/{run_id}/export"

        markdown = client.get(base, params={"format": "markdown"})
        assert markdown.status_code == 200
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert 'filename="registro.md"' in markdown.headers["content-disposition"]
        assert markdown.text == "<!-- registro.png -->\n\nRecognized text\n"

        pdf = client.get(base, params={"format": "pdf", "scope": "raw"})
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert 'filename="registro-raw.pdf"' in pdf.headers["content-disposition"]
        assert _shown_text(pdf.content) == ["Recognized text"]

        text = client.get(base, params={"format": "text"})
        assert 'filename="registro.txt"' in text.headers["content-disposition"]
        assert client.get(base, params={"format": "docx"}).status_code == 422
