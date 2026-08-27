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
            {"bbox": [10.0, 20.0, 100.0, 60.0], "text": "LLOYD'S LIST", "score": 0.91},
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
        assert "LLOYD'S LIST" in texts
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
                {"bbox": [60.0, 20.0, 500.0, 60.0], "text": "LLOYD'S LIST", "score": 0.9},
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
    monkeypatch.setattr(prelabelmod, "_looks_like_table", lambda *_args: True)

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
