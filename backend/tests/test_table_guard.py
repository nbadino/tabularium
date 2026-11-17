"""Structure guard on model table output (`domain/table_guard.py`).

The fixtures are small OTSL tables shaped like the three faults a fine-tuned
Xiaomi-OCR-0 made on the Lloyd's gold pages (`docs/spec/71-evaluation.md`,
"Structure guard"): flag glued to the vessel name, an empty 'built' cell with
the year in 'gross', and a broken "Gr ss" header splitting one column in two.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import table_guard as tg
from app.domain.otsl import otsl_to_grid
from app.services import domain_profiles
from app.services import table_guard as guardsvc

HEADER = ["VESSEL—Owner", "Flg", "Reg", "Blt", "Gross", "Net", "From", "For", "Latest Report"]
GOOD = [
    ["Orbita (P.S.N.) 3sc", "Br", "*", "15", "16538", "9890", "Liverpool Mar 9", "Hongkong", "Sd Aden Mar 26"],
    ["Orcades (Orient) 2sc", "Br", "*", "48", "28164", "15839", "London Mar 17", "Sydney", "Ar Colombo Apr 1"],
    ["Orduna (P.S.N.) 3sc", "Br", "*", "14", "15507", "9565", "Singapore Feb 8", "Liverpool", "Ar Mar 8"],
]


def _otsl(rows: list[list[str]]) -> str:
    return "<nl>".join("".join(f"<fcel>{t}" if t else "<ecel>" for t in row) for row in rows)


def _dense(grid: dict) -> list[list[str]]:
    m = [[""] * grid["cols"] for _ in range(grid["rows"])]
    for c in grid["cells"]:
        m[c["r"]][c["c"]] = c["text"]
    return m


@pytest.fixture(scope="module")
def columns():
    return guardsvc.resolve({}).columns


def test_bundled_profile_declares_valid_column_types():
    guard = guardsvc.resolve({})
    assert guard.profile_id == domain_profiles.DEFAULT_PROFILE_ID
    assert guard.enabled and guard.source == "profile"
    assert {c.field for c in guard.columns} >= {"vessel", "flag", "reg", "built", "gross", "net"}


def test_flag_glued_to_the_vessel_is_split_back(columns):
    # The model reads "Ordu (D.I.M.) Tu" in the vessel cell, puts the registry
    # in the flag column and an empty cell in the registry column.
    bad = ["Ordu (D.I.M.) Tu", "R", "", "37", "7279", "5155", "Izmir Nov 29", "Istanbul", "Ar Nov 30"]
    grid = tg.guard_grid(otsl_to_grid(_otsl([HEADER, *GOOD, bad])), columns)
    assert _dense(grid)[4] == ["Ordu (D.I.M.)", "Tu", "R", "37", "7279", "5155", "Izmir Nov 29", "Istanbul", "Ar Nov 30"]
    assert grid["structure_repairs"] == [{
        "rule": "glued_value_split", "row": 4, "col": 0,
        "before": ["Ordu (D.I.M.) Tu", "R", ""], "after": ["Ordu (D.I.M.)", "Tu", "R"],
    }]


def test_empty_built_cell_with_year_in_gross_shifts_back_left(columns):
    bad = ["Commercial Courier (Moore)", "Am", "R", "", "03", "2111", "1278", "Ar Norfolk Dec 6", ""]
    grid = tg.guard_grid(otsl_to_grid(_otsl([HEADER, *GOOD, bad])), columns)
    assert _dense(grid)[4] == ["Commercial Courier (Moore)", "Am", "R", "03", "2111", "1278", "Ar Norfolk Dec 6", "", ""]
    (repair,) = grid["structure_repairs"]
    assert repair["rule"] == "empty_cell_moved" and repair["row"] == 4 and repair["col"] == 3


def test_split_header_column_is_merged_into_its_neighbour(columns):
    header = ["VESSEL—Owner", "Flg", "Reg", "blt", "Gr", "as", "Net", "From", "For", "Latest Report"]
    body = [row[:5] + [""] + row[5:] for row in GOOD]
    grid = tg.guard_grid(otsl_to_grid(_otsl([header, *body])), columns)
    assert grid["cols"] == 9
    assert _dense(grid)[0][4] == "Gr as"
    assert _dense(grid)[1:] == GOOD
    (repair,) = grid["structure_repairs"]
    assert repair == {"rule": "column_merged", "reason": "split_header", "col": 4, "header": ["Gr", "as"], "rows": []}


def test_duplicate_header_column_is_merged_without_a_profile():
    body = [[f"Ship {i}", str(10 + i), ""] for i in range(10)]
    grid = otsl_to_grid(_otsl([["Name", "Tons", "Tons"], *body]))
    out = tg.guard_grid(grid)
    assert out["cols"] == 2 and _dense(out) == [["Name", "Tons"], *[row[:2] for row in body]]
    assert out["structure_repairs"][0]["reason"] == "duplicate_header"


def test_a_correct_table_is_returned_unchanged(columns):
    grid = otsl_to_grid(_otsl([HEADER, *GOOD]))
    assert tg.guard_grid(grid, columns) is grid


def test_rows_are_left_alone_without_declared_column_types():
    bad = ["Ordu (D.I.M.) Tu", "R", "", "37", "7279", "5155", "Izmir Nov 29", "Istanbul", "Ar Nov 30"]
    grid = otsl_to_grid(_otsl([HEADER, *GOOD, bad]))
    assert tg.guard_grid(grid) is grid


def test_an_empty_column_with_an_empty_header_is_kept(columns):
    # It may be a phantom column, which the conventions keep on purpose.
    header = HEADER[:5] + [""] + HEADER[5:]
    body = [row[:5] + [""] + row[5:] for row in GOOD]
    grid = otsl_to_grid(_otsl([header, *body]))
    assert tg.guard_grid(grid, columns)["cols"] == 10


def test_a_merge_that_would_lose_text_is_refused(columns):
    header = ["VESSEL—Owner", "Flg", "Reg", "blt", "Gr", "as", "Net", "From", "For", "Latest Report"]
    body = [row[:5] + [""] + row[5:] for row in GOOD]
    body[0][5] = "999"  # both halves filled on one row
    grid = tg.guard_grid(otsl_to_grid(_otsl([header, *body])), columns)
    assert grid["cols"] == 10


def test_an_ambiguous_row_is_not_touched(columns):
    # The empty cell could have come from either neighbour: no unique repair.
    bad = ["Oregon (C.G.T.) 2sc", "Fr", "v", "", "29", "7706", "Dunkirk Mar 15", "Havre", "Ar Mar 16"]
    grid = otsl_to_grid(_otsl([HEADER, *GOOD, bad]))
    assert tg.guard_grid(grid, columns) is grid


def test_project_setting_overrides_the_profile_default():
    assert guardsvc.resolve({"table_guard": False}).enabled is False
    assert guardsvc.resolve({"table_guard": False}).source == "project"
    settings = {"table_guard": False}
    guardsvc.set_enabled(settings, None)
    assert guardsvc.resolve(settings).source == "profile"
    unknown = guardsvc.resolve({"study_protocol": {"domain_profile": "custom"}})
    assert unknown.enabled is False and unknown.columns == ()
    grid = {"rows": 1, "cols": 1, "cells": [{"r": 0, "c": 0, "text": "x"}]}
    assert guardsvc.apply(grid, unknown) is grid


def test_bad_column_declarations_fail_loudly():
    with pytest.raises(ValueError):
        tg.parse_columns([{"field": "x", "headers": ["x"]}])
    with pytest.raises(ValueError):
        tg.parse_columns([{"field": "x", "headers": ["x"], "values": ["a"]}, {"field": "x", "headers": ["y"], "values": ["b"]}])


def test_prefill_applies_the_guard_and_records_the_repair(tmp_path, monkeypatch):
    from app.api import prelabel as prelabelmod
    from app.main import app
    from app.services import prefill as prefillsvc
    from tests.test_prelabel import _get_page

    bad = ["Ordu (D.I.M.) Tu", "R", "", "37", "7279", "5155", "Izmir Nov 29", "Istanbul", "Ar Nov 30"]

    class FakeModel:
        model = "fake-xiaomi"

        def ping(self):
            return True

        def end2end(self, image):
            return [{"bbox": [10, 20, 890, 1280], "label": "Table", "content": _otsl([HEADER, *GOOD, bad])}]

    monkeypatch.setattr(prelabelmod.inference, "VllmClient", FakeModel)
    monkeypatch.setattr(prefillsvc, "_looks_like_table", lambda *_args: True)
    with TestClient(app) as client:
        pid, page_id = _get_page(client, tmp_path / "guard")
        body = {"page_ids": [page_id], "mode": "replace", "confirm": True, "engine": "model", "model_mode": "end2end"}
        result = client.post(f"/api/projects/{pid}/prelabel", json=body).json()["results"][0]
        assert result["table_structure_repairs"] == 1
        block = client.get(f"/api/pages/{page_id}/annotations").json()["items"][0]
        grid = client.get(f"/api/blocks/{block['id']}/table").json()["grid"]
        assert grid["structure_repairs"][0]["rule"] == "glued_value_split"
        assert _dense(grid)[4][:3] == ["Ordu (D.I.M.)", "Tu", "R"]
        # The repair is provenance: saving the grid from the editor keeps it.
        saved = client.put(f"/api/blocks/{block['id']}/table", json=grid)
        assert saved.status_code == 200, saved.text
        assert saved.json()["grid"]["structure_repairs"] == grid["structure_repairs"]

        # Switched off for the project: the model's grid is stored as read.
        assert client.put(f"/api/projects/{pid}/table-guard", json={"enabled": False}).json()["source"] == "project"
        client.post(f"/api/projects/{pid}/prelabel", json=body)
        block = client.get(f"/api/pages/{page_id}/annotations").json()["items"][0]
        grid = client.get(f"/api/blocks/{block['id']}/table").json()["grid"]
        assert grid["structure_repairs"] == [] and _dense(grid)[4][0] == "Ordu (D.I.M.) Tu"
        out = client.put(f"/api/projects/{pid}/table-guard", json={"enabled": None}).json()
        assert out == {"enabled": True, "source": "profile", "profile_id": "shipping-registers-1900s",
                       "profile_default": True, "declares_columns": True}
