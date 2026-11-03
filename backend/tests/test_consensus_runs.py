"""Consensus runs: several models on the same pages, without touching the annotations.

A model taking part in a comparison is a saved compute profile (endpoint, model, adapter
and its own credential). A run sends every page to every profile, keeps each model's
blocks in `consensus_outputs`, and the Studio reads one page's comparison back.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

OCRIA = {
    "url": "https://api.cloud.eu.llamaindex.ai",
    "model": "ocria-1",
    "adapter_id": "llamaindex-ocria-1",
    "provider": "llamaindex",
    "api_key": "llx-ocria-secret",
}
ARGON = {
    "url": "https://opencode.ai/inference/go/google/v1beta",
    "model": "gemini-4-argon",
    "adapter_id": "gemini-argon",
    "api_key": "",
}


def _project(client: TestClient, root: Path) -> tuple[int, list[int]]:
    archive = root / "archive"
    archive.mkdir(parents=True)
    for index in range(2):
        Image.new("RGB", (200, 300), "white").save(archive / f"p{index}.png")
    project_id = client.post(
        "/api/projects", json={"name": "Consensus", "archive_dir": str(archive)}
    ).json()["id"]
    client.post(f"/api/projects/{project_id}/scan")
    pages = client.get(f"/api/projects/{project_id}/pages").json()["items"]
    return project_id, [page["id"] for page in pages]


def _save_profile(client: TestClient, cfg: dict, name: str) -> dict:
    from app.services import inference

    # The first request opens the database (the test client runs the lifespan per call).
    client.get("/api/consensus/profiles")
    inference.save_inference_config(cfg)
    response = client.post("/api/consensus/profiles", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


class FakeOcria:
    model = "ocria-1"

    def __init__(self):
        from app.services.model_adapters import LlamaIndexOcriaAdapter

        self.adapter = LlamaIndexOcriaAdapter()

    def llamaindex_native_page(self, image, *, on_delta=None, cancel_event=None):
        return [
            {"bbox": [50, 20, 950, 80], "label": "Title", "content": "Arrivi nel porto"},
            {
                "bbox": [50, 100, 950, 900],
                "label": "Table",
                "content": (
                    "<table><tr><td>Nave</td><td>Da</td></tr>"
                    "<tr><td>Aurora</td><td>Genova</td></tr></table>"
                ),
            },
        ]


class FakeArgon:
    model = "gemini-4-argon"

    def __init__(self):
        from app.services.model_adapters import GeminiArgonAdapter

        self.adapter = GeminiArgonAdapter()

    def end2end(self, image, **_kwargs):
        return [
            {"bbox": [50, 20, 950, 80], "label": "Title", "content": "Arrivi nel porto"},
            {
                "bbox": [50, 100, 950, 900],
                "label": "Table",
                "content": "<fcel>Nave<fcel>Da<nl><fcel>Aurora<fcel>Genovo<nl>",
            },
            {"bbox": [50, 920, 500, 980], "label": "Note", "content": "Fonte: capitaneria"},
        ]


def _fake_clients(monkeypatch):
    from app.services import consensus, prefill

    def client_for_profile(profile, **_kwargs):
        return FakeOcria() if profile["model_adapter_id"] == "llamaindex-ocria-1" else FakeArgon()

    monkeypatch.setattr(consensus.inference, "client_for_profile", client_for_profile)
    monkeypatch.setattr(prefill, "_looks_like_table", lambda *_args: False)


def _wait(client: TestClient, project_id: int, run_id: int) -> dict:
    body: dict = {}
    for _ in range(200):
        body = client.get(f"/api/projects/{project_id}/consensus-runs/{run_id}").json()
        if body["state"] not in {"queued", "running"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"consensus run still {body.get('state')}")


def test_saving_a_profile_keeps_its_own_key_and_leaves_the_active_model_alone(tmp_path, monkeypatch):
    from app.services import inference, opencode_gateway

    monkeypatch.setattr(opencode_gateway, "read_api_key", lambda: "opencode-machine-key")
    with TestClient(app) as client:
        ocria = _save_profile(client, OCRIA, "OCRia-1 (test)")
        argon = _save_profile(client, ARGON, "Argon (test)")

        assert ocria["model_adapter_id"] == "llamaindex-ocria-1"
        assert ocria["has_credential"] is True and ocria["active"] is False
        assert argon["model_adapter_id"] == "gemini-argon" and argon["active"] is False

        # Switching the global model (and its single key) afterwards does not
        # change what the saved profile sends.
        inference.save_inference_config({**ARGON, "api_key": "another-key"})
        listed = {p["id"]: p for p in client.get("/api/consensus/profiles").json()["items"]}
        assert {ocria["id"], argon["id"]} <= set(listed)

        from app.adapters import compute_profiles as profilestore

        # The API never shows the credential reference; the client reads the stored row.
        assert "credential_ref" not in listed[ocria["id"]]
        ocria_client = inference.client_for_profile(profilestore.get(ocria["id"]))
        assert ocria_client.adapter.adapter_id == "llamaindex-ocria-1"
        assert ocria_client.api_key == "llx-ocria-secret"
        assert ocria_client.url.startswith("https://api.cloud.eu.llamaindex.ai")

        # Argon was saved without a key: the gateway's key on this machine is used.
        argon_client = inference.client_for_profile(profilestore.get(argon["id"]))
        assert argon_client.model == "gemini-4-argon"
        assert argon_client.api_key == "opencode-machine-key"

        # Saving profiles never activates one: the configured model is unchanged.
        cfg = inference.get_inference_config()
        assert cfg["adapter_id"] == "gemini-argon"
        assert cfg["source_profile_id"] is None

        duplicate = client.post("/api/consensus/profiles", json={"name": "OCRia-1 (test)"})
        assert duplicate.status_code == 409

        assert client.delete(f"/api/consensus/profiles/{argon['id']}").status_code == 204
        remaining = {p["id"] for p in client.get("/api/consensus/profiles").json()["items"]}
        assert argon["id"] not in remaining
        client.delete(f"/api/consensus/profiles/{ocria['id']}")


def test_consensus_run_stores_each_model_and_never_writes_annotations(tmp_path, monkeypatch):
    from app.adapters import blocks as blockstore
    from app.db import connect

    _fake_clients(monkeypatch)
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        with blockstore.writing() as writer:
            mine = writer.insert_block(
                page_ids[0], label="Text", points=[[1, 1], [50, 50]],
                content="my transcription", order_idx=1, prefill_source=None,
            )
            writer.commit()
        with connect() as conn:
            revision_before = conn.execute(
                "SELECT annotation_revision FROM pages WHERE id=?", (page_ids[0],)
            ).fetchone()[0]

        ocria = _save_profile(client, OCRIA, "OCRia-1 (run)")
        argon = _save_profile(client, ARGON, "Argon (run)")

        too_few = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids, "profile_ids": [ocria["id"]]},
        )
        assert too_few.status_code == 422

        unknown = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids, "profile_ids": [ocria["id"], 999999]},
        )
        assert unknown.status_code == 404

        created = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids, "profile_ids": [ocria["id"], argon["id"]]},
        )
        assert created.status_code == 202, created.text
        run = _wait(client, project_id, created.json()["id"])
        assert run["state"] == "finished", run
        assert run["total"] == 4 and run["succeeded"] == 4

        # The user's annotations are exactly as they were.
        blocks = client.get(f"/api/pages/{page_ids[0]}/annotations").json()["items"]
        assert [(b["id"], b["content"]) for b in blocks] == [(mine, "my transcription")]
        assert client.get(f"/api/pages/{page_ids[1]}/annotations").json()["items"] == []
        with connect() as conn:
            revision_after = conn.execute(
                "SELECT annotation_revision FROM pages WHERE id=?", (page_ids[0],)
            ).fetchone()[0]
        assert revision_after == revision_before

        page = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        assert page["run_id"] == run["id"]
        assert [m["profile_id"] for m in page["models"]] == [ocria["id"], argon["id"]]
        assert all(m["state"] == "finished" for m in page["models"])
        keys = [m["key"] for m in page["models"]]
        assert len(page["outputs"][keys[0]]) == 2 and len(page["outputs"][keys[1]]) == 3

        clusters = page["comparison"]["clusters"]
        title = next(c for c in clusters if c["label"] == "Title")
        assert title["score"] == 0.0
        table = next(c for c in clusters if c["label"] == "Table")
        cells = {(c["r"], c["c"]): c for c in table["table"]["cells"]}
        assert cells[(1, 1)]["values"] == {keys[0]: "Genova", keys[1]: "Genovo"}
        assert cells[(1, 1)]["score"] > 0 and cells[(0, 0)]["score"] == 0.0
        footnote = next(c for c in clusters if c["label"] == "Note")
        assert footnote["missing"] == [keys[0]] and footnote["score"] == 1.0
        assert 0.0 < page["comparison"]["agreement"] < 1.0

        listed = client.get(f"/api/projects/{project_id}/consensus-runs").json()["items"]
        assert listed[0]["id"] == run["id"]


def test_a_page_without_consensus_says_so(tmp_path):
    with TestClient(app) as client:
        _project_id, page_ids = _project(client, tmp_path)
        body = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        assert body == {"run_id": None, "models": [], "outputs": {}, "comparison": None}


def test_one_model_failing_does_not_stop_the_others(tmp_path, monkeypatch):
    from app.services import consensus

    _fake_clients(monkeypatch)

    class BrokenArgon(FakeArgon):
        def end2end(self, image, **_kwargs):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(
        consensus.inference,
        "client_for_profile",
        lambda profile, **_k: FakeOcria() if profile["model_adapter_id"] == "llamaindex-ocria-1" else BrokenArgon(),
    )
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        ocria = _save_profile(client, OCRIA, "OCRia-1 (fail)")
        argon = _save_profile(client, ARGON, "Argon (fail)")
        created = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids[:1], "profile_ids": [ocria["id"], argon["id"]]},
        )
        run = _wait(client, project_id, created.json()["id"])
        assert run["state"] == "finished_with_errors"
        assert run["succeeded"] == 1 and run["failed"] == 1

        page = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        states = {m["profile_id"]: m for m in page["models"]}
        assert states[ocria["id"]]["state"] == "finished"
        assert states[argon["id"]]["state"] == "failed"
        assert "gateway down" in states[argon["id"]]["error"]
        # With one model the page cannot be compared: say so instead of inventing agreement.
        assert page["comparison"] is None
