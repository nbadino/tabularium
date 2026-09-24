from types import SimpleNamespace
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import benchmark_models


def _adapter(table_format):
    return SimpleNamespace(capabilities=SimpleNamespace(table_format=table_format))


def test_target_accepts_provider_and_model_owned_gateway():
    assert benchmark_models._target(
        "teleocr,http://127.0.0.1:8888/v1,TeleOCR,vast,http://127.0.0.1:8889"
    ) == (
        "teleocr", "http://127.0.0.1:8888/v1", "TeleOCR", "vast",
        "http://127.0.0.1:8889",
    )


def test_table_output_validation_follows_the_model_format():
    assert benchmark_models._valid_table_output(_adapter("otsl"), "<fcel>A</fcel><nl><fcel>B</fcel>")
    assert benchmark_models._valid_table_output(_adapter("html"), "<table><tr><th>A</th></tr></table>")
    assert benchmark_models._valid_table_output(_adapter("markdown"), "| A | B |\n|---|---|\n| 1 | 2 |")
    assert benchmark_models._valid_table_output(_adapter(None), "Ship / Destination")
    assert not benchmark_models._valid_table_output(_adapter("html"), "plain text")
    assert not benchmark_models._valid_table_output(_adapter("markdown"), "plain text")


def test_qwen_table_contract_uses_qwen_html_and_native_document_parser():
    from app.services.model_adapters import get_adapter

    adapter = get_adapter("qwen3-vl-8b")
    assert adapter.capabilities.table_format == "html"
    assert adapter.prompt_for("layout") == "qwenvl html"
    assert benchmark_models._valid_table_output(
        adapter, "<table><tr><td>AEGERAN</td><td>Ropner &amp; Son</td></tr></table>",
    )


def test_native_qwen_benchmark_uses_full_page_document_parser():
    from app.services.model_adapters import get_adapter

    seen = {}
    def qwen_native_page(image):
        seen["size"] = image.size
        return [{"label": "Title"}]

    client = SimpleNamespace(
        adapter=get_adapter("qwen3-vl-8b"),
        provider="local", url="http://127.0.0.1:8892/v1", model="qwen3-vl-8b",
        qwen_native_page=qwen_native_page,
    )
    items = benchmark_models._run_native(client, Image.new("RGB", (100, 200)), 30)
    assert seen["size"] == (100, 200)
    assert items == [{"label": "Title"}]


def test_native_paddle_benchmark_uses_official_layout_and_vlm_pipeline(monkeypatch):
    monkeypatch.setattr("app.services.prefill.native_mode", lambda adapter: "official")
    seen = {}

    def parse_page(image, url, model, width, height, *, vl_rec_backend):
        seen.update(url=url, model=model, size=(width, height), backend=vl_rec_backend)
        return [{"bbox": [10, 20, 50, 100], "label": "Text", "content": "page"}]

    monkeypatch.setattr(benchmark_models.paddle_official, "parse_page", parse_page)
    client = SimpleNamespace(
        adapter=SimpleNamespace(adapter_id="paddleocr-vl", capabilities=SimpleNamespace()),
        provider="vast", url="http://vast/v1", model="PaddleOCR-VL-1.6",
    )
    items = benchmark_models._run_native(client, Image.new("RGB", (100, 200)), 30)
    assert seen == {
        "url": "http://vast/v1", "model": "PaddleOCR-VL-1.6",
        "size": (100, 200), "backend": "vllm-server",
    }
    assert items[0]["bbox"] == [100, 100, 500, 500]


def test_native_mineru_benchmark_uses_the_official_page_client(monkeypatch):
    monkeypatch.setattr("app.services.prefill.native_mode", lambda adapter: "official")
    seen = []

    class Client:
        adapter = SimpleNamespace(adapter_id="mineru2.5", capabilities=SimpleNamespace())

        def mineru_native_page(self, image):
            seen.append(image.size)
            return [{"bbox": [0, 0, 1000, 1000], "label": "Table", "content": "<table/>"}]

    items = benchmark_models._run_native(Client(), Image.new("RGB", (100, 200)), 30)
    assert seen == [(100, 200)]
    assert items[0]["label"] == "Table"


def test_native_two_stage_benchmark_recognizes_regions_on_source_crops(monkeypatch):
    monkeypatch.setattr("app.services.prefill.native_mode", lambda adapter: "two_stage")
    recognized = []

    class Adapter:
        adapter_id = "monkeyocrv2-parsing"

        def prompt_for(self, task, label=None):
            return "recognize" if task == "text" else None

    class Client:
        adapter = Adapter()

        def layout(self, image, *, total_timeout):
            assert image.size == (100, 200)
            return [{"bbox": [100, 200, 500, 600], "label": "Text"}]

        def recognize(self, crop, label, *, total_timeout):
            recognized.append((crop.size, label, total_timeout))
            return "recognized crop"

    items = benchmark_models._run_native(Client(), Image.new("RGB", (100, 200)), 30)
    assert recognized == [((40, 80), "Text", 30)]
    assert items[0]["content"] == "recognized crop"
