"""Consensus metric: where two or more models disagree on the same page.

The metric is pure (`app/domain/consensus.py`): blocks are matched across models by
IoU, each match gets a text distance (character edit distance), a layout distance
(1 - IoU) and a label check, and tables are aligned row by row and column by column
before their cells are compared.
"""
from __future__ import annotations

import pytest

from app.domain import consensus as cs


def _block(label, box, content="", grid=None):
    x1, y1, x2, y2 = box
    return {"label": label, "points": [[x1, y1], [x2, y2]], "content": content, "grid": grid}


def _grid(rows):
    cells = [
        {"r": r, "c": c, "rowspan": 1, "colspan": 1, "text": text}
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    return {"rows": len(rows), "cols": max(len(row) for row in rows), "cells": cells}


# --- text and layout distances ---------------------------------------------


def test_text_distance_is_normalised_character_edit_distance():
    assert cs.text_distance("Rossi", "Russo") == pytest.approx(2 / 5)
    assert cs.text_distance("Genova", "Genova") == 0.0
    assert cs.text_distance("", "") == 0.0
    assert cs.text_distance("abc", "") == 1.0


def test_text_distance_ignores_whitespace_and_markdown_emphasis():
    # Models disagree on line breaks and on markdown decoration far more often
    # than on the words: neither is a disagreement about what the page says.
    assert cs.text_distance("Porto  di\nGenova", "Porto di Genova") == 0.0
    assert cs.text_distance("## **Arrivi**", "Arrivi") == 0.0


def test_layout_distance_is_one_minus_iou():
    assert cs.layout_distance([0, 0, 10, 10], [0, 0, 10, 10]) == 0.0
    assert cs.layout_distance([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 - 50 / 150)
    assert cs.layout_distance([0, 0, 10, 10], [20, 20, 30, 30]) == 1.0


# --- block matching ---------------------------------------------------------


def test_blocks_are_matched_one_to_one_by_best_iou():
    outputs = {
        "A": [_block("Title", (0, 0, 100, 20), "Arrivi"), _block("Text", (0, 30, 100, 80), "x")],
        "B": [_block("Text", (2, 32, 100, 80), "x"), _block("Title", (0, 0, 98, 20), "Arrivi")],
    }
    clusters = cs.match_blocks(outputs)
    members = sorted((c["members"]["A"], c["members"]["B"]) for c in clusters)
    assert members == [(0, 1), (1, 0)]


def test_a_block_only_one_model_found_is_its_own_cluster():
    outputs = {
        "A": [_block("Text", (0, 0, 100, 20), "a")],
        "B": [_block("Text", (0, 0, 100, 20), "a"), _block("Footnote", (0, 500, 100, 520), "n")],
    }
    clusters = cs.match_blocks(outputs)
    assert len(clusters) == 2
    lonely = next(c for c in clusters if c["members"]["A"] is None)
    assert lonely["members"]["B"] == 1


def test_three_models_never_put_two_blocks_of_one_model_in_a_cluster():
    outputs = {
        "A": [_block("Text", (0, 0, 100, 100))],
        "B": [_block("Text", (0, 0, 100, 50)), _block("Text", (0, 50, 100, 100))],
        "C": [_block("Text", (0, 0, 100, 98))],
    }
    for cluster in cs.match_blocks(outputs):
        assert set(cluster["members"]) == {"A", "B", "C"}
    joined = [c for c in cs.match_blocks(outputs) if c["members"]["A"] is not None]
    assert len(joined) == 1 and joined[0]["members"]["C"] == 0


# --- page comparison --------------------------------------------------------


def test_identical_outputs_agree_completely():
    blocks = [_block("Title", (0, 0, 100, 20), "Arrivi"), _block("Text", (0, 30, 100, 80), "Navi")]
    page = cs.compare_page({"A": blocks, "B": [dict(b) for b in blocks]})
    assert page["agreement"] == pytest.approx(1.0)
    assert all(c["score"] == 0.0 for c in page["clusters"])


def test_block_score_takes_the_worst_of_text_layout_and_label():
    page = cs.compare_page({
        "A": [_block("Text", (0, 0, 100, 100), "Rossi")],
        "B": [_block("Text", (0, 0, 100, 100), "Russo")],
    })
    (cluster,) = page["clusters"]
    assert cluster["text"] == pytest.approx(0.4)
    assert cluster["layout"] == 0.0
    assert cluster["label_agree"] is True
    assert cluster["score"] == pytest.approx(0.4)

    shifted = cs.compare_page({
        "A": [_block("Text", (0, 0, 100, 100), "Navi")],
        "B": [_block("Text", (0, 0, 100, 60), "Navi")],
    })["clusters"][0]
    assert shifted["text"] == 0.0
    assert shifted["score"] == pytest.approx(0.4)  # 1 - IoU = 1 - 0.6

    relabelled = cs.compare_page({
        "A": [_block("Title", (0, 0, 100, 100), "Navi")],
        "B": [_block("Section-header", (0, 0, 100, 100), "Navi")],
    })["clusters"][0]
    assert relabelled["label_agree"] is False
    assert relabelled["score"] == pytest.approx(cs.LABEL_MISMATCH)


def test_a_block_missing_from_a_model_scores_one():
    page = cs.compare_page({
        "A": [_block("Text", (0, 0, 100, 100), "a")],
        "B": [],
    })
    (cluster,) = page["clusters"]
    assert cluster["missing"] == ["B"]
    assert cluster["score"] == 1.0
    assert page["agreement"] == 0.0


def test_page_agreement_is_weighted_by_block_area():
    page = cs.compare_page({
        "A": [_block("Text", (0, 0, 100, 90), "same"), _block("Text", (0, 90, 100, 100), "x")],
        "B": [_block("Text", (0, 0, 100, 90), "same")],
    })
    # 90% of the area agrees, the missing 10% scores 1.
    assert page["agreement"] == pytest.approx(0.9)


# --- tables -----------------------------------------------------------------


def test_identical_tables_have_zero_cell_scores():
    grid = _grid([["Nave", "Da"], ["Aurora", "Genova"]])
    table = cs.compare_tables({"A": grid, "B": grid})
    assert table["score"] == 0.0
    assert all(cell["score"] == 0.0 for cell in table["cells"])


def test_a_differing_cell_is_scored_by_character_distance():
    table = cs.compare_tables({
        "A": _grid([["Nave", "Capitano"], ["Aurora", "Rossi"]]),
        "B": _grid([["Nave", "Capitano"], ["Aurora", "Russo"]]),
    })
    by_pos = {(c["r"], c["c"]): c for c in table["cells"]}
    assert by_pos[(1, 1)]["score"] == pytest.approx(0.4)
    assert by_pos[(1, 1)]["values"] == {"A": "Rossi", "B": "Russo"}
    assert by_pos[(0, 0)]["score"] == 0.0


def test_an_extra_row_is_aligned_not_shifted():
    """A model that reads one more row must not make every later row disagree."""
    a = _grid([["Nave", "Da"], ["Aurora", "Genova"], ["Bruno", "Napoli"], ["Etna", "Catania"]])
    b = _grid([["Nave", "Da"], ["Aurora", "Genova"], ["Lampo", "Bari"], ["Bruno", "Napoli"], ["Etna", "Catania"]])
    table = cs.compare_tables({"A": a, "B": b})
    by_pos = {(c["r"], c["c"]): c for c in table["cells"]}
    assert by_pos[(2, 0)]["values"]["B"] == "Bruno"
    assert by_pos[(3, 1)]["values"]["B"] == "Catania"
    assert all(cell["score"] == 0.0 for cell in table["cells"])
    assert table["extra_rows"] == [{"model": "B", "index": 2, "texts": ["Lampo", "Bari"]}]
    assert table["score"] > 0.0


def test_a_missing_column_scores_one_for_that_column():
    a = _grid([["Nave", "Capitano", "Da"], ["Aurora", "Rossi", "Genova"]])
    b = _grid([["Nave", "Da"], ["Aurora", "Genova"]])
    table = cs.compare_tables({"A": a, "B": b})
    by_pos = {(c["r"], c["c"]): c for c in table["cells"]}
    assert by_pos[(0, 1)]["score"] == 1.0 and by_pos[(0, 1)]["values"]["B"] is None
    assert by_pos[(1, 2)]["values"]["B"] == "Genova" and by_pos[(1, 2)]["score"] == 0.0


def test_table_cluster_uses_the_cell_comparison_for_its_text_channel():
    grid_a = _grid([["Nave", "Da"], ["Aurora", "Genova"]])
    grid_b = _grid([["Nave", "Da"], ["Aurora", "Genovo"]])
    page = cs.compare_page({
        # The raw content differs in format (HTML vs OTSL); the grids are what count.
        "A": [_block("Table", (0, 0, 100, 100), "<table>…</table>", grid_a)],
        "B": [_block("Table", (0, 0, 100, 100), "<fcel>…", grid_b)],
    })
    (cluster,) = page["clusters"]
    assert cluster["table"] is not None
    assert cluster["text"] == pytest.approx(cluster["table"]["score"])
    assert 0.0 < cluster["text"] < 0.2


# --- different segmentation -------------------------------------------------


def test_pieces_inside_another_models_block_join_its_cluster():
    """One model reads an advert as one block, the other as three pieces.

    Both read that area: the pieces are not "missing", they are a different
    segmentation, scored as a layout disagreement with the text compared joined.
    """
    page = cs.compare_page({
        "A": [
            _block("Title", (0, 0, 100, 20), "JAMES POWER"),
            _block("Text", (0, 22, 100, 40), "Chartering agents"),
            _block("Text", (0, 42, 100, 60), "London"),
        ],
        "B": [_block("Advertisement", (0, 0, 100, 60), "JAMES POWER Chartering agents London")],
    })
    (cluster,) = page["clusters"]
    assert cluster["missing"] == []
    assert cluster["parts"] == {"A": [0, 1, 2], "B": [0]}
    assert cluster["split"] is True
    assert cluster["text"] == 0.0
    assert cluster["score"] == pytest.approx(cs.SPLIT_MISMATCH)


def test_the_pieces_of_a_split_block_are_compared_whatever_their_order():
    """Two columns of an advert, pieced in reading order, interleave against one block.

    The same words in another order are no disagreement; a misread word still is.
    """
    page = cs.compare_page({
        "A": [
            _block("Title", (0, 0, 50, 20), "JAMES POWER"),
            _block("Title", (50, 0, 100, 20), "CHARTERING AGENTS"),
            _block("Text", (0, 20, 50, 60), "158 Leadenhall Street"),
            _block("Text", (50, 20, 100, 60), "Particulars free"),
        ],
        "B": [_block("Advertisement", (0, 0, 100, 60), "JAMES POWER 158 Leadenhall Street CHARTERING AGENTS Particulars free")],
    })
    (cluster,) = page["clusters"]
    assert cluster["split"] is True and cluster["text"] == 0.0
    misread = cs.compare_page({
        "A": [_block("Text", (0, 0, 50, 60), "158 Leadenhall Street"), _block("Text", (50, 0, 100, 60), "Particulars free")],
        "B": [_block("Advertisement", (0, 0, 100, 60), "158 Leadenhall Strect Particulars free")],
    })
    assert misread["clusters"][0]["text"] > 0.0


def test_a_block_barely_touching_another_is_still_missing():
    page = cs.compare_page({
        "A": [_block("Text", (0, 0, 100, 100), "a"), _block("Note", (90, 90, 200, 200), "n")],
        "B": [_block("Text", (0, 0, 100, 100), "a")],
    })
    note = next(c for c in page["clusters"] if c["label"] == "Note")
    assert note["missing"] == ["B"] and note["score"] == 1.0


def test_a_block_that_received_pieces_keeps_them():
    # B's header contains A's two pieces, and is itself inside A's large frame:
    # it must not wander into the frame's cluster and drop the pieces.
    page = cs.compare_page({
        "A": [_block("Text", (0, 0, 50, 10), "x"), _block("Text", (50, 0, 100, 10), "y")],
        "B": [_block("Page-header", (0, 0, 100, 10), "x y")],
    })
    (cluster,) = page["clusters"]
    assert cluster["parts"] == {"A": [0, 1], "B": [0]}


# --- the user's own table as reference --------------------------------------


def test_models_are_aligned_onto_the_users_grid_and_scored_among_themselves():
    mine = _grid([["Nave", "Da"], ["Aurora", "Genva"], ["Etna", "Catania"], ["(nota mia)", ""]])
    a = _grid([["Nave", "Da"], ["Aurora", "Genova"], ["Etna", "Catania"]])
    b = _grid([["Nave", "Da"], ["Aurora", "Genova"], ["Etna", "Catanzaro"]])
    out = cs.annotate_grid(mine, {"A": a, "B": b})
    by_pos = {(c["r"], c["c"]): c for c in out["cells"]}
    # The models agree with each other: no colour, even though my reading differs.
    assert by_pos[(1, 1)]["values"] == {"A": "Genova", "B": "Genova"}
    assert by_pos[(1, 1)]["score"] == 0.0
    # The models disagree: that is the cell to look at.
    assert by_pos[(2, 1)]["score"] > 0.3
    # A row only I have: no model reading, nothing to score.
    assert by_pos[(3, 0)]["values"] == {"A": None, "B": None}
    assert by_pos[(3, 0)]["score"] is None


def test_a_cell_only_one_model_has_scores_one():
    mine = _grid([["Nave", "Da", "Note"]])
    a = _grid([["Nave", "Da", "Note"]])
    b = _grid([["Nave", "Da"]])
    by_pos = {(c["r"], c["c"]): c for c in cs.annotate_grid(mine, {"A": a, "B": b})["cells"]}
    assert by_pos[(0, 2)]["values"] == {"A": "Note", "B": None}
    assert by_pos[(0, 2)]["score"] == 1.0
