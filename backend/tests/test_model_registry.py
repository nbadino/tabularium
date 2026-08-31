"""Test per il registro modelli: adapter aggiuntivi, stato installazione, API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import model_adapters, model_registry


def test_supported_prefill_modes_probes_prompt_for_not_declared_tasks():
    # `capabilities.tasks` non è affidabile per questo (v. docstring di
    # `supported_prefill_modes`): MonkeyOCRv2 sa fare end2end pur non
    # dichiarandolo in `tasks`, dots.ocr dichiara "layout" in `tasks` ma il
    # suo prompt_for lo rifiuta. La sonda deve rispecchiare la realtà, non
    # il campo dichiarativo. `supports_native` è ciò che il prefill offre
    # oggi: basta una via verificata (end2end preferita al due passi).
    monkey = model_adapters.get_adapter("monkeyocrv2-parsing")
    assert model_adapters.supported_prefill_modes(monkey) == {
        "supports_two_stage": True, "supports_end2end": True, "supports_native": True,
    }

    dots = model_adapters.get_adapter("dots-ocr")
    assert model_adapters.supported_prefill_modes(dots) == {
        "supports_two_stage": False, "supports_end2end": True, "supports_native": True,
    }

    mineru = model_adapters.get_adapter("mineru2.5")
    assert model_adapters.supported_prefill_modes(mineru) == {
        "supports_two_stage": True, "supports_end2end": False, "supports_native": True,
    }

    paddle = model_adapters.get_adapter("paddleocr-vl")
    # `supports_native` è True qui non per un prompt verificato, ma per il
    # fallback dichiarato `page_layout_fallback == "ocr"` (il detector OCR
    # locale segmenta, Paddle riconosce i crop) — v. anche
    # `test_paddle_vl_exposes_gpu_recognition_with_explicit_layout_fallback`
    # in test_inference_adapters.py, stessa asserzione.
    assert model_adapters.supported_prefill_modes(paddle) == {
        "supports_two_stage": False, "supports_end2end": False, "supports_native": True,
    }


def test_prefill_engines_endpoint_exposes_supported_modes_for_active_adapter():
    with TestClient(app) as client:
        res = client.get("/api/system/prefill-engines")
        assert res.status_code == 200
        model = res.json()["model"]
        assert "supports_two_stage" in model
        assert "supports_end2end" in model


def test_supports_export_probes_prompt_for_per_family():
    # Il builder chiama `prompt_for` per layout/testo/tabella (v.
    # `dataset_builder`): l'export è possibile solo dove tutte e tre le
    # famiglie hanno un prompt. Solo MonkeyOCRv2 e MinerU2.5 li hanno tutti;
    # dots.ocr e PaddleOCR-VL ne mancano almeno uno, gli stub di sola ricetta
    # non ne hanno nessuno — non vanno offerti nel selettore dell'export.
    assert model_adapters.supports_export(model_adapters.get_adapter("monkeyocrv2-parsing"))
    assert model_adapters.supports_export(model_adapters.get_adapter("mineru2.5"))
    assert not model_adapters.supports_export(model_adapters.get_adapter("dots-ocr"))
    assert not model_adapters.supports_export(model_adapters.get_adapter("paddleocr-vl"))
    assert not model_adapters.supports_export(model_adapters.get_adapter("unlimited-ocr"))
    assert not model_adapters.supports_export(model_adapters.get_adapter("glm-ocr"))
    assert not model_adapters.supports_export(model_adapters.get_adapter("deepseek-ocr"))
    assert not model_adapters.supports_export(model_adapters.get_adapter("qwen3-vl-8b"))


def test_model_adapters_endpoint_exposes_export_ready():
    with TestClient(app) as client:
        res = client.get("/api/system/model-adapters")
        assert res.status_code == 200
        items = {item["adapter_id"]: item for item in res.json()["items"]}
        assert items["monkeyocrv2-parsing"]["export_ready"] is True
        assert items["mineru2.5"]["export_ready"] is True
        assert items["glm-ocr"]["export_ready"] is False


def test_new_adapters_are_registered_alongside_monkeyocrv2():
    ids = {item["adapter_id"] for item in model_adapters.list_adapters()}
    assert "monkeyocrv2-parsing" in ids
    for expected in (
        "mineru2.5",
        "unlimited-ocr",
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


def test_mineru_serves_and_layout_table_prompts_are_implemented():
    adapter = model_adapters.get_adapter("mineru2.5")
    cmd = adapter.serve_command("/tmp/whatever", 8888)
    assert cmd == [
        "vllm", "serve", "/tmp/whatever", "--port", "8888",
        "--logits-processors", "mineru_vl_utils:MinerULogitsProcessor",
        "--dtype", "bfloat16",
        "--gpu-memory-utilization", "0.75",
        "--max-model-len", "16384",
        "--max-num-seqs", "4",
        "--max-num-batched-tokens", "8192",
        "--served-model-name", "mineru2.5",
    ]
    # Protocollo reimplementato (v. docstring dell'adapter): layout/text/
    # table/formula verificati sul client ufficiale, end2end resta non
    # implementato (nessun equivalente in mineru-vl-utils).
    assert adapter.prompt_for("layout") == "\nLayout Detection:"
    assert adapter.prompt_for("table") == "\nTable Recognition:"
    try:
        adapter.prompt_for("end2end")
        raise AssertionError("doveva sollevare NotImplementedError")
    except NotImplementedError:
        pass


def test_mineru_parse_layout_reads_special_token_format():
    adapter = model_adapters.get_adapter("mineru2.5")
    raw = (
        "<|box_start|>10 20 30 40<|box_end|><|ref_start|>table<|ref_end|>"
        "<|box_start|>0 0 999 999<|box_end|><|ref_start|>text<|ref_end|>hello"
    )
    items = adapter.parse_layout(raw)
    assert items == [
        {"bbox": [10, 20, 30, 40], "label": "Table", "content": ""},
        {"bbox": [0, 0, 999, 999], "label": "Text", "content": "hello"},
    ]


def test_mineru_parse_layout_falls_back_to_one_table_block_on_bare_otsl():
    # Verificato live contro l'endpoint reale: su una pagina a piena tabella
    # (il caso normale per il corpus Historic Shipping Index) il modello a
    # volte non emette alcun wrapper <|box_start|>/<|ref_start|> e restituisce
    # direttamente il dump OTSL — va trattato come un blocco Table a piena
    # pagina, non come zero blocchi.
    adapter = model_adapters.get_adapter("mineru2.5")
    raw = "<fcel>Vessel<fcel>Flag<nl><fcel>Almati<fcel>Du<nl>"
    items = adapter.parse_layout(raw)
    assert items == [{"bbox": [0, 0, 1000, 1000], "label": "Table", "content": ""}]


def test_mineru_parse_layout_returns_nothing_for_unrecognizable_output():
    adapter = model_adapters.get_adapter("mineru2.5")
    assert adapter.parse_layout("plain prose with no structure markers at all") == []


def test_mineru_sampling_matches_official_client_defaults():
    adapter = model_adapters.get_adapter("mineru2.5")
    base = {
        "top_p": 0.01,
        "top_k": 1,
        "skip_special_tokens": False,
        "vllm_xargs": {"no_repeat_ngram_size": 100},
    }
    assert adapter.sampling_for("layout") == base
    assert adapter.sampling_for("table") == {
        **base, "presence_penalty": 1.0, "frequency_penalty": 0.005, "max_tokens": 2048,
    }
    assert adapter.sampling_for("text") == {**base, "presence_penalty": 1.0, "frequency_penalty": 0.05}


def test_stub_adapters_are_now_servable_but_still_lack_ocr_integration():
    # Deployment (download + serve locale/cloud) e integrazione OCR (prompt/
    # parsing) sono due lavori separati: questi adapter hanno guadagnato il
    # primo (v. model_adapters.py) ma restano stub sul secondo.
    for adapter_id in ("glm-ocr", "deepseek-ocr", "qwen3-vl-8b"):
        adapter = model_adapters.get_adapter(adapter_id)
        assert adapter.serve_command("/tmp/whatever", 8888) is not None
        try:
            adapter.prompt_for("text")
            raise AssertionError(f"{adapter_id}: doveva sollevare NotImplementedError")
        except NotImplementedError:
            pass


def test_unlimited_ocr_serves_locally_via_dedicated_docker_image():
    # L'architettura non è nella wheel pip stabile di vLLM: il serve locale
    # passa dall'immagine Docker dedicata, non da `vllm serve` diretto.
    adapter = model_adapters.get_adapter("unlimited-ocr")
    cmd = adapter.serve_command("/weights/unlimited-ocr", 8888)
    assert cmd[:2] == ["docker", "run"]
    assert "vllm/vllm-openai:unlimited-ocr" in cmd
    assert "/weights/unlimited-ocr:/model" in cmd


def test_list_models_api_merges_capabilities_and_install_state():
    with TestClient(app) as client:
        res = client.get("/api/models")
        assert res.status_code == 200
        items = res.json()["items"]
        by_id = {item["adapter_id"]: item for item in items}
        assert "monkeyocrv2-parsing" in by_id
        assert "mineru2.5" in by_id
        assert by_id["mineru2.5"]["installed"] is False
        assert by_id["mineru2.5"]["hf_repo"] == "opendatalab/MinerU2.5-Pro-2605-1.2B"
        assert by_id["dots-ocr"]["hf_repo"] == "dots-studio/dots.mocr"
        assert by_id["dots-ocr"]["download_only"] is False
        assert by_id["unlimited-ocr"]["cloud_serve_ready"] is True
        assert by_id["unlimited-ocr"]["cloud_template"] == "unlimited-ocr"
        assert by_id["unlimited-ocr"]["download_only"] is False
        assert by_id["deepseek-ocr"]["hf_repo"] == "deepseek-ai/DeepSeek-OCR-2"
        # Ora servibile in locale (vllm serve generico): non più "solo download".
        assert by_id["glm-ocr"]["download_only"] is False
        assert by_id["glm-ocr"]["local_serve_ready"] is True
        assert by_id["qwen3-vl-8b"]["cloud_template"] == "qwen3-vl"


def test_download_unknown_adapter_returns_400():
    with TestClient(app) as client:
        res = client.post("/api/models/does-not-exist/download")
        assert res.status_code == 400


def test_delete_uninstalled_model_is_a_noop():
    with TestClient(app) as client:
        res = client.delete("/api/models/qwen3-vl-8b")
        assert res.status_code == 200
        assert res.json()["installed"] is False
