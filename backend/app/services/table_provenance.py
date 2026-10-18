"""Provenienza delle celle, anche per le griglie precedenti ai metadati espliciti."""
from __future__ import annotations


def with_cell_provenance(grid: dict, prefill_source: str | None, *, generated: bool = False) -> dict:
    if not prefill_source:
        return grid
    source = "model" if prefill_source.startswith("model:") else "ocr"
    cells = []
    for cell in grid.get("cells", []):
        default_source = cell.get("source", source)
        cells.append({
            **cell,
            "source": source if generated else default_source,
            "verified": False if generated else cell.get("verified", default_source == "manual"),
        })
    return {**grid, "cells": cells}
