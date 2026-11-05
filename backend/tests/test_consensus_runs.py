"""Consensus runs: several models on the same pages, without touching the annotations.

A model taking part in a comparison is a saved compute profile (endpoint, model, adapter
and its own credential). A run sends every page to every profile, keeps each model's
blocks in `consensus_outputs`, and the Studio reads one page's comparison back.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app



@pytest.fixture(autouse=True)
def _vault_key(monkeypatch):
    """Saved profiles carry their own credential, encrypted in the vault: the tests
    must not depend on a key happening to be in the developer's environment."""
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "test-vault-key")


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


def test_retrying_a_run_reruns_only_the_failed_outputs(tmp_path, monkeypatch):
    from app.services import consensus

    _fake_clients(monkeypatch)
    calls = {"argon": 0}

    class FlakyArgon(FakeArgon):
        def end2end(self, image, **kwargs):
            calls["argon"] += 1
            if calls["argon"] == 1:
                raise RuntimeError("gateway down")
            return super().end2end(image, **kwargs)

    ocria_calls = {"n": 0}

    class CountingOcria(FakeOcria):
        def llamaindex_native_page(self, image, **kwargs):
            ocria_calls["n"] += 1
            return super().llamaindex_native_page(image, **kwargs)

    flaky = FlakyArgon()
    monkeypatch.setattr(
        consensus.inference,
        "client_for_profile",
        lambda profile, **_k: CountingOcria() if profile["model_adapter_id"] == "llamaindex-ocria-1" else flaky,
    )
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        ocria = _save_profile(client, OCRIA, "OCRia-1 (retry)")
        argon = _save_profile(client, ARGON, "Argon (retry)")
        created = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids[:1], "profile_ids": [ocria["id"], argon["id"]]},
        )
        run = _wait(client, project_id, created.json()["id"])
        assert run["state"] == "finished_with_errors"

        retried = client.post(f"/api/projects/{project_id}/consensus-runs/{run['id']}/retry")
        assert retried.status_code == 202, retried.text
        run = _wait(client, project_id, run["id"])
        assert run["state"] == "finished" and run["failed"] == 0 and run["succeeded"] == 2
        assert ocria_calls["n"] == 1  # the finished output was not sent again
        page = client.get(f"/api/pages/{page_ids[0]}/consensus").json()
        assert page["comparison"] is not None

        again = client.post(f"/api/projects/{project_id}/consensus-runs/{run['id']}/retry")
        assert again.status_code == 409


def test_the_table_editor_gets_the_models_readings_for_my_grid(tmp_path, monkeypatch):
    from app.adapters import blocks as blockstore

    _fake_clients(monkeypatch)
    with TestClient(app) as client:
        project_id, page_ids = _project(client, tmp_path)
        with blockstore.writing() as writer:
            table = writer.insert_block(
                page_ids[0], label="Table", points=[[10, 30], [190, 270]],
                content="", order_idx=1, prefill_source=None,
            )
            other = writer.insert_block(
                page_ids[1], label="Table", points=[[10, 30], [190, 270]],
                content="", order_idx=1, prefill_source=None,
            )
            writer.commit()
        mine = {"rows": 2, "cols": 2, "cells": [
            {"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "Nave"},
            {"r": 0, "c": 1, "rowspan": 1, "colspan": 1, "text": "Da"},
            {"r": 1, "c": 0, "rowspan": 1, "colspan": 1, "text": "Aurora"},
            {"r": 1, "c": 1, "rowspan": 1, "colspan": 1, "text": "Genoa"},
        ]}
        empty = client.post(f"/api/blocks/{table}/consensus", json=mine).json()
        assert empty["run_id"] is None and empty["cells"] == []

        ocria = _save_profile(client, OCRIA, "OCRia-1 (editor)")
        argon = _save_profile(client, ARGON, "Argon (editor)")
        created = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids[:1], "profile_ids": [ocria["id"], argon["id"]]},
        )
        run = _wait(client, project_id, created.json()["id"])

        out = client.post(f"/api/blocks/{table}/consensus", json=mine).json()
        assert out["run_id"] == run["id"]
        assert [m["name"] for m in out["models"]] == ["OCRia-1 (editor)", "Argon (editor)"]
        keys = [m["key"] for m in out["models"]]
        cells = {(c["r"], c["c"]): c for c in out["cells"]}
        assert cells[(1, 1)]["values"] == {keys[0]: "Genova", keys[1]: "Genovo"}
        assert cells[(1, 1)]["score"] > 0
        assert cells[(0, 0)]["score"] == 0.0

        # A page the run did not cover has nothing to show.
        assert client.post(f"/api/blocks/{other}/consensus", json=mine).json()["cells"] == []


def test_adopting_a_model_writes_its_reading_as_drafts_and_spares_human_work(tmp_path, monkeypatch):
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
            writer.insert_block(
                page_ids[1], label="Text", points=[[1, 1], [50, 50]],
                content="old draft", order_idx=1, prefill_source="ocr:rapidocr",
            )
            writer.commit()
        ocria = _save_profile(client, OCRIA, "OCRia-1 (adopt)")
        argon = _save_profile(client, ARGON, "Argon (adopt)")
        created = client.post(
            f"/api/projects/{project_id}/consensus-runs",
            json={"page_ids": page_ids, "profile_ids": [ocria["id"], argon["id"]]},
        ).json()
        run = _wait(client, project_id, created["id"])
        argon_key = next(m["key"] for m in run["models"] if m["profile_id"] == argon["id"])
        url = f"/api/projects/{project_id}/consensus-runs/{run['id']}/adopt"

        assert client.post(url, json={"model_key": "nobody"}).status_code == 404

        with connect() as conn:
            before = conn.execute("SELECT annotation_revision FROM pages WHERE id=?", (page_ids[1],)).fetchone()[0]
        out = client.post(url, json={"model_key": argon_key})
        assert out.status_code == 200, out.text
        body = out.json()
        assert [w["page_id"] for w in body["written"]] == [page_ids[1]]
        assert body["skipped"] == [{"page_id": page_ids[0], "reason": "human_work", "blocks": 1}]

        # The old draft is gone; Argon's three blocks are drafts, the table with its grid.
        blocks = client.get(f"/api/pages/{page_ids[1]}/annotations").json()["items"]
        assert [(b["label"], b["confirmed"]) for b in blocks] == [("Title", False), ("Table", False), ("Note", False)]
        assert all(b["prefill_source"].startswith("model:gemini-4-argon") for b in blocks)
        table = client.get(f"/api/blocks/{blocks[1]['id']}/table").json()
        assert any(cell["text"] == "Genovo" for cell in table["grid"]["cells"])
        with connect() as conn:
            after = conn.execute("SELECT annotation_revision FROM pages WHERE id=?", (page_ids[1],)).fetchone()[0]
        assert after > before

        # The page with human work is untouched until the user says to replace it.
        assert [b["id"] for b in client.get(f"/api/pages/{page_ids[0]}/annotations").json()["items"]] == [mine]
        forced = client.post(url, json={"model_key": argon_key, "page_ids": [page_ids[0]], "replace_human": True}).json()
        assert [w["page_id"] for w in forced["written"]] == [page_ids[0]]
        contents = [b["content"] for b in client.get(f"/api/pages/{page_ids[0]}/annotations").json()["items"]]
        assert "my transcription" not in contents and len(contents) == 3

        missing = client.post(url, json={"model_key": argon_key, "page_ids": [999999]}).json()
        assert missing["written"] == [] and missing["skipped"][0]["reason"] == "no_result"
