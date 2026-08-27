"""Test M7: pseudo-labeling con motore OCR finto (rapidocr non richiesto)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

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
