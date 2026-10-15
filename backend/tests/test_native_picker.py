"""Test della scelta cartella con la finestra di sistema (`POST /api/system/fs/pick`).

La finestra vera non si può aprire in CI: si verifica il contratto dell'endpoint
— percorso scelto, annullamento, GUI non disponibile — sostituendo
`native_picker.pick_folder`. La logica di partenza del dialogo (`_initial_dir`)
si prova da sola, senza GUI.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import native_picker


def test_pick_returns_chosen_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(native_picker, "pick_folder", lambda start=None: str(tmp_path))
    with TestClient(app) as client:
        out = client.post("/api/system/fs/pick").json()
    assert out == {"path": str(tmp_path)}


def test_pick_cancelled_returns_null(monkeypatch):
    monkeypatch.setattr(native_picker, "pick_folder", lambda start=None: None)
    with TestClient(app) as client:
        out = client.post("/api/system/fs/pick").json()
    assert out == {"path": None}


def test_pick_unavailable_reports_501(monkeypatch):
    def boom(start=None):
        raise native_picker.PickerUnavailable("nessun display")

    monkeypatch.setattr(native_picker, "pick_folder", boom)
    with TestClient(app) as client:
        resp = client.post("/api/system/fs/pick")
    assert resp.status_code == 501
    assert "finestra di sistema non disponibile" in resp.json()["detail"]


def test_initial_dir_prefers_folder_then_parent(tmp_path):
    folder = tmp_path / "scans"
    folder.mkdir()
    file = folder / "page.png"
    file.write_bytes(b"x")

    assert native_picker._initial_dir(str(folder)) == str(folder)
    assert native_picker._initial_dir(str(file)) == str(folder)
    assert native_picker._initial_dir(str(tmp_path / "missing")) is None
    assert native_picker._initial_dir(None) is None
