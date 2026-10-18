"""Gemini 4 Argon via gateway OpenCode: protocollo Google `generateContent`.

Verifica il contratto che rende il modello utilizzabile come riconoscitore
esterno: l'adapter dichiara solo testo/tabella, il client parla
`generateContent` (non OpenAI), l'endpoint è normalizzato, l'header di
sessione c'è, e la chiave si legge dall'archivio di opencode quando il vault
non ne ha una.
"""
from __future__ import annotations

import json

import pytest
from PIL import Image

from app.services import inference as infmod
from app.services import model_adapters, opencode_gateway


def test_gemini_argon_adapter_declares_remote_gateway_and_end2end():
    adapter = model_adapters.get_adapter("gemini-argon")
    caps = adapter.capabilities
    assert caps.serve_backend == "opencode-gateway"
    assert caps.training_types == ()
    assert caps.local_runtimes == ()
    assert adapter.chat_protocol == "google-generatecontent"
    assert adapter.native_prefill_mode == "end2end"
    # Il percorso verificato è END2END (bbox + label + contenuto); un prompt di
    # layout separato non esiste.
    with pytest.raises(NotImplementedError):
        adapter.prompt_for("layout")
    end2end = adapter.prompt_for("end2end")
    assert "JSON array" in end2end and "Issue-date" in end2end and "<nl>" in end2end


def test_table_prompt_pins_the_canonical_otsl_tokens():
    adapter = model_adapters.get_adapter("gemini-argon")
    prompt = adapter.prompt_for("table")
    # Il vincolo sui token è la ragione per cui l'output resta OTSL valido:
    # senza, il modello emette <ched>/<splt>/<uched>.
    for token in ("<fcel>", "<ecel>", "<lcel>", "<ucel>", "<xcel>", "<nl>"):
        assert token in prompt
    assert "<ched>" in prompt and "Do not emit" in prompt
    assert adapter.prompt_for("text") == infmod.TEXT_PROMPT


def test_endpoint_guessing_recognizes_argon_and_opencode_provider():
    assert infmod._adapter_for_endpoint(
        "gemini-4-argon-contributor",
        "https://opencode.ai/inference/go/google/v1beta",
    ) == "gemini-argon"
    assert infmod._provider_for_endpoint(
        "https://opencode.ai/inference/go/google/v1beta",
    ) == "opencode"


def test_google_base_url_accepts_base_or_full_model_endpoint():
    client = infmod.VllmClient(
        url="https://opencode.ai/inference/go/google/v1beta",
        adapter=model_adapters.get_adapter("gemini-argon"),
    )
    assert client._google_base_url() == "https://opencode.ai/inference/go/google/v1beta"
    client.url = "https://opencode.ai/inference/go/google/v1beta/models/gemini-4-argon-contributor"
    assert client._google_base_url() == "https://opencode.ai/inference/go/google/v1beta"
    client.url = "https://generativelanguage.googleapis.com/v1beta/models/x:generateContent"
    assert client._google_base_url() == "https://generativelanguage.googleapis.com/v1beta"


def test_recognize_table_uses_generate_content_with_session_header(monkeypatch):
    adapter = model_adapters.get_adapter("gemini-argon")
    client = infmod.VllmClient(
        url="https://opencode.ai/inference/go/google/v1beta",
        model="gemini-4-argon-contributor",
        api_key="oc-key",
        adapter=adapter,
        provider="opencode",
    )
    captured: dict = {}

    class Response:
        def raise_for_status(self):  # noqa: D401 - mock
            pass

        def json(self):
            return {
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [
                        {"text": "<fcel>Corbank<fcel>Br<nl>"},
                        {"text": "<fcel>Corbeach<ecel><nl>"},
                    ]},
                }],
                "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 12},
            }

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(infmod.requests, "post", post)
    text = client.recognize(Image.new("RGB", (64, 48), "white"), "Table")

    assert text == "<fcel>Corbank<fcel>Br<nl><fcel>Corbeach<ecel><nl>"
    assert captured["url"].endswith("/models/gemini-4-argon-contributor:generateContent")
    assert captured["headers"]["x-opencode-session"] == client._session_id
    assert captured["headers"]["Authorization"] == "Bearer oc-key"
    body = captured["json"]
    parts = body["contents"][0]["parts"]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert "<fcel>" in parts[1]["text"]
    assert body["generationConfig"]["maxOutputTokens"] > 0
    assert client.last_trace["completion_tokens"] == 12


def _client():
    from app.services import inference as infmod2

    return infmod2, infmod2.VllmClient(
        url="https://opencode.ai/inference/go/google/v1beta",
        model="gemini-4-argon-contributor",
        api_key="oc",
        adapter=model_adapters.get_adapter("gemini-argon"),
        provider="opencode",
    )


def test_connection_probe_is_a_minimal_generation(monkeypatch):
    """Il gateway non ha un ping economico (le richieste malformate tornano 500
    vuoto): la prova è una generazione minima con maxOutputTokens 1."""
    infmod2, client = _client()
    captured: dict = {}

    class Response:
        status_code = 200

    def post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(infmod2.requests, "post", post)
    result = client.test_connection(timeout=5.0)
    assert result["ok"] is True
    assert result["is_cloud"] is True
    # `ping()` (pre-flight di prelabel/blocks) usa lo stesso probe.
    assert client.ping() is True
    assert captured["json"]["generationConfig"]["maxOutputTokens"] == 1
    assert captured["url"].endswith(":generateContent")


def test_connection_probe_flags_bad_key_and_gateway_error(monkeypatch):
    infmod2, client = _client()

    class BadKey:
        status_code = 401

    monkeypatch.setattr(infmod2.requests, "post", lambda *a, **k: BadKey())
    bad = client.test_connection(timeout=5.0)
    assert bad["ok"] is False and "401" in bad["error"]

    class Gateway500:
        status_code = 500

    monkeypatch.setattr(infmod2.requests, "post", lambda *a, **k: Gateway500())
    gone = client.test_connection(timeout=5.0)
    assert gone["ok"] is False and "500" in gone["error"]


def test_save_normalizes_the_gateway_provider_and_clears_the_legacy_cap():
    """`compute_profiles.provider` ha un CHECK: «opencode» lo violerebbe (500
    all'utente, riprodotto). Il profilo registra «custom», e il tetto pixel
    globale di MonkeyOCRv2 non viaggia sul nuovo modello."""
    from app.db import init_db
    from app.services import inference as inf

    init_db()
    inf.save_inference_config({
        "enabled": True,
        "url": "https://opencode.ai/inference/go/google/v1beta",
        "model": "gemini-4-argon-contributor",
        "adapter_id": "gemini-argon",
    })
    cfg = inf.get_inference_config()
    assert cfg["adapter_id"] == "gemini-argon"
    assert cfg["provider"] == "custom"
    assert cfg["max_pixels"] is None


def test_opencode_key_reads_from_auth_store(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"opencode-go": {"type": "api", "key": "secret-go"}}), encoding="utf-8")
    monkeypatch.setenv("TABULARIUM_OPENCODE_AUTH", str(auth))
    monkeypatch.delenv("TABULARIUM_OPENCODE_KEY", raising=False)
    assert opencode_gateway.read_api_key() == "secret-go"


def test_opencode_key_env_overrides_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("TABULARIUM_OPENCODE_AUTH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("TABULARIUM_OPENCODE_KEY", "from-env")
    assert opencode_gateway.read_api_key() == "from-env"


def test_vllm_client_falls_back_to_the_opencode_store_for_the_gateway(monkeypatch):
    monkeypatch.setenv("TABULARIUM_OPENCODE_KEY", "store-key")
    called: dict = {}

    def fake_open(**kwargs):
        called.update({"api_key": kwargs.get("api_key")})
        return object()

    monkeypatch.setattr(infmod, "VllmClient", fake_open)
    monkeypatch.setattr(
        infmod,
        "get_inference_config",
        lambda: {
            "url": "https://opencode.ai/inference/go/google/v1beta",
            "model": "gemini-4-argon-contributor",
            "api_key": "",
            "adapter_id": "gemini-argon",
            "provider": "opencode",
        },
    )
    infmod.get_vllm_client()
    assert called["api_key"] == "store-key"
