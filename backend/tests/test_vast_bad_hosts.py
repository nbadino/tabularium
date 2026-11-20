"""A Vast.ai host that never starts its container is replaced, not waited on.

On prod (2026-11-17) two one-click trainings on datacenter A100 SXM4 offers stayed
in `waiting_ssh` for 20+ minutes with `actual_status` "created" and `status_msg`
"Error response from daemon: failed to create task for container: ... unresolvable
CDI devices", until a human cancelled them. The second job picked offer 29019280
after 29019285 had failed: another GPU slot of, very likely, the same machine,
because only offer ids were excluded.

The official vast-cli (`wait_for_instance`, used by `self-test machine`) destroys
an instance whose `status_msg` contains "Error" or whose `actual_status` is
"offline"; offers and instances carry `machine_id` ("machine id of instance").
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auto_training, cloud_manager, i18n, trainer

DAEMON_ERROR = (
    "Error response from daemon: failed to create task for container: failed to create shim task: "
    "OCI runtime create failed: could not apply required modification to OCI specification: "
    "error modifying OCI spec: failed to inject CDI devices: unresolvable CDI devices gpu=2: unknown\n"
)


# The first CUDA call of `run_1794954387239` on machine 25723 (A100 SXM4, offer 29019380).
CUDA_BUSY_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<string>", line 1, in <module>\n'
    "RuntimeError: CUDA error: CUDA-capable device(s) is/are busy or unavailable\n"
    "CUDA kernel errors might be asynchronously reported at some other API call, so the stacktrace "
    "below might be incorrect.\n"
)


def _offer(offer_id: int, machine_id: int, dph: float = 1.16) -> dict:
    return {"id": offer_id, "machine_id": machine_id, "gpu_name": "A100 SXM4", "compute_cap": 800,
            "cuda_max_good": 13.2, "gpu_ram": 81920, "dph_total": dph, "cpu_arch": "amd64",
            "hosting_type": 1, "reliability": 0.99}


# --- the Vast fields ---------------------------------------------------------------

def test_search_keeps_the_machine_id_of_each_offer(monkeypatch):
    monkeypatch.setattr(cloud_manager, "_vast_call", lambda *_a, **_k: {"offers": [_offer(29019285, 4242)]})
    found = cloud_manager.search_vast_offers("key")
    assert found[0]["machine_id"] == 4242


def test_search_sends_the_verified_filter_only_when_asked(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(cloud_manager, "_vast_call",
                        lambda _k, _m, _p, json_body=None, **_kw: sent.append(json_body["q"]) or {"offers": []})
    cloud_manager.search_vast_offers("key")
    cloud_manager.search_vast_offers("key", verified_only=True)
    assert "verified" not in sent[0]
    assert sent[1]["verified"] == {"eq": True}


def test_instance_keeps_machine_id_and_actual_status(monkeypatch):
    row = {"id": 61079485, "machine_id": 4242, "actual_status": "created", "status_msg": DAEMON_ERROR}
    monkeypatch.setattr(cloud_manager, "vast_instance_record", lambda *_a: row)
    monkeypatch.setattr(cloud_manager, "track_cloud_resource", lambda *_a, **_k: None)
    monkeypatch.setattr(cloud_manager, "cloud_resource_cost", lambda *_a: None)
    inst = cloud_manager.observe_vast_instance("key", 61079485)
    assert inst["machine_id"] == 4242
    assert inst["actual_status"] == "created"


# --- the rule --------------------------------------------------------------------------

def test_a_daemon_error_fails_the_host_at_once():
    inst = {"is_running": False, "actual_status": "created", "status_message": DAEMON_ERROR}
    reason = cloud_manager.vast_provisioning_failure(inst, 0)
    assert reason and "Error response from daemon" in reason
    assert "Error response from daemon" in i18n.localize_detail(reason, "en")


@pytest.mark.parametrize("status", ["created", "offline"])
def test_created_or_offline_fails_only_after_the_grace_period(status):
    inst = {"is_running": False, "actual_status": status, "status_message": ""}
    assert cloud_manager.vast_provisioning_failure(inst, 60) is None
    reason = cloud_manager.vast_provisioning_failure(inst, cloud_manager.VAST_STUCK_LIMIT_S)
    assert reason and status in reason and "10" in reason


def test_a_slow_pull_gets_twenty_minutes_and_a_running_instance_is_not_a_failure():
    # Real prod status messages of hosts that were only slow: fine inside the limit...
    for message in ("f1844fd52522: Retrying in 1 second\n", "150ae9de14cf: Download complete\n"):
        inst = {"is_running": False, "actual_status": "loading", "status_message": message}
        assert cloud_manager.vast_provisioning_failure(inst, cloud_manager.VAST_LOADING_LIMIT_S - 1) is None
        # ...but a host still pulling after it is replaced, not paid to wait.
        reason = cloud_manager.vast_provisioning_failure(inst, cloud_manager.VAST_LOADING_LIMIT_S)
        assert reason and "loading" in reason and "20" in reason
    assert cloud_manager.vast_provisioning_failure(
        {"is_running": True, "actual_status": "running", "status_message": "Error: old"}, 3600) is None


# --- the one-click run -------------------------------------------------------------------

def _job(job_id: str = "auto_new") -> dict:
    return {"id": job_id, "project_id": 4, "adapter_id": "glm-ocr", "profile": "glm", "state": "queued",
            "phase": "queued", "message": "", "error": None, "log": "", "offer": None,
            "instance_id": None, "host": None, "port": None, "run_id": None,
            "instance_destroyed": False, "created_at": "now", "updated_at": "now"}


class _FakeVast:
    """Offers and instances: `bad` machines report the daemon error."""

    def __init__(self, offers: list[dict], bad: set[int], cuda_busy: set[int] | None = None):
        self.offers = offers
        self.bad = bad
        self.cuda_busy = cuda_busy or set()
        self.preflights: list[int] = []
        self.rented: list[int] = []
        self.deleted: list[int] = []
        self.machine_of: dict[int, int] = {}
        self.searches: list[dict] = []

    def install(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(auto_training, "_job_dir", lambda: tmp_path)
        monkeypatch.setattr(auto_training, "_save", lambda _job: None)
        monkeypatch.setattr(auto_training, "SSH_POLL_S", 0)
        monkeypatch.setattr(cloud_manager, "search_vast_offers", self.search)
        monkeypatch.setattr(cloud_manager, "rent_vast_instance", self.rent)
        monkeypatch.setattr(cloud_manager, "track_cloud_resource", lambda *_a, **_k: None)
        monkeypatch.setattr(cloud_manager, "observe_vast_instance", self.observe)
        monkeypatch.setattr(cloud_manager, "control_vast_instance",
                            lambda _key, iid, action: self.deleted.append(int(iid)) if action == "delete" else None)
        monkeypatch.setattr(cloud_manager, "pin_ssh_host_key", lambda *_a, **_k: {"ok": True})
        monkeypatch.setattr(cloud_manager, "check_ssh_access", lambda *_a, **_k: {"ok": True})
        ready = '{"python": "/opt/venv/bin/python", "repo": "/opt/LLaMA-Factory"}'
        monkeypatch.setattr(auto_training, "CUDA_PREFLIGHT_RETRY_S", 0)

        def ssh(host, _port, command, **_k):
            if "torch" not in command:
                return subprocess.CompletedProcess([], 0, ready, "")
            machine = int(host.split(".")[0].removeprefix("gpu"))
            self.preflights.append(machine)
            if machine in self.cuda_busy:
                return subprocess.CompletedProcess([], 1, CUDA_BUSY_TRACEBACK, "")
            return subprocess.CompletedProcess([], 0, f"{auto_training.CUDA_PREFLIGHT_OK} NVIDIA A100-SXM4-80GB\n", "")

        monkeypatch.setattr(auto_training, "_ssh", ssh)
        monkeypatch.setattr(trainer, "start_run", self.start_run)
        monkeypatch.setattr(auto_training, "_monitor_run", lambda *_a, **_k: None)

    def start_run(self, *_a, **_k):
        self.started = getattr(self, "started", 0) + 1
        return {"run": {"run_id": "run_1"}}

    def search(self, *_a, **kwargs):
        self.searches.append(kwargs)
        return [dict(o) for o in self.offers]

    def rent(self, _key, offer_id, **_kwargs):
        instance_id = 61079485 + len(self.rented)
        self.rented.append(int(offer_id))
        self.machine_of[instance_id] = next(o["machine_id"] for o in self.offers if o["id"] == offer_id)
        return {"contract_id": instance_id}

    def observe(self, _key, instance_id, **_kwargs):
        if instance_id in self.deleted:
            return None
        machine = self.machine_of[instance_id]
        if machine in self.bad:
            return {"id": instance_id, "machine_id": machine, "is_running": False, "status": "created",
                    "actual_status": "created", "status_message": DAEMON_ERROR}
        return {"id": instance_id, "machine_id": machine, "is_running": True, "status": "running",
                "actual_status": "running", "status_message": "success", "ssh_host": f"gpu{machine}.example",
                "ssh_port": 2222}


def _run(job: dict, **kwargs) -> None:
    auto_training._run(  # noqa: SLF001
        job, {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", "glm", 1, "it", 1.5, 40, 120,
        threading.Event(), **kwargs,
    )


def test_a_broken_host_is_destroyed_and_another_machine_is_rented(monkeypatch, tmp_path):
    # Two GPU slots of the broken machine 4242 are the best offers; 7 is healthy.
    vast = _FakeVast([_offer(29019285, 4242), _offer(29019280, 4242), _offer(31000001, 7, dph=1.2)], bad={4242})
    vast.install(monkeypatch, tmp_path)
    job = _job()
    _run(job)
    assert vast.rented == [29019285, 31000001], "the second slot of the broken machine must be skipped"
    assert vast.deleted == [61079485]
    assert job["state"] == "running" and job["run_id"] == "run_1"
    assert job["instance_id"] == 61079486 and job["instance_destroyed"] is False
    assert job["failed_hosts"][0]["machine_id"] == 4242
    assert job["failed_hosts"][0]["offer_id"] == 29019285
    assert "Error response from daemon" in job["failed_hosts"][0]["reason"]
    assert job["offer"]["machine_id"] == 7


def test_retries_are_bounded_and_the_failure_says_why(monkeypatch, tmp_path):
    offers = [_offer(100 + i, 900 + i) for i in range(6)]
    vast = _FakeVast(offers, bad={o["machine_id"] for o in offers})
    vast.install(monkeypatch, tmp_path)
    job = _job()
    _run(job)
    assert len(vast.rented) == auto_training.MAX_HOST_ATTEMPTS
    assert sorted(vast.deleted) == sorted(61079485 + i for i in range(auto_training.MAX_HOST_ATTEMPTS))
    assert job["state"] == "failed" and job["instance_destroyed"] is True
    assert str(auto_training.MAX_HOST_ATTEMPTS) in job["error"] and "24" in job["error"]
    assert "Error response from daemon" in job["error"]
    assert "24 hours" in i18n.localize_detail(job["error"], "en")


def test_no_other_offer_after_a_failed_host_is_explicit(monkeypatch, tmp_path):
    vast = _FakeVast([_offer(29019285, 4242), _offer(29019280, 4242)], bad={4242})
    vast.install(monkeypatch, tmp_path)
    job = _job()
    _run(job, datacenter_only=True)
    assert vast.rented == [29019285]
    assert job["state"] == "failed"
    english = i18n.localize_detail(job["error"], "en")
    assert "no other Vast.ai offer" in english and "1 failed host" in english


def test_a_host_stuck_in_created_is_replaced(monkeypatch, tmp_path):
    vast = _FakeVast([_offer(1, 11), _offer(2, 22, dph=1.2)], bad=set())
    vast.install(monkeypatch, tmp_path)
    monkeypatch.setattr(cloud_manager, "VAST_STUCK_LIMIT_S", 0)
    healthy = vast.observe

    def observe(key, instance_id, **kwargs):
        inst = healthy(key, instance_id, **kwargs)
        if inst and inst["machine_id"] == 11:
            return {**inst, "is_running": False, "status": "created", "actual_status": "created",
                    "status_message": "", "ssh_host": None, "ssh_port": None}
        return inst

    monkeypatch.setattr(cloud_manager, "observe_vast_instance", observe)
    job = _job()
    _run(job)
    assert vast.rented == [1, 2] and vast.deleted == [61079485]
    assert job["state"] == "running"
    assert "created" in job["failed_hosts"][0]["reason"]


def _write_previous(tmp_path: Path, name: str, payload: dict, *, age_s: float = 0) -> None:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"id": name, "project_id": 4, "adapter_id": "glm-ocr", **payload}))
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))


def test_recent_failures_exclude_the_machine_not_only_the_offer(monkeypatch, tmp_path):
    # A job cancelled while provisioning on machine 4242, one whose host was replaced
    # on machine 5 (in another project), an old failure, and a finished run.
    _write_previous(tmp_path, "auto_a", {"state": "cancelled", "run_id": None,
                                         "offer": {"id": 29019285, "machine_id": 4242}})
    _write_previous(tmp_path, "auto_b", {"project_id": 9, "state": "running", "run_id": "r",
                                         "failed_hosts": [{"machine_id": 5, "offer_id": 50}]})
    _write_previous(tmp_path, "auto_c", {"state": "failed", "run_id": None,
                                         "offer": {"id": 60, "machine_id": 6}}, age_s=25 * 3600)
    _write_previous(tmp_path, "auto_d", {"state": "finished", "run_id": "r2",
                                         "offer": {"id": 70, "machine_id": 7}})
    vast = _FakeVast([_offer(29019280, 4242, dph=0.9), _offer(51, 5, dph=0.9), _offer(61, 6, dph=1.3),
                      _offer(71, 7, dph=1.4)], bad=set())
    vast.install(monkeypatch, tmp_path)
    job = _job()
    _run(job)
    # Offer 61 (machine 6, failed over 24 h ago) is the best remaining: highest price,
    # same VRAM, so best_offer would pick 51 or 29019280 if they were not excluded.
    assert vast.rented == [61]


def test_verified_only_reaches_the_search(monkeypatch, tmp_path):
    vast = _FakeVast([_offer(1, 11)], bad=set())
    vast.install(monkeypatch, tmp_path)
    _run(_job(), verified_only=True)
    assert vast.searches[0]["verified_only"] is True
    _run(_job("auto_other"))
    assert vast.searches[1]["verified_only"] is False


def test_training_auto_route_forwards_and_persists_verified_only(tmp_path: Path, monkeypatch):
    from app.services import vault

    seen: dict = {}
    monkeypatch.setattr(auto_training, "start_auto", lambda *_a, **k: seen.update(k) or {
        "id": "auto_1", "adapter_id": "glm-ocr", "profile": "glm"})
    monkeypatch.setattr(vault, "get", lambda _ref: "key")
    archive = tmp_path / "archive"
    archive.mkdir()
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "V", "archive_dir": str(archive)}).json()["id"]
        cfg = {"adapter_id": "glm-ocr", "train_type": "lora"}
        res = client.post(f"/api/projects/{pid}/training/auto", json={"config": cfg, "verified_only": True})
        assert res.status_code == 200, res.text
        assert seen["verified_only"] is True
        client.post(f"/api/projects/{pid}/training/auto", json={"config": cfg})
        assert seen["verified_only"] is False


def test_start_auto_stores_verified_only_in_the_job(monkeypatch):
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
        4, {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", owner_id=1, verified_only=True,
    )
    auto_training._THREADS[job["id"]].join(timeout=5)  # noqa: SLF001
    assert job["verified_only"] is True and saved[0]["verified_only"] is True
    assert started[0]["verified_only"] is True


# --- a GPU that does not answer CUDA (prod, 2026-11-17) -----------------------------------
# Job `auto_1794953975433` rented offer 29019380 on machine 25723; its run failed a
# minute in at the first CUDA call, after the dataset sync, and the job neither
# recorded the host nor tried another one.

def test_a_busy_gpu_is_caught_before_the_sync_and_the_host_replaced(monkeypatch, tmp_path):
    vast = _FakeVast([_offer(29019380, 25723), _offer(29019381, 25723), _offer(31000001, 7, dph=1.2)],
                     bad=set(), cuda_busy={25723})
    vast.install(monkeypatch, tmp_path)
    job = _job()
    _run(job)
    assert vast.rented == [29019380, 31000001], "the other slot of the busy machine must be skipped"
    assert vast.deleted == [61079485]
    assert vast.preflights == [25723, 25723, 7], "the preflight is retried once, then the host replaced"
    assert getattr(vast, "started", 0) == 1, "training (and the dataset sync) start only on the healthy host"
    assert job["state"] == "running" and job["instance_id"] == 61079486
    failed = job["failed_hosts"]
    assert [(f["offer_id"], f["machine_id"]) for f in failed] == [(29019380, 25723)]
    assert "busy or unavailable" in failed[0]["reason"]
    assert "does not answer CUDA" in i18n.localize_detail(failed[0]["reason"], "en")
    # The next job skips the machine too.
    (tmp_path / "auto_new.json").write_text(json.dumps(job))
    assert auto_training._recently_failed_hosts(_job("auto_next")) == ({29019380}, {25723})  # noqa: SLF001


def test_busy_gpus_count_against_the_retry_budget(monkeypatch, tmp_path):
    offers = [_offer(100 + i, 900 + i) for i in range(6)]
    vast = _FakeVast(offers, bad=set(), cuda_busy={o["machine_id"] for o in offers})
    vast.install(monkeypatch, tmp_path)
    job = _job()
    _run(job)
    assert len(vast.rented) == auto_training.MAX_HOST_ATTEMPTS
    assert getattr(vast, "started", 0) == 0
    assert job["state"] == "failed" and job["instance_destroyed"] is True
    assert "not usable" in i18n.localize_detail(job["error"], "en")


def test_an_image_without_torch_fails_the_job_not_the_host(monkeypatch, tmp_path):
    vast = _FakeVast([_offer(1, 11), _offer(2, 22)], bad=set())
    vast.install(monkeypatch, tmp_path)
    ready = '{"python": "/opt/venv/bin/python", "repo": "/opt/LLaMA-Factory"}'
    monkeypatch.setattr(auto_training, "_ssh", lambda _h, _p, cmd, **_k: subprocess.CompletedProcess(
        [], 1 if "torch" in cmd else 0,
        "ModuleNotFoundError: No module named 'torch'\n" if "torch" in cmd else ready, ""))
    job = _job()
    _run(job)
    assert vast.rented == [1] and job["state"] == "failed"
    assert "torch" in job["error"] and not job.get("failed_hosts")


def test_preflight_uses_the_training_python_and_passes_on_success(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(auto_training, "_ssh", lambda _h, _p, cmd, **_k: seen.append(cmd) or subprocess.CompletedProcess(
        [], 0, f"{auto_training.CUDA_PREFLIGHT_OK} NVIDIA A100\n", ""))
    assert auto_training._cuda_preflight("h", 22, "/opt/venv/bin/python", threading.Event()) is None  # noqa: SLF001
    assert seen[0].startswith("timeout 120 /opt/venv/bin/python -c ")
    assert 'torch.ones(1, device="cuda")' in seen[0] and "synchronize" in seen[0]


def _monitor_failed_run(monkeypatch, tmp_path, log: str, error: str = "manifest remoto vuoto: nessun checkpoint recuperato"):
    run_dir = tmp_path / "run_1794954387239"
    run_dir.mkdir()
    (run_dir / "train.log").write_text(log)
    monkeypatch.setattr(auto_training, "_save", lambda _job: None)
    monkeypatch.setattr(trainer, "_load_run", lambda *_a: (run_dir, {"state": "failed", "error": error}))
    monkeypatch.setattr(trainer, "write_run_meta", lambda *_a: None)
    monkeypatch.setattr(auto_training, "_instance", lambda *_a: None)
    deleted: list[int] = []
    monkeypatch.setattr(cloud_manager, "control_vast_instance",
                        lambda _key, iid, action: deleted.append(int(iid)) if action == "delete" else None)
    job = {**_job("auto_1794953975433"), "state": "running", "run_id": "run_1794954387239",
           "instance_id": 61079485, "offer": {"id": 29019380, "machine_id": 25723}}
    auto_training._monitor_run(job, "key", 4, "run_1794954387239", 61079485, threading.Event())  # noqa: SLF001
    assert deleted == [61079485] and job["state"] == "failed"
    return job


def test_a_training_failed_on_a_busy_gpu_records_the_host(monkeypatch, tmp_path):
    # The real log: the CUDA error, then a long torchrun shutdown report after it.
    log = ("[rank0]: " + CUDA_BUSY_TRACEBACK.replace("\n", "\n[rank0]: ")
           + "\n" + "E1117 torch/distributed/elastic/multiprocessing/api.py: failed\n" * 400)
    job = _monitor_failed_run(monkeypatch, tmp_path, log)
    failed = job["failed_hosts"]
    assert [(f["offer_id"], f["machine_id"], f["instance_id"]) for f in failed] == [(29019380, 25723, 61079485)]
    assert "busy or unavailable" in failed[0]["reason"]
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "auto_1794953975433.json").write_text(json.dumps(job))
    monkeypatch.setattr(auto_training, "_job_dir", lambda: tmp_path / "jobs")
    assert auto_training._recently_failed_hosts({**_job("auto_next"), "project_id": 9}) == ({29019380}, {25723})  # noqa: SLF001


def test_an_ordinary_training_failure_does_not_blame_the_host(monkeypatch, tmp_path):
    job = _monitor_failed_run(monkeypatch, tmp_path, "torch.OutOfMemoryError: CUDA out of memory.\n",
                              error="training failed")
    assert not job.get("failed_hosts")
