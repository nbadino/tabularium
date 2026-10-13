"""Test del bridge nativa → canonica (il vocabolario interno di Tabularium)."""
from __future__ import annotations

from app.services import labeling, model_labels


def _labels(*, custom: list | None = None):
    settings = {"labels": custom} if custom else {}
    return labeling.project_schema(settings).labels


def test_normalize_unifies_separators_and_case():
    assert model_labels.normalize_label("Table_Document") == "table document"
    assert model_labels.normalize_label("List-Item") == "list item"
    assert model_labels.normalize_label("  PAGE   NUMBER ") == "page number"


def test_resolve_canonical_name_directly():
    # I modelli che parlano già il canonico (MonkeyOCRv2).
    assert model_labels.resolve_native_label("Table", _labels(), "monkeyocrv2-parsing") == "Table"
    assert model_labels.resolve_native_label("issue_date", _labels()) == "Issue-date"


def test_resolve_via_adapter_map():
    # PaddleOCR-VL: `number` → Issue-number; MinerU: `doc_title` → Title.
    assert model_labels.resolve_native_label("number", _labels(), "paddleocr-vl") == "Issue-number"
    assert model_labels.resolve_native_label("doc_title", _labels(), "mineru2.5") == "Title"


def test_resolve_via_default_alias():
    # Sinonimi dichiarati sulle classi di default: `document_date` → Issue-date.
    assert model_labels.resolve_native_label("document_date", _labels()) == "Issue-date"
    assert model_labels.resolve_native_label("grid", _labels()) == "Table"


def test_resolve_via_project_alias_bridges_a_new_model_token():
    labels = _labels(custom=[{
        "name": "Vessel-name",
        "color": "#3a5f7d",
        "short": "VSL",
        "prompt_kind": "text",
        "prompt": "text",
        "aliases": ["ship_name", "Table_document"],
    }])
    assert model_labels.resolve_native_label("ship_name", labels) == "Vessel-name"
    assert model_labels.resolve_native_label("Table_document", labels) == "Vessel-name"


def test_resolve_is_none_when_unmapped_or_out_of_schema():
    # Token mai visto: nessuna coercizione implicita, decide il chiamante.
    assert model_labels.resolve_native_label("bogus_xyz", _labels()) is None
    # `footer` mappa a Page-footer, che NON è nel progetto: resta fuori.
    assert model_labels.resolve_native_label("footer", _labels(), "mineru2.5") is None
    # Se il progetto aggiunge la classe pubblica, allora si risolve.
    labels = _labels(custom=[{
        "name": "Page-footer", "color": "#45594f", "short": "PGF",
        "prompt_kind": "text", "prompt": "text",
    }])
    assert model_labels.resolve_native_label("footer", labels, "mineru2.5") == "Page-footer"


def test_adapter_map_wins_over_generic_alias():
    # `page_number` per MinerU è Page-footer, non Issue-number (alias di default
    # di Issue-number è `number`, non `page_number`): la semantica del modello
    # batte il sinonimo generico.
    labels = _labels(custom=[{
        "name": "Page-footer", "color": "#45594f", "short": "PGF",
        "prompt_kind": "text", "prompt": "text",
    }])
    assert model_labels.resolve_native_label("page_number", labels, "mineru2.5") == "Page-footer"
