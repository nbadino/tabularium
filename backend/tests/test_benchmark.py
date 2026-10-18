"""Test M17: benchmark multi-modello sulle pagine gold.

Contratto: metriche riusate da `evaluate`, nessuna scrittura nel DB, cache per
(adapter, model, page, revision), classifica per F1@0.75.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import benchmark as benchmod
from app.services import inference as infmod


def _gold_project(client: TestClient, root: Path) -> int:
    archive = root / "archive"
    archive.mkdir(parents=True)
    for name in ("p.png", "q.png"):
        Image.new("RGB", (900, 1300), (215, 215, 215)).save(archive / name)
    pid = client.post("/api/projects", json={"name": "Bench", "archive_dir": str(archive)}).json()["id"]
    client.post(f"/api/projects/{pid}/scan")
    pages = client.get(f"/api/projects/{pid}/pages").json()["items"]
    for pg in pages:
        client.put(
            f"/api/pages/{pg['id']}/annotations",
            json={"items": [
                {"label": "Title", "kind": "rect", "points": [[10, 10], [890, 90]],
                 "content": "HISTORIC SHIPPING INDEX", "order_idx": 1},
                {"label": "Table", "kind": "rect", "points": [[10, 100], [890, 900]],
                 "content": "", "order_idx": 2},
            ]},
        )
        blocks = client.get(f"/api/pages/{pg['id']}/annotations").json()["items"]
        table = next(b for b in blocks if b["label"] == "Table")
        client.put(
            f"/api/blocks/{table['id']}/table",
            json={"rows": 1, "cols": 1,
                  "cells": [{"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "Abidjan"}]},
        )
    from app.db import connect

    with connect() as conn:
        conn.execute("UPDATE pages SET status='approved' WHERE project_id=?", (pid,))
    return pid


def _fake_client_factory(calls: list):
    """Client finto: l'output dipende dal nome modello, per testare la classifica."""

    class Adapter:
        native_prefill_mode = "end2end"

    class FakeClient:
        def __init__(self, url=None, model=None, adapter=None, api_key=None, provider=None, **kwargs):
            self.url = url or "http://fake/v1"
            self.model = model or "fake"
            self.adapter = Adapter()

        def ping(self):
            return True

        def end2end(self, image):
            calls.append(self.model)
            if self.model == "good":
                return [
                    {"bbox": [11, 8, 989, 69], "label": "Title", "content": "HISTORIC SHIPPING INDEX"},
                    {"bbox": [11, 77, 989, 692], "label": "Table", "content": "<fcel>Abidjan<nl>"},
                ]
            return [
                # label sbagliata e bbox fuori: peggio del riferimento
                {"bbox": [0, 0, 200, 100], "label": "Page-footer", "content": "HISTORIC SHIPPING INDEX"},
            ]

    return FakeClient


def test_benchmark_ranks_and_reuses_metrics_without_writing_db(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(infmod, "VllmClient", _fake_client_factory(calls))
    monkeypatch.setattr(
        "app.services.evaluate.pagesvc.load_source_image",
        lambda page: Image.new("RGB", (900, 1300)),
    )
    with TestClient(app) as client:
        pid = _gold_project(client, tmp_path)
        from app.db import connect

        with connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]

        report = benchmod.run_benchmark(
            pid,
            [{"adapter_id": "lightonocr-3", "model": "good"},
             {"adapter_id": "lightonocr-3", "model": "bad"}],
            limit=2, with_text=True,
        )

        with connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        assert after == before, "il benchmark non deve scrivere blocchi nel DB"

    assert report["gold_pages"] == 2
    assert [e["model"] for e in report["leaderboard"]] == ["good", "bad"]
    best = report["leaderboard"][0]
    assert best["aggregates"]["layout_strict"]["f1"] == 1.0
    assert best["aggregates"]["tables"]["structure_ok_pct"] == 100.0
    assert Path(report["bench_dir"], "report.json").exists()
    assert Path(report["bench_dir"], "report.html").exists()
    # Le metriche sono quelle di `evaluate`, non una reimplementazione.
    assert best["aggregates"]["layout"]["n_gt"] == 4  # 2 pagine × (Title + Table)


def test_benchmark_cache_avoids_repeat_model_calls(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(infmod, "VllmClient", _fake_client_factory(calls))
    monkeypatch.setattr(
        "app.services.evaluate.pagesvc.load_source_image",
        lambda page: Image.new("RGB", (900, 1300)),
    )
    with TestClient(app) as client:
        pid = _gold_project(client, tmp_path)
        targets = [{"adapter_id": "lightonocr-3", "model": "good"}]
        benchmod.run_benchmark(pid, targets, limit=2, with_text=False, use_cache=True)
        first = len(calls)
        assert first == 2
        benchmod.run_benchmark(pid, targets, limit=2, with_text=False, use_cache=True)
        assert len(calls) == first, "la seconda run deve leggere dalla cache"


def test_benchmark_api_rejects_empty_targets(tmp_path):
    with TestClient(app) as client:
        pid = _gold_project(client, tmp_path)
        res = client.post(f"/api/projects/{pid}/benchmark", json={"targets": []})
        assert res.status_code == 400


def test_reconcile_marks_interrupted_benchmark_and_clears_serve_pid():
    """All'avvio un benchmark `running` non ha thread vivo: va marcato interrotto
    e il server locale orfano (pid registrato) spento."""
    benchmod._write_state(999999, state="running", serve_pid=999999)
    benchmod.reconcile_benchmarks()
    state = benchmod.benchmark_status(999999)
    assert state["state"] == "error"
    assert state["serve_pid"] is None


def test_benchmark_target_client_uses_opencode_store_key(monkeypatch):
    """Il gateway Argon non ha chiave nel vault: il target la legge da opencode."""
    monkeypatch.setenv("TABULARIUM_OPENCODE_KEY", "store-key")
    client = benchmod._target_client({
        "adapter_id": "gemini-argon",
        "url": "https://opencode.ai/inference/go/google/v1beta",
        "model": "gemini-4-argon-contributor",
    })
    assert client.api_key == "store-key"


def test_render_html_tolerates_missing_aggregates():
    """Un target che non ha valutato pagine ha aggregati `None`: il report HTML
    non deve andare in 500 formattando None."""
    report = {
        "project_id": 1, "gold_pages": 0, "scope": "gold",
        "config": {"with_text": False, "limit": 1, "use_cache": True},
        "leaderboard": [{
            "adapter_id": "x", "model": "m", "url": "u", "reachable": False,
            "pages_evaluated": 0, "seconds": 0.0,
            "aggregates": {
                "layout": {"f1": 0.0}, "layout_strict": {"f1": 0.0},
                "order": {"mean_levenshtein_norm": None},
                "tables": {"structure_ok_pct": None}, "text": {"mean_cer": None},
            },
        }],
    }
    html = benchmod._render_html(report)
    assert "<table>" in html and "—" in html


def test_bulk_approve_skips_drafts_unless_confirmed(tmp_path):
    from app.db import connect

    with TestClient(app) as client:
        pid = _gold_project(client, tmp_path)
        with connect() as conn:
            conn.execute("UPDATE pages SET status='new' WHERE project_id=?", (pid,))
            conn.execute(
                "UPDATE blocks SET confirmed=0 WHERE page_id IN (SELECT id FROM pages WHERE project_id=?)",
                (pid,),
            )
        # Senza conferma delle bozze: nulla è approvabile.
        first = client.post(f"/api/projects/{pid}/pages/approve", json={}).json()
        assert first["approved"] == 0 and first["skipped_count"] == 2
        # Con `confirm_drafts`: conferma e approva tutto in blocco.
        second = client.post(
            f"/api/projects/{pid}/pages/approve", json={"confirm_drafts": True}
        ).json()
        assert second["approved"] == 2 and second["confirmed_blocks"] >= 4
        with connect() as conn:
            approved = conn.execute(
                "SELECT COUNT(*) FROM pages WHERE project_id=? AND status='approved'", (pid,)
            ).fetchone()[0]
        assert approved == 2
