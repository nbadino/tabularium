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

def rel_archive(path: Path, archive_dir: str | Path) -> str:
    """Percorso della pagina relativo alla cartella archivio (assoluto se fuori)."""
    try:
        return str(path.relative_to(Path(archive_dir).resolve()))
    except ValueError:
        return str(path)


def register_candidate(
    conn,
    project_id: int,
    cand: Candidate,
    archive_dir: Path,
    report: ScanReport,
    lang: str = "it",
) -> None:
    """Registra un candidato (immagine o PDF) come pagine del progetto.

    Aggiorna il report con l'esito (registrato, duplicato, non supportato,
    errore). È la logica condivisa da scansione e import manuale: il router
    resta una porta HTTP.
    """
    import json  # noqa: PLC0415

    from . import page_meta as pagemeta  # noqa: PLC0415
    from . import pages as pagesvc  # noqa: PLC0415  (import lazy: pages→scan)
    from .i18n import msg  # noqa: PLC0415

    abs_path = str(cand.path.resolve())
    rel_path = rel_archive(cand.path, archive_dir)

    if cand.source_kind == "image":
        try:
            width, height = imgmod.image_size(cand.path)
        except Exception as exc:  # immagine corrotta/illeggibile
            report.errors.append(f"{cand.path.name}: {exc}")
            return
        # L'EXIF porta la data di *digitalizzazione*, non quella del giornale:
        # tenerla come issue_date falserebbe lo split per annata. Il nome file,
        # invece, codifica testata/numero/pagina in modo affidabile.
        meta = pagemeta.parse_filename(cand.path.name)
        extra = {"scan_date": imgmod.exif_datetime(cand.path) or None}
        if meta.publication:
            extra["publication"] = meta.publication
        cur = conn.execute(
            """INSERT OR IGNORE INTO pages
               (project_id, rel_path, abs_path, source_kind, width, height,
                issue_no, page_no, page_type, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                rel_path,
                abs_path,
                "image",
                width,
                height,
                meta.issue_no,
                meta.page_no,
                meta.page_type,
                json.dumps(extra),
            ),
        )
        if cur.rowcount == 0:
            report.duplicates += 1
            return
        page_id = cur.lastrowid
        thumb = pagesvc.thumb_path(page_id)
        imgmod.make_thumbnail(cand.path, thumb)
        conn.execute("UPDATE pages SET thumb_path=? WHERE id=?", (str(thumb), page_id))
        report.registered += 1
        return

    # -- PDF -----------------------------------------------------------------
    try:
        rendered = render_pdf_pages(cand.path)
    except Exception as exc:
        report.unsupported += 1
        report.errors.append(f"{cand.path.name}: {exc}")
        return
    if not rendered:
        report.unsupported += 1
        report.errors.append(f"{cand.path.name}: {msg('no_pages_rendered', lang)}")
        return
    meta = pagemeta.parse_filename(cand.path.name)
    extra = {"publication": meta.publication} if meta.publication else {}
    for page_idx, (pil_img, (width, height)) in enumerate(rendered):
        cur = conn.execute(
            """INSERT OR IGNORE INTO pages
               (project_id, rel_path, abs_path, source_kind, pdf_page, width, height,
                issue_no, page_no, page_type, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                rel_path,
                abs_path,
                "pdf",
                page_idx,
                width,
                height,
                meta.issue_no,
                # In un PDF multipagina il numero di pagina è l'indice, non il
                # suffisso del nome file (che identifica il fascicolo intero).
                meta.page_no if len(rendered) == 1 else str(page_idx + 1),
                meta.page_type,
                json.dumps(extra),
            ),
        )
        if cur.rowcount == 0:
            report.duplicates += 1
            continue
        page_id = cur.lastrowid
        thumb = pagesvc.save_pdf_thumb(pil_img, page_id)
        conn.execute("UPDATE pages SET thumb_path=? WHERE id=?", (str(thumb), page_id))
        report.registered += 1
