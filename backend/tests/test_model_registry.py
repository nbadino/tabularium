"""Test per il registro modelli: adapter aggiuntivi, stato installazione, API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import model_adapters, model_registry


def test_new_adapters_are_registered_alongside_monkeyocrv2():
    ids = {item["adapter_id"] for item in model_adapters.list_adapters()}
    assert "monkeyocrv2-parsing" in ids
    for expected in (
        "mineru2.5",
        "dots-ocr",
        "glm-ocr",
        "deepseek-ocr",
        "paddleocr-vl",
        "qwen3-vl-8b",
    ):
        assert expected in ids


def test_unknown_adapter_raises():
    try:
        model_adapters.get_adapter("does-not-exist")
        raise AssertionError("doveva sollevare ValueError")
    except ValueError:
        pass


def test_install_state_reflects_disk_without_weights():
    state = model_registry.install_state("mineru2.5")
    assert state["adapter_id"] == "mineru2.5"
    assert state["installed"] is False
    assert state["downloading"] is False


def test_dots_ocr_serves_but_only_end2end_prompt_is_verified():
    adapter = model_adapters.get_adapter("dots-ocr")
    # Il solo prompt confermato sul README ufficiale è quello end2end.
    try:
        adapter.prompt_for("table")
        raise AssertionError("doveva sollevare NotImplementedError")
    except NotImplementedError:
        pass
    assert "layout information" in adapter.prompt_for("end2end")
    # serve_command è verificato: il comando vLLM ufficiale del README.
    cmd = adapter.serve_command("/tmp/whatever", 8888)
    assert cmd[:3] == ["vllm", "serve", "/tmp/whatever"]
    assert "--trust-remote-code" in cmd


def test_mineru_serves_but_prompts_are_not_implemented():
    adapter = model_adapters.get_adapter("mineru2.5")
    cmd = adapter.serve_command("/tmp/whatever", 8888)
    assert cmd == ["vllm", "serve", "/tmp/whatever", "--port", "8888", "--served-model-name", "mineru2.5"]
    try:
        adapter.prompt_for("layout")
        raise AssertionError("doveva sollevare NotImplementedError")
    except NotImplementedError:
        pass


def test_still_unimplemented_stub_adapter_has_no_serve_command():
    adapter = model_adapters.get_adapter("glm-ocr")
    assert adapter.serve_command("/tmp/whatever", 8888) is None


def test_list_models_api_merges_capabilities_and_install_state():
    with TestClient(app) as client:
        res = client.get("/api/models")
        assert res.status_code == 200
        items = res.json()["items"]
        by_id = {item["adapter_id"]: item for item in items}
        assert "monkeyocrv2-parsing" in by_id
        assert "mineru2.5" in by_id
        assert by_id["mineru2.5"]["installed"] is False
        assert by_id["mineru2.5"]["hf_repo"] == "opendatalab/MinerU2.5-2509-1.2B"


def test_download_unknown_adapter_returns_400():
    with TestClient(app) as client:
        res = client.post("/api/models/does-not-exist/download")
        assert res.status_code == 400


def test_delete_uninstalled_model_is_a_noop():
    with TestClient(app) as client:
        res = client.delete("/api/models/qwen3-vl-8b")
        assert res.status_code == 200
        assert res.json()["installed"] is False
