"""Whole-document recognition (Unlimited-OCR 2): consecutive pages in one request,
pages saved as their output closes, the next request picking up where it stopped."""
from __future__ import annotations

import threading

import pytest
from PIL import Image

from app.services import document_recognition as doc
from app.services import run_documents
from app.services.inference import PromptTooLong
from app.services.model_adapters import get_adapter


def test_page_stream_closes_a_page_when_the_next_marker_arrives():
    pages = []
    stream = doc.PageStream(lambda i, seg: pages.append((i, seg)))
    for delta in ["<PA", "GE><BLOCK>text[1,1,9,9]<FORK>one", "<PAGE", "><BLOCK>text[1,1,9,9]<FORK>two"]:
        stream.feed(delta)
    assert pages == [(0, "<BLOCK>text[1,1,9,9]<FORK>one")]
    stream.finish(complete=True)
    assert pages[1] == (1, "<BLOCK>text[1,1,9,9]<FORK>two")


def test_a_cut_output_does_not_save_its_unfinished_page():
    pages = []
    stream = doc.PageStream(lambda i, seg: pages.append(i))
    stream.feed("<PAGE>a<PAGE>b<PAGE>half")
    stream.finish(complete=False)
    assert pages == [0, 1]


def test_plan_keeps_thirty_percent_for_the_output():
    pages, max_tokens = doc.plan(131072, 273, 527)
    assert max_tokens == int(131072 * 0.3)
    assert pages == int((131072 - max_tokens - 700) // 273)
    assert doc.plan(131072, 273, 5)[0] == 5


class FakeClient:
    """Answers each request with one `<PAGE>` per page, up to `fits` pages."""

    def __init__(self, fits: int, *, page_tokens: int | None = None):
        self.adapter = get_adapter("unlimited-ocr-2")
        self.fits = fits
        self.page_tokens = page_tokens  # the real prompt cost of a page, if set
        self.requests: list[int] = []

    def served_context_length(self):
        return 131072

    def end2end_document(self, images, *, max_tokens, on_delta, cancel_event):
        self.requests.append(len(images))
        if self.page_tokens is not None:
            prompt = len(images) * self.page_tokens
            if prompt + max_tokens > 131072:  # as the server refuses it
                raise PromptTooLong(prompt, "too long")
        done = min(self.fits, len(images))
        for i in range(done):
            on_delta(f"<PAGE><BLOCK>text[1,1,9,9]<FORK>page{i}")
        if done < len(images):
            on_delta("<PAGE><BLOCK>text[1,1,9,9]<FORK>cut sh")
            return "", "length"
        return "", "stop"


def _run(client, n):
    items = [{"id": i, "page_id": i} for i in range(n)]
    saved, alone = [], []
    doc.recognize(
        items, client=client, load_image=lambda _i: Image.new("RGB", (10, 10)),
        save_page=lambda item, seg: saved.append((item["id"], seg)),
        fallback_page=lambda item: alone.append(item["id"]), cancel=threading.Event(),
    )
    return saved, alone


def test_a_long_book_goes_in_as_few_requests_as_the_output_allows():
    client = FakeClient(fits=40)
    saved, alone = _run(client, 100)
    assert [i for i, _ in saved] == list(range(100))  # every page, in order, once
    assert alone == []
    # The first request offers every page that fits; the next ones are sized on
    # what the output managed (40 -> 44).
    assert client.requests[0] == 100
    assert client.requests[1] == 44
    assert sum(1 for _ in client.requests) <= 4


def test_a_too_long_prompt_is_refitted_from_the_server_count():
    # A page really costs 1500 tokens (gundam tiles), not the 273 first guessed.
    client = FakeClient(fits=1000, page_tokens=1500)
    saved, _ = _run(client, 100)
    assert [i for i, _ in saved] == list(range(100))
    assert client.requests[0] == 100  # refused once...
    assert client.requests[1] * 1500 + int(131072 * 0.3) <= 131072  # ...then it fits
    assert len(client.requests) <= 4


def test_no_progress_falls_back_to_one_page_alone():
    client = FakeClient(fits=0)
    saved, alone = _run(client, 3)
    assert alone == [0, 1, 2]


def test_multi_page_requests_use_the_vendor_window():
    adapter = get_adapter("unlimited-ocr-2")
    assert adapter.document_parsing is True
    assert adapter.document_request_overrides(30)["custom_params"]["window_size"] == 1024
    assert adapter.document_request_overrides(1)["custom_params"]["window_size"] == adapter.request_overrides("end2end")["custom_params"]["window_size"]


def test_markdown_joins_a_paragraph_and_a_table_split_by_a_page_break():
    table_a = "<table><tr><td>Ship</td><td>Port</td></tr><tr><td>Pamir</td><td>Genoa</td></tr></table>"
    table_b = "<table><tr><td>Ship</td><td>Port</td></tr><tr><td>Passat</td><td>Lisbon</td></tr></table>"
    exported = {"scope": "raw", "run": {"id": 1}, "pages": [
        {"page_id": 1, "rel_path": "book.pdf", "pdf_page": 0, "blocks": [
            {"id": 1, "label": "Text", "content": "The ship sailed from", "continuation": "cross"},
            {"id": 2, "label": "Table", "content": table_a, "continuation": "cross"},
        ]},
        {"page_id": 2, "rel_path": "book.pdf", "pdf_page": 1, "blocks": [
            {"id": 3, "label": "Text", "content": " Genoa in May.", "continuation": "append"},
            {"id": 4, "label": "Table", "content": table_b, "continuation": "append"},
            {"id": 5, "label": "Text", "content": "Next paragraph."},
        ]},
    ]}
    md = run_documents.run_markdown(exported, {})
    assert "The ship sailed from Genoa in May." in md
    # One table; the header printed again on page 2 is dropped.
    assert md.count("| Ship | Port |") == 1
    assert "| Pamir | Genoa |" in md and "| Passat | Lisbon |" in md
    assert md.index("<!-- p. 2 · book.pdf -->") < md.index("Next paragraph.")


def test_document_mode_needs_a_model_that_reads_documents(monkeypatch):
    from fastapi import HTTPException

    from app.services import recognition

    monkeypatch.setattr(recognition.inference, "get_inference_config",
                        lambda: {"enabled": True, "adapter_id": "monkeyocrv2-parsing", "url": "http://127.0.0.1:1/v1"})
    monkeypatch.setattr(recognition, "_refuse_host_blocked", lambda *a: None)
    with pytest.raises(HTTPException) as refused:
        recognition.create_run(1, [1], engine="model", model_mode="document", lang="en")
    assert refused.value.status_code == 422
    assert "whole documents" in refused.value.detail


def test_a_document_run_saves_every_page_through_the_page_pipeline(tmp_path, monkeypatch):
    """Worker: a document run hands each page its share of the response."""
    import time

    from fastapi.testclient import TestClient

    from app.adapters import runs as runstore
    from app.main import app
    from app.services import recognition

    archive = tmp_path / "archive"
    archive.mkdir()
    for i in range(3):
        Image.new("RGB", (100, 140), "white").save(archive / f"p{i}.png")
    with TestClient(app) as http:
        project_id = http.post("/api/projects", json={"name": "Book", "archive_dir": str(archive)}).json()["id"]
        http.post(f"/api/projects/{project_id}/scan")
        page_ids = [p["id"] for p in http.get(f"/api/projects/{project_id}/pages").json()["items"]]
        run_id = runstore.create(
            project_id, page_ids, engine="model", mode="replace_drafts", model_mode="document",
            stop_policy="none", owner_id=None, model_name="Unlimited-OCR-2", adapter_id="unlimited-ocr-2",
            provider="vast", endpoint="http://127.0.0.1:9/v1",
        )
    client = FakeClient(fits=10)
    monkeypatch.setattr(recognition.inference, "get_vllm_client", lambda: client)
    seen = []

    def page(run, item, opts, page_client, engine, cancel):
        seen.append((item["page_id"], page_client._raw, opts.model_mode))
        return [], {"detected": 1, "inserted": 1}, None

    monkeypatch.setattr(recognition, "_recognize_page", page)
    recognition._worker(run_id, threading.Event())
    assert [s[0] for s in seen] == sorted(page_ids)
    assert all(raw.startswith("<PAGE><BLOCK>text") for _p, raw, _m in seen)
    assert {m for *_x, m in seen} == {"native"}
    assert client.requests == [3]
    assert runstore.items_for_run(run_id)[0]["state"] == "finished"


def test_joining_a_paragraph_reads_like_text():
    assert run_documents.join_continued("sailed from", "Genoa") == "sailed from Genoa"
    assert run_documents.join_continued("el 24 de no-", "viembre del 978") == "el 24 de noviembre del 978"
    assert run_documents.join_continued("北京", "大学") == "北京大学"
