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


def test_plan_sizes_on_what_the_model_can_write_and_gives_it_the_rest():
    # infer.py: max_tokens = max_model_len - prompt - 700.
    pages, max_tokens = doc.plan(131072, 1000, 2000, 527)
    assert pages == (131072 - 700) // 3000
    assert max_tokens == 131072 - pages * 1000 - 700
    assert doc.plan(131072, 273, 200, 527)[0] == doc.MAX_PAGES_PER_REQUEST  # never a huge body
    assert doc.plan(131072, 1000, 2000, 5)[0] == 5


class FakeImage:
    opened = 0
    closed = 0

    def __init__(self):
        FakeImage.opened += 1
        self.open = True

    def close(self):
        FakeImage.closed += 1


class FakeClient:
    """Writes `out_per_page` tokens per page within `max_tokens`; a page costs
    `page_tokens` of prompt; bodies over `max_body` pages break on upload."""

    def __init__(self, *, out_per_page=1500, page_tokens=900, max_body=None, context=131072):
        self.adapter = get_adapter("unlimited-ocr-2")
        self.out_per_page = out_per_page
        self.page_tokens = page_tokens
        self.max_body = max_body
        self.context = context
        self.requests: list[int] = []
        self.peak_open = 0

    def served_context_length(self):
        return self.context

    def end2end_document(self, images, *, max_tokens, on_delta, cancel_event):
        n = 0
        for image in images:  # consumed one at a time, closed before the next
            self.peak_open = max(self.peak_open, FakeImage.opened - FakeImage.closed)
            image.close()
            n += 1
        self.requests.append(n)
        prompt = n * self.page_tokens
        if prompt + max_tokens > self.context:
            raise PromptTooLong(prompt, "too long")
        if self.max_body is not None and n > self.max_body:
            raise RuntimeError("Connection aborted")  # upload broke, no output
        done = min(n, max_tokens // self.out_per_page)
        for i in range(done):
            on_delta(f"<PAGE><BLOCK>text[1,1,9,9]<FORK>page{i}")
        if done < n:
            on_delta("<PAGE><BLOCK>text[1,1,9,9]<FORK>cut sh")
            return "", "length", {"prompt_tokens": prompt, "completion_tokens": done * self.out_per_page + 10}
        return "", "stop", {"prompt_tokens": prompt, "completion_tokens": done * self.out_per_page}


def _run(client, n):
    items = [{"id": i, "page_id": i} for i in range(n)]
    saved, alone = [], []
    doc.recognize(
        items, client=client, load_image=lambda _i: FakeImage(),
        save_page=lambda item, seg: saved.append((item["id"], seg)),
        fallback_page=lambda item: alone.append(item["id"]), cancel=threading.Event(),
    )
    return saved, alone


def test_a_long_book_goes_in_bounded_requests_sized_on_the_output():
    client = FakeClient(out_per_page=1500)
    saved, alone = _run(client, 527)
    assert [i for i, _ in saved] == list(range(527))  # every page, in order, once
    assert alone == []
    assert max(client.requests) <= doc.MAX_PAGES_PER_REQUEST
    # Pages are opened one at a time, never a whole request in memory.
    assert client.peak_open <= 1
    # After the first request the size follows the measured output.
    assert len(client.requests) <= 527 // 40 + 3


def test_a_too_long_prompt_is_refitted_from_the_server_count():
    # A page really costs 2500 prompt tokens, not the first guess.
    client = FakeClient(page_tokens=2500, out_per_page=500)
    saved, _ = _run(client, 100)
    assert [i for i, _ in saved] == list(range(100))
    assert len(client.requests) <= 6


def test_a_broken_upload_halves_the_request_instead_of_going_page_by_page():
    client = FakeClient(max_body=10, out_per_page=500)
    saved, alone = _run(client, 60)
    assert [i for i, _ in saved] == list(range(60))
    assert alone == []
    assert client.requests[1] == client.requests[0] // 2
    assert all(n <= 10 for n in client.requests[-3:])


def test_no_progress_falls_back_to_one_page_alone():
    client = FakeClient(out_per_page=10**9)  # not even one page fits the output
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
    client = FakeClient()
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


def test_pages_go_lossless_as_the_model_card_sends_them(tmp_path):
    from app.services.inference import _page_data_uri

    path = tmp_path / "p.png"
    Image.new("L", (40, 60), 200).save(path)
    with Image.open(path) as image:
        uri = _page_data_uri(image, None)
    import base64 as b64
    assert uri.startswith("data:image/png;base64,")
    assert b64.b64decode(uri.split(",", 1)[1]) == path.read_bytes()  # the file itself
    # An image without a file (deskewed in memory) goes as PNG, not JPEG.
    assert _page_data_uri(Image.new("RGB", (10, 10)), None).startswith("data:image/png;base64,")
