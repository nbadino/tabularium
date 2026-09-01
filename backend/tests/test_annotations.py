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

        cleared = client.delete(f"/api/pages/{page_id}/annotations")
        assert cleared.status_code == 200
        assert cleared.json()["deleted"] == 2
        assert client.get(f"/api/pages/{page_id}/annotations").json()["items"] == []

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


def test_autosave_preserves_prefill_drafts(tmp_path: Path):
    """L'autosave del canvas non deve cancellare le bozze di prefill.

    Le bozze non verificate non compaiono sul canvas, quindi non sono mai nel
    payload del salvataggio: dedurne la cancellazione dall'assenza distruggeva
    il lavoro del prefill — griglia tabellare compresa — al primo gesto
    dell'annotatore sull'immagine.
    """
    from app.db import connect

    pid, page_id = _setup_page(tmp_path / "drafts")
    with TestClient(app) as client:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO blocks (page_id,label,kind,points,content,order_idx,"
                "prefill_source,confirmed) VALUES (?,?,?,?,?,?,?,0)",
                (page_id, "Table", "rect", "[[10,10],[500,900]]", "", 1, "model:paddle"),
            )
            draft_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO tables (block_id, grid_json) VALUES (?,?)",
                (draft_id, '{"rows":3,"cols":2,"cells":[]}'),
            )
        revision = client.get(f"/api/pages/{page_id}/annotations").json()["annotation_revision"]
        res = client.put(
            f"/api/pages/{page_id}/annotations",
            json={
                "expected_revision": revision,
                "items": [
                    {
                        "label": "Text",
                        "kind": "rect",
                        "points": [[0, 0], [100, 100]],
                        "content": "",
                        "order_idx": 1,
                        "confirmed": True,
                    }
                ],
            },
        )
        assert res.status_code == 200, res.text
        ids = [b["id"] for b in client.get(f"/api/pages/{page_id}/annotations").json()["items"]]
        assert draft_id in ids
        assert client.get(f"/api/blocks/{draft_id}/table").json()["grid"] is not None


def test_draft_is_deleted_when_the_client_asks_by_name(tmp_path: Path):
    """La protezione delle bozze non deve rendere impossibile cancellarne una.

    Le tabelle stanno sul canvas: quando l'annotatore ne cancella una, il
    salvataggio lo dichiara in `deleted_ids` invece di lasciarlo dedurre
    dall'assenza. Un id che torna fra gli `items` — annullamento prima del
    salvataggio — vince sulla cancellazione.
    """
    from app.db import connect

    pid, page_id = _setup_page(tmp_path / "explicit")
    with TestClient(app) as client:
        with connect() as conn:
            ids = [
                int(
                    conn.execute(
                        "INSERT INTO blocks (page_id,label,kind,points,content,order_idx,"
                        "prefill_source,confirmed) VALUES (?,?,?,?,?,?,?,0)",
                        (page_id, label, "rect", "[[10,10],[500,900]]", "", i, "model:paddle"),
                    ).lastrowid
                )
                for i, label in enumerate(("Table", "Text"), start=1)
            ]
        table_id, text_id = ids
        revision = client.get(f"/api/pages/{page_id}/annotations").json()["annotation_revision"]
        res = client.put(
            f"/api/pages/{page_id}/annotations",
            json={
                "expected_revision": revision,
                "deleted_ids": [table_id, text_id],
                "items": [
                    {
                        "id": text_id,
                        "label": "Text",
                        "kind": "rect",
                        "points": [[10, 10], [500, 900]],
                        "content": "",
                        "order_idx": 1,
                        "confirmed": False,
                    }
                ],
            },
        )
        assert res.status_code == 200, res.text
        remaining = [b["id"] for b in res.json()["items"]]
        # La tabella era dichiarata cancellata e sparisce…
        assert table_id not in remaining
        # …il testo era dichiarato cancellato ma è tornato fra gli items: resta.
        assert text_id in remaining


def test_saving_reports_which_id_each_item_received(tmp_path: Path):
    """`items` è tutta la pagina, `assigned_ids` è il payload.

    Accoppiare per posizione i blocchi mandati con quelli tornati era corretto
    solo finché la pagina conteneva esattamente ciò che il canvas porta. Con
    una bozza di prefill in mezzo — che il canvas non manda ma il server
    elenca — il client si prendeva l'id di un altro blocco, e la modifica
    successiva finiva sulla riga sbagliata.
    """
    from app.db import connect

    pid, page_id = _setup_page(tmp_path / "assigned")
    with TestClient(app) as client:
        with connect() as conn:
            # Bozza con order_idx 1: nell'elenco del server viene PRIMA.
            draft_id = int(
                conn.execute(
                    "INSERT INTO blocks (page_id,label,kind,points,content,order_idx,"
                    "prefill_source,confirmed) VALUES (?,?,?,?,?,?,?,0)",
                    (page_id, "Text", "rect", "[[0,0],[10,10]]", "bozza", 1, "model:paddle"),
                ).lastrowid
            )
        revision = client.get(f"/api/pages/{page_id}/annotations").json()["annotation_revision"]
        res = client.put(
            f"/api/pages/{page_id}/annotations",
            json={
                "expected_revision": revision,
                "items": [
                    {
                        "label": "Table",
                        "kind": "rect",
                        "points": [[20, 20], [400, 400]],
                        "content": "",
                        "order_idx": 2,
                        "confirmed": True,
                    }
                ],
            },
        ).json()

        assigned = res["assigned_ids"]
        assert len(assigned) == 1
        assert assigned[0] != draft_id
        # Per posizione il client avrebbe preso la bozza: è il primo elemento.
        assert res["items"][0]["id"] == draft_id
        table = next(b for b in res["items"] if b["id"] == assigned[0])
        assert table["label"] == "Table"
