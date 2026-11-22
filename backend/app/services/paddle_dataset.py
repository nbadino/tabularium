"""Exporter dei dataset ufficiali Paddle: layout COCO e VLM ERNIEKit.

Le annotazioni restano nel formato canonico di Tabularium. Questo modulo crea
solo viste derivate, con split per pagina, e non modifica né corregge il gold.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import dataset_builder as builder
from ..models import pp_doclayoutv3 as detector
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


def link_layout_image(out: Path, image: Path, name: str) -> str:
    """Hard-link (or copy) a page image into `images/` and `images_mask/` of a PaddleX
    layout dataset and return its `file_name`, which PaddleX resolves against `images/`
    (`layout_analysis/dataset_checker/.../check_dataset.py`). The tutorial's tree has
    `images_mask/` with "the same content as images"; no code reads it, so it costs a link.
    The folder is self-contained and can be moved to the training machine."""
    for folder in ("images", "images_mask"):
        target = out / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        try:
            os.link(image, target)  # same disk: no second copy of the scan
        except OSError:
            shutil.copy2(image, target)
    return name


def layout_coco(pages: list[dict]) -> tuple[dict, list[str]]:
    """PaddleX layout annotations (COCOInstSegDataset + `read_order`) for one split.

    `pages`: `{"id", "file_name", "width", "height", "boxes": [(label, [x1, y1, x2, y2])]}`
    with pixel boxes in reading order. Categories are PP-DocLayoutV3's 25 with the class
    index as id, so fine-tuning keeps the pretrained head; a Tabularium label without a
    declared category is left out and returned, for the caller to refuse or report.
    """
    images: list[dict] = []
    annotations: list[dict] = []
    unmapped: set[str] = set()
    for page in pages:
        images.append({key: page[key] for key in ("id", "file_name", "width", "height")})
        order = 0
        for label, (x1, y1, x2, y2) in page["boxes"]:
            category = detector.CATEGORY_FOR_LABEL.get(label)
            if category is None:
                unmapped.add(label)
                continue
            width, height = x2 - x1, y2 - y1
            annotations.append({
                "id": len(annotations) + 1,
                "image_id": page["id"],
                "category_id": detector.CATEGORIES.index(category),
                "bbox": [x1, y1, width, height],
                # Tabularium blocks are rectangles: the polygon is the box itself.
                "segmentation": [[x1, y1, x2, y1, x2, y2, x1, y2]],
                "area": width * height,
                "iscrowd": 0,
                # From 0 and consecutive within the image, as the checker requires.
                "read_order": order,
            })
            order += 1
    categories = [{"id": index, "name": name} for index, name in enumerate(detector.CATEGORIES)]
    return {"images": images, "annotations": annotations, "categories": categories}, sorted(unmapped)


def write_layout_split(out: Path, split: str, coco: dict) -> Path:
    """`annotations/instance_{train,val}.json`, the names the PaddleX checker requires."""
    path = out / "annotations" / f"instance_{split}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(coco, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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
    layout: dict[str, list[dict]] = {"train": [], "val": []}
    # Riconoscitore di riga (PP-OCR rec): un campione per cella trascritta.
    # È un modello diverso dal VLM — quello che gira in locale dentro
    # `table_detect.fill_cells` — e vuole il suo formato: `percorso\ttesto`.
    rec: dict[str, list[tuple[str, str]]] = {"train": [], "val": []}
    rec_cells = {"total": 0, "unverified_skipped": 0}
    cells_dir = out / "cells"
    cells_dir.mkdir(exist_ok=True)
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
        boxes = []
        for block in sorted(item["blocks"], key=lambda b: b["order_idx"] if b["order_idx"] is not None else 0):
            if approved_only and not bool(block["confirmed"]):
                continue
            points = builder.parse_points(block["points"])
            if len(points) < 2:
                continue
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            boxes.append((str(block["label"]), [min(xs), min(ys), max(xs), max(ys)]))
        layout[split].append({
            "id": int(page_id),
            "file_name": link_layout_image(out, Path(page_image), f"page_{page_id}{Path(page_image).suffix.lower()}"),
            "width": int(page["width"]), "height": int(page["height"]), "boxes": boxes,
        })
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


    written = {}
    for split in ("train", "val"):
        path = out / f"rec_{split}.txt"
        path.write_text(
            "".join(f"{image}\t{text}\n" for image, text in rec[split]), encoding="utf-8"
        )
        written[f"rec_{split}"] = str(path)
    unmapped: set[str] = set()
    for split in ("train", "val"):
        coco, missing = layout_coco(layout[split])
        unmapped.update(missing)
        counts["layout"][split] = len(coco["annotations"])
        written[f"layout_{split}"] = str(write_layout_split(out, split, coco))
    # PP-OCR rec vuole il proprio dizionario di caratteri: quello del corpus,
    # non quello generico, altrimenti la testa di classificazione porta migliaia
    # di classi che questo materiale non contiene.
    charset = sorted({ch for split in ("train", "val") for _, text in rec[split] for ch in text})
    dict_path = out / "rec_dict.txt"
    dict_path.write_text("".join(f"{ch}\n" for ch in charset), encoding="utf-8")
    written["rec_dict"] = str(dict_path)
    written["rec_data_dir"] = str(out)
    written["layout_dataset_dir"] = str(out)
    if unmapped:
        warnings.append(
            "layout: labels without a PP-DocLayoutV3 category, blocks left out: " + ", ".join(sorted(unmapped))
        )
    if not layout["val"]:
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
    manifest = {"format": "paddlex-layout-ppocr-rec-v2", "project_id": project_id, "split_ratio": split_ratio, "seed": seed, "approved_only": approved_only, "categories": list(detector.CATEGORIES), "counts": counts, "cells": rec_cells, "charset": len(charset), "warnings": warnings, "files": written}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
