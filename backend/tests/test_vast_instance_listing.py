"""Vast.ai instance listing: an error is never an empty account.

On 2026-11-16 `GET /api/v0/instances/` (with or without `?owner=me`) answered
410 Gone with `{success, error, msg}` and no `instances` key, while
`GET /api/v0/instances/{id}/` still worked. A reader that took the missing key
as "no instances" reported an empty account; in Tabularium the same reading
closes the cost rows and the auto-training jobs of GPUs that are still billing.

Shapes from vast-cli `vastai/api/instances.py` (show_instances, show_instances_v1,
show_instance) at 6eb4ed8.
"""
from __future__ import annotations

import json

import pytest

from app.db import connect, init_db
from app.services import auto_training, cloud_manager, vault

V0 = cloud_manager.VAST_API_BASE
V1 = cloud_manager.VAST_API_V1
GONE_BODY = {"success": False, "error": "deprecated_endpoint", "msg": "Use /api/v1/instances/"}


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload


def _provider(monkeypatch, route):
    """Route every Vast request through `route(method, url, params) -> (status, body)`."""
    calls: list[tuple[str, str, dict]] = []

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def request(self, method, url, params=None, **kwargs):
            calls.append((method, url, dict(params or {})))
            status, body = route(method, url, params or {})
            return _Response(body, status)

    monkeypatch.setattr(cloud_manager.httpx, "Client", Client)
    return calls


@pytest.fixture
def rows():
    init_db()

    def wipe():
        with connect() as conn:
            conn.execute("DELETE FROM jobs WHERE kind='cloud_resource' AND provider='vast'")

    wipe()
    yield
    wipe()


def _open(remote_id: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT ended_at FROM jobs WHERE kind='cloud_resource' AND provider='vast' "
            "AND remote_job_id=? ORDER BY id DESC LIMIT 1",
            (remote_id,),
        ).fetchone()
    return row is not None and row["ended_at"] is None


def _key(monkeypatch):
    monkeypatch.setattr(vault, "get", lambda ref: "key" if ref == "vault:vast_api_key" else "")


# --- the listing ----------------------------------------------------------------


def test_a_failing_v1_listing_never_falls_back_to_the_gone_v0_collection(monkeypatch, rows):
    """v1 down, v0 answering a body with no `instances`: on main this read as an
    empty account and closed the cost row of a running GPU."""
    _key(monkeypatch)
    calls = _provider(
        monkeypatch,
        lambda m, url, p: (503, {"error": "busy"}) if url.startswith(V1) else (200, {"success": True}),
    )
    cloud_manager.track_cloud_resource("vast", 60940198, hourly_rate=1.1)

    with pytest.raises(RuntimeError):
        cloud_manager.list_vast_instances("key")
    assert cloud_manager.reconcile_cloud_resources() == []
    assert _open("60940198")
    assert all(url.startswith(V1) for _, url, _ in calls)


@pytest.mark.parametrize("status, body", [
    (410, GONE_BODY),
    (200, GONE_BODY),
    (200, {"success": True, "msg": "ok"}),  # no `instances` key at all
    (200, {"instances": None}),
    (200, []),
])
def test_a_listing_without_instances_is_an_error_not_an_empty_account(monkeypatch, rows, status, body):
    _key(monkeypatch)
    _provider(monkeypatch, lambda m, url, p: (status, body))
    cloud_manager.track_cloud_resource("vast", 60940198, hourly_rate=1.1)

    with pytest.raises(RuntimeError):
        cloud_manager._fetch_vast_inventory("key")  # noqa: SLF001
    assert cloud_manager.reconcile_cloud_resources() == []
    assert _open("60940198")


def test_the_v1_listing_shape_is_parsed_across_pages(monkeypatch, rows):
    pages = {
        None: {"instances": [{"id": 1, "actual_status": "running"}], "next_token": "t2",
               "total_instances": 2, "label_counts": {}},
        "t2": {"instances": [{"id": 2, "actual_status": "exited"}], "next_token": None,
               "total_instances": 2, "label_counts": {}},
    }
    calls = _provider(monkeypatch, lambda m, url, p: (200, pages[p.get("after_token")]))

    items, complete = cloud_manager._fetch_vast_inventory("key")  # noqa: SLF001
    assert [item["id"] for item in items] == [1, 2] and complete is True
    assert [url for _, url, _ in calls] == [f"{V1}/instances/"] * 2
    assert json.loads(calls[0][2]["order_by"]) == [{"col": "id", "dir": "asc"}]
    assert calls[1][2]["after_token"] == "t2"


def test_fewer_rows_than_total_instances_is_not_a_complete_inventory(monkeypatch, rows):
    _key(monkeypatch)
    _provider(monkeypatch, lambda m, url, p: (200, {
        "instances": [{"id": 1, "actual_status": "running"}], "next_token": None, "total_instances": 2,
    }))
    cloud_manager.track_cloud_resource("vast", 60940198, hourly_rate=1.1)

    assert cloud_manager._fetch_vast_inventory("key")[1] is False  # noqa: SLF001
    cloud_manager.reconcile_cloud_resources()
    assert _open("60940198")


# --- one instance -----------------------------------------------------------------


def test_the_single_instance_lookup_reads_vast_cli_show_instance(monkeypatch, rows):
    calls = _provider(monkeypatch, lambda m, url, p: (200, {"instances": {
        "id": 60940198, "actual_status": "running", "dph_total": 1.1,
    }}))
    item = cloud_manager.observe_vast_instance("key", 60940198)
    assert item["id"] == 60940198 and item["is_running"] is True
    assert calls == [("GET", f"{V0}/instances/60940198/", {"owner": "me"})]


def test_an_instance_vast_does_not_have_is_none_and_opens_no_row(monkeypatch, rows):
    """`instances: null` is Vast's "no such instance": main recorded it as running."""
    _provider(monkeypatch, lambda m, url, p: (200, {"instances": None}))
    assert cloud_manager.observe_vast_instance("key", 60999999) is None
    item = cloud_manager.get_vast_instance("key", 60999999)
    assert item["status"] == "not_found" and item["is_running"] is False
    assert not _open("60999999")


@pytest.mark.parametrize("status, body", [(500, {"error": "x"}), (200, GONE_BODY), (200, {"msg": "?"})])
def test_a_failed_single_lookup_raises(monkeypatch, rows, status, body):
    _provider(monkeypatch, lambda m, url, p: (status, body))
    with pytest.raises(RuntimeError):
        cloud_manager.observe_vast_instance("key", 60940198)


# --- auto-training reconcile --------------------------------------------------------


def _active_job(tmp_path, monkeypatch) -> dict:
    monkeypatch.setattr(auto_training, "_job_dir", lambda: tmp_path)
    job = {"id": "auto_1794830000000", "project_id": 4, "state": "running", "phase": "train",
           "instance_id": 60940198, "run_id": "run_1"}
    auto_training._save(job)  # noqa: SLF001
    return job


def test_reconcile_with_an_erroring_provider_keeps_the_job_open(monkeypatch, rows, tmp_path):
    """The listing answers a body with no `instances` and the instance lookup fails:
    on main the "empty" listing failed the job of a GPU that was still training."""
    _key(monkeypatch)
    job = _active_job(tmp_path, monkeypatch)
    _provider(monkeypatch, lambda m, url, p: (200, {"success": True, "msg": "moved"})
              if url.endswith("/instances/") else (502, {"error": "bad gateway"}))

    auto_training.reconcile_jobs()
    assert auto_training.get_job(job["id"])["state"] == "running"


def test_reconcile_fails_the_job_only_when_vast_says_the_instance_is_gone(monkeypatch, rows, tmp_path):
    _key(monkeypatch)
    job = _active_job(tmp_path, monkeypatch)
    _provider(monkeypatch, lambda m, url, p: (200, {"instances": None}))

    auto_training.reconcile_jobs()
    after = auto_training.get_job(job["id"])
    assert after["state"] == "failed" and after["instance_destroyed"] is True
