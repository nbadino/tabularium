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


def test_parse_result_falls_back_to_full_page_markdown():
    result = parse_result({"markdownText": "hello"}, 640, 480)
    assert result == [{"bbox": [0, 0, 640, 480], "label": "Text", "content": "hello"}]


def test_parse_result_does_not_invent_blocks_for_empty_output():
    assert parse_result({}, 640, 480) == []
