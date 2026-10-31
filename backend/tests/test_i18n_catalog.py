"""Il catalogo delle stringhe di backend, nelle tre lingue.

Tabularium parla tre lingue anche nei messaggi che il backend manda alla UI.
Una chiave presente in italiano e assente in francese non produce un errore:
produce la *chiave* sullo schermo, o una frase italiana dentro un'interfaccia
francese. È un errore silenzioso, quindi lo intercetta un test.
"""
from __future__ import annotations

import re

import pytest

from app.services import i18n

# Segnaposto `{nome}`: devono essere gli stessi in tutte le lingue, altrimenti
# una traduzione "funziona" finché non arriva un valore da interpolare.
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_language_has_the_same_keys():
    keys = {lang: set(catalog) for lang, catalog in i18n.CATALOG.items()}
    reference = keys[i18n.DEFAULT]
    for lang, actual in keys.items():
        assert actual == reference, (
            f"chiavi diverse fra {i18n.DEFAULT} e {lang}: "
            f"mancanti={sorted(reference - actual)} extra={sorted(actual - reference)}"
        )


def test_placeholders_match_across_languages():
    """`{url}` in italiano e `{lien}` in francese non è una traduzione: è un
    messaggio che perde il valore (o alza) al momento dell'interpolazione."""
    for key, italian in i18n.CATALOG[i18n.DEFAULT].items():
        expected = set(_PLACEHOLDER.findall(italian))
        for lang, catalog in i18n.CATALOG.items():
            if lang == i18n.DEFAULT:
                continue
            found = set(_PLACEHOLDER.findall(catalog[key]))
            assert found == expected, (
                f"{key!r} in {lang}: segnaposto {sorted(found)} invece di {sorted(expected)}"
            )


@pytest.mark.parametrize("lang", i18n.SUPPORTED)
def test_no_message_is_empty(lang):
    for key, value in i18n.CATALOG[lang].items():
        assert value.strip(), f"{key!r} vuoto in {lang}"


def test_msg_falls_back_to_the_key_when_unknown():
    """Un codice sconosciuto resta visibile invece di diventare una stringa
    vuota: meglio un codice da tradurre che uno schermo muto."""
    assert i18n.msg("chiave_che_non_esiste", "it") == "chiave_che_non_esiste"


def test_a_missing_translation_falls_back_to_italian():
    """Se una lingua non copre una chiave, si serve l'italiano: la UI resta
    leggibile anche prima che la traduzione arrivi."""
    key = next(iter(i18n.CATALOG[i18n.DEFAULT]))
    partial = {**i18n.CATALOG["en"]}
    partial.pop(key, None)
    original = i18n.CATALOG["en"]
    i18n.CATALOG["en"] = partial
    try:
        assert i18n.msg(key, "en") == i18n.CATALOG[i18n.DEFAULT][key]
    finally:
        i18n.CATALOG["en"] = original


def test_every_constant_error_detail_is_in_the_catalog():
    # R11: an Italian `detail` with no catalog entry reaches English and French UIs
    # untranslated. Codes (snake_case) are translated by the frontend instead.
    import ast
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "app"
    untranslated = []
    for path in app.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "HTTPException"):
                continue
            for kw in node.keywords:
                value = getattr(kw.value, "value", None)
                if kw.arg != "detail" or not isinstance(value, str):
                    continue
                if re.fullmatch(r"[a-z0-9_.]+|not found", value):
                    continue
                if i18n.localize_detail(value, "en") == value:
                    untranslated.append(f"{path.relative_to(app)}:{node.lineno} {value!r}")
    assert not untranslated, "\n".join(untranslated)
