"""Riparazione delle intestazioni a due livelli appiattite dal modello.

MonkeyOCRv2 legge bene il testo di un'intestazione come

    COUNTRIES. |      1908-1909.        |      1909-1910.
               | Merch. | Treas. | Total | Merch. | Treas. | Total

ma perde tutte le celle unite: ogni voce diventa una cella larga una colonna e
le due righe scivolano verso il bordo, così nessuna etichetta sta più sopra i
propri numeri. I dati sotto restano allineati.

`repair_flat_header` rimette le voci al loro posto, ma solo quando la forma è
quella e nient'altro: le colonne dei dati si ricavano dai numeri del corpo, le
voci di secondo livello devono ripetersi con un periodo (Merch./Treas./Total,
oppure cinque anni) e ogni titolo di primo livello copre un periodo. Se un solo
controllo non torna la griglia esce invariata: una griglia già giusta o di
forma diversa non viene mai toccata.

Si applica solo all'uscita del modello, mai a griglie scritte da una persona.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter

_NUMBER = re.compile(r"^[\d][\d,.\s'½¼¾⅓⅔⅛]*$")
_NIL = re.compile(r"^[\s.·…\-–—:£]*$")  # vuoto, puntini guida, simbolo di valuta


def _same(a: str, b: str) -> bool:
    ka, kb = (re.sub(r"[^0-9a-z]", "", (t or "").lower()) for t in (a, b))
    return bool(ka and kb) and (ka == kb or difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.8)


def repair_flat_header(grid: dict) -> dict:
    cols, cells = grid.get("cols") or 0, grid.get("cells") or []
    head = [c for c in cells if c["r"] < 2]
    if any(c.get("rowspan", 1) > 1 or c.get("colspan", 1) > 1 for c in head):
        return grid  # il modello ha già dato le unioni: la griglia non è piatta
    row0 = [c["text"] for c in sorted((c for c in head if c["r"] == 0), key=lambda c: c["c"]) if c["text"].strip()]
    leaves = [c["text"] for c in sorted((c for c in head if c["r"] == 1), key=lambda c: c["c"]) if c["text"].strip()]
    if len(row0) < 2 or len(leaves) < 3 or any(_NUMBER.match(t.strip()) for t in leaves):
        return grid

    # Colonne dei dati: quelle dove i numeri del corpo superano il testo
    # (celle larghe una colonna; un «1.» nella colonna dei nomi non basta).
    tally: Counter[int] = Counter()
    for c in cells:
        t = c["text"].strip()
        if c["r"] >= 2 and c.get("colspan", 1) == 1 and not _NIL.match(t):
            tally[c["c"]] += 1 if _NUMBER.match(t) else -1
    num_cols = sorted(c for c, v in tally.items() if v > 0)
    if not num_cols or num_cols != list(range(num_cols[0], num_cols[-1] + 1)):
        return grid
    left, right = list(range(num_cols[0])), list(range(num_cols[-1] + 1, cols))
    # Il lato delle etichette è quello con del testo; dall'altro lato il
    # modello a volte aggiunge una colonna vuota, che resta vuota anche sopra.
    label_left, text_right = (any(c in tally for c in side) for side in (left, right))
    if label_left == text_right:
        return grid
    label_cols, spare = (left, right) if label_left else (right, left)

    # Prima riga: il titolo delle etichette sta dal lato delle etichette; le
    # voci di secondo livello finite lassù (il modello le sposta al bordo
    # opposto) stanno in fondo dall'altro lato; il resto sono i titoli di gruppo.
    title, rest = (row0[0], row0[1:]) if label_left else (row0[-1], row0[:-1])
    if not label_left:
        rest = rest[::-1]  # da qui in poi «dal lato delle etichette verso il bordo»
        leaves = leaves[::-1]
    strays = 0
    while strays < len(rest) and any(_same(rest[-1 - strays], t) for t in leaves):
        strays += 1
    groups = rest[: len(rest) - strays]
    leaves = leaves + rest[len(rest) - strays:]
    n = len(num_cols)
    # Il periodo più corto con cui le voci si ripetono, se si ripetono.
    k = next((k for k in range(2, n) if all(_same(leaves[i], leaves[i + k]) for i in range(n - k))), None) \
        if len(leaves) == n else None
    if not groups or k is None:
        return grid
    # I gruppi pieni stanno dal lato delle etichette; al bordo della pagina
    # (dove la tavola continua sulla pagina accanto) l'ultimo può essere
    # tagliato, oppure restano colonne senza titolo.
    spans = [k] * len(groups)
    if len(groups) * k > n:
        spans[-1] = n - (len(groups) - 1) * k
        if spans[-1] < 1:
            return grid
    elif n - len(groups) * k >= k:
        return grid
    ordered = num_cols if label_left else num_cols[::-1]

    new_head = [{"r": 0, "c": label_cols[0], "rowspan": 2, "colspan": len(label_cols), "text": title}]
    at = 0
    for text, span in zip(groups, spans):
        new_head.append({"r": 0, "c": min(ordered[at:at + span]), "rowspan": 1, "colspan": span, "text": text})
        at += span
    new_head += [{"r": 0, "c": c, "rowspan": 1, "colspan": 1, "text": ""} for c in ordered[at:]]
    new_head += [{"r": 1, "c": c, "rowspan": 1, "colspan": 1, "text": t} for c, t in zip(ordered, leaves)]
    new_head += [{"r": r, "c": c, "rowspan": 1, "colspan": 1, "text": ""} for r in (0, 1) for c in spare]
    return {**grid, "cells": sorted(new_head, key=lambda c: (c["r"], c["c"])) + [c for c in cells if c["r"] >= 2]}
