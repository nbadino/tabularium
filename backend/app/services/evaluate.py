"""Valutazione del modello affinato sul val split (pagine mai viste).

Metriche (AGENTS.md §11):
- layout: precision/recall/F1/IoU per blocco (match greedy su IoU+label),
  a IoU 0.5 e alla soglia stretta 0.75, anche per label
- ordine di lettura: Levenshtein normalizzato sulle sequenze di label
- testo: CER/WER sui ritagli trascritti
- tabelle: struttura (righe/colonne/span) + CER medio sulle celle
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import dataset_builder as builder
from . import inference as infmod
from . import labeling
from . import otsl as otslmod
from . import pages as pagesvc
from .i18n import msg
from .. import config

_TEXT_LABELS = {l.name for l in labeling.DEFAULT_LABELS if l.prompt_kind == "text"}
STRICT_IOU = 0.75

# --- metriche di base ---------------------------------------------------------


def _edit_distance(a, b) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    return _edit_distance(ref, hyp) / len(ref)


def wer(ref: str, hyp: str) -> float:
    rw = ref.split()
    hw = hyp.split()
    if not rw:
        return 0.0 if not hw else 1.0
    return _edit_distance(rw, hw) / len(rw)


def norm_levenshtein(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 0.0
    return _edit_distance(a, b) / max(len(a), len(b))


def iou(b1: list[float], b2: list[float]) -> float:
    x1, y1, x2, y2 = max(b1[0], b2[0]), max(b1[1], b2[1]), min(b1[2], b2[2]), min(b1[3], b2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = max(0, b1[2] - b1[0]) * max(0, b1[3] - b1[1])
    area2 = max(0, b2[2] - b2[0]) * max(0, b2[3] - b2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


# --- match layout -------------------------------------------------------------


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def layout_match(
    gt: list[dict], pred: list[dict], iou_thr: float = 0.5
) -> dict:
    """Match greedy: ogni GT cerca il pred con stessa label e IoU massimo.

    A soglia 0.75 è la regola del reward di layout di LightOnOCR-3: un
    riquadro vale solo se ha la label giusta e combacia davvero; i blocchi
    mancati e quelli inventati abbassano l'F1.
    """
    used = [False] * len(pred)
    matched = 0
    ious = []
    pairs = []
    per_label: dict[str, dict] = {}
    for g in gt:
        per_label.setdefault(g["label"], {"n_gt": 0, "n_pred": 0, "matched": 0})["n_gt"] += 1
    for p in pred:
        per_label.setdefault(p["label"], {"n_gt": 0, "n_pred": 0, "matched": 0})["n_pred"] += 1
    for gi, g in enumerate(gt):
        best_i, best_iou = -1, iou_thr
        for i, p in enumerate(pred):
            if used[i] or p["label"] != g["label"]:
                continue
            v = iou(g["bbox"], p["bbox"])
            if v >= best_iou:
                best_i, best_iou = i, v
        if best_i >= 0:
            matched += 1
            ious.append(best_iou)
            used[best_i] = True
            pairs.append([gi, best_i])
            per_label[g["label"]]["matched"] += 1
    precision = matched / len(pred) if pred else 0.0
    recall = matched / len(gt) if gt else 0.0
    return {
        "n_gt": len(gt),
        "n_pred": len(pred),
        "matched": matched,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "mean_iou": (sum(ious) / len(ious)) if ious else 0.0,
        "iou_thr": iou_thr,
        "per_label": per_label,
        "pairs": pairs,
    }


def order_metrics(gt_labels: list[str], pred_labels: list[str]) -> dict:
    return {
        "levenshtein_norm": norm_levenshtein(gt_labels, pred_labels),
        "exact": gt_labels == pred_labels,
        "n": len(gt_labels),
    }


# --- tabelle ------------------------------------------------------------------


def table_metrics(gt_grid: dict, pred_otsl: str, lang: str = "it") -> dict:
    try:
        pred = otslmod.otsl_to_grid(pred_otsl)
    except Exception:  # noqa: BLE001
        return {"structure_ok": False, "cell_cer": 1.0, "error": msg("otsl_unparsable", lang)}
    gt_cells = {(c["r"], c["c"]): c for c in gt_grid.get("cells", [])}
    pred_cells = {(c["r"], c["c"]): c for c in pred.get("cells", [])}
    structure_ok = (
        pred.get("rows") == gt_grid.get("rows")
        and pred.get("cols") == gt_grid.get("cols")
        and len(pred_cells) == len(gt_cells)
        and all(
            gt_cells[k].get("rowspan") == pred_cells[k].get("rowspan")
            and gt_cells[k].get("colspan") == pred_cells[k].get("colspan")
            for k in gt_cells
            if k in pred_cells
        )
    )
    cers = [
        cer(gt_cells[k].get("text", ""), pred_cells[k].get("text", ""))
        for k in gt_cells
        if k in pred_cells
    ]
    return {
        "structure_ok": bool(structure_ok),
        "cell_cer": sum(cers) / len(cers) if cers else 1.0,
    }


# --- bbox 0-1000 --- -----------------------------------------------------------


def _norm(points: list) -> list[float]:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


# --- valutazione pagina -------------------------------------------------------


def _evaluate_page(
    client, data: dict, with_text: bool, warnings: list[str], lang: str = "it"
) -> dict:
    page = data["page"]
    gt_blocks = data["blocks"]
    gt_items = []
    for b in gt_blocks:
        points = builder.parse_points(b["points"])
        if not points:
            continue
        source_bbox = _norm(points)
        try:
            normalized_bbox = builder._normalize_bbox(  # noqa: SLF001
                points, page["width"], page["height"]
            )
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            warnings.append(msg("bbox_invalid", lang, id=b["id"], exc=exc))
            continue
        gt_items.append(
            {
                # L'API del modello restituisce coordinate 0-1000. Conservare
                # qui i pixel sorgente rendeva IoU e overlay dipendenti dalla
                # risoluzione della pagina e quindi scientificamente falsi.
                "bbox": normalized_bbox,
                "source_bbox": source_bbox,
                "label": b["label"],
                "content": (b["content"] or "").strip(),
                "id": b["id"],
            }
        )

    try:
        image = pagesvc.load_source_image(page)
        # I modelli solo end2end (LightOnOCR-3, Unlimited-OCR, DeepSeek-OCR…)
        # non hanno un prompt di layout: si misurano sulla loro generazione
        # nativa, che porta anche il contenuto di ogni blocco.
        adapter = getattr(client, "adapter", None)
        end2end = getattr(adapter, "native_prefill_mode", None) == "end2end"
        if image is None:
            pred_items = []
        elif end2end:
            pred_items = client.end2end(image)
        else:
            pred_items = client.layout(image)
    except Exception as exc:  # noqa: BLE001
        warnings.append(msg("inference_failed", lang, id=page["id"], exc=exc))
        return {
            "page_id": page["id"],
            "rel_path": page["rel_path"],
            "pdf_page": page["pdf_page"],
            "error": str(exc),
        }

    # riordina per label/ordine (qualsiasi ordine emesso dal modello = reading order)
    page_result = {
        "page_id": page["id"],
        "rel_path": page["rel_path"],
        "pdf_page": page["pdf_page"],
        "layout": layout_match(gt_items, pred_items),
        "layout_strict": layout_match(gt_items, pred_items, iou_thr=STRICT_IOU),
        "order": order_metrics(
            [g["label"] for g in gt_items], [p["label"] for p in pred_items]
        ),
        "gt_items": gt_items,
        "pred_items": pred_items,
        "text": [],
        "tables": [],
        "actions": [],
    }

    if with_text:
        # end2end: il contenuto è quello del blocco predetto abbinato (IoU 0.5).
        # Un blocco non trovato non ha contenuto da misurare e resta fuori:
        # lo conta già il recall del layout.
        matched_content = {
            gi: str(pred_items[pi].get("content") or "")
            for gi, pi in page_result["layout"]["pairs"]
        }

        def hypothesis(gi: int, g: dict) -> str | None:
            if end2end:
                return matched_content.get(gi)
            crop = pagesvc.crop_block_jpeg(page, [int(v) for v in g["source_bbox"]])
            return client.recognize(_image_from_bytes(crop), g["label"])

        for gi, g in enumerate(gt_items):
            label = g["label"]
            if label == "Table":
                grid = data["tables"].get(g["id"])
                if not grid or not grid.get("cells"):
                    continue
                try:
                    otsl_out = hypothesis(gi, g)
                    if otsl_out is None:
                        continue
                    if "<table" in otsl_out.lower():
                        otsl_out = otslmod.html_to_otsl(otsl_out)
                    page_result["tables"].append({**table_metrics(grid, otsl_out, lang=lang), "label": "Table"})
                except Exception as exc:  # noqa: BLE001
                    warnings.append(msg("table_error_w", lang, id=g["id"], exc=exc))
            elif label in _TEXT_LABELS:
                content = g["content"]
                if not content:
                    continue
                try:
                    hyp = hypothesis(gi, g)
                    if hyp is None:
                        continue
                    page_result["text"].append(
                        {"label": label, "cer": cer(content, hyp), "wer": wer(content, hyp), "gt": content, "hyp": hyp}
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(msg("text_error_w", lang, id=g["id"], exc=exc))
    # Azioni concrete per la prossima iterazione di annotazione.
    if page_result["layout"]["recall"] < 0.8:
        page_result["actions"].append(msg("action_add_similar", lang))
    if page_result["order"]["levenshtein_norm"] > 0.2:
        page_result["actions"].append(msg("action_review_order", lang))
    if any(t["cer"] > 0.2 for t in page_result["text"]):
        page_result["actions"].append(msg("action_fix_cer", lang))
    if any(not t["structure_ok"] for t in page_result["tables"]):
        page_result["actions"].append(msg("action_review_tables", lang))
    return page_result


def _image_from_bytes(data: bytes):
    from io import BytesIO

    from PIL import Image as PILImage

    return PILImage.open(BytesIO(data))


# --- valutazione progetto -----------------------------------------------------


def _has_finished_training(project_id: int) -> bool:
    """Vero se almeno un training del progetto è arrivato in fondo."""
    from . import trainer  # noqa: PLC0415

    root = trainer.runs_dir(project_id)
    if not root.is_dir():
        return False
    return any(
        (trainer._read_run_meta(run) or {}).get("state") == "finished"  # noqa: SLF001
        for run in root.iterdir() if run.is_dir()
    )


def evaluate_project(
    project_id: int,
    server_url: str | None = None,
    model: str | None = None,
    with_text: bool = True,
    limit: int = 50,
    lang: str = "it",
) -> dict:
    """Valuta il val split; scrive report JSON in eval_<ts>/report.json."""
    warnings: list[str] = []
    page_data = builder.collect_pages_with_blocks(project_id)
    if not page_data:
        raise ValueError(msg("no_pages_annotated", lang))
    # parametri split coerenti con l'ultima build del dataset (se presente)
    ratio, seed, split_strategy, approved_only = 0.9, 42, "page", False
    report_path = builder._project_dir(project_id) / "dataset" / "report.json"  # noqa: SLF001
    if report_path.exists():
        try:
            rep = json.loads(report_path.read_text(encoding="utf-8"))
            ratio = rep.get("split", {}).get("ratio", ratio)
            seed = rep.get("split", {}).get("seed", seed)
            split_strategy = rep.get("split", {}).get("strategy", split_strategy)
            approved_only = rep.get("approved_only", approved_only)
        except (TypeError, ValueError):
            pass
    # La verità di una valutazione è solo ciò che una persona ha approvato,
    # qualunque cosa dica l'ultima build del dataset: una pagina con bozze mai
    # verificate conterebbe l'output del modello come risposta giusta, e il
    # punteggio misurerebbe quanto il modello somiglia a se stesso.
    page_data = {
        pid: data for pid, data in page_data.items()
        if data["page"]["status"] in {"approved", "exported"}
    }
    if not page_data:
        raise ValueError(msg("no_gold_pages", lang))
    # Il val split serve a tenere fuori le pagine viste in training. Finché
    # nessun training è finito nessuna pagina è stata vista: la baseline si
    # misura su tutte le pagine approvate, non su un decimo di esse.
    scope = "val" if _has_finished_training(project_id) else "all_approved"
    if scope == "val":
        _train, val_ids = builder.compute_split(project_id, ratio, seed, split_strategy, approved_only=approved_only)
        val_ids &= set(page_data)
    else:
        val_ids = set(page_data)
    val_ids = sorted(val_ids)[:limit]
    if not val_ids:
        raise ValueError(msg("no_val_pages", lang))

    client = infmod.get_vllm_client(url=server_url, model=model)
    if not client.ping():
        warnings.append(msg("vllm_ping", lang, url=client.url))

    pages_result = []
    for pid in val_ids:
        data = page_data[pid]
        res = _evaluate_page(client, data, with_text, warnings, lang=lang)
        pages_result.append(res)

    # aggregazioni solo sulle pagine valutate con successo
    ok_pages = [p for p in pages_result if "error" not in p]
    n_text = sum(len(p["text"]) for p in ok_pages)
    n_tables = sum(len(p["tables"]) for p in ok_pages)
    texts = [t for p in ok_pages for t in p["text"]]
    tables = [t for p in ok_pages for t in p["tables"]]
    report = {
        "project_id": project_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "config": {"server_url": client.url, "model": client.model, "provider": getattr(client, "provider", None), "with_text": with_text, "split": {"ratio": ratio, "seed": seed, "strategy": split_strategy}, "approved_only": approved_only},
        "pages_evaluated": len(ok_pages),
        "val_pages": len(val_ids),
        "scope": scope,
        "aggregates": {
            "layout": _agg_layout(ok_pages),
            "layout_strict": _agg_layout(ok_pages, key="layout_strict"),
            "order": _agg_order(ok_pages),
            "text": {"n": n_text, "mean_cer": _mean([t["cer"] for t in texts]), "mean_wer": _mean([t["wer"] for t in texts])},
            "tables": {"n": n_tables, "structure_ok_pct": (_mean([1.0 if t["structure_ok"] else 0.0 for t in tables]) * 100) if tables else None, "mean_cell_cer": _mean([t["cell_cer"] for t in tables])},
        },
        "pages": pages_result,
        "warnings": warnings,
    }
    eval_dir = builder._project_dir(project_id) / "eval" / f"eval_{int(__import__('time').time())}"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["eval_dir"] = str(eval_dir)
    return report


def _agg_layout(pages: list[dict], key: str = "layout") -> dict:
    layouts = [p[key] for p in pages if key in p]
    n_gt = sum(l["n_gt"] for l in layouts)
    n_pred = sum(l["n_pred"] for l in layouts)
    matched = sum(l["matched"] for l in layouts)
    ious = [l["mean_iou"] for l in layouts if l["matched"]]
    per_label: dict[str, dict] = {}
    for layout in layouts:
        for label, counts in layout.get("per_label", {}).items():
            total = per_label.setdefault(label, {"n_gt": 0, "n_pred": 0, "matched": 0})
            for field in total:
                total[field] += counts[field]
    for counts in per_label.values():
        precision = counts["matched"] / counts["n_pred"] if counts["n_pred"] else 0.0
        recall = counts["matched"] / counts["n_gt"] if counts["n_gt"] else 0.0
        counts.update(precision=precision, recall=recall, f1=_f1(precision, recall))
    precision = matched / n_pred if n_pred else 0.0
    recall = matched / n_gt if n_gt else 0.0
    return {
        "n_gt": n_gt,
        "n_pred": n_pred,
        "matched": matched,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "mean_iou_of_matched": _mean(ious),
        "per_label": per_label,
    }


def _agg_order(pages: list[dict]) -> dict:
    lev = [p["order"]["levenshtein_norm"] for p in pages]
    exact = sum(1 for p in pages if p["order"]["exact"])
    return {
        "mean_levenshtein_norm": _mean(lev),
        "exact_pct": (exact / len(pages) * 100) if pages else None,
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None
