"""Readable documents built from a recognition run export: Markdown and the text
layer of a searchable PDF.

Both start from `recognition.export_run`, so they cover exactly the pages and the
blocks of the JSON export, in the same order. The block label decides the shape
(heading, list item, table, formula); the content is kept as the model or the
reviewer wrote it, because several models already emit Markdown.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

# Labels that change the Markdown shape. Every other label, custom ones included,
# is a paragraph: a missing label is never dropped.
HEADING_1 = frozenset({"Title"})
HEADING_2 = frozenset({"Headline", "Section-header"})
LIST_ITEM = frozenset({"List-item"})
FORMULA = frozenset({"Formula"})
TABLE = "Table"

_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_BREAK_RE = re.compile(r"<br\s*/?>|</p\s*>|</div\s*>|</li\s*>|</h[1-6]\s*>", re.IGNORECASE)
_HEADING_MARK_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*+•·]|\d{1,3}[.)])\s+")
_EMPHASIS_RE = re.compile(r"(\*\*|__)(.+?)\1")


# ── Tables ──────────────────────────────────────────────────────────────────


@dataclass
class TableRows:
    """A table as rows of cell texts. A cell covered by a span is an empty string,
    so every row has the same width."""

    rows: list[list[str]]
    header_rows: int = 0
    # Cell boxes normalised 0–1 on the table block, when the grid has its lines:
    # `(row, col, x0, y0, x1, y1, text)`. Empty when the geometry is unknown.
    boxes: list[tuple[int, int, float, float, float, float, str]] = field(default_factory=list)


class _HtmlTableParser(HTMLParser):
    """Rows of `(text, rowspan, colspan)`. A row is a header when it sits in
    `<thead>` or holds only `<th>` cells; only leading header rows count."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self.header_rows = 0
        self._cell: list[str] | None = None
        self._span = (1, 1)
        self._in_thead = False
        self._row_header = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._close_row()
            self.rows.append([])
            self._row_header = True
        elif tag in ("td", "th"):
            if not self.rows:
                self.rows.append([])
                self._row_header = True
            values = dict(attrs)
            self._cell = []
            self._span = (_int(values.get("rowspan")), _int(values.get("colspan")))
            self._row_header = self._row_header and (tag == "th" or self._in_thead)
        elif tag == "br" and self._cell is not None:
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "thead":
            self._in_thead = False
        elif tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            self.rows[-1].append((text, *self._span))
            self._cell = None
        elif tag in ("tr", "table"):
            self._close_row()

    def _close_row(self) -> None:
        if self.rows and self.rows[-1] and self._row_header and len(self.rows) == self.header_rows + 1:
            self.header_rows += 1
        self._row_header = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _int(value: object) -> int:
    try:
        return max(1, int(str(value)))
    except (TypeError, ValueError):
        return 1


def _spread(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """Lay HTML cells with spans on a regular grid."""
    grid: dict[tuple[int, int], str] = {}
    width = 0
    for r, row in enumerate(rows):
        c = 0
        for text, rowspan, colspan in row:
            while (r, c) in grid:
                c += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, c + dc)] = text if dr == 0 and dc == 0 else ""
            c += colspan
            width = max(width, c)
    height = max((r for r, _ in grid), default=-1) + 1
    return [[grid.get((r, c), "") for c in range(width)] for r in range(height)]


def html_table_rows(content: str) -> TableRows | None:
    """The first HTML table of `content`, or `None` when there is none."""
    match = _TABLE_RE.search(content or "")
    if match is None:
        return None
    parser = _HtmlTableParser()
    parser.feed(match.group(0))
    parser.close()
    rows = _spread(parser.rows)
    if not rows:
        return None
    return TableRows(rows=rows, header_rows=min(parser.header_rows, len(rows)))


def _lines(grid: dict, key: str, count: int) -> list[float] | None:
    values = grid.get(key) or []
    if len(values) != count + 1:
        return None
    try:
        return [min(1.0, max(0.0, float(v))) for v in values]
    except (TypeError, ValueError):
        return None


def grid_table_rows(grid: dict | None) -> TableRows | None:
    """A saved table grid (the table editor's), without its phantom columns."""
    if not grid:
        return None
    rows_n, cols_n = int(grid.get("rows", 0) or 0), int(grid.get("cols", 0) or 0)
    cells = [cell for cell in grid.get("cells", []) if isinstance(cell, dict)]
    if rows_n <= 0 or cols_n <= 0 or not any(str(c.get("text", "")).strip() for c in cells):
        return None
    phantom = {int(c) for c in grid.get("phantom_cols", []) if str(c).lstrip("-").isdigit()}
    keep = [c for c in range(cols_n) if c not in phantom]
    matrix = [["" for _ in range(cols_n)] for _ in range(rows_n)]
    hlines = _lines(grid, "hlines", rows_n)
    vlines = _lines(grid, "vlines", cols_n)
    bent = grid.get("row_columns") or []
    boxes: list[tuple[int, int, float, float, float, float, str]] = []
    for cell in cells:
        r, c = int(cell.get("r", 0)), int(cell.get("c", 0))
        if not (0 <= r < rows_n and 0 <= c < cols_n):
            continue
        text = " ".join(str(cell.get("text", "")).split())
        matrix[r][c] = text
        if not text or c in phantom or hlines is None:
            continue
        row_v = vlines
        if r < len(bent) and len(bent[r]) == cols_n + 1:
            row_v = [min(1.0, max(0.0, float(v))) for v in bent[r]]
        if row_v is None:
            continue
        rowspan = max(1, int(cell.get("rowspan", 1) or 1))
        colspan = max(1, int(cell.get("colspan", 1) or 1))
        r1, c1 = min(rows_n, r + rowspan), min(cols_n, c + colspan)
        boxes.append((r, c, row_v[c], hlines[r], row_v[c1], hlines[r1], text))
    return TableRows(
        rows=[[row[c] for c in keep] for row in matrix],
        header_rows=max(0, min(int(grid.get("header_rows", 0) or 0), rows_n)),
        boxes=boxes,
    )


def block_table(block: dict[str, Any], grid: dict | None) -> TableRows | None:
    """The table of a block: the saved grid wins, the HTML content is the fallback."""
    return grid_table_rows(grid) or html_table_rows(str(block.get("content") or ""))


# ── Plain text ──────────────────────────────────────────────────────────────


def plain_text(content: str) -> str:
    """Text without HTML tags and Markdown emphasis, line breaks kept."""
    text = _BREAK_RE.sub("\n", content or "")
    text = html.unescape(_TAG_RE.sub("", text))
    lines = []
    for line in text.splitlines():
        line = _EMPHASIS_RE.sub(r"\2", _HEADING_MARK_RE.sub("", line))
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


# ── Markdown ────────────────────────────────────────────────────────────────


def _cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>") or " "


def markdown_table(table: TableRows) -> str:
    """A GitHub-flavoured pipe table. GFM needs a header row: without a declared
    one the first row plays it, as in the scan."""
    rows = [row for row in table.rows if any(cell.strip() for cell in row)] or table.rows
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    head = max(1, table.header_rows)
    header = [" ".join(filter(None, column)) for column in zip(*rows[:head])]
    out = ["| " + " | ".join(_cell(c) for c in header) + " |", "|" + "|".join([" --- "] * width) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows[head:]]
    return "\n".join(out)


def _html_tables_to_markdown(content: str) -> str:
    def replace(match: re.Match[str]) -> str:
        table = html_table_rows(match.group(0))
        return f"\n\n{markdown_table(table)}\n\n" if table else ""

    text = _TABLE_RE.sub(replace, content)
    if "<" in text:
        text = html.unescape(_TAG_RE.sub("", _BREAK_RE.sub("\n", text)))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def block_markdown(block: dict[str, Any], grid: dict | None = None) -> str:
    """One block as Markdown; an empty string when it has no text."""
    label = str(block.get("label") or "")
    content = str(block.get("content") or "").strip()
    if label == TABLE:
        table = block_table(block, grid)
        if table is not None:
            return markdown_table(table)
    if not content:
        return ""
    content = _html_tables_to_markdown(content)
    if not content:
        return ""
    if label in HEADING_1 or label in HEADING_2:
        if _HEADING_MARK_RE.match(content):
            return content
        marks = "#" if label in HEADING_1 else "##"
        return f"{marks} {' '.join(content.split())}"
    if label in LIST_ITEM:
        first, *rest = content.splitlines()
        mark = _LIST_MARK_RE.match(first)
        if mark and mark.group(0).strip() not in ("•", "·"):
            return content  # already a Markdown list item
        if mark:
            first = first[mark.end():]
        return "\n".join([f"- {first.strip()}", *(f"  {line.strip()}" for line in rest)])
    if label in FORMULA and not content.startswith("$"):
        return f"$$\n{content}\n$$"
    return content


def page_title(page: dict[str, Any]) -> str:
    return str(page.get("rel_path") or f"page {page.get('page_id')}")


def run_markdown(exported: dict[str, Any], grids: dict[int, dict]) -> str:
    """The run as one Markdown document. Each page starts with an HTML comment:
    invisible once rendered, it still tells where a passage comes from."""
    sections: list[str] = []
    for page in exported["pages"]:
        parts = [f"<!-- {page_label(page)} -->"]
        for block in page["blocks"]:
            text = block_markdown(block, grids.get(_block_id(block)))
            if text:
                parts.append(text)
        sections.append("\n\n".join(parts))
    return "\n\n".join(sections).rstrip() + "\n"


def page_label(page: dict[str, Any]) -> str:
    """`p. 3 · volume.pdf` for a PDF page, the file name for an image."""
    rel_path = page_title(page)
    pdf_page = page.get("pdf_page")
    if pdf_page is None:
        return rel_path.replace("--", "- -")
    return f"p. {int(pdf_page) + 1} · {rel_path}".replace("--", "- -")


def _block_id(block: dict[str, Any]) -> int | None:
    try:
        return int(block["id"])
    except (KeyError, TypeError, ValueError):
        return None


# ── File names ──────────────────────────────────────────────────────────────

_UNSAFE_RE = re.compile(r"[^\w.\- ]+", re.UNICODE)


def download_stem(exported: dict[str, Any]) -> str:
    """The name of the source when every page comes from one file (`volume`),
    otherwise `tabularium-run-<id>`; the raw export says so in its name."""
    sources = {str(page.get("rel_path") or "") for page in exported["pages"]}
    run_id = exported["run"]["id"]
    stem = f"tabularium-run-{run_id}"
    if len(sources) == 1:
        name = Path(next(iter(sources))).stem
        cleaned = _UNSAFE_RE.sub("_", name).strip(" ._")
        if cleaned:
            stem = cleaned[:120]
    return f"{stem}-raw" if exported.get("scope") == "raw" else stem
