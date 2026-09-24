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
        "paragraph_title": "Title",
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
