"""Where two or more models disagree about the same page.

Pure: values in, values out. The input is one list of blocks per model (the shape the
prefill emits: `label`, `points` [[x1, y1], [x2, y2]] in page pixels, `content`,
optional `grid`); the output says, for every group of blocks the models agree is "the
same block", how much they disagree and why.

- Blocks are matched across models by IoU (>= `MATCH_IOU`), one block per model per
  cluster, best overlaps first.
- A cluster has three channels: `text` (normalised character edit distance), `layout`
  (1 - IoU) and `label_agree`. Its `score` is the worst of them, with a label mismatch
  counting `LABEL_MISMATCH`; a block that some model did not find scores 1.
- Tables are compared on their grids, not on their raw content (one model returns
  HTML, another OTSL): rows are aligned by sequence alignment on the row text, then
  columns on the aligned cells, so one extra row does not shift every later cell.
- Page agreement is 1 - the area-weighted mean cluster score.
"""
from __future__ import annotations

import re
import unicodedata
from itertools import combinations

from rapidfuzz.distance import Levenshtein

MATCH_IOU = 0.5
# Title vs Section-header is a disagreement worth seeing, but not one as strong as two
# different readings of the text: it paints a block "partly", not "disagree".
LABEL_MISMATCH = 0.5
# Cost of leaving a row or column unmatched in the alignment. Two rows that share
# nothing (distance 1) cost the same paired as left apart, so the alignment pairs rows
# whenever they resemble each other at all.
GAP_COST = 0.5

_SPACE = re.compile(r"\s+")
_HEADING = re.compile(r"^#{1,6}\s+")
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")


def normalize_text(value: str | None) -> str:
    """Text as a reader compares it: NFC, single spaces, no markdown decoration."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _EMPHASIS.sub("", text)
    text = "\n".join(_HEADING.sub("", line.strip()) for line in text.splitlines())
    return _SPACE.sub(" ", text).strip()


def text_distance(a: str | None, b: str | None) -> float:
    """Normalised character edit distance in [0, 1] (0 = same text)."""
    left, right = normalize_text(a), normalize_text(b)
    if not left and not right:
        return 0.0
    return Levenshtein.distance(left, right) / max(len(left), len(right))


def _box(block: dict) -> list[float]:
    (x1, y1), (x2, y2) = block["points"][0], block["points"][-1]
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def iou(a: list[float], b: list[float]) -> float:
    inter = _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def layout_distance(a: list[float], b: list[float]) -> float:
    return 1.0 - iou(a, b)


def match_blocks(outputs: dict[str, list[dict]]) -> list[dict]:
    """Group the blocks of every model into clusters of "the same block".

    Each cluster is `{"members": {model: block index or None}}`. Pairs from different
    models are joined best IoU first, and two clusters merge only if no model would
    then have two blocks in one cluster.
    """
    keys = list(outputs)
    boxes = {key: [_box(block) for block in outputs[key]] for key in keys}
    cluster_of: dict[tuple[str, int], int] = {}
    clusters: list[dict[str, int]] = []
    for key in keys:
        for index in range(len(outputs[key])):
            cluster_of[(key, index)] = len(clusters)
            clusters.append({key: index})

    pairs = []
    for left, right in combinations(keys, 2):
        for i, a in enumerate(boxes[left]):
            for j, b in enumerate(boxes[right]):
                value = iou(a, b)
                if value >= MATCH_IOU:
                    pairs.append((value, left, i, right, j))
    pairs.sort(key=lambda pair: -pair[0])
    for _value, left, i, right, j in pairs:
        a, b = cluster_of[(left, i)], cluster_of[(right, j)]
        if a == b or set(clusters[a]) & set(clusters[b]):
            continue
        for key, index in clusters[b].items():
            clusters[a][key] = index
            cluster_of[(key, index)] = a
        clusters[b] = {}

    out = []
    for members in clusters:
        if members:
            out.append({"members": {key: members.get(key) for key in keys}})
    return out


# --- tables -------------------------------------------------------------------


def _dense(grid: dict) -> list[list[str]]:
    """The grid as a rows x cols matrix; positions covered by a span are empty."""
    rows, cols = int(grid.get("rows") or 0), int(grid.get("cols") or 0)
    matrix = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in grid.get("cells") or []:
        r, c = int(cell.get("r", 0)), int(cell.get("c", 0))
        if 0 <= r < rows and 0 <= c < cols:
            matrix[r][c] = str(cell.get("text") or "")
    return matrix


def _align(n: int, m: int, cost) -> list[tuple[int | None, int | None]]:
    """Needleman-Wunsch alignment of two sequences by a pairwise cost in [0, 1]."""
    table = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        table[i][0] = i * GAP_COST
    for j in range(1, m + 1):
        table[0][j] = j * GAP_COST
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            table[i][j] = min(
                table[i - 1][j - 1] + cost(i - 1, j - 1),
                table[i - 1][j] + GAP_COST,
                table[i][j - 1] + GAP_COST,
            )
    path: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        if i and j and table[i][j] == table[i - 1][j - 1] + cost(i - 1, j - 1):
            path.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and table[i][j] == table[i - 1][j] + GAP_COST:
            path.append((i - 1, None))
            i -= 1
        else:
            path.append((None, j - 1))
            j -= 1
    path.reverse()
    return path


def _align_grids(ref: list[list[str]], other: list[list[str]]):
    """Map reference rows/columns to the other grid's (None = no counterpart)."""
    ref_rows = [" ".join(row) for row in ref]
    other_rows = [" ".join(row) for row in other]
    row_path = _align(len(ref), len(other), lambda i, j: text_distance(ref_rows[i], other_rows[j]))
    row_pairs = [(i, j) for i, j in row_path if i is not None and j is not None]

    ref_cols = len(ref[0]) if ref else 0
    other_cols = len(other[0]) if other else 0

    def column_cost(a: int, b: int) -> float:
        if not row_pairs:
            return 1.0
        return sum(text_distance(ref[i][a], other[j][b]) for i, j in row_pairs) / len(row_pairs)

    col_path = _align(ref_cols, other_cols, column_cost)
    row_map = {i: j for i, j in row_path if i is not None}
    col_map = {a: b for a, b in col_path if a is not None}
    extra_rows = [j for i, j in row_path if i is None and j is not None]
    extra_cols = [b for a, b in col_path if a is None and b is not None]
    return row_map, col_map, extra_rows, extra_cols


def compare_tables(grids: dict[str, dict]) -> dict:
    """Cell-by-cell disagreement, laid out on the first model's grid.

    Every reference cell carries each model's value at the aligned position (None when
    that model has no matching row or column) and a score: the mean distance from the
    reference value, 1 for a model without the cell. Rows and columns only another
    model has are listed apart and count as disagreeing cells in the table score.
    """
    keys = list(grids)
    reference = keys[0]
    ref = _dense(grids[reference])
    others = {key: _dense(grids[key]) for key in keys[1:]}
    alignments = {key: _align_grids(ref, matrix) for key, matrix in others.items()}

    cells = []
    for cell in grids[reference].get("cells") or []:
        r, c = int(cell.get("r", 0)), int(cell.get("c", 0))
        if not (0 <= r < len(ref) and ref and 0 <= c < len(ref[0])):
            continue
        values: dict[str, str | None] = {reference: ref[r][c]}
        distances = []
        for key, matrix in others.items():
            row_map, col_map, _rows, _cols = alignments[key]
            j, b = row_map.get(r), col_map.get(c)
            if j is None or b is None:
                values[key] = None
                distances.append(1.0)
            else:
                values[key] = matrix[j][b]
                distances.append(text_distance(ref[r][c], matrix[j][b]))
        cells.append({
            "r": r,
            "c": c,
            "rowspan": int(cell.get("rowspan", 1)),
            "colspan": int(cell.get("colspan", 1)),
            "values": values,
            "score": sum(distances) / len(distances) if distances else 0.0,
        })

    extra_rows, extra_cols = [], []
    extra_cells = 0
    for key, matrix in others.items():
        _row_map, _col_map, rows, cols = alignments[key]
        width = len(matrix[0]) if matrix else 0
        for j in rows:
            extra_rows.append({"model": key, "index": j, "texts": list(matrix[j])})
            extra_cells += width
        for b in cols:
            extra_cols.append({"model": key, "index": b})
            extra_cells += len(matrix) - len(rows)
    total = len(cells) + extra_cells
    score = (sum(cell["score"] for cell in cells) + extra_cells) / total if total else 0.0
    return {
        "reference": reference,
        "rows": len(ref),
        "cols": len(ref[0]) if ref else 0,
        "cells": cells,
        "extra_rows": extra_rows,
        "extra_cols": extra_cols,
        "score": score,
    }


# --- page ---------------------------------------------------------------------


def _mean_pairwise(values: list, distance) -> float:
    pairs = list(combinations(values, 2))
    if not pairs:
        return 0.0
    return sum(distance(a, b) for a, b in pairs) / len(pairs)


def compare_page(outputs: dict[str, list[dict]]) -> dict:
    """Clusters with their disagreement, and the page's overall agreement."""
    keys = list(outputs)
    clusters = []
    for index, cluster in enumerate(match_blocks(outputs)):
        present = {key: outputs[key][i] for key, i in cluster["members"].items() if i is not None}
        missing = [key for key in keys if cluster["members"][key] is None]
        boxes = [_box(block) for block in present.values()]
        union = [
            min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes),
        ]
        labels = [str(block.get("label") or "") for block in present.values()]
        label_agree = len(set(labels)) <= 1
        grids = {
            key: block["grid"] for key, block in present.items()
            if block.get("grid") and block["grid"].get("rows")
        }
        table = compare_tables(grids) if len(grids) >= 2 and len(grids) == len(present) else None
        if table is not None:
            text = table["score"]
        else:
            text = _mean_pairwise([block.get("content") for block in present.values()], text_distance)
        layout = _mean_pairwise(boxes, layout_distance)
        if missing:
            score = 1.0
        else:
            score = max(text, layout, 0.0 if label_agree else LABEL_MISMATCH)
        clusters.append({
            "id": index,
            "members": cluster["members"],
            "label": max(set(labels), key=labels.count) if labels else "",
            "labels": {key: str(block.get("label") or "") for key, block in present.items()},
            "bbox": union,
            "text": text,
            "layout": layout,
            "label_agree": label_agree,
            "missing": missing,
            "score": score,
            "table": table,
        })
    clusters.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
    weight = sum(_area(c["bbox"]) for c in clusters)
    if weight:
        disagreement = sum(_area(c["bbox"]) * c["score"] for c in clusters) / weight
    else:
        disagreement = 0.0
    return {"models": keys, "agreement": 1.0 - disagreement, "clusters": clusters}
