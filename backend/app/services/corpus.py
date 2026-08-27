"""Statistiche riproducibili del corpus importato."""
from __future__ import annotations

from collections import Counter

from ..db import connect


def corpus_map(project_id: int) -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pages WHERE project_id=? ORDER BY rel_path, pdf_page", (project_id,)).fetchall()
    years = Counter((str(r["issue_date"] or "")[:4] or "unknown") for r in rows)
    page_types = Counter(str(r["page_type"] or "unclassified") for r in rows)
    statuses = Counter(str(r["status"] or "new") for r in rows)
    # Le pagine di uno stesso PDF sono distinte, quindi non vanno scambiate
    # per duplicati: la sorgente conta solo per immagini senza indice pagina.
    sources = Counter(str(r["abs_path"]) for r in rows if r["source_kind"] == "image")
    exact_duplicates = sum(max(0, count - 1) for count in sources.values())
    dimensions = Counter(f"{r['width']}x{r['height']}" for r in rows)
    # PDF multi-pagina e scansioni con stessa sorgente restano distinti, ma la
    # metrica evidenzia subito importazioni ripetute dello stesso file.
    return {
        "project_id": project_id,
        "pages": len(rows),
        "by_year": dict(sorted(years.items())),
        "by_page_type": dict(page_types),
        "by_status": dict(statuses),
        "by_source": dict(sources),
        "by_dimensions": dict(dimensions),
        "exact_duplicate_pages": exact_duplicates,
        "warnings": (["alcune pagine non hanno anno/data"] if years.get("unknown") else []),
    }
