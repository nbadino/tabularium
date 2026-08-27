"""Test OTSL: generazione + round-trip con l'algoritmo ufficiale.

`otsl_to_grid` replica la logica di `core_runner.py::otsl_to_html`; per ogni
griglia generiamo l'OTSL e verifichiamo che la ripersatura sia identica.
"""
from __future__ import annotations

import pytest

from app.services.otsl import grid_to_otsl, normalize_cells, otsl_to_grid


def _g(rows: int, cols: int, cells: list[dict]) -> dict:
    return {"rows": rows, "cols": cols, "cells": cells}


def _roundtrip(grid: dict) -> dict:
    parsed = otsl_to_grid(grid_to_otsl(grid))
    return {
        "rows": parsed["rows"],
        "cols": parsed["cols"],
        "cells": normalize_cells(parsed["cells"]),
    }


def test_single_cell():
    grid = _g(1, 1, [{"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "A"}])
    assert grid_to_otsl(grid) == "<fcel>A"
    assert _roundtrip(grid) == grid


def test_empty_cell():
    grid = _g(1, 2, [
        {"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": ""},
        {"r": 0, "c": 1, "rowspan": 1, "colspan": 1, "text": ""},
    ])
    assert grid_to_otsl(grid) == "<ecel><ecel>"
    assert _roundtrip(grid) == grid


def test_full_row_span():
    grid = _g(1, 4, [{"r": 0, "c": 0, "rowspan": 1, "colspan": 4, "text": "Header"}])
    assert grid_to_otsl(grid) == "<fcel>Header<lcel><lcel><lcel>"
    assert _roundtrip(grid) == grid


def test_mixed_grid():
    grid = _g(
        2,
        4,
        [
            {"r": 0, "c": 0, "rowspan": 1, "colspan": 4, "text": "Header"},
            {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "B1"},
            {"r": 1, "c": 1, "rowspan": 1, "colspan": 3, "text": "B234"},
        ],
    )
    assert _roundtrip(grid) == {
        "rows": 2,
        "cols": 4,
        "cells": normalize_cells(grid["cells"]),
    }


def test_twod_merge():
    """Merge 2D (rowspan+colspan) con celle interne xcel."""
    grid = _g(
        3,
        2,
        [
            {"r": 0, "c": 0, "rowspan": 2, "colspan": 2, "text": "X"},
            {"r": 2, "c": 0, "rowspan": 1, "colspan": 1, "text": "Y"},
            {"r": 2, "c": 1, "rowspan": 1, "colspan": 1, "text": "Z"},
        ],
    )
    otsl_str = grid_to_otsl(grid)
    assert "<ucel><xcel>" in otsl_str
    assert _roundtrip(grid) == {
        "rows": 3,
        "cols": 2,
        "cells": normalize_cells(grid["cells"]),
    }


def test_out_of_bounds_not_allowed():
    grid = _g(2, 2, [{"r": 1, "c": 1, "rowspan": 2, "colspan": 1, "text": "x"}])
    with pytest.raises(ValueError):
        grid_to_otsl(grid)