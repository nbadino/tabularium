"""Scansione della cartella archivio: individuazione immagini/PDF e candidati pagina.

Il rendering dei PDF è lazy (pypdfium2, dipendenza opzionale): se non installata,
i PDF vengono segnalati come non supportati nel report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import images as imgmod

SUPPORTED_IMAGE_EXTS = imgmod.SUPPORTED_IMAGE_EXTS


@dataclass(frozen=True)
class Candidate:
    path: Path
    source_kind: str  # 'image' | 'pdf'


@dataclass
class ScanReport:
    found_files: int = 0
    registered: int = 0
    duplicates: int = 0
    unsupported: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "found_files": self.found_files,
            "registered": self.registered,
            "duplicates": self.duplicates,
            "unsupported": self.unsupported,
            "errors": self.errors,
        }


def scan_archive(root_dir: str | Path) -> list[Candidate]:
    """Cammina ricorsivamente la cartella e restituisce i file supportati."""
    root = Path(root_dir)
    candidates: list[Candidate] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in SUPPORTED_IMAGE_EXTS:
            candidates.append(Candidate(p, "image"))
        elif ext == ".pdf":
            candidates.append(Candidate(p, "pdf"))
    return candidates


def render_pdf_pages(pdf_path: str | Path) -> list[tuple[object, tuple[int, int]]]:
    """Rende tutte le pagine di un PDF in immagini PIL. Richiede pypdfium2."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF richiede pypdfium2 (pip install pypdfium2)") from exc

    images: list[tuple[object, tuple[int, int]]] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for page in pdf:
            bitmap = page.render(scale=200 / 72)
            try:
                pil = bitmap.to_pil().convert("RGB")
                images.append((pil, (pil.width, pil.height)))
            finally:
                close = getattr(bitmap, "close", None)
                if callable(close):
                    close()
    finally:
        close = getattr(pdf, "close", None)
        if callable(close):
            close()
    return images