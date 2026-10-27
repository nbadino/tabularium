"""Benchmark delle tabelle contro una chiave di risposta congelata.

Strumento interno di verifica, come gli altri ``benchmark_*.py``: non scrive
mai nel progetto (niente blocchi, griglie, run di riconoscimento), non è
esposto nella UX e non produce annotazioni.

Tre comandi:

``freeze``
    Copia le griglie tabellari correnti di un insieme di pagine in un file
    JSON che da quel momento non si modifica più: la chiave di risposta.
    Il file porta uno SHA-256 del proprio contenuto e viene reso di sola
    lettura; se esiste già il comando si rifiuta di sovrascriverlo. Una chiave
    da correggere diventa una nuova versione (``--version 2``), mai una
    modifica sul posto: modelli valutati con chiavi diverse non si confrontano.

    Insieme alle griglie congela anche i *controlli aritmetici* ricavati dalla
    chiave stessa (Merchandise + Treasure = Total, somme di colonna, subtotali).
    Si tengono solo i controlli che la chiave soddisfa: quelli che nemmeno la
    stampa originale rispetta (refusi del tipografo) sono elencati a parte e
    esclusi, così al modello non si chiede di «sbagliare» come la stampa.

``run``
    Chiede al modello attualmente collegato di leggere le pagine, con lo
    stesso percorso di un'elaborazione dalla pagina Riconosci (END2END, con
    ricaduta sul due-stadi quando l'uscita non entra nel budget), ma tiene il
    risultato in memoria: lo salva in ``data/benchmarks/runs/<data>_<modello>/``
    e lo valuta. Le immagini si leggono senza scrivere cache.

``score``
    Rivaluta previsioni già salvate (senza GPU) contro la chiave.

Metriche, per pagina e complessive:

- **controlli aritmetici rispettati**: la quota dei controlli congelati che
  vale anche sui valori letti dal modello. È la misura «le righe tornano».
- **numeri esatti**: celle numeriche della chiave lette identiche (una cifra
  sbagliata è un errore; le virgole di separazione non contano).
- **vuoti rispettati**: celle ``......`` della chiave che il modello lascia
  vuote invece di inventare un numero (e viceversa: un numero perso conta
  tra i numeri sbagliati).
- **nomi di riga esatti** e CER medio sui nomi.
- **righe trovate**: righe della chiave che trovano una riga corrispondente.

Il confronto non assume che la riga 5 del modello sia la riga 5 della chiave:
le righe si allineano per nome e per numeri in comune (programmazione
dinamica, ordine preservato), poi le colonne per massimo accordo. Così una
riga fusa, persa o aggiunta costa solo quella riga, non tutte le successive.

Esempi::

  PYTHONPATH=backend backend/.venv/bin/python scripts/benchmark_tables.py freeze \\
      --project 1 --source annualstatemento1912indi-11-21.pdf --name calcutta

  PYTHONPATH=backend backend/.venv/bin/python scripts/benchmark_tables.py run \\
      --gold data/benchmarks/calcutta_gold_v1.json

  PYTHONPATH=backend backend/.venv/bin/python scripts/benchmark_tables.py score \\
      --gold data/benchmarks/calcutta_gold_v1.json --predictions <run>/predictions.json
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "data" / "benchmarks"

# ---------------------------------------------------------------- valori ----

from app.services.table_checks import dense, find_checks, is_nil, label_column, number, row_label, value  # noqa: E402
# Lo stesso CER della pagina Valutazione: due benchmark con due definizioni
# darebbero numeri non confrontabili.
from app.services.evaluate import cer  # noqa: E402


def derive_checks(grid: dict) -> tuple[list[dict], list[dict]]:
    """Controlli della chiave: (tenuti, esclusi). Si tengono solo quelli che
    la chiave rispetta; gli esclusi sono refusi della stampa o somme che
    attraversano le pagine, e al modello non si chiede di riprodurli."""
    checks = [
        {"kind": c["kind"], "cells": c["cells"], "total": c["total"], "ok": c["ok"],
         # unità diverse nella stessa somma (Lbs. dentro un totale in Cwt.):
         # il fattore di conversione per cella, come frazione «num/den»
         **({"factors": [[r, k, str(f)] for (r, k), f in c["factors"].items()]} if c.get("factors") else {})}
        for c in find_checks(grid)
    ]
    strip = lambda c: {k: v for k, v in c.items() if k != "ok"}  # noqa: E731
    return [strip(c) for c in checks if c["ok"]], [strip(c) for c in checks if not c["ok"]]


def label_key(text: str | None) -> str:
    """Nome di riga confrontabile: senza puntini guida, segni di nota, maiuscole."""
    s = (text or "").lower()
    s = re.sub(r"[*†‡§]", "", s)
    s = re.sub(r"\.{2,}|…", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------------------------------------------- griglie --

# -------------------------------------------------------- controlli (freeze) --

# ------------------------------------------------------------------ freeze --

def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def cmd_freeze(args) -> int:
    db = ROOT / "data" / "tabularium.db"
    out = BENCH_DIR / f"{args.name}_gold_v{args.version}.json"
    if out.exists():
        print(f"!! {out} esiste già: una chiave congelata non si sovrascrive. Usa --version {args.version + 1}.")
        return 2
    with _db(db) as conn:
        pages = conn.execute(
            "SELECT id, rel_path, pdf_page, width, height FROM pages WHERE project_id=? AND rel_path=? ORDER BY pdf_page",
            (args.project, args.source),
        ).fetchall()
        if not pages:
            print("!! nessuna pagina per questo progetto/sorgente")
            return 2
        gold_pages, kept_n, dropped_all = [], 0, []
        for p in pages:
            tables = []
            for b in conn.execute(
                "SELECT b.id, b.order_idx, b.confirmed, t.grid_json FROM blocks b JOIN tables t ON t.block_id=b.id "
                "WHERE b.page_id=? AND b.label='Table' ORDER BY b.order_idx, b.id",
                (p["id"],),
            ):
                grid = json.loads(b["grid_json"])
                grid = {k: grid.get(k) for k in ("rows", "cols", "cells", "header_rows")}
                grid["cells"] = [
                    {k: c.get(k) for k in ("r", "c", "rowspan", "colspan", "text")} for c in grid["cells"]
                ]
                kept, dropped = derive_checks(grid)
                kept_n += len(kept)
                dropped_all += [{"page": p["pdf_page"] + 1, "block": b["id"], **d} for d in dropped]
                tables.append({"block_id": b["id"], "confirmed_at_freeze": bool(b["confirmed"]), "grid": grid, "checks": kept})
            gold_pages.append({"page_id": p["id"], "pdf_page": p["pdf_page"] + 1, "tables": tables})
    body = {
        "benchmark": args.name,
        "version": args.version,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"project_id": args.project, "file": args.source, "db": str(db.relative_to(ROOT) if db.is_relative_to(ROOT) else db)},
        "note": args.note or "",
        "rule": "Pagine riservate alla valutazione: non vanno mai usate per addestrare.",
        "pages": gold_pages,
        "excluded_checks": dropped_all,
    }
    doc = {"sha256": hashlib.sha256(_canonical(body)).hexdigest(), **body}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(out, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    n_tables = sum(len(p["tables"]) for p in gold_pages)
    n_num = sum(1 for p in gold_pages for t in p["tables"] for c in t["grid"]["cells"] if number(c["text"]) is not None)
    print(f">> congelata {out.relative_to(ROOT)}")
    print(f"   pagine {len(gold_pages)} · tabelle {n_tables} · celle numeriche {n_num} · controlli {kept_n} (+{len(dropped_all)} esclusi)")
    print(f"   sha256 {doc['sha256']}")
    return 0


def load_gold(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    body = {k: v for k, v in doc.items() if k != "sha256"}
    if hashlib.sha256(_canonical(body)).hexdigest() != doc.get("sha256"):
        raise SystemExit(f"!! {path}: lo sha256 non torna — la chiave è stata modificata dopo il congelamento.")
    return doc


# ------------------------------------------------------------ allineamento --

def _row_numbers(m, r) -> list[Fraction]:
    return [v for v in (number(t) for t in m[r]) if v is not None]


def _row_sim(g, gr, glab, p, pr, plab) -> float:
    gl, pl = label_key(row_label(g, gr, glab)), label_key(row_label(p, pr, plab))
    lab_sim = difflib.SequenceMatcher(None, gl, pl, autojunk=False).ratio() if (gl or pl) else 1.0
    gn = _row_numbers(g, gr)
    if not gn:
        return lab_sim
    num_sim = sum((Counter(gn) & Counter(_row_numbers(p, pr))).values()) / len(gn)
    return 0.4 * lab_sim + 0.6 * num_sim if gl else num_sim


def align_rows(g, g_rows, glab, p, p_rows, plab, threshold=0.45) -> dict[int, int]:
    """Allineamento monotono righe chiave → righe previste (DP)."""
    n, k = len(g_rows), len(p_rows)
    sim = [[_row_sim(g, g_rows[i], glab, p, p_rows[j], plab) for j in range(k)] for i in range(n)]
    best = [[0.0] * (k + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(k - 1, -1, -1):
            take = best[i + 1][j + 1] + sim[i][j] if sim[i][j] >= threshold else -1
            best[i][j] = max(best[i + 1][j], best[i][j + 1], take)
    out, i, j = {}, 0, 0
    while i < n and j < k:
        if sim[i][j] >= threshold and best[i][j] == best[i + 1][j + 1] + sim[i][j]:
            out[g_rows[i]] = p_rows[j]
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return out


def _same(a: str | None, b: str | None) -> bool:
    if is_nil(a) and is_nil(b):
        return True
    na, nb = number(a), number(b)
    if na is not None or nb is not None:
        return na == nb
    return label_key(a) == label_key(b)


def align_cols(g, p, row_map, g_cols, p_cols) -> dict[int, int]:
    """Allineamento monotono colonne per massimo accordo sulle righe allineate."""
    # un vuoto in accordo pesa 1/100 di un valore: decide solo le colonne
    # senza numeri (es. p. 11, 1912-13 tutta a puntini), mai le altre
    score = [[sum((1 if is_nil(g[gr][gc]) else 100) for gr, pr in row_map.items()
                  if g[gr][gc] is not None and _same(g[gr][gc], p[pr][pc]))
              for pc in p_cols] for gc in g_cols]
    n, k = len(g_cols), len(p_cols)
    best = [[0] * (k + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(k - 1, -1, -1):
            best[i][j] = max(best[i + 1][j], best[i][j + 1], best[i + 1][j + 1] + score[i][j])
    out, i, j = {}, 0, 0
    while i < n and j < k:
        if score[i][j] > 0 and best[i][j] == best[i + 1][j + 1] + score[i][j]:
            out[g_cols[i]] = p_cols[j]
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            i += 1
        else:
            j += 1
    return out


# --------------------------------------------------------------- punteggio --

def _empty_counts() -> dict:
    return {k: 0 for k in ("numbers", "numbers_ok", "blanks", "blanks_ok", "labels", "labels_ok",
                           "rows", "rows_found", "checks", "checks_ok")} | {"label_cer_sum": 0.0}


def score_table(gold_t: dict, pred_grid: dict | None, errors: list, page_no: int) -> dict:
    g = dense(gold_t["grid"])
    hr = int(gold_t["grid"].get("header_rows") or 0)
    glab = label_column(g)
    g_rows = [r for r in range(hr, len(g)) if any(t and not is_nil(t) for t in g[r])]
    cnt = _empty_counts()
    cnt["rows"] = len(g_rows)
    p = dense(pred_grid) if pred_grid else []
    row_map, col_map = {}, {}
    if p:
        plab = label_column(p)
        row_map = align_rows(g, g_rows, glab, p, list(range(len(p))), plab)
        col_map = align_cols(g, p, row_map, list(range(len(g[0]))), list(range(len(p[0]))))
    cnt["rows_found"] = len(row_map)

    def pred(r, c):
        pr, pc = row_map.get(r), col_map.get(c)
        return None if pr is None or pc is None else p[pr][pc]

    for r in g_rows:
        for c in range(len(g[0])):
            t = g[r][c]
            if t is None:
                continue
            got = pred(r, c)
            if c == glab or (number(t) is None and not is_nil(t)):
                # solo i nomi di riga: unità di misura e virgolette di
                # ripetizione («Lbs.», «„») non sono nomi
                if c != glab or not t.strip():
                    continue
                cnt["labels"] += 1
                ok = got is not None and label_key(got) == label_key(t)
                cnt["labels_ok"] += ok
                # Tetto a 1: un nome spazzatura non deve pesare più di un nome perso.
                cnt["label_cer_sum"] += min(1.0, cer(label_key(t), label_key(got or "")))
                if not ok:
                    errors.append({"page": page_no, "row": row_label(g, r, glab), "kind": "label", "gold": t, "pred": got})
            elif is_nil(t):
                if not t.strip():
                    continue
                cnt["blanks"] += 1
                ok = got is not None and is_nil(got)
                cnt["blanks_ok"] += ok
                if not ok:
                    errors.append({"page": page_no, "row": row_label(g, r, glab), "col": c, "kind": "invented", "gold": t, "pred": got})
            else:
                cnt["numbers"] += 1
                ok = got is not None and number(got) == number(t)
                cnt["numbers_ok"] += ok
                if not ok:
                    errors.append({"page": page_no, "row": row_label(g, r, glab), "col": c, "kind": "number", "gold": t, "pred": got})
    for chk in gold_t["checks"]:
        cnt["checks"] += 1
        # stessa regola del congelamento: le celle coperte da uno span non
        # sono addendi (vale anche per chiavi congelate prima della regola)
        factors = {(r, c): Fraction(f) for r, c, f in chk.get("factors", [])}
        cells = [(r, c) for r, c in chk["cells"] if g[r][c] is not None]
        vals = [pred(r, c) for r, c in cells]
        tot = pred(*chk["total"])
        if tot is None or any(v is None for v in vals):
            continue
        vs, tv = [value(v) for v in vals], value(tot)
        if tv is None or None in vs:
            continue
        total = sum(v * factors.get(rc, 1) for v, rc in zip(vs, cells))
        if (abs(total - tv) < 1) if factors else total == tv:
            cnt["checks_ok"] += 1
    return cnt


def _pct(a, b) -> float | None:
    return round(100 * a / b, 1) if b else None


def summarize(cnt: dict) -> dict:
    return {
        "checks_ok_pct": _pct(cnt["checks_ok"], cnt["checks"]),
        "numbers_exact_pct": _pct(cnt["numbers_ok"], cnt["numbers"]),
        "blanks_kept_pct": _pct(cnt["blanks_ok"], cnt["blanks"]),
        "labels_exact_pct": _pct(cnt["labels_ok"], cnt["labels"]),
        "label_cer": round(cnt["label_cer_sum"] / cnt["labels"], 3) if cnt["labels"] else None,
        "rows_found_pct": _pct(cnt["rows_found"], cnt["rows"]),
        **{k: v for k, v in cnt.items() if k != "label_cer_sum"},
    }


def score(gold: dict, predictions: dict) -> dict:
    """``predictions``: {page_id(str): {"tables": [grid, …], …}}."""
    total, per_page, errors = _empty_counts(), [], []
    for gp in gold["pages"]:
        pred = predictions.get(str(gp["page_id"])) or {}
        preds = list(pred.get("tables") or [])
        page_cnt = _empty_counts()
        for gt in gp["tables"]:
            # la tabella prevista che si allinea meglio a questa tabella della chiave
            best, best_cnt = None, None
            for i, pg in enumerate(preds):
                c = score_table(gt, pg, [], gp["pdf_page"])
                if best_cnt is None or (c["numbers_ok"] + c["labels_ok"]) > (best_cnt["numbers_ok"] + best_cnt["labels_ok"]):
                    best, best_cnt = i, c
            chosen = preds.pop(best) if best is not None else None
            c = score_table(gt, chosen, errors, gp["pdf_page"])
            for k in page_cnt:
                page_cnt[k] += c[k]
        for k in total:
            total[k] += page_cnt[k]
        per_page.append({"page": gp["pdf_page"], "page_id": gp["page_id"], "mode": pred.get("mode"),
                         "error": pred.get("error"), **summarize(page_cnt)})
    return {"overall": summarize(total), "pages": per_page, "errors": errors}


def write_report(run_dir: Path, gold_path: Path, gold: dict, meta: dict, result: dict) -> None:
    report = {"gold": str(gold_path.relative_to(ROOT) if gold_path.is_relative_to(ROOT) else gold_path),
              "gold_sha256": gold["sha256"], **meta, **result}
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    o = result["overall"]
    lines = [
        f"# Benchmark tabelle — {meta.get('model')}", "",
        f"Chiave: `{report['gold']}` (sha256 `{gold['sha256'][:12]}…`) · eseguito {meta.get('started_at')}", "",
        "| Metrica | Valore |", "|---|---|",
        f"| Controlli aritmetici rispettati | **{o['checks_ok_pct']}%** ({o['checks_ok']}/{o['checks']}) |",
        f"| Numeri esatti | **{o['numbers_exact_pct']}%** ({o['numbers_ok']}/{o['numbers']}) |",
        f"| Vuoti rispettati | {o['blanks_kept_pct']}% ({o['blanks_ok']}/{o['blanks']}) |",
        f"| Nomi di riga esatti | {o['labels_exact_pct']}% ({o['labels_ok']}/{o['labels']}), CER {o['label_cer']} |",
        f"| Righe trovate | {o['rows_found_pct']}% ({o['rows_found']}/{o['rows']}) |", "",
        "| Pagina | Modo | Controlli | Numeri | Nomi | Righe | Errore |", "|---|---|---|---|---|---|---|",
    ]
    for p in result["pages"]:
        lines.append(f"| {p['page']} | {p.get('mode') or ''} | {p['checks_ok_pct']}% | {p['numbers_exact_pct']}% | "
                     f"{p['labels_exact_pct']}% | {p['rows_found_pct']}% | {(p.get('error') or '')[:60]} |")
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------- run --

def _page_image(page):
    """Immagine della pagina come la vede il riconoscimento, senza scrivere:
    il master accettato se c'è, altrimenti l'originale raddrizzato in memoria."""
    from app.services import images as imgmod
    from app.services import pages as pagesvc

    active = pagesvc._active_transform_path(page["id"])  # noqa: SLF001
    if active is not None and not pagesvc._has_invalid_transform(page):  # noqa: SLF001
        from PIL import Image
        return Image.open(active)
    img = pagesvc._load_source_image(page, use_deskew=False)  # noqa: SLF001
    if img is None:
        raise RuntimeError("immagine della pagina non disponibile")
    aligned, _angle = imgmod.deskew(img)
    return aligned


def _read_page(client, image, width, height, mode: str) -> list[dict]:
    """Stessi passi di `prefill.model_prelabel_events`, senza database."""
    from app.services import labeling
    from app.domain import otsl, table_headers
    from app.services import prefill as pf

    opts = pf.PrelabelOptions()
    items = client.end2end(image) if mode == "end2end" else client.layout(image)
    items = pf._dedupe_model_items(items)  # noqa: SLF001
    known = {label.name for label in labeling.DEFAULT_LABELS}
    kept = []
    for item in items:
        bbox = pf._scale_bbox(item, width, height)  # noqa: SLF001
        if bbox[2] - bbox[0] < opts.min_size or bbox[3] - bbox[1] < opts.min_size:
            continue
        label = item["label"] if item["label"] in known else "Text"
        if label in ("Table", "Text") and pf._looks_like_table(image, bbox):  # noqa: SLF001
            label = "Table"
        kept.append({"bbox": bbox, "label": label, "content": str(item.get("content") or "").strip()})
    grids = []
    for k in kept:
        if k["label"] != "Table":
            continue
        grid = None
        if mode == "end2end" and k["content"]:
            try:
                raw = k["content"]
                cand = table_headers.repair_flat_header(
                    otsl.otsl_to_grid(otsl.html_to_otsl(raw) if "<table" in raw.lower() else raw))
                if cand.get("rows") and cand.get("cols"):
                    grid = cand
            except Exception:  # noqa: BLE001
                grid = None
        if grid is None:
            grid = pf._recognize_table(client, image, k["bbox"])  # noqa: SLF001
        if grid and grid.get("rows"):
            grids.append(grid)
    return grids


def cmd_run(args) -> int:
    from app.services import inference

    gold_path = Path(args.gold)
    gold = load_gold(gold_path)
    client = inference.get_vllm_client()
    if not client.ping():
        print(f"!! modello non raggiungibile su {client.url}")
        return 2
    model = getattr(client, "model", "model")
    adapter = getattr(client.adapter, "adapter_id", "?")
    started = datetime.now(timezone.utc)
    run_dir = BENCH_DIR / "runs" / f"{started.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^A-Za-z0-9_.-]', '-', model)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db = ROOT / gold["source"]["db"]
    predictions: dict[str, dict] = {}
    with _db(db) as conn:
        for gp in gold["pages"]:
            page = conn.execute("SELECT * FROM pages WHERE id=?", (gp["page_id"],)).fetchone()
            entry: dict = {"tables": []}
            t0 = time.monotonic()
            try:
                image = _page_image(page)
                modes = ["end2end", "two_stage"] if args.mode == "native" else [args.mode]
                for i, mode in enumerate(modes):
                    try:
                        entry["tables"] = _read_page(client, image, page["width"], page["height"], mode)
                        entry["mode"] = mode
                        break
                    except RuntimeError as exc:
                        entry.setdefault("fallbacks", []).append(f"{mode}: {exc}")
                        if i == len(modes) - 1:
                            raise
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)[:500]
            entry["seconds"] = round(time.monotonic() - t0, 1)
            predictions[str(gp["page_id"])] = entry
            print(f"   p.{gp['pdf_page']:>2}: {entry.get('mode') or 'errore'} · {len(entry['tables'])} tabelle · {entry['seconds']}s"
                  + (f"  !! {entry['error'][:100]}" if entry.get("error") else ""))
    meta = {"model": model, "adapter": adapter, "url": client.url, "mode": args.mode,
            "max_pixels": getattr(client, "max_pixels", None),
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (run_dir / "predictions.json").write_text(json.dumps({"meta": meta, "pages": predictions}, ensure_ascii=False), encoding="utf-8")
    result = score(gold, predictions)
    write_report(run_dir, gold_path, gold, meta, result)
    print(f">> {run_dir.relative_to(ROOT)}")
    print((run_dir / "report.md").read_text(encoding="utf-8"))
    return 0


def cmd_score(args) -> int:
    gold_path = Path(args.gold)
    gold = load_gold(gold_path)
    pred_path = Path(args.predictions)
    doc = json.loads(pred_path.read_text(encoding="utf-8"))
    meta = doc.get("meta") or {"model": pred_path.parent.name}
    result = score(gold, doc["pages"])
    run_dir = Path(args.out) if args.out else pred_path.parent
    run_dir.mkdir(parents=True, exist_ok=True)
    write_report(run_dir, gold_path, gold, meta, result)
    print(f">> {run_dir}")
    print((run_dir / "report.md").read_text(encoding="utf-8"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="congela la chiave di risposta")
    f.add_argument("--project", type=int, required=True)
    f.add_argument("--source", required=True, help="rel_path del file sorgente (es. il PDF)")
    f.add_argument("--name", required=True)
    f.add_argument("--version", type=int, default=1)
    f.add_argument("--note", default="")
    r = sub.add_parser("run", help="fa leggere le pagine al modello collegato e valuta")
    r.add_argument("--gold", required=True)
    r.add_argument("--mode", default="native", choices=["native", "end2end", "two_stage"])
    s = sub.add_parser("score", help="rivaluta previsioni salvate")
    s.add_argument("--gold", required=True)
    s.add_argument("--predictions", required=True)
    s.add_argument("--out")
    args = ap.parse_args()
    return {"freeze": cmd_freeze, "run": cmd_run, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
