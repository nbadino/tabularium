"""Unlimited-OCR-2: the vendor's block protocol, its SGLang request and its recipe."""
from __future__ import annotations

import json
import pickle
import sys
import types

from app.services import auto_training, serve_recipes, sglang_runtime, train_recipes
from app.services.model_adapters import get_adapter
from app.services.prefill import native_mode


def test_unlimited2_parses_the_vendor_block_format():
    # Shapes from `utils/README.md` and `process_unlimitedOCR2.BLOCK_RE`.
    adapter = get_adapter("unlimited-ocr-2")
    items = adapter.parse_layout(
        "<PAGE><BLOCK>title<LEVEL1>[100,40,900,90]<FORK>HISTORIC SHIPPING INDEX"
        "<BLOCK>header[10,5,300,30]<FORK>Lloyd's"
        "<BLOCK>text[100,100,900,200]<LEVEL2><FORK>Arrived at <|ref|>Genoa<|/ref|>"
        '<BLOCK>cross_table[50,210,950,1003]<FORK><table><tr><td colspan="2x">Ship</td></tr></table>'
        "<MERGE><BLOCK>image[0,0,120,120]<FORK>a ship"
        "\nequation[200,600,800,650]<FORK>x^2"
        "<BLOCK>marginalia[1,1,2,2]<FORK>unknown label"
        "<BLOCK>text[500,500,400,600]<FORK>inverted box"
        "<BLOCK>text<FORK>no box"
    )
    assert [{k: v for k, v in item.items() if k != "native"} for item in items] == [
        {"bbox": [100, 40, 900, 90], "label": "Title", "content": "HISTORIC SHIPPING INDEX"},
        {"bbox": [10, 5, 300, 30], "label": "Page-header", "content": "Lloyd's"},
        {"bbox": [100, 100, 900, 200], "label": "Text", "content": "Arrived at Genoa"},
        # Clamped to 0-1000 like the vendor's `_scale`; malformed span values repaired.
        {"bbox": [50, 210, 950, 1000], "label": "Table",
         "content": '<table><tr><td colspan="2">Ship</td></tr></table>'},
        {"bbox": [0, 0, 120, 120], "label": "Picture", "content": ""},
        # A bare block (no `<BLOCK>`) with a known label is recovered.
        {"bbox": [200, 600, 800, 650], "label": "Formula", "content": "x^2"},
    ]


def test_unlimited2_request_follows_the_sglang_example():
    adapter = get_adapter("unlimited-ocr-2")
    assert native_mode(adapter) == "end2end"
    assert adapter.prompt_for("end2end") == "<PAGE><image>Multi page parsing."
    overrides = adapter.request_overrides("end2end")
    assert overrides["skip_special_tokens"] is False
    assert overrides["images_config"] == {"crop_mode": True}
    assert overrides["custom_params"] == {
        "ngram_size": 35, "window_size": 128, "page_token_id": 128827,
        "eos_token_id": 1, "max_page_tokens": 20000,
    }
    # `to_str()` is JSON around a hex pickle; ours must resolve to SGLang's class.
    module = types.ModuleType("sglang.srt.sampling.custom_logit_processor")

    class DeepseekOCRNoRepeatNGramLogitProcessor:  # stand-in for the server's class
        pass

    module.DeepseekOCRNoRepeatNGramLogitProcessor = DeepseekOCRNoRepeatNGramLogitProcessor
    names = ("sglang", "sglang.srt", "sglang.srt.sampling", module.__name__)
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules.update({name: types.ModuleType(name) for name in names[:-1]})
    sys.modules[module.__name__] = module
    try:
        payload = bytes.fromhex(json.loads(overrides["custom_logit_processor"])["callable"])
        assert pickle.loads(payload) is DeepseekOCRNoRepeatNGramLogitProcessor
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def test_unlimited2_is_served_by_sglang_with_the_vendor_environment():
    recipe = serve_recipes.recipe_for("unlimited-ocr-2")
    assert recipe.runtime == "sglang"
    assert not recipe.installs_vllm
    argv = serve_recipes.serve_argv(recipe, model_path="/m", port=8000, api_key="k")
    assert argv[:4] == ["-m", "sglang.launch_server", "--model-path", "/m"]
    assert argv[argv.index("--chat-template") + 1] == "unlimited-ocr"
    assert "--enable-custom-logit-processor" in argv
    assert argv[-4:] == ["--served-model-name", "Unlimited-OCR-2", "--api-key", "k"]
    assert dict(recipe.serve_env)["SGLANG_UOCR_MAX_PAGE_TOKENS"] == "20000"

    local = get_adapter("unlimited-ocr-2").serve_command("/m", 8001)
    assert local[0] == str(sglang_runtime.python_bin("unlimited-ocr-2"))
    assert local[1:3] == ["-m", "sglang.launch_server"]


def test_unlimited2_vast_recipe_carries_interpreter_and_environment():
    from app.services import cloud_manager

    recipe = cloud_manager.build_provision_recipe("unlimited-ocr-2")
    assert recipe["runtime"] == "sglang"
    assert recipe["install_vllm"] is False
    assert recipe["python_version"] == "3.10"
    assert recipe["serve_env"]["SGLANG_UOCR_EANABLE_DOC_LIMIT"] == "1"
    assert any(item.endswith("py3-none-any.whl") for item in recipe["pip_extra"])
    assert recipe["argv"][:2] == ["-m", "sglang.launch_server"]


def test_unlimited2_trains_with_ms_swift_main_and_its_own_profile():
    recipe = train_recipes.get_recipe("unlimited-ocr-2")
    assert recipe.script_ready
    assert recipe.swift_model_type == "unlimited-ocr"
    assert recipe.swift_template == "unlimited_ocr"
    assert recipe.defaults_for("lora")["grad_accum"] == 16
    assert recipe.environment["transformers"] == "==4.52.1"
    assert auto_training.profile_for("unlimited-ocr-2") == "uocr2"


def test_unlimited2_parser_applies_the_vendor_cleanup():
    items = get_adapter("unlimited-ocr-2").parse_layout(
        "<PAGE><BLOCK>text[10,10,100,50]<FORK>[unreadable]"
        "<BLOCK>text[10,60,100,90]<FORK>Keep\n[1,2,3,4]\nthis"
        "<BLOCK>picture[10,100,100,190]<FORK>[image]"
    )
    assert [{k: v for k, v in item.items() if k != "native"} for item in items] == [
        {"bbox": [10, 60, 100, 90], "label": "Text", "content": "Keep\n\nthis"},
        {"bbox": [10, 100, 100, 190], "label": "Picture", "content": ""},
    ]


def test_unlimited2_parser_drops_blocks_without_content():
    """`_render_blocks` skips a block with no content unless it is an image.

    Seen on a real A40 run: a Lloyd's register page ended in ~460 empty
    `text` blocks, which became empty boxes in the editor.
    """
    raw = "<PAGE><BLOCK>text[10,10,100,50]<FORK>Agawam" + "".join(
        f"<BLOCK>text[434,{y},529,{y + 10}]<FORK>\n" for y in range(100, 400, 10)
    ) + "<BLOCK>figure[10,500,100,600]<FORK><BLOCK>table[10,700,900,800]<FORK><table></table>"
    items = get_adapter("unlimited-ocr-2").parse_layout(raw)
    assert [(item["label"], item["content"]) for item in items] == [
        ("Text", "Agawam"),
        ("Picture", ""),
        ("Table", "<table></table>"),
    ]


def test_unlimited2_document_request_follows_the_readme(monkeypatch):
    adapter = get_adapter("unlimited-ocr-2")
    assert adapter.document_prompt(3) == "<PAGE><image><PAGE><image><PAGE><image>Multi page parsing."
    assert adapter.request_overrides("document")["custom_params"]["window_size"] == 1024
    assert adapter.request_overrides("end2end")["custom_params"]["window_size"] == 128
    # tokens_per_page * pages for the whole request: the server splits a long document
    # into page windows and spends the budget across them, so the context does not cap it.
    assert adapter.document_budget(2) == 16000
    assert adapter.document_budget(300) == 2_400_000
    assert adapter.document_scope() is False


class _Stream:
    status_code = 200
    headers: dict = {}

    def __init__(self, deltas, finish="stop"):
        self.deltas, self.finish = deltas, finish

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=True):
        for delta in self.deltas:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": delta}}]})
        yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": self.finish}]})
        yield "data: [DONE]"


def _document_client(monkeypatch, deltas, finish="stop"):
    from app.services import inference as infmod

    sent = {}

    def post(url, json=None, **kwargs):  # noqa: A002
        sent.update(json)
        sent["_timeout"] = kwargs.get("timeout")
        return _Stream(deltas, finish)

    monkeypatch.setattr(infmod.requests, "post", post)
    client = infmod.VllmClient(url="http://127.0.0.1:9/v1", model="Unlimited-OCR-2", adapter=get_adapter("unlimited-ocr-2"))
    return client, sent


def test_end2end_document_tolerates_a_slow_upload(monkeypatch):
    """urllib3 sends the body under the connect timeout: 10 s of stall cut a 26 MB,
    four-page request over an SSH tunnel on a real A40 run (`ClientDisconnect`)."""
    from app.services import inference as infmod

    client, sent = _document_client(monkeypatch, ["<PAGE><BLOCK>text[0,0,10,10]<FORK>one"])
    client.end2end_document(["a"], lambda index, items: None)
    assert sent["_timeout"][0] == infmod.DOCUMENT_UPLOAD_STALL_S >= 120
    client.end2end(__import__("PIL.Image", fromlist=["Image"]).new("RGB", (10, 10)))
    assert sent["_timeout"][0] == 10


def test_end2end_document_splits_pages_while_streaming(monkeypatch):
    client, sent = _document_client(monkeypatch, [
        "<PAGE><BLOCK>text[0,0,10,10]<FORK>one",
        "<PA",  # a marker split across two deltas
        "GE><BLOCK>append_table[0,0,20,20]<FORK><table></table>",
        "<PAGE><BLOCK>text[0,0,30,30]<FORK>three",
    ])
    pages = []
    delivered = client.end2end_document(["a", "b", "c"], lambda index, items: pages.append((index, items)))
    assert delivered == 3
    assert [(i, [b["label"] for b in items]) for i, items in pages] == [(0, ["Text"]), (1, ["Table"]), (2, ["Text"])]
    assert pages[0][1][0]["content"] == "one"
    content = sent["messages"][0]["content"]
    assert [part["type"] for part in content] == ["image_url", "image_url", "image_url", "text"]
    assert content[-1]["text"] == "<PAGE><image>" * 3 + "Multi page parsing."
    assert sent["custom_params"]["window_size"] == 1024
    assert sent["max_tokens"] == 24000
    assert sent["temperature"] == 0.0
    assert sent["stream"] is True


def test_end2end_document_withholds_a_page_cut_off_by_the_budget(monkeypatch):
    client, _ = _document_client(
        monkeypatch,
        ["<PAGE><BLOCK>text[0,0,10,10]<FORK>one<PAGE><BLOCK>text[0,0,10,10]<FORK>cut of"],
        finish="length",
    )
    pages = []
    assert client.end2end_document(["a", "b"], lambda index, items: pages.append(index)) == 1
    assert pages == [0]


def test_end2end_document_ignores_pages_beyond_the_request(monkeypatch):
    client, _ = _document_client(monkeypatch, ["<PAGE>x<PAGE>y<PAGE>z"])
    pages = []
    assert client.end2end_document(["a", "b"], lambda index, items: pages.append(index)) == 2
    assert pages == [0, 1]


def _item(ident, rel_path, pdf_page=None, collection_id=None):
    return {"id": ident, "page_id": ident, "rel_path": rel_path, "pdf_page": pdf_page,
            "collection_id": collection_id}


class _DocumentScope:
    class adapter:  # noqa: N801
        @staticmethod
        def document_scope():
            return True


class _Native:
    model_mode = "native"


def test_work_units_send_each_document_whole():
    """No page cap: 300 pages of one PDF are one request (the server windows them)."""
    from app.services import recognition

    items = [_item(i, "big.pdf", i - 1) for i in range(1, 301)] + [
        _item(301, "b.pdf", 0),
        _item(302, "issue1/p1.png"), _item(303, "issue1/p2.png"), _item(304, "issue2/p1.png"),
    ]
    groups = recognition._work_units(items, _Native, _DocumentScope)
    assert [len(group) for group in groups] == [300, 1, 2, 1]


def test_work_units_read_a_collection_as_one_document_in_page_order():
    """A collection spans folders and PDFs; its pages go together, in project order."""
    from app.services import recognition

    items = [
        _item(1, "1920/p2.tif", collection_id=7),
        _item(2, "loose/x.png"),
        _item(3, "1919/a.pdf", 1, collection_id=7),
        _item(4, "1920/p1.tif", collection_id=7),
        _item(5, "1919/a.pdf", 0, collection_id=7),
        _item(6, "1920/p3.tif", collection_id=8),
    ]
    groups = recognition._work_units(items, _Native, _DocumentScope)
    assert [[i["id"] for i in group] for group in groups] == [[5, 3, 4, 1], [2], [6]]


def test_document_pages_are_sent_as_lossless_grey_when_they_are_grey():
    import base64
    import io

    from PIL import Image

    from app.services import inference as infmod

    client = infmod.VllmClient(url="http://127.0.0.1:9/v1", model="Unlimited-OCR-2",
                               adapter=get_adapter("unlimited-ocr-2"))

    def decoded(uri):
        return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))

    grey = Image.linear_gradient("L").convert("RGB")
    sent = decoded(client.encode_page(grey))
    assert sent.mode == "L"
    assert sent.convert("RGB").tobytes() == grey.tobytes()
    colour = grey.copy()
    colour.putpixel((3, 3), (255, 0, 0))
    assert decoded(client.encode_page(colour)).mode == "RGB"
    # A bilevel register scan, as loaded (mode "1") or after an RGB round trip.
    bilevel = Image.linear_gradient("L").point(lambda v: 255 if v > 127 else 0).convert("1")
    for page in (bilevel, bilevel.convert("RGB")):
        sent = decoded(client.encode_page(page))
        assert sent.mode == "1"
        assert sent.convert("RGB").tobytes() == bilevel.convert("RGB").tobytes()


def test_work_units_stay_per_page_when_the_scope_is_page():
    from app.services import recognition

    adapter = get_adapter("unlimited-ocr-2")

    class Client:
        pass

    Client.adapter = adapter

    class Opts:
        model_mode = "native"

    items = [_item(1, "a.pdf", 0), _item(2, "a.pdf", 1)]
    assert [len(group) for group in recognition._work_units(items, Opts, Client)] == [1, 1]


def _document_harness(monkeypatch):
    from app.services import prefill, recognition

    marks = []
    monkeypatch.setattr(recognition, "_mark_item", lambda ident, state, **v: marks.append((ident, state, v)))
    monkeypatch.setattr(recognition, "_refresh_counts", lambda run_id: None)
    monkeypatch.setattr(prefill, "document_page_input", lambda project, page, opts, client: f"uri{page}")
    seen = []

    def page(run, item, opts, client, engine, cancel):
        alone = not getattr(client, "precomputed", False)
        seen.append((item["id"], "alone" if alone else client.end2end(None)))
        return [{"id": 1}], {"detected": 1, "inserted": 1}, None

    monkeypatch.setattr(recognition, "_recognize_page", page)
    return recognition, marks, seen


_RUN = {"id": 7, "project_id": 3, "provider": "vast", "engine": "model", "adapter_id": "unlimited-ocr-2"}
_TEXT = [{"bbox": [0, 0, 1, 1], "label": "Text", "content": "x"}]


def test_recognize_document_reads_alone_the_pages_it_did_not_return(monkeypatch):
    """Seen on a real A40 run: a page looping until `max_page_tokens` made the model
    emit `<PAGE><PAGE></td>` and stop, so the next page came back empty, unread."""
    import threading

    recognition, marks, seen = _document_harness(monkeypatch)

    class Client:
        def end2end_document(self, encoded, on_page, cancel_event=None):
            assert encoded == ["uri1", "uri2", "uri3"]
            on_page(0, _TEXT)
            on_page(1, [])  # empty: not read
            return 2  # page 3 never delivered (budget)

    group = [_item(1, "a.pdf", 0), _item(2, "a.pdf", 1), _item(3, "a.pdf", 2)]
    recognition._recognize_document(_RUN, group, object(), Client(), threading.Event())
    assert seen == [(1, _TEXT), (2, "alone"), (3, "alone")]
    assert {ident: state for ident, state, _v in marks} == {1: "finished", 2: "finished", 3: "finished"}


def test_recognize_document_fails_the_unread_pages_when_the_request_fails(monkeypatch):
    import threading

    recognition, marks, seen = _document_harness(monkeypatch)

    class Client:
        def end2end_document(self, encoded, on_page, cancel_event=None):
            on_page(0, _TEXT)
            raise RuntimeError("server gone")

    recognition._recognize_document(_RUN, [_item(1, "a.pdf", 0), _item(2, "a.pdf", 1)], object(), Client(), threading.Event())
    assert seen == [(1, _TEXT)]
    final = {ident: state for ident, state, _v in marks}
    assert final == {1: "finished", 2: "failed"}
    assert [v["error"] for ident, state, v in marks if state == "failed"] == ["server gone"]


def test_pdf_pages_reach_the_model_at_the_vendor_resolution(monkeypatch):
    from PIL import Image

    from app.services import pages as pagesvc

    displayed = Image.new("RGB", (10, 10))
    hires = Image.new("RGB", (30, 30))
    calls = []
    monkeypatch.setattr(pagesvc.scanmod, "render_pdf_page", lambda path, index, dpi: calls.append((index, dpi)) or (hires, (30, 30)))
    monkeypatch.setattr(pagesvc.Path, "exists", lambda self: True)
    active = {"value": None}
    monkeypatch.setattr(pagesvc, "_active_transform_path", lambda page_id: active["value"])
    page = {"id": 1, "source_kind": "pdf", "abs_path": "/x.pdf", "pdf_page": 4}

    assert pagesvc.model_input_image(page, displayed, dpi=300) is hires
    assert calls == [(4, 300)]
    assert pagesvc.model_input_image(page, displayed, dpi=None) is displayed
    assert pagesvc.model_input_image({**page, "source_kind": "image"}, displayed, dpi=300) is displayed
    active["value"] = "/deskewed.png"
    assert pagesvc.model_input_image(page, displayed, dpi=300) is displayed
