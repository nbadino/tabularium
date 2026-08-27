"""Metadati di pagina dedotti dal nome file dell'archivio.

Le scansioni Lloyd's arrivano con un nome che codifica già testata, numero di
fascicolo e pagina — `LSI_17186_015.tif` = Lloyd's Shipping Index, n. 17.186,
pagina 15 — cioè esattamente i campi che il registro pagine tiene in `issue_no`,
`page_no` e `page_type`.

La data del fascicolo **non** si deduce dal nome file e non va mai presa dall'EXIF:
quello è il momento in cui l'archivio è stato digitalizzato (2018 per il corpus
attuale), non la data del giornale. Finisce in `meta_json.scan_date`, dove è un
dato utile e non inquina lo split per annata; `issue_date` resta vuota finché
non la si legge dalla testata o non la si compila a mano.

Il tipo di pagina non è una decorazione: decide quale ricetta di annotazione si
applica. Le pagine indice sono un'unica tabella larga (→ `Table` + OTSL), quelle
del Voyage Supplement sono colonne parallele di schede-nave (→ `Column` +
`List-item`, niente OTSL).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Prefissi di testata riconosciuti → tipo di pagina.
PAGE_TYPES: dict[str, str] = {
    "LSI": "index",
    "LSIVS": "voyage-supplement",
}

# `PREFISSO_numero_pagina` con estensione qualsiasi. Il prefisso più lungo vince
# (LSIVS prima di LSI), cosa che la ricerca esplicita sotto garantisce.
_PATTERN = re.compile(r"^(?P<prefix>[A-Za-z]+)[_-](?P<issue>\d+)[_-](?P<page>\d+)$")


@dataclass(frozen=True)
class FileMeta:
    """Ciò che il nome file dice con certezza. Campi sconosciuti a None."""

    issue_no: str | None = None
    page_no: str | None = None
    page_type: str | None = None
    publication: str | None = None

    def as_updates(self) -> dict[str, str]:
        """Solo i campi valorizzati, pronti per un UPDATE parziale."""
        return {
            key: value
            for key, value in (
                ("issue_no", self.issue_no),
                ("page_no", self.page_no),
                ("page_type", self.page_type),
            )
            if value
        }


def parse_filename(name: str | Path) -> FileMeta:
    """Estrae testata, numero e pagina dal nome file. Tollerante: mai un'eccezione."""
    stem = Path(str(name)).stem
    match = _PATTERN.match(stem)
    if not match:
        return FileMeta()

    prefix = match.group("prefix").upper()
    # Gli zeri iniziali sono impaginazione dell'archivio, non parte del numero.
    issue_no = match.group("issue").lstrip("0") or "0"
    page_no = match.group("page").lstrip("0") or "0"
    return FileMeta(
        issue_no=issue_no,
        page_no=page_no,
        page_type=PAGE_TYPES.get(prefix),
        publication=prefix,
    )
