"""Where two or more models disagree about the same page.

Pure: values in, values out. The input is one list of blocks per model (the shape the
prefill emits: `label`, `points` [[x1, y1], [x2, y2]] in page pixels, `content`,
optional `grid`); the output says, for every group of blocks the models agree is "the
same block", how much they disagree and why.

- Blocks are matched across models by IoU (>= `MATCH_IOU`), one block per model per
  cluster, best overlaps first. A block left alone that lies mostly inside another
  model's block (>= `CONTAINMENT`) joins that block's cluster as one of its `parts`:
  the models read the area but segmented it differently (`split`).
- A cluster has three channels: `text` (normalised character edit distance, parts
  joined in reading order), `layout` (1 - IoU of each model's union of parts) and
  `label_agree`. Its `score` is the worst of them, with a label mismatch counting
  `LABEL_MISMATCH` and a different segmentation `SPLIT_MISMATCH` (labels are not
  compared then: an advert read as Title + Text is the same area); a block that some
  model did not find scores 1.
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
# Share of a block's own area that must lie inside another model's block for it to
# count as a piece of that block rather than a block the other model missed.
CONTAINMENT = 0.7
# Same area, different segmentation: worth a look ("partly"), not a misreading.
SPLIT_MISMATCH = 0.3
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


def words_distance(a: str | None, b: str | None) -> float:
    """`text_distance` on the words sorted: the same words in another order are the same text.

    For a block one model split: its pieces joined in reading order can interleave two
    columns another model read one after the other, and the character distance then
    counted the order, not the misreadings.
    """
    def bag(value: str | None) -> str:
        return " ".join(sorted(normalize_text(value).split()))

    return text_distance(bag(a), bag(b))


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

    # Second pass: a block still alone that lies mostly inside another model's block
    # is a piece of it. Pieces never join a cluster where their model already has a
    # block matched one-to-one: that would hide a block the other model missed.
    parts: list[dict[str, list[int]]] = [
        {key: [index] for key, index in members.items()} for members in clusters
    ]
    joined: set[tuple[int, str]] = set()

    def containment(inner: list[float], outer: list[float]) -> float:
        area = _area(inner)
        inter = _area([max(inner[0], outer[0]), max(inner[1], outer[1]),
                       min(inner[2], outer[2]), min(inner[3], outer[3])])
        return inter / area if area else 0.0

    for c, members in enumerate(clusters):
        # A block that already received pieces stays where it is.
        if len(members) != 1 or sum(len(v) for v in parts[c].values()) != 1:
            continue
        ((key, index),) = members.items()
        best, best_value = None, CONTAINMENT
        for other in keys:
            if other == key:
                continue
            for j, box in enumerate(boxes[other]):
                target = cluster_of[(other, j)]
                if target == c or len(parts[target].get(other, [])) != 1:
                    continue
                if key in parts[target] and (target, key) not in joined and not all(
                    # The block matched one-to-one is itself a piece of the target.
                    containment(boxes[key][i], box) >= CONTAINMENT
                    and _area(boxes[key][i]) < 0.9 * _area(box)
                    for i in parts[target][key]
                ):
                    continue
                value = containment(boxes[key][index], box)
                if value >= best_value:
                    best, best_value = target, value
        if best is not None:
            parts[best].setdefault(key, []).append(index)
            joined.add((best, key))
            cluster_of[(key, index)] = best
            parts[c] = {}
            clusters[c] = {}

    out = []
    for c, members in enumerate(parts):
        if not members:
            continue
        ordered = {
            key: sorted(members.get(key, []), key=lambda i: (boxes[key][i][1], boxes[key][i][0]))
            for key in keys
        }
        out.append({
            "members": {key: (ordered[key][0] if ordered[key] else None) for key in keys},
            "parts": {key: ordered[key] for key in keys if ordered[key]},
            "split": any(len(indices) > 1 for indices in ordered.values()),
        })
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


def _pair_distance(a: str | None, b: str | None) -> float:
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return 1.0
    return text_distance(a, b)


def annotate_grid(reference: dict, grids: dict[str, dict]) -> dict:
    """The models' readings laid on the user's own grid, for the table editor.

    The user's grid is the reference (it is the one being edited); each model's
    grid is aligned onto it like in `compare_tables`. A cell's `score` is how much
    the models disagree *with each other* (mean pairwise distance, 1 between a
    reading and no reading), not with the user: two models that agree point at
    nothing, whatever the user typed. A cell no model has scores None.
    """
    ref = _dense(reference)
    others = {key: _dense(grid) for key, grid in grids.items()}
    alignments = {key: _align_grids(ref, matrix) for key, matrix in others.items()}
    cells = []
    for cell in reference.get("cells") or []:
        r, c = int(cell.get("r", 0)), int(cell.get("c", 0))
        if not (0 <= r < len(ref) and ref and 0 <= c < len(ref[0])):
            continue
        values: dict[str, str | None] = {}
        for key, matrix in others.items():
            row_map, col_map, _rows, _cols = alignments[key]
            j, b = row_map.get(r), col_map.get(c)
            values[key] = None if j is None or b is None else matrix[j][b]
        readings = list(values.values())
        if all(value is None for value in readings):
            score = None
        else:
            score = _mean_pairwise(readings, _pair_distance)
        cells.append({"r": r, "c": c, "values": values, "score": score})
    extra_rows = [
        {"model": key, "index": j, "texts": list(others[key][j])}
        for key, (_rm, _cm, rows, _cols) in alignments.items()
        for j in rows
    ]
    return {"cells": cells, "extra_rows": extra_rows}


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
        pieces = {key: [outputs[key][i] for i in indices] for key, indices in cluster["parts"].items()}
        missing = [key for key in keys if key not in pieces]
        split = cluster["split"]

        def union_of(found: list[list[float]]) -> list[float]:
            return [
                min(box[0] for box in found), min(box[1] for box in found),
                max(box[2] for box in found), max(box[3] for box in found),
            ]

        model_boxes = {key: union_of([_box(block) for block in blocks]) for key, blocks in pieces.items()}
        union = union_of(list(model_boxes.values()))
        model_labels = {
            key: " + ".join(dict.fromkeys(str(block.get("label") or "") for block in blocks))
            for key, blocks in pieces.items()
        }
        labels = list(model_labels.values())
        label_agree = len(set(labels)) <= 1
        singles = {key: blocks[0] for key, blocks in pieces.items() if len(blocks) == 1}
        grids = {
            key: block["grid"] for key, block in singles.items()
            if block.get("grid") and block["grid"].get("rows")
        }
        table = compare_tables(grids) if len(grids) >= 2 and len(grids) == len(pieces) else None
        if table is not None:
            text = table["score"]
        else:
            texts = [" ".join(str(block.get("content") or "") for block in blocks) for blocks in pieces.values()]
            text = _mean_pairwise(texts, words_distance if split else text_distance)
        layout = _mean_pairwise(list(model_boxes.values()), layout_distance)
        if missing:
            score = 1.0
        elif split:
            score = max(text, layout, SPLIT_MISMATCH)
        else:
            score = max(text, layout, 0.0 if label_agree else LABEL_MISMATCH)
        clusters.append({
            "id": index,
            "members": cluster["members"],
            "parts": cluster["parts"],
            "split": split,
            "label": max(set(labels), key=labels.count) if labels else "",
            "labels": model_labels,
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
