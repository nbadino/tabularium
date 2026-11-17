"""Renting only Vast.ai datacenter (Secure Cloud) hosts, on request.

On community hosts the one-click training hit SSH sessions that dropped, a
container that exited mid-run, and 84 KB/s of real download on a host that
advertised 3.9 Gb/s. `datacenter_only` restricts the search to datacenter hosts:
the Vast filter `datacenter` (official vast-cli, `search offers`: "show only
datacenter offers") and, on each offer, `hosting_type` 1 (seen on the live
`/search/asks/` answer: 1 with the filter on, 0 for community hosts). When no
such offer exists the error says so; it never falls back to community hosts.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auto_training, cloud_manager, i18n

DC = {"id": 1, "gpu_name": "A100 PCIE", "compute_cap": 800, "cuda_max_good": 12.8,
      "gpu_ram": 81920, "dph_total": 0.74, "cpu_arch": "amd64", "hosting_type": 1}
COMMUNITY = {**DC, "id": 2, "gpu_name": "RTX A6000", "dph_total": 0.32, "hosting_type": 0}
UNKNOWN = {k: v for k, v in {**DC, "id": 3}.items() if k != "hosting_type"}


def _search(monkeypatch, offers, **kwargs):
    sent: list[dict] = []

    def fake_call(_key, _method, _path, json_body=None, **_k):
        sent.append(json_body["q"])
        return {"offers": offers}

    monkeypatch.setattr(cloud_manager, "_vast_call", fake_call)
    return cloud_manager.search_vast_offers("key", **kwargs), sent[0]


def test_search_sends_the_datacenter_filter_only_when_asked(monkeypatch):
    found, query = _search(monkeypatch, [DC, COMMUNITY, UNKNOWN])
    assert "datacenter" not in query
    assert [o["id"] for o in found] == [1, 2, 3]
    assert [o["hosting_type"] for o in found] == [1, 0, None]
    _found, query = _search(monkeypatch, [DC], datacenter_only=True)
    assert query["datacenter"] == {"eq": True}


def test_search_rechecks_hosting_type_locally(monkeypatch):
    """A community or unlabelled offer that slips through the provider filter is dropped."""
    found, _query = _search(monkeypatch, [DC, COMMUNITY, UNKNOWN], datacenter_only=True)
    assert [o["id"] for o in found] == [1]
    assert found[0]["datacenter"] is True


def test_rent_refuses_a_community_offer_when_datacenter_only(monkeypatch):
    calls: list = []
    monkeypatch.setattr(cloud_manager, "_vast_call", lambda *a, **k: calls.append(a) or {"new_contract": 9})
    reason = cloud_manager.offer_unfit_reason(
        "", 800, 12.8, "US", gpu_name="RTX A6000", datacenter_only=True, hosting_type=0,
    )
    assert reason and "datacenter" in reason
    assert cloud_manager.offer_unfit_reason(
        "", 800, 12.8, "US", gpu_name="A100", datacenter_only=True, hosting_type=None,
    )
    assert cloud_manager.offer_unfit_reason(
        "", 800, 12.8, "US", gpu_name="A100", datacenter_only=True, hosting_type=1,
    ) is None
    assert cloud_manager.offer_unfit_reason("", 800, 12.8, "US", gpu_name="A100", hosting_type=0) is None
    with pytest.raises(ValueError, match="datacenter"):
        cloud_manager.rent_vast_instance(
            "key", 2, cuda_max_good=12.8, compute_cap=800, cpu_arch="amd64", gpu_name="RTX A6000",
            hosting_type=0, datacenter_only=True,
        )
    assert calls == []
    # The reason reaches the reader in their language.
    assert "datacenter" in i18n.localize_detail(reason, "en")
    assert "datacenter" in i18n.localize_detail(reason, "fr")


def _job(adapter: str = "glm-ocr") -> dict:
    return {"id": "auto_dc", "project_id": 4, "adapter_id": adapter, "state": "queued",
            "phase": "queued", "message": "", "offer": None, "instance_id": None}


def test_auto_run_searches_datacenter_and_says_when_there_is_none(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(cloud_manager, "search_vast_offers", lambda *_a, **k: seen.update(k) or [])
    saved: list[dict] = []
    monkeypatch.setattr(auto_training, "_save", lambda job: saved.append(dict(job)))
    job = _job()
    auto_training._run(  # noqa: SLF001
        job, {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", "glm", 1, "it", 1.5, 40, 120,
        threading.Event(), datacenter_only=True,
    )
    assert seen["datacenter_only"] is True
    assert job["state"] == "failed"
    assert "datacenter" in job["error"] and "1.50" in job["error"]
    english = i18n.localize_detail(job["error"], "en")
    assert "no datacenter" in english.lower() and "1.50" in english


def test_auto_run_rechecks_the_offer_at_rent_time(monkeypatch):
    rented: dict = {}
    monkeypatch.setattr(cloud_manager, "search_vast_offers", lambda *_a, **_k: [dict(DC, location="US")])
    monkeypatch.setattr(cloud_manager, "rent_vast_instance",
                        lambda *a, **k: rented.update(k) or (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(auto_training, "_save", lambda _job: None)
    auto_training._run(  # noqa: SLF001
        _job(), {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", "glm", 1, "it", 1.5, 40, 120,
        threading.Event(), datacenter_only=True,
    )
    assert rented["datacenter_only"] is True and rented["hosting_type"] == 1


def test_auto_run_defaults_to_any_host(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(cloud_manager, "search_vast_offers", lambda *_a, **k: seen.update(k) or [])
    monkeypatch.setattr(auto_training, "_save", lambda _job: None)
    job = _job()
    auto_training._run(  # noqa: SLF001
        job, {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", "glm", 1, "it", 1.5, 40, 120,
        threading.Event(),
    )
    assert seen["datacenter_only"] is False
    assert "datacenter" not in job["error"]


def test_training_auto_route_forwards_and_persists_the_choice(tmp_path: Path, monkeypatch):
    from app.services import vault

    seen: dict = {}

    def fake_start(*_a, **kwargs):
        seen.update(kwargs)
        return {"id": "auto_1", "adapter_id": "glm-ocr", "profile": "glm",
                "datacenter_only": kwargs.get("datacenter_only")}

    monkeypatch.setattr(auto_training, "start_auto", fake_start)
    monkeypatch.setattr(vault, "get", lambda _ref: "key")
    archive = tmp_path / "archive"
    archive.mkdir()
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "DC", "archive_dir": str(archive)}).json()["id"]
        cfg = {"adapter_id": "glm-ocr", "train_type": "lora"}
        res = client.post(f"/api/projects/{pid}/training/auto", json={"config": cfg, "datacenter_only": True})
        assert res.status_code == 200, res.text
        assert seen["datacenter_only"] is True
        client.post(f"/api/projects/{pid}/training/auto", json={"config": cfg})
        assert seen["datacenter_only"] is False


def test_start_auto_stores_the_choice_in_the_job(monkeypatch):
    from app.services import trainer

    class _Src:
        def glob(self, _pattern):
            return iter([Path("x_train.jsonl")])

    started: list = []
    saved: list[dict] = []
    monkeypatch.setattr(trainer, "_dataset_source", lambda _pid: (_Src(), "snap"))
    monkeypatch.setattr(trainer, "preflight", lambda *_a, **_k: {"dataset": {"snapshot_id": "snap"}})
    monkeypatch.setattr(auto_training, "_active_for_project", lambda _pid: None)
    monkeypatch.setattr(auto_training, "_save", lambda job: saved.append(dict(job)))
    monkeypatch.setattr(auto_training, "_run", lambda *a, **k: started.append(k))
    job = auto_training.start_auto(
        4, {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", owner_id=1, datacenter_only=True,
    )
    auto_training._THREADS[job["id"]].join(timeout=5)  # noqa: SLF001
    assert job["datacenter_only"] is True and saved[0]["datacenter_only"] is True
    assert started[0]["datacenter_only"] is True


def test_cloud_routes_forward_datacenter_only(monkeypatch):
    from app.services import vault

    monkeypatch.setattr(vault, "get", lambda _ref: "key")
    seen: dict = {}
    monkeypatch.setattr(cloud_manager, "search_vast_offers", lambda *_a, **k: seen.update(search=k) or [])
    monkeypatch.setattr(cloud_manager, "rent_vast_instance",
                        lambda *_a, **k: seen.update(rent=k) or {"contract_id": None})
    with TestClient(app) as client:
        res = client.post("/api/system/cloud/vast/offers", json={"api_key": "k", "datacenter_only": True})
        assert res.status_code == 200, res.text
        assert seen["search"]["datacenter_only"] is True
        res = client.post("/api/system/cloud/vast/rent", json={
            "api_key": "k", "offer_id": 1, "datacenter_only": True, "hosting_type": 1,
        })
        assert res.status_code == 200, res.text
        assert seen["rent"]["datacenter_only"] is True and seen["rent"]["hosting_type"] == 1
        res = client.post("/api/system/cloud/vast/rent-training", json={
            "api_key": "k", "datacenter_only": True, "max_dph": 1.2,
        })
        assert res.status_code == 400
        assert seen["search"]["datacenter_only"] is True
        assert "datacenter" in res.json()["detail"] and "1.20" in res.json()["detail"]
