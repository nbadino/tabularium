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


def test_a_slow_reader_does_not_hold_the_model():
    """The lock covers the generation, not the client's reading pace: a reader that
    stops pulling (a slow or vanished connection) must not keep the model locked."""
    stalled = server._exclusive(lambda: iter("abc"), lambda: None)
    assert next(stalled) == "a"  # ...and nothing more is read

    done = threading.Event()
    threading.Thread(target=lambda: (list(server._exclusive(lambda: iter("x"), lambda: None)), done.set()), daemon=True).start()
    assert done.wait(2), "the model stayed locked while the first reader was idle"
    assert list(stalled) == ["b", "c"]  # what was generated is still delivered


def test_a_closed_reader_stops_the_generation():
    produced: list[int] = []

    def endless():
        while True:
            produced.append(1)
            yield "t"

    stream = server._exclusive(endless, lambda: None)
    next(stream)
    stream.close()
    import time

    time.sleep(0.2)
    count = len(produced)
    time.sleep(0.2)
    assert len(produced) == count, "the generation kept running after the reader left"


def test_a_failed_generation_still_frees_memory_and_reports_the_error():
    released: list[bool] = []

    def broken():
        yield "a"
        raise RuntimeError("metal error")

    stream = server._exclusive(broken, lambda: released.append(True))
    assert next(stream) == "a"
    try:
        next(stream)
        raise AssertionError("the error was swallowed")
    except RuntimeError as exc:
        assert "metal" in str(exc)
    assert released == [True]
