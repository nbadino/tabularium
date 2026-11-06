"""Saved models run on the API key of the user who starts the run.

A saved model (a compute profile used by Recognition and by model comparisons) reaches a
paid API: OpenCode for Gemini, LlamaCloud for OCRia. Each user stores their own key for
it, encrypted; a run uses the key of whoever started it; a user without a key cannot use
the model and is told how to add one. Nobody, administrators included, can read another
user's key back, not even masked.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import users as usersvc

PASSWORD = "password123"
ADMIN_KEY = "llx-admin-secret-0001"
EDITOR_KEY = "llx-editor-secret-0002"
OCRIA = {
    "url": "https://api.cloud.eu.llamaindex.ai",
    "model": "ocria-1",
    "adapter_id": "llamaindex-ocria-1",
    "provider": "llamaindex",
    "api_key": ADMIN_KEY,
}
ARGON = {
    "url": "https://opencode.ai/inference/go/google/v1beta",
    "model": "gemini-4-argon",
    "adapter_id": "gemini-argon",
    "api_key": "",
}


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", "test-vault-key")
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM project_members")
        conn.execute("DELETE FROM consensus_runs")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM user_model_keys")
        conn.execute("DELETE FROM compute_profiles")
        conn.execute("DELETE FROM users")
    yield


def _user(username: str, role: str) -> dict:
    with connect() as conn:
        return usersvc.create_user(conn, username, PASSWORD, role=role)


def _login(username: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def _project(admin: TestClient, root: Path, members: list[dict], pages: int = 2) -> tuple[int, list[int]]:
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    for index in range(pages):
        Image.new("RGB", (200, 300), "white").save(archive / f"p{index}.png")
    created = admin.post("/api/projects", json={"name": "Keys", "archive_dir": str(archive)})
    assert created.status_code in (200, 201), created.text
    project_id = created.json()["id"]
    admin.post(f"/api/projects/{project_id}/scan")
    with connect() as conn:
        for member in members:
            conn.execute(
                "INSERT INTO project_members (project_id, user_id, role) VALUES (?,?,'editor')",
                (project_id, member["id"]),
            )
    items = admin.get(f"/api/projects/{project_id}/pages").json()["items"]
    return project_id, [page["id"] for page in items]


def _save(admin: TestClient, cfg: dict, name: str) -> dict:
    from app.services import inference

    admin.get("/api/consensus/profiles")
    inference.save_inference_config(cfg)
    response = admin.post("/api/consensus/profiles", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


class _Recorder:
    """Stands in for `client_for_profile`: remembers the key each model was given."""

    def __init__(self):
        self.keys: list[tuple[str, str]] = []

    def __call__(self, profile, *, api_key, **_kwargs):
        self.keys.append((str(profile["name"]), api_key))
        return _FakeClient()


class _FakeClient:
    model = "fake"

    def __init__(self):
        from app.services.model_adapters import GeminiArgonAdapter

        self.adapter = GeminiArgonAdapter()

    def end2end(self, image, **_kwargs):
        return [{"bbox": [50, 20, 950, 80], "label": "Title", "content": "Arrivi"}]


@pytest.fixture()
def recorder(monkeypatch):
    from app.services import consensus, prefill

    rec = _Recorder()
    monkeypatch.setattr(consensus.inference, "client_for_profile", rec)
    monkeypatch.setattr(prefill, "_looks_like_table", lambda *_args: False)
    return rec


def _wait_consensus(client: TestClient, project_id: int, run_id: int) -> dict:
    body: dict = {}
    for _ in range(300):
        body = client.get(f"/api/projects/{project_id}/consensus-runs/{run_id}").json()
        if body["state"] not in {"queued", "running"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run still {body.get('state')}")


def _wait_recognition(client: TestClient, project_id: int, run_id: int) -> dict:
    body: dict = {}
    for _ in range(300):
        body = client.get(f"/api/projects/{project_id}/recognition-runs/{run_id}").json()
        if body["state"] not in {"queued", "running"}:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run still {body.get('state')}")


def test_saving_a_model_keeps_the_key_as_the_admins_own():
    admin_user = _user("admin", "admin")
    _user("editor", "editor")
    admin, editor = _login("admin"), _login("editor")
    saved = _save(admin, OCRIA, "OCRia")
    assert saved["key_required"] is True and saved["has_key"] is True

    with connect() as conn:
        row = conn.execute("SELECT credential_ref FROM compute_profiles WHERE id=?", (saved["id"],)).fetchone()
        owners = conn.execute("SELECT user_id FROM user_model_keys WHERE profile_id=?", (saved["id"],)).fetchall()
        stored = conn.execute("SELECT ciphertext FROM user_model_keys").fetchone()["ciphertext"]
    assert row["credential_ref"] is None, "the profile no longer carries a shared key"
    assert [r["user_id"] for r in owners] == [admin_user["id"]]
    assert ADMIN_KEY not in stored, "keys are encrypted at rest"

    listed = {p["id"]: p for p in editor.get("/api/consensus/profiles").json()["items"]}
    assert listed[saved["id"]]["key_required"] is True
    assert listed[saved["id"]]["has_key"] is False


def test_a_user_without_a_key_cannot_use_the_model(tmp_path, recorder):
    _user("admin", "admin")
    editor_user = _user("editor", "editor")
    admin, editor = _login("admin"), _login("editor")
    ocria = _save(admin, OCRIA, "OCRia")
    argon = _save(admin, {**ARGON, "api_key": "sk-admin-argon"}, "Argon")
    project_id, pages = _project(admin, tmp_path, [editor_user])

    comparison = editor.post(
        f"/api/projects/{project_id}/consensus-runs",
        json={"page_ids": pages, "profile_ids": [ocria["id"], argon["id"]]},
        headers={"Accept-Language": "en"},
    )
    assert comparison.status_code == 409
    assert "OCRia" in comparison.json()["detail"] and "key" in comparison.json()["detail"].lower()

    recognition = editor.post(
        f"/api/projects/{project_id}/recognition-runs",
        json={"page_ids": pages, "profile_id": ocria["id"], "stop_policy": "none"},
        headers={"Accept-Language": "it"},
    )
    assert recognition.status_code == 409
    assert "chiave" in recognition.json()["detail"].lower()
    assert recorder.keys == [], "nothing reached the model"


def test_each_run_uses_the_key_of_the_user_who_starts_it(tmp_path, recorder):
    _user("admin", "admin")
    editor_user = _user("editor", "editor")
    admin, editor = _login("admin"), _login("editor")
    ocria = _save(admin, OCRIA, "OCRia")
    argon = _save(admin, {**ARGON, "api_key": "sk-admin-argon"}, "Argon")
    project_id, pages = _project(admin, tmp_path, [editor_user])

    for profile, key in ((ocria, EDITOR_KEY), (argon, "sk-editor-argon")):
        response = editor.put(f"/api/consensus/profiles/{profile['id']}/key", json={"api_key": f"  {key}  "})
        assert response.status_code == 200, response.text
        assert response.json() == {"id": profile["id"], "key_required": True, "has_key": True}
        assert key not in response.text

    run = editor.post(
        f"/api/projects/{project_id}/consensus-runs",
        json={"page_ids": pages, "profile_ids": [ocria["id"], argon["id"]]},
    )
    assert run.status_code == 202, run.text
    assert _wait_consensus(editor, project_id, run.json()["id"])["state"] == "finished"
    assert sorted(set(recorder.keys)) == [("Argon", "sk-editor-argon"), ("OCRia", EDITOR_KEY)]

    recorder.keys.clear()
    started = admin.post(
        f"/api/projects/{project_id}/recognition-runs",
        json={"page_ids": pages[:1], "profile_id": ocria["id"], "stop_policy": "none"},
    )
    assert started.status_code == 202, started.text
    _wait_recognition(admin, project_id, started.json()["id"])
    assert recorder.keys == [("OCRia", ADMIN_KEY)]


def test_a_retry_uses_the_key_of_whoever_retries(tmp_path, recorder, monkeypatch):
    _user("admin", "admin")
    editor_user = _user("editor", "editor")
    admin, editor = _login("admin"), _login("editor")
    ocria = _save(admin, OCRIA, "OCRia")
    argon = _save(admin, {**ARGON, "api_key": "sk-admin-argon"}, "Argon")
    project_id, pages = _project(admin, tmp_path, [editor_user])
    editor.put(f"/api/consensus/profiles/{ocria['id']}/key", json={"api_key": EDITOR_KEY})
    editor.put(f"/api/consensus/profiles/{argon['id']}/key", json={"api_key": "sk-editor-argon"})

    from app.services import consensus

    def failing(profile, *, api_key, **_kwargs):
        raise RuntimeError("401 invalid key")

    monkeypatch.setattr(consensus.inference, "client_for_profile", failing)
    run_id = admin.post(
        f"/api/projects/{project_id}/consensus-runs",
        json={"page_ids": pages, "profile_ids": [ocria["id"], argon["id"]]},
    ).json()["id"]
    assert _wait_consensus(admin, project_id, run_id)["state"] == "finished_with_errors"

    monkeypatch.setattr(consensus.inference, "client_for_profile", recorder)
    retried = editor.post(f"/api/projects/{project_id}/consensus-runs/{run_id}/retry")
    assert retried.status_code == 202, retried.text
    assert _wait_consensus(editor, project_id, run_id)["state"] == "finished"
    assert sorted(set(recorder.keys)) == [("Argon", "sk-editor-argon"), ("OCRia", EDITOR_KEY)]


def test_a_recognition_retry_stays_on_the_saved_model(tmp_path, recorder, monkeypatch):
    """Retrying a saved-model recognition used to fall back to the active model."""
    _user("admin", "admin")
    admin = _login("admin")
    ocria = _save(admin, OCRIA, "OCRia")
    project_id, pages = _project(admin, tmp_path, [])
    from app.services import consensus

    class Refused(_FakeClient):
        def end2end(self, image, **_kwargs):
            raise RuntimeError("401 invalid key")

    monkeypatch.setattr(consensus.inference, "client_for_profile", lambda profile, *, api_key, **_k: Refused())
    run = admin.post(
        f"/api/projects/{project_id}/recognition-runs",
        json={"page_ids": pages, "profile_id": ocria["id"], "stop_policy": "none"},
    ).json()
    assert _wait_recognition(admin, project_id, run["id"])["state"] in {"failed", "finished_with_errors"}

    monkeypatch.setattr(consensus.inference, "client_for_profile", recorder)
    retried = admin.post(f"/api/projects/{project_id}/recognition-runs/{run['id']}/retry")
    assert retried.status_code == 202, retried.text
    _wait_recognition(admin, project_id, retried.json()["id"])
    assert recorder.keys and {name for name, _key in recorder.keys} == {"OCRia"}


def test_nobody_reads_another_users_key():
    _user("admin", "admin")
    _user("editor", "editor")
    admin, editor = _login("admin"), _login("editor")
    ocria = _save(admin, OCRIA, "OCRia")
    editor.put(f"/api/consensus/profiles/{ocria['id']}/key", json={"api_key": EDITOR_KEY})

    # The admin removes their own key: the editor's stays, and neither shows up anywhere.
    assert admin.delete(f"/api/consensus/profiles/{ocria['id']}/key").status_code == 204
    mine = {p["id"]: p for p in admin.get("/api/consensus/profiles").json()["items"]}
    theirs = {p["id"]: p for p in editor.get("/api/consensus/profiles").json()["items"]}
    assert mine[ocria["id"]]["has_key"] is False
    assert theirs[ocria["id"]]["has_key"] is True
    for client in (admin, editor):
        for path in ("/api/consensus/profiles", "/api/system/compute-profiles", "/api/system/inference"):
            body = client.get(path).text
            assert EDITOR_KEY not in body and EDITOR_KEY[-4:] not in body, path


def test_key_routes_need_a_login_and_a_real_saved_model():
    _user("admin", "admin")
    admin = _login("admin")
    ocria = _save(admin, OCRIA, "OCRia")
    anonymous = TestClient(app)
    assert anonymous.put(f"/api/consensus/profiles/{ocria['id']}/key", json={"api_key": "x"}).status_code == 401
    assert anonymous.delete(f"/api/consensus/profiles/{ocria['id']}/key").status_code == 401
    assert admin.put("/api/consensus/profiles/99999/key", json={"api_key": "x"}).status_code == 404
    assert admin.put(f"/api/consensus/profiles/{ocria['id']}/key", json={"api_key": "   "}).status_code == 422


def test_deleting_a_user_or_a_model_deletes_its_keys():
    _user("admin", "admin")
    editor_user = _user("editor", "editor")
    admin, editor = _login("admin"), _login("editor")
    ocria = _save(admin, OCRIA, "OCRia")
    editor.put(f"/api/consensus/profiles/{ocria['id']}/key", json={"api_key": EDITOR_KEY})
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (editor_user["id"],))
        conn.execute("DELETE FROM users WHERE id=?", (editor_user["id"],))
        assert conn.execute("SELECT count(*) FROM user_model_keys WHERE user_id=?", (editor_user["id"],)).fetchone()[0] == 0
    assert admin.delete(f"/api/consensus/profiles/{ocria['id']}").status_code == 204
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM user_model_keys").fetchone()[0] == 0
