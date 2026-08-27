"""Test M2: blocchi di annotazione (CRUD, bulk, ordine) e tassonomia label."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def _setup_page(tmp_path: Path) -> tuple[int, int]:
    """Crea progetto con 1 pagina, ritorna (project_id, page_id)."""
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    Image.new("RGB", (1200, 1600), (200, 200, 200)).save(archive / "p01.png")
    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "A", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]
        return pid, page["id"]


def test_labels_schema(tmp_path: Path):
    with TestClient(app) as client:
        pid, _ = _setup_page(tmp_path / "labels")
        body = client.get(f"/api/projects/{pid}/labels").json()
        names = [l["name"] for l in body["labels"]]
        # Numero e data del fascicolo sono classi distinte: il corpus ha più
        # formati di testata e separarle evita una regex per formato.
        assert "Issue-number" in names
        assert "Issue-date" in names
        # Classi pubbliche del parsing: il modello base le conosce già.
        assert "Title" in names
        assert "Page-header" in names
        assert "Table" in names
        table = next(l for l in body["labels"] if l["name"] == "Table")
        assert table["prompt_kind"] == "table"
        assert "OTSL" in table["prompt"]


def test_block_crud_and_bulk(tmp_path: Path):
    _pid, page_id = _setup_page(tmp_path / "annot")

    with TestClient(app) as client:
        # bulk: 2 blocchi (rect a 2 punti, polygon a 4 punti)
        items = [
            {
                "label": "Title",
                "kind": "rect",
                "points": [[10, 20], [500, 120]],
                "content": "",
                "order_idx": 1,
            },
            {
                "label": "Text",
                "kind": "polygon",
                "points": [[10, 130], [600, 130], [600, 800], [10, 800]],
                "content": "",
                "order_idx": 2,
            },
        ]
        r = client.put(f"/api/pages/{page_id}/annotations", json={"items": items})
        assert r.status_code == 200, r.text
        saved = r.json()["items"]
        assert len(saved) == 2
        assert saved[0]["points"] == items[0]["points"]

        # lettura ordinata
        got = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert [b["order_idx"] for b in got] == [1, 2]

        # patch di un blocco: label + punti
        bid = saved[0]["id"]
        r = client.patch(
            f"/api/blocks/{bid}",
            json={"label": "Headline", "points": [[5, 5], [600, 100]]},
        )
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated["label"] == "Headline"
        assert updated["points"] == [[5.0, 5.0], [600.0, 100.0]]

        # reorder: scambia order_idx
        client.patch(f"/api/blocks/{saved[1]['id']}", json={"order_idx": 0})
        got = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert got[0]["label"] == "Text"

        # delete
        assert client.delete(f"/api/blocks/{bid}").status_code == 200
        got = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert len(got) == 1

        # 404 su blocco inesistente
        assert client.delete("/api/blocks/999999").status_code == 404

    # --- tabella + crop + convenzioni ---------------------------------------
    with TestClient(app) as client:
        client.put(f"/api/pages/{page_id}/annotations", json={"items": items})
        blk = client.get(f"/api/pages/{page_id}/annotations").json()["items"][0]

        # griglia tabella con merge 2D
        grid = {
            "rows": 2,
            "cols": 3,
            "phantom_cols": [],
            "cells": [
                {"r": 0, "c": 0, "rowspan": 1, "colspan": 3, "text": "Vessel"},
                {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "Belle of the Seas"},
                {"r": 1, "c": 1, "rowspan": 1, "colspan": 2, "text": "From Calcutta"},
            ],
        }
        r = client.put(f"/api/blocks/{blk['id']}/table", json=grid)
        assert r.status_code == 200, r.text
        saved = r.json()
        assert saved["otsl"].startswith("<fcel>Vessel<lcel>")
        assert saved["grid"]["rows"] == 2

        # GET rilegge la stessa griglia
        got = client.get(f"/api/blocks/{blk['id']}/table").json()["grid"]
        assert got["cells"][0]["colspan"] == 3

        # Un autosave successivo del canvas conserva l'ID del blocco e quindi
        # non elimina la tabella collegata via ON DELETE CASCADE.
        current = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        bulk = {
            "items": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "kind": item["kind"],
                    "points": item["points"],
                    "content": item["content"],
                    "order_idx": item["order_idx"],
                    "confirmed": item["confirmed"],
                }
                for item in current
            ]
        }
        autosaved = client.put(f"/api/pages/{page_id}/annotations", json=bulk)
        assert autosaved.status_code == 200, autosaved.text
        assert autosaved.json()["items"][0]["id"] == blk["id"]
        assert client.get(f"/api/blocks/{blk['id']}/table").json()["grid"] is not None

        # griglia invalida (fuori bounds) -> 400
        bad = {"rows": 1, "cols": 1, "cells": [{"r": 2, "c": 0}]}
        assert client.put(f"/api/blocks/{blk['id']}/table", json=bad).status_code == 400

        # crop del blocco
        r = client.get(f"/api/blocks/{blk['id']}/crop")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert len(r.content) > 50

        # convenzioni: default 5, poi custom
        conv = client.get(f"/api/projects/{_pid}/conventions").json()["conventions"]
        assert len(conv) == 5 and all("id" in c for c in conv)
        custom = [
            {"id": "soft_hyphen", "label": "x", "checked": False},
        ]
        r = client.put(f"/api/projects/{_pid}/conventions", json={"conventions": custom})
        assert r.status_code == 200
        conv = client.get(f"/api/projects/{_pid}/conventions").json()["conventions"]
        assert len(conv) == 1 and conv[0]["checked"] is False
