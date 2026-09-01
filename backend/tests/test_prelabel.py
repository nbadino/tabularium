"""Test M7: pseudo-labeling con motore OCR finto (rapidocr non richiesto)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app


class FakeEngine:
    name = "rapidocr"
    available = True

    def detect(self, image):
        return [
            {"bbox": [10.0, 20.0, 100.0, 60.0], "text": "HISTORIC SHIPPING INDEX", "score": 0.91},
            {"bbox": [10.0, 70.0, 400.0, 110.0], "text": "Maritime intelligence.", "score": 0.85},
            {"bbox": [200.0, 120.0, 500.0, 160.0], "text": "low quality", "score": 0.40},
            {"bbox": [10.0, 130.0, 60.0, 135.0], "text": "tiny", "score": 0.80},  # h=5 px < min_size
        ]


def _get_page(client, tmp_path: Path) -> tuple[int, int]:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    Image.new("RGB", (900, 1300), (215, 215, 215)).save(archive / "p.png")
    pid = client.post("/api/projects", json={"name": "P", "archive_dir": str(archive)}).json()["id"]
    client.post(f"/api/projects/{pid}/scan")
    page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]
    return pid, page["id"]


def test_prelabel_requires_engine(tmp_path: Path, monkeypatch):
    """Senza motore OCR installato -> 400 con messaggio chiaro."""
    from app.api import prelabel as prelabelmod

    monkeypatch.setattr(prelabelmod.ocrmod, "available_engine", lambda: None)
    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path)
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "replace"},
        )
        assert r.status_code == 400
        assert "OCR" in r.json()["detail"]


def test_prelabel_inserts_filtered_blocks(tmp_path: Path, monkeypatch):
    from app.api import prelabel as prelabelmod
    from app.services import ocr as ocrmod

    monkeypatch.setattr(ocrmod, "OcrEngine", FakeEngine)

    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path)
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "replace", "confidence": 0.5, "min_size": 10},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["engine"] == "rapidocr"
        assert body["results"][0]["detected"] == 4
        assert body["results"][0]["inserted"] == 2  # filtrate: score 0.4 e altezza <10px

        blocks = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert len(blocks) == 2
        texts = {b["content"] for b in blocks}
        assert "HISTORIC SHIPPING INDEX" in texts
        assert "Maritime intelligence." in texts
        assert all(
            str(b.get("prefill_source") or b.get("provenance") or "").startswith("rapidocr")
            for b in blocks
        )
        assert all(b["label"] == "Text" for b in blocks)
        assert sorted(b["order_idx"] for b in blocks) == [1, 2]
        assert blocks[0]["points"][0] == [10.0, 20.0]

        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "merge", "confidence": 0.5},
        )
        assert r.status_code == 200
        assert len(client.get(f"/api/pages/{page_id}/annotations").json()["items"]) == 4

        client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "replace", "confidence": 0.5},
        )
        assert len(client.get(f"/api/pages/{page_id}/annotations").json()["items"]) == 2


def test_prelabel_replace_modes_protect_human_work(tmp_path: Path, monkeypatch):
    """replace_drafts cancella solo le bozze del prefill; replace_all tutto.

    Il prefill distruttivo senza distinzione cancellava anche blocchi manuali e
    griglie tabellari curate (ON DELETE CASCADE). Le tre modalità devono essere
    distinguibili e la risposta deve dichiarare cosa ha rimosso.
    """
    from app.services import ocr as ocrmod

    monkeypatch.setattr(ocrmod, "OcrEngine", FakeEngine)

    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path)

        # Due blocchi manuali: uno Tabella con griglia curata, uno testo.
        put = client.put(
            f"/api/pages/{page_id}/annotations",
            json={
                "items": [
                    {
                        "label": "Table",
                        "kind": "rect",
                        "points": [[50, 300], [850, 1100]],
                        "content": "",
                        "order_idx": 1,
                        "confirmed": True,
                    },
                    {
                        "label": "Title",
                        "kind": "rect",
                        "points": [[10, 20], [500, 60]],
                        "content": "HISTORIC SHIPPING INDEX",
                        "order_idx": 2,
                        "confirmed": True,
                    },
                ]
            },
        )
        assert put.status_code == 200, put.text
        table_block = next(b for b in put.json()["items"] if b["label"] == "Table")
        grid = {
            "rows": 2,
            "cols": 2,
            "cells": [
                {"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "Abidjan"},
                {"r": 0, "c": 1, "rowspan": 1, "colspan": 1, "text": "Li"},
                {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "Accra"},
                {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "Br"},
            ],
        }
        assert client.put(f"/api/blocks/{table_block['id']}/table", json=grid).status_code == 200

        # merge: aggiunge bozze, non tocca il lavoro umano.
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "merge", "confidence": 0.5},
        )
        assert r.status_code == 200, r.text
        assert r.json()["results"][0]["replaced_blocks"] == 0
        blocks = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert len(blocks) == 4

        # replace_drafts: via le bozze precedenti (che il prefill reinserisce
        # come nuove), restano i blocchi umani e la griglia.
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "replace_drafts", "confidence": 0.5},
        )
        assert r.status_code == 200, r.text
        res = r.json()["results"][0]
        assert res["replaced_blocks"] == 2
        assert res["replaced_grids"] == 0
        blocks = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert len(blocks) == 4
        manual = [b for b in blocks if b["prefill_source"] is None]
        assert [b["label"] for b in manual] == ["Table", "Title"]
        assert client.get(f"/api/blocks/{table_block['id']}/table").json()["grid"]["rows"] == 2

        # Una bozza di prefill che l'utente ha confermato non è più una bozza:
        # replace_drafts non la tocca.
        drafts = [b for b in blocks if b["prefill_source"]]
        assert len(drafts) == 2
        for d in drafts:
            assert client.patch(
                f"/api/blocks/{d['id']}", json={"confirmed": True}
            ).status_code == 200
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "replace_drafts", "confidence": 0.5},
        )
        assert r.status_code == 200
        assert r.json()["results"][0]["replaced_blocks"] == 0
        assert len(client.get(f"/api/pages/{page_id}/annotations").json()["items"]) == 6

        # replace_all: distruttivo, ma dichiarato nella risposta.
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page_id], "mode": "replace_all", "confidence": 0.5},
        )
        assert r.status_code == 200, r.text
        res = r.json()["results"][0]
        assert res["replaced_blocks"] == 6
        assert res["replaced_grids"] == 1
        blocks = client.get(f"/api/pages/{page_id}/annotations").json()["items"]
        assert len(blocks) == 2
        assert all(b["prefill_source"] for b in blocks)


def test_ocr_prelabel_promotes_register_to_table(tmp_path: Path, monkeypatch):
    """Su un registro allineato a spazi il prefill OCR produce una tabella.

    La pagina sintetica ha una testata in alto e un registro di 15 righe a 5
    colonne. L'OCR finto vede solo righe (le colonne fuse, come nella realtà):
    la promozione deve ricostruire la struttura dal ritaglio, non dalle righe.
    """
    from app.services import ocr as ocrmod

    class FakeRowEngine:
        name = "rapidocr"
        available = True

        def detect(self, image):
            lines = [
                {"bbox": [60.0, 20.0, 500.0, 60.0], "text": "HISTORIC SHIPPING INDEX", "score": 0.9},
                {"bbox": [60.0, 70.0, 400.0, 110.0], "text": "May 20, 1940.", "score": 0.9},
            ]
            for r in range(15):
                y = 400.0 + r * 40.0
                lines.append(
                    {"bbox": [60.0, y + 4, 870.0, y + 30], "text": "row", "score": 0.9}
                )
            return lines

        def recognize_line(self, image):
            return ("x", 0.9)

    # La pagina deve contenere davvero la tabella: la promozione passa dalla
    # geometria del ritaglio, non dai bbox dell'OCR.
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    img = Image.new("L", (950, 1300), 255)
    draw = ImageDraw.Draw(img)
    col_x = (60, 300, 420, 560, 800)
    for r in range(15):
        y = 400 + r * 40
        for x in col_x:
            draw.rectangle([x, y + 6, x + 70, y + 26], fill=0)
    draw.rectangle([60, 30, 300, 55], fill=0)  # testata
    draw.rectangle([60, 80, 260, 105], fill=0)  # data
    img.convert("RGB").save(archive / "LSI_test_001.png")

    monkeypatch.setattr(ocrmod, "OcrEngine", FakeRowEngine)

    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "T", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]

        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={"page_ids": [page["id"]], "mode": "replace", "confidence": 0.5},
        )
        assert r.status_code == 200, r.text
        body = r.json()["results"][0]
        assert body["tables"] == 1
        assert body["grids"] == 1

        blocks = client.get(f"/api/pages/{page['id']}/annotations").json()["items"]
        labels = sorted(b["label"] for b in blocks)
        assert labels == ["Table", "Text", "Text"]
        table = next(b for b in blocks if b["label"] == "Table")
        assert table["prefill_source"] == "rapidocr:table"
        # L'ordine di lettura mette la testata sopra il registro.
        assert max(b["order_idx"] for b in blocks if b["label"] == "Text") < table["order_idx"]

        grid = client.get(f"/api/blocks/{table['id']}/table").json()["grid"]
        assert grid["rows"] == 15
        assert grid["cols"] == 5
        filled = [c for c in grid["cells"] if c["text"].strip()]
        assert filled, "le celle con inchiostro devono avere il testo proposto"
        assert all(c["source"] == "ocr" and c["verified"] is False for c in filled)
        empty = [c for c in grid["cells"] if not c["text"].strip()]
        assert all(c["verified"] for c in empty)

        # Con la promozione disattivata si torna al comportamento di riga.
        r = client.post(
            f"/api/projects/{pid}/prelabel",
            json={
                "page_ids": [page["id"]],
                "mode": "replace",
                "confidence": 0.5,
                "table_promote": False,
            },
        )
        assert r.status_code == 200
        assert r.json()["results"][0]["tables"] == 0
        blocks = client.get(f"/api/pages/{page['id']}/annotations").json()["items"]
        assert all(b["label"] == "Text" for b in blocks)


def test_model_end2end_persists_table_without_second_call(tmp_path: Path, monkeypatch):
    from app.api import prelabel as prelabelmod
    from app.services import prefill as prefillsvc

    class FakeModel:
        model = "fake-monkey"

        def ping(self):
            return True

        def end2end(self, image):
            return [
                {
                    "bbox": [10, 20, 900, 980],
                    "label": "Table",
                    "content": "<fcel>Abidjan<fcel>Li<nl><fcel>Accra<fcel>Br",
                }
            ]

        def table_grid(self, *args, **kwargs):
            raise AssertionError("OTSL END2END valido: il fallback non deve partire")

    monkeypatch.setattr(prelabelmod.inference, "VllmClient", FakeModel)
    monkeypatch.setattr(prefillsvc, "_looks_like_table", lambda *_args: True)

    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path / "end2end")
        response = client.post(
            f"/api/projects/{pid}/prelabel",
            json={
                "page_ids": [page_id],
                "mode": "replace",
                "engine": "model",
                "model_mode": "end2end",
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["model_mode"] == "end2end"
        assert result["table_sources"] == ["end2end"]
        block = client.get(f"/api/pages/{page_id}/annotations").json()["items"][0]
        assert block["label"] == "Table"
        assert block["prefill_source"].endswith(":end2end")
        grid = client.get(f"/api/blocks/{block['id']}/table").json()["grid"]
        assert grid["rows"] == 2 and grid["cols"] == 2


def test_prelabel_stream_emits_block_events(tmp_path: Path, monkeypatch):
    """Lo stream SSE emette start → page → block… → page_done → end, e i
    blocchi emessi esistono già nel DB: ciò che la UI mostra progressivamente
    è ciò che esiste davvero."""
    import json as jsonlib

    from app.api import prelabel as prelabelmod
    from app.services import ocr as ocrmod

    monkeypatch.setattr(ocrmod, "OcrEngine", FakeEngine)

    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path)
        with client.stream(
            "POST",
            f"/api/projects/{pid}/prelabel/stream",
            json={"page_ids": [page_id], "mode": "replace", "confidence": 0.5, "min_size": 10},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            events = []
            for line in response.iter_lines():
                if line.startswith("data: "):
                    events.append(jsonlib.loads(line[len("data: "):]))

        types = [ev["type"] for ev in events]
        assert types[0] == "start"
        assert types[1] == "page"
        assert types[-1] == "end"
        assert types[-2] == "page_done"
        blocks = [ev for ev in events if ev["type"] == "block"]
        # Le due righe sopra soglia (la terza è sotto confidence, la quarta
        # sotto min_size) arrivano come eventi separati.
        assert [ev["block"]["label"] for ev in blocks] == ["Text", "Text"]
        assert blocks[0]["block"]["content"] == "HISTORIC SHIPPING INDEX"
        assert blocks[0]["block"]["order_idx"] == 1

        # Ciò che è stato emesso esiste già nel DB (con lo stesso id).
        for ev in blocks:
            r = client.get(f"/api/blocks/{ev['block']['id']}/crop")
            assert r.status_code == 200


def test_prelabel_stream_summary_matches_batch(tmp_path: Path, monkeypatch):
    """Lo stream e la risposta batch sono due consumatori della stessa
    orchestrazione: il riepilogo finale deve coincidere."""
    import json as jsonlib

    from app.api import prelabel as prelabelmod
    from app.services import ocr as ocrmod

    monkeypatch.setattr(ocrmod, "OcrEngine", FakeEngine)

    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path)
        # `merge`: nessun run cancella i blocchi dell'altro, così i due
        # riepiloghi sono comparabili alla pari.
        body = {"page_ids": [page_id], "mode": "merge", "confidence": 0.5, "min_size": 10}
        batch = client.post(f"/api/projects/{pid}/prelabel", json=body).json()["results"][0]

        with client.stream("POST", f"/api/projects/{pid}/prelabel/stream", json=body) as response:
            events = [
                jsonlib.loads(line[len("data: "):])
                for line in response.iter_lines()
                if line.startswith("data: ")
            ]
        streamed = next(ev["summary"] for ev in events if ev["type"] == "page_done")
        assert streamed == batch


def test_dedupe_drops_nested_same_label_blocks():
    """Le fasce contenute in una tabella più grande sono la stessa tabella.

    Riprodotto dalla pagina LSI_17186_015: il risultato ufficiale PaddleOCR-VL
    portava la colonna sinistra una volta intera (con testo) e tre volte a
    fasce. L'IoU non le riconosce come duplicati, il contenimento sì.
    """
    from app.services.prefill import _dedupe_model_items

    items = [
        {"bbox": [100, 684, 1229, 3818], "label": "Table", "content": "<table>…</table>"},
        {"bbox": [106, 688, 1229, 1269], "label": "Table", "content": ""},
        {"bbox": [103, 1292, 1226, 2091], "label": "Table", "content": ""},
        {"bbox": [100, 2126, 1217, 3814], "label": "Table", "content": ""},
        {"bbox": [1232, 680, 2695, 2083], "label": "Text", "content": "London Mar 8"},
    ]
    out = _dedupe_model_items(items)
    assert [i["bbox"] for i in out] == [[100, 684, 1229, 3818], [1232, 680, 2695, 2083]]


def test_dedupe_keeps_nested_blocks_of_a_different_class():
    """Una cosa dentro un'altra non è un duplicato se le classi differiscono."""
    from app.services.prefill import _dedupe_model_items

    items = [
        {"bbox": [0, 0, 1000, 1000], "label": "Table", "content": ""},
        {"bbox": [100, 100, 200, 200], "label": "Picture", "content": ""},
    ]
    assert len(_dedupe_model_items(items)) == 2
