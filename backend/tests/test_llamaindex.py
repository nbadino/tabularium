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
    ]
    assert supported_prefill_modes(adapter)["supports_native"] is True
    assert prefill.native_mode(adapter) == "official"


def test_ocria_workflow_defaults_validate_and_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ocria.db")
    init_db()

    defaults = model_settings.get_settings("llamaindex-ocria-1")
    assert defaults["recommended"]["workflow"]["tier"] == "agentic"
    assert defaults["recommended"]["workflow"]["aggressive_table_extraction"] is True
    assert defaults["recommended"]["workflow"]["languages"] == ["eng"]
    assert defaults["recommended"]["serving"]["max_model_len"] is None

    saved = model_settings.save_settings("llamaindex-ocria-1", {
        "workflow": {
            "tier": "agentic",
            "confidence_score_effort": "high",
            "languages": ["eng", "ita"],
            "cost_optimizer": True,
            "crop_enabled": True,
            "crop_box": {"top": 0.05, "bottom": 0.1, "left": 0.0, "right": 0.0},
            "custom_prompt": "Keep table headers.",
        },
    })
    effective = saved["effective"]["workflow"]
    assert effective["confidence_score_effort"] == "high"
    assert effective["languages"] == ["eng", "ita"]
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
            "languages": ["eng", "ita"],
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

    items = client.llamaindex_native_page(Image.new("RGB", (200, 100), "white"))

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
    assert payload["processing_options"]["ocr_parameters"]["languages"] == ["eng", "ita"]
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
