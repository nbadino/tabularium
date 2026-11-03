"""Fine-tuning on the whole project or on chosen collections (docs/COLLECTIONS.md).

The scope is chosen when the dataset is built; training records it from the
dataset snapshot, evaluation measures only pages inside it, and both say which
scope they used. Recognition exports can be limited to one collection.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import connect, init_db
from app.main import app
from app.services import evaluate as evmod
from app.services import inference as infmod
from app.services import trainer


@pytest.fixture()
def client():
    init_db()
    with TestClient(app) as c:
        yield c


def _project(client, tmp_path: Path) -> tuple[int, dict[str, int], list[int]]:
    """Six annotated, approved pages: two in A, two in B, two without a collection."""
    archive = tmp_path / f"arch{time.time_ns()}"
    archive.mkdir(parents=True)
    for i in range(6):
        Image.new("RGB", (800, 1000), (215, 215, 215)).save(archive / f"{i:02}.png")
    pid = client.post("/api/projects", json={"name": f"S{time.time_ns()}", "archive_dir": str(archive)}).json()["id"]
    client.post(f"/api/projects/{pid}/scan")
    ids = [p["id"] for p in client.get(f"/api/projects/{pid}/pages").json()["items"]]
    for i, page_id in enumerate(ids):
        client.put(
            f"/api/pages/{page_id}/annotations",
            json={"items": [{"label": "Text", "kind": "rect", "points": [[10, 100], [700, 900]], "content": f"Line {i}", "order_idx": 1}]},
        )
    cols = {}
    for name, chunk in (("A", ids[0:2]), ("B", ids[2:4])):
        cols[name] = client.post(f"/api/projects/{pid}/collections", json={"name": name}).json()["id"]
        client.post(f"/api/projects/{pid}/pages/collection", json={"page_ids": chunk, "collection_id": cols[name]})
    with connect() as conn:
        conn.execute("UPDATE pages SET status='approved' WHERE project_id=?", (pid,))
    return pid, cols, ids


def _pages_in_report(report: dict) -> int:
    return report["pages"]["train"] + report["pages"]["val"]


def test_default_scope_is_the_whole_project(client, tmp_path):
    pid, _cols, _ids = _project(client, tmp_path)
    r = client.post(f"/api/projects/{pid}/datasets/build", json={"split_ratio": 0.5})
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["collection_scope"] == {"kind": "project", "collections": [], "include_unassigned": True, "pages": 6}
    assert _pages_in_report(report) == 6


def test_scope_on_chosen_collections(client, tmp_path):
    pid, cols, ids = _project(client, tmp_path)
    r = client.post(
        f"/api/projects/{pid}/datasets/build",
        json={"split_ratio": 0.5, "scope": {"kind": "collections", "collection_ids": [cols["A"]], "include_unassigned": True}},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    scope = report["collection_scope"]
    assert scope["kind"] == "collections"
    assert scope["collections"] == [{"id": cols["A"], "name": "A"}]
    assert scope["include_unassigned"] is True and scope["pages"] == 4
    assert _pages_in_report(report) == 4
    # recorded in the published report and in the snapshot the training reads
    saved = client.get(f"/api/projects/{pid}/datasets").json()["report"]
    assert saved["collection_scope"] == scope
    snap = json.loads((Path(report["snapshot_dir"]) / "report.json").read_text(encoding="utf-8"))
    assert snap["collection_scope"] == scope


def test_invalid_scopes_are_refused(client, tmp_path):
    pid, cols, _ids = _project(client, tmp_path)
    empty = {"kind": "collections", "collection_ids": [], "include_unassigned": False}
    assert client.post(f"/api/projects/{pid}/datasets/build", json={"scope": empty}).status_code == 400
    other_pid, other_cols, _ = _project(client, tmp_path)
    foreign = {"kind": "collections", "collection_ids": [other_cols["A"]], "include_unassigned": False}
    assert client.post(f"/api/projects/{pid}/datasets/build", json={"scope": foreign}).status_code == 404


def test_training_run_records_the_scope(client, tmp_path):
    pid, cols, _ids = _project(client, tmp_path)
    client.post(
        f"/api/projects/{pid}/datasets/build",
        json={"split_ratio": 0.5, "scope": {"kind": "collections", "collection_ids": [cols["B"]], "include_unassigned": False}},
    )
    # what start_run writes into run.json, read from the snapshot it trains on
    scope = trainer.dataset_collection_scope(pid)
    assert scope["collections"] == [{"id": cols["B"], "name": "B"}]
    # the run list shows the scope a run recorded
    run = trainer.runs_dir(pid) / "run_7"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": "run_7", "state": "finished", "collection_scope": scope}), encoding="utf-8")
    runs = client.get(f"/api/projects/{pid}/training/runs").json()
    items = runs["items"] if isinstance(runs, dict) else runs
    assert items[0]["collection_scope"] == scope


class _FakeClient:
    def __init__(self, url=None, model=None, timeout=0, max_retries=0):
        self.url = url or "http://fake"
        self.model = model or "fake"

    def ping(self):
        return True

    def layout(self, image):
        return [{"bbox": [11.0, 77.0, 989.0, 692.0], "label": "Text"}]

    def recognize(self, image, label):
        return "Line"


def test_evaluation_measures_only_the_scope(client, tmp_path, monkeypatch):
    pid, cols, ids = _project(client, tmp_path)
    client.post(
        f"/api/projects/{pid}/datasets/build",
        json={"split_ratio": 0.5, "scope": {"kind": "collections", "collection_ids": [cols["A"]], "include_unassigned": False}},
    )
    monkeypatch.setattr(infmod, "VllmClient", _FakeClient)
    report = evmod.evaluate_project(pid, with_text=False)
    assert report["collection_scope"]["collections"] == [{"id": cols["A"], "name": "A"}]
    assert report["pages_evaluated"] == 2
    assert {p["page_id"] for p in report["pages"]} == set(ids[0:2])


def test_recognition_export_by_collection(client, tmp_path):
    pid, cols, ids = _project(client, tmp_path)
    with connect() as conn:
        run_id = conn.execute(
            "INSERT INTO recognition_runs (project_id, state, engine, total_pages) VALUES (?, 'finished', 'ocr', 6)",
            (pid,),
        ).lastrowid
        for page_id in ids:
            conn.execute(
                "INSERT INTO recognition_run_items (run_id, page_id, state) VALUES (?, ?, 'finished')",
                (run_id, page_id),
            )
    base = f"/api/projects/{pid}/recognition-runs/{run_id}/export"
    assert len(client.get(base).json()["pages"]) == 6
    only_a = client.get(base, params={"collection": cols["A"]}).json()
    assert {p["page_id"] for p in only_a["pages"]} == set(ids[0:2])
    assert only_a["collection"] == {"id": cols["A"], "name": "A"}
    none = client.get(base, params={"collection": "none"}).json()
    assert {p["page_id"] for p in none["pages"]} == set(ids[4:6])
    csv = client.get(base, params={"collection": cols["B"], "format": "csv"}).text
    assert csv.count("\n") == 1 + 2
    assert client.get(base, params={"collection": "x"}).status_code == 400
