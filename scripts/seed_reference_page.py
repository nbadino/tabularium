#!/usr/bin/env python3
"""Annota la pagina di riferimento LSI_17186_015 come esempio lavorato.

Serve a due cose: dare un punto di partenza corretto sulla pagina che ha aperto
la discussione, e provare end-to-end la catena rilevamento → griglia → OTSL sul
DB reale invece che su una tabella sintetica.

L'annotazione è quella discussa: **cinque blocchi**, non trecentosettanta.

    Title          HISTORIC SHIPPING INDEX     (testata, solo dove compare)
    Issue-number   No. 17,186
    Issue-date     MONDAY, MAY 14, 1973.
    Page-header    A. E. S-ADA                (indice a pollice, cambia a ogni pagina)
    Table          tutto il registro sotto la testa corrente

I riquadri non sono scritti a mano: si ricavano dalla proiezione dell'inchiostro
dentro fasce note, così restano corretti anche se la scansione viene rifatta o
raddrizzata. La griglia della tabella arriva dal rilevatore e viene salvata come
bozza da correggere — in particolare i due confini che la geometria non può
provare (Vessel|Owner e From|For, entrambi nascosti dai puntini di guida).

Uso:
    python3 scripts/seed_reference_page.py            # dry-run
    python3 scripts/seed_reference_page.py --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.services import otsl, table_detect  # noqa: E402

PAGE_FILE = "LSI_17186_015.tif"
INK_LEVEL = 128


def _runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask):
        if value and start is None:
            start = i
        elif not value and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    if start is not None and len(mask) - start >= min_len:
        out.append((start, len(mask)))
    return out


def without_rules(ink: np.ndarray) -> np.ndarray:
    """Azzera i filetti orizzontali: sono struttura di pagina, non testo.

    Senza questo passaggio ogni riquadro si allarga fino al filetto più vicino,
    perché una riga di filetto ha inchiostro su quasi tutte le colonne.
    """
    cleaned = ink.copy()
    cleaned[ink.sum(axis=1) > 0.5 * ink.shape[1]] = False
    return cleaned


def ink_bbox(
    ink: np.ndarray,
    y0: int,
    y1: int,
    x0: int = 0,
    x1: int | None = None,
    *,
    density: float = 0.15,
    min_run: int = 12,
    min_text_h: int = 8,
) -> tuple[int, int, int, int]:
    """Riquadro stretto sul testo dentro la fascia indicata.

    La densità minima e la lunghezza minima di tratto ignorano la striscia scura
    che le scansioni hanno sul bordo del foglio: è densa ma larga pochi pixel,
    quindi passerebbe una soglia di densità e non una di larghezza.
    """
    x1 = x1 or ink.shape[1]
    sub = ink[y0:y1, x0:x1]
    _, width = sub.shape

    # Prima si stringe in verticale, e si tengono solo le fasce alte almeno
    # `min_text_h`: un filetto parziale è spesso due o tre pixel, una riga di
    # testo qualche decina. Restano tutte le righe di testo (una testata su due
    # righe le mantiene entrambe) e cadono i filetti.
    ys = _runs(sub.sum(axis=1) > max(2, 0.02 * width), min_text_h)
    if not ys:
        raise SystemExit(f"nessun testo nella fascia y={y0}..{y1}")
    ty0, ty1 = ys[0][0], ys[-1][1]

    # Poi la densità orizzontale si misura sull'altezza del testo, non su quella
    # della fascia di ricerca, che è sempre più alta e diluisce la soglia.
    band = sub[ty0:ty1]
    text_h = ty1 - ty0
    xs = _runs(band.sum(axis=0) > max(2, density * text_h), 1)
    if not xs:
        raise SystemExit(f"nessuna colonna di testo nella fascia y={y0}..{y1}")

    # I tratti vicini si uniscono *prima* di applicare la larghezza minima:
    # altrimenti un'asta sottile di fine riga (la «6» di «17,186») resta un
    # tratto isolato di pochi pixel e viene scartata, accorciando il riquadro.
    # La striscia scura sul bordo del foglio resta invece isolata e cade.
    # Il salto ammesso è al più un'altezza di riga: oltre, non è più uno spazio
    # fra parole ma un gutter di colonna. Il tetto serve ai blocchi alti più
    # righe (la tabella), dove `text_h` è l'altezza dell'intero blocco.
    gap = min(text_h, 48)
    groups: list[list[int]] = []
    for start, end in xs:
        if groups and start - groups[-1][1] < gap:
            groups[-1][1] = end
        else:
            groups.append([start, end])
    groups = [g for g in groups if g[1] - g[0] >= min_run]
    if not groups:
        raise SystemExit(f"nessuna colonna di testo nella fascia y={y0}..{y1}")
    return (groups[0][0] + x0, ty0 + y0, groups[-1][1] + x0, ty1 + y0)


def build_blocks(image: Image.Image) -> list[dict]:
    ink = without_rules(np.asarray(image.convert("L")) < INK_LEVEL)
    height = ink.shape[0]

    # Fasce ricavate dai due filetti orizzontali della testata (y≈525 e y≈650),
    # che su questo formato separano masthead, riga del numero e corpo pagina.
    title = ink_bbox(ink, 150, 505)
    number = ink_bbox(ink, 535, 645, 0, 780)
    date = ink_bbox(ink, 535, 645, 900, 2200)
    running = ink_bbox(ink, 655, 702, 2200, min_text_h=5)
    # Il corpo comincia sotto l'indice a pollice, non sotto il filetto. Qui la
    # densità dev'essere bassa: le colonne di destra (For, Latest Report) sono
    # piene solo su una frazione delle righe e verrebbero tagliate fuori.
    body = ink_bbox(ink, running[3] + 4, height, density=0.005, min_run=20)

    return [
        {"label": "Title", "bbox": title, "content": "HISTORIC SHIPPING INDEX"},
        {"label": "Issue-number", "bbox": number, "content": "No. 17,186"},
        {"label": "Issue-date", "bbox": date, "content": "MONDAY, MAY 14, 1973."},
        {"label": "Page-header", "bbox": running, "content": "A. E. S-ADA"},
        {"label": "Table", "bbox": body, "content": ""},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    page = conn.execute(
        "SELECT * FROM pages WHERE rel_path LIKE ?", (f"%{PAGE_FILE}",)
    ).fetchone()
    if page is None:
        print(f"pagina {PAGE_FILE} non registrata")
        return 1

    existing = conn.execute(
        "SELECT COUNT(*) AS n FROM blocks WHERE page_id=?", (page["id"],)
    ).fetchone()["n"]
    if existing:
        print(f"la pagina {page['id']} ha già {existing} blocchi: nulla da fare")
        return 1

    image = Image.open(page["abs_path"])
    blocks = build_blocks(image)

    print(f"pagina {page['id']} — {PAGE_FILE}\n")
    for order, block in enumerate(blocks, start=1):
        x1, y1, x2, y2 = block["bbox"]
        print(
            f"  {order}. {block['label']:<13} "
            f"({x1:>4},{y1:>4})-({x2:>4},{y2:>4})  {block['content']}"
        )

    table = blocks[-1]
    crop = image.crop(table["bbox"])
    detection = table_detect.detect_grid(crop)
    width = crop.width
    internal = [
        (round(v * width) + table["bbox"][0], s)
        for v, s in zip(detection.vlines, detection.column_support)
    ][1:-1]

    print(
        f"\n  griglia rilevata: {detection.rows} righe × {detection.cols} colonne "
        f"(passo {detection.diagnostics['pitch_px']}px, "
        f"{detection.diagnostics['leader_dots_suppressed']} puntini soppressi)"
    )
    for x, support in internal:
        flag = "  ← debole, da verificare" if support < 0.5 * detection.rows else ""
        print(f"     confine x={x:<5} attestato su {support:>3}/{detection.rows} righe{flag}")

    if not args.apply:
        print("\ndry-run: nessuna modifica scritta. Aggiungi --apply per eseguire.")
        return 0

    for order, block in enumerate(blocks, start=1):
        x1, y1, x2, y2 = block["bbox"]
        cur = conn.execute(
            "INSERT INTO blocks (page_id, label, kind, points, content, order_idx, "
            "prefill_source, confirmed) VALUES (?,?,?,?,?,?,?,0)",
            (
                page["id"],
                block["label"],
                "rect",
                json.dumps([[x1, y1], [x2, y2]]),
                block["content"],
                order,
                "seed:reference" if block["label"] == "Table" else None,
            ),
        )
        if block["label"] == "Table":
            grid = {
                "rows": detection.rows,
                "cols": detection.cols,
                "cells": table_detect.empty_cells(detection.rows, detection.cols),
                "phantom_cols": [],
                "vlines": detection.vlines,
                "hlines": detection.hlines,
            }
            # Verifica che la griglia sia davvero codificabile prima di salvarla.
            otsl.grid_to_otsl(grid)
            conn.execute(
                "INSERT INTO tables (block_id, grid_json) VALUES (?, ?)",
                (cur.lastrowid, json.dumps(grid)),
            )

    conn.execute("UPDATE pages SET status='annotated' WHERE id=?", (page["id"],))
    conn.commit()
    conn.close()
    print("\napplicato: 5 blocchi + griglia tabella salvati.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
