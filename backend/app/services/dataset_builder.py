"""Dataset builder: annotazioni → dataset JSONL ms-swift (3 famiglie).

Regole (da AGENTS.md §7):
- split TRAIN/VAL **per pagina** (deterministico, seed fisso)
- coordinate bbox normalizzate a [0, 1000] per asse (§2.4)
- formato riga JSONL ms-swift (§2.2), percorsi immagine ASSOLUTI
- famiglie: layout (pagina intera), text_rec (ritagli con trascrizione),
  table (ritagli con OTSL), formula (opzionale)
"""
from __future__ import annotations

import json
import hashlib
import random
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import labeling
from . import otsl as otslmod
from . import pages as pagesvc
from .model_adapters import get_adapter
from .i18n import msg
from .. import config
from ..db import connect

LAYOUT_PROMPT = "Please output the categories and coordinates of the document elements in reading order."
TEXT_PROMPT = "Please output the text content from the image."
TABLE_PROMPT = "Please extract the table from the image and represent it in OTSL format."
FORMULA_PROMPT = (
    "Please write out the expression of the formula in the image using LaTeX format."
)

_LABEL_INFO = {l.name: l for l in labeling.DEFAULT_LABELS}


def _sample(image_path: str, prompt: str, content: str) -> dict:
    return {
        "messages": [
            {"role": "user", "content": f"<image>{prompt}"},
            {"role": "assistant", "content": content},
        ],
        "images": [image_path],
    }


def _clamp(value: float, lo: float, hi: float) -> int:
    return int(round(max(lo, min(value, hi))))


def _normalize_bbox(points: list, page_w: int, page_h: int) -> list[int]:
    """bbox [x1,y1,x2,y2] in pixel pagina → scala 0..1000."""
    if page_w <= 0 or page_h <= 0:
        raise ValueError(f"dimensioni pagina non valide: {page_w}x{page_h}")
    if len(points) < 2:
        raise ValueError("bbox senza punti sufficienti")
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    bbox = [
        _clamp(min(xs) / page_w * 1000, 0, 1000),
        _clamp(min(ys) / page_h * 1000, 0, 1000),
        _clamp(max(xs) / page_w * 1000, 0, 1000),
        _clamp(max(ys) / page_h * 1000, 0, 1000),
    ]
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError(f"bbox degenerata dopo normalizzazione: {bbox}")
    return bbox


def parse_points(raw: str) -> list:
    try:
        points = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return points if isinstance(points, list) else []


def _page_image_path(
    page: sqlite3.Row, crops_dir: Path, warnings: list[str], lang: str = "it"
) -> str:
    """Immagine della pagina intera (renderizza il PDF se serve)."""
    src = Path(page["abs_path"])
    if page["source_kind"] == "image" and src.exists():
        return str(src)
    rendered = pagesvc.load_source_image(page)
    if rendered is None:
        warnings.append(msg("page_image_unavailable_w", lang, id=page["id"]))
        return ""
    out = crops_dir / f"page_{page['id']}.png"
    rendered.convert("RGB").save(out, "PNG")
    return str(out)


def _write_crop(
    page: sqlite3.Row,
    block_id: int,
    points: list,
    crops_dir: Path,
    warnings: list[str],
    lang: str = "it",
) -> str:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
    # Il digest lega il file alla geometria corrente e alla sorgente. In questo
    # modo una modifica dell'annotazione non può riusare silenziosamente un
    # crop generato per una bbox precedente.
    source = Path(page["abs_path"])
    try:
        stat = source.stat()
        source_fingerprint = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        source_fingerprint = str(source)
    fingerprint = json.dumps(
        {"page": page["id"], "block": block_id, "bbox": bbox, "source": source_fingerprint},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(fingerprint).hexdigest()[:16]
    out = crops_dir / f"crop_{block_id}_{digest}.jpg"
    if out.exists():
        return str(out)
    try:
        data = pagesvc.crop_block_jpeg(page, bbox)
    except Exception as exc:  # noqa: BLE001
        warnings.append(msg("crop_failed", lang, id=block_id, exc=exc))
        return ""
    out.write_bytes(data)
    return str(out)


def table_band_windows(
    grid: dict, rows_per_band: int = 15, overlap: int = 2
) -> list[tuple[int, int]]:
    """Finestre di righe logiche che non tagliano mai un rowspan."""
    rows = int(grid.get("rows", 0))
    if rows <= rows_per_band or rows_per_band < 1:
        return []
    unsafe = {
        boundary
        for cell in grid.get("cells", [])
        for boundary in range(
            int(cell.get("r", 0)) + 1,
            int(cell.get("r", 0)) + max(1, int(cell.get("rowspan", 1))),
        )
    }
    safe = [boundary for boundary in range(rows + 1) if boundary not in unsafe]
    windows: list[tuple[int, int]] = []
    start = 0
    while start < rows:
        goal = min(rows, start + rows_per_band)
        before = [boundary for boundary in safe if start < boundary <= goal]
        after = [boundary for boundary in safe if boundary > goal]
        end = max(before) if before else (min(after) if after else rows)
        if end <= start:
            break
        windows.append((start, end))
        if end >= rows:
            break
        target = max(start + 1, end - max(0, overlap))
        candidates = [boundary for boundary in safe if start < boundary < end]
        start = min(candidates, key=lambda value: abs(value - target)) if candidates else end
    return windows


def _slice_table_grid(grid: dict, start: int, end: int) -> dict:
    """Ritaglia righe logiche; i boundary sono già scelti fuori dai merge."""
    cells = []
    for cell in grid.get("cells", []):
        row = int(cell.get("r", 0))
        rowspan = max(1, int(cell.get("rowspan", 1)))
        if row < start or row + rowspan > end:
            continue
        cells.append({**cell, "r": row - start})
    hlines = [float(value) for value in grid.get("hlines", [])]
    local_hlines: list[float] = []
    if len(hlines) == int(grid.get("rows", 0)) + 1:
        selected = hlines[start : end + 1]
        span = selected[-1] - selected[0]
        if span > 0:
            local_hlines = [(value - selected[0]) / span for value in selected]
    return {
        "rows": end - start,
        "cols": int(grid.get("cols", 0)),
        "cells": cells,
        "phantom_cols": list(grid.get("phantom_cols", [])),
        "header_rows": max(0, min(int(grid.get("header_rows", 0) or 0) - start, end - start)),
        "vlines": list(grid.get("vlines", [])),
        "hlines": local_hlines,
    }


def _write_table_band_crop(
    page: sqlite3.Row,
    block_id: int,
    points: list,
    hlines: list[float],
    start: int,
    end: int,
    crops_dir: Path,
    warnings: list[str],
    lang: str,
) -> str:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
    try:
        table_image = pagesvc.crop_block_image(page, bbox)
        top = max(0, min(table_image.height, round(hlines[start] * table_image.height)))
        bottom = max(0, min(table_image.height, round(hlines[end] * table_image.height)))
        if bottom <= top:
            raise ValueError(f"banda vuota {start}:{end}")
        band = table_image.crop((0, top, table_image.width, bottom)).convert("RGB")
        source = Path(page["abs_path"])
        try:
            stat = source.stat()
            source_mark = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
        except OSError:
            source_mark = str(source)
        digest = hashlib.sha256(
            f"{source_mark}:{block_id}:{bbox}:{start}:{end}:{top}:{bottom}".encode()
        ).hexdigest()[:16]
        out = crops_dir / f"table_{block_id}_rows_{start}_{end}_{digest}.jpg"
        if not out.exists():
            band.save(out, "JPEG", quality=95, subsampling=0)
        return str(out)
    except Exception as exc:  # noqa: BLE001
        warnings.append(msg("table_band_failed", lang, id=block_id, exc=exc))
        return ""


def _project_dir(project_id: int) -> Path:
    return config.DATA_DIR / str(project_id)


def collect_pages_with_blocks(project_id: int) -> dict[int, dict]:
    """Raccoglie pagine con blocchi: {page_id: {"page", "blocks", "tables"}}."""
    page_data: dict[int, dict] = {}
    with connect() as conn:
        pages = conn.execute(
            "SELECT * FROM pages WHERE project_id=? ORDER BY id", (project_id,)
        ).fetchall()
        for page in pages:
            blocks = conn.execute(
                "SELECT * FROM blocks WHERE page_id=? "
                "ORDER BY COALESCE(order_idx, 2147483647), id",
                (page["id"],),
            ).fetchall()
            if not blocks:
                continue
            tables: dict[int, dict] = {}
            if blocks:
                placeholders = ",".join("?" for _ in blocks)
                for row in conn.execute(
                    "SELECT * FROM tables WHERE block_id IN (" + placeholders + ")",
                    [b["id"] for b in blocks],
                ).fetchall():
                    try:
                        tables[row["block_id"]] = json.loads(row["grid_json"] or "{}")
                    except (TypeError, ValueError):
                        tables[row["block_id"]] = {}
            page_data[page["id"]] = {"page": page, "blocks": blocks, "tables": tables}
    return page_data


def gold_page_ids(project_id: int) -> set[int]:
    """Pagine protette dal protocollo: non entrano accidentalmente nel tuning."""
    with connect() as conn:
        row = conn.execute("SELECT settings_json FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return set()
    try:
        settings = json.loads(row["settings_json"] or "{}")
        return {int(value) for value in settings.get("study_protocol", {}).get("gold_pages", [])}
    except (TypeError, ValueError, KeyError):
        return set()


def pilot_page_ids(project_id: int) -> set[int]:
    """Restituisce il campione pilot salvato nel protocollo di studio."""
    with connect() as conn:
        row = conn.execute("SELECT settings_json FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        return set()
    try:
        settings = json.loads(row["settings_json"] or "{}")
        values = settings.get("study_protocol", {}).get("pilot_pages", [])
        return {int(value) for value in values}
    except (TypeError, ValueError, KeyError):
        return set()


def compute_split(
    project_id: int, split_ratio: float = 0.90, seed: int = 42, strategy: str = "page", approved_only: bool = False,
    page_filter: set[int] | None = None,
) -> tuple[set[int], set[int]]:
    """Split deterministico per pagina o gruppo editoriale.

    ``issue`` raggruppa per numero/data dell'edizione, ``year`` per annata e
    ``source`` per file sorgente. Il fallback a pagina evita di bloccare
    l'importazione quando i metadati non sono ancora compilati.
    """
    page_data = collect_pages_with_blocks(project_id)
    if page_filter is not None:
        page_data = {pid: data for pid, data in page_data.items() if pid in page_filter}
    protected_gold = gold_page_ids(project_id)
    page_data = {pid: data for pid, data in page_data.items() if pid not in protected_gold}
    if approved_only:
        page_data = {pid: data for pid, data in page_data.items() if data["page"]["status"] in {"approved", "exported"}}
    rng = random.Random(seed)
    page_ids = sorted(page_data)
    if not page_ids:
        return set(), set()

    def group_key(data: dict) -> str:
        page = data["page"]
        if strategy == "issue":
            return str(page["issue_no"] or page["issue_date"] or page["rel_path"])
        if strategy == "year":
            return str(page["issue_date"] or "")[:4] or str(page["rel_path"])
        if strategy == "source":
            return str(page["abs_path"])
        if strategy == "page_type":
            return str(page["page_type"] or "unclassified")
        if strategy in {"scanner", "collection"}:
            try:
                metadata = json.loads(page["meta_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            return str(metadata.get(strategy) or page["abs_path"])
        return str(page["id"])

    groups: dict[str, list[int]] = {}
    for pid in page_ids:
        groups.setdefault(group_key(page_data[pid]), []).append(pid)
    group_ids = sorted(groups)
    rng.shuffle(group_ids)
    if len(group_ids) == 1:
        # Con un solo gruppo non è possibile promettere una validation
        # indipendente; manteniamo comunque il comportamento storico.
        shuffled = [pid for gid in group_ids for pid in groups[gid]]
        n_train = max(1, min(len(shuffled) - 1, int(len(shuffled) * split_ratio))) if len(shuffled) > 1 else 1
        return set(shuffled[:n_train]), set(shuffled[n_train:])
    n_train = max(1, min(len(group_ids) - 1, int(len(group_ids) * split_ratio)))
    train_groups = set(group_ids[:n_train])
    return (
        {pid for gid, ids in groups.items() if gid in train_groups for pid in ids},
        {pid for gid, ids in groups.items() if gid not in train_groups for pid in ids},
    )


def build_datasets(
    project_id: int,
    split_ratio: float = 0.90,
    seed: int = 42,
    split_strategy: str = "page",
    adapter_id: str = "monkeyocrv2-parsing",
    approved_only: bool = False,
    pilot_only: bool = False,
    table_band_rows: int = 15,
    table_band_overlap: int = 2,
    include_full_tables: bool = True,
    lang: str = "it",
) -> dict:
    """Costruisce e scrive su disco il dataset del progetto. Ritorna il report."""
    warnings: list[str] = []
    try:
        adapter = get_adapter(adapter_id)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    base_dir = _project_dir(project_id)
    dataset_dir = base_dir / "dataset"
    crops_dir = base_dir / "crops"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)

    # --- raccolta dati ------------------------------------------------------
    page_data = collect_pages_with_blocks(project_id)
    pilot_ids = pilot_page_ids(project_id) if pilot_only else set()
    if pilot_only:
        if not pilot_ids:
            raise ValueError("nessun campione pilot salvato nel protocollo")
        page_data = {pid: data for pid, data in page_data.items() if pid in pilot_ids}
    protected_gold = gold_page_ids(project_id)
    page_data = {pid: data for pid, data in page_data.items() if pid not in protected_gold}
    if protected_gold:
        warnings.append(msg("gold_excluded", lang, n=len(protected_gold)))
    if approved_only:
        page_data = {
            pid: data
            for pid, data in page_data.items()
            if data["page"]["status"] in {"approved", "exported"}
        }
    pages = [d["page"] for d in page_data.values()]
    with connect() as conn:
        total_pages = conn.execute(
            "SELECT COUNT(*) AS n FROM pages WHERE project_id=?", (project_id,)
        ).fetchone()["n"]

    # --- split per pagina ----------------------------------------------------
    train_ids, val_ids = compute_split(
        project_id, split_ratio, seed, split_strategy, approved_only=approved_only,
        page_filter=pilot_ids if pilot_only else None,
    )
    page_ids = sorted(page_data)

    # --- famiglie -------------------------------------------------------------
    families: dict[str, dict[str, list[dict]]] = {
        name: {"train": [], "val": []}
        for name in ("layout", "text_rec", "table", "formula")
    }
    crops_generated = 0
    table_variants = {"full": 0, "bands": 0, "without_boundaries": 0}
    # Provenienza del testo nelle celle esportate: il prefill non verificato è
    # la parte del dataset che un umano non ha ancora guardato, e va dichiarata.
    table_cells = {"total": 0, "verified": 0, "prefill_unverified": 0}

    for pid in page_ids:
        split = "train" if pid in train_ids else "val"
        data = page_data[pid]
        page = data["page"]

        # LAYOUT: un campione per pagina, ordine di lettura
        layout_items = []
        for block in data["blocks"]:
            points = parse_points(block["points"])
            if not points:
                warnings.append(
                    msg("points_missing", lang, id=block["id"])
                )
                continue
            try:
                bbox = _normalize_bbox(points, page["width"], page["height"])
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                warnings.append(msg("bbox_invalid", lang, id=block["id"], exc=exc))
                continue
            layout_items.append({"bbox": bbox, "label": block["label"]})
        if layout_items:
            img = _page_image_path(page, crops_dir, warnings, lang=lang)
            if img:
                content = json.dumps(
                    layout_items, ensure_ascii=False, separators=(",", ":")
                )
                families["layout"][split].append(_sample(img, adapter.prompt_for("layout") or LAYOUT_PROMPT, content))

        # RITAGLI: text_rec / table / formula
        for block in data["blocks"]:
            label = block["label"]
            info = _LABEL_INFO.get(label)
            points = parse_points(block["points"])
            if not points:
                continue

            if info is not None and info.prompt_kind == "text":
                content = (block["content"] or "").strip()
                if not content:
                    warnings.append(
                        msg("empty_transcription_skip", lang, id=block["id"], label=label)
                    )
                    continue
                img = _write_crop(page, block["id"], points, crops_dir, warnings, lang=lang)
                if img:
                    families["text_rec"][split].append(_sample(img, adapter.prompt_for("text", label) or TEXT_PROMPT, content))
                    crops_generated += 1

            elif label == "Table":
                grid = data["tables"].get(block["id"])
                if not grid or not grid.get("cells"):
                    warnings.append(
                        msg("table_grid_missing_skip", lang, id=block["id"])
                    )
                    continue
                try:
                    otsl_str = otslmod.grid_to_otsl(grid)
                except ValueError as exc:
                    warnings.append(msg("table_error", lang, id=block["id"], exc=exc))
                    continue
                # Conteggio per il report, con avviso quando il testo proposto
                # dall'OCR/modello non è ancora passato dall'annotatore.
                text_cells = [
                    c for c in grid.get("cells", []) if (c.get("text") or "").strip()
                ]
                unverified = [c for c in text_cells if c.get("verified") is False]
                table_cells["total"] += len(text_cells)
                table_cells["verified"] += len(text_cells) - len(unverified)
                table_cells["prefill_unverified"] += len(unverified)
                if unverified:
                    warnings.append(
                        msg(
                            "table_unverified_cells",
                            lang,
                            id=block["id"],
                            n=len(unverified),
                            total=len(text_cells),
                        )
                    )
                if include_full_tables:
                    img = _write_crop(page, block["id"], points, crops_dir, warnings, lang=lang)
                    if img:
                        families["table"][split].append(_sample(img, adapter.prompt_for("table", label) or TABLE_PROMPT, otsl_str))
                        crops_generated += 1
                        table_variants["full"] += 1

                windows = table_band_windows(
                    grid, rows_per_band=table_band_rows, overlap=table_band_overlap
                )
                hlines = [float(value) for value in grid.get("hlines", [])]
                if windows and len(hlines) != int(grid.get("rows", 0)) + 1:
                    table_variants["without_boundaries"] += 1
                    warnings.append(msg("table_band_no_boundaries", lang, id=block["id"]))
                    windows = []
                for start, end in windows:
                    band_grid = _slice_table_grid(grid, start, end)
                    if not band_grid["cells"]:
                        continue
                    try:
                        band_otsl = otslmod.grid_to_otsl(band_grid)
                    except ValueError as exc:
                        warnings.append(msg("table_error", lang, id=block["id"], exc=exc))
                        continue
                    band_img = _write_table_band_crop(
                        page, block["id"], points, hlines, start, end,
                        crops_dir, warnings, lang,
                    )
                    if band_img:
                        families["table"][split].append(
                            _sample(
                                band_img,
                                adapter.prompt_for("table", label) or TABLE_PROMPT,
                                band_otsl,
                            )
                        )
                        crops_generated += 1
                        table_variants["bands"] += 1

            elif label == "Formula":
                content = (block["content"] or "").strip()
                if not content:
                    warnings.append(msg("formula_empty", lang, id=block["id"]))
                    continue
                if not content.startswith("$"):
                    content = f"${content}$"
                img = _write_crop(page, block["id"], points, crops_dir, warnings, lang=lang)
                if img:
                    families["formula"][split].append(_sample(img, adapter.prompt_for("formula", label) or FORMULA_PROMPT, content))
                    crops_generated += 1

    # --- scrittura atomica (un file per famiglia e per split) -----------------
    # Tutti i contenuti vengono completati e controllati prima di sostituire i
    # file pubblici. Un crash non lascia un dataset misto tra due build.
    staging_dir = Path(tempfile.mkdtemp(prefix="dataset-", dir=str(dataset_dir)))
    counts: dict[str, dict[str, int]] = {}
    sample_lines: dict[str, list[str]] = {}
    files_meta: list[dict] = []
    staged_files: list[tuple[Path, Path]] = []
    for family, splits in families.items():
        counts[family] = {s: len(lines) for s, lines in splits.items()}
        sample_lines[family] = []
        for split in ("train", "val"):
            lines = splits[split]
            filename = f"{family}_{split}.jsonl"
            staging_path = staging_dir / filename
            with staging_path.open("w", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            out_path = dataset_dir / filename
            staged_files.append((staging_path, out_path))
            files_meta.append(
                {"path": str(out_path), "size": staging_path.stat().st_size, "lines": len(lines)}
            )
            for line in lines[:1]:
                sample_lines[family].append(json.dumps(line, ensure_ascii=False))

    snapshot_id = datetime.now(timezone.utc).strftime("v%Y%m%dT%H%M%S%fZ")
    report = _report(
        project_id,
        split_ratio,
        seed,
        counts,
        crops_generated,
        warnings,
        dataset_dir,
        files=files_meta,
        sample_lines=sample_lines,
        pages=total_pages,
        pages_with_blocks=len(page_ids),
        train_pages=len(train_ids),
        val_pages=len(val_ids),
        split_strategy=split_strategy,
        snapshot_id=snapshot_id,
        adapter_id=adapter_id,
        approved_only=approved_only,
        pilot_only=pilot_only,
        pilot_pages=len(pilot_ids),
        table_variants=table_variants,
        table_cells=table_cells,
        table_band_rows=table_band_rows,
        table_band_overlap=table_band_overlap,
        include_full_tables=include_full_tables,
    )
    report_staging = staging_dir / "report.json"
    report_staging.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for staging_path, out_path in staged_files:
        staging_path.replace(out_path)
    report_staging.replace(dataset_dir / "report.json")
    snapshot_dir = dataset_dir / "snapshots" / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    for _staging_path, out_path in staged_files:
        shutil.copy2(out_path, snapshot_dir / out_path.name)
    shutil.copy2(dataset_dir / "report.json", snapshot_dir / "report.json")
    report["snapshot_dir"] = str(snapshot_dir)
    # Aggiorna il report pubblico con il percorso completo dello snapshot dopo
    # averlo creato; i file JSONL restano già atomici e disponibili.
    (dataset_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (snapshot_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.rmtree(staging_dir, ignore_errors=True)
    return report


def _report(
    project_id: int,
    split_ratio: float,
    seed: int,
    counts: dict,
    crops: int,
    warnings: list[str],
    dataset_dir: Path,
    files: list | None = None,
    sample_lines: dict | None = None,
    pages: int = 0,
    pages_with_blocks: int = 0,
    train_pages: int = 0,
    val_pages: int = 0,
    split_strategy: str = "page",
    snapshot_id: str | None = None,
    adapter_id: str = "monkeyocrv2-parsing",
    approved_only: bool = False,
    pilot_only: bool = False,
    pilot_pages: int = 0,
    table_variants: dict | None = None,
    table_cells: dict | None = None,
    table_band_rows: int = 15,
    table_band_overlap: int = 2,
    include_full_tables: bool = True,
) -> dict:
    return {
        "project_id": project_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "split": {"ratio": split_ratio, "seed": seed, "strategy": split_strategy},
        "pages": {
            "total": pages,
            "with_blocks": pages_with_blocks,
            "train": train_pages,
            "val": val_pages,
        },
        "counts": counts,
        "crops_generated": crops,
        "warnings": warnings,
        "files": files or [],
        "sample_lines": sample_lines or {},
        "dataset_dir": str(dataset_dir),
        "snapshot_id": snapshot_id,
        "adapter_id": adapter_id,
        "approved_only": approved_only,
        "pilot_only": pilot_only,
        "pilot_pages": pilot_pages,
        "table_variants": table_variants or {"full": 0, "bands": 0, "without_boundaries": 0},
        "table_cells": table_cells or {"total": 0, "verified": 0, "prefill_unverified": 0},
        "table_strategy": {
            "experimental": True,
            "include_full": include_full_tables,
            "band_rows": table_band_rows,
            "overlap": table_band_overlap,
            "requires_verified_hlines": True,
        },
    }
