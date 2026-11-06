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
- Differences that only the named convention rules explain (dash, dot leaders,
  spacing, ligatures, small caps, case, quotes) are classified `convention` and set
  aside by the distances; every other difference is `digits`, `text` or `missing`
  (`classify_difference`). The raw distances are kept as `raw_score` / `text_raw`.
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


# --- conventions --------------------------------------------------------------
#
# Two readings can differ only in how the same print is written down: an em dash
# typed as a hyphen, dot leaders kept or dropped, `Piræus` or `Piraeus`, a small-cap
# `ᴠ` as `V`. On a Lloyd's register a third of the differing cells are of this kind.
# They are not misreadings, and counting them as such buried the real errors; nor
# are they noise to throw away, since a training set must write them one way. So a
# difference is *classified*: it is a `convention` difference only when the named
# rules below explain all of it, and the rules it needed are listed, so the
# researcher can settle each rule once (`conventions`) instead of cell by cell.

# Applied in this order: leaders before spacing (a leader is dots between spaces),
# small caps and ligatures before case.
CONVENTIONS = ("dash", "quotes", "leaders", "spacing", "ligature", "small_caps", "case")

_DASHES = re.compile(r"[‐‑‒–—―−]")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "„": '"', "«": '"', "»": '"'})
# Two or more dots (or middle dots, or an ellipsis) with optional spaces between.
_LEADERS = re.compile(r"(?:\s*(?:\.|·|…)){2,}\s*|\s*…\s*")
# Whitespace not between two digits.
_SPACING = re.compile(r"(?<!\d)\s+|\s+(?!\d)")
_LIGATURES = str.maketrans({"æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ﬁ": "fi", "ﬂ": "fl", "ß": "ss"})
# Small capitals (and the dotless i OCR uses for a small-cap I) as the capital they print.
_SMALL_CAPS = str.maketrans({
    "ᴀ": "A", "ʙ": "B", "ᴄ": "C", "ᴅ": "D", "ᴇ": "E", "ꜰ": "F", "ɢ": "G", "ʜ": "H", "ɪ": "I", "ı": "I",
    "ᴊ": "J", "ᴋ": "K", "ʟ": "L", "ᴍ": "M", "ɴ": "N", "ᴏ": "O", "ᴘ": "P", "ʀ": "R", "ꜱ": "S", "ᴛ": "T",
    "ᴜ": "U", "ᴠ": "V", "ᴡ": "W", "ʏ": "Y", "ᴢ": "Z",
})


def _convention(kind: str, text: str) -> str:
    if kind == "dash":
        return _DASHES.sub("-", text)
    if kind == "quotes":
        return text.translate(_QUOTES)
    if kind == "leaders":
        return _SPACE.sub(" ", _LEADERS.sub(" ", text)).strip()
    if kind == "spacing":
        # A space between two digits is not spacing: `1523` and `15 23` are two
        # readings of a number (or two numbers), whatever the rule says.
        return _SPACING.sub("", _SPACE.sub(" ", text))
    if kind == "ligature":
        return text.translate(_LIGATURES)
    if kind == "small_caps":
        return text.translate(_SMALL_CAPS)
    if kind == "case":
        return text.casefold()
    raise ValueError(kind)


def canonical_text(value: str | None, kinds: tuple[str, ...] = CONVENTIONS) -> str:
    """`normalize_text` with the convention rules `kinds` applied (all by default)."""
    text = normalize_text(value)
    for kind in CONVENTIONS:
        if kind in kinds:
            text = _convention(kind, text)
    return text


def content_distance(a: str | None, b: str | None) -> float:
    """`text_distance` once the conventions are set aside: what the readings disagree on."""
    left, right = canonical_text(a), canonical_text(b)
    if not left and not right:
        return 0.0
    return Levenshtein.distance(left, right) / max(len(left), len(right))


_DIGITS = re.compile(r"\d+")

# How much a kind of difference matters, for ordering and for combining pairs:
# a number read differently first (a year, a tonnage, a date), then other words,
# then a reading only some models have, then a matter of convention.
DIFFERENCE_KINDS = ("same", "convention", "missing", "text", "digits")
_RANK = {kind: rank for rank, kind in enumerate(DIFFERENCE_KINDS)}


def classify_difference(a: str | None, b: str | None) -> dict:
    """What kind of difference lies between two readings, and the conventions it needs.

    `None` is no reading (a model without that cell). Numbers come first: if the
    digits differ, even because one side is empty, the kind is `digits`. A
    `convention` difference lists only the rules it cannot do without, so a cell
    explained by the dash rule alone does not also count against `spacing`.
    """
    if a is None or b is None:
        if a is None and b is None:
            return {"kind": "same", "conventions": []}
        present = a if b is None else b
        kind = "digits" if _DIGITS.search(normalize_text(present)) else "missing"
        return {"kind": kind, "conventions": []}
    if normalize_text(a) == normalize_text(b):
        return {"kind": "same", "conventions": []}
    left, right = canonical_text(a), canonical_text(b)
    if left == right:
        needed = [
            kind for kind in CONVENTIONS
            if canonical_text(a, tuple(k for k in CONVENTIONS if k != kind))
            != canonical_text(b, tuple(k for k in CONVENTIONS if k != kind))
        ]
        if not needed:
            # Redundant rules (either alone would do): name every rule that changes something.
            needed = [
                kind for kind in CONVENTIONS
                if _convention(kind, normalize_text(a)) != normalize_text(a)
                or _convention(kind, normalize_text(b)) != normalize_text(b)
            ]
        return {"kind": "convention", "conventions": needed}
    if _DIGITS.findall(left) != _DIGITS.findall(right):
        return {"kind": "digits", "conventions": []}
    if not left or not right:
        return {"kind": "missing", "conventions": []}
    return {"kind": "text", "conventions": []}


def worst_kind(kinds) -> str:
    return max(kinds, key=_RANK.__getitem__, default="same")


def classify_readings(readings: list[str | None]) -> dict:
    """`classify_difference` over every pair of readings: the worst kind, all conventions."""
    kinds, conventions = [], set()
    for a, b in combinations(readings, 2):
        found = classify_difference(a, b)
        kinds.append(found["kind"])
        conventions.update(found["conventions"])
    return {"kind": worst_kind(kinds), "conventions": [k for k in CONVENTIONS if k in conventions]}


def _bag(value: str | None) -> str:
    """The words of a text sorted (conventions aside for the order), for `split` clusters."""
    return " ".join(sorted(normalize_text(value).split(), key=lambda word: (canonical_text(word), word)))


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
    # Aligned on the content, conventions aside: dot leaders kept by one model and
    # dropped by the other must not push a row off its counterpart.
    row_path = _align(len(ref), len(other), lambda i, j: content_distance(ref_rows[i], other_rows[j]))
    row_pairs = [(i, j) for i, j in row_path if i is not None and j is not None]

    ref_cols = len(ref[0]) if ref else 0
    other_cols = len(other[0]) if other else 0

    def column_cost(a: int, b: int) -> float:
        if not row_pairs:
            return 1.0
        return sum(content_distance(ref[i][a], other[j][b]) for i, j in row_pairs) / len(row_pairs)

    col_path = _align(ref_cols, other_cols, column_cost)
    row_map = {i: j for i, j in row_path if i is not None}
    col_map = {a: b for a, b in col_path if a is not None}
    extra_rows = [j for i, j in row_path if i is None and j is not None]
    extra_cols = [b for a, b in col_path if a is None and b is not None]
    return row_map, col_map, extra_rows, extra_cols


def compare_tables(grids: dict[str, dict]) -> dict:
    """Cell-by-cell disagreement, laid out on the first model's grid.

    Every reference cell carries each model's value at the aligned position (None when
    that model has no matching row or column), a score: the mean `content_distance`
    from the reference value (conventions aside), 1 for a model without the cell, the
    same with conventions counted (`raw_score`), and the `kind` of difference with
    the `conventions` it needs (`classify_readings`). Rows and columns only another
    model has are listed apart and count as disagreeing cells in the table score.
    `kinds` and `conventions` count the cells of each kind and of each rule.
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
        distances, raw = [], []
        for key, matrix in others.items():
            row_map, col_map, _rows, _cols = alignments[key]
            j, b = row_map.get(r), col_map.get(c)
            if j is None or b is None:
                values[key] = None
                distances.append(1.0)
                raw.append(1.0)
            else:
                values[key] = matrix[j][b]
                distances.append(content_distance(ref[r][c], matrix[j][b]))
                raw.append(text_distance(ref[r][c], matrix[j][b]))
        found = classify_readings([values[reference], *(values[key] for key in others)])
        cells.append({
            "r": r,
            "c": c,
            "rowspan": int(cell.get("rowspan", 1)),
            "colspan": int(cell.get("colspan", 1)),
            "values": values,
            "score": sum(distances) / len(distances) if distances else 0.0,
            "raw_score": sum(raw) / len(raw) if raw else 0.0,
            **found,
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
    raw_score = (sum(cell["raw_score"] for cell in cells) + extra_cells) / total if total else 0.0
    return {
        **_tally(cells),
        "raw_score": raw_score,
        "reference": reference,
        "rows": len(ref),
        "cols": len(ref[0]) if ref else 0,
        "cells": cells,
        "extra_rows": extra_rows,
        "extra_cols": extra_cols,
        "score": score,
    }


def _tally(cells: list[dict]) -> dict:
    """Cells of each kind of difference, and of each convention rule."""
    kinds = {kind: 0 for kind in DIFFERENCE_KINDS}
    conventions = {kind: 0 for kind in CONVENTIONS}
    for cell in cells:
        kinds[cell["kind"]] += 1
        for rule in cell["conventions"]:
            conventions[rule] += 1
    return {"kinds": kinds, "conventions": {rule: n for rule, n in conventions.items() if n}}


def _pair_distance(a: str | None, b: str | None) -> float:
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return 1.0
    return content_distance(a, b)


def annotate_grid(reference: dict, grids: dict[str, dict]) -> dict:
    """The models' readings laid on the user's own grid, for the table editor.

    The user's grid is the reference (it is the one being edited); each model's
    grid is aligned onto it like in `compare_tables`. A cell's `score` is how much
    the models disagree *with each other* (mean pairwise distance, 1 between a
    reading and no reading), not with the user: two models that agree point at
    nothing, whatever the user typed. The distance sets the conventions aside; the
    cell's `kind` and `conventions` say what kind of difference it is. A cell no
    model has scores None, kind `same`.
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
            found = {"kind": "same", "conventions": []}
        else:
            score = _mean_pairwise(readings, _pair_distance)
            found = classify_readings(readings)
        cells.append({"r": r, "c": c, "values": values, "score": score, **found})
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
            text_raw = table["raw_score"]
            found = {
                "kind": worst_kind(kind for kind, n in table["kinds"].items() if n),
                "conventions": list(table["conventions"]),
            }
        else:
            texts = [" ".join(str(block.get("content") or "") for block in blocks) for blocks in pieces.values()]
            if split:
                # Pieces joined in reading order can interleave columns: words, any order.
                texts = [_bag(value) for value in texts]
            text = _mean_pairwise(texts, content_distance)
            text_raw = _mean_pairwise(texts, text_distance)
            found = classify_readings(texts)
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
            # The text channel with the conventions counted, and what kind of difference it is.
            "text_raw": text_raw,
            "text_kind": found["kind"],
            "conventions": found["conventions"],
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
    # How often each convention rule turns up on the page: a table's cells one by
    # one, any other block once. It is what the researcher settles rule by rule.
    conventions = {kind: 0 for kind in CONVENTIONS}
    for c in clusters:
        if c["table"] is not None:
            for kind, n in c["table"]["conventions"].items():
                conventions[kind] += n
        else:
            for kind in c["conventions"]:
                conventions[kind] += 1
    return {
        "models": keys,
        "agreement": 1.0 - disagreement,
        "clusters": clusters,
        "conventions": {kind: n for kind, n in conventions.items() if n},
    }
