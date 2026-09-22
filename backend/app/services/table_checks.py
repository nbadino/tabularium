"""Controlli aritmetici di una tabella: le somme che la stampa stessa rispetta.

Le tavole statistiche portano le proprie prove: Merchandise + Treasure = Total
su ogni riga, le voci di una sezione che sommano al loro «Total», i subtotali
che sommano al «Grand Total». Quando un numero è letto male, la somma che lo
contiene non torna più. Queste funzioni trovano le somme dalla struttura della
tabella (intestazioni, righe «Total…»), le rifanno sui valori letti e dicono
quali tornano e quali no, così chi corregge va dritto alle celle sospette.

Le somme di colonna si riconoscono solo se tornano su almeno una colonna, e le
terne Merchandise/Treasure/Total solo se tornano su almeno una riga: una
struttura che non torna da nessuna parte è un'ipotesi sbagliata, non un errore
da segnalare. Una somma che non torna può essere anche un refuso della stampa
originale: lo dice l'immagine, non il conto.
"""
from __future__ import annotations

import re
from fractions import Fraction

_NIL = re.compile(r"^[\s.·…‥\-–—:]*$")
_FRACTIONS = {"½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4), "⅕": Fraction(1, 5),
              "⅛": Fraction(1, 8), "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
              "⅓": Fraction(1, 3), "⅔": Fraction(2, 3)}
_DITTO = {"„", ",,", "\"", "do.", "„ „"}


def is_nil(text: str | None) -> bool:
    """Cella vuota o a puntini: nella stampa vuol dire «niente», non «illeggibile»."""
    return text is None or bool(_NIL.match(text))


def number(text: str | None) -> Fraction | None:
    """Valore numerico di una cella, o None se non è un numero.

    Le virgole sono separatori (anche il raggruppamento indiano 3,72,958);
    frazioni tipografiche (½) e miste («13 12/20») sono ammesse.
    """
    if text is None:
        return None
    s = text.strip().replace(" ", " ")
    if not s or is_nil(s):
        return None
    total = Fraction(0)
    for ch, frac in _FRACTIONS.items():
        if ch in s:
            total += frac
            s = s.replace(ch, "")
    s = s.strip()
    m = re.fullmatch(r"([\d,.\s']+?)\s+(\d+)\s*/\s*(\d+)", s)
    if m:
        whole = re.sub(r"[,.\s']", "", m.group(1))
        if not whole.isdigit() or int(m.group(3)) == 0:
            return None
        return total + int(whole) + Fraction(int(m.group(2)), int(m.group(3)))
    digits = re.sub(r"[,\s']", "", s)
    if not digits:
        return total if total else None
    if not digits.isdigit():
        return None
    return total + int(digits)


def value(text: str | None) -> Fraction | None:
    """Valore per l'aritmetica: i vuoti valgono zero."""
    return Fraction(0) if is_nil(text) else number(text)


def dense(grid: dict) -> list[list[str | None]]:
    """Matrice righe×colonne: il testo sta nella cella d'origine, le celle
    coperte da uno span valgono None (non sono un vuoto stampato)."""
    rows, cols = int(grid.get("rows", 0)), int(grid.get("cols", 0))
    m: list[list[str | None]] = [[None] * cols for _ in range(rows)]
    for c in grid.get("cells", []):
        r, k = int(c["r"]), int(c["c"])
        if 0 <= r < rows and 0 <= k < cols:
            m[r][k] = c.get("text") or ""
    return m


def label_column(m: list[list[str | None]]) -> int:
    """La colonna dei nomi: quella con più testo alfabetico."""
    return max(range(len(m[0])), key=lambda k: sum(1 for row in m if row[k] and re.search(r"[A-Za-z]{3,}", row[k]))) if m else 0


def row_label(m, r: int, lab: int) -> str:
    """Nome della riga: la colonna dei nomi, più eventuali altri testi non
    numerici accanto (le tabelle di p. 1 hanno «IMPORTS» e «Treasure» in due
    colonne)."""
    return " ".join(t for k, t in enumerate(m[r])
                    if t and not is_nil(t) and number(t) is None and (k == lab or re.search(r"[A-Za-z]{3,}", t)))


# -------------------------------------------------------- controlli (freeze) --

_TOTAL = re.compile(r"^\s*(grand\s+)?total\b", re.I)
_GRAND = re.compile(r"\bgrand\s+total\b", re.I)
_LETTER_HEAD = re.compile(r"^\s*[IVX]*\.?\s*[A-J]\.\s*[—-]|^\s*[IVX]+\.\s*[—-]")


def _units(m, lab: int) -> list[str]:
    """Unità di misura per riga (colonna subito dopo i nomi nelle tabelle
    ARTICLES), con le virgolette di ripetizione risolte."""
    out, last = [], ""
    unit_col = lab + 1 if m and lab + 1 < len(m[0]) else None
    for row in m:
        u = (row[unit_col] or "").strip() if unit_col is not None else ""
        if u in _DITTO or u.startswith("„"):
            u = last
        if u and number(u) is None:
            last = u
        out.append(u)
    return out


def _numeric_row(m, r, cols) -> bool:
    return any(number(m[r][k]) is not None for k in cols)


def find_checks(grid: dict) -> list[dict]:
    """Le somme della tabella, ciascuna con ``ok``: torna sui valori letti.

    Una cella che dovrebbe portare un numero e non si legge come tale fa
    fallire la sua somma (``ok`` falso): nell'uscita di un modello è proprio
    l'errore da mostrare.
    """
    m = dense(grid)
    if not m:
        return []
    rows, cols = len(m), len(m[0])
    hr = int(grid.get("header_rows") or 0)
    lab = label_column(m)
    num_cols = [k for k in range(cols) if k != lab and any(number(m[r][k]) is not None for r in range(hr, rows))]
    candidates: list[dict] = []

    # 1) Merchandise + Treasure = Total, orizzontale (tabelle per paese)
    head_rows = [r for r in range(min(rows, max(hr, 3))) if any(t and re.match(r"\s*merch", t, re.I) for t in m[r])]
    for h in head_rows:
        for a in range(cols - 2):
            names = [(m[h][a + i] or "").lower() for i in range(3)]
            if names[0].startswith("merch") and names[1].startswith("treas") and names[2].startswith("total"):
                triple = []
                for r in range(h + 1, rows):
                    vals = [m[r][a + i] for i in range(3)]
                    if all(v is not None for v in vals) and any(number(v) is not None for v in vals):
                        triple.append({"kind": "row", "cells": [[r, a], [r, a + 1]], "total": [r, a + 2]})
                # la terna vale solo se torna almeno su una riga: con
                # un'intestazione ancora scivolata di una colonna non tornerebbe
                # da nessuna parte, e segnalarla tutta sarebbe un falso allarme
                if any(_holds(m, c) for c in triple):
                    candidates += triple

    # 2) Merchandise + Treasure = TOTAL, verticale (le due tabelle di p. 1)
    labels = [row_label(m, r, lab) for r in range(rows)]
    for r in range(1, rows - 1):
        if re.search(r"\btreasure\b", labels[r], re.I) and re.match(r"^\s*total\b", labels[r + 1], re.I):
            group = [{"kind": "col", "cells": [[r - 1, k], [r, k]], "total": [r + 1, k]} for k in num_cols]
            candidates += group if any(_holds(m, c) for c in group) else []
    for r in range(rows):
        if _GRAND.search(labels[r]):
            parts = [q for q in range(r) if re.match(r"^\s*total\s*\W*$", labels[q], re.I)]
            if len(parts) >= 2:
                group = [{"kind": "col", "cells": [[q, k] for q in parts], "total": [r, k]} for k in num_cols]
                candidates += group if any(_holds(m, c) for c in group) else []

    # 3) somme di colonna e subtotali: per ogni riga «Total…» si prova una
    #    serie di candidati e si tiene quello che la chiave soddisfa sul maggior
    #    numero di colonne.
    units = _units(m, lab)
    articles = any(t and "quantit" in t.lower() for row in m[: max(hr, 2)] for t in row if t)
    qty_cols = set(range(lab + 2, lab + 7)) if articles else set()
    total_rows = [r for r in range(hr, rows) if _TOTAL.match(labels[r] or "") and _numeric_row(m, r, num_cols)]
    heading = [r for r in range(hr, rows) if labels[r] and not _numeric_row(m, r, num_cols)]
    letter = [r for r in heading if _LETTER_HEAD.match(labels[r])]
    items = lambda a, b: [q for q in range(a, b) if q not in total_rows and _numeric_row(m, q, num_cols)]  # noqa: E731
    seen_vertical = {tuple(c["total"]) for c in candidates if c["kind"] == "col"}
    for t in total_rows:
        if _GRAND.search(labels[t]) and any((t, k) in seen_vertical for k in num_cols):
            continue
        prev_total = max([q for q in total_rows if q < t], default=hr - 1)
        prev_head = max([q for q in heading if q < t], default=hr - 1)
        prev_letter = max([q for q in letter if q < t], default=hr - 1)
        prev_grand = max([q for q in total_rows if q < t and _GRAND.search(labels[q])], default=hr - 1)
        options = {
            "since_total": items(prev_total + 1, t),
            "since_heading": items(prev_head + 1, t),
            "since_section": items(prev_letter + 1, t),
            "totals_above": [q for q in total_rows if prev_grand < q < t],
        }
        best = None
        for name, members in options.items():
            if len(members) < 2 and name != "totals_above":
                continue
            if not members:
                continue
            held = 0
            for k in num_cols:
                ms = [q for q in members if not (k in qty_cols and units[q] != units[t])]
                tv = value(m[t][k])
                vs = [value(m[q][k]) for q in ms if m[q][k] is not None]
                if tv and None not in vs and sum(vs) == tv:
                    held += 1
            if held and (best is None or held > best[1]):
                best = (members, held)
        if best is None:
            continue
        for k in num_cols:
            ms = [q for q in best[0] if not (k in qty_cols and units[q] != units[t])]
            if ms and number(m[t][k]) is not None:
                candidates.append({"kind": "col", "cells": [[q, k] for q in ms], "total": [t, k]})

    out, seen = [], set()
    for c in candidates:
        # la metà coperta di una cella unita non ha un valore proprio: il
        # valore sta nella cella d'origine, che è già tra gli addendi
        c["cells"] = [[r, k] for r, k in c["cells"] if m[r][k] is not None]
        if not c["cells"]:
            continue
        key = (tuple(map(tuple, c["cells"])), tuple(c["total"]))
        if key in seen:
            continue
        seen.add(key)
        vs = [value(m[r][k]) for r, k in c["cells"]]
        tv = value(m[c["total"][0]][c["total"][1]])
        if tv == 0 and all(v == 0 for v in vs):
            continue  # tutto vuoto: niente da verificare
        c["ok"] = _holds(m, c)
        c["sum"] = None if None in vs else sum(vs)
        c["total_value"] = tv
        out.append(c)
    return out


def _holds(m, check: dict) -> bool:
    vs = [value(m[r][k]) for r, k in check["cells"] if m[r][k] is not None]
    tv = value(m[check["total"][0]][check["total"][1]])
    return tv is not None and None not in vs and sum(vs) == tv


def _fmt(v: Fraction | None) -> str | None:
    if v is None:
        return None
    return f"{v.numerator // v.denominator:,}" + (f" {v % 1}" if v.denominator != 1 else "")


def _one_digit(a: Fraction, b: Fraction) -> bool:
    """Due interi che differiscono per una cifra sola: il misread tipico."""
    if a < 0 or b < 0 or a.denominator != 1 or b.denominator != 1:
        return False
    sa, sb = str(a.numerator), str(b.numerator)
    return len(sa) == len(sb) and sum(x != y for x, y in zip(sa, sb)) == 1


def _culprits(m, check: dict, vouched: set) -> set[tuple[int, int]]:
    """Le celle da guardare per una somma che non torna, dalla più precisa:

    1. le celle che dovrebbero portare un numero e non si leggono;
    2. le celle che una cifra sola renderebbe giuste (il totale, o l'addendo
       che, corretto della differenza, cambia una cifra) — fra quelle che
       nessuna somma giusta garantisce, se ce ne sono;
    3. altrimenti le celle non garantite, se sono poche;
    4. altrimenti solo il totale: il conto dice «qui non torna», non dove.
    """
    total = tuple(check["total"])
    members = [tuple(x) for x in check["cells"]] + [total]
    unreadable = {x for x in members if value(m[x[0]][x[1]]) is None}
    if unreadable:
        return unreadable
    tv = value(m[total[0]][total[1]])
    diff = tv - check["sum"]
    one = {total} if _one_digit(tv, check["sum"]) else set()
    one |= {x for x in members[:-1] if _one_digit(value(m[x[0]][x[1]]), value(m[x[0]][x[1]]) + diff)}
    if one:
        return (one - vouched) or one
    loose = set(members) - vouched
    return loose if 0 < len(loose) <= 3 else {total}


def check_grid(grid: dict) -> dict:
    """Esito per l'editor: le somme, quante tornano, e le celle sospette.

    Una somma che torna fa da garante per le sue celle; per ogni somma che
    non torna `_culprits` sceglie le poche celle da guardare. Una cella
    sospetta resta un suggerimento: l'ultima parola è dell'immagine.
    """
    checks = find_checks(grid)
    m = dense(grid)
    vouched = {tuple(x) for c in checks if c["ok"] for x in [*c["cells"], c["total"]]}
    suspects: set[tuple[int, int]] = set()
    for c in checks:
        if not c["ok"]:
            suspects |= _culprits(m, c, vouched)
    return {
        "checks": [
            {"cells": c["cells"], "total": c["total"], "ok": c["ok"],
             "sum": _fmt(c["sum"]), "total_value": _fmt(c["total_value"])}
            for c in checks
        ],
        "passed": sum(c["ok"] for c in checks),
        "failed": sum(not c["ok"] for c in checks),
        "suspects": sorted([r, k] for r, k in suspects),
    }

