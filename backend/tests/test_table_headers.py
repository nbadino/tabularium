"""Libreria di intestazioni tabella riutilizzabili, condivisa nel progetto."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def _project(tmp_path: Path) -> int:
    archive = tmp_path / "archive"
    archive.mkdir()
    with TestClient(app) as client:
        response = client.post(
            "/api/projects",
            json={"name": "Headers", "archive_dir": str(archive)},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]


def test_known_table_header_crud(tmp_path: Path):
    project_id = _project(tmp_path)
    payload = {
        "name": "Shipping register",
        "rows": 2,
        "cols": 3,
        "cells": [
            {"r": 0, "c": 0, "rowspan": 2, "colspan": 1, "text": "Vessel"},
            {"r": 0, "c": 1, "rowspan": 1, "colspan": 2, "text": "Year"},
            {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "Built"},
            {"r": 1, "c": 2, "rowspan": 1, "colspan": 1, "text": "Gross"},
        ],
    }
    with TestClient(app) as client:
        created = client.post(f"/api/projects/{project_id}/table-headers", json=payload)
        assert created.status_code == 201, created.text
        item = created.json()
        assert item["name"] == "Shipping register"
        assert item["cells"][0]["rowspan"] == 2

        listed = client.get(f"/api/projects/{project_id}/table-headers")
        assert listed.status_code == 200
        assert [entry["id"] for entry in listed.json()["items"]] == [item["id"]]

        # Lo stesso nome sostituisce la voce precedente invece di duplicarla.
        payload["name"] = "  shipping register  "
        replaced = client.post(f"/api/projects/{project_id}/table-headers", json=payload)
        assert replaced.status_code == 201
        replaced_item = replaced.json()
        listed = client.get(f"/api/projects/{project_id}/table-headers").json()["items"]
        assert len(listed) == 1
        assert listed[0]["name"] == "shipping register"

        deleted = client.delete(f"/api/projects/{project_id}/table-headers/{replaced_item['id']}")
        assert deleted.status_code == 200
        assert client.get(f"/api/projects/{project_id}/table-headers").json()["items"] == []


def test_known_table_header_rejects_empty_name(tmp_path: Path):
    project_id = _project(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/projects/{project_id}/table-headers",
            json={"name": "   ", "rows": 1, "cols": 1, "cells": []},
        )
        assert response.status_code == 422
