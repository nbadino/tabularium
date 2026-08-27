"""Generazione e parsing OTSL (formato tabella di MonkeyOCRv2).

Formato ufficiale (derivato da `core_runner.py::otsl_to_html`): token SENZA
tag di chiusura; il contenuto di `<fcel>` termina al token successivo.
  <fcel>text  cella piena (text fino al prossimo token)
  <ecel>      cella vuota valida
  <lcel>      estende colspan della cella valida a sinistra
  <ucel>      estende rowspan della cella valida sopra
  <xcel>      cella invalida (interiore di un merge 2D)
  <nl>        separatore di riga

`otsl_to_grid` è un port fedele dell'algoritmo ufficiale `otsl_to_html`
(usato come oracle nei test: genero OTSL, lo riperso, confronto le griglie).
"""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

# --- modelli griglia ----------------------------------------------------------
# griglia = {"rows": R, "cols": C, "cells": [{"r","c","rowspan","colspan","text"}]}
#           phantom_cols opzionale: colonne con filetto sbiadito ("fantasma").


def _start_text(cells: list[dict], r: int, c: int) -> str | None:
    for cell in cells:
        if cell["r"] == r and cell["c"] == c:
            return cell.get("text", "") or ""
    return None


def grid_to_otsl(grid: dict) -> str:
    """Codifica una griglia (celle logiche con span) in OTSL."""
    rows = int(grid.get("rows", 0))
    cols = int(grid.get("cols", 0))
    if rows <= 0 or cols <= 0 or rows * cols > 4096:
        raise ValueError(f"griglia non valida: {rows}x{cols}")

    meta: list[list[str | None]] = [[None] * cols for _ in range(rows)]
    for cell in grid.get("cells", []):
        r = int(cell.get("r", 0))
        c = int(cell.get("c", 0))
        rs = max(1, int(cell.get("rowspan", 1)))
        cs = max(1, int(cell.get("colspan", 1)))
        if r < 0 or c < 0 or r + rs > rows or c + cs > cols:
            raise ValueError(f"cella fuori griglia: {cell}")
        for rr in range(r, r + rs):
            for cc in range(c, c + cs):
                if rr == r and cc == c:
                    meta[rr][cc] = "start"
                elif rr == r:
                    meta[rr][cc] = "lcel"
                elif cc == c:
                    meta[rr][cc] = "ucel"
                else:
                    meta[rr][cc] = "xcel"

    rows_tokens: list[str] = []
    for rr in range(rows):
        tokens: list[str] = []
        for cc in range(cols):
            kind = meta[rr][cc]
            # NB: OTSL non ha tag di chiusura: il contenuto di <fcel> termina
            # al token successivo (coerente con otsl_to_html ufficiale).
            if kind == "start":
                text = _start_text(grid.get("cells", []), rr, cc)
                if text:
                    tokens.append(f"<fcel>{text}")
                else:
                    tokens.append("<ecel>")
            elif kind == "lcel":
                tokens.append("<lcel>")
            elif kind == "ucel":
                tokens.append("<ucel>")
            elif kind == "xcel":
                tokens.append("<xcel>")
            else:
                tokens.append("<ecel>")
        rows_tokens.append("".join(tokens))
    return "<nl>".join(rows_tokens)


def otsl_to_grid(otsl: str) -> dict:
    """Ripersa un OTSL nella griglia logica (port dell'algoritmo ufficiale)."""
    grid: list[list[dict | None]] = []

    for row_str in otsl.split("<nl>"):
        r_idx = len(grid)
        grid.append([])
        col_idx = 0
        import re

        for tag, content in re.findall(r"<([a-z]+)>(.*?)(?=<[a-z]+>|$)", row_str, flags=re.DOTALL):
            while len(grid[r_idx]) <= col_idx:
                grid[r_idx].append(None)
            cell = grid[r_idx][col_idx]
            if cell is not None:  # posizione già occupata
                col_idx += 1
                continue
            if tag in ("fcel", "ecel"):
                text = content.strip() if tag == "fcel" else ""
                grid[r_idx][col_idx] = {
                    "valid": True,
                    "text": text,
                    "rowspan": 1,
                    "colspan": 1,
                }
            elif tag == "lcel":
                found = False
                for sc in range(col_idx - 1, -1, -1):
                    c = grid[r_idx][sc]
                    if c is not None and c.get("valid"):
                        c["colspan"] += 1
                        found = True
                        break
                grid[r_idx][col_idx] = {"valid": False, "kind": "lcel"} if found else {
                    "valid": True, "text": "", "rowspan": 1, "colspan": 1
                }
            elif tag == "ucel":
                found = False
                for sr in range(r_idx - 1, -1, -1):
                    if len(grid[sr]) > col_idx:
                        c = grid[sr][col_idx]
                        if c is not None and c.get("valid"):
                            c["rowspan"] += 1
                            found = True
                            break
                grid[r_idx][col_idx] = {"valid": False, "kind": "ucel"} if found else {
                    "valid": True, "text": "", "rowspan": 1, "colspan": 1
                }
            elif tag == "xcel":
                grid[r_idx][col_idx] = {"valid": False, "kind": "xcel"}
            col_idx += 1

    rows = len(grid)
    cols = max((len(row) for row in grid), default=0)
    cells: list[dict] = []
    for r in range(rows):
        for c in range(cols):
            cell = grid[r][c] if c < len(grid[r]) else None
            if cell is not None and cell.get("valid"):
                cells.append(
                    {
                        "r": r,
                        "c": c,
                        "rowspan": cell["rowspan"],
                        "colspan": cell["colspan"],
                        "text": cell.get("text", ""),
                    }
                )
    return {"rows": rows, "cols": cols, "cells": cells}


def normalize_cells(cells: list[dict]) -> list[dict]:
    """Celle normalizzate (ordinate per r,c) per confronti deterministici."""
    return sorted(
        (
            {
                "r": int(c.get("r", 0)),
                "c": int(c.get("c", 0)),
                "rowspan": int(c.get("rowspan", 1)),
                "colspan": int(c.get("colspan", 1)),
                "text": c.get("text", ""),
            }
            for c in cells
        ),
        key=lambda c: (c["r"], c["c"]),
    )


class _HtmlTableParser(HTMLParser):
    """Parser stdlib per l'HTML tabellare prodotto da END2END."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.table_depth = 0
        self.rows: list[list[dict]] = []
        self.current_row: list[dict] | None = None
        self.current_cell: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self.table_depth += 1
            return
        if self.table_depth != 1:
            return
        if tag == "tr":
            self.current_row = []
            self.rows.append(self.current_row)
        elif tag in {"td", "th"} and self.current_row is not None:
            values = {key.lower(): value for key, value in attrs}
            self.current_cell = {
                "text_parts": [],
                "rowspan": values.get("rowspan", "1"),
                "colspan": values.get("colspan", "1"),
            }
            self.current_row.append(self.current_cell)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"}:
            self.current_cell = None
        elif tag == "tr":
            self.current_row = None
        elif tag == "table":
            self.table_depth = max(0, self.table_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.table_depth == 1 and self.current_cell is not None:
            self.current_cell["text_parts"].append(data)


def html_to_otsl(source: str) -> str:
    """Converte il primo `<table>` HTML in OTSL, preservando span e testo."""
    if not source or "<table" not in source.lower():
        return ""
    parser = _HtmlTableParser()
    try:
        parser.feed(source)
        parser.close()
    except Exception:  # noqa: BLE001
        return ""
    if not parser.rows:
        return ""

    pending: dict[int, tuple[int, str]] = {}
    output: list[str] = []
    for row in parser.rows:
        tokens: list[str] = []
        col = 0
        index = 0
        while index < len(row) or any(left > 0 for left, _ in pending.values()):
            span = pending.get(col)
            if span is not None and span[0] > 0:
                left, marker = span
                tokens.append(f"<{marker}>")
                if left == 1:
                    del pending[col]
                else:
                    pending[col] = (left - 1, marker)
                col += 1
                continue
            if index >= len(row):
                later = [key for key, (left, _) in pending.items() if key > col and left > 0]
                if later:
                    col += 1
                    continue
                break
            cell = row[index]
            text = "".join(cell["text_parts"])
            tokens.append(f"<fcel>{text}" if text else "<ecel>")
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
            except (TypeError, ValueError):
                colspan = 1
            try:
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except (TypeError, ValueError):
                rowspan = 1
            if rowspan > 1:
                pending[col] = (rowspan - 1, "ucel")
            col += 1
            for _ in range(1, colspan):
                tokens.append("<lcel>")
                if rowspan > 1:
                    pending[col] = (rowspan - 1, "xcel")
                col += 1
            index += 1
        output.append("".join(tokens))
    return "<nl>".join(output)
