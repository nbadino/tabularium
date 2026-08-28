"""Test M1: progetti, scansione archivio, pagine, metadati, anteprime."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def _make_archive(tmp_path: Path, n: int = 3) -> Path:
    """Crea una cartella archivio temporanea con immagini PNG sintetiche."""
    archive = tmp_path / "archive"
    (archive / "sub").mkdir(parents=True)
    for i in range(n):
        img = Image.new("RGB", (400 + i * 100, 600 + i * 50), (220, 220, 220))
        img.save(archive / f"page_{i:03}.png")
    # file non supportato, deve essere ignorato
    (archive / "notes.txt").write_text("ignored")
    img = Image.new("RGB", (300, 400), (50, 50, 50))
    img.save(archive / "sub" / "extra.png")
    return archive


def test_project_crud_and_scan(tmp_path: Path):
    archive = _make_archive(tmp_path)

    with TestClient(app) as client:
        # creazione progetto con archivio inesistente -> 400
        r = client.post(
            "/api/projects",
            json={"name": "Bad", "archive_dir": str(tmp_path / "nope")},
        )
        assert r.status_code == 400

        # creazione progetto
        r = client.post(
            "/api/projects",
            json={"name": "Tabularium 1904", "archive_dir": str(archive)},
        )
        assert r.status_code == 201, r.text
        project = r.json()
        pid = project["id"]
        assert project["pages_count"] == 0

        # list
        projects = client.get("/api/projects").json()["items"]
        assert any(p["id"] == pid for p in projects)

        # scan: 4 immagini in archivio (3 + 1 nella sottocartella)
        report = client.post(f"/api/projects/{pid}/scan").json()
        assert report["found_files"] == 4, report
        assert report["registered"] == 4, report
        assert report["unsupported"] == 0

        # pagine
        pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
        assert len(pages) == 4
        first = pages[0]
        assert first["width"] > 0 and first["height"] > 0
        assert first["status"] == "new"

        # update metadati
        r = client.patch(
            f"/api/pages/{first['id']}",
            json={
                "issue_date": "1904-03-08",
                "issue_no": "22135",
                "page_no": "1",
                "page_type": "shipping",
                "status": "annotated",
            },
        )
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated["issue_date"] == "1904-03-08"
        assert updated["page_type"] == "shipping"

        # Gli stati che certificano qualità non sono modificabili con una
        # PATCH generica: devono attraversare readiness/review/approve.
        assert client.patch(
            f"/api/pages/{first['id']}", json={"status": "approved"}
        ).status_code == 409
        assert client.patch(
            f"/api/pages/{first['id']}", json={"status": "exported"}
        ).status_code == 409

        # filtro per tipo
        pages = client.get(
            f"/api/projects/{pid}/pages", params={"page_type": "shipping"}
        ).json()["items"]
        assert len(pages) == 1

        # thumbnail e preview serviti
        r = client.get(f"/api/pages/{first['id']}/thumbnail")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        r = client.get(f"/api/pages/{first['id']}/preview")
        assert r.status_code == 200

        # pagina inesistente -> 404
        assert client.get("/api/pages/999999/thumbnail").status_code == 404

        # seconda scansione: nessun duplicato
        report = client.post(f"/api/projects/{pid}/scan").json()
        assert report["registered"] == 0
        assert report["duplicates"] == 4

        # delete richiede conferma esplicita
        assert client.delete(f"/api/projects/{pid}").status_code == 400
        assert client.delete(f"/api/projects/{pid}?confirm=true").status_code == 200
        assert client.get(f"/api/projects/{pid}").status_code == 404
