"""Controlli aritmetici: le somme della tabella indicano le celle da guardare."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.table_checks import check_grid


def _grid(rows: list[list[str]]) -> dict:
    return {"rows": len(rows), "cols": len(rows[0]),
            "cells": [{"r": r, "c": c, "text": t} for r, row in enumerate(rows) for c, t in enumerate(row)]}


COUNTRIES = [
    ["COUNTRIES", "Merchandise.", "Treasure.", "Total."],
    ["Austria", "34,356", "1,036", "35,392"],
    ["France", "250,123", "8,280", "258,403"],
    ["Total", "284,479", "9,316", "293,795"],
]


def test_a_correct_table_balances_everywhere():
    out = check_grid(_grid(COUNTRIES))
    assert (out["passed"], out["failed"], out["suspects"]) == (6, 0, [])


def test_one_misread_digit_is_pinpointed():
    rows = [r[:] for r in COUNTRIES]
    rows[2][3] = "258,493"  # 4 letto come 9
    out = check_grid(_grid(rows))
    assert out["failed"] == 2  # la riga di France e la colonna Total
    assert out["suspects"] == [[2, 3]]


def test_an_unreadable_number_is_the_suspect():
    rows = [r[:] for r in COUNTRIES]
    rows[1][1] = "34,3S6"
    assert check_grid(_grid(rows))["suspects"] == [[1, 1]]


def test_a_header_shifted_by_one_column_raises_no_alarm():
    """Con l'intestazione scivolata la terna non torna su nessuna riga: è una
    struttura sbagliata, non trenta errori da segnalare."""
    rows = [r[:] for r in COUNTRIES]
    rows[0] = ["Merchandise.", "Treasure.", "Total.", ""]
    out = check_grid(_grid(rows))
    assert all(c["total"][1] != 2 or c["cells"][0][1] != 0 for c in out["checks"])
    assert out["suspects"] == []


def test_a_long_failing_column_flags_its_total_not_every_row():
    """Una colonna di dodici voci che non torna, senza una cifra sola che la
    aggiusti: si segnala il totale, non dodici celle. La colonna accanto torna
    e prova che la riga «Total» somma le voci sopra."""
    items = [[f"Item {i}", str(100 + i), str(200 + i)] for i in range(12)]
    items[3][2] = "271"  # 203 → 271: due cifre, nessun colpevole unico
    total = ["Total", str(sum(100 + i for i in range(12))), str(sum(200 + i for i in range(12)))]
    out = check_grid(_grid([["ARTICLES", "1910-11.", "1911-12."], *items, total]))
    assert (out["passed"], out["failed"]) == (1, 1)
    assert out["suspects"] == [[13, 2]]


def test_endpoint_checks_a_grid_without_saving_it():
    rows = [r[:] for r in COUNTRIES]
    rows[2][3] = "258,493"
    with TestClient(app) as client:
        r = client.post("/api/tables/checks", json={**_grid(rows), "phantom_cols": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suspects"] == [[2, 3]]
    wrong = next(c for c in body["checks"] if not c["ok"] and c["total"] == [2, 3])
    assert (wrong["kind"], wrong["cells"]) == ("row", [[2, 1], [2, 2]])


def test_page_list_counts_failing_sums_and_follows_the_fix(tmp_path):
    """La lista delle pagine sa quali pagine hanno somme sbagliate; corretta la
    cifra, il conto scende a zero (l'esito in cache segue il contenuto)."""
    from PIL import Image

    archive = tmp_path / "archive"
    archive.mkdir()
    Image.new("RGB", (800, 1000), (200, 200, 200)).save(archive / "p01.png")
    rows = [r[:] for r in COUNTRIES]
    rows[2][3] = "258,493"
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "S", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        page = client.get(f"/api/projects/{pid}/pages").json()["items"][0]["id"]
        client.put(f"/api/pages/{page}/annotations", json={"items": [
            {"label": "Table", "kind": "rect", "points": [[10, 10], [700, 900]], "content": "", "order_idx": 1}]})
        block = client.get(f"/api/pages/{page}/annotations").json()["items"][0]["id"]

        assert client.put(f"/api/blocks/{block}/table", json={**_grid(rows), "phantom_cols": []}).status_code == 200
        sums = client.get(f"/api/projects/{pid}/sum-failures").json()["pages"]
        # tre somme lungo le righe (Merchandise + Treasure = Total) e tre
        # lungo le colonne (le voci che sommano alla riga «Total»)
        assert sums == {str(page): {"checks": 6, "failed": 2, "rows": 3, "cols": 3}}

        rows[2][3] = "258,403"
        client.put(f"/api/blocks/{block}/table", json={**_grid(rows), "phantom_cols": []})
        sums = client.get(f"/api/projects/{pid}/sum-failures").json()["pages"]
        assert sums == {str(page): {"checks": 6, "failed": 0, "rows": 3, "cols": 3}}
