"""Consensus decisions are shared: two people on the same page see, revert and comment them.

What a person settles in the model comparison (an area that is fine as it is, a model's
reading taken for a cell, "my reading is right", a column confirmed at once, a model's
table added, a model's whole reading adopted) is a row in `consensus_decisions`, with
who, when, the value before and after, and an optional comment. The "settled" state of
an area is derived from those rows, so it is the same in every browser. Reverting puts
the value before back and is a decision itself.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import connect, init_db
from app.main import app
from app.services.users import create_user

PASSWORD = "Decisions2026!"

GRID = {
    "rows": 2,
    "cols": 2,
    "cells": [
        {"r": 0, "c": 0, "text": "Nave", "source": "model", "verified": False},
        {"r": 0, "c": 1, "text": "Da", "source": "model", "verified": False},
        {"r": 1, "c": 0, "text": "Aurora", "source": "model", "verified": False},
        {"r": 1, "c": 1, "text": "Genovo", "source": "model", "verified": False},
    ],
}

MODEL_TABLE = {
    "label": "Table",
    "points": [[10, 10], [90, 90]],
    "content": "",
    "order_idx": 1,
    "grid": GRID,
}


@pytest.fixture()
def team(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "on")
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM users")
        ids = {
            name: create_user(conn, name, PASSWORD, role=role)["id"]
            for name, role in [
                ("anna", "editor"), ("bruno", "editor"), ("carla", "viewer"), ("outsider", "editor"),
            ]
        }
        pid = conn.execute(
            "INSERT INTO projects(name,root_dir,owner_id) VALUES('Lloyd','/tmp',?)", (ids["anna"],)
        ).lastrowid
        other = conn.execute(
            "INSERT INTO projects(name,root_dir,owner_id) VALUES('Other','/tmp',?)", (ids["outsider"],)
        ).lastrowid
        for name, role in [("bruno", "editor"), ("carla", "viewer")]:
            conn.execute(
                "INSERT INTO project_members(project_id,user_id,role) VALUES(?,?,?)", (pid, ids[name], role)
            )
        page = conn.execute(
            "INSERT INTO pages(project_id,rel_path,abs_path,width,height) "
            "VALUES(?,'p1.png','/tmp/p1.png',100,100)", (pid,)
        ).lastrowid
        other_page = conn.execute(
            "INSERT INTO pages(project_id,rel_path,abs_path,width,height) "
            "VALUES(?,'o.png','/tmp/o.png',100,100)", (other,)
        ).lastrowid
        text_block = conn.execute(
            "INSERT INTO blocks(page_id,label,points,content,confirmed) "
            "VALUES(?,'Text','[[0,0],[9,9]]','Arrivi nel porto',0)", (page,)
        ).lastrowid
        table_block = conn.execute(
            "INSERT INTO blocks(page_id,label,points,content,confirmed) "
            "VALUES(?,'Table','[[10,10],[90,90]]','',0)", (page,)
        ).lastrowid
        conn.execute(
            "INSERT INTO tables(block_id, grid_json) VALUES(?,?)", (table_block, json.dumps(GRID))
        )
        models = [
            {"key": "p1", "profile_id": 1, "name": "OCRia", "adapter_id": "a", "model_name": "m1"},
            {"key": "p2", "profile_id": 2, "name": "Argon", "adapter_id": "b", "model_name": "m2"},
        ]
        run = conn.execute(
            "INSERT INTO consensus_runs(project_id, owner_id, state, models_json) VALUES(?,?,?,?)",
            (pid, ids["anna"], "finished", json.dumps(models)),
        ).lastrowid
        other_run = conn.execute(
            "INSERT INTO consensus_runs(project_id, owner_id, state, models_json) VALUES(?,?,?,?)",
            (other, ids["outsider"], "finished", json.dumps(models)),
        ).lastrowid
        for key, content in (("p1", "Arrivi nel porto"), ("p2", "Arrivi nel porta")):
            blocks = [
                {"label": "Title", "points": [[0, 0], [9, 9]], "content": content, "order_idx": 0},
                MODEL_TABLE,
            ]
            conn.execute(
                "INSERT INTO consensus_outputs(run_id,page_id,model_key,state,blocks_json) "
                "VALUES(?,?,?,'finished',?)", (run, page, key, json.dumps(blocks)),
            )
    return dict(
        ids=ids, pid=pid, page=page, other_page=other_page, text_block=text_block,
        table_block=table_block, run=run, other_run=other_run,
    )


def login(client: TestClient, who: str) -> None:
    assert client.post("/api/auth/login", json={"username": who, "password": PASSWORD}).status_code == 200


def decide(client: TestClient, team: dict, **body):
    return client.post(
        f"/api/pages/{team['page']}/consensus/decisions", json={"run_id": team["run"], **body}
    )


def history(client: TestClient, team: dict) -> dict:
    response = client.get(f"/api/pages/{team['page']}/consensus/decisions")
    assert response.status_code == 200, response.text
    return response.json()


def grid_of(block_id: int) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT grid_json FROM tables WHERE block_id=?", (block_id,)).fetchone()
    return json.loads(row["grid_json"])


def cell(grid: dict, r: int, c: int) -> dict:
    return next(item for item in grid["cells"] if item["r"] == r and item["c"] == c)


def revision(page_id: int) -> int:
    with connect() as conn:
        return int(conn.execute("SELECT annotation_revision FROM pages WHERE id=?", (page_id,)).fetchone()[0])


def test_two_users_share_settled_areas_revert_and_comment(team):
    """Walkthrough: Anna settles an area, Bruno (another browser) sees it, reverts it
    with a reason, and Anna answers in the history."""
    with TestClient(app) as anna, TestClient(app) as bruno:
        login(anna, "anna")
        login(bruno, "bruno")

        response = decide(anna, team, kind="resolved", cluster_id=0, comment="la grafia è dell'originale")
        assert response.status_code == 201, response.text
        settled = response.json()["decision"]
        assert response.json()["resolved"] == [0]

        # Bruno sees Anna's decision with the page's comparison and in the history.
        page = bruno.get(f"/api/pages/{team['page']}/consensus").json()
        assert page["resolved"] == [0]
        items = history(bruno, team)["items"]
        assert [(d["kind"], d["user"]["username"], d["comment"]) for d in items] == [
            ("resolved", "anna", "la grafia è dell'originale"),
        ]
        assert items[0]["before"] == {"resolved": False} and items[0]["after"] == {"resolved": True}
        assert items[0]["revertible"] is True and items[0]["created_at"]

        # Bruno reverts it, saying why: the area is open again for both.
        response = bruno.post(
            f"/api/pages/{team['page']}/consensus/decisions/{settled['id']}/revert",
            json={"comment": "«porta» è un refuso del modello, ricontrolla"},
        )
        assert response.status_code == 201, response.text
        revert = response.json()["decision"]
        assert revert["kind"] == "revert" and revert["about_id"] == settled["id"]
        assert revert["after"] == {"resolved": False}
        assert anna.get(f"/api/pages/{team['page']}/consensus").json()["resolved"] == []

        items = history(anna, team)["items"]
        assert [d["kind"] for d in items] == ["revert", "resolved"]
        assert items[1]["reverted_by"] == revert["id"] and items[1]["revertible"] is False
        assert items[0]["user"]["username"] == "bruno"

        # A decision is reverted once; the revert itself can be reverted (a redo).
        again = bruno.post(f"/api/pages/{team['page']}/consensus/decisions/{settled['id']}/revert", json={})
        assert again.status_code == 409
        redo = anna.post(f"/api/pages/{team['page']}/consensus/decisions/{revert['id']}/revert", json={})
        assert redo.status_code == 201, redo.text
        assert bruno.get(f"/api/pages/{team['page']}/consensus").json()["resolved"] == [0]

        # Anna answers on Bruno's revert.
        response = decide(anna, team, kind="comment", about_id=revert["id"], comment="ok, era «porto»")
        assert response.status_code == 201, response.text
        items = history(bruno, team)["items"]
        assert items[0]["kind"] == "comment" and items[0]["about_id"] == revert["id"]
        assert items[0]["revertible"] is False


def test_settling_twice_is_one_decision(team):
    with TestClient(app) as anna:
        login(anna, "anna")
        assert decide(anna, team, kind="resolved", cluster_id=2).status_code == 201
        response = decide(anna, team, kind="resolved", cluster_id=2)
        assert response.status_code == 200 and response.json()["decision"] is None
        assert len(history(anna, team)["items"]) == 1
        assert decide(anna, team, kind="reopened", cluster_id=2).json()["resolved"] == []


def test_a_cell_confirmed_by_one_user_is_reverted_by_another(team):
    with TestClient(app) as anna, TestClient(app) as bruno:
        login(anna, "anna")
        login(bruno, "bruno")
        block = team["table_block"]
        # The editor saves the grid, then records why the cell changed.
        grid = json.loads(json.dumps(GRID))
        cell(grid, 1, 1).update(text="Genova", source="manual", verified=False)
        assert anna.put(f"/api/blocks/{block}/table", json=grid).status_code == 200
        response = decide(
            anna, team, kind="use_model", block_id=block, model_key="p1",
            before={"cells": [{"r": 1, "c": 1, "text": "Genovo", "source": "model", "verified": False}]},
            after={"cells": [{"r": 1, "c": 1, "text": "Genova", "source": "manual", "verified": False}]},
        )
        assert response.status_code == 201, response.text
        used = response.json()["decision"]
        assert used["model_name"] == "OCRia" and used["revertible"] is True

        cell(grid, 1, 0)["verified"] = True
        cell(grid, 0, 0)["verified"] = True
        assert anna.put(f"/api/blocks/{block}/table", json=grid).status_code == 200
        response = decide(
            anna, team, kind="group_confirm", block_id=block,
            before={"cells": [
                {"r": 1, "c": 0, "text": "Aurora", "verified": False},
                {"r": 0, "c": 0, "text": "Nave", "verified": False},
            ]},
            after={"cells": [
                {"r": 1, "c": 0, "text": "Aurora", "verified": True},
                {"r": 0, "c": 0, "text": "Nave", "verified": True},
            ]},
        )
        group = response.json()["decision"]

        before = revision(team["page"])
        response = bruno.post(
            f"/api/pages/{team['page']}/consensus/decisions/{group['id']}/revert", json={}
        )
        assert response.status_code == 201, response.text
        assert response.json()["annotation_revision"] == before + 1
        saved = grid_of(block)
        assert cell(saved, 1, 0)["verified"] is False and cell(saved, 0, 0)["verified"] is False
        assert cell(saved, 1, 1)["text"] == "Genova"  # the other decision is untouched

        # The cell changed again after the decision: reverting would overwrite that.
        cell(grid, 1, 1).update(text="Genova.", source="manual")
        assert anna.put(f"/api/blocks/{block}/table", json=grid).status_code == 200
        response = bruno.post(f"/api/pages/{team['page']}/consensus/decisions/{used['id']}/revert", json={})
        assert response.status_code == 409
        assert cell(grid_of(block), 1, 1)["text"] == "Genova."
        assert history(bruno, team)["items"][-1]["reverted_by"] is None


def test_a_model_reading_used_for_a_block_is_reverted_on_the_block(team):
    with TestClient(app) as anna, TestClient(app) as bruno:
        login(anna, "anna")
        login(bruno, "bruno")
        block = team["text_block"]
        with connect() as conn:
            conn.execute("UPDATE blocks SET content='Arrivi nel porta' WHERE id=?", (block,))
        response = decide(
            anna, team, kind="use_model", cluster_id=0, model_key="p2",
            before={"blocks": [{"id": str(block), "label": "Text", "content": "Arrivi nel porto"}]},
            after={"blocks": [{"id": str(block), "label": "Text", "content": "Arrivi nel porta"}]},
        )
        assert response.status_code == 201, response.text
        # Taking a model's reading settles the area.
        assert response.json()["resolved"] == [0]
        used = response.json()["decision"]
        response = bruno.post(f"/api/pages/{team['page']}/consensus/decisions/{used['id']}/revert", json={})
        assert response.status_code == 201, response.text
        with connect() as conn:
            content = conn.execute("SELECT content FROM blocks WHERE id=?", (block,)).fetchone()[0]
        assert content == "Arrivi nel porto"
        assert bruno.get(f"/api/pages/{team['page']}/consensus").json()["resolved"] == []


def test_adopting_records_decisions_and_an_added_table_can_be_reverted(team):
    with TestClient(app) as anna, TestClient(app) as bruno:
        login(anna, "anna")
        login(bruno, "bruno")
        pid, run, page = team["pid"], team["run"], team["page"]
        response = anna.post(
            f"/api/projects/{pid}/consensus-runs/{run}/adopt-block",
            json={"model_key": "p2", "page_id": page, "index": 1},
        )
        assert response.status_code == 200, response.text
        added = response.json()["block_id"]
        items = history(bruno, team)["items"]
        assert items[0]["kind"] == "add_table" and items[0]["block_id"] == added
        assert items[0]["model_name"] == "Argon" and items[0]["revertible"] is True

        response = bruno.post(f"/api/pages/{page}/consensus/decisions/{items[0]['id']}/revert", json={})
        assert response.status_code == 201, response.text
        with connect() as conn:
            assert conn.execute("SELECT 1 FROM blocks WHERE id=?", (added,)).fetchone() is None

        response = anna.post(
            f"/api/projects/{pid}/consensus-runs/{run}/adopt",
            json={"model_key": "p1", "page_ids": [page], "replace_human": True},
        )
        assert response.status_code == 200, response.text
        adopted = history(bruno, team)["items"][0]
        assert adopted["kind"] == "adopt_page" and adopted["model_name"] == "OCRia"
        assert adopted["revertible"] is False
        response = bruno.post(f"/api/pages/{page}/consensus/decisions/{adopted['id']}/revert", json={})
        assert response.status_code == 409


def test_a_changed_added_table_is_not_deleted_by_a_revert(team):
    with TestClient(app) as anna:
        login(anna, "anna")
        response = anna.post(
            f"/api/projects/{team['pid']}/consensus-runs/{team['run']}/adopt-block",
            json={"model_key": "p2", "page_id": team["page"], "index": 1},
        )
        added = response.json()["block_id"]
        grid = json.loads(json.dumps(GRID))
        cell(grid, 0, 0)["verified"] = True
        assert anna.put(f"/api/blocks/{added}/table", json=grid).status_code == 200
        decision = history(anna, team)["items"][0]
        response = anna.post(f"/api/pages/{team['page']}/consensus/decisions/{decision['id']}/revert", json={})
        assert response.status_code == 409
        with connect() as conn:
            assert conn.execute("SELECT 1 FROM blocks WHERE id=?", (added,)).fetchone() is not None


def test_decisions_are_guarded_like_the_page(team):
    with TestClient(app) as client:
        login(client, "carla")  # viewer: reads, never writes
        assert history(client, team)["items"] == []
        assert decide(client, team, kind="resolved", cluster_id=0).status_code == 403

        login(client, "outsider")
        assert client.get(f"/api/pages/{team['page']}/consensus/decisions").status_code == 404
        assert decide(client, team, kind="resolved", cluster_id=0).status_code == 404

        login(client, "anna")
        # A run of another project, a block of another page, a model not in the run.
        response = client.post(
            f"/api/pages/{team['page']}/consensus/decisions",
            json={"run_id": team["other_run"], "kind": "resolved", "cluster_id": 0},
        )
        assert response.status_code == 404
        with connect() as conn:
            foreign = conn.execute(
                "INSERT INTO blocks(page_id,label,points,content) VALUES(?,'Text','[[0,0],[1,1]]','x')",
                (team["other_page"],),
            ).lastrowid
        assert decide(client, team, kind="confirm_mine", block_id=foreign,
                      before={"cells": []}, after={"cells": []}).status_code == 404
        assert decide(client, team, kind="use_model", cluster_id=0, model_key="p9",
                      before={"blocks": []}, after={"blocks": []}).status_code == 404
        # Kinds the server writes itself are not accepted from a client.
        assert decide(client, team, kind="adopt_page").status_code == 422
        assert decide(client, team, kind="resolved").status_code == 422
        assert decide(client, team, kind="comment").status_code == 422
        # A decision of another page cannot be reverted through this one.
        settled = decide(client, team, kind="resolved", cluster_id=0).json()["decision"]
        login(client, "outsider")
        assert client.post(
            f"/api/pages/{team['other_page']}/consensus/decisions/{settled['id']}/revert", json={}
        ).status_code == 404


def test_every_decision_reaches_the_pages_live_channel(team, monkeypatch):
    from app.services import live

    sent: list[tuple[int, dict]] = []
    monkeypatch.setattr(live.hub, "publish", lambda page_id, message: sent.append((page_id, message)))
    with TestClient(app) as anna:
        login(anna, "anna")
        decision = decide(anna, team, kind="resolved", cluster_id=1).json()["decision"]
        assert sent[-1][0] == team["page"]
        assert sent[-1][1]["type"] == "consensus_decision"
        assert sent[-1][1]["decision_id"] == decision["id"]
        assert sent[-1][1]["author"]["username"] == "anna"

        sent.clear()
        grid = json.loads(json.dumps(GRID))
        cell(grid, 0, 1)["verified"] = True
        anna.put(f"/api/blocks/{team['table_block']}/table", json=grid)
        sent.clear()
        confirmed = decide(
            anna, team, kind="confirm_mine", block_id=team["table_block"],
            before={"cells": [{"r": 0, "c": 1, "text": "Da", "verified": False}]},
            after={"cells": [{"r": 0, "c": 1, "text": "Da", "verified": True}]},
        ).json()["decision"]
        sent.clear()
        anna.post(f"/api/pages/{team['page']}/consensus/decisions/{confirmed['id']}/revert", json={})
        types = [message["type"] for _, message in sent]
        # The grid changed: the editors reload it, and the histories refresh.
        assert types == ["annotations_changed", "consensus_decision"]
        assert sent[0][1]["revision"] == revision(team["page"])
