"""Valutazione del modello affinato sul val split (pagine mai viste).

Metriche (AGENTS.md §11):
- layout: precision/recall/IoU per blocco (match greedy su IoU+label)
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


def layout_match(
    gt: list[dict], pred: list[dict], iou_thr: float = 0.5
) -> dict:
    """Match greedy: ogni GT cerca il pred con stessa label e IoU massimo."""
    used = [False] * len(pred)
    matched = 0
    ious = []
    for g in gt:
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
    return {
        "n_gt": len(gt),
        "n_pred": len(pred),
        "matched": matched,
        "precision": matched / len(pred) if pred else 0.0,
        "recall": matched / len(gt) if gt else 0.0,
        "mean_iou": (sum(ious) / len(ious)) if ious else 0.0,
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
        pred_items = client.layout(image) if image is not None else []
    except Exception as exc:  # noqa: BLE001
        warnings.append(msg("inference_failed", lang, id=page["id"], exc=exc))
        return {"page_id": page["id"], "rel_path": page["rel_path"], "error": str(exc)}

    # riordina per label/ordine (qualsiasi ordine emesso dal modello = reading order)
    page_result = {
        "page_id": page["id"],
        "rel_path": page["rel_path"],
        "layout": layout_match(gt_items, pred_items),
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
        for g in gt_items:
            label = g["label"]
            if label == "Table":
                grid = data["tables"].get(g["id"])
                if not grid or not grid.get("cells"):
                    continue
                try:
                    crop = pagesvc.crop_block_jpeg(
                        page, [int(v) for v in g["source_bbox"]]
                    )
                    crop_img = _image_from_bytes(crop)
                    otsl_out = client.recognize(crop_img, "Table")
                    page_result["tables"].append({**table_metrics(grid, otsl_out, lang=lang), "label": "Table"})
                except Exception as exc:  # noqa: BLE001
                    warnings.append(msg("table_error_w", lang, id=g["id"], exc=exc))
            elif label in _TEXT_LABELS:
                content = g["content"]
                if not content:
                    continue
                try:
                    crop = pagesvc.crop_block_jpeg(
                        page, [int(v) for v in g["source_bbox"]]
                    )
                    crop_img = _image_from_bytes(crop)
                    hyp = client.recognize(crop_img, label)
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
    if approved_only:
        page_data = {
            pid: data for pid, data in page_data.items()
            if data["page"]["status"] in {"approved", "exported"}
        }
    _train, val_ids = builder.compute_split(project_id, ratio, seed, split_strategy, approved_only=approved_only)
    val_ids &= set(page_data)
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
        "config": {"server_url": client.url, "model": client.model, "with_text": with_text, "split": {"ratio": ratio, "seed": seed, "strategy": split_strategy}, "approved_only": approved_only},
        "pages_evaluated": len(ok_pages),
        "val_pages": len(val_ids),
        "aggregates": {
            "layout": _agg_layout(ok_pages),
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


def _agg_layout(pages: list[dict]) -> dict:
    n_gt = sum(p["layout"]["n_gt"] for p in pages)
    n_pred = sum(p["layout"]["n_pred"] for p in pages)
    matched = sum(p["layout"]["matched"] for p in pages)
    ious = [p["layout"]["mean_iou"] for p in pages if p["layout"]["matched"]]
    return {
        "n_gt": n_gt,
        "n_pred": n_pred,
        "matched": matched,
        "precision": matched / n_pred if n_pred else 0.0,
        "recall": matched / n_gt if n_gt else 0.0,
        "mean_iou_of_matched": _mean(ious),
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
