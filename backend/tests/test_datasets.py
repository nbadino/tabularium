"""Test M4: dataset builder (JSONL ms-swift, split per pagina, coordinate 0-1000)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import dataset_builder as builder


def _make_archive(tmp_path: Path, n: int = 2) -> Path:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    for i in range(n):
        Image.new("RGB", (1000, 1400), (210, 210, 210)).save(archive / f"{i:03}.png")
    return archive


def _setup(tmp_path: Path) -> tuple[int, int]:
    """Progetto con 2 pagine, ritorna (project_id, [page_ids])."""
    archive = _make_archive(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "D", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
        return pid, [p["id"] for p in pages]


def _annotate(client, page_id: int, items: list[dict]) -> None:
    client.put(f"/api/pages/{page_id}/annotations", json={"items": items})


def test_build_datasets(tmp_path: Path):
    pid, (p1, p2) = _setup(tmp_path / "ds")

    with TestClient(app) as client:
        # pagina 1: Title (testata, con contenuto) + Text (contenuto)
        _annotate(
            client,
            p1,
            [
                {"label": "Title", "kind": "rect", "points": [[10, 20], [900, 120]], "content": "HISTORIC SHIPPING INDEX", "order_idx": 1},
                {"label": "Text", "kind": "rect", "points": [[10, 130], [900, 900]], "content": "Maritime intelligence report.", "order_idx": 2},
            ],
        )
        # pagina 2: Text (contenuto) + Table (griglia)
        _annotate(
            client,
            p2,
            [
                {"label": "Text", "kind": "rect", "points": [[10, 10], [500, 300]], "content": "Ship arrivals.", "order_idx": 1},
                {"label": "Table", "kind": "rect", "points": [[10, 320], [900, 1200]], "content": "", "order_idx": 2},
            ],
        )
        blk_table = None
        for item in client.get(f"/api/pages/{p2}/annotations").json()["items"]:
            if item["label"] == "Table":
                blk_table = item
        assert blk_table is not None
        client.put(
            f"/api/blocks/{blk_table['id']}/table",
            json={
                "rows": 2,
                "cols": 3,
                "phantom_cols": [],
                "cells": [
                    {"r": 0, "c": 0, "rowspan": 1, "colspan": 3, "text": "Vessel"},
                    {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "Belle of the Seas"},
                    {"r": 1, "c": 1, "rowspan": 1, "colspan": 2, "text": "From Calcutta"},
                ],
            },
        )

        # build con 2 pagine -> 1 train, 1 val
        r = client.post(
            f"/api/projects/{pid}/datasets/build",
            json={"split_ratio": 0.5, "seed": 42},
        )
        assert r.status_code == 200, r.text
        report = r.json()
        assert report["pages"]["train"] == 1
        assert report["pages"]["val"] == 1

        counts = report["counts"]
        assert counts["layout"]["train"] == 1 and counts["layout"]["val"] == 1
        assert counts["text_rec"]["train"] + counts["text_rec"]["val"] == 3
        assert counts["table"]["train"] + counts["table"]["val"] == 1
        assert report["crops_generated"] >= 3

        # file JSONL coerenti (uno per famiglia e per split)
        base = Path(report["dataset_dir"])
        assert (base / "layout_train.jsonl").exists()
        assert (base / "layout_val.jsonl").exists()
        assert (base / "text_rec_train.jsonl").exists()
        assert (base / "table_train.jsonl").exists()

        layout_lines = [
            json.loads(l)
            for f in ("layout_train.jsonl", "layout_val.jsonl")
            for l in (base / f).read_text().splitlines()
        ]
        text_lines = [
            json.loads(l)
            for f in ("text_rec_train.jsonl", "text_rec_val.jsonl")
            for l in (base / f).read_text().splitlines()
        ]
        table_lines = [
            json.loads(l)
            for f in ("table_train.jsonl", "table_val.jsonl")
            for l in (base / f).read_text().splitlines()
        ]

        # formato ms-swift + immagini assolute esistenti
        for line in layout_lines + text_lines + table_lines:
            assert line["messages"][0]["content"].startswith("<image>")
            img = Path(line["images"][0])
            assert img.is_absolute() and img.exists(), img

        # layout: bbox 0..1000 e ordine di lettura
        layout = layout_lines[0]
        content = json.loads(layout["messages"][1]["content"])
        assert len(content) >= 2
        for item in content:
            assert all(0 <= v <= 1000 for v in item["bbox"])
            assert item["bbox"][0] < item["bbox"][2]
            assert item["bbox"][1] < item["bbox"][3]
        # pagina con Title: prima Title poi Text (ordine di lettura)
        if content[0]["label"] == "Title":
            assert [c["label"] for c in content] == ["Title", "Text"]

        # table: contenuto OTSL con fcel
        assert all("<fcel>" in t["messages"][1]["content"] for t in table_lines)

        # text_rec: i contenuti attesi sono presenti
        texts = [t["messages"][1]["content"] for t in text_lines]
        assert "HISTORIC SHIPPING INDEX" in texts
        assert "Maritime intelligence report." in texts

        # GET datasets restituisce il report persistito
        got = client.get(f"/api/projects/{pid}/datasets").json()
        assert got["built"] is True
        assert got["report"]["counts"]["table"]["train"] + got["report"]["counts"]["table"]["val"] == 1

        # Il campione pilot salvato può produrre uno snapshot isolato, senza
        # includere le altre pagine annotate del progetto.
        saved = client.post(f"/api/projects/{pid}/pilot-sample/save", json=[p1]).json()
        assert saved["pilot_pages"] == [p1]
        pilot = client.post(
            f"/api/projects/{pid}/datasets/build",
            json={"split_ratio": 0.5, "seed": 42, "pilot_only": True},
        )
        assert pilot.status_code == 200, pilot.text
        pilot_report = pilot.json()
        assert pilot_report["pilot_only"] is True
        assert pilot_report["pilot_pages"] == 1
        assert pilot_report["pages"]["with_blocks"] == 1


def test_build_unannotated_project(tmp_path: Path):
    pid, _pages = _setup(tmp_path / "empty")
    with TestClient(app) as client:
        r = client.post(f"/api/projects/{pid}/datasets/build", json={})
        assert r.status_code == 200
        report = r.json()
        assert report["pages"]["with_blocks"] == 0
        # split_ratio non valido -> 422 (vincolo Pydantic sulle lelt..lt bounds)
        assert client.post(
            f"/api/projects/{pid}/datasets/build", json={"split_ratio": 1.3}
        ).status_code == 422


def test_table_windows_are_row_aligned_and_do_not_cut_rowspans():
    grid = {
        "rows": 30,
        "cols": 2,
        "cells": [
            {"r": row, "c": col, "rowspan": 1, "colspan": 1, "text": str(row)}
            for row in range(30)
            for col in range(2)
        ],
    }
    # Un merge verticale rende il boundary 15 non sicuro.
    grid["cells"] = [cell for cell in grid["cells"] if not (cell["r"] == 14 and cell["c"] == 0)]
    grid["cells"].append(
        {"r": 14, "c": 0, "rowspan": 2, "colspan": 1, "text": "merged"}
    )
    windows = builder.table_band_windows(grid, rows_per_band=15, overlap=2)
    assert windows
    assert all(start != 15 and end != 15 for start, end in windows)
    assert windows[0][0] == 0 and windows[-1][1] == 30
    assert all(end > start for start, end in windows)


def test_dataset_exports_full_table_and_verified_row_bands(tmp_path: Path):
    pid, (page_id, _other) = _setup(tmp_path / "bands")
    rows, cols = 20, 3
    with TestClient(app) as client:
        _annotate(
            client,
            page_id,
            [
                {
                    "label": "Table",
                    "kind": "rect",
                    "points": [[50, 100], [950, 1300]],
                    "content": "",
                    "order_idx": 1,
                }
            ],
        )
        block = client.get(f"/api/pages/{page_id}/annotations").json()["items"][0]
        grid = {
            "rows": rows,
            "cols": cols,
            "cells": [
                {"r": row, "c": col, "text": f"r{row}c{col}"}
                for row in range(rows)
                for col in range(cols)
            ],
            "vlines": [0.0, 0.33, 0.66, 1.0],
            "hlines": [index / rows for index in range(rows + 1)],
        }
        saved = client.put(f"/api/blocks/{block['id']}/table", json=grid)
        assert saved.status_code == 200, saved.text

        response = client.post(
            f"/api/projects/{pid}/datasets/build",
            json={
                "split_ratio": 0.5,
                "table_band_rows": 8,
                "table_band_overlap": 2,
                "include_full_tables": True,
            },
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["table_variants"]["full"] == 1
        assert report["table_variants"]["bands"] == 3
        assert report["table_variants"]["without_boundaries"] == 0
        assert sum(report["counts"]["table"].values()) == 4
        assert report["table_strategy"]["experimental"] is True

        table_files = [
            Path(json.loads(line)["images"][0])
            for split in ("train", "val")
            for line in (
                Path(report["dataset_dir"]) / f"table_{split}.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        assert all(path.exists() for path in table_files)
        assert sum("_rows_" in path.name for path in table_files) == 3
