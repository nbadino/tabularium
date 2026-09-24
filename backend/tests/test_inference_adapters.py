"""Test per il legame VllmClient <-> adapter modello (Fase 2 multi-modello)."""
from __future__ import annotations

import json

from PIL import Image

from app.services import inference as infmod
from app.services import model_adapters
from app.services import otsl


def test_default_client_keeps_monkeyocrv2_prompts():
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1")
    assert client.adapter.adapter_id == "monkeyocrv2-parsing"
    assert infmod._prompt_for(client.adapter, "layout", None, "fallback") == infmod.LAYOUT_PROMPT
    assert infmod._prompt_for(client.adapter, "table", "Table", "fallback") == infmod.TABLE_PROMPT


def test_prompt_for_propagates_not_implemented_instead_of_falling_back():
    # Un adapter che non sa fare un task deve fallire in modo esplicito, non
    # ricevere in silenzio il prompt di un modello diverso (v. `_prompt_for`
    # in inference.py): mandare il prompt end2end di MonkeyOCRv2 a un modello
    # che non lo capisce è una degradazione silenziosa, non un fallback sicuro.
    adapter = model_adapters.get_adapter("mineru2.5")
    try:
        infmod._prompt_for(adapter, "end2end", None, "FALLBACK-PROMPT")
        raise AssertionError("doveva sollevare NotImplementedError")
    except NotImplementedError:
        pass


def test_prompt_for_still_falls_back_on_explicit_none_from_same_adapter():
    # Diverso da NotImplementedError: qui è lo STESSO adapter a scegliere
    # esplicitamente di non prompare per quella label (torna None), un
    # default deliberato — quel ramo di `or fallback` resta.
    adapter = model_adapters.get_adapter("monkeyocrv2-parsing")
    resolved = infmod._prompt_for(adapter, "text", "Picture", "FALLBACK-PROMPT")
    assert resolved == "FALLBACK-PROMPT"


def test_prompt_for_uses_verified_dots_ocr_end2end_prompt():
    adapter = model_adapters.get_adapter("dots-ocr")
    resolved = infmod._prompt_for(adapter, "end2end", None, "FALLBACK-PROMPT")
    assert resolved == adapter.prompt_for("end2end")
    assert resolved != "FALLBACK-PROMPT"


def test_dots_ocr_end2end_uses_category_pixel_boxes_without_monkey_retry():
    adapter = model_adapters.get_adapter("dots-ocr")
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1", adapter=adapter)
    calls = []
    raw = json.dumps([{
        "bbox": [10, 20, 150, 90],
        "category": "Title",
        "text": "Historic Shipping Index",
    }])
    client._chat = lambda image, prompt, **kwargs: calls.append(kwargs) or raw

    items = client.end2end(Image.new("RGB", (200, 100), "white"), total_timeout=5)

    assert len(calls) == 1
    assert items == [{
        "bbox": [50.0, 200.0, 750.0, 900.0],
        "label": "Title",
        "content": "Historic Shipping Index",
    }]


def test_dots_ocr_settings_override_vendor_sampling_and_image_limit(tmp_path, monkeypatch):
    from app import config
    from app.db import init_db
    from app.services import model_settings

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "dots-settings.db")
    init_db()
    model_settings.save_settings("dots-ocr", {
        "generation": {"temperature": 0.4, "max_tokens": 4096},
        "image": {"max_pixels": 2_000_000},
    })
    client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=model_adapters.get_adapter("dots-ocr"),
    )

    assert client.max_pixels == 2_000_000
    assert client._sampling_for("end2end") == {
        "temperature": 0.4,
        "top_p": 1.0,
        "max_tokens": 4096,
    }


def test_teleocr_native_gateway_posts_binary_image_with_requests_data(monkeypatch):
    adapter = model_adapters.get_adapter("teleocr")
    client = infmod.VllmClient(
        url="http://127.0.0.1:18888/v1", adapter=adapter,
        native_url="http://127.0.0.1:18889", provider="vast",
    )
    captured = {}

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"blocks": [{"type": "text", "bbox": [0.1, 0.2, 0.9, 0.8], "content": "native"}]}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    blocks = client.teleocr_native_page(Image.new("RGB", (32, 24), "white"))
    assert captured["url"] == "http://127.0.0.1:18889/parse"
    assert captured["data"].startswith(b"\x89PNG\r\n\x1a\n")
    assert "content" not in captured
    assert blocks == [{"bbox": [100, 200, 900, 800], "label": "Text", "content": "native"}]


def test_unlimited_ocr_uses_official_vllm_recipe_and_parses_grounding():
    adapter = model_adapters.get_adapter("unlimited-ocr")
    assert adapter.prompt_for("end2end").startswith("<image>")
    assert adapter.request_overrides("end2end") == {
        "skip_special_tokens": False,
        "images_config": {"image_mode": "gundam"},
        "vllm_xargs": {"ngram_size": 35, "window_size": 128},
    }
    parsed = adapter.parse_layout(
        "<|det|>text [10,20,300,400]<|/det|>Hello <|ref|>x<|/ref|>\n"
        "<|det|>image [1,2,3]<|/det|>ignored"
    )
    assert parsed == [{"bbox": [10.0, 20.0, 300.0, 400.0], "label": "text", "content": "Hello"}]


def test_unlimited_ocr_settings_reach_the_official_request_overrides(tmp_path, monkeypatch):
    from app import config
    from app.db import init_db
    from app.services import model_settings

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "unlimited-settings.db")
    init_db()
    model_settings.save_settings("unlimited-ocr", {
        "workflow": {"ngram_size": 42, "window_size": 1024, "image_mode": "base"},
    })
    adapter = model_adapters.get_adapter("unlimited-ocr")
    assert adapter.request_overrides("end2end") == {
        "skip_special_tokens": False,
        "images_config": {"image_mode": "base"},
        "vllm_xargs": {"ngram_size": 42, "window_size": 1024},
    }


def test_mineru_ngram_override_reaches_vllm_xargs(tmp_path, monkeypatch):
    from app import config
    from app.db import init_db
    from app.services import model_settings

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "mineru-settings.db")
    init_db()
    model_settings.save_settings("mineru2.5", {"generation": {"no_repeat_ngram_size": 72}})
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "ok"}}]})
            yield "data: [DONE]"

    def post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=model_adapters.get_adapter("mineru2.5"),
    )
    result = client._chat(
        Image.new("RGB", (12, 12), "white"), "\nText Recognition:",
        sampling=client._sampling_for("text"), task="text",
    )
    assert result == "ok"
    assert captured["vllm_xargs"]["no_repeat_ngram_size"] == 72
    assert "extra_args" not in captured


def test_adapter_without_sampling_keeps_native_vllm_generation_defaults(monkeypatch, tmp_path):
    from app import config
    from app.db import init_db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "native-sampling.db")
    init_db()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            yield 'data: ' + json.dumps({"choices": [{"delta": {"content": "ok"}}]})
            yield "data: [DONE]"

    def post(_url, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    adapter = model_adapters.get_adapter("paddleocr-vl")
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1", adapter=adapter)
    assert client._chat(
        Image.new("RGB", (12, 12), "white"), adapter.prompt_for("text"),
        sampling=client._sampling_for("text"), task="text",
    ) == "ok"
    assert "temperature" not in captured


def test_dots_serve_uses_required_chat_template_content_format():
    # Verificato sul README ufficiale: il flag è obbligatorio e non interferisce
    # con l'invio di contenuto multimodale in stile OpenAI (image_url + text) —
    # controlla solo come vLLM serializza il testo per il chat template, non
    # l'estrazione dei dati immagine (v. docstring di DotsOcrAdapter).
    command = model_adapters.get_adapter("dots-ocr").serve_command("/weights/dots", 8888)
    assert command is not None
    assert "--chat-template-content-format" in command
    assert command[command.index("--chat-template-content-format") + 1] == "string"


def test_paddle_vl_exposes_gpu_recognition_with_explicit_layout_fallback():
    adapter = model_adapters.get_adapter("paddleocr-vl")
    modes = model_adapters.supported_prefill_modes(adapter)
    assert modes == {
        "supports_two_stage": False,
        "supports_end2end": False,
        "supports_native": True,
    }
    assert adapter.prompt_for("table") == "Table Recognition:"
    assert getattr(adapter, "page_layout_fallback") == "official-pipeline"
    assert adapter.capabilities.table_format == "html"


def test_glm_native_gateway_forwards_official_page_and_sampling(monkeypatch):
    from types import SimpleNamespace

    adapter = model_adapters.get_adapter("glm-ocr")
    client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=adapter,
        native_url="http://127.0.0.1:18890", provider="vast",
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"json_result": [[{
                "label": "table", "content": "record", "bbox_2d": [0, 0, 1000, 1000],
            }]]}

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    blocks = client.glmocr_native_page(Image.new("RGB", (32, 24), "white"))
    assert captured["url"] == "http://127.0.0.1:18890/parse"
    assert captured["headers"]["x-glmocr-generation"] == json.dumps(
        model_adapters.get_adapter("glm-ocr").recommended_generation, separators=(",", ":")
    )
    assert captured["headers"]["x-glmocr-max-pixels"] == "71372800"
    assert blocks == [{"bbox": [0, 0, 1000, 1000], "label": "Table", "content": "record"}]


def test_glm_native_gateway_applies_and_restores_vendor_sdk_settings():
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    gateway_path = Path(__file__).resolve().parents[2] / "scripts/cloud/glmocr_native_gateway.py"
    spec = importlib.util.spec_from_file_location("glmocr_native_gateway_test", gateway_path)
    assert spec is not None and spec.loader is not None
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    page_loader = SimpleNamespace(
        max_tokens=8192, temperature=0.0, top_p=0.00001, top_k=1,
        repetition_penalty=1.1, max_pixels=1_003_520,
    )
    seen = {}

    class Parser:
        config_model = SimpleNamespace(pipeline=SimpleNamespace(page_loader=page_loader))
        def parse(self, body, *, save_layout_visualization):
            seen.update({
                "body": body,
                "settings": {key: getattr(page_loader, key) for key in (
                    "max_tokens", "temperature", "top_p", "top_k",
                    "repetition_penalty", "max_pixels",
                )},
                "save_layout_visualization": save_layout_visualization,
            })
            return SimpleNamespace(json_result=[[{"label": "text"}],], markdown_result="text")

    gateway._parser = Parser()
    result = gateway._parse(b"page", {"max_tokens": 4096, "temperature": 0.2}, 2_000_000)

    assert seen == {
        "body": b"page",
        "settings": {
            "max_tokens": 4096, "temperature": 0.2, "top_p": 0.00001,
            "top_k": 1, "repetition_penalty": 1.1, "max_pixels": 2_000_000,
        },
        "save_layout_visualization": False,
    }
    assert result == {"json_result": [[{"label": "text"}]], "markdown_result": "text"}
    assert page_loader.max_tokens == 8192 and page_loader.temperature == 0.0
    assert page_loader.max_pixels == 1_003_520


def test_glm_native_gateway_configures_the_official_selfhosted_sdk(monkeypatch):
    import asyncio
    import importlib.util
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    gateway_path = Path(__file__).resolve().parents[2] / "scripts/cloud/glmocr_native_gateway.py"
    spec = importlib.util.spec_from_file_location("glmocr_native_gateway_start_test", gateway_path)
    assert spec is not None and spec.loader is not None
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    captured = {}
    parser = object()
    monkeypatch.setitem(sys.modules, "glmocr", SimpleNamespace(GlmOcr=lambda **kwargs: captured.update(kwargs) or parser))
    monkeypatch.setenv("TABULARIUM_SERVER_API_KEY", "test-key")
    monkeypatch.setenv("TABULARIUM_GLMOCR_VLLM_PORT", "8123")

    asyncio.run(gateway._startup())

    assert captured == {
        "mode": "selfhosted", "api_key": "test-key",
        "ocr_api_host": "127.0.0.1", "ocr_api_port": 8123,
        "layout_device": None,
    }
    assert gateway._parser is parser


def test_mineru_keeps_official_layout_and_full_table_context():
    adapter = model_adapters.get_adapter("mineru2.5")
    assert adapter.official_layout_size == (1036, 1036)
    assert adapter.table_recognition_strategy == "full_crop"


def test_tolerant_items_accepts_category_and_label():
    # Percorso letterale (ast.literal_eval sull'intera risposta): non filtra
    # per chiave, qualunque dict passa.
    literal = "[{'bbox': [1, 2, 3, 4], 'category': 'Table', 'text': 'ciao'}]"
    items = infmod._tolerant_items(literal)
    assert items == [{"bbox": [1, 2, 3, 4], "category": "Table", "text": "ciao"}]

    # Percorso di recupero a blocchi (usato quando l'intera risposta non è una
    # lista valida): richiede "bbox" più "label" O "category".
    truncated = "testo introduttivo {'bbox': [1, 2, 3, 4], 'category': 'Table', 'text': 'ciao'} coda"
    items = infmod._tolerant_items(truncated)
    assert len(items) == 1
    assert items[0]["category"] == "Table"


def test_client_table_text_to_grid_dispatches_on_table_format(monkeypatch):
    otsl_client = infmod.VllmClient(url="http://127.0.0.1:8888/v1")
    assert otsl_client.adapter.capabilities.table_format == "otsl"

    html_client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=model_adapters.get_adapter("dots-ocr")
    )
    assert html_client.adapter.capabilities.table_format == "html"

    calls: list[str] = []
    original = otsl.html_to_otsl

    def spy(source: str) -> str:
        calls.append(source)
        return original(source)

    monkeypatch.setattr(otsl, "html_to_otsl", spy)
    grid = html_client._table_text_to_grid(
        "<table><tr><td>a</td><td>b</td></tr></table>"
    )
    assert calls, "html_to_otsl doveva essere chiamato per un adapter table_format='html'"


def test_html_adapter_keeps_native_otsl_from_paddle():
    client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=model_adapters.get_adapter("paddleocr-vl")
    )
    grid = client._table_text_to_grid("<fcel>VESSEL<lcel><fcel>Fig<nl><fcel>A.E.S.<fcel>Da")
    assert grid["rows"] == 2
    assert grid["cols"] == 3
    assert grid["cells"][0]["text"] == "VESSEL"


def test_modal_streaming_drains_after_complete_list(monkeypatch):
    consumed: list[str] = []
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            events = [
                {"choices": [{"delta": {"content": "[{'bbox': [1, 2, 3, 4], 'label': 'Text'}]"}}]},
                {"choices": [{"delta": {"content": " trailing output that must be drained"}}]},
            ]
            for event in events:
                consumed.append("event")
                yield "data: " + json.dumps(event)
            consumed.append("done")
            yield "data: [DONE]"

    monkeypatch.setattr(infmod.requests, "post", lambda *args, **kwargs: Response())
    client = infmod.VllmClient(url="https://workspace--app.modal.run/v1")
    raw = client._chat(
        Image.new("RGB", (16, 16), "white"),
        "prompt",
        stop_when_complete_list=True,
    )

    assert raw == "[{'bbox': [1, 2, 3, 4], 'label': 'Text'}]"
    assert consumed == ["event", "event", "done"]


def test_chat_forwards_each_accepted_delta_to_live_output(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            for text in ("hello", " world"):
                yield "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})
            yield "data: [DONE]"

    monkeypatch.setattr(infmod.requests, "post", lambda *args, **kwargs: Response())
    output: list[str] = []
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1")
    assert client._chat(Image.new("RGB", (16, 16), "white"), "prompt", on_delta=output.append) == "hello world"
    assert output == ["hello", " world"]


def test_unlimited_end2end_accepts_grounded_markdown_until_server_finish(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            yield "data: " + json.dumps({
                "choices": [{"delta": {"content": "<|det|>text [1,2,300,400]<|/det|>Hello"}}],
            })
            yield "data: [DONE]"

    requests_seen: list[dict] = []

    def post(*_args, **kwargs):
        requests_seen.append(kwargs["json"])
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    client = infmod.VllmClient(
        url="https://workspace--unlimited.modal.run/v1",
        adapter=model_adapters.get_adapter("unlimited-ocr"),
    )
    items = client.end2end(Image.new("RGB", (16, 16), "white"), total_timeout=5)

    assert items[0]["label"] == "text"
    assert items[0]["content"] == "Hello"
    assert requests_seen[0]["skip_special_tokens"] is False
    assert requests_seen[0]["images_config"] == {"image_mode": "gundam"}
    assert requests_seen[0]["max_tokens"] == 8192
    assert requests_seen[0]["messages"][0]["content"][0]["type"] == "text"
    assert requests_seen[0]["messages"][0]["content"][1]["type"] == "image_url"


def test_deepseek_end2end_uses_official_grounded_prompt_and_vllm_sampling(monkeypatch, tmp_path):
    from app import config
    from app.db import init_db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "deepseek-inference.db")
    init_db()

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            text = (
                "<|ref|>title<|/ref|><|det|>[[330, 198, 558, 230]]<|/det|>\n# Register"
            )
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})
            yield "data: [DONE]"

    requests_seen = []
    def post(*_args, **kwargs):
        requests_seen.append(kwargs["json"])
        return Response()
    monkeypatch.setattr(infmod.requests, "post", post)

    client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=model_adapters.get_adapter("deepseek-ocr")
    )
    items = client.end2end(Image.new("RGB", (16, 16), "white"), total_timeout=5)
    payload = requests_seen[0]
    assert items == [{"bbox": [330.0, 198.0, 558.0, 230.0], "label": "Title", "content": "# Register"}]
    assert payload["temperature"] == 0.0
    assert payload["skip_special_tokens"] is False
    assert payload["vllm_xargs"] == {
        "ngram_size": 30, "window_size": 90, "whitelist_token_ids": [128821, 128822]
    }
    assert payload["max_tokens"] == 4096
    assert payload["messages"][0]["content"][1]["text"] == "<|grounding|>Convert the document to markdown."


def test_streaming_complete_list_drains_until_done_for_non_modal(monkeypatch):
    consumed: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            del decode_unicode
            events = [
                {"choices": [{"delta": {"content": "[{'bbox': [1, 2, 3, 4], 'label': 'Text'}]"}}]},
                {"choices": [{"delta": {"content": " trailing output that must be drained"}}]},
            ]
            for event in events:
                consumed.append("event")
                yield "data: " + json.dumps(event)
            consumed.append("done")
            yield "data: [DONE]"

    monkeypatch.setattr(infmod.requests, "post", lambda *args, **kwargs: Response())
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1")
    raw = client._chat(
        Image.new("RGB", (16, 16), "white"),
        "prompt",
        stop_when_complete_list=True,
    )

    assert raw == "[{'bbox': [1, 2, 3, 4], 'label': 'Text'}]"
    assert consumed == ["event", "event", "done"]
