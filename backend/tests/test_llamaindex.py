from __future__ import annotations

import pytest
from PIL import Image
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app import config
from app.db import connect, init_db
from app.main import app
from app.services import inference as infmod
from app.services import model_settings
from app.services import prefill
from app.services import vault
from app.services.model_adapters import get_adapter, supported_prefill_modes


def test_ocria_adapter_maps_native_items_rotation_and_nested_containers():
    adapter = get_adapter("llamaindex-ocria-1")
    pages = [{
        "page_width": 1000,
        "page_height": 1000,
        "items": [
            {
                "type": "header",
                "bbox": [{"x": 10, "y": 10, "w": 900, "h": 50, "r": 0}],
                "md": "HISTORIC SHIPPING INDEX",
                "items": [{"type": "text", "bbox": [{"x": 20, "y": 20, "w": 100, "h": 20}], "value": "duplicate"}],
            },
            {"type": "heading", "level": 1, "bbox": [{"x": 20, "y": 80, "w": 300, "h": 40}], "value": "Index"},
            {"type": "heading", "level": 3, "bbox": [{"x": 20, "y": 130, "w": 300, "h": 30}], "value": "Casualties"},
            {"type": "text", "bbox": [{"x": 100, "y": 100, "w": 100, "h": 50, "r": 90}], "value": "Rotated text"},
            {"type": "table", "bbox": [{"x": 20, "y": 200, "w": 900, "h": 500}], "html": "<table><tr><td>A</td></tr></table>"},
            {"type": "image", "bbox": [{"x": 20, "y": 720, "w": 200, "h": 100}], "value": "ignored"},
            {"type": "text", "bbox": None, "value": "No coordinates"},
        ],
    }]

    assert adapter.parse_native_result(pages) == [
        {"bbox": [10, 10, 910, 60], "label": "Page-header", "content": "HISTORIC SHIPPING INDEX"},
        {"bbox": [20, 80, 320, 120], "label": "Title", "content": "Index"},
        {"bbox": [20, 130, 320, 160], "label": "Section-header", "content": "Casualties"},
        {"bbox": [125, 75, 175, 175], "label": "Text", "content": "Rotated text"},
        {"bbox": [20, 200, 920, 700], "label": "Table", "content": "<table><tr><td>A</td></tr></table>"},
        {"bbox": [20, 720, 220, 820], "label": "Picture", "content": ""},
        # Read but not placed: kept, in the band after the last placed item.
        {"bbox": [10, 820, 920, 1000], "label": "Text", "content": "No coordinates"},
    ]
    assert supported_prefill_modes(adapter)["supports_native"] is True
    assert prefill.native_mode(adapter) == "official"


def test_ocria_keeps_a_table_its_layout_model_did_not_place():
    """Lloyd's Daily Index p. 10 (run 5): LlamaParse's layout model saw only the
    header and the adverts, its vision model still read the 67-row register and
    returned it as a `table` with `bbox: null`. The table must survive, in the
    band between the header above and the advert below."""
    adapter = get_adapter("llamaindex-ocria-1")
    pages = [{
        "page_width": 509.76,
        "page_height": 664.8,
        "items": [
            {
                "type": "header",
                "bbox": [{"x": 42.31, "y": 16.62, "w": 286.49, "h": 15.29}],
                "md": "Apr. 1, 1920.] **LLOYD'S DAILY INDEX** 9",
                "items": [{"type": "text", "bbox": [{"x": 42.31, "y": 16.62, "w": 286.49, "h": 15.29}], "value": "x"}],
            },
            {
                "type": "table",
                "md": "|STEAMER—Owner|Flg Reg|\n|-|-|\n|**Apsley**|Br|",
                "html": "<table><tr><th>STEAMER—Owner</th></tr><tr><td>Apsley</td></tr></table>",
                "parse_concerns": [{"type": "layout_lvm_disagreement"}],
            },
            {"type": "heading", "level": 2, "bbox": [{"x": 31.1, "y": 559.76, "w": 368.04, "h": 25.26}],
             "md": "## WATSON & PARKER, MARSEILLES.", "value": "WATSON & PARKER, MARSEILLES."},
            {"type": "heading", "level": 3, "md": "", "value": ""},
            {"type": "footer", "bbox": [{"x": 39.76, "y": 612.95, "w": 443.49, "h": 15.95}], "md": "Mar 29"},
        ],
    }]

    items = adapter.parse_native_result(pages)
    table = [item for item in items if item["label"] == "Table"]
    assert len(table) == 1
    assert table[0]["content"].startswith("<table>")
    x1, y1, x2, y2 = table[0]["bbox"]
    # Below the header (ends at y≈48/1000), above the heading (starts at y≈842/1000).
    assert (y1, y2) == (48, 842)
    # As wide as everything placed on the page (heading left edge to footer right edge).
    assert (x1, x2) == (61, 948)
    # An empty item without a box adds nothing.
    assert [item["label"] for item in items] == ["Page-header", "Table", "Title", "Page-footer"]


def test_ocria_workflow_defaults_validate_and_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria.db")
    init_db()

    defaults = model_settings.get_settings("llamaindex-ocria-1")
    assert defaults["recommended"]["workflow"]["tier"] == "agentic"
    assert defaults["recommended"]["workflow"]["aggressive_table_extraction"] is True
    assert defaults["recommended"]["workflow"]["languages"] == ["en"]
    assert defaults["recommended"]["serving"]["max_model_len"] is None

    saved = model_settings.save_settings("llamaindex-ocria-1", {
        "workflow": {
            "tier": "agentic",
            "confidence_score_effort": "high",
            "languages": ["en", "it"],
            "cost_optimizer": True,
            "crop_enabled": True,
            "crop_box": {"top": 0.05, "bottom": 0.1, "left": 0.0, "right": 0.0},
            "custom_prompt": "Keep table headers.",
        },
    })
    effective = saved["effective"]["workflow"]
    assert effective["confidence_score_effort"] == "high"
    assert effective["languages"] == ["en", "it"]
    assert effective["cost_optimizer"] is True
    assert effective["crop_box"]["top"] == 0.05

    with pytest.raises(HTTPException, match="cost_optimizer richiede"):
        model_settings.save_settings("llamaindex-ocria-1", {
            "workflow": {"tier": "cost_effective"},
        })
    with pytest.raises(HTTPException, match="languages"):
        model_settings.save_settings("llamaindex-ocria-1", {
            "workflow": {"languages": ["not-a-language"]},
        })
    with pytest.raises(HTTPException, match="serving"):
        model_settings.save_settings("llamaindex-ocria-1", {
            "serving": {"max_model_len": 8192},
        })

    reset = model_settings.save_settings("llamaindex-ocria-1", {})
    assert reset["overrides"] == {}


def test_ocria_native_job_uploads_polls_and_sends_native_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria-job.db")
    init_db()
    model_settings.save_settings("llamaindex-ocria-1", {
        "workflow": {
            "tier": "agentic",
            "confidence_score_effort": "high",
            "languages": ["en", "it"],
            "cost_optimizer": True,
            "specialized_chart_parsing": "efficient",
            "job_timeout_in_seconds": 120,
        },
    })

    calls: list[tuple[str, dict]] = []

    class Response:
        def __init__(self, data, status_code=200):
            self._data = data
            self.status_code = status_code
            self.text = ""

        def json(self):
            return self._data

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/v1/beta/files"):
            return Response({"id": "file-1"})
        if url.endswith("/api/v2/parse"):
            return Response({"job": {"id": "job-1", "status": "PENDING"}})
        raise AssertionError(f"unexpected POST {url}")

    def get(url, **kwargs):
        calls.append((url, kwargs))
        assert url.endswith("/api/v2/parse/job-1")
        return Response({
            "job": {"id": "job-1", "status": "COMPLETED"},
            "items": {"pages": [{
                "page_width": 200,
                "page_height": 100,
                "items": [{
                    "type": "table",
                    "bbox": [{"x": 10, "y": 10, "w": 180, "h": 80}],
                    "html": "<table><tr><td>Ship</td></tr></table>",
                }],
            }]},
            "usage": {"credits": 2},
        })

    monkeypatch.setattr(infmod.requests, "post", post)
    monkeypatch.setattr(infmod.requests, "get", get)
    client = infmod.VllmClient(
        url="https://api.cloud.llamaindex.ai/api/v2/parse",
        model="ocria-1",
        api_key="llx-test",
        adapter=get_adapter("llamaindex-ocria-1"),
        provider="llamaindex",
    )

    # The official prefill path always passes `on_delta`: go through it, not the bare method.
    items = prefill.native_page_items(
        client, Image.new("RGB", (200, 100), "white"), on_delta=lambda delta: None, cancel_event=None,
    )

    assert items == [{
        "bbox": [50, 100, 950, 900],
        "label": "Table",
        "content": "<table><tr><td>Ship</td></tr></table>",
    }]
    upload = next(kwargs for url, kwargs in calls if url.endswith("/api/v1/beta/files"))
    assert upload["data"] == {"purpose": "parse"}
    assert upload["headers"]["Authorization"] == "Bearer llx-test"
    start = next(kwargs for url, kwargs in calls if url.endswith("/api/v2/parse"))
    payload = start["json"]
    assert payload["file_id"] == "file-1"
    assert payload["tier"] == "agentic"
    assert payload["version"] == "latest"
    assert payload["processing_options"]["confidence_score_effort"] == "high"
    assert payload["processing_options"]["ocr_parameters"]["languages"] == ["en", "it"]
    assert payload["processing_options"]["cost_optimizer"] == {"enable": True}
    assert payload["processing_options"]["specialized_chart_parsing"] == "efficient"
    assert payload["output_options"]["markdown"]["tables"]["output_tables_as_markdown"] is False
    poll = next(kwargs for url, kwargs in calls if url.endswith("/api/v2/parse/job-1"))
    assert poll["params"]["expand"] == "items,metadata,usage,job_metadata"
    assert client.last_trace["job_id"] == "job-1"


def test_ocria_connection_uses_versions_endpoint():
    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"versions": ["latest", "2026-09-01"]}

    captured = {}

    def get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        return Response()

    original = infmod.requests.get
    infmod.requests.get = get
    try:
        result = infmod.VllmClient(
            url="https://api.cloud.llamaindex.ai",
            model="ocria-1",
            api_key="llx-test",
            adapter=get_adapter("llamaindex-ocria-1"),
            provider="llamaindex",
        ).test_connection(timeout=1)
    finally:
        infmod.requests.get = original

    assert result["ok"] is True
    assert result["models_available"] == ["latest", "2026-09-01"]
    assert captured["url"].endswith("/api/v2/parse/versions")
    assert captured["headers"]["Authorization"] == "Bearer llx-test"


@pytest.mark.skipif(vault.Fernet is None, reason="cryptography non installata nel virtualenv")
def test_ocria_test_endpoint_can_use_saved_backend_key(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria-api.db")
    monkeypatch.setenv("TABULARIUM_VAULT_KEY", vault.Fernet.generate_key().decode("ascii"))
    original_resolve = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: (
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
            if host == "api.cloud.llamaindex.ai"
            else original_resolve(host, port, *args, **kwargs)
        ),
    )
    init_db()
    infmod.save_inference_config({
        "enabled": True,
        "url": "https://api.cloud.llamaindex.ai",
        "model": "ocria-1",
        "api_key": "llx-saved",
        "adapter_id": "llamaindex-ocria-1",
        "provider": "llamaindex",
    })

    captured = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"versions": ["latest"]}

    def get(url, **kwargs):
        captured["headers"] = kwargs["headers"]
        return Response()

    monkeypatch.setattr(infmod.requests, "get", get)
    with TestClient(app) as client:
        response = client.post("/api/system/inference/test", json={
            "url": "https://api.cloud.llamaindex.ai",
            "model": "ocria-1",
            "adapter_id": "llamaindex-ocria-1",
            "provider": "llamaindex",
            "use_saved_credentials": True,
        })
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert captured["headers"]["Authorization"] == "Bearer llx-saved"


def test_provider_detection_and_database_accept_llamaindex(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria-provider.db")
    init_db()
    assert infmod._provider_for_endpoint("https://api.cloud.llamaindex.ai") == "llamaindex"
    assert infmod._adapter_for_endpoint("ocria-1", "https://api.cloud.llamaindex.ai") == "llamaindex-ocria-1"
    with connect() as conn:
        conn.execute(
            "INSERT INTO compute_profiles(name,provider,purpose,model_adapter_id,served_model_name,endpoint) "
            "VALUES(?,?,?,?,?,?)",
            ("ocria", "llamaindex", "inference", "llamaindex-ocria-1", "ocria-1", "https://api.cloud.llamaindex.ai"),
        )
        row = conn.execute("SELECT provider FROM compute_profiles WHERE name='ocria'").fetchone()
    assert row["provider"] == "llamaindex"


def test_ocria_cost_optimizer_rule_follows_the_vendor_tiers(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria-tiers.db")
    init_db()
    # A disabled toggle is not a use of the option: the cheap tier must save.
    saved = model_settings.save_settings("llamaindex-ocria-1", {
        "workflow": {"tier": "cost_effective", "cost_optimizer": False},
    })
    assert saved["effective"]["workflow"]["tier"] == "cost_effective"
    # The vendor accepts the optimizer on both agentic tiers.
    saved = model_settings.save_settings("llamaindex-ocria-1", {
        "workflow": {"tier": "agentic_plus", "cost_optimizer": True},
    })
    assert saved["effective"]["workflow"]["cost_optimizer"] is True


def test_ocria_effective_workflow_drops_keys_the_vendor_renamed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria-stale.db")
    init_db()
    from app.adapters import store

    store.put("model_settings:llamaindex-ocria-1", {
        "workflow": {"fail_on_job_timeout": True, "allowed_failure_ratio": 0.5, "tier": "agentic"},
    })
    effective = model_settings.get_settings("llamaindex-ocria-1")["effective"]["workflow"]
    assert "fail_on_job_timeout" not in effective
    assert "allowed_failure_ratio" not in effective
    # The panel posts the effective workflow back: it must still validate.
    model_settings.save_settings("llamaindex-ocria-1", {"workflow": effective})


def test_every_native_runner_takes_the_prefill_callbacks():
    """`native_page_items` passes `on_delta` and `cancel_event` to every runner."""
    import inspect

    runners = [name for name in dir(infmod.VllmClient) if name.endswith("_native_page")]
    assert "llamaindex_native_page" in runners
    for name in runners:
        params = inspect.signature(getattr(infmod.VllmClient, name)).parameters
        assert {"on_delta", "cancel_event"} <= params.keys(), name


class _RegionResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = "" if status_code < 400 else '{"detail":"Invalid API Key. Please check your region"}'

    def json(self):
        return self._data


def _eu_only(url):
    """LlamaCloud keys are per region: this one is valid on the EU host only."""
    return url.startswith(infmod.LLAMAINDEX_EU_BASE_URL)


def test_ocria_native_job_moves_an_eu_key_to_the_eu_region(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria-region.db")
    init_db()
    monkeypatch.setattr(infmod, "_LLAMAINDEX_REGION", {})
    calls: list[str] = []

    def post(url, **kwargs):
        calls.append(url)
        if not _eu_only(url):
            return _RegionResponse({}, 401)
        if url.endswith("/api/v1/beta/files"):
            return _RegionResponse({"id": "file-1"})
        return _RegionResponse({"job": {"id": "job-1", "status": "PENDING"}})

    def get(url, **kwargs):
        calls.append(url)
        assert _eu_only(url)
        return _RegionResponse({"job": {"id": "job-1", "status": "COMPLETED"}, "items": {"pages": []}})

    monkeypatch.setattr(infmod.requests, "post", post)
    monkeypatch.setattr(infmod.requests, "get", get)

    def page():
        return infmod.VllmClient(
            url=infmod.LLAMAINDEX_BASE_URL, model="ocria-1", api_key="llx-eu",
            adapter=get_adapter("llamaindex-ocria-1"), provider="llamaindex",
        ).llamaindex_native_page(Image.new("RGB", (20, 10), "white"))

    assert page() == []
    assert calls[0].startswith(infmod.LLAMAINDEX_BASE_URL)
    assert all(_eu_only(url) for url in calls[1:])
    # The region is remembered: the next page goes straight to the EU host.
    calls.clear()
    page()
    assert all(_eu_only(url) for url in calls)


def test_ocria_connection_test_finds_the_eu_region_and_a_bad_key_names_it(monkeypatch):
    monkeypatch.setattr(infmod, "_LLAMAINDEX_REGION", {})

    def get(url, **kwargs):
        if _eu_only(url) and kwargs["headers"]["Authorization"] == "Bearer llx-eu":
            return _RegionResponse({"versions": ["latest"]})
        return _RegionResponse({}, 401)

    monkeypatch.setattr(infmod.requests, "get", get)

    def test(key, url=infmod.LLAMAINDEX_BASE_URL):
        return infmod.VllmClient(
            url=url, model="ocria-1", api_key=key,
            adapter=get_adapter("llamaindex-ocria-1"), provider="llamaindex",
        ).test_connection(timeout=1)

    found = test("llx-eu")
    assert found["ok"] is True
    assert found["url"] == infmod.LLAMAINDEX_EU_BASE_URL
    rejected = test("llx-wrong")
    assert rejected["ok"] is False
    assert "regione" in rejected["error"]
    # A host the user typed is theirs: no switching behind their back.
    assert test("llx-eu", url="https://llama.example.org")["ok"] is False
