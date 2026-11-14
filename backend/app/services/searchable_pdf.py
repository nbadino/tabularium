"""Searchable PDF writer: each page is the scan Tabularium shows, with the
recognised text laid over it as invisible text (render mode 3).

The text uses an embedded *glyphless* TrueType font, the technique of Tesseract
and OCRmyPDF: every character is an empty glyph of fixed width, the `ToUnicode`
map turns the codes back into the characters, and each line is stretched to the
width of its box. A viewer then finds, highlights and copies the text where it is
printed on the scan. Nothing outside the standard library is needed.

The precision is the one of the annotation: a block, its lines (split evenly when
the content does not say where they break) and, for tables, the cells of the
saved grid.

The document is produced as a stream of chunks, one page at a time: a long run
starts downloading at once and never holds the whole file in memory.
"""
from __future__ import annotations

import io
import math
import struct
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

from PIL import Image

from . import run_documents
from .scan import PDF_RENDER_DPI

# Scans without a resolution in their metadata are taken at 300 DPI: the
# common archive value, and what makes an A4 scan an A4 page.
DEFAULT_IMAGE_DPI = 300
JPEG_QUALITY = 88
_GLYPH_ADVANCE = 0.5  # every glyph is 500/1000 em wide (`/DW 500`)
# How wide a printed character is against the height of its line box, to guess
# how many lines a paragraph spans. Measured on scans (a recognised line box is
# taller than its letters): 0.19 for a single OCR line, 0.32 for a dense
# paragraph. A quarter keeps one line one line and splits a paragraph right.
_CHAR_TO_LINE = 0.25


@dataclass
class PdfPageInput:
    """One page to write. `image` is the page image the blocks refer to (it may
    be `None` when the source file is missing: the page is then blank)."""

    image: Image.Image | None
    dpi: float
    blocks: list[dict[str, Any]]
    grids: dict[int, dict] = field(default_factory=dict)
    size_px: tuple[int, int] | None = None


def page_dpi(row: dict[str, Any] | None, image: Image.Image | None) -> float:
    """The resolution that turns the pixels of the page image back into points."""
    if row is not None and row.get("source_kind") == "pdf":
        return float(PDF_RENDER_DPI)
    if image is not None:
        dpi = image.info.get("dpi")
        try:
            value = float(dpi[0] if isinstance(dpi, (tuple, list)) else dpi)
        except (TypeError, ValueError, IndexError):
            value = 0.0
        if 50 <= value <= 1200:
            return value
    return float(DEFAULT_IMAGE_DPI)


# ── The glyphless font ──────────────────────────────────────────────────────


def _checksum(data: bytes) -> int:
    data += b"\0" * (-len(data) % 4)
    return sum(struct.unpack(f">{len(data) // 4}L", data)) & 0xFFFFFFFF


@lru_cache(maxsize=1)
def glyphless_font() -> bytes:
    """A minimal TrueType font: `.notdef` and one glyph, both 500 units wide.
    The PDF maps every character to glyph 1.

    Glyph 1 is a full-height rectangle rather than an empty glyph: the text is
    never painted (render mode 3), but viewers size search highlights and
    selections on the glyph box, and an empty one gives them zero height."""
    units, advance, glyphs = 1000, 500, 2
    head = struct.pack(
        ">LLLLHHqqhhhhHHh",
        0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0b1011, units,
        0, 0, 0, 0, advance, units, 0, 8, 2,  # created, modified, bbox, style…
    ) + struct.pack(">hh", 0, 0)  # indexToLocFormat (short), glyphDataFormat
    hhea = struct.pack(
        ">LhhhHhhhhhhhhhhhH",
        0x00010000, units, 0, 0, advance, 0, 0, advance, 1, 0, 0, 0, 0, 0, 0, 0, glyphs,
    )
    maxp = struct.pack(">LH13H", 0x00010000, glyphs, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    hmtx = struct.pack(">HhHh", advance, 0, advance, 0)
    # `.notdef` has no data. Glyph 1: one contour, four on-curve points
    # (0,0) (500,0) (500,1000) (0,1000), coordinates as 16-bit deltas.
    glyf = (
        struct.pack(">5h", 1, 0, 0, advance, units)
        + struct.pack(">HH", 3, 0)  # endPtsOfContours, no instructions
        + bytes([0x01] * 4)
        + struct.pack(">4h", 0, advance, 0, -advance)
        + struct.pack(">4h", 0, 0, units, 0)
    )
    glyf += b"\0" * (len(glyf) % 2)  # short `loca` stores offsets / 2
    loca = struct.pack(">3H", 0, 0, len(glyf) // 2)
    # cmap: one format 4 subtable with only the mandatory final segment. The PDF
    # addresses glyphs through `CIDToGIDMap`, never through this table.
    subtable = struct.pack(">7H", 4, 24, 0, 2, 2, 0, 0) + struct.pack(">HHHhH", 0xFFFF, 0, 0xFFFF, 1, 0)
    cmap = struct.pack(">HHHHL", 0, 1, 3, 1, 12) + subtable
    names = {1: "GlyphLessFont", 2: "Regular", 3: "Tabularium:GlyphLessFont", 4: "GlyphLessFont",
             5: "Version 1.0", 6: "GlyphLessFont"}
    records, strings = b"", b""
    for name_id, text in names.items():
        encoded = text.encode("utf-16-be")
        records += struct.pack(">6H", 3, 1, 0x409, name_id, len(encoded), len(strings))
        strings += encoded
    name = struct.pack(">3H", 0, len(names), 6 + len(records)) + records + strings
    post = struct.pack(">LLhhL4L", 0x00030000, 0, -100, 50, 1, 0, 0, 0, 0)
    os2 = struct.pack(
        ">HhHHH11h10s4L4sHHHhhhHH2L",
        1, advance, 400, 5, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 50, 300, 0,
        b"\0" * 10, 0, 0, 0, 0, b"TBLR", 0x40, 0x20, 0xFFFF,
        units, 0, 0, units, 0, 1, 0,
    )
    tables = {b"OS/2": os2, b"cmap": cmap, b"glyf": glyf, b"head": head, b"hhea": hhea,
              b"hmtx": hmtx, b"loca": loca, b"maxp": maxp, b"name": name, b"post": post}
    count = len(tables)
    power = 2 ** int(math.log2(count))
    directory = struct.pack(">LHHHH", 0x00010000, count, power * 16, int(math.log2(power)), count * 16 - power * 16)
    offset = 12 + 16 * count
    body = b""
    entries = b""
    head_offset = 0
    for tag in sorted(tables):
        data = tables[tag]
        if tag == b"head":
            head_offset = offset + len(body)
        entries += struct.pack(">4sLLL", tag, _checksum(data), offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    font = bytearray(directory + entries + body)
    adjustment = (0xB1B0AFBA - _checksum(bytes(font))) & 0xFFFFFFFF
    font[head_offset + 8 : head_offset + 12] = struct.pack(">L", adjustment)
    return bytes(font)


def _to_unicode_cmap() -> bytes:
    """Identity `ToUnicode` for the Basic Multilingual Plane, in ranges that only
    vary in their last byte (as the CMap rules require)."""
    starts = [hi for hi in range(256) if not 0xD8 <= hi <= 0xDF]  # no surrogates
    blocks = []
    for i in range(0, len(starts), 100):
        chunk = starts[i : i + 100]
        lines = "\n".join(f"<{hi:02X}00> <{hi:02X}FF> <{hi:02X}00>" for hi in chunk)
        blocks.append(f"{len(chunk)} beginbfrange\n{lines}\nendbfrange")
    return (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        + "\n".join(blocks)
        + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    ).encode("ascii")


# ── Text layout ─────────────────────────────────────────────────────────────


@dataclass
class TextLine:
    """One line of invisible text, in image pixels (origin top-left)."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def _bbox(points: object) -> tuple[float, float, float, float] | None:
    try:
        xs = [float(p[0]) for p in points]  # type: ignore[index, union-attr]
        ys = [float(p[1]) for p in points]  # type: ignore[index, union-attr]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or max(xs) - min(xs) < 1 or max(ys) - min(ys) < 1:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _wrap(words: list[str], count: int) -> list[str]:
    """Split `words` in `count` lines of about the same length: each word goes to
    the line its middle falls in."""
    total = sum(len(word) + 1 for word in words)
    target = total / count
    lines: list[list[str]] = [[] for _ in range(count)]
    position = 0
    for word in words:
        index = min(count - 1, int((position + len(word) / 2) / target))
        lines[index].append(word)
        position += len(word) + 1
    return [" ".join(line) for line in lines if line]


def text_lines(text: str, box: tuple[float, float, float, float]) -> list[TextLine]:
    """Lines of `text` spread over `box`.

    When the content has fewer line breaks than the box can hold (a paragraph
    recognised as one string), it is rewrapped: with characters `r` times as wide
    as the line is high, `k` lines of `n/k` characters fill a `w × h` box when
    `k² = n·r·h / w` (`r` is `_CHAR_TO_LINE`).
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    chars = sum(len(line) for line in lines)
    estimated = max(1, round(math.sqrt(chars * height * _CHAR_TO_LINE / max(width, 1.0))))
    if estimated > len(lines):
        words = " ".join(lines).split()
        lines = _wrap(words, min(estimated, len(words)))
    pitch = height / len(lines)
    return [TextLine(line, x0, y0 + i * pitch, x1, y0 + (i + 1) * pitch) for i, line in enumerate(lines)]


def _table_lines(table: run_documents.TableRows, box: tuple[float, float, float, float]) -> list[TextLine]:
    """One line per cell. Each cell ends with a space, so a viewer that copies a
    row does not glue adjacent cells together."""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if table.boxes:
        return [
            TextLine(text + " ", x0 + bx0 * width, y0 + by0 * height, x0 + bx1 * width, y0 + by1 * height)
            for _r, _c, bx0, by0, bx1, by1, text in table.boxes
            if bx1 > bx0 and by1 > by0
        ]
    rows = table.rows
    out: list[TextLine] = []
    pitch = height / len(rows)
    for r, row in enumerate(rows):
        step = width / max(1, len(row))
        for c, text in enumerate(row):
            if text.strip():
                out.append(TextLine(text + " ", x0 + c * step, y0 + r * pitch, x0 + (c + 1) * step, y0 + (r + 1) * pitch))
    return out


def block_lines(block: dict[str, Any], grid: dict | None, page_size: tuple[float, float]) -> list[TextLine]:
    """The invisible lines of one block. A block without a usable box keeps its
    text in a thin strip at the bottom of the page: still found by a search."""
    box = _bbox(block.get("points") or [])
    if block.get("label") == run_documents.TABLE:
        table = run_documents.block_table(block, grid)
        if table is not None and box is not None:
            return _table_lines(table, box)
        text = "\n".join(" ".join(c for c in row if c) for row in table.rows) if table else ""
    else:
        text = run_documents.plain_text(str(block.get("content") or ""))
    if not text.strip():
        return []
    if box is None:
        w, h = page_size
        return [TextLine(" ".join(text.split()), 0, h - max(2.0, h / 200), w, h)]
    return text_lines(text, box)


def _encode(text: str) -> str:
    """UTF-16BE hex of the BMP characters; others become U+FFFD."""
    units = [ord(ch) if ord(ch) <= 0xFFFF and not 0xD800 <= ord(ch) <= 0xDFFF else 0xFFFD for ch in text]
    return "".join(f"{unit:04X}" for unit in units)


def _text_ops(lines: list[TextLine], scale: float, page_height: float) -> list[str]:
    ops: list[str] = []
    for line in lines:
        text = " ".join(line.text.split()) + (" " if line.text.endswith(" ") else "")
        if not text.strip():
            continue
        width = (line.x1 - line.x0) * scale
        height = (line.y1 - line.y0) * scale
        if width <= 0 or height <= 0:
            continue
        # Font size 1 and a text matrix that stretches each glyph: the line fills
        # its box exactly, whatever the length of the text.
        sx = width / (len(text) * _GLYPH_ADVANCE)
        x = line.x0 * scale
        y = page_height - line.y1 * scale
        ops.append(f"{sx:.4f} 0 0 {height:.4f} {x:.3f} {y:.3f} Tm <{_encode(text)}> Tj")
    return ops


# ── Images ──────────────────────────────────────────────────────────────────


def _image_xobject(image: Image.Image) -> tuple[dict[str, str], bytes]:
    """The image as a PDF XObject: a JPEG file is passed through untouched,
    a bilevel scan stays bilevel, the rest is encoded once as JPEG."""
    source = getattr(image, "filename", "") or ""
    if image.format == "JPEG" and image.mode in ("RGB", "L") and source and Path(source).is_file():
        colour = "/DeviceRGB" if image.mode == "RGB" else "/DeviceGray"
        data = Path(source).read_bytes()
        return {"ColorSpace": colour, "BitsPerComponent": "8", "Filter": "/DCTDecode"}, data
    if image.mode == "1":
        data = zlib.compress(image.tobytes(), 6)
        return {"ColorSpace": "/DeviceGray", "BitsPerComponent": "1", "Filter": "/FlateDecode"}, data
    if image.mode not in ("RGB", "L"):
        image = image.convert("L" if image.mode in ("LA", "I", "I;16", "F") else "RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    colour = "/DeviceRGB" if image.mode == "RGB" else "/DeviceGray"
    return {"ColorSpace": colour, "BitsPerComponent": "8", "Filter": "/DCTDecode"}, buffer.getvalue()


# ── The document ────────────────────────────────────────────────────────────


def _pdf_string(text: str) -> str:
    """A text string for the document information (UTF-16BE with BOM)."""
    return "<FEFF" + "".join(f"{b:02X}" for b in text.encode("utf-16-be")) + ">"


class _Writer:
    def __init__(self) -> None:
        self.offset = 0
        self.offsets: dict[int, int] = {}
        self.next_id = 1

    def reserve(self) -> int:
        number = self.next_id
        self.next_id += 1
        return number

    def obj(self, number: int, body: bytes) -> bytes:
        self.offsets[number] = self.offset
        chunk = f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
        self.offset += len(chunk)
        return chunk

    def stream(self, number: int, entries: dict[str, str], data: bytes) -> bytes:
        header = " ".join(f"/{key} {value}" for key, value in {**entries, "Length": str(len(data))}.items())
        return self.obj(number, f"<< {header} >>\nstream\n".encode() + data + b"\nendstream")

    def raw(self, data: bytes) -> bytes:
        self.offset += len(data)
        return data


def write_pdf(pages: Iterable[PdfPageInput], *, title: str = "") -> Iterator[bytes]:
    """Yield the bytes of the searchable PDF, one page at a time."""
    w = _Writer()
    catalog, pages_id, font, cid_font, descriptor, font_file, cid_map, to_unicode, info = (
        w.reserve() for _ in range(9)
    )
    yield w.raw(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    yield w.obj(catalog, f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    yield w.obj(font, (
        f"<< /Type /Font /Subtype /Type0 /BaseFont /GlyphLessFont /Encoding /Identity-H "
        f"/DescendantFonts [{cid_font} 0 R] /ToUnicode {to_unicode} 0 R >>"
    ).encode())
    yield w.obj(cid_font, (
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /GlyphLessFont "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor {descriptor} 0 R /DW 500 /CIDToGIDMap {cid_map} 0 R >>"
    ).encode())
    yield w.obj(descriptor, (
        "<< /Type /FontDescriptor /FontName /GlyphLessFont /Flags 5 /FontBBox [0 0 500 1000] "
        f"/ItalicAngle 0 /Ascent 1000 /Descent 0 /CapHeight 1000 /StemV 80 /FontFile2 {font_file} 0 R >>"
    ).encode())
    font_bytes = glyphless_font()
    yield w.stream(font_file, {"Length1": str(len(font_bytes)), "Filter": "/FlateDecode"}, zlib.compress(font_bytes))
    yield w.stream(cid_map, {"Filter": "/FlateDecode"}, zlib.compress(b"\x00\x01" * 65536, 9))
    yield w.stream(to_unicode, {"Filter": "/FlateDecode"}, zlib.compress(_to_unicode_cmap()))
    yield w.obj(info, f"<< /Producer (Tabularium) /Title {_pdf_string(title)} >>".encode())

    kids: list[int] = []
    for page in pages:
        image = page.image
        try:
            size_px = image.size if image is not None else (page.size_px if page.size_px and min(page.size_px) > 0 else (2480, 3508))
            scale = 72.0 / page.dpi
            width_pt, height_pt = size_px[0] * scale, size_px[1] * scale
            page_id, contents_id = w.reserve(), w.reserve()
            ops = []
            resources = f"/Font << /F1 {font} 0 R >>"
            if image is not None:
                image_id = w.reserve()
                entries, data = _image_xobject(image)
                entries = {"Type": "/XObject", "Subtype": "/Image", "Width": str(image.width),
                           "Height": str(image.height), **entries}
                yield w.stream(image_id, entries, data)
                resources += f" /XObject << /Im0 {image_id} 0 R >>"
                ops.append(f"q {width_pt:.3f} 0 0 {height_pt:.3f} 0 0 cm /Im0 Do Q")
            lines: list[TextLine] = []
            for block in page.blocks:
                block_id = block.get("id")
                grid = page.grids.get(int(block_id)) if isinstance(block_id, int) else None
                lines.extend(block_lines(block, grid, (float(size_px[0]), float(size_px[1]))))
            text_ops = _text_ops(lines, scale, height_pt)
            if text_ops:
                ops.append("BT 3 Tr /F1 1 Tf")
                ops.extend(text_ops)
                ops.append("ET")
            yield w.stream(contents_id, {"Filter": "/FlateDecode"}, zlib.compress("\n".join(ops).encode("ascii")))
            yield w.obj(page_id, (
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {width_pt:.3f} {height_pt:.3f}] "
                f"/Resources << {resources} >> /Contents {contents_id} 0 R >>"
            ).encode())
            kids.append(page_id)
        finally:
            if image is not None:
                image.close()

    kid_refs = " ".join(f"{kid} 0 R" for kid in kids)
    yield w.obj(pages_id, f"<< /Type /Pages /Kids [{kid_refs}] /Count {len(kids)} >>".encode())
    xref_at = w.offset
    total = w.next_id
    rows = ["0000000000 65535 f "] + [
        f"{w.offsets[n]:010d} 00000 n " if n in w.offsets else "0000000000 00000 f " for n in range(1, total)
    ]
    yield w.raw(
        (f"xref\n0 {total}\n" + "\n".join(rows) + "\n"
         f"trailer\n<< /Size {total} /Root {catalog} 0 R /Info {info} 0 R >>\n"
         f"startxref\n{xref_at}\n%%EOF\n").encode()
    )
