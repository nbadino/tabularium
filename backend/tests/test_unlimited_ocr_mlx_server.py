"""One MLX model, one generation at a time."""
from __future__ import annotations

import threading

from app.services import unlimited_ocr_mlx_server as server


def test_generations_on_the_shared_model_do_not_overlap():
    first_started, release_first = threading.Event(), threading.Event()
    order: list[str] = []

    def slow():
        order.append("first:start")
        first_started.set()
        release_first.wait(5)
        yield "a"
        order.append("first:end")

    def fast():
        order.append("second:start")
        yield "b"

    first = server._exclusive(slow, lambda: order.append("first:released"))
    worker = threading.Thread(target=lambda: list(first))
    worker.start()
    first_started.wait(5)
    second = threading.Thread(target=lambda: list(server._exclusive(fast, lambda: None)))
    second.start()
    second.join(0.3)
    assert "second:start" not in order, "the second generation started on a busy model"
    release_first.set()
    worker.join(5)
    second.join(5)
    assert order == ["first:start", "first:end", "first:released", "second:start"]
