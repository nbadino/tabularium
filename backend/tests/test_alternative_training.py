from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app


def test_alternative_training_views(tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for index in range(2):
        Image.new("RGB", (400, 500), "white").save(archive / f"{index}.png")
    with TestClient(app) as client:
        project = client.post("/api/projects", json={"name": "alternatives", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{project}/scan")
        pages = client.get(f"/api/projects/{project}/pages").json()["items"]
        for page in pages:
            client.put(f"/api/pages/{page['id']}/annotations", json={"items": [{"label": "Text", "kind": "rect", "points": [[10, 10], [350, 100]], "content": "sample", "order_idx": 1}]})

        glm = client.post(f"/api/projects/{project}/datasets/glm-training", json={"approved_only": False})
        assert glm.status_code == 200, glm.text
        glm_files = glm.json()["files"]
        assert Path(glm_files["train"]).exists()
        assert json.loads(Path(glm_files["train"]).read_text())[0]["images"]

        deepseek = client.post(f"/api/projects/{project}/datasets/deepseek-training", json={"approved_only": False})
        assert deepseek.status_code == 200, deepseek.text
        row = json.loads(Path(deepseek.json()["files"]["train"]).read_text())[0]
        assert row["messages"][0]["role"] == "<|User|>"

        for adapter in ("dots-ocr", "unlimited-ocr", "mineru2.5"):
            result = client.post(f"/api/projects/{project}/datasets/{adapter}-training", json={"approved_only": False})
            assert result.status_code == 200, result.text
            assert result.json()["files"]["script"]
