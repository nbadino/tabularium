import pytest

from app.services.model_adapters import (
    MonkeyOCRv2ParsingAdapter,
    get_adapter,
    list_adapters,
    supported_prefill_modes,
)
from app.domain.otsl import html_to_otsl
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
        ("lightonocr-3", "end2end"),
        ("gemini-argon", "end2end"),
        ("surya-ocr-2.5", "end2end"),
        ("xiaomi-ocr-0", "official"),
        ("unlimited-ocr-2", "end2end"),
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


def test_lightonocr_grounding_parses_markers_tables_and_continuations():
    adapter = get_adapter("lightonocr-3")
    assert adapter.prompt_for("end2end") == "grounding"
    with pytest.raises(NotImplementedError):
        adapter.prompt_for("table")
    items = adapter.parse_layout(
        "![title](80,70,920,140)\n# HISTORIC SHIPPING INDEX\n"
        "![page_number](900,20,980,40)\n17,186\n"
        "![table](25,150,980,950)\n<table>\n  <tr><td>Abidjan</td><td>Br</td></tr>\n</table>\n"
        "![text+](510,960,980,990)\nContinued.\n"
        "![image](10,10,60,60)\nAn anchor.\n"
        "![text](900,900,100,950)\nbad box\n"
    )
    assert items == [
        {"bbox": [80, 70, 920, 140], "label": "Title", "content": "HISTORIC SHIPPING INDEX"},
        {"bbox": [900, 20, 980, 40], "label": "Issue-number", "content": "17,186"},
        {
            "bbox": [25, 150, 980, 950], "label": "Table",
            "content": "<table>\n  <tr><td>Abidjan</td><td>Br</td></tr>\n</table>",
        },
        {"bbox": [510, 960, 980, 990], "label": "Text", "content": "Continued."},
        {"bbox": [10, 10, 60, 60], "label": "Picture", "content": ""},
    ]


def test_surya_full_page_decodes_compact_headers_and_merged_cells():
    """Surya 2.5 output, in the compact format its model card and `compact.py` define:
    a block is `<|Label|>` plus four 0-1000 coordinate tokens, `</td></th></tr>` and the
    block `</div>` are left out, and merged cells are `data-kind` parent/ghost runs."""
    adapter = get_adapter("surya-ocr-2.5")
    assert adapter.prompt_for("end2end") == (
        "OCR this image to HTML. Each block is a div with data-label and data-bbox "
        "(x0 y0 x1 y1, normalized 0-1000)."
    )
    assert adapter.request_overrides("end2end") == {"skip_special_tokens": False}
    items = adapter.parse_layout(
        "<|Page-Header|><|71|><|33|><|143|><|45|><p>THE SHIPPING INDEX</p>"
        "<|Section-Header|><|100|><|60|><|900|><|90|><h2>Arrivals</h2>"
        "<|Table|><|100|><|100|><|900|><|400|><table>"
        '<tr><th data-cs="2" data-kind="parent">Vessel<th data-kind="ghost"><th>Port'
        '<tr><td data-rs="2" data-kind="parent">Ada<td>A1<td>Genoa'
        '<tr><td data-kind="ghost"><td>B2<td>Leith</table>'
        "<|Text|><|100|><|420|><|900|><|480|><p>Arrived 3rd,<br/><b>Ada</b> &amp; crew.</p>"
        '<|Image|><|100|><|500|><|500|><|700|><|box|><|110|><|510|><|490|><|690|><img alt="ship"/>'
        "<|Blank-Page|><|0|><|0|><|1000|><|1000|>"
        "<|Text|><|900|><|900|><|100|><|950|><p>bad box</p>"
        "<|Page-Footer|><|400|><|950|><|600|><|970|><p>12</p>"
    )
    assert [(item["label"], item["bbox"]) for item in items] == [
        ("Page-header", [71, 33, 143, 45]),
        ("Section-header", [100, 60, 900, 90]),
        ("Table", [100, 100, 900, 400]),
        ("Text", [100, 420, 900, 480]),
        ("Picture", [100, 500, 500, 700]),
        ("Page-footer", [400, 950, 600, 970]),
    ]
    assert items[0]["content"] == "THE SHIPPING INDEX"
    assert items[3]["content"] == "Arrived 3rd,\nAda & crew."
    assert items[4]["content"] == ""
    assert html_to_otsl(items[2]["content"]) == (
        "<fcel>Vessel<lcel><fcel>Port<nl>"
        "<fcel>Ada<fcel>A1<fcel>Genoa<nl>"
        "<ucel><fcel>B2<fcel>Leith"
    )


def test_surya_also_reads_the_div_form_the_prompt_describes():
    adapter = get_adapter("surya-ocr-2.5")
    items = adapter.parse_layout(
        '<div data-bbox="10 20 990 80" data-label="Caption"><p>Fig. 1</p></div>\n'
        '<div data-bbox="10 100 990 300" data-label="Equation-Block">'
        '<math display="block">x^2</math></div>'
    )
    assert items == [
        {"bbox": [10, 20, 990, 80], "label": "Caption", "content": "Fig. 1"},
        {"bbox": [10, 100, 990, 300], "label": "Formula", "content": "x^2"},
    ]
