"""Sessioni bulk: il browser avvia, il backend persiste e continua."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


class FakeOcr:
    available = True
    name = "fake-ocr"

    def detect(self, _image):
        return [
            {"bbox": [10, 20, 180, 50], "text": "Recognized text", "score": 0.99},
        ]


def _project(client: TestClient, root: Path) -> tuple[int, list[int]]:
    archive = root / "archive"
    archive.mkdir(parents=True)
    for index in range(2):
        Image.new("RGB", (200, 300), "white").save(archive / f"p{index}.png")
    project_id = client.post(
        "/api/projects", json={"name": "Bulk", "archive_dir": str(archive)}
    ).json()["id"]
    client.post(f"/api/projects/{project_id}/scan")
    pages = client.get(f"/api/projects/{project_id}/pages").json()["items"]
    return project_id, [page["id"] for page in pages]


def test_bulk_run_persists_pages_and_exports_raw_and_reviewed(tmp_path, monkeypatch):
    from app.services import recognition

    monkeypatch.setattr(recognition.ocrmod, "OcrEngine", FakeOcr)
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        created = client.post(
            f"/api/projects/{project_id}/recognition-runs",
            json={
                "page_ids": page_ids,
                "engine": "ocr",
                "mode": "replace_drafts",
                "stop_policy": "none",
            },
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["id"]

        body = created.json()
        for _ in range(100):
            body = client.get(
                f"/api/projects/{project_id}/recognition-runs/{run_id}"
            ).json()
            if body["state"] not in {"queued", "running"}:
                break
            time.sleep(0.02)

        assert body["state"] == "finished"
        assert body["completed_pages"] == 2
        assert body["succeeded_pages"] == 2
        assert [item["state"] for item in body["items"]] == ["finished", "finished"]
        assert all(item["drafts"] == 1 for item in body["items"])

        raw = client.get(
            f"/api/projects/{project_id}/recognition-runs/{run_id}/export?scope=raw"
        ).json()
        assert raw["schema"] == "tabularium-recognition-export/v1"
        assert raw["pages"][0]["blocks"][0]["content"] == "Recognized text"

        reviewed = client.get(
            f"/api/projects/{project_id}/recognition-runs/{run_id}/export?scope=reviewed"
        ).json()
        assert reviewed["pages"][0]["blocks"][0]["confirmed"] == 0

        text_export = client.get(
            f"/api/projects/{project_id}/recognition-runs/{run_id}/export?scope=reviewed&format=text"
        )
        assert text_export.status_code == 200
        assert text_export.headers["content-type"].startswith("text/plain")
        assert "Recognized text" in text_export.text

        csv_export = client.get(
            f"/api/projects/{project_id}/recognition-runs/{run_id}/export?scope=reviewed&format=csv"
        )
        assert csv_export.status_code == 200
        assert csv_export.headers["content-type"].startswith("text/csv")
        assert "rel_path" in csv_export.text
        assert "Recognized text" in csv_export.text


def test_run_rejects_pages_from_another_project(tmp_path, monkeypatch):
    from app.services import recognition

    monkeypatch.setattr(recognition.ocrmod, "OcrEngine", FakeOcr)
    with TestClient(app) as client:
        first, _ = _project(client, tmp_path / "first")
        _second, other_pages = _project(client, tmp_path / "second")
        response = client.post(
            f"/api/projects/{first}/recognition-runs",
            json={"page_ids": [other_pages[0]], "engine": "ocr"},
        )
        assert response.status_code == 404


def test_release_inference_stops_managed_local_server(monkeypatch):
    from app.services import recognition

    calls: list[object] = []
    monkeypatch.setattr(
        recognition.inference,
        "save_inference_config",
        lambda payload: calls.append(payload),
    )
    from app.services import serve_manager

    monkeypatch.setattr(serve_manager, "stop", lambda: calls.append("stop"))

    recognition._release_inference("local")

    assert calls == [{"enabled": False}, "stop"]


def test_second_model_run_is_rejected_while_first_is_active(tmp_path):
    from app.db import connect
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        with connect() as conn:
            conn.execute(
                "INSERT INTO recognition_runs(project_id,state,engine,total_pages) "
                "VALUES (?,'running','model',1)",
                (project_id,),
            )
        with pytest.raises(HTTPException) as caught:
            recognition.create_run(
                project_id,
                [page_ids[0]],
                engine="model",
                stop_policy="none",
            )
        assert caught.value.status_code == 409
        assert "sessione #" in str(caught.value.detail)


def test_retry_creates_a_run_with_only_failed_pages(tmp_path, monkeypatch):
    from app.db import connect
    from app.services import recognition

    monkeypatch.setattr(recognition, "_start_worker", lambda _run_id: None)
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        with connect() as conn:
            old = conn.execute(
                "INSERT INTO recognition_runs(project_id,state,engine,mode,model_mode,total_pages) "
                "VALUES (?,'finished_with_errors','ocr','replace_drafts','native',2)",
                (project_id,),
            )
            conn.executemany(
                "INSERT INTO recognition_run_items(run_id,page_id,state) VALUES (?,?,?)",
                [
                    (old.lastrowid, page_ids[0], "finished"),
                    (old.lastrowid, page_ids[1], "failed"),
                ],
            )

        retried = recognition.retry_failed_run(int(old.lastrowid))
        assert retried["total_pages"] == 1
        assert [item["page_id"] for item in retried["items"]] == [page_ids[1]]
