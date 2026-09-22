"""Intestazioni appiattite: griglie grezze di MonkeyOCRv2 contro quelle corrette.

La fixture viene dall'archivio Calcutta (1912): per ogni tabella la griglia
com'è uscita dal modello (prime righe) e le due righe d'intestazione corrette a
mano nella chiave `data/benchmarks/calcutta_gold_v1.json`. Il confronto è sulle
celle non vuote (posizione, span, testo); il testo resta quello letto dal
modello, refusi compresi, quindi si confronta per somiglianza.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

import pytest

from app.services.table_headers import repair_flat_header

FIXTURES = {f["block_id"]: f for f in json.loads(
    (Path(__file__).parent / "fixtures" / "calcutta_raw_headers.json").read_text(encoding="utf-8"))}


def _head(cells: list[dict]) -> list[tuple]:
    return sorted((c["r"], c["c"], c.get("rowspan", 1), c.get("colspan", 1), c["text"])
                  for c in cells if c["r"] < 2 and c["text"].strip())


def _similar(a: str, b: str) -> bool:
    ka, kb = (re.sub(r"[^0-9a-z]", "", t.lower()) for t in (a, b))
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.8


def _assert_header(got: list[dict], want: list[tuple]) -> None:
    g = _head(got)
    assert [x[:4] for x in g] == [x[:4] for x in want]
    for (*_, gt), (*_, wt) in zip(g, want):
        assert _similar(gt, wt), (gt, wt)


@pytest.mark.parametrize("block_id", [51, 57, 116, 121])
def test_country_tables_match_the_corrected_header(block_id):
    """Pp. 2-5: titolo alto due righe (a sinistra o, sul verso, a destra),
    anni larghi tre colonne, gruppo tagliato al bordo della pagina."""
    f = FIXTURES[block_id]
    _assert_header(repair_flat_header(f["raw"])["cells"], _head(f["gold_header"]))


@pytest.mark.parametrize("block_id", [135, 142, 149])
def test_article_tables_match_except_the_missing_units_column(block_id):
    """Pp. 7, 8, 10: il modello perde anche la colonna delle unità (Tons.,
    Cwt.) accanto ai nomi, che non è compito di questa riparazione: rispetto
    alla chiave tutto sta una colonna più a sinistra e ARTICLES è largo uno."""
    f = FIXTURES[block_id]
    want = sorted((r, c if c < 2 else c - 1, rs, cs - 1 if c == 0 else cs, t)
                  for r, c, rs, cs, t in _head(f["gold_header"]))
    _assert_header(repair_flat_header(f["raw"])["cells"], want)


@pytest.mark.parametrize("block_id", [15, 18, 155, 163])
def test_other_shapes_are_left_alone(block_id):
    """P. 1 ha un altro difetto (nomi di riga fusi); pp. 9 e 11 hanno già le
    unioni giuste. Nessuna delle due forme va toccata."""
    raw = FIXTURES[block_id]["raw"]
    assert repair_flat_header(raw) == raw


def test_body_rows_are_untouched():
    raw = FIXTURES[116]["raw"]
    out = repair_flat_header(raw)
    assert [c for c in out["cells"] if c["r"] >= 2] == [c for c in raw["cells"] if c["r"] >= 2]
    assert (out["rows"], out["cols"]) == (raw["rows"], raw["cols"])
