"""Controlli aritmetici: le somme della tabella indicano le celle da guardare."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from fractions import Fraction

from app.services.table_checks import check_grid, number


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


def test_stacked_fractions_read_like_typed_ones():
    assert number("13¹²⁄₂₀") == number("13 12/20") == Fraction(68, 5)
    assert number("125,419⁹⁄₂₀") == Fraction(125419) + Fraction(9, 20)
    assert number("8⅛") == Fraction(65, 8)


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


def test_a_confirmed_misprint_stops_counting_until_its_numbers_change():
    """La stampa del 1913 sbaglia una somma (Jeddah: 62,031 stampato, 62,081
    nei totali). Confermato il refuso sulla scansione, la trascrizione resta
    quella stampata e la somma non conta più come errore; se poi qualcuno
    tocca uno dei suoi numeri, torna da controllare."""
    rows = [r[:] for r in COUNTRIES]
    rows[2][3] = "258,493"
    grid = _grid(rows)
    first = check_grid(grid)
    row_check = next(c for c in first["checks"] if not c["ok"] and c["kind"] == "row")
    assert row_check["key"] == "row:258,493=250,123+8,280"

    acknowledged = check_grid({**grid, "misprints": [row_check["key"]]})
    assert (acknowledged["failed"], acknowledged["misprints"]) == (1, 1)
    assert next(c for c in acknowledged["checks"] if c["key"] == row_check["key"])["misprint"] is True

    rows[2][1] = "250,124"
    edited = check_grid({**_grid(rows), "misprints": [row_check["key"]]})
    assert edited["misprints"] == 0


def test_quantities_in_another_unit_are_converted_into_the_total():
    """Calcutta p. 6: la farina è in libbre dentro un totale in hundredweight
    (112 lbs = 1 cwt), e la virgoletta di «Other sorts» sotto la farina torna
    ai Cwt. — lo dicono i totali. Senza conversione cinque somme giuste
    risultavano sbagliate."""
    rows = [
        ["ARTICLES.", "", "5—QUANTITIES.", "", "", "", "", "5a—VALUE."],
        ["", "", "1908-09", "1909-10", "1910-11", "1911-12", "1912-13", "1908-09"],
        ["C.—GRAIN—", "", "", "", "", "", "", ""],
        ["1. Barley", "Cwt.", "1,348", "2,026½", "4,050¼", "3,651", "17,468", "421"],
        ["2. Wheat", "„", "13,801", "11,510½", "11,237", "14,934½", "13,376", "7,929"],
        ["3. „ flour", "Lbs.", "13,045,690", "12,405,344", "11,252,067", "14,783,524", "25,588,640", "72,939"],
        ["4. Other sorts", "„", "5,422½", "4,576", "5,296½", "15,981", "10,083", "2,219"],
        ["Total, Grain", "Cwt.", "137,051", "128,875", "121,049", "166,562", "269,397", "83,508"],
    ]
    out = check_grid(_grid(rows))
    assert (out["passed"], out["failed"]) == (6, 0)
    # il valore in sterline non si converte: lì la farina conta com'è
    value = next(c for c in out["checks"] if c["total"] == [7, 7])
    assert value["cells"] == [[3, 7], [4, 7], [5, 7], [6, 7]]


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

        # Refusi della stampa confermati: salvati con la griglia, escono dal conto.
        keys = [c["key"] for c in check_grid(_grid(rows))["checks"] if not c["ok"]]
        assert client.put(
            f"/api/blocks/{block}/table", json={**_grid(rows), "phantom_cols": [], "misprints": keys},
        ).status_code == 200
        assert client.get(f"/api/blocks/{block}/table").json()["grid"]["misprints"] == keys
        sums = client.get(f"/api/projects/{pid}/sum-failures").json()["pages"]
        assert sums[str(page)]["failed"] == 0

        rows[2][3] = "258,403"
        client.put(f"/api/blocks/{block}/table", json={**_grid(rows), "phantom_cols": []})
        sums = client.get(f"/api/projects/{pid}/sum-failures").json()["pages"]
        assert sums == {str(page): {"checks": 6, "failed": 0, "rows": 3, "cols": 3}}
