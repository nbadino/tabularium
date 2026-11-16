"""A training run must not silently use a dataset snapshot older than the pages.

On 2026-11-16 a one-click Vast training of project 4 trained on the 13:29
snapshot (124 pages) while 134 eligible pages had been approved, and cells
corrected, after it. Nothing said so.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import connect
from app.main import app
from app.services import auto_training, trainer


def _project(tmp_path: Path) -> tuple[int, list[int]]:
    archive = tmp_path / "archive"
    archive.mkdir(parents=True)
    for i in range(3):
        Image.new("RGB", (1000, 1400), (210, 210, 210)).save(archive / f"{i:03}.png")
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "S", "archive_dir": str(archive)}).json()["id"]
        client.post(f"/api/projects/{pid}/scan")
        pages = [p["id"] for p in client.get(f"/api/projects/{pid}/pages").json()["items"]]
        for page_id in pages:
            client.put(
                f"/api/pages/{page_id}/annotations",
                json={"items": [{"label": "Text", "kind": "rect", "points": [[10, 10], [900, 900]],
                                 "content": f"line {page_id}", "order_idx": 1}]},
            )
    return pid, pages


def _approve(*page_ids: int) -> None:
    with connect() as conn:
        for page_id in page_ids:
            conn.execute("UPDATE pages SET status='approved' WHERE id=?", (page_id,))


def _build(pid: int) -> dict:
    with TestClient(app) as client:
        out = client.post(
            f"/api/projects/{pid}/datasets/build",
            json={"split_ratio": 0.5, "seed": 42, "approved_only": True},
        )
    assert out.status_code == 200, out.text
    return out.json()


def test_a_fresh_snapshot_is_not_stale(tmp_path: Path):
    pid, (p1, p2, _p3) = _project(tmp_path / "fresh")
    _approve(p1, p2)
    _build(pid)
    result = trainer.preflight(pid, {"executor": "vast"}, lang="en")
    assert result["dataset"]["stale"] is None
    assert not any("rebuild" in w for w in result["warnings"])


def test_an_edit_after_the_build_makes_the_snapshot_stale(tmp_path: Path):
    pid, (p1, p2, _p3) = _project(tmp_path / "edited")
    _approve(p1, p2)
    _build(pid)
    # A cell or block corrected after the build (the clock moves on).
    with connect() as conn:
        conn.execute("UPDATE blocks SET updated_at=datetime('now', '+1 minute') WHERE page_id=?", (p1,))
    result = trainer.preflight(pid, {"executor": "vast"}, lang="en")
    stale = result["dataset"]["stale"]
    assert stale is not None
    assert stale["changed_pages"] == 1
    assert stale["snapshot_pages"] == 2 and stale["eligible_pages"] == 2
    assert any("rebuild" in w.lower() for w in result["warnings"])


def test_a_page_approved_after_the_build_makes_the_snapshot_stale(tmp_path: Path):
    """A direct approval only flips `pages.status`: the page count betrays it."""
    pid, (p1, p2, p3) = _project(tmp_path / "approved")
    _approve(p1, p2)
    _build(pid)
    _approve(p3)
    stale = trainer.preflight(pid, {"executor": "vast"}, lang="en")["dataset"]["stale"]
    assert stale is not None
    assert stale["snapshot_pages"] == 2 and stale["eligible_pages"] == 3


def test_edits_to_pages_outside_the_dataset_do_not_count(tmp_path: Path):
    pid, (p1, p2, p3) = _project(tmp_path / "outside")
    _approve(p1, p2)
    _build(pid)
    with connect() as conn:  # p3 is not approved: an approved-only build ignores it
        conn.execute("UPDATE blocks SET updated_at=datetime('now', '+1 minute') WHERE page_id=?", (p3,))
    assert trainer.preflight(pid, {"executor": "vast"}, lang="en")["dataset"]["stale"] is None


def test_auto_training_refuses_a_stale_snapshot_unless_confirmed(tmp_path: Path, monkeypatch):
    pid, (p1, p2, p3) = _project(tmp_path / "auto")
    _approve(p1, p2)
    _build(pid)
    _approve(p3)
    started: list = []
    monkeypatch.setattr(auto_training, "_run", lambda *a, **k: started.append(k))
    cfg = {"adapter_id": "glm-ocr", "train_type": "lora"}
    with pytest.raises(ValueError, match="2.*3") as err:
        auto_training.start_auto(pid, cfg, "key", owner_id=1, lang="en")
    assert started == []
    from app.services import i18n

    assert "rebuild" in i18n.localize_detail(str(err.value), "en").lower()
    job = auto_training.start_auto(pid, cfg, "key", owner_id=1, lang="en", allow_stale_dataset=True)
    assert job["dataset_stale"]["eligible_pages"] == 3
    auto_training._THREADS[job["id"]].join(timeout=5)  # noqa: SLF001
    assert len(started) == 1
