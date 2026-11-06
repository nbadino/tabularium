"""A user who never chose a language reads English; one who chose keeps the choice.

The interface sends `Accept-Language` with the language picked in the menu (stored in
the browser). With no choice there is no header worth trusting, and every message the
backend produces, now or later, must come out in English: HTTP error details, catalog
messages, and run errors stored in the database and shown afterwards.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services import i18n


def test_english_is_the_default_language():
    assert i18n.DEFAULT == "en"
    assert i18n.parse_lang(None) == "en"
    assert i18n.parse_lang("") == "en"
    assert i18n.parse_lang("de-DE,de;q=0.9") == "en"


def test_msg_without_a_language_is_english():
    assert i18n.msg("project_not_found") == "project not found"
    assert i18n.msg("page_not_in_project", None, id=3) == i18n.CATALOG["en"]["page_not_in_project"].format(id=3)
    assert i18n.msg("project_not_found", "xx") == "project not found"


def test_a_chosen_language_is_kept():
    assert i18n.msg("project_not_found", "it") == "progetto non trovato"
    assert i18n.msg("project_not_found", "fr") == i18n.CATALOG["fr"]["project_not_found"]


def test_an_italian_detail_without_a_language_comes_out_in_english():
    assert i18n.localize_detail("progetto non trovato") == "project not found"
    assert i18n.localize_detail("pagina 7 non nel progetto", None) == i18n.msg("page_not_in_project", "en", id=7)
    # The source text stays the source text for an Italian reader.
    assert i18n.localize_detail("progetto non trovato", "it") == "progetto non trovato"


def test_a_message_stored_in_english_is_translated_for_an_italian_reader():
    """A background job that stored the English text (no request, no language) must
    still read in the language of whoever looks at it later."""
    stored = i18n.msg("consensus_model_stopped", None, n=3, error="402 insufficient credits")
    assert stored.startswith(i18n.CATALOG["en"]["consensus_model_stopped"].split("{")[0])
    italian = i18n.localize_detail(stored, "it")
    assert italian == i18n.msg("consensus_model_stopped", "it", n=3, error="402 insufficient credits")
    assert i18n.localize_detail(stored, "en") == stored


def test_an_unknown_detail_is_left_alone():
    assert i18n.localize_detail("HTTP 502 from upstream", None) == "HTTP 502 from upstream"
    assert i18n.localize_detail("HTTP 502 from upstream", "it") == "HTTP 502 from upstream"


def test_a_request_without_accept_language_gets_english_details():
    with TestClient(app) as client:
        response = client.get("/api/projects/987654")
        assert response.status_code == 404
        assert response.json()["detail"] == "project not found"


def test_a_request_with_a_saved_preference_keeps_its_language():
    """The menu choice travels as `Accept-Language`: Italian and French stay."""
    with TestClient(app) as client:
        italian = client.get("/api/projects/987654", headers={"Accept-Language": "it"})
        french = client.get("/api/projects/987654", headers={"Accept-Language": "fr"})
        assert italian.json()["detail"] == "progetto non trovato"
        assert french.json()["detail"] == i18n.CATALOG["fr"]["project_not_found"]
