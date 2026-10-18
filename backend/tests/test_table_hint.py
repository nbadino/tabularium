"""Hint di schema tabella: capability, composizione del prompt, API di progetto.

L'hint è testo libero accodato al prompt tabella dei soli modelli che lo
accettano (v. `ModelCapabilities.accepts_prompt_hint`). Questi test bloccano la
regressione più costosa: mandare l'hint a un motore a prompt di protocollo o a
pipeline nativa, che lo leggerebbe come testo fuori distribuzione.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import inference as infmod
from app.services import model_adapters


def _adapter(adapter_id: str):
    return model_adapters.get_adapter(adapter_id)


def test_only_instruction_adapters_accept_prompt_hint():
    # Qwen3-VL è l'unico eseguibile in locale su Apple Silicon che accetta un
    # hint: gli altri locali usano protocollo/pipeline nativa.
    assert _adapter("qwen3-vl-8b").capabilities.accepts_prompt_hint is True
    for adapter_id in (
        "monkeyocrv2-parsing",
        "paddleocr-vl",
        "mineru2.5",
        "unlimited-ocr",
        "deepseek-ocr",
    ):
        assert _adapter(adapter_id).capabilities.accepts_prompt_hint is False


def test_with_table_hint_appends_only_when_accepted():
    qwen = _adapter("qwen3-vl-8b")
    assert infmod._with_table_hint(qwen, "PROMPT", "SCHEMA") == "PROMPT\n\nSCHEMA"
    assert infmod._with_table_hint(qwen, "PROMPT", "   ") == "PROMPT"
    assert infmod._with_table_hint(qwen, "PROMPT", None) == "PROMPT"
    monkey = _adapter("monkeyocrv2-parsing")
    assert infmod._with_table_hint(monkey, "PROMPT", "SCHEMA") == "PROMPT"


def _client_with_capture(adapter_id: str, raw: str):
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1", adapter=_adapter(adapter_id))
    seen: dict[str, str] = {}

    def fake_chat(image, prompt, **kwargs):
        seen["prompt"] = prompt
        return raw

    client._chat = fake_chat
    return client, seen


def test_table_grid_forwards_hint_to_capable_adapter():
    client, seen = _client_with_capture(
        "qwen3-vl-8b", "<fcel>A<fcel>B<nl><fcel>1<fcel>2"
    )
    client.table_grid(Image.new("RGB", (200, 120), "white"), hint="SCHEMA-HINT")
    assert "SCHEMA-HINT" in seen["prompt"]


def test_table_grid_ignores_hint_for_protocol_adapter():
    client, seen = _client_with_capture(
        "monkeyocrv2-parsing", "<fcel>A<fcel>B<nl><fcel>1<fcel>2"
    )
    client.table_grid(Image.new("RGB", (200, 120), "white"), hint="SCHEMA-HINT")
    assert "SCHEMA-HINT" not in seen["prompt"]


def test_recognize_hint_only_applies_to_table_task():
    client, seen = _client_with_capture("qwen3-vl-8b", "<fcel>A")
    client.recognize(Image.new("RGB", (80, 40), "white"), "Table", hint="SCHEMA-HINT")
    assert "SCHEMA-HINT" in seen["prompt"]
    client.recognize(Image.new("RGB", (80, 40), "white"), "Text", hint="SCHEMA-HINT")
    assert "SCHEMA-HINT" not in seen["prompt"]


def test_table_hint_api_round_trip():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "Hint"}).json()["id"]
        initial = client.get(f"/api/projects/{pid}/table-hint").json()
        assert initial["hint"] == ""
        # C'è sempre un modello attivo nella configurazione di default e la
        # risposta dichiara se accetta il hint.
        assert "accepts_hint" in initial

        saved = client.put(
            f"/api/projects/{pid}/table-hint",
            json={"hint": "  Year e Tons unite  "},
        ).json()
        assert saved["hint"] == "Year e Tons unite"
        assert (
            client.get(f"/api/projects/{pid}/table-hint").json()["hint"]
            == "Year e Tons unite"
        )

        cleared = client.put(f"/api/projects/{pid}/table-hint", json={"hint": ""}).json()
        assert cleared["hint"] == ""
        assert client.get(f"/api/projects/{pid}/table-hint").json()["hint"] == ""


def test_table_hint_api_rejects_overlong_hint():
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "Hint2"}).json()["id"]
        r = client.put(
            f"/api/projects/{pid}/table-hint", json={"hint": "x" * 2001}
        )
        assert r.status_code == 422
