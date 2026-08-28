"""Test per il legame VllmClient <-> adapter modello (Fase 2 multi-modello)."""
from __future__ import annotations

from app.services import inference as infmod
from app.services import model_adapters
from app.services import otsl


def test_default_client_keeps_monkeyocrv2_prompts():
    client = infmod.VllmClient(url="http://127.0.0.1:8888/v1")
    assert client.adapter.adapter_id == "monkeyocrv2-parsing"
    assert infmod._prompt_for(client.adapter, "layout", None, "fallback") == infmod.LAYOUT_PROMPT
    assert infmod._prompt_for(client.adapter, "table", "Table", "fallback") == infmod.TABLE_PROMPT


def test_prompt_for_falls_back_when_adapter_raises_not_implemented():
    adapter = model_adapters.get_adapter("mineru2.5")
    resolved = infmod._prompt_for(adapter, "layout", None, "FALLBACK-PROMPT")
    assert resolved == "FALLBACK-PROMPT"


def test_prompt_for_uses_verified_dots_ocr_end2end_prompt():
    adapter = model_adapters.get_adapter("dots-ocr")
    resolved = infmod._prompt_for(adapter, "end2end", None, "FALLBACK-PROMPT")
    assert resolved == adapter.prompt_for("end2end")
    assert resolved != "FALLBACK-PROMPT"


def test_tolerant_items_accepts_category_and_label():
    # Percorso letterale (ast.literal_eval sull'intera risposta): non filtra
    # per chiave, qualunque dict passa.
    literal = "[{'bbox': [1, 2, 3, 4], 'category': 'Table', 'text': 'ciao'}]"
    items = infmod._tolerant_items(literal)
    assert items == [{"bbox": [1, 2, 3, 4], "category": "Table", "text": "ciao"}]

    # Percorso di recupero a blocchi (usato quando l'intera risposta non è una
    # lista valida): richiede "bbox" più "label" O "category".
    truncated = "testo introduttivo {'bbox': [1, 2, 3, 4], 'category': 'Table', 'text': 'ciao'} coda"
    items = infmod._tolerant_items(truncated)
    assert len(items) == 1
    assert items[0]["category"] == "Table"


def test_client_table_text_to_grid_dispatches_on_table_format(monkeypatch):
    otsl_client = infmod.VllmClient(url="http://127.0.0.1:8888/v1")
    assert otsl_client.adapter.capabilities.table_format == "otsl"

    html_client = infmod.VllmClient(
        url="http://127.0.0.1:8888/v1", adapter=model_adapters.get_adapter("dots-ocr")
    )
    assert html_client.adapter.capabilities.table_format == "html"

    calls: list[str] = []
    original = otsl.html_to_otsl

    def spy(source: str) -> str:
        calls.append(source)
        return original(source)

    monkeypatch.setattr(otsl, "html_to_otsl", spy)
    grid = html_client._table_text_to_grid(
        "<table><tr><td>a</td><td>b</td></tr></table>"
    )
    assert calls, "html_to_otsl doveva essere chiamato per un adapter table_format='html'"
    assert grid["cols"] == 2
