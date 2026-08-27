"""Test M6: metriche di valutazione e pipeline di valutazione con client finto."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import evaluate as evmod
from app.services import inference as infmod


# --- metriche -----------------------------------------------------------------
def test_cer_wer():
    assert evmod.cer("abc", "abc") == 0.0
    assert abs(evmod.cer("abc", "abd") - 1 / 3) < 1e-9
    assert abs(evmod.wer("hello world", "hello there world") - 0.5) < 1e-9
    assert evmod.cer("", "") == 0.0


def test_iou():
    assert abs(evmod.iou([0, 0, 100, 100], [50, 0, 150, 100]) - 1 / 3) < 1e-9
    assert evmod.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_order_metrics():
    o = evmod.order_metrics(["Text", "Table"], ["Text", "Table"])
    assert o["exact"] is True and o["levenshtein_norm"] == 0.0
    o2 = evmod.order_metrics(["Text", "Table"], ["Table", "Text"])
    assert o2["exact"] is False and o2["levenshtein_norm"] == 1.0


def test_layout_match():
    gt = [
        {"bbox": [0, 0, 100, 100], "label": "Text"},
        {"bbox": [200, 0, 300, 100], "label": "Text"},
    ]
    pred = [
        {"bbox": [5, 5, 95, 95], "label": "Text"},
        {"bbox": [210, 0, 300, 100], "label": "Text"},
    ]
    m = evmod.layout_match(gt, pred)
    assert m["matched"] == 2
    assert m["precision"] == 1.0 and m["recall"] == 1.0


def test_table_metrics():
    grid = {
        "rows": 2,
        "cols": 2,
        "cells": [
            {"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "A"},
            {"r": 0, "c": 1, "rowspan": 1, "colspan": 1, "text": "B"},
            {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "C"},
            {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "D"},
        ],
    }
    from app.services.otsl import grid_to_otsl

    m = evmod.table_metrics(grid, grid_to_otsl(grid))
    assert m["structure_ok"] is True
    # struttura cambiata -> struttura fallisce
    bad = {
        "rows": 2,
        "cols": 2,
        "cells": [
            {"r": 0, "c": 0, "rowspan": 1, "colspan": 2, "text": "AB"},
            {"r": 1, "c": 0, "rowspan": 1, "colspan": 2, "text": "CD"},
        ],
    }
    m2 = evmod.table_metrics(grid, grid_to_otsl(bad))
    assert m2["structure_ok"] is False


def test_html_to_otsl_preserves_headers_and_spans():
    from app.services.otsl import html_to_otsl, otsl_to_grid

    html = (
        "<table><tr><th colspan='2'>Vessel</th></tr>"
        "<tr><td rowspan='2'>Abidjan</td><td>18</td></tr>"
        "<tr><td>27</td></tr></table>"
    )
    grid = otsl_to_grid(html_to_otsl(html))
    assert grid["rows"] == 3 and grid["cols"] == 2
    assert grid["cells"][0]["colspan"] == 2
    assert any(c["text"] == "Abidjan" and c["rowspan"] == 2 for c in grid["cells"])


# --- pipeline con client finto ------------------------------------------------
def _setup(tmp_path: Path) -> tuple[int, int]:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    Image.new("RGB", (900, 1300), (215, 215, 215)).save(archive / "p.png")
    with TestClient(app) as client:
        pid = client.post(
            "/api/projects", json={"name": "E", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]
        client.put(
            f"/api/pages/{page['id']}/annotations",
            json={
                "items": [
                    {"label": "Title", "kind": "rect", "points": [[10, 10], [890, 90]], "content": "LLOYD'S LIST", "order_idx": 1},
                    {"label": "Text", "kind": "rect", "points": [[10, 100], [890, 900]], "content": "Maritime intelligence.", "order_idx": 2},
                ]
            },
        )
        client.post(f"/api/projects/{pid}/datasets/build", json={"split_ratio": 0.5})
        return pid, page["id"]


class FakeClient:
    def __init__(self, url=None, model=None, timeout=0, max_retries=0):
        self.url = url or "http://fake"
        self.model = model or "fake"

    def ping(self):
        return True

    def layout(self, image):
        return [
            {"bbox": [11.0, 8.0, 989.0, 69.0], "label": "Title"},
            {"bbox": [11.0, 77.0, 989.0, 692.0], "label": "Text"},
        ]

    def recognize(self, image, label):
        return "LLOYD'S LIST" if label == "Title" else "Maritime intelligence."


def test_evaluate_project(tmp_path: Path, monkeypatch):
    with TestClient(app) as client:
        archive = tmp_path / "archive2"
        archive.mkdir(parents=True)
        Image.new("RGB", (900, 1300), (215, 215, 215)).save(archive / "p.png")
        Image.new("RGB", (900, 1300), (215, 215, 215)).save(archive / "q.png")
        pid = client.post(
            "/api/projects", json={"name": "E2", "archive_dir": str(archive)}
        ).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
        for i, pg in enumerate(pages):
            client.put(
                f"/api/pages/{pg['id']}/annotations",
                json={
                    "items": [
                        {"label": "Text", "kind": "rect", "points": [[10, 100], [890, 900]], "content": f"Maritime intelligence {i}.", "order_idx": 1},
                    ]
                },
            )
        client.post(f"/api/projects/{pid}/datasets/build", json={"split_ratio": 0.5})

    monkeypatch.setattr(infmod, "VllmClient", FakeClient)
    report = evmod.evaluate_project(pid, with_text=True)
    assert report["pages_evaluated"] >= 1
    agg = report["aggregates"]
    assert "layout" in agg and "text" in agg and "tables" in agg
    assert agg["text"]["n"] >= 1
    assert Path(report["eval_dir"]).exists() and (Path(report["eval_dir"]) / "report.json").exists()
    first_gt = report["pages"][0]["gt_items"][0]
    assert first_gt["bbox"] == [11, 77, 989, 692]
    assert first_gt["source_bbox"] == [10.0, 100.0, 890.0, 900.0]

    # progetto senza annotazioni -> errore chiaro
    try:
        evmod.evaluate_project(pid + 999, with_text=False)
        raise AssertionError("atteso ValueError")
    except ValueError:
        pass

# --- risoluzione immagine inviata al modello ----------------------------------
def test_min_pixels_upscales_and_never_shrinks():
    """`min_pixels` è un MINIMO, come in `load_image` del repo ufficiale.

    Il pipeline ufficiale lo passa a 1003520 per la chiamata di layout: serve a
    ingrandire le immagini piccole, non a ridurre le grandi. Confonderlo con
    `max_pixels 1003520` degli iperparametri di training è un errore facile e
    porta a ridurre immagini che andavano lasciate stare.
    """
    from PIL import Image

    from app.services.inference import LAYOUT_MIN_PIXELS, _fit_pixels

    small = Image.new("RGB", (400, 300))
    grown = _fit_pixels(small, min_pixels=LAYOUT_MIN_PIXELS)
    # Il troncamento a int dei lati (come nel `load_image` ufficiale) può
    # lasciare una frazione di percento sotto la soglia: si verifica che sia
    # cresciuta fino al target, non che lo superi al pixel.
    assert grown.width * grown.height >= 0.99 * LAYOUT_MIN_PIXELS
    assert abs(grown.width / grown.height - 4 / 3) < 0.01

    big = Image.new("RGB", (2864, 3952))
    assert _fit_pixels(big, min_pixels=LAYOUT_MIN_PIXELS) is big


def test_layout_caps_oversized_archive_scans():
    """Il layout impone un tetto che il codice ufficiale non ha.

    Una pagina Lloyd's è 11.3 MP e a quella taglia il modello restituisce due
    riquadri sovrapposti; a 2 MP restituisce Title, numero, data e corpo. Il
    tetto vale solo per il layout: sui ritagli di testo e tabella ridurre
    perderebbe i caratteri.
    """
    from PIL import Image

    from app.services.inference import LAYOUT_MAX_PIXELS, _fit_pixels

    scan = Image.new("RGB", (2864, 3952))
    capped = _fit_pixels(scan, max_pixels=LAYOUT_MAX_PIXELS)
    assert capped.width * capped.height <= LAYOUT_MAX_PIXELS
    assert abs(capped.width / capped.height - scan.width / scan.height) < 0.01


def test_end2end_keeps_table_otsl_content_and_caps_page():
    from PIL import Image

    from app.services.inference import (
        END2END_MAX_TOKENS,
        END2END_PROMPT,
        LAYOUT_MAX_PIXELS,
        VllmClient,
    )

    seen: dict = {}

    class FakeClient(VllmClient):
        def _chat(self, image, prompt, max_tokens=4096, min_pixels=None, **kwargs):
            seen.update(pixels=image.width * image.height, prompt=prompt, max_tokens=max_tokens)
            return (
                "[{'bbox':[10,20,900,980],'label':'Table',"
                "'content':'<fcel>Abidjan<fcel>Li<nl><fcel>Accra<fcel>Br'}]"
            )

    items = FakeClient(url="http://127.0.0.1:9/v1").end2end(
        Image.new("RGB", (2864, 3952))
    )
    assert seen["pixels"] <= LAYOUT_MAX_PIXELS
    assert seen["prompt"] == END2END_PROMPT
    assert seen["max_tokens"] == END2END_MAX_TOKENS
    assert items[0]["label"] == "Table"
    assert items[0]["content"].startswith("<fcel>Abidjan")


def test_table_grid_repeats_the_header_on_every_band():
    """Ogni banda dopo la prima riceve l'intestazione in testa, e la riga
    riletta viene scartata.

    Il modello ancora la struttura delle colonne sull'intestazione: misurato
    sulla stessa tabella, la banda che la contiene esce a 8 colonne e le
    successive a 10, 15 e 17. Cucire l'intestazione in cima a ogni banda le
    riporta tutte a 8 — ma allora la riga di intestazione torna in ogni esito e
    va tolta, altrimenti compare a metà tabella.
    """
    from PIL import Image

    from app.services.inference import VllmClient

    heights: list[int] = []

    class FakeClient(VllmClient):
        def _chat(self, image, prompt, max_tokens=4096, min_pixels=None):
            heights.append(image.height)
            return "<fcel>Vessel<fcel>Flg<nl><fcel>Abagur<fcel>Ru<nl>"

    client = FakeClient(url="http://127.0.0.1:9/v1")
    image = Image.new("RGB", (600, 400), "white")
    # Confini: riga 0 alta 40 px (intestazione), poi tre righe da 120.
    grid = client.table_grid(
        image,
        row_bounds=[0, 40, 160, 280, 400],
        rows_per_band=2,
        header_rows=1,
    )

    assert len(heights) == 2, heights
    # La seconda banda è più alta della sua fetta: porta l'intestazione cucita.
    assert heights[1] > heights[0] - 40

    # Prima banda: intestazione + 1 riga. Seconda: intestazione scartata, 1 riga.
    assert grid["rows"] == 3
    assert grid["cols"] == 2
    texts = [c["text"] for c in grid["cells"] if c["r"] == 0]
    assert texts == ["Vessel", "Flg"]


def test_table_grid_normalises_to_the_modal_column_count():
    """Una banda mal parsata non deve gonfiare l'intera griglia.

    Normalizzando al massimo osservato, una sola banda che restituisce 17
    colonne trasforma una tabella da 9 in una da 17 piena di celle vuote
    (osservato sul corpus). La moda è robusta a quel caso; il testo che sfora
    finisce nell'ultima cella invece di essere buttato.
    """
    from PIL import Image

    from app.services import otsl
    from app.services.inference import VllmClient

    calls = {"n": 0}

    class FakeClient(VllmClient):
        def _chat(self, image, prompt, max_tokens=4096, min_pixels=None):
            calls["n"] += 1
            body = (
                "<fcel>a<fcel>b<fcel>c<fcel>d<fcel>e<nl>"
                if calls["n"] == 2
                else "<fcel>a<fcel>b<nl>"
            )
            return "<fcel>H1<fcel>H2<nl>" + body

    client = FakeClient(url="http://127.0.0.1:9/v1")
    grid = client.table_grid(
        Image.new("RGB", (600, 400), "white"),
        row_bounds=[0, 40, 160, 280, 400],
        rows_per_band=1,
    )

    assert calls["n"] == 4, calls
    assert grid["cols"] == 2, grid["cols"]
    # Niente testo perso: l'eccedenza è confluita nell'ultima colonna.
    joined = " ".join(c["text"] for c in grid["cells"])
    for letter in ("c", "d", "e"):
        assert letter in joined, joined
    assert otsl.grid_to_otsl(grid)


def test_table_grid_without_row_bounds_falls_back_to_fixed_bands():
    """Senza confini di riga noti si taglia comunque, a intervalli regolari."""
    from PIL import Image

    from app.services.inference import VllmClient

    class FakeClient(VllmClient):
        def _chat(self, image, prompt, max_tokens=4096, min_pixels=None):
            return "<fcel>a<fcel>b<nl>"

    client = FakeClient(url="http://127.0.0.1:9/v1")
    grid = client.table_grid(Image.new("RGB", (800, 3000), "white"))
    assert grid["rows"] >= 2
    assert grid["cols"] == 2


def test_ping_uses_the_client_timeout():
    """Il ping di disponibilità non deve restare appeso al timeout di default.

    L'annotatore interroga i motori disponibili all'apertura della pagina con un
    timeout di pochi secondi: se `ping` ne usasse uno fisso da venti, la pagina
    resterebbe bloccata ogni volta che il server di inferenza è spento.
    """
    import requests

    from app.services.inference import VllmClient

    seen: dict = {}

    def fake_get(url, timeout=None):
        seen["timeout"] = timeout
        raise requests.exceptions.ConnectTimeout("spento")

    original = requests.get
    requests.get = fake_get
    try:
        assert VllmClient(url="http://127.0.0.1:9/v1", timeout=2).ping() is False
    finally:
        requests.get = original
    assert seen["timeout"] == 2
