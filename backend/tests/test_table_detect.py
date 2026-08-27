"""Test del rilevatore di struttura per tabelle senza filetti.

Le tabelle sintetiche danno controllo esatto sulla verità: si compone una
pagina con colonne e righe a coordinate note e si verifica che il rilevatore
le ritrovi. Il test sulla scansione reale (se presente in `test/`) protegge il
comportamento sul corpus vero, dove i puntini di guida e la carta sporca sono
il problema effettivo.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from app.services import table_detect

REAL_SCAN = Path(__file__).resolve().parents[2] / "test" / "LSI_17186_015.tif"


def _synthetic_table(
    *,
    rows: int = 20,
    pitch: int = 40,
    col_x: tuple[int, ...] = (60, 300, 420, 560, 800),
    leaders: bool = False,
) -> Image.Image:
    """Tabella allineata a spazi: testo nero su bianco, nessun filetto."""
    width, height = col_x[-1] + 120, rows * pitch + 2 * pitch
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    for r in range(rows):
        y = pitch + r * pitch
        for c, x in enumerate(col_x):
            # Blocchi pieni di altezza riga: sostituiscono il testo e danno al
            # profilo di inchiostro la stessa forma che avrebbe con i caratteri.
            draw.rectangle([x, y + 6, x + 70, y + 26], fill=0)
        if leaders:
            # Puntini di guida fra la prima e la seconda colonna: saldano le due
            # parole e, senza soppressione, cancellano il confine.
            for dot_x in range(col_x[0] + 74, col_x[1] - 4, 10):
                draw.rectangle([dot_x, y + 22, dot_x + 3, y + 25], fill=0)
    return img


def test_detects_rows_and_columns_on_synthetic_table():
    rows = 20
    col_x = (60, 300, 420, 560, 800)
    detection = table_detect.detect_grid(_synthetic_table(rows=rows, col_x=col_x))

    assert detection.rows == rows
    # Cinque colonne di testo separate da bianco: quattro confini interni.
    assert detection.cols == len(col_x)

    width = _synthetic_table(rows=rows, col_x=col_x).width
    found = [round(v * width) for v in detection.vlines]
    # Il confine cade a metà del bianco fra due colonne, non sul bordo del testo:
    # i blocchi sono larghi 70 px, quindi il confine atteso è (fine, inizio)/2.
    for left, right in zip(col_x, col_x[1:]):
        expected = (left + 70 + right) // 2
        assert any(abs(f - expected) <= 8 for f in found), (expected, found)


def test_blank_separator_rows_do_not_become_table_rows():
    """Le righe bianche di gruppo sono respiro tipografico, non righe."""
    pitch, groups, per_group = 40, 4, 5
    col_x = (60, 300, 560)
    width, height = 700, (groups * (per_group * pitch + pitch)) + 2 * pitch
    img = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(img)
    y = pitch
    for _ in range(groups):
        for _ in range(per_group):
            for x in col_x:
                draw.rectangle([x, y + 6, x + 70, y + 26], fill=0)
            y += pitch
        y += pitch  # riga bianca di separazione fra gruppi

    detection = table_detect.detect_grid(img)
    assert detection.rows == groups * per_group


def test_leader_dots_suppression_recovers_the_bridged_boundary():
    """I puntini saldano due colonne: soppressi, il confine riappare."""
    col_x = (60, 300, 560)
    img = _synthetic_table(rows=20, col_x=col_x, leaders=True)

    with_suppression = table_detect.detect_grid(img, suppress_leaders=True)
    without = table_detect.detect_grid(img, suppress_leaders=False)

    assert with_suppression.diagnostics["leader_dots_suppressed"] > 0
    assert without.diagnostics["leader_dots_suppressed"] == 0
    # Senza soppressione le prime due colonne restano fuse in una sola.
    assert with_suppression.cols > without.cols


def test_every_boundary_carries_its_support():
    detection = table_detect.detect_grid(_synthetic_table())
    assert len(detection.column_support) == detection.cols + 1
    assert all(s >= 0 for s in detection.column_support)


def test_lines_are_normalised_and_monotonic():
    detection = table_detect.detect_grid(_synthetic_table())
    for lines in (detection.vlines, detection.hlines):
        assert lines == sorted(lines)
        assert all(0.0 <= v <= 1.0 for v in lines)
    assert len(detection.vlines) == detection.cols + 1
    assert len(detection.hlines) == detection.rows + 1


def test_table_save_preserves_and_validates_boundaries(tmp_path: Path):
    archive = tmp_path / "boundary-archive"
    archive.mkdir()
    Image.new("RGB", (400, 300), "white").save(archive / "page.png")
    with TestClient(app) as client:
        project_id = client.post(
            "/api/projects", json={"name": "Boundaries", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{project_id}/scan")
        page = client.get(f"/api/projects/{project_id}/pages").json()["items"][0]
        block = client.put(
            f"/api/pages/{page['id']}/annotations",
            json={
                "items": [
                    {
                        "label": "Table",
                        "kind": "rect",
                        "points": [[0, 0], [400, 300]],
                        "order_idx": 0,
                    }
                ]
            },
        ).json()["items"][0]
        payload = {
            "rows": 2,
            "cols": 2,
            "cells": [
                {"r": r, "c": c, "text": f"{r},{c}"}
                for r in range(2)
                for c in range(2)
            ],
            "phantom_cols": [],
            "vlines": [0.0, 0.4, 1.0],
            "hlines": [0.0, 0.55, 1.0],
        }
        saved = client.put(f"/api/blocks/{block['id']}/table", json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["grid"]["vlines"] == payload["vlines"]
        fetched = client.get(f"/api/blocks/{block['id']}/table").json()["grid"]
        assert fetched["hlines"] == payload["hlines"]

        payload["vlines"] = [0.0, 0.8, 0.7]
        assert client.put(
            f"/api/blocks/{block['id']}/table", json=payload
        ).status_code == 400


def test_blank_image_is_rejected_not_guessed():
    """Su una pagina vuota il rilevatore dichiara di non sapere, non inventa."""
    with pytest.raises(ValueError):
        table_detect.detect_grid(Image.new("L", (600, 400), 255))


def test_fill_cells_skips_blank_cells_without_calling_the_engine():
    """Le celle senza inchiostro sono vuote legittime: non vanno all'OCR."""
    calls: list[int] = []

    class CountingEngine:
        def recognize_line(self, image):
            calls.append(1)
            return "x", 0.9

    img = Image.new("L", (400, 200), 255)
    ImageDraw.Draw(img).rectangle([10, 10, 80, 40], fill=0)  # inchiostro in 1 cella
    cells, stats = table_detect.fill_cells(
        img,
        vlines=[0.0, 0.25, 0.5, 0.75, 1.0],
        hlines=[0.0, 0.5, 1.0],
        engine=CountingEngine(),
        pitch=40,
    )
    assert len(cells) == 8
    assert stats["blank"] == 7
    assert len(calls) == 1


@pytest.mark.skipif(not REAL_SCAN.exists(), reason="scansione di riferimento assente")
def test_real_lloyds_index_page():
    """Guardia sul corpus vero: LSI_17186_015 è una tabella a piena pagina.

    Valori attesi verificati a mano sulla scansione: passo tipografico di 40 px,
    79 righe di testo sotto il doppio filetto e i confini di colonna dei campi
    numerici, che sono quelli su cui la trascrizione sbaglia di più.
    """
    image = Image.open(REAL_SCAN).crop((0, 660, 2864, 3952))
    detection = table_detect.detect_grid(image)

    assert detection.diagnostics["pitch_px"] == 40
    assert detection.rows == 79
    assert detection.diagnostics["leader_dots_suppressed"] > 1000

    width = image.width
    found = [round(v * width) for v in detection.vlines]
    # Flg|Reg, Reg|Blt, Blt|Gross, Gross|Net, For|Latest Report.
    for expected in (806, 893, 960, 1098, 1227, 1964):
        assert any(abs(f - expected) <= 12 for f in found), (expected, found)


@pytest.mark.skipif(not REAL_SCAN.exists(), reason="scansione di riferimento assente")
def test_detect_endpoint_returns_a_draft_without_saving(tmp_path: Path):
    """L'endpoint propone una griglia ma non tocca il DB finché non si salva."""
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    Image.open(REAL_SCAN).crop((0, 660, 2864, 3952)).save(archive / "LSI_17186_015.png")

    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "P", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]
        saved = client.put(
            f"/api/pages/{page['id']}/annotations",
            json={
                "items": [
                    {
                        "label": "Table",
                        "kind": "rect",
                        "points": [[0, 0], [page["width"], page["height"]]],
                        "content": "",
                        "order_idx": 1,
                        "confirmed": False,
                    }
                ]
            },
        ).json()
        block_id = saved["items"][0]["id"]

        r = client.post(f"/api/blocks/{block_id}/table/detect", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["grid"]["rows"] > 50
        assert body["grid"]["cols"] >= 8
        assert len(body["column_support"]) == body["grid"]["cols"] + 1
        assert body["ocr"] is None

        # Nessuna griglia salvata: il rilevamento è una proposta.
        assert client.get(f"/api/blocks/{block_id}/table").json()["grid"] is None
