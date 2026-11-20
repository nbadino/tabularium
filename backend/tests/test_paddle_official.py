"""Test senza dipendenze del bridge verso gli output JSON PaddleOCR-VL."""
from __future__ import annotations

from app.services.paddle_official import parse_result


def test_parse_result_accepts_nested_paddlex_block_variants():
    result = parse_result(
        {
            "output": [
                {
                    "block_bbox": [1, 2, 30, 40],
                    "block_label": "table",
                    "block_content": "<fcel>A</fcel>",
                }
            ]
        },
        100,
        200,
    )
    assert result == [{
        "bbox": [1, 2, 30, 40],
        "label": "Table",
        "content": "<fcel>A</fcel>",
    }]


def test_parse_result_converts_official_quad_and_polygon_geometry_to_xyxy():
    result = parse_result(
        {"parsing_res_list": [
            {"block_bbox": [[5, 10], [40, 2], [48, 30], [12, 38]], "block_label": "text", "block_content": "quad"},
            {"block_bbox": [2, 4, 30, 4, 36, 18, 20, 32, 2, 20], "block_label": "table", "block_content": "poly"},
        ]},
        100,
        100,
    )
    assert result == [
        {"bbox": [5, 2, 48, 38], "label": "Text", "content": "quad"},
        {"bbox": [2, 4, 36, 32], "label": "Table", "content": "poly"},
    ]


def test_parse_result_uses_paddlex_ordered_blocks_without_layout_duplicates():
    block = {
        "block_bbox": [1, 2, 30, 40],
        "block_label": "table",
        "block_content": "<table><tr><td>A</td></tr></table>",
    }
    # Current PaddleOCRVL saves the same page regions in both fields. The
    # ordered, content-bearing list is authoritative for annotations.
    payload = {"res": {
        "parsing_res_list": [block],
        "layout_det_res": {"boxes": [block]},
    }}
    result = parse_result(payload, 100, 200)
    assert result == [{
        "bbox": [1, 2, 30, 40],
        "label": "Table",
        "content": "<table><tr><td>A</td></tr></table>",
    }]


def test_parse_result_falls_back_to_full_page_markdown():
    result = parse_result({"markdownText": "hello"}, 640, 480)
    assert result == [{"bbox": [0, 0, 640, 480], "label": "Text", "content": "hello"}]


def test_parse_result_does_not_invent_blocks_for_empty_output():
    assert parse_result({}, 640, 480) == []


def test_paddle_labels_land_in_the_tabularium_taxonomy():
    """Le label di PP-DocLayout entrano nel DB come label di Tabularium.

    Un `doc_title` grezzo non è una label del progetto: il dataset builder lo
    segnala come «classe senza prompt» e la UI mostra un valore che non esiste
    nella palette. La mappa copre la tassonomia pubblica (§2.7 di AGENTS.md);
    una label che resta fuori passa comunque, visibile, invece di essere
    mascherata da un "Text" che direbbe una cosa falsa.
    """
    from app.services.paddle_official import parse_result

    expected = {
        "doc_title": "Title",
        "paragraph_title": "Section-header",
        "figure_title": "Caption",
        "table_title": "Caption",
        "number": "Issue-number",
        "header": "Page-header",
        "footer": "Page-footer",
        "footnote": "Footnote",
        "vision_footnote": "Footnote",
        "text": "Text",
        "abstract": "Text",
        "vertical_text": "Text",
        "reference": "List-item",
        "table": "Table",
        "formula": "Formula",
        "image": "Picture",
        "chart": "Picture",
        "seal": "Picture",
    }
    payload = [
        {"block_label": label, "block_bbox": [0, 0, 10, 10], "block_content": label}
        for label in expected
    ]
    payload.append({"block_label": "label_ignota", "block_bbox": [0, 0, 5, 5], "block_content": "label_ignota"})

    out = {item["content"]: item["label"] for item in parse_result(payload, 100, 100)}

    for raw, wanted in expected.items():
        assert out[raw] == wanted, f"{raw} -> {out[raw]}, atteso {wanted}"
    assert out["label_ignota"] == "label_ignota"


def test_persistent_layout_worker_reuses_single_process_and_restarts_on_exit(monkeypatch):
    """Stage 1 (`PP-DocLayoutV3`) reuses one warm subprocess across pages instead of
    re-importing PaddleX and writing a temporary PNG on every page."""
    import io
    import json
    from PIL import Image
    from app.services import paddle_official

    paddle_official._shutdown_layout_worker()
    paddle_official._clear_layout_cache()
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda: True)
    monkeypatch.setattr(paddle_official.paddle_runtime, "cpu_unsupported_reason", lambda: None)
    monkeypatch.setattr(paddle_official.paddle_runtime, "python_bin", lambda: "/fake/paddle-python")

    spawned: list["FakeWorkerProc"] = []

    class FakeStdin:
        def __init__(self, owner: "FakeWorkerProc") -> None:
            self.owner = owner

        def write(self, data: bytes) -> int:
            self.owner.buffer.extend(data)
            self.owner._try_consume()
            return len(data)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeWorkerProc:
        def __init__(self, fail_first: bool = False) -> None:
            self.buffer = bytearray()
            self.responses: list[bytes] = []
            self.requests = 0
            self.model_dirs: list = []
            self.exited: int | None = None
            self.fail_first = fail_first
            self.stdin = FakeStdin(self)
            self.stdout = self
            self.stderr = io.BytesIO(b"")

        def _try_consume(self) -> None:
            while b"\n" in self.buffer:
                nl = self.buffer.index(b"\n")
                header = json.loads(bytes(self.buffer[:nl]).decode("utf-8"))
                nbytes = int(header["nbytes"])
                if len(self.buffer) < nl + 1 + nbytes:
                    return
                raw = bytes(self.buffer[nl + 1 : nl + 1 + nbytes])
                del self.buffer[: nl + 1 + nbytes]
                assert len(raw) == header["width"] * header["height"] * 3
                self.requests += 1
                self.model_dirs.append(header.get("model_dir"))
                if self.fail_first and self.requests == 1:
                    self.exited = 1
                    return
                boxes = [{"label": "table", "coordinate": [10.0, 20.0, 90.0, 80.0]}]
                self.responses.append((json.dumps({"ok": True, "boxes": boxes}) + "\n").encode("utf-8"))

        def readline(self) -> bytes:
            if not self.responses:
                return b""
            return self.responses.pop(0)

        def poll(self) -> int | None:
            return self.exited

        def terminate(self) -> None:
            self.exited = 0

        def wait(self, timeout: float | None = None) -> int:
            self.exited = 0
            return 0

    def fake_popen(argv, **kwargs):
        proc = FakeWorkerProc()
        spawned.append(proc)
        return proc

    monkeypatch.setattr(paddle_official.subprocess, "Popen", fake_popen)
    try:
        img1 = Image.new("RGB", (100, 100), "white")
        img2 = Image.new("RGB", (100, 100), "black")
        r1 = paddle_official.detect_layout(img1, threshold=0.25)
        r2 = paddle_official.detect_layout(img2, threshold=0.25)
        assert r1 == [{"label": "table", "bbox": [100, 200, 900, 800]}]
        assert r2 == [{"label": "table", "bbox": [100, 200, 900, 800]}]
        # Both distinct images were served by the SAME warm worker process!
        assert len(spawned) == 1
        assert spawned[0].requests == 2

        # Repeating img1 at the same threshold hits the in-memory LRU cache (0 IPC)!
        r1_cached = paddle_official.detect_layout(img1, threshold=0.25)
        assert r1_cached == r1
        assert spawned[0].requests == 2

        # A fine-tuned layout model is a different detector: no cache hit across models,
        # and the worker is told which export to load.
        paddle_official.detect_layout(img1, threshold=0.25, model_dir="/runs/layout/inference")
        assert spawned[0].requests == 3
        assert spawned[0].model_dirs == ["", "", "/runs/layout/inference"]

        # Simulate worker crash on a new image -> cleanly respawns and succeeds!
        spawned[0].exited = 1
        img3 = Image.new("RGB", (100, 100), "blue")
        r3 = paddle_official.detect_layout(img3, threshold=0.25)
        assert r3 == [{"label": "table", "bbox": [100, 200, 900, 800]}]
        assert len(spawned) == 2
    finally:
        paddle_official._shutdown_layout_worker()
        paddle_official._clear_layout_cache()


def test_layout_worker_recycles_after_max_requests(monkeypatch):
    """Persistent PP-DocLayoutV3 worker recycles after `_LAYOUT_WORKER_MAX_REQUESTS`
    pages so C++/oneDNN RSS stays bounded on long multi-hundred-page bulk runs."""
    import io
    import json
    from PIL import Image
    from app.services import paddle_official

    paddle_official._shutdown_layout_worker()
    paddle_official._clear_layout_cache()
    monkeypatch.setattr(paddle_official.paddle_runtime, "ready", lambda: True)
    monkeypatch.setattr(paddle_official.paddle_runtime, "cpu_unsupported_reason", lambda: None)
    monkeypatch.setattr(paddle_official.paddle_runtime, "python_bin", lambda: "/fake/paddle-python")
    monkeypatch.setattr(paddle_official, "_LAYOUT_WORKER_MAX_REQUESTS", 2)

    spawned: list = []

    class FakeStdin:
        def __init__(self, owner) -> None:
            self.owner = owner

        def write(self, data: bytes) -> int:
            self.owner.buffer.extend(data)
            self.owner._try_consume()
            return len(data)

        def flush(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeWorkerProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid
            self.buffer = bytearray()
            self.responses: list[bytes] = []
            self.requests = 0
            self.model_dirs: list = []
            self.exited: int | None = None
            self.stdin = FakeStdin(self)
            self.stdout = self
            self.stderr = io.BytesIO(b"")

        def _try_consume(self) -> None:
            while b"\n" in self.buffer:
                nl = self.buffer.index(b"\n")
                header = json.loads(bytes(self.buffer[:nl]).decode("utf-8"))
                nbytes = int(header["nbytes"])
                if len(self.buffer) < nl + 1 + nbytes:
                    return
                del self.buffer[: nl + 1 + nbytes]
                self.requests += 1
                boxes = [{"label": "table", "coordinate": [10.0, 20.0, 90.0, 80.0]}]
                self.responses.append((json.dumps({"ok": True, "boxes": boxes}) + "\n").encode("utf-8"))

        def readline(self) -> bytes:
            return self.responses.pop(0) if self.responses else b""

        def poll(self) -> int | None:
            return self.exited

        def terminate(self) -> None:
            self.exited = 0

        def wait(self, timeout: float | None = None) -> int:
            self.exited = 0
            return 0

    monkeypatch.setattr(
        paddle_official.subprocess,
        "Popen",
        lambda argv, **kwargs: (spawned.append(FakeWorkerProc(9000 + len(spawned))), spawned[-1])[1],
    )
    try:
        for color in ("white", "black", "blue"):
            paddle_official.detect_layout(Image.new("RGB", (60, 60), color), threshold=0.25)

        assert len(spawned) == 2
        assert spawned[0].requests == 2
        assert spawned[0].poll() is not None
        assert spawned[1].requests == 1
        assert spawned[1].poll() is None
    finally:
        paddle_official._shutdown_layout_worker()
        paddle_official._clear_layout_cache()


