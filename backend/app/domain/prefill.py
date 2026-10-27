"""Page-geometry decisions used by prefill, without a database or a model client.

Phase P3b of `docs/architecture/program.md`. These functions answer questions about
boxes and labels — do they overlap, which one contains which, which duplicates are the
same block, what cluster of lines is a table — and they answer them from values alone.
They lived inside `services/prefill.py`, where the surrounding function also wrote to
SQLite; extracting them is what lets the selection be tested without a page, a database
or an inference server.
"""
from __future__ import annotations

from typing import Any

# A register: at least six lines over three columns. Below that it is prose that
# happens to line up, and promoting it to a table would be a wrong correction.
_TABLE_MIN_ROWS = 6
_TABLE_MIN_COLS = 3

def _iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0

def _nms_lite(items: list[dict], thr: float = 0.85) -> list[dict]:
    kept: list[dict] = []
    for item in sorted(items, key=lambda x: -x["score"]):
        if any(_iou(item["bbox"], k["bbox"]) > thr for k in kept):
            continue
        kept.append(item)
    return kept

def _table_cluster(kept: list[dict]) -> tuple[list[int], list[int]] | None:
    """Il più grande cluster verticale di righe consecutive, candidato tabella.

    L'OCR di riga non sa che è una tabella, ma le righe del registro si
    riconoscono per contiguità: il passo fra una riga e la successiva è
    dell'ordine dell'altezza di riga, mentre sopra (testata, numero del
    fascicolo) c'è un salto. Prendere il cluster più grande evita il difetto
    misurato di passare al rilevatore la pagina intera, testata compresa.
    """
    if len(kept) < _TABLE_MIN_ROWS:
        return None
    heights = sorted(b["bbox"][3] - b["bbox"][1] for b in kept)
    med_h = max(1, heights[len(heights) // 2])
    order = sorted(range(len(kept)), key=lambda i: kept[i]["bbox"][1])
    clusters: list[list[int]] = []
    for i in order:
        if clusters and kept[i]["bbox"][1] - kept[clusters[-1][-1]]["bbox"][3] <= 1.5 * med_h:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    best = max(clusters, key=len)
    if len(best) < _TABLE_MIN_ROWS:
        return None
    xs1 = [kept[i]["bbox"][0] for i in best]
    ys1 = [kept[i]["bbox"][1] for i in best]
    xs2 = [kept[i]["bbox"][2] for i in best]
    ys2 = [kept[i]["bbox"][3] for i in best]
    return [min(xs1), min(ys1), max(xs2), max(ys2)], best

def _scale_bbox(item: dict, width: int, height: int) -> list[int]:
    """bbox normalizzato 0–1000 → pixel pagina, clamped ai bordi."""
    x1, y1, x2, y2 = item["bbox"]
    return [
        max(0, min(round(x1 / 1000 * width), width)),
        max(0, min(round(y1 / 1000 * height), height)),
        max(0, min(round(x2 / 1000 * width), width)),
        max(0, min(round(y2 / 1000 * height), height)),
    ]

def _containment(inner: list[float], outer: list[float]) -> float:
    """Quanta parte di ``inner`` sta dentro ``outer``, 0–1."""
    x1, y1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x2, y2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area = max(0, inner[2] - inner[0]) * max(0, inner[3] - inner[1])
    return inter / area if area > 0 else 0.0

def _box_of(item: dict) -> list[float] | None:
    bbox = item.get("bbox")
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None

def _label_of(item: dict) -> str:
    return str(item.get("label") or item.get("category") or "")

def _drop_nested_duplicates(items: list[dict], threshold: float = 0.9) -> list[dict]:
    """Scarta i blocchi contenuti in un altro blocco della stessa classe.

    Il risultato ufficiale di PaddleX porta la stessa regione a due livelli —
    la lista di parsing finale e le detection grezze del layout — e il bridge
    (`paddle_official._items`) raccoglie ricorsivamente entrambi. Sul registro
    del campione la colonna di sinistra arrivava così quattro volte: una intera,
    con testo e griglia, più tre fasce che la ritagliano. L'IoU non le vede
    (una fascia sovrapposta al 100% con l'intero ha IoU 0,25), il contenimento
    sì.

    Si tiene sempre il blocco più grande: è quello su cui il pipeline ha
    riconosciuto il contenuto, e la fascia interna non aggiunge nulla che la
    griglia dell'intero non abbia già.
    """
    boxes = [(item, _box_of(item)) for item in items]
    kept: list[dict] = []
    for item, box in boxes:
        if box is None:
            kept.append(item)
            continue
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        swallowed = False
        for other, other_box in boxes:
            if other is item or other_box is None:
                continue
            other_area = max(0.0, other_box[2] - other_box[0]) * max(0.0, other_box[3] - other_box[1])
            if other_area <= area or _label_of(other) != _label_of(item):
                continue
            if _containment(box, other_box) >= threshold:
                swallowed = True
                break
        if not swallowed:
            kept.append(item)
    return kept

def _dedupe_model_items(items: list[dict], iou_threshold: float = 0.92) -> list[dict]:
    """Rimuove duplicati evidenti dalla lista generata dal modello.

    Alcuni server/vLLM possono ripetere lo stesso record durante una
    generazione end2end lunga. Non è corretto creare decine di bozze identiche
    né mostrarle come blocchi distinti: conserviamo il primo record nell'ordine
    di lettura e lasciamo intatti i blocchi realmente diversi.

    Al duplicato esatto si aggiunge quello *annidato*: v. ``_drop_nested_duplicates``.
    """
    items = _drop_nested_duplicates(items)
    kept: list[dict] = []
    for item in items:
        bbox = item.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            kept.append(item)
            continue
        try:
            box = [float(value) for value in bbox]
        except (TypeError, ValueError):
            kept.append(item)
            continue
        label = str(item.get("label") or item.get("category") or "")
        duplicate = False
        for previous in kept:
            previous_box = previous.get("bbox")
            previous_label = str(previous.get("label") or previous.get("category") or "")
            if (
                isinstance(previous_box, (list, tuple))
                and len(previous_box) == 4
                and previous_label == label
                and _iou(box, [float(value) for value in previous_box]) >= iou_threshold
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept
