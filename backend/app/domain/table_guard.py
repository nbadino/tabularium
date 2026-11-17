"""Structure guard for a model's table output.

A fine-tuned OCR model can read every cell of a register correctly and still
break the page's column structure in two mechanical ways (gold evaluation of
Xiaomi-OCR-0 LoRA on Lloyd's registers, `docs/spec/71-evaluation.md`):

* **Split column.** A broken printed header ("Gr ss" for "Gross") makes the
  model emit two header cells and one extra column that stays empty in the
  body. Every cell to its right is then one column off for any consumer.
* **Row shift.** In one row the model inserts an empty cell where its
  neighbours are filled (often while gluing the flag to the vessel name, or
  leaving 'built' empty while the 2-digit year lands in 'gross'). The row's
  remaining values slide by one column.

`guard_grid` repairs both, and only when the repair is provably better:

* (a) a column is merged into its neighbour only when the header shows it as a
  duplicate of that neighbour (`_same`) or as a split of the one declared
  field the header is missing, and the merge loses nothing (no body row has
  text in both). A column with an *empty* header is never dropped: it may be a
  phantom column, which the annotation conventions keep on purpose.
* (b) a row is rearranged only by content-preserving moves (one empty cell
  moved, or a trailing token split off into the next column while an empty
  cell further right is consumed), only when the declared column types say the
  row fits strictly better afterwards, and only when one best repair exists.
  Without declared column types (b) does nothing: types inferred from the same
  table cannot see a fault the model repeats on every row.

Column types are not invented here: they come from the domain profile
(`conf/profiles/*.json`, key `table_guard.columns`), which knows its corpus.
Every repair is returned in `structure_repairs`, never applied silently. The
guard runs on model output only, never on a grid a person saved.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

# Empty for the guard: blank, leader dots, dashes ("——" is an empty cell).
_NIL = re.compile(r"^[\s.·…‥\-–—:_]*$")
_NUMERIC = re.compile(r"^[\d\s.,'½¼¾/-]+$")
# Header split: the two fragments must spell the missing field this well.
SPLIT_HEADER_MIN_RATIO = 0.6
# A merged column may carry text in at most this share of body rows.
MERGE_MAX_FILLED_SHARE = 0.1
MAX_HEADER_ROWS = 3


@dataclass(frozen=True)
class ColumnType:
    """What one declared column of a table accepts (from a domain profile)."""

    field: str
    headers: tuple[str, ...]
    pattern: re.Pattern | None = None
    values: frozenset[str] = frozenset()

    def fits(self, text: str) -> bool:
        value = (text or "").strip()
        if is_nil(value):
            return True
        if value in self.values:
            return True
        return bool(self.pattern is not None and self.pattern.search(value))


def is_nil(text: str | None) -> bool:
    return text is None or bool(_NIL.match(text))


def parse_columns(spec: list[dict] | None) -> tuple[ColumnType, ...]:
    """Column types declared by a profile. Raises ValueError on a bad entry."""
    out: list[ColumnType] = []
    for item in spec or []:
        field = str(item.get("field") or "").strip()
        headers = tuple(_norm(h) for h in item.get("headers") or [] if _norm(h))
        if not field or not headers:
            raise ValueError(f"table_guard column needs a field and headers: {item}")
        pattern = re.compile(item["pattern"]) if item.get("pattern") else None
        values = frozenset(str(v) for v in item.get("values") or [])
        if pattern is None and not values:
            raise ValueError(f"table_guard column {field} needs a pattern or values")
        out.append(ColumnType(field, headers, pattern, values))
    if len({c.field for c in out}) != len(out):
        raise ValueError("table_guard columns repeat a field")
    return tuple(out)


def _norm(text: str | None) -> str:
    return re.sub(r"[^0-9a-z]+", " ", (text or "").lower()).strip()


def _same(a: str, b: str) -> bool:
    ka, kb = (re.sub(r"[^0-9a-z]", "", (t or "").lower()) for t in (a, b))
    return bool(ka and kb) and (ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.8)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", "")).ratio()


# --- grid access ----------------------------------------------------------------


def _covering(cells: list[dict], r: int, c: int) -> dict | None:
    for cell in cells:
        if cell["r"] <= r < cell["r"] + cell.get("rowspan", 1) and cell["c"] <= c < cell["c"] + cell.get("colspan", 1):
            return cell
    return None


def _column_header(cells: list[dict], h: int, c: int) -> list[str]:
    """Header texts above column c, top to bottom, each logical cell once."""
    seen: list[int] = []
    texts: list[str] = []
    for r in range(h):
        cell = _covering(cells, r, c)
        if cell is not None and id(cell) not in seen:
            seen.append(id(cell))
            if (cell.get("text") or "").strip():
                texts.append(cell["text"].strip())
    return texts


def _field_of(text: str, columns: tuple[ColumnType, ...]) -> ColumnType | None:
    words = set(_norm(text).split())
    hits = [col for col in columns if any(set(alias.split()) <= words for alias in col.headers)]
    return hits[0] if len(hits) == 1 else None


def _header_rows(grid: dict, columns: tuple[ColumnType, ...]) -> int:
    declared = int(grid.get("header_rows") or 0)
    if declared:
        return declared
    rows, cells = int(grid.get("rows") or 0), grid.get("cells") or []
    if columns:
        h = 0
        for r in range(min(MAX_HEADER_ROWS, rows - 1)):
            hits = sum(1 for cell in cells if cell["r"] == r and _field_of(cell.get("text") or "", columns))
            if hits < 2:
                break
            h = r + 1
        if h:
            return h
    # No profile (or a table it does not describe): the first row is the header when it holds words, not numbers.
    first = [(cell.get("text") or "").strip() for cell in cells if cell["r"] == 0]
    filled = [t for t in first if not is_nil(t)]
    return 1 if rows > 1 and len(filled) >= 2 and not any(_NUMERIC.match(t) for t in filled) else 0


def _map_columns(grid: dict, h: int, columns: tuple[ColumnType, ...]) -> list[ColumnType | None]:
    """Declared type of each column, read from its header (leaf row first)."""
    cols, cells = int(grid["cols"]), grid["cells"]
    found: list[ColumnType | None] = []
    for c in range(cols):
        hit = None
        for text in reversed(_column_header(cells, h, c)):
            hit = _field_of(text, columns)
            if hit is not None:
                break
        found.append(hit)
    # A field claimed by two columns says nothing about either.
    counts: dict[str, int] = {}
    for col in found:
        if col is not None:
            counts[col.field] = counts.get(col.field, 0) + 1
    return [col if col is not None and counts[col.field] == 1 else None for col in found]


# --- (a) split or duplicated column --------------------------------------------


def _merge_reason(grid: dict, h: int, columns, mapping, c: int, p: int) -> str | None:
    cells = grid["cells"]
    hc, hp = " ".join(_column_header(cells, h, c)), " ".join(_column_header(cells, h, p))
    leaf_c = _covering(cells, h - 1, c)
    leaf_p = _covering(cells, h - 1, p)
    if leaf_c is None or leaf_p is None or leaf_c is leaf_p or not hc or not hp:
        return None
    if _same(leaf_c.get("text", ""), leaf_p.get("text", "")):
        return "duplicate_header"
    if not columns or mapping[c] is not None or mapping[p] is not None:
        return None
    missing = [col for col in columns if col not in mapping]
    left, right = (leaf_p, leaf_c) if p < c else (leaf_c, leaf_p)
    joined = _norm(left.get("text")) + _norm(right.get("text"))
    for col in missing:
        best = max(_ratio(joined, alias) for alias in col.headers)
        alone = max(_ratio(_norm(cell.get("text")), alias) for alias in col.headers for cell in (leaf_c, leaf_p))
        if best >= SPLIT_HEADER_MIN_RATIO and best > alone:
            return "split_header"
    return None


def _try_merge(grid: dict, h: int, columns, mapping) -> tuple[dict, dict] | None:
    rows, cols, cells = int(grid["rows"]), int(grid["cols"]), grid["cells"]
    body = rows - h
    if h < 1 or body < 1 or cols < 2 or grid.get("row_columns"):
        return None
    for c in range(cols):
        for p in (c - 1, c + 1):
            if not 0 <= p < cols:
                continue
            reason = _merge_reason(grid, h, columns, mapping, c, p)
            if reason is None:
                continue
            moved: list[int] = []
            lossless = True
            for r in range(h, rows):
                a, b = _covering(cells, r, c), _covering(cells, r, p)
                if a is None or b is None or a is b or a.get("colspan", 1) > 1 or a.get("rowspan", 1) > 1:
                    lossless = False
                    break
                if not is_nil(a.get("text")):
                    if not is_nil(b.get("text")):
                        lossless = False
                        break
                    moved.append(r)
            if not lossless or len(moved) > MERGE_MAX_FILLED_SHARE * body:
                continue
            return _drop_column(grid, h, c, p, keep_header=reason == "duplicate_header"), {
                "rule": "column_merged",
                "reason": reason,
                "col": min(c, p),
                "header": [" ".join(_column_header(cells, h, k)) for k in sorted((c, p))],
                "rows": moved,
            }
    return None


def _drop_column(grid: dict, h: int, c: int, p: int, *, keep_header: bool = False) -> dict:
    cells = grid["cells"]
    merged: dict[int, str] = {}  # id(partner cell) -> its text after the merge
    for cell in cells:
        if cell["c"] == c and cell.get("colspan", 1) == 1:
            partner = _covering(cells, cell["r"], p)
            text = (cell.get("text") or "").strip()
            if cell["r"] < h and keep_header:
                continue  # a duplicated header says the same thing once
            if partner is not None and text and not (cell["r"] >= h and is_nil(text)):
                old = merged.get(id(partner), (partner.get("text") or "").strip())
                merged[id(partner)] = " ".join(x for x in ((old, text) if p < c else (text, old)) if x)
    out: list[dict] = []
    for cell in cells:
        start, span = cell["c"], cell.get("colspan", 1)
        if start <= c < start + span:
            if span > 1:
                out.append({**cell, "colspan": span - 1})
            continue
        new_cell = {**cell, "c": start - 1} if start > c else dict(cell)
        if id(cell) in merged:
            new_cell["text"] = merged[id(cell)]
        out.append(new_cell)
    new = {**grid, "cols": int(grid["cols"]) - 1, "cells": sorted(out, key=lambda x: (x["r"], x["c"]))}
    new["phantom_cols"] = [k - (k > c) for k in grid.get("phantom_cols") or [] if k != c]
    vlines = list(grid.get("vlines") or [])
    if len(vlines) == int(grid["cols"]) + 1:
        del vlines[max(c, p)]
        new["vlines"] = vlines
    return new


# --- (b) row shift ----------------------------------------------------------------


def _row_cost(texts: list[str], mapping: list[ColumnType | None]) -> tuple[int, set[int]]:
    """Cells that do not fit their column; also a value glued to its left
    neighbour while its own column is empty or holds something else."""
    bad: set[int] = set()
    for k, (text, col) in enumerate(zip(texts, mapping)):
        if col is not None and not col.fits(text):
            bad.add(k)
    for k in range(len(texts) - 1):
        nxt = mapping[k + 1]
        if nxt is None or not nxt.values or (not is_nil(texts[k + 1]) and nxt.fits(texts[k + 1])):
            continue
        parts = (texts[k] or "").strip().rsplit(None, 1)
        if len(parts) == 2 and parts[1] in nxt.values:
            bad.add(k)
    return len(bad), bad


def _candidates(texts: list[str], mapping: list[ColumnType | None]):
    n = len(texts)
    for j in range(n):
        if not is_nil(texts[j]):
            continue
        rest = texts[:j] + texts[j + 1:]
        for t in range(n):
            if t != j:
                yield "empty_cell_moved", rest[:t] + [""] + rest[t:], t == n - 1
    for k in range(n - 1):
        nxt = mapping[k + 1]
        if nxt is None or not nxt.values:
            continue
        parts = (texts[k] or "").strip().rsplit(None, 1)
        if len(parts) != 2 or parts[1] not in nxt.values:
            continue
        for j in range(k + 1, n):
            if not (texts[j] or "").strip():
                yield "glued_value_split", texts[:k] + parts + texts[k + 1:j] + texts[j + 1:], False


def _repair_row(texts: list[str], mapping: list[ColumnType | None]) -> tuple[str, list[str]] | None:
    before, _ = _row_cost(texts, mapping)
    if before == 0:
        return None
    best: dict[tuple, dict[tuple, str]] = {}
    for rule, new, to_end in _candidates(texts, mapping):
        changed = [k for k in range(len(texts)) if new[k] != texts[k]]
        if not changed:
            continue
        cost, bad = _row_cost(new, mapping)
        if cost >= before or bad & set(changed):
            continue
        key = (cost, not to_end, changed[-1] - changed[0])
        best.setdefault(key, {})[tuple(new)] = rule
    if not best:
        return None
    results = best[min(best)]
    if len(results) != 1:
        return None  # two equally good repairs: the row stays as the model read it
    (new, rule), = results.items()
    return rule, list(new)


def _repair_rows(grid: dict, h: int, mapping) -> tuple[dict, list[dict]]:
    rows, cols, cells = int(grid["rows"]), int(grid["cols"]), grid["cells"]
    by_row: dict[int, list[dict]] = {}
    for cell in cells:
        by_row.setdefault(cell["r"], []).append(cell)
    spanned = {r for cell in cells for r in range(cell["r"] + 1, cell["r"] + cell.get("rowspan", 1))}
    repairs: list[dict] = []
    replace: dict[tuple[int, int], str] = {}
    for r in range(h, rows):
        row = sorted(by_row.get(r, []), key=lambda x: x["c"])
        if r in spanned or len(row) != cols or any(x.get("colspan", 1) > 1 or x.get("rowspan", 1) > 1 for x in row):
            continue
        texts = [x.get("text") or "" for x in row]
        fixed = _repair_row(texts, mapping)
        if fixed is None:
            continue
        rule, new = fixed
        changed = [k for k in range(cols) if new[k] != texts[k]]
        lo, hi = changed[0], changed[-1]
        repairs.append({"rule": rule, "row": r, "col": lo, "before": texts[lo:hi + 1], "after": new[lo:hi + 1]})
        for k in changed:
            replace[(r, k)] = new[k]
    if not replace:
        return grid, []
    out = [{**cell, "text": replace[(cell["r"], cell["c"])]} if (cell["r"], cell["c"]) in replace else cell
           for cell in cells]
    return {**grid, "cells": out}, repairs


# --- entry point ------------------------------------------------------------------


def guard_grid(grid: dict, columns: tuple[ColumnType, ...] = ()) -> dict:
    """The grid with its structure repaired; repairs listed in `structure_repairs`.

    `columns` are the declared column types of the corpus (a domain profile);
    without them only exact header duplicates are merged and rows are left
    alone. A grid that needs no repair is returned unchanged.
    """
    if not grid or not grid.get("rows") or not grid.get("cols") or not grid.get("cells"):
        return grid
    work = {**grid, "cells": [dict(cell) for cell in grid["cells"]]}
    h = _header_rows(work, columns)
    repairs: list[dict] = []
    for _ in range(int(work["cols"])):
        mapping = _map_columns(work, h, columns)
        merged = _try_merge(work, h, columns, mapping)
        if merged is None:
            break
        work, repair = merged
        repairs.append(repair)
    mapping = _map_columns(work, h, columns)
    if sum(1 for col in mapping if col is not None) >= 3:
        work, row_repairs = _repair_rows(work, h, mapping)
        repairs += row_repairs
    if not repairs:
        return grid
    return {**work, "structure_repairs": list(grid.get("structure_repairs") or []) + repairs}
