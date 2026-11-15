"""Whole-document recognition for models that parse many pages in one request.

Unlimited-OCR 2's model card: "All pages of a document are parsed in one request, so
the model can use cross-page context". A request still has to fit the context the
server serves, so a long book goes out as the longest runs of consecutive pages that
fit, and the next run starts where the previous one stopped:

* the output budget is 30% of the context and the prompt gets the rest (the vendor's
  `infer.py`: `gen_reserve = 0.3 * max_model_len`); a page's prompt cost starts at
  the model's declared value and is refitted from the input size the server reports
  when it refuses a request as too long (`infer.py`'s refit);
* the response streams; each `<PAGE>` marker closes the previous page, which is saved
  at once, so the run shows progress and an interrupted request loses one page at
  most;
* when the output budget runs out, the unfinished page and those after it go into the
  next request, sized on how many pages the last one managed.

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


def plan(context: int, page_tokens: float, remaining: int) -> tuple[int, int]:
    """`(pages, max_tokens)` for the next request: the output budget, and as many
    pages as the rest of the context holds."""
    max_tokens = max(1, int(context * OUTPUT_SHARE))
    room = context - max_tokens - PROMPT_MARGIN
    pages = int(room // max(1.0, page_tokens))
    return max(1, min(remaining, pages)), max_tokens


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
    `fallback_page(item)` recognizes a page on its own, used when a request cannot
    make progress (a single page that does not fit, an empty response).
    """
    context = client.served_context_length()
    page_tokens = float(getattr(client.adapter, "document_page_tokens", 0) or 1000)
    pending = list(items)
    limit: int | None = None  # pages the last request managed, when it was cut short
    while pending and not cancel.is_set():
        before_request()
        if cancel.is_set():
            break
        pages, max_tokens = plan(context, page_tokens, len(pending))
        if limit is not None:
            pages = max(1, min(pages, limit))
        batch = pending[:pages]
        def on_page(index: int, segment: str, batch=batch) -> None:
            if index < len(batch):  # a stray extra <PAGE> has no page to go to
                save_page(batch[index], segment)

        stream = PageStream(on_page)
        try:
            text, finish = client.end2end_document(
                [load_image(item) for item in batch],
                max_tokens=max_tokens,
                on_delta=stream.feed,
                cancel_event=cancel,
            )
        except Exception as exc:  # noqa: BLE001
            from .inference import PromptTooLong  # noqa: PLC0415

            if isinstance(exc, PromptTooLong) and len(batch) > 1:
                # The server says what the input really costs: refit and retry.
                page_tokens = max(page_tokens * 1.05, exc.prompt_tokens / len(batch) * 1.05)
                log(f"document request too long ({exc.prompt_tokens} tokens for {len(batch)} pages): refit")
                continue
            if stream.done == 0:
                fallback_page(batch[0])
                pending = pending[1:]
                continue
            finish = "interrupted"
            text = ""
        stream.finish(complete=finish not in {"length", "interrupted"})
        done = stream.done
        log(f"document request: {done} of {len(batch)} pages (finish={finish})")
        if done == 0:
            # No page closed: a single page larger than the output budget, or an
            # empty answer. That page goes alone; the rest continues as a document.
            fallback_page(batch[0])
            pending = pending[1:]
            limit = None
            continue
        pending = pending[done:]
        limit = max(1, int(done * 1.1)) if done < len(batch) else None
