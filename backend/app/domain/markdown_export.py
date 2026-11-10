"""One Markdown document from a run's pages, following Unlimited-OCR-2's converter.

The rules are those of `utils/process_unlimitedOCR2.py` in github.com/baidu/Unlimited-OCR
(the vendor's MarathonDocBench converter), reimplemented on Tabularium's blocks:

* pages in order, blank pages kept so page numbers do not shift;
* `title` -> a heading of its `<LEVELn>` (none: one `#`); strictly adjacent titles of
  the same page and level become one; the heading tree is made single-rooted;
* images -> `![](page<N>_x1_y1_x2_y2.jpg)`, coordinates on the 0-1000 grid;
* `cross_<t>`/`append_<t>` chains (text and tables) and same-page `<MERGE>` columns are
  grouped and concatenated in reading order; table fragments are merged into one table
  without the header rows a continuation repeats;
* stray special tokens, bare bbox lines and malformed `colspan`/`rowspan` are removed.

A block carries the vendor's facts in `native` (`kind`, `chain`, `level`, `merge_prev`)
when Unlimited-OCR-2 read it; any other block (another model, or drawn by hand) falls
back on its Tabularium label, without chains.
"""
from __future__ import annotations

import re
from typing import Any

from . import otsl

_STRAY = re.compile(
    r"<[|｜]/?[a-zA-Z_]+[|｜]>|<image>"
    r"|<\s*/?\s*PAGE\s*\d*\s*>|<\s*/?\s*BLOCK\s*>|<\s*/?\s*FORK\s*>"
    r"|<\s*/?\s*LEVEL\s*\d*\s*[>}]?|<\s*/?\s*MERGE\s*>",
    re.I,
)
_TR = re.compile(r"<tr\b[^>]*>.*?</tr>", re.I | re.S)
_TABLE_OPEN = re.compile(r"<table\b[^>]*>", re.I)
_SPAN = re.compile(r'\b(colspan|rowspan)\s*=\s*"([^"]*)"', re.I)
_BBOX_LINE = re.compile(r"(?m)^\s*\[\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*\]\s*$")
_NOISE = {"[non-text]", "[unreadable]", "[image]", "[figure]"}
_IMAGE_KINDS = {"image", "figure", "picture", "chart", "img", "seal"}
_CHAIN_KINDS = {"text", "table"}
_MAX_HEADER_ROWS = 5

# A block without native facts: its Tabularium label stands for the vendor's kind.
_KIND_OF_LABEL = {
    "Title": ("title", 1),
    "Section-header": ("title", 2),
    "Table": ("table", None),
    "Picture": ("image", None),
    "Page-header": ("header", None),
    "Page-footer": ("footer", None),
    "Caption": ("image_caption", None),
    "Footnote": ("page_footnote", None),
    "Formula": ("equation", None),
}


def _block(block: dict[str, Any], page_no: int, width: float, height: float) -> dict[str, Any]:
    native = block.get("native") or {}
    if native.get("kind"):
        kind, level = str(native["kind"]), native.get("level")
    else:
        kind, level = _KIND_OF_LABEL.get(str(block.get("label") or ""), ("text", None))
    content = str(block.get("content") or "")
    grid = block.get("grid")
    if kind == "table" and isinstance(grid, dict) and grid.get("rows") and grid.get("cols"):
        content = otsl.grid_to_html(grid)
    return {
        "kind": kind,
        "chain": native.get("chain"),
        "level": int(level) if level else None,
        "merge_prev": bool(native.get("merge_prev")),
        "content": content.strip(),
        "page": page_no,
        "bbox": _grid_bbox(block.get("points"), width, height),
    }


def _grid_bbox(points: Any, width: float, height: float) -> tuple[int, int, int, int] | None:
    try:
        (x1, y1), (x2, y2) = points[0], points[-1]
    except (TypeError, ValueError, IndexError):
        return None
    if not width or not height:
        return None

    def scale(value: float, size: float) -> int:
        return max(0, min(1000, round(float(value) / size * 1000)))

    return scale(x1, width), scale(y1, height), scale(x2, width), scale(y2, height)


def _merge_adjacent_titles(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (
            prev is not None and block["kind"] == prev["kind"] == "title"
            and block["page"] == prev["page"]
            and block["level"] is not None and block["level"] == prev["level"]
        ):
            prev["content"] = "\n".join(x for x in (prev["content"], block["content"]) if x)
            continue
        out.append(dict(block))
    return out


def _groups(blocks: list[dict]) -> list[list[int]]:
    """Union-find of `cross_`/`append_` chains and same-page `<MERGE>` columns."""
    parent = list(range(len(blocks)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    table_pages = {b["page"] for b in blocks if b["kind"] == "table"}
    open_chain: dict[str, int] = {}
    for i, block in enumerate(blocks):
        kind = block["kind"]
        if kind not in _CHAIN_KINDS:
            continue
        if block["chain"] == "append":
            tail = open_chain.get(kind)
            if tail is None:
                continue
            tail_page = blocks[tail]["page"]
            # A table continues across pages only if no other table sits in between.
            if kind == "table" and block["page"] - tail_page > 1 and any(
                p in table_pages for p in range(tail_page + 1, block["page"])
            ):
                open_chain.pop(kind, None)
                continue
            union(tail, i)
            open_chain[kind] = i
        elif block["chain"] == "cross" or kind == "table":
            open_chain[kind] = i

    def joinable(block: dict) -> bool:
        return block["kind"] in {"title", "text"}

    for i in range(1, len(blocks)):
        block, prev = blocks[i], blocks[i - 1]
        if block["merge_prev"] and block["page"] == prev["page"] and joinable(block) and joinable(prev):
            union(i - 1, i)

    grouped: dict[int, list[int]] = {}
    for i in range(len(blocks)):
        grouped.setdefault(find(i), []).append(i)
    return [grouped[root] for root in sorted(grouped, key=lambda root: grouped[root][0])]


def _row_key(row: str) -> tuple[int, str]:
    text = re.sub(r"<[^>]+>", " ", row)
    # Full-width ASCII to ASCII, as a repeated header may be typed either way.
    text = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)
    cells = len(re.findall(r"<t[hd]\b", row, re.I))
    return cells, "".join(text.split()).lower()


def merge_table_fragments(fragments: list[str]) -> str:
    """One table from its page fragments, without the header rows a fragment repeats."""
    fragments = [f for f in fragments if f and f.strip()]
    if len(fragments) <= 1:
        return fragments[0] if fragments else ""
    base_rows = _TR.findall(fragments[0])
    if not base_rows:
        return "\n\n".join(fragments)
    rows, base_keys, extra = list(base_rows), [_row_key(r) for r in base_rows], []
    for fragment in fragments[1:]:
        fragment_rows = _TR.findall(fragment)
        if not fragment_rows:
            extra.append(fragment.strip())
            continue
        skip = 0
        for row in fragment_rows[:_MAX_HEADER_ROWS]:
            if skip < len(base_keys) and _row_key(row) == base_keys[skip]:
                skip += 1
            else:
                break
        rows.extend(fragment_rows[skip:])
    opening = _TABLE_OPEN.search(fragments[0])
    merged = (opening.group(0) if opening else "<table>") + "".join(rows) + "</table>"
    return merged + ("\n\n" + "\n\n".join(extra) if extra else "")


def _render(blocks: list[dict], groups: list[list[int]]) -> list[str]:
    items: list[str] = []
    for members in groups:
        head = blocks[members[0]]
        kind = head["kind"]
        contents = [
            blocks[k]["content"] for k in members
            if blocks[k]["content"] and blocks[k]["content"].lower() not in _NOISE
        ]
        if kind in _IMAGE_KINDS:
            if head["bbox"] is not None:
                x1, y1, x2, y2 = head["bbox"]
                items.append(f"![](page{head['page']}_{x1}_{y1}_{x2}_{y2}.jpg)")
            if contents:
                items.append(contents[0])
            continue
        if not contents:
            continue
        if kind == "title":
            joined = "".join(contents)
            if _STRAY.sub("", joined).strip():
                items.append("#" * max(1, head["level"] or 1) + " " + joined)
            continue
        if kind == "table":
            items.append(merge_table_fragments(contents) if len(contents) > 1 else contents[0])
            continue
        items.append("".join(contents))  # text family: concatenated, no separator
    return items


def _heading_level(item: str) -> int:
    stripped = item.lstrip()
    return len(stripped) - len(stripped.lstrip("#"))


def _single_root(items: list[str]) -> list[str]:
    heads = [i for i, item in enumerate(items) if item.lstrip().startswith("#")]
    if len(heads) <= 1:
        return items
    levels = [_heading_level(items[i]) for i in heads]
    if sum(1 for level in levels if level == min(levels)) <= 1:
        return items
    out = list(items)
    for k, i in enumerate(heads):
        body = items[i].lstrip()[levels[k]:].lstrip(" ")
        out[i] = "#" * (1 if k == 0 else max(2, levels[k] - levels[0] + 2)) + " " + body
    return out


def _span_attrs(text: str) -> str:
    def repl(match: re.Match) -> str:
        if match.group(2).isdigit():
            return match.group(0)
        number = re.search(r"\d+", match.group(2))
        return f'{match.group(1)}="{number.group() if number else 1}"'

    return _SPAN.sub(repl, text)


def document_markdown(pages: list[dict[str, Any]]) -> str:
    """Markdown of one document: `pages` in order, each `{blocks, width, height}`."""
    blocks = [
        _block(block, page_no, float(page.get("width") or 0), float(page.get("height") or 0))
        for page_no, page in enumerate(pages, start=1)
        for block in page.get("blocks") or ()
    ]
    blocks = _merge_adjacent_titles(blocks)
    items = _render(blocks, _groups(blocks))
    items = [i for i in items if not (i.lstrip().startswith("#") and not i.lstrip().lstrip("#").strip())]
    folded = []
    for item in items:
        stripped = item.lstrip()
        if stripped.startswith("#"):
            n = _heading_level(stripped)
            item = "#" * n + " " + re.sub(r"\s+", " ", stripped[n:]).strip()
        folded.append(item)
    text = "\n\n".join(folded)
    text = text.replace("\\coloneqq", ":=").replace("\\eqqcolon", "=:")
    text = _span_attrs(_STRAY.sub("", text))
    text = _BBOX_LINE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return "\n\n".join(_single_root(text.split("\n\n")))
