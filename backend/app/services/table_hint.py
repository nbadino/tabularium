"""Hint di schema tabella dichiarato dal progetto.

Il ricercatore descrive l'intestazione del registro (es. «Year e Tons sono
unite su una riga sopra»), Tabularium lo accoda al prompt tabella dei modelli
che lo accettano (`ModelCapabilities.accepts_prompt_hint`). È una
configurazione di **progetto**, non di modello: lo schema vale per tutte le
pagine dello stesso genere.

Il hint è un suggerimento, non una verità: i motori con prompt di protocollo o
pipeline nativa lo ignorano, e chi lo accetta può comunque non seguirlo. Per
questo resta affiancato dalla riparazione deterministica dell'intestazione
(`services/table_headers.py`), che non dipende dall'obbedienza del modello.
"""
from __future__ import annotations

MAX_LENGTH = 2000
_KEY = "table_hint"


def get_table_hint(settings: dict | None) -> str:
    """Hint salvato nel progetto, normalizzato; stringa vuota se assente."""
    return str((settings or {}).get(_KEY) or "").strip()


def clean_table_hint(value: object) -> str:
    """Normalizza un hint in ingresso (trim). Il tetto di lunghezza è
    validato dallo schema Pydantic, non troncato in silenzio."""
    return str(value or "").strip()
