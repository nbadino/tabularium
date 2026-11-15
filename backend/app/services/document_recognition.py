"""Whole-document recognition for models that parse many pages in one request.

Unlimited-OCR 2's model card: "All pages of a document are parsed in one request, so
the model can use cross-page context". A request still has to fit the context the
server serves, so a long book goes out as runs of consecutive pages, the next run
starting where the previous one stopped:

* sizing follows the vendor's `infer.py`: the prompt is what the pages cost and the
  output gets all the room left (`max_tokens = context - prompt - 700`). A run holds
  as many pages as the model can also *write* in that room, from per-page estimates
  of prompt and output tokens that are refitted after every request from the usage
  the server reports (and from its "too long" refusal, as `infer.py` does);
* a run never exceeds `MAX_PAGES_PER_REQUEST`: the pages are sent as images in one
  body, and a 333-page body exhausted the Tabularium host (OOM at 4.7 GB) and broke
  on the way to the GPU;
* the response streams; each `<PAGE>` marker closes the previous page, which is saved
  at once; when the output budget runs out, the next run starts at the unfinished
  page; a request that breaks before any output is retried with half the pages.

Context is lost only across the boundary between two requests. The server's own
page-window scheduling (`SGLANG_UOCR_DOC_*_PAGE_WINDOW`) works inside one request.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Callable, Sequence
from typing import Any

from PIL import Image

PAGE_MARK_RE = re.compile(r"<\s*/?\s*PAGE\s*\d*\s*>", re.I)
# `infer.py`: `PROMPT_MARGIN` tokens kept for the text part of the prompt.
PROMPT_MARGIN = 700
OUTPUT_SHARE = 0.3


def split_pages(text: str) -> list[str]:
    """The response cut at its `<PAGE>` markers, one segment per page in order
    (`process_unlimitedOCR2._parse_blocks`: a leading empty segment is dropped)."""
    segments = PAGE_MARK_RE.split(text or "")
    if segments and not segments[0].strip():
        segments = segments[1:]
    return segments


class PageStream:
    """Feeds streamed text and calls `on_page(index, segment)` for every page whose
    output is complete, i.e. once the marker of the next page has arrived.

    Markers are found incrementally (only the new text is scanned), so a response
    of millions of characters in hundreds of thousands of deltas stays linear.
    """

    _OVERLAP = 16  # a marker can be split across two deltas

    def __init__(self, on_page: Callable[[int, str], None]) -> None:
        self._on_page = on_page
        self._text = ""
        self._marks: list[tuple[int, int]] = []
        self.done = 0

    def _lead(self) -> bool:
        """Text before the first marker is page 0 when it is not blank (the vendor
        drops only an empty leading segment)."""
        first = self._marks[0][0] if self._marks else len(self._text)
        return bool(self._text[:first].strip())

    def _segment(self, index: int) -> str:
        lead = self._lead()
        starts = ([0] if lead else []) + [end for _start, end in self._marks]
        ends = [start for start, _end in self._marks][0 if lead else 1:] + [len(self._text)]
        return self._text[starts[index]:ends[index]]

    def _closed(self) -> int:
        return len(self._marks) if self._lead() else max(0, len(self._marks) - 1)

    def feed(self, delta: str) -> None:
        before = len(self._text)
        self._text += delta
        start = max(self._marks[-1][1] if self._marks else 0, before - self._OVERLAP)
        for match in PAGE_MARK_RE.finditer(self._text, start):
            if not self._marks or match.start() >= self._marks[-1][1]:
                self._marks.append((match.start(), match.end()))
        while self.done < self._closed():
            self._on_page(self.done, self._segment(self.done))
            self.done += 1

    def finish(self, complete: bool) -> None:
        """End of response: the last page counts only if the output was not cut."""
        if not complete or not self._text.strip():
            return
        total = self._closed() + 1
        while self.done < total:
            self._on_page(self.done, self._segment(self.done))
            self.done += 1


# Pages per request, at most: bounds the request body (one JPEG per page) and the
# memory to build it, whatever the context allows.
MAX_PAGES_PER_REQUEST = 48
# First guess of a dense page's output, until a request has been measured.
INITIAL_PAGE_OUTPUT = 2000
MIN_PAGE_OUTPUT = 200


def plan(context: int, page_in: float, page_out: float, remaining: int,
         cap: int = MAX_PAGES_PER_REQUEST) -> tuple[int, int]:
    """`(pages, max_tokens)` for the next request: as many pages as the context
    holds both to read (`page_in`) and to write (`page_out`), and the whole rest of
    the context as output budget (`infer.py`: `max_model_len - prompt - 700`)."""
    room = context - PROMPT_MARGIN
    pages = int(room // max(1.0, page_in + page_out))
    pages = max(1, min(remaining, cap, pages))
    max_tokens = max(1, int(context - pages * page_in - PROMPT_MARGIN))
    return pages, max_tokens


def recognize(
    items: Sequence[dict[str, Any]],
    *,
    client,
    load_image: Callable[[dict[str, Any]], Image.Image],
    save_page: Callable[[dict[str, Any], str], None],
    fallback_page: Callable[[dict[str, Any]], None],
    cancel: threading.Event,
    before_request: Callable[[], None] = lambda: None,
    log: Callable[[str], None] = lambda _m: None,
) -> None:
    """Recognize `items` (pages in book order) as one document.

    `save_page(item, segment)` stores one page from its share of the response;
    `fallback_page(item)` recognizes a page on its own, used when even a one-page
    request cannot make progress.
    """
    from .inference import PromptTooLong  # noqa: PLC0415

    context = client.served_context_length()
    cost = getattr(client.adapter, "document_page_cost", None)
    page_in = float(cost() if callable(cost) else getattr(client.adapter, "document_page_tokens", 1000))
    page_out = float(INITIAL_PAGE_OUTPUT)
    cap = MAX_PAGES_PER_REQUEST
    pending = list(items)
    while pending and not cancel.is_set():
        before_request()
        if cancel.is_set():
            break
        pages, max_tokens = plan(context, page_in, page_out, len(pending), cap)
        batch = pending[:pages]

        def on_page(index: int, segment: str, batch=batch) -> None:
            if index < len(batch):  # a stray extra <PAGE> has no page to go to
                save_page(batch[index], segment)

        stream = PageStream(on_page)
        usage: dict = {}
        try:
            text, finish, usage = client.end2end_document(
                (load_image(item) for item in batch),  # opened one at a time
                max_tokens=max_tokens,
                on_delta=stream.feed,
                cancel_event=cancel,
            )
        except PromptTooLong as exc:
            # The server says what the input really costs: refit and retry.
            page_in = max(page_in * 1.05, exc.prompt_tokens / len(batch) * 1.05)
            log(f"document request too long ({exc.prompt_tokens} tokens for {len(batch)} pages): refit")
            continue
        except Exception as exc:  # noqa: BLE001
            if stream.done == 0 and len(batch) > 1:
                # Broke before any output (upload, server): try half as many pages.
                cap = max(1, len(batch) // 2)
                log(f"document request of {len(batch)} pages failed ({exc}): retrying with {cap}")
                continue
            if stream.done == 0:
                fallback_page(batch[0])
                pending = pending[1:]
                continue
            finish, text = "interrupted", ""
        stream.finish(complete=finish not in {"length", "interrupted"})
        done = stream.done
        log(f"document request: {done} of {len(batch)} pages (finish={finish}, usage={usage})")
        # Refit the estimates from what the server measured.
        prompt_tokens = int((usage or {}).get("prompt_tokens") or 0)
        completion_tokens = int((usage or {}).get("completion_tokens") or 0)
        if prompt_tokens:
            page_in = prompt_tokens / len(batch)
        if done and completion_tokens:
            page_out = max(MIN_PAGE_OUTPUT, completion_tokens / done * 1.1)
        if done == 0:
            # Not even one page fitted the output: that page goes alone.
            fallback_page(batch[0])
            pending = pending[1:]
            continue
        pending = pending[done:]
