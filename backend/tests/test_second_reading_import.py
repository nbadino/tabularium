"""A second reading made elsewhere becomes a comparison between two models.

The files are a reader's result for a page plus `doubts`: the cells where a second
recognizer read something else. They are stored as a finished consensus run (first
reading against second reading), so the Studio shows them as cells to check and
nothing on the page is written.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from tests.test_recognition_import import ADAPTER, _blocks, _project

OTSL = (
    "<fcel>VESSEL—Owner<fcel>Flg<fcel>Gross<nl>"
    "<fcel>Triona (B.P.C.)<fcel>Br<fcel>4413<nl>"
    "<fcel>Trojan Star<fcel>Br<fcel>9037<nl>"
    "<fcel>Tudor Prince<fcel>Br<fcel>1958"
)
DOUBTS = [
    {"row": 1, "col": 2, "text": "4413", "other": "4418", "only_spacing": False},
    {"row": 2, "col": 0, "text": "Trojan Star", "other": "TrojanStar", "only_spacing": True},
    {"row": 3, "col": None, "text": "Tudor Prince | Br | 1958", "other": ""},
]


def _result(doubts=DOUBTS) -> dict:
    return {
        "otsl": OTSL,
        "blocks": [
            {"label": "Title", "bbox": [60.0, 6.0, 140.0, 18.0], "text": "LLOYD'S SHIPPING INDEX"},
            {"label": "Table", "bbox": [0.0, 30.0, 200.0, 270.0], "text": OTSL},
        ],
        "doubts": doubts,
    }


def _results(folder: Path, pages: dict[int, dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for page_id, result in pages.items():
        (folder / f"{page_id}.json").write_text(json.dumps(result), encoding="utf-8")
    return folder


def _texts(block: dict) -> dict[tuple[int, int], str]:
    return {(cell["r"], cell["c"]): cell["text"] for cell in block["grid"]["cells"]}


def _import(project_id: int, source: Path, **kwargs) -> dict:
    from app.services import consensus

    return consensus.import_second_reading(
        project_id, source, adapter_id=ADAPTER, second_name="Second reading", **kwargs
    )


def test_doubts_become_a_finished_comparison_and_the_page_is_not_written(tmp_path):
    from app.services import consensus

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        source = _results(tmp_path / "pair", {
            page_ids[0]: _result(), page_ids[1]: _result(doubts=[]),
        })
        report = _import(project_id, source)

        assert report["imported"] == 1 and report["failed"] == 0 and report["doubts"] == 3
        assert report["skipped"]["no_doubts"] == 1 and len(report["runs"]) == 1
        # Nothing on the page: a comparison never edits the annotations.
        assert _blocks(page_ids[0]) == []
        with connect() as conn:
            revision = conn.execute(
                "SELECT annotation_revision FROM pages WHERE id=?", (page_ids[0],)
            ).fetchone()[0]
        assert revision == 0

        run = client.get(f"/api/projects/{project_id}/consensus-runs/{report['runs'][0]}").json()
        assert run["state"] == "finished" and run["succeeded"] == 2 and run["pending"] == 0
        assert [model["key"] for model in run["models"]] == [consensus.IMPORT_FIRST, consensus.IMPORT_SECOND]
        assert [model["profile_id"] for model in run["models"]] == [None, None]
        assert run["models"][1]["name"] == "Second reading"

        page = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        assert page["run_id"] == report["runs"][0]
        first = page["outputs"][consensus.IMPORT_FIRST]
        second = page["outputs"][consensus.IMPORT_SECOND]
        assert [block["label"] for block in first] == ["Title", "Table"]
        assert _texts(first[1])[(1, 2)] == "4413"
        changed = _texts(second[1])
        assert changed[(1, 2)] == "4418" and changed[(2, 0)] == "TrojanStar"
        # A row the second reading does not have is empty there.
        assert [changed[(3, c)] for c in range(3)] == ["", "", ""]
        assert changed[(1, 0)] == "Triona (B.P.C.)"
        # The page with no doubt has no comparison: nothing to check is not a run.
        assert client.get(f"/api/pages/{page_ids[1]}/consensus").json()["run_id"] is None


def test_a_second_import_skips_what_is_there_and_dry_run_writes_nothing(tmp_path):
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        source = _results(tmp_path / "pair", {page_ids[0]: _result(), page_ids[1]: _result()})

        plan = _import(project_id, source, dry_run=True)
        assert plan["selected"] == 2 and plan["runs"] == []
        assert client.get(f"/api/projects/{project_id}/consensus-runs").json()["items"] == []

        assert _import(project_id, source, limit=1)["imported"] == 1
        again = _import(project_id, source)
        assert again["imported"] == 1 and again["skipped"]["already_imported"] == 1
        assert _import(project_id, source)["selected"] == 0
        assert _import(project_id, source, again=True)["imported"] == 2


def test_a_doubt_that_does_not_fit_the_table_fails_the_page_and_stores_nothing(tmp_path):
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        wrong = [{"row": 1, "col": 2, "text": "not what the table says", "other": "x"}]
        source = _results(tmp_path / "pair", {page_ids[0]: _result(doubts=wrong), page_ids[1]: _result()})
        failures: list[tuple[int, str | None]] = []
        report = _import(project_id, source, progress=lambda page_id, failure: failures.append((page_id, failure)))

        assert report["imported"] == 1 and report["failed"] == 1
        assert failures[0][0] == page_ids[0] and "row 1, column 2" in failures[0][1]
        assert client.get(f"/api/pages/{page_ids[0]}/consensus").json()["run_id"] is None


def test_verified_pages_and_pages_already_compared_are_left_alone(tmp_path):
    from app.adapters import consensus as runstore
    from app.services import consensus

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        with connect() as conn:
            conn.execute("UPDATE pages SET status='approved' WHERE id=?", (page_ids[0],))
            conn.commit()
        # A comparison between two other models already exists for the second page.
        other = runstore.create_finished(
            project_id, [{"key": "p1", "name": "A"}, {"key": "p2", "name": "B"}], owner_id=None, at="t",
        )
        runstore.add_finished_outputs(other, page_ids[1], {"p1": [], "p2": []}, at="t")
        source = _results(tmp_path / "pair", {page_id: _result() for page_id in page_ids})

        report = _import(project_id, source)
        assert report["imported"] == 1
        assert report["skipped"]["status"] == {"approved": 1} and report["skipped"]["compared"] == 1
        assert consensus.page_comparison(page_ids[1])["run_id"] == other


def test_only_gateway_models_and_real_folders(tmp_path):
    from app.services import consensus

    with TestClient(app) as client:
        project_id, _page_ids = _project(client, tmp_path)
        with pytest.raises(ValueError):
            _import(project_id, tmp_path / "missing")
        with pytest.raises(ValueError):
            consensus.import_second_reading(
                project_id, tmp_path, adapter_id="glm-ocr", second_name="x",
            )
        with pytest.raises(ValueError):
            consensus.import_second_reading(project_id, tmp_path, adapter_id=ADAPTER, second_name=" ")


def test_a_doubt_is_placed_in_the_delivered_table_before_the_structure_guard(tmp_path):
    """The guard splits "Valo (Rivö) Sw" into name and flag; the doubt still names the delivered cell."""
    from app.services import consensus

    otsl = (
        "<fcel>VESSEL—Owner<fcel>Flg<fcel>Gross<nl>"
        "<fcel>Triona (B.P.C.)<fcel>Br<fcel>4413<nl>"
        "<fcel>Trojan Star (Blue Star)<fcel>Br<fcel>9037<nl>"
        "<fcel>Valo (Rivö) Sw<ecel><fcel>642"
    )
    result = _result(doubts=[{"row": 3, "col": 0, "text": "Valo (Rivö) Sw", "other": "Valo (Rivo) Sw"}])
    result["otsl"] = result["blocks"][1]["text"] = otsl
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        report = _import(project_id, _results(tmp_path / "pair", {page_ids[0]: result}))
        assert report["imported"] == 1 and report["failed"] == 0
        page = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        first = _texts(page["outputs"][consensus.IMPORT_FIRST][1])
        second = _texts(page["outputs"][consensus.IMPORT_SECOND][1])
        assert (first[(3, 0)], first[(3, 1)]) == ("Valo (Rivö)", "Sw")
        assert (second[(3, 0)], second[(3, 1)]) == ("Valo (Rivo)", "Sw")


MERGED = (
    "<fcel>VESSEL<fcel>Tons<lcel><nl>"
    "<ucel><fcel>Gross<fcel>Net<nl>"
    "<fcel>Triona<fcel>4413<fcel>2000<nl>"
    "<fcel>Trojan Star<fcel>9037<fcel>5000"
)


def _merged(doubts: list[dict]) -> dict:
    result = _result(doubts=doubts)
    result["otsl"] = result["blocks"][1]["text"] = MERGED
    return result


def test_a_doubt_under_a_merged_cell_or_past_the_last_column_opens_the_cell(tmp_path):
    """The second recognizer read text where the first has a merge, or one column more.

    The first reading has nothing of its own there (`text` is empty), so the second
    reading gets the cell: the merge is split, or the table widened. The first
    reading keeps its structure.
    """
    from app.services import consensus

    doubts = [
        {"row": 0, "col": 2, "text": "", "other": "Net"},      # under "Tons", two columns wide
        {"row": 1, "col": 0, "text": "", "other": "Owner"},    # under "VESSEL", two rows high
        {"row": 2, "col": 3, "text": "", "other": "London"},   # a fourth column
        {"row": 2, "col": 1, "text": "4413", "other": "4418"},
    ]
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        report = _import(project_id, _results(tmp_path / "pair", {page_ids[0]: _merged(doubts)}))
        assert report["imported"] == 1 and report["failed"] == 0 and report["doubts"] == 4

        page = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        first = page["outputs"][consensus.IMPORT_FIRST][1]
        second = page["outputs"][consensus.IMPORT_SECOND][1]
        assert first["grid"]["cols"] == 3 and (0, 2) not in _texts(first) and (1, 0) not in _texts(first)
        texts = _texts(second)
        assert second["grid"]["cols"] == 4
        assert (texts[(0, 1)], texts[(0, 2)]) == ("Tons", "Net")
        assert (texts[(0, 0)], texts[(1, 0)]) == ("VESSEL", "Owner")
        assert (texts[(2, 1)], texts[(2, 3)], texts[(3, 3)]) == ("4418", "London", "")
        assert all(cell["rowspan"] == 1 and cell["colspan"] == 1 for cell in second["grid"]["cells"])
        assert page["comparison"] is not None


def test_a_doubt_that_gives_text_to_a_place_without_a_cell_still_fails_the_page(tmp_path):
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        pages = {
            page_ids[0]: _merged([{"row": 0, "col": 2, "text": "Tons", "other": "Net"}]),
            page_ids[1]: _merged([{"row": 9, "col": 3, "text": "", "other": "London"}]),
        }
        report = _import(project_id, _results(tmp_path / "pair", pages))
        assert report["imported"] == 0 and report["failed"] == 2
