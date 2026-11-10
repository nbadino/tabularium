"""Single-file Markdown export, after Unlimited-OCR-2's `process_unlimitedOCR2.py`."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.domain.markdown_export import document_markdown, merge_table_fragments
from app.main import app
from app.services.model_adapters import get_adapter


def _block(label, content, native=None, points=((0, 0), (100, 100)), **extra):
    value = {"label": label, "content": content, "points": [list(p) for p in points], **extra}
    if native is not None:
        value["native"] = native
    return value


def _native(kind, chain=None, level=None, merge_prev=False):
    return {"kind": kind, "chain": chain, "level": level, "merge_prev": merge_prev}


HEADER = "<tr><td>Name</td><td>Tons</td></tr>"


def test_parser_keeps_the_native_facts_the_converter_needs():
    items = get_adapter("unlimited-ocr-2").parse_layout(
        "<PAGE><BLOCK>title<LEVEL2>[10,10,500,40]<FORK>Index"
        "<BLOCK>cross_table[10,50,900,900]<FORK><table><tr><td>a</td></tr></table>"
        "<BLOCK>text[10,910,400,950]<FORK>left<MERGE>"
        "<BLOCK>text[410,910,900,950]<FORK>right"
    )
    assert [item["native"] for item in items] == [
        _native("title", level=2),
        _native("table", chain="cross"),
        _native("text"),
        _native("text", merge_prev=True),
    ]


def test_cross_page_table_becomes_one_table_without_the_repeated_header():
    pages = [
        {"width": 1000, "height": 1000, "blocks": [
            _block("Title", "Index to Steamers", _native("title", level=1)),
            _block("Table", f"<table>{HEADER}<tr><td>Agawam</td><td>3659</td></tr></table>",
                   _native("table", chain="cross")),
        ]},
        {"width": 1000, "height": 1000, "blocks": [
            _block("Table", f"<table>{HEADER}<tr><td>Agga</td><td>1121</td></tr></table>",
                   _native("table", chain="append")),
        ]},
    ]
    assert document_markdown(pages) == (
        "# Index to Steamers\n\n"
        f"<table>{HEADER}<tr><td>Agawam</td><td>3659</td></tr><tr><td>Agga</td><td>1121</td></tr></table>"
    )


def test_text_chains_and_merge_columns_are_concatenated_in_reading_order():
    """Joined with no separator, as the vendor does: the model cuts where the line breaks."""
    pages = [
        {"width": 1000, "height": 1000, "blocks": [
            _block("Text", "The ship sailed from Hu", _native("text", chain="cross")),
            _block("Text", "Left col", _native("text")),
            _block("Text", "umn continues.", _native("text", merge_prev=True)),
        ]},
        {"width": 1000, "height": 1000, "blocks": [
            _block("Text", "ll on 17 December.", _native("text", chain="append")),
        ]},
    ]
    assert document_markdown(pages) == (
        "The ship sailed from Hull on 17 December.\n\nLeft column continues."
    )


def test_a_table_chain_does_not_jump_over_another_table():
    table = "<table><tr><td>{}</td></tr></table>"
    pages = [
        {"blocks": [_block("Table", table.format("a"), _native("table", chain="cross"))]},
        {"blocks": [_block("Table", table.format("b"), _native("table"))]},
        {"blocks": [_block("Table", table.format("c"), _native("table", chain="append"))]},
    ]
    # Page 2's table opens its own chain, so page 3 continues that one.
    assert document_markdown(pages) == (
        "<table><tr><td>a</td></tr></table>\n\n"
        "<table><tr><td>b</td></tr><tr><td>c</td></tr></table>"
    )


def test_images_titles_and_blocks_without_native_facts():
    pages = [
        {"blocks": []},  # a blank page still counts in the numbering
        {"width": 2000, "height": 1000, "blocks": [
            _block("Title", "First", _native("title", level=1)),
            _block("Picture", "", _native("figure"), points=((200, 100), (1000, 500))),
            _block("Section-header", "Drawn by hand"),
            _block("Title", "Second", _native("title", level=1)),
            _block("Text", "[unreadable]", _native("text")),
        ]},
    ]
    assert document_markdown(pages) == (
        "# First\n\n![](page2_100_100_500_500.jpg)\n\n### Drawn by hand\n\n## Second"
    )


def test_a_corrected_grid_replaces_the_model_table():
    grid = {"rows": 2, "cols": 2, "header_rows": 1, "cells": [
        {"r": 0, "c": 0, "rowspan": 1, "colspan": 2, "text": "Ship & owner"},
        {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "Agawam"},
        {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "3659"},
    ]}
    pages = [{"blocks": [_block("Table", "<table><tr><td>Agawarn</td></tr></table>",
                                _native("table"), grid=grid)]}]
    assert document_markdown(pages) == (
        '<table><tr><th colspan="2">Ship &amp; owner</th></tr>'
        "<tr><td>Agawam</td><td>3659</td></tr></table>"
    )


def test_merge_table_fragments_keeps_rows_that_only_look_like_the_header():
    first = f"<table>{HEADER}<tr><td>x</td><td>1</td></tr></table>"
    second = f"<table><tr><td>y</td><td>2</td></tr>{HEADER}</table>"
    assert merge_table_fragments([first, second]) == (
        f"<table>{HEADER}<tr><td>x</td><td>1</td></tr><tr><td>y</td><td>2</td></tr>{HEADER}</table>"
    )


def test_prefill_saves_the_native_facts_with_the_block(tmp_path: Path, monkeypatch):
    from app.api import prelabel as prelabelmod
    from app.services import prefill as prefillsvc

    native = _native("table", chain="append")

    class FakeModel:
        model = "fake-uocr2"

        def ping(self):
            return True

        def end2end(self, image):
            return [{"bbox": [10, 20, 900, 980], "label": "Text", "content": "Agawam", "native": native}]

    monkeypatch.setattr(prelabelmod.inference, "VllmClient", FakeModel)
    monkeypatch.setattr(prefillsvc, "_looks_like_table", lambda *_args: False)
    archive = tmp_path / "archive"
    archive.mkdir()
    Image.new("RGB", (900, 1300), "white").save(archive / "p.png")
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "P", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page_id = client.get(f"/api/projects/{pid}/pages").json()["items"][0]["id"]
        events = list(prefillsvc.model_prelabel_events(
            pid, [page_id], prefillsvc.PrelabelOptions(mode="replace_all", model_mode="end2end"),
            FakeModel(), "en",
        ))
    blocks = [event["block"] for event in events if event["type"] == "block"]
    assert [block["native"] for block in blocks] == [native]
