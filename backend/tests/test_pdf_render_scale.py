"""PDF pages render at the resolution of their embedded scan."""
from __future__ import annotations

import pytest
from PIL import Image

pdfium = pytest.importorskip("pypdfium2")

from app.services import scan  # noqa: E402


def _scan_pdf(path, size, dpi, pages=1):
    images = [Image.new("RGB", size, "white") for _ in range(pages)]
    images[0].save(path, save_all=True, append_images=images[1:], resolution=dpi)


def test_scan_renders_at_native_resolution(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scan_pdf(pdf, (1100, 1560), 300, pages=2)
    sizes = [size for _img, size in scan.iter_pdf_pages(pdf)]
    assert len(sizes) == 2
    for width, height in sizes:
        assert abs(width - 1100) <= 2 and abs(height - 1560) <= 2


def test_low_resolution_scan_uses_the_minimum_dpi(tmp_path):
    pdf = tmp_path / "low.pdf"
    _scan_pdf(pdf, (500, 700), 72)  # 500 x 700 pt
    (_img, (width, _height)), = list(scan.iter_pdf_pages(pdf))
    assert abs(width - 500 * scan.PDF_MIN_DPI / 72) <= 2


def test_single_page_render_reproduces_registered_width(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _scan_pdf(pdf, (1100, 1560), 300)
    # A page registered earlier at 200 DPI keeps its size, so its coordinates stay valid.
    _img, (width, _height) = scan.render_pdf_page(pdf, 0, target_width=733)
    assert width == 733
    _img, (width, _height) = scan.render_pdf_page(pdf, 0)
    assert abs(width - 1100) <= 2
