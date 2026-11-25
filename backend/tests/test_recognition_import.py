"""Importing results a model produced elsewhere: same write path as a recognition run.

The files are what the Indra gateway's reader returns for a page (`blocks` with a
label, a pixel box and the text), one `<page id>.json` per page.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import connect
from app.main import app

ADAPTER = "indra-lloyds"
OTSL = "<fcel>VESSEL—Owner<fcel>Flg<nl><fcel>Triona (B.P.C.)<fcel>Br"


def _project(client: TestClient, root: Path, pages: int = 3) -> tuple[int, list[int]]:
    archive = root / "archive"
    archive.mkdir(parents=True)
    for index in range(pages):
        Image.new("RGB", (200, 300), (255, 255, 250 - index)).save(archive / f"p{index}.png")
    project_id = client.post(
        "/api/projects", json={"name": "Imported", "archive_dir": str(archive)}
    ).json()["id"]
    client.post(f"/api/projects/{project_id}/scan")
    items = client.get(f"/api/projects/{project_id}/pages").json()["items"]
    return project_id, [page["id"] for page in items]


def _result(**extra) -> dict:
    return {
        "otsl": OTSL,
        "blocks": [
            {"label": "Title", "bbox": [60.0, 6.0, 140.0, 18.0], "text": "LLOYD'S SHIPPING INDEX"},
            {"label": "Issue-date", "bbox": [150.0, 6.0, 198.0, 18.0], "text": "[Mon., Apr. 3, 1950."},
            {"label": "Table", "bbox": [0.0, 30.0, 200.0, 270.0], "text": OTSL},
        ],
        "image": "somewhere-else.tif",
        **extra,
    }


def _results(folder: Path, page_ids: list[int], **extra) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for page_id in page_ids:
        (folder / f"{page_id}.json").write_text(json.dumps(_result(**extra)), encoding="utf-8")
    return folder


def _blocks(page_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT label,content,prefill_source,confirmed,recognition_run_id,points "
            "FROM blocks WHERE page_id=? ORDER BY order_idx,id", (page_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def test_results_become_drafts_through_a_recognition_run(tmp_path):
    from app.services import pages as pagesvc
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        source = _results(tmp_path / "results", page_ids[:2])
        report = recognition.import_results(project_id, source, adapter_id=ADAPTER)

        assert report["imported"] == 2 and report["failed"] == 0
        assert report["selected"] == 2 and len(report["runs"]) == 1
        run = client.get(f"/api/projects/{project_id}/recognition-runs/{report['runs'][0]}").json()
        assert run["state"] == "finished" and run["succeeded_pages"] == 2
        assert run["provider"] == "import" and run["adapter_id"] == ADAPTER

        blocks = _blocks(page_ids[0])
        assert [block["label"] for block in blocks] == ["Title", "Issue-date", "Table"]
        assert blocks[1]["content"] == "[Mon., Apr. 3, 1950."
        assert {block["prefill_source"] for block in blocks} == {"model:indra-lloyds:official"}
        assert {block["confirmed"] for block in blocks} == {0}
        assert {block["recognition_run_id"] for block in blocks} == {report["runs"][0]}
        with connect() as conn:
            grid = conn.execute(
                "SELECT t.* FROM tables t JOIN blocks b ON b.id=t.block_id WHERE b.page_id=?",
                (page_ids[0],),
            ).fetchone()
        assert grid is not None
        # The boxes belong to the scan as it is: the page is not straightened under them.
        assert pagesvc.deskew_path(page_ids[0]).exists() is False
        # A page without a file is left alone.
        assert _blocks(page_ids[2]) == []


def test_only_pages_in_the_chosen_statuses_are_written(tmp_path):
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        with connect() as conn:
            conn.execute("UPDATE pages SET status='approved' WHERE id=?", (page_ids[0],))
            conn.execute("UPDATE pages SET status='annotated' WHERE id=?", (page_ids[1],))
        source = _results(tmp_path / "results", page_ids)
        report = recognition.import_results(project_id, source, adapter_id=ADAPTER)

        assert report["selected"] == 1 and report["imported"] == 1
        assert report["skipped"]["status"] == {"approved": 1, "annotated": 1}
        assert _blocks(page_ids[0]) == [] and _blocks(page_ids[1]) == []
        assert len(_blocks(page_ids[2])) == 3


def test_a_dry_run_writes_nothing(tmp_path):
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        source = _results(tmp_path / "results", page_ids)
        (source / "999999.json").write_text("{}", encoding="utf-8")
        (source / "notes.txt").write_text("not a result", encoding="utf-8")
        report = recognition.import_results(project_id, source, adapter_id=ADAPTER, dry_run=True)

        assert report["dry_run"] is True and report["selected"] == 3
        assert report["skipped"]["not_in_project"] == 1
        assert report["runs"] == [] and report["imported"] == 0
        assert all(_blocks(page_id) == [] for page_id in page_ids)
        runs = client.get("/api/recognition-runs").json()["items"]
        assert [run for run in runs if run["project_id"] == project_id] == []


def test_drafts_are_replaced_and_verified_blocks_kept(tmp_path):
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path, pages=1)
        page_id = page_ids[0]
        points = json.dumps([[10, 10], [100, 10], [100, 50], [10, 50]])
        with connect() as conn:
            conn.execute(
                "INSERT INTO blocks(page_id,label,kind,points,content,order_idx,prefill_source,confirmed) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (page_id, "Text", "text", points, "an older draft", 0, "rapidocr:0.99", 0),
            )
            conn.execute(
                "INSERT INTO blocks(page_id,label,kind,points,content,order_idx,prefill_source,confirmed) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (page_id, "Text", "text", points, "verified by a person", 1, "rapidocr:0.99", 1),
            )
        report = recognition.import_results(
            project_id, _results(tmp_path / "results", page_ids), adapter_id=ADAPTER,
        )
        assert report["imported"] == 1
        contents = [block["content"] for block in _blocks(page_id)]
        assert "an older draft" not in contents
        assert "verified by a person" in contents
        assert "[Mon., Apr. 3, 1950." in contents


def test_a_result_that_does_not_fit_the_page_fails_that_page_only(tmp_path):
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path, pages=2)
        source = _results(tmp_path / "results", page_ids)
        wrong = _result()
        wrong["blocks"][2]["bbox"] = [0.0, 30.0, 2868.0, 3613.0]   # another scan's pixels
        (source / f"{page_ids[0]}.json").write_text(json.dumps(wrong), encoding="utf-8")
        (source / f"{page_ids[1]}.json").write_text(json.dumps(_result()), encoding="utf-8")
        report = recognition.import_results(project_id, source, adapter_id=ADAPTER)

        assert report["imported"] == 1 and report["failed"] == 1
        assert _blocks(page_ids[0]) == [] and len(_blocks(page_ids[1])) == 3
        run = client.get(f"/api/projects/{project_id}/recognition-runs/{report['runs'][0]}").json()
        assert run["state"] == "finished_with_errors"
        failed = next(item for item in run["items"] if item["page_id"] == page_ids[0])
        assert "200x300" in failed["error"]


def test_a_straightened_page_is_not_given_boxes_read_on_the_original(tmp_path):
    from app.services import pages as pagesvc
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path, pages=1)
        master = pagesvc.deskew_path(page_ids[0])
        master.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 300), "white").save(master)
        pagesvc.mark_transform(page_ids[0], "deskew", (200, 300), details={"angle": 1.0})
        report = recognition.import_results(
            project_id, _results(tmp_path / "results", page_ids), adapter_id=ADAPTER,
        )
        assert report["selected"] == 0 and report["skipped"]["transformed"] == 1
        assert _blocks(page_ids[0]) == []


def test_the_results_are_split_into_runs_of_a_bounded_size(tmp_path):
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        source = _results(tmp_path / "results", page_ids)
        report = recognition.import_results(project_id, source, adapter_id=ADAPTER, chunk=2, limit=3)
        assert len(report["runs"]) == 2 and report["imported"] == 3


def test_models_outside_the_indra_gateway_are_refused(tmp_path):
    from app.services import recognition

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path, pages=1)
        source = _results(tmp_path / "results", page_ids)
        with pytest.raises(ValueError, match="glm-ocr"):
            recognition.import_results(project_id, source, adapter_id="glm-ocr")
        assert _blocks(page_ids[0]) == []


def test_the_command_reports_the_plan_and_the_outcome(tmp_path, capsys):
    from app import cli

    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path, pages=2)
        source = _results(tmp_path / "results", page_ids)
        base = ["recognition", "import", str(source), "--project", str(project_id), "--adapter", ADAPTER]
        assert cli.main([*base, "--dry-run"]) == 0
        assert "2 pages would be imported" in capsys.readouterr().out
        assert _blocks(page_ids[0]) == []
        assert cli.main(base) == 0
        assert "imported 2" in capsys.readouterr().out
        assert len(_blocks(page_ids[0])) == 3
