"""Exporter dei dataset ufficiali Paddle: layout COCO e VLM ERNIEKit.

Le annotazioni restano nel formato canonico di Tabularium. Questo modulo crea
solo viste derivate, con split per pagina, e non modifica né corregge il gold.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import dataset_builder as builder
from . import pages as pagesvc


# Sotto questa misura in pixel non c'è una riga di testo da leggere: la cella è
# un artefatto della griglia, non un campione.
_REC_MIN_SIDE = 6


def _grid_lines(grid: dict, axis: str, count: int) -> list[float]:
    """Confini normalizzati 0–1, o la spaziatura uniforme se non ce ne sono."""
    lines = [float(v) for v in (grid.get(axis) or [])]
    if len(lines) == count + 1:
        return lines
    return [i / count for i in range(count + 1)]


def _cell_samples(grid: dict, bbox: tuple[int, int, int, int]) -> list[dict]:
    """Celle con testo → ritaglio in pixel pagina, testo, provenienza.

    È lo stesso taglio che il riconoscitore di riga vede in esercizio
    (``api/blocks.block_table_cell_recognize``): stessi confini, stesso respiro
    di un quarto di passo attorno alla cella. Un dataset tagliato diversamente
    da come si legge insegnerebbe al modello un'inquadratura che non incontrerà
    mai. L'altezza resta quella nativa: la normalizzazione a 32/48 px è compito
    del trainer PP-OCR, e farla due volte perde inchiostro.
    """
    rows, cols = int(grid.get("rows") or 0), int(grid.get("cols") or 0)
    if rows <= 0 or cols <= 0:
        return []
    x0p, y0p, x1p, y1p = bbox
    width, height = x1p - x0p, y1p - y0p
    if width <= 0 or height <= 0:
        return []
    vlines = _grid_lines(grid, "vlines", cols)
    hlines = _grid_lines(grid, "hlines", rows)
    steps = sorted(b - a for a, b in zip(hlines, hlines[1:]) if b > a)
    pitch = max(8, round((steps[len(steps) // 2] if steps else 1 / rows) * height))
    pad = max(2, int(0.25 * pitch))

    samples: list[dict] = []
    for cell in grid.get("cells", []):
        text = " ".join(str(cell.get("text") or "").split())
        if not text:
            continue
        r0 = int(cell.get("r", 0))
        c0 = int(cell.get("c", 0))
        r1 = min(rows, r0 + int(cell.get("rowspan", 1)))
        c1 = min(cols, c0 + int(cell.get("colspan", 1)))
        if not (0 <= r0 < r1 <= rows and 0 <= c0 < c1 <= cols):
            continue
        left = x0p + round(vlines[c0] * width) - pad
        right = x0p + round(vlines[c1] * width) + pad
        top = y0p + round(hlines[r0] * height) - pad
        bottom = y0p + round(hlines[r1] * height) + pad
        box = (
            max(x0p, left),
            max(y0p, top),
            min(x1p, right),
            min(y1p, bottom),
        )
        if box[2] - box[0] < _REC_MIN_SIDE or box[3] - box[1] < _REC_MIN_SIDE:
            continue
        samples.append(
            {
                "r": r0,
                "c": c0,
                "text": text,
                "bbox": box,
                "verified": bool(cell.get("verified")),
            }
        )
    return samples


def _split(
    project_id: int, page_ids: list[int], ratio: float, seed: int, approved_only: bool
) -> tuple[set[int], set[int]]:
    """Lo stesso split del dataset canonico, non una seconda regola.

    Il layout PaddleX e le celle PP-OCR devono vedere le stesse pagine di
    training/validation del VLM: un export con uno split diverso rende i
    numeri dei due modelli non confrontabili. `compute_split` esclude anche
    il gold protetto; quelle pagine non entrano in nessuna delle due viste.
    """
    train_ids, val_ids = builder.compute_split(
        project_id, ratio, seed, "page", approved_only=approved_only
    )
    known = set(page_ids)
    return train_ids & known, val_ids & known


def build(project_id: int, split_ratio: float = 0.9, seed: int = 42, approved_only: bool = True) -> dict:
    data = builder.collect_pages_with_blocks(project_id)
    if not data:
        raise ValueError("nessuna pagina annotata nel progetto")
    out = builder._project_dir(project_id) / "paddle-dataset"
    crops = out / "crops"
    out.mkdir(parents=True, exist_ok=True)
    crops.mkdir(parents=True, exist_ok=True)
    train_ids, val_ids = _split(project_id, list(data), split_ratio, seed, approved_only)
    warnings: list[str] = []
    layout: dict[str, dict] = {
        "train": {"images": [], "annotations": []},
        "val": {"images": [], "annotations": []},
    }
    # Riconoscitore di riga (PP-OCR rec): un campione per cella trascritta.
    # È un modello diverso dal VLM — quello che gira in locale dentro
    # `table_detect.fill_cells` — e vuole il suo formato: `percorso\ttesto`.
    rec: dict[str, list[tuple[str, str]]] = {"train": [], "val": []}
    rec_cells = {"total": 0, "unverified_skipped": 0}
    cells_dir = out / "cells"
    cells_dir.mkdir(exist_ok=True)
    categories: dict[str, int] = {}
    ann_id = 1
    counts = {
        "layout": {"train": 0, "val": 0},
        "rec": {"train": 0, "val": 0},
    }

    for page_id, item in data.items():
        page = item["page"]
        if approved_only and page["status"] not in {"approved", "exported"}:
            continue
        if page_id not in train_ids and page_id not in val_ids:
            # Gold protetto o pagina non scelta dallo split: non si inventa
            # una validation con ciò che il training non può vedere.
            continue
        split = "train" if page_id in train_ids else "val"
        page_image = builder._page_image_path(page, crops, warnings)
        if not page_image:
            continue
        layout[split]["images"].append({
            "id": int(page_id), "file_name": page_image,
            "width": int(page["width"]), "height": int(page["height"]),
        })
        for block in item["blocks"]:
            if approved_only and not bool(block["confirmed"]):
                continue
            points = builder.parse_points(block["points"])
            if len(points) < 2:
                continue
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            bbox = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
            label = str(block["label"])
            categories.setdefault(label, len(categories) + 1)
            layout[split]["annotations"].append({
                "id": ann_id, "image_id": int(page_id), "category_id": categories[label], "bbox": bbox,
                "area": bbox[2] * bbox[3], "iscrowd": 0,
                "reading_order": block["order_idx"],
            })
            ann_id += 1
        # --- celle: dataset del riconoscitore di riga -----------------------
        # Passata separata dal VLM di proposito: una tabella il cui OTSL non si
        # genera (celle unite incoerenti) ha comunque celle leggibili, e sono
        # esattamente le correzioni fatte a mano che devono tornare indietro
        # come addestramento.
        for block in item["blocks"]:
            if str(block["label"]) != "Table":
                continue
            if approved_only and not bool(block["confirmed"]):
                continue
            grid = item["tables"].get(int(block["id"]))
            if not grid:
                continue
            points = builder.parse_points(block["points"])
            if len(points) < 2:
                continue
            xs = [int(float(pt[0])) for pt in points]
            ys = [int(float(pt[1])) for pt in points]
            block_box = (min(xs), min(ys), max(xs), max(ys))
            for sample in _cell_samples(grid, block_box):
                rec_cells["total"] += 1
                if approved_only and not sample["verified"]:
                    rec_cells["unverified_skipped"] += 1
                    continue
                name = f"cell_{block['id']}_{sample['r']}_{sample['c']}.jpg"
                target = cells_dir / name
                try:
                    target.write_bytes(pagesvc.crop_block_jpeg(page, sample["bbox"]))
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"cella {block['id']}:{sample['r']},{sample['c']} non ritagliata: {exc}")
                    continue
                rec[split].append((f"cells/{name}", sample["text"]))
                counts["rec"][split] += 1

        counts["layout"][split] += sum(1 for ann in layout[split]["annotations"] if ann["image_id"] == page_id)

    written = {}
    for split in ("train", "val"):
        path = out / f"rec_{split}.txt"
        path.write_text(
            "".join(f"{image}\t{text}\n" for image, text in rec[split]), encoding="utf-8"
        )
        written[f"rec_{split}"] = str(path)
        path = out / f"layout_{split}.json"
        path.write_text(json.dumps({**layout[split], "categories": [{"id": i, "name": n} for n, i in categories.items()]}, ensure_ascii=False, indent=2), encoding="utf-8")
        written[f"layout_{split}"] = str(path)
    # PaddleX discovers the COCO annotation files relative to dataset_dir. Keep
    # the JSONL/COCO flat files for reproducibility and also expose the
    # conventional annotations/ layout expected by COCODetDataset.
    annotations = out / "annotations"
    annotations.mkdir(exist_ok=True)
    for split in ("train", "val"):
        source = out / f"layout_{split}.json"
        target = annotations / f"{split}.json"
        shutil.copyfile(source, target)
    # PP-OCR rec vuole il proprio dizionario di caratteri: quello del corpus,
    # non quello generico, altrimenti la testa di classificazione porta migliaia
    # di classi che questo materiale non contiene.
    charset = sorted({ch for split in ("train", "val") for _, text in rec[split] for ch in text})
    dict_path = out / "rec_dict.txt"
    dict_path.write_text("".join(f"{ch}\n" for ch in charset), encoding="utf-8")
    written["rec_dict"] = str(dict_path)
    written["rec_data_dir"] = str(out)
    written["layout_dataset_dir"] = str(out)
    if not layout["val"]["images"]:
        warnings.append("validation layout vuota: servono almeno due pagine annotate per creare uno split")
    if rec_cells["unverified_skipped"]:
        warnings.append(
            f"{rec_cells['unverified_skipped']} celle escluse dal dataset di riconoscimento: "
            "il testo è ancora quello proposto dal prefill, nessuno l'ha verificato"
        )
    if not rec["train"]:
        warnings.append(
            "dataset di riconoscimento vuoto: servono celle con testo verificato in una tabella"
        )
    manifest = {"format": "paddlex-layout-ppocr-rec-v1", "project_id": project_id, "split_ratio": split_ratio, "seed": seed, "approved_only": approved_only, "categories": categories, "counts": counts, "cells": rec_cells, "charset": len(charset), "warnings": warnings, "files": written}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
