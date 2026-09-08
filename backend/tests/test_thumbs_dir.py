"""L'import di un PDF su un data root pulito.

Il primo utente che registra un PDF trova `thumbs/` inesistente: `scan.py`
salva lì il PNG sorgente della pagina prima di confermarla, quindi l'import
moriva con `FileNotFoundError` a metà transazione e la scansione registrava
zero pagine senza spiegare perché.
"""
from __future__ import annotations

import conftest  # noqa: F401  (imposta TABULARIUM_ROOT prima dei moduli app)

import pytest
from PIL import Image

from app import config
from app.services import pages as pagesvc


def test_ensure_dirs_creates_the_thumbs_directory(tmp_path, monkeypatch):
    """`ensure_dirs` prepara tutte le cartelle su cui il codice scrive."""
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")

    assert not (tmp_path / "thumbs").exists()
    config.ensure_dirs()
    assert (tmp_path / "thumbs").is_dir()


def test_pdf_source_png_is_writable_after_ensure_dirs(tmp_path, monkeypatch):
    """Il percorso su cui `scan.py` salva il PNG deve essere scrivibile.

    Non basta che la cartella esista in astratto: la prova è che il salvataggio
    reale, con lo stesso helper usato dall'import, vada a buon fine.
    """
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    config.ensure_dirs()

    target = pagesvc.pdf_image_path(1)
    assert target.parent == tmp_path / "thumbs"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(target, format="PNG")
    assert target.is_file()
