"""Export ufficiale Paddle: layout COCO, VLM ERNIEKit e celle per PP-OCR rec.

Il dataset di riconoscimento è la parte che chiude il ciclo: ciò che un
annotatore corregge dentro una tabella deve poter tornare indietro come
campione di addestramento per il motore di riga locale.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.db import connect
from app.services import paddle_dataset


def _project_with_table(tmp_path: Path, *, verified: bool) -> int:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    for name in ("p01.png", "p02.png"):
        image = Image.new("RGB", (600, 400), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        for y in range(40, 360, 40):
            draw.text((30, y), "Abava (m. tank)   Ru   71", fill=(0, 0, 0))
        image.save(archive / name)
    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "R", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
        for page in pages:
            revision = client.get(f"/api/pages/{page['id']}/annotations").json()[
                "annotation_revision"
            ]
            res = client.put(
                f"/api/pages/{page['id']}/annotations",
                json={
                    "expected_revision": revision,
                    "items": [
                        {
                            "label": "Table",
                            "kind": "rect",
                            "points": [[20, 20], [580, 380]],
                            "content": "",
                            "order_idx": 1,
                            "confirmed": True,
                        }
                    ],
                },
            )
            block_id = res.json()["items"][0]["id"]
            client.put(
                f"/api/blocks/{block_id}/table",
                json={
                    "rows": 2,
                    "cols": 2,
                    "cells": [
                        {"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "Abava", "verified": verified},
                        {"r": 0, "c": 1, "rowspan": 1, "colspan": 1, "text": "Ru", "verified": verified},
                        {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "", "verified": True},
                        {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "71", "verified": verified},
                    ],
                    "phantom_cols": [],
                    "vlines": [0.0, 0.5, 1.0],
                    "hlines": [0.0, 0.5, 1.0],
                },
            )
        return pid


def test_cell_dataset_carries_verified_cells(tmp_path: Path):
    pid = _project_with_table(tmp_path / "ok", verified=True)
    report = paddle_dataset.build(pid, approved_only=True)

    out = Path(report["files"]["rec_data_dir"])
    lines = []
    for split in ("train", "val"):
        lines += [
            line.split("\t")
            for line in Path(report["files"][f"rec_{split}"]).read_text(encoding="utf-8").splitlines()
        ]
    # Due pagine × tre celle con testo: la cella vuota non è un campione.
    assert len(lines) == 6
    assert sorted({text for _, text in lines}) == ["71", "Abava", "Ru"]
    for image, _ in lines:
        assert (out / image).exists(), image
        assert (out / image).stat().st_size > 0
    assert report["counts"]["rec"]["train"] + report["counts"]["rec"]["val"] == 6
    # Il dizionario dei caratteri è quello del corpus, non uno generico.
    charset = Path(report["files"]["rec_dict"]).read_text(encoding="utf-8").split("\n")
    assert "A" in charset and "7" in charset


def test_cell_dataset_refuses_unverified_prefill(tmp_path: Path):
    """Il testo proposto dal prefill non è gold: non entra finché non è visto."""
    pid = _project_with_table(tmp_path / "raw", verified=False)
    report = paddle_dataset.build(pid, approved_only=True)
    assert report["counts"]["rec"] == {"train": 0, "val": 0}
    assert report["cells"]["unverified_skipped"] == 6
    assert any("nessuno l'ha verificato" in w for w in report["warnings"])

    # Senza il filtro l'export serve a misurare, e le celle ci sono tutte.
    loose = paddle_dataset.build(pid, approved_only=False)
    assert loose["counts"]["rec"]["train"] + loose["counts"]["rec"]["val"] == 6


def test_cell_crop_follows_the_grid_geometry(tmp_path: Path):
    """Il ritaglio della cella è quello che il riconoscitore vede in esercizio."""
    samples = paddle_dataset._cell_samples(
        {
            "rows": 2,
            "cols": 2,
            "vlines": [0.0, 0.5, 1.0],
            "hlines": [0.0, 0.5, 1.0],
            "cells": [
                {"r": 0, "c": 0, "rowspan": 1, "colspan": 2, "text": "testata"},
                {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "x"},
            ],
        },
        (100, 200, 300, 400),
    )
    by_cell = {(s["r"], s["c"]): s["bbox"] for s in samples}
    # La cella unita copre entrambe le colonne, non solo la propria traccia.
    left, top, right, bottom = by_cell[(0, 0)]
    assert left == 100 and right == 300
    assert top == 200 and bottom > 300
    # La cella in basso a destra parte da metà ritaglio, con lo stesso respiro.
    left, top, right, bottom = by_cell[(1, 1)]
    assert left < 200 and right == 300 and bottom == 400
