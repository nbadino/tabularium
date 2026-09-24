import pytest

from app.services.model_adapters import (
    MonkeyOCRv2ParsingAdapter,
    get_adapter,
    list_adapters,
    supported_prefill_modes,
)
from app.services.prefill import native_mode


def test_monkeyocr_adapter_contract():
    adapter = get_adapter("monkeyocrv2-parsing")
    assert isinstance(adapter, MonkeyOCRv2ParsingAdapter)
    assert adapter.capabilities.coordinate_system == "normalized-0-1000"
    assert adapter.prompt_for("table")
    assert adapter.prompt_for("text", "Column") is None
    assert adapter.serialize_target("formula", "x^2") == "$x^2$"
    assert any(item["adapter_id"] == "monkeyocrv2-parsing" for item in list_adapters())


@pytest.mark.parametrize(
    ("adapter_id", "expected"),
    [
        ("monkeyocrv2-parsing", "two_stage"),
        ("mineru2.5", "official"),
        ("teleocr", "official"),
        ("unlimited-ocr", "end2end"),
        ("deepseek-ocr", "end2end"),
        ("dots-ocr", "end2end"),
        ("paddleocr-vl", "official"),
        ("glm-ocr", "official"),
        ("qwen3-vl-8b", "official"),
    ],
)
def test_native_prefill_selects_model_workflow(adapter_id, expected):
    adapter = get_adapter(adapter_id)
    assert native_mode(adapter) == expected
    assert supported_prefill_modes(adapter)["supports_native"]


def test_glm_native_result_uses_vendor_normalized_boxes_and_labels():
    adapter = get_adapter("glm-ocr")
    assert adapter.parse_native_result([[{
        "index": 0, "label": "table", "content": "| Ship | Port |",
        "bbox_2d": [10, 20, 900, 800],
    }, {
        "index": 1, "label": "display_formula", "content": "$x^2$",
        "bbox_2d": [10, 810, 900, 900],
    }, {
        "index": 2, "label": "text", "content": "bad bbox",
        "bbox_2d": [900, 900, 10, 10],
    }]]) == [
        {"bbox": [10, 20, 900, 800], "label": "Table", "content": "| Ship | Port |"},
        {"bbox": [10, 810, 900, 900], "label": "Formula", "content": "$x^2$"},
    ]


def test_qwen_native_html_preserves_official_boxes_and_table_markup():
    adapter = get_adapter("qwen3-vl-8b")
    blocks = adapter.parse_native_result(
        '<html><body><h1 data-bbox="50 10 300 30">Index</h1>'
        '<table data-bbox="10 40 900 800"><tr><th>Ship</th><th>Port</th></tr>'
        '<tr><td>ASTER</td><td>London</td></tr></table></body></html>'
    )
    assert blocks == [
        {"bbox": [50, 10, 300, 30], "label": "Title", "content": "Index"},
        {
            "bbox": [10, 40, 900, 800], "label": "Table",
            "content": '<table data-bbox="10 40 900 800"><tr><th>Ship</th><th>Port</th></tr><tr><td>ASTER</td><td>London</td></tr></table>',
        },
    ]


def test_qwen_document_prompt_is_reported_as_native():
    adapter = get_adapter("qwen3-vl-8b")
    assert supported_prefill_modes(adapter)["supports_native"] is True
    assert native_mode(adapter) == "official"


def test_deepseek_native_workflow_parses_vendor_grounded_markdown():
    adapter = get_adapter("deepseek-ocr")
    assert native_mode(adapter) == "end2end"
    assert adapter.prompt_for("end2end") == "<|grounding|>Convert the document to markdown."
    items = adapter.parse_layout(
        "<|ref|>title<|/ref|><|det|>[[330, 198, 558, 230]]<|/det|>\n# Register\n"
        "<|ref|>table<|/ref|><|det|>[[25, 240, 980, 950]]<|/det|>\n| Ship | Port |"
    )
    assert items == [
        {"bbox": [330.0, 198.0, 558.0, 230.0], "label": "Title", "content": "# Register"},
        {"bbox": [25.0, 240.0, 980.0, 950.0], "label": "Table", "content": "| Ship | Port |"},
    ]


def test_mineru_native_result_converts_official_fractional_boxes():
    adapter = get_adapter("mineru2.5")
    result = adapter.parse_native_result([
        {"type": "table", "bbox": [0.1, 0.2, 0.9, 0.8], "content": "<table></table>"},
        {"type": "equation", "bbox": [0.0, 0.0, 1.0, 1.0], "content": "x^2"},
    ])
    assert result == [
        {"bbox": [100, 200, 900, 800], "label": "Table", "content": "<table></table>"},
        {"bbox": [0, 0, 1000, 1000], "label": "Formula", "content": "x^2"},
    ]
def test_teleocr_native_result_maps_vendor_content_blocks():
    adapter = get_adapter("teleocr")
    items = adapter.parse_native_result([
        {"type": "table", "bbox": [0.1, 0.2, 0.8, 0.2, 0.8, 0.9, 0.1, 0.9], "content": "<fcel>x</fcel>"},
        {"type": "equation_block", "bbox": [0.2, 0.3, 0.4, 0.5], "content": "$x$"},
        {"type": "table", "bbox": [0.9, 0.9, 0.9, 0.9], "content": "invalid"},
    ])
    assert items == [
        {"bbox": [100, 200, 800, 900], "label": "Table", "content": "<fcel>x</fcel>"},
        {"bbox": [200, 300, 400, 500], "label": "Formula", "content": "$x$"},
    ]
