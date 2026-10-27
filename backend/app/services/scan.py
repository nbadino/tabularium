"""Scansione della cartella archivio: individuazione immagini/PDF e candidati pagina.

Il rendering dei PDF è lazy (pypdfium2, dipendenza opzionale): se non installata,
i PDF vengono segnalati come non supportati nel report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..adapters import pages as pagestore
from ..db import connect
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
    missing: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "found_files": self.found_files,
            "registered": self.registered,
            "duplicates": self.duplicates,
            "unsupported": self.unsupported,
            "missing": self.missing,
            "errors": self.errors,
        }


def find_missing_page_ids(conn, project_id: int, candidates: list[Candidate]) -> list[int]:
    """Nasconde le pagine il cui file sorgente non è più nell'archivio.

    Non elimina righe, blocchi o risultati: una pagina può quindi tornare
    disponibile se il file viene ripristinato e sottoposto a una nuova scan.
    """
    paths = {str(c.path.resolve()) for c in candidates}
    return [
        row["id"]
        for row in pagestore.existing_paths(project_id)
        if row["abs_path"] not in paths
    ]


def mark_missing_pages(conn, page_ids: list[int]) -> int:
    return pagestore.mark_missing(page_ids, conn)


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
    return list(iter_pdf_pages(pdf_path))


def iter_pdf_pages(pdf_path: str | Path):
    """Itera le pagine con un solo PdfDocument, rilasciandone una alla volta."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF richiede pypdfium2 (pip install pypdfium2)") from exc
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for page in pdf:
            try:
                bitmap = page.render(scale=200 / 72)
                try:
                    pil = bitmap.to_pil().convert("RGB")
                    yield pil, (pil.width, pil.height)
                finally:
                    close_bitmap = getattr(bitmap, "close", None)
                    if callable(close_bitmap):
                        close_bitmap()
            finally:
                # pypdfium2 mantiene i PdfPage nel proprio ObjectTracker:
                # chiuderli subito evita centinaia di weakref «dead» sui PDF
                # multipagina e rilascia memoria prima della pagina seguente.
                close_page = getattr(page, "close", None)
                if callable(close_page):
                    close_page()
    finally:
        close = getattr(pdf, "close", None)
        if callable(close):
            close()



def render_pdf_page(pdf_path: str | Path, index: int) -> tuple[object, tuple[int, int]] | None:
    """Renderizza una sola pagina: preview/riconoscimento non devono renderizzare
    l'intero PDF a ogni click."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("PDF richiede pypdfium2 (pip install pypdfium2)") from exc
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if index < 0 or index >= len(pdf):
            return None
        page = pdf[index]
        try:
            bitmap = page.render(scale=200 / 72)
            try:
                pil = bitmap.to_pil().convert("RGB")
                return pil, (pil.width, pil.height)
            finally:
                close = getattr(bitmap, "close", None)
                if callable(close): close()
        finally:
            close = getattr(page, "close", None)
            if callable(close): close()
    finally:
        close = getattr(pdf, "close", None)
        if callable(close): close()


def pdf_page_count(pdf_path: str | Path) -> int:
    """Restituisce il numero di pagine senza renderizzare il documento."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PDF richiede pypdfium2 (pip install pypdfium2)") from exc
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return len(pdf)
    finally:
        close = getattr(pdf, "close", None)
        if callable(close):
            close()

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
        page_id = pagestore.insert_image_page(
            conn,
            project_id=project_id,
            rel_path=rel_path,
            abs_path=abs_path,
            width=width,
            height=height,
            issue_no=meta.issue_no,
            page_no=meta.page_no,
            page_type=meta.page_type,
            meta_json=json.dumps(extra),
        )
        if page_id is None:
            report.duplicates += 1
            return
        thumb = pagesvc.thumb_path(page_id)
        imgmod.make_thumbnail(cand.path, thumb)
        pagestore.set_thumb(conn, page_id, str(thumb))
        report.registered += 1
        return

    # -- PDF -----------------------------------------------------------------
    try:
        page_count = pdf_page_count(cand.path)
    except Exception as exc:
        report.unsupported += 1
        report.errors.append(f"{cand.path.name}: {exc}")
        return
    if page_count <= 0:
        report.unsupported += 1
        report.errors.append(f"{cand.path.name}: {msg('no_pages_rendered', lang)}")
        return
    meta = pagemeta.parse_filename(cand.path.name)
    extra = {"publication": meta.publication} if meta.publication else {}
    for page_idx, rendered in enumerate(iter_pdf_pages(cand.path)):
        pil_img, (width, height) = rendered
        page_id = pagestore.insert_pdf_page(
            conn,
            project_id=project_id,
            rel_path=rel_path,
            abs_path=abs_path,
            pdf_page=page_idx,
            width=width,
            height=height,
            issue_no=meta.issue_no,
            # In un PDF multipagina il numero di pagina è l'indice, non il
            # suffisso del nome file (che identifica il fascicolo intero).
            page_no=meta.page_no if page_count == 1 else str(page_idx + 1),
            page_type=meta.page_type,
            meta_json=json.dumps(extra),
        )
        if page_id is None:
            report.duplicates += 1
            pil_img.close()
            continue
        extracted = pagesvc.pdf_image_path(page_id)
        pil_img.save(extracted, format="PNG", optimize=True)
        pagestore.set_meta(
            conn, page_id, json.dumps({**extra, "extracted_path": str(extracted)})
        )
        thumb = pagesvc.save_pdf_thumb(pil_img, page_id)
        pagestore.set_thumb(conn, page_id, str(thumb))
        pil_img.close()
        report.registered += 1


def run_scan(
    project_id: int,
    archive_dir: str | Path,
    *,
    confirm_missing: bool = False,
    lang: str = "it",
    on_progress=None,
) -> ScanReport:
    """Scan an archive into the project, in one transaction.

    The router used to open the connection and pass it down, which is why the last
    statements of `api/projects.py` still lived there. The scan is one unit of work —
    discovering, marking missing sources, registering every candidate — so it owns the
    connection and reports progress through a callback for the streaming job.
    """
    root = Path(archive_dir)
    with connect() as conn:
        candidates = scan_archive(root)
        missing_ids = find_missing_page_ids(conn, project_id, candidates)
        report = ScanReport(found_files=len(candidates), missing=len(missing_ids))
        if confirm_missing:
            mark_missing_pages(conn, missing_ids)
        for index, candidate in enumerate(candidates, 1):
            register_candidate(conn, project_id, candidate, root, report, lang=lang)
            if on_progress is not None:
                on_progress(index, candidate, report)
    return report


def register_uploads(
    project_id: int, session_dir: str | Path, report: ScanReport, *, lang: str = "it"
) -> None:
    """Register a folder of uploaded files, in one transaction."""
    root = Path(session_dir)
    with connect() as conn:
        for candidate in scan_archive(root):
            register_candidate(conn, project_id, candidate, root, report, lang=lang)

