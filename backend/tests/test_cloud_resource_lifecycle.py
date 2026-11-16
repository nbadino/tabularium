"""A `cloud_resource` job closes when its instance is destroyed or disappears.

Prod rows 36/37 (2026-11-16) stayed `running` after their Vast instances were gone:
the delete verb never closed the row, and a vanished instance was never noticed.
"""
from __future__ import annotations

import json

import pytest

from app.adapters import jobs as jobstore
from app.db import connect, init_db
from app.services import auto_training, cloud_manager, vault

PREFIX = "lifecycle-"


def _row(remote_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT state, ended_at, cost_json FROM jobs "
            "WHERE kind='cloud_resource' AND provider='vast' AND remote_job_id=? "
            "ORDER BY id DESC LIMIT 1",
            (remote_id,),
        ).fetchone()
    return dict(row)


@pytest.fixture
def clean_rows():
    init_db()

    def wipe() -> None:
        with connect() as conn:
            conn.execute(
                "DELETE FROM jobs WHERE kind='cloud_resource' AND provider='vast'"
            )

    wipe()
    yield
    wipe()


class _Response:
    status_code = 200
    text = ""


class _Client:
    """Stands in for the Vast REST endpoint: every verb succeeds."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put(self, *args, **kwargs):
        return _Response()

    def delete(self, *args, **kwargs):
        return _Response()


def _vast(monkeypatch, listed: list[dict] | Exception):
    """Mock the Vast client: control verbs succeed, the listing is `listed`."""
    monkeypatch.setattr(cloud_manager.httpx, "Client", _Client)

    def call(api_key, method, path, **kwargs):
        if isinstance(listed, Exception):
            raise listed
        return {"instances": listed, "next_token": None}

    monkeypatch.setattr(cloud_manager, "_vast_call", call)


def test_deleting_a_vast_instance_closes_its_job(monkeypatch, clean_rows):
    _vast(monkeypatch, [])
    cloud_manager.track_cloud_resource("vast", 60902644, hourly_rate=0.25)
    cloud_manager.control_vast_instance("key", 60902644, "delete")
    row = _row("60902644")
    assert row["state"] == "deleted"
    assert row["ended_at"] is not None


def test_cancelling_an_auto_training_closes_the_cloud_job(monkeypatch, clean_rows, tmp_path):
    _vast(monkeypatch, [])
    monkeypatch.setattr(auto_training, "_job_dir", lambda: tmp_path)
    job = {"id": "auto_1794819057378", "project_id": 4, "state": "waiting_ssh",
           "phase": "ssh", "instance_id": 60902644, "run_id": None}
    auto_training._save(job)
    cloud_manager.track_cloud_resource("vast", 60902644, hourly_rate=0.25)

    auto_training.cancel_auto(job["id"], "key")

    assert auto_training.get_job(job["id"])["state"] == "cancelled"
    row = _row("60902644")
    assert row["state"] == "deleted" and row["ended_at"] is not None


def test_startup_closes_jobs_whose_instance_is_gone(monkeypatch, clean_rows):
    _vast(monkeypatch, [{"id": 70000002, "actual_status": "running", "dph_total": 1.0}])
    monkeypatch.setattr(vault, "get", lambda ref: "key" if ref == "vault:vast_api_key" else "")
    cloud_manager.track_cloud_resource("vast", 60907839, hourly_rate=1.37)
    cloud_manager.track_cloud_resource("vast", 70000002, hourly_rate=1.0)
    # A row left with the raw verb by the old control route is open as well.
    jobstore.cloud_upsert(owner_id=None, provider="vast", remote_id="60912760",
                          state="delete", cost={}, terminal=False)

    closed = cloud_manager.reconcile_cloud_resources()

    assert closed == ["60907839", "60912760"]
    gone = _row("60907839")
    assert gone["state"] == "deleted" and gone["ended_at"] is not None
    assert json.loads(gone["cost_json"])["closed_reason"] == cloud_manager.VANISHED_REASON
    alive = _row("70000002")
    assert alive["state"] == "running" and alive["ended_at"] is None


def test_an_unreachable_provider_closes_nothing(monkeypatch, clean_rows):
    _vast(monkeypatch, RuntimeError("Vast.ai unreachable"))
    monkeypatch.setattr(vault, "get", lambda ref: "key" if ref == "vault:vast_api_key" else "")
    cloud_manager.track_cloud_resource("vast", 60907839, hourly_rate=1.37)

    assert cloud_manager.reconcile_cloud_resources() == []
    assert _row("60907839")["ended_at"] is None


def test_a_truncated_listing_closes_nothing(monkeypatch, clean_rows):
    monkeypatch.setattr(
        cloud_manager, "_vast_call",
        lambda *a, **k: {"instances": [{"id": 1, "actual_status": "running"}], "next_token": "more"},
    )
    monkeypatch.setattr(vault, "get", lambda ref: "key" if ref == "vault:vast_api_key" else "")
    cloud_manager.track_cloud_resource("vast", 60907839, hourly_rate=1.37)

    cloud_manager.reconcile_cloud_resources()
    assert _row("60907839")["ended_at"] is None


def _open_rows() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT remote_job_id FROM jobs WHERE kind='cloud_resource' AND provider='vast' "
            "AND ended_at IS NULL ORDER BY id"
        ).fetchall()
    return [str(row["remote_job_id"]) for row in rows]


def test_destroying_the_training_gpu_opens_no_row_for_other_instances(monkeypatch, clean_rows):
    """Prod row 43 (2026-11-16): the end-of-run destroy confirmed the deletion with a
    full account listing, which recorded an instance rented outside Tabularium
    (60933398) as a running resource that nothing would ever close."""
    _vast(monkeypatch, [{"id": 60933398, "actual_status": "running", "dph_total": 0.0674}])
    cloud_manager.track_cloud_resource("vast", 60929663, hourly_rate=1.37)

    auto_training._destroy("key", 60929663)

    assert _row("60929663")["state"] == "deleted"
    assert _open_rows() == []


def test_startup_reconcile_adopts_no_foreign_instance(monkeypatch, clean_rows):
    _vast(monkeypatch, [{"id": 60933398, "actual_status": "running", "dph_total": 0.0674}])
    monkeypatch.setattr(vault, "get", lambda ref: "key" if ref == "vault:vast_api_key" else "")
    cloud_manager.track_cloud_resource("vast", 60929663, hourly_rate=1.37)

    assert cloud_manager.reconcile_cloud_resources() == ["60929663"]
    assert _open_rows() == []


def test_the_cloud_panel_listing_still_records_every_instance(monkeypatch, clean_rows):
    _vast(monkeypatch, [{"id": 60933398, "actual_status": "running", "dph_total": 0.0674}])

    cloud_manager.list_vast_instances("key", adopt=True)

    assert _open_rows() == ["60933398"]
