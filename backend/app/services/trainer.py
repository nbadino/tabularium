"""Orchestrazione training (ms-swift).

Gestisce il ciclo di vita dei run: start, monitor, stop, resume.
Delega la generazione script a `trainer_script` e la telemetria a `trainer_metrics`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .. import config
from .dataset_builder import _project_dir
from . import vram
from .trainer_metrics import _log_tail, _metrics, gpu_snapshot, parse_metrics_line
from .trainer_script import (
    FAMILIES,
    _dataset_source,
    _now,
    generate_script,
    prepare_training_files,
)
from .i18n import msg

# Re-export per compatibilità con chi importava da trainer prima dello split.
__all__ = [
    "gpu_snapshot",
    "parse_metrics_line",
    "generate_script",
    "prepare_training_files",
    "start_run",
    "stop_run",
    "runs_dir",
    "preflight",
]


def runs_dir(project_id: int) -> Path:
    return _project_dir(project_id) / "runs"


def preflight(project_id: int, cfg: dict | None = None, lang: str = "it") -> dict:
    """Controlli non distruttivi prima di avviare un run.

    Restituisce errori bloccanti e avvisi leggibili dalla UI, senza importare
    PyTorch né avviare processi di training.
    """
    cfg = cfg or {}
    dataset_dir = _project_dir(project_id) / "dataset"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        dataset_source, snapshot_id = _dataset_source(project_id)
    except FileNotFoundError:
        dataset_source, snapshot_id = dataset_dir, None

    family_counts: dict[str, dict[str, int]] = {}
    counts = {"train.jsonl": 0, "val.jsonl": 0}
    found_any = False
    for family in FAMILIES:
        family_counts[family] = {}
        for split in ("train", "val"):
            name = f"{family}_{split}.jsonl"
            path = dataset_source / name
            value = 0
            if path.exists():
                found_any = True
                try:
                    value = sum(
                        1
                        for line in path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    )
                except OSError as exc:
                    errors.append(msg("unreadable_train", lang, name=name, exc=exc))
            family_counts[family][split] = value
            counts[f"{split}.jsonl"] += value
    if not found_any:
        errors.append(msg("missing_train", lang, name="dataset snapshot"))
    if counts["train.jsonl"] < 2:
        errors.append(msg("min_train", lang))
    if counts["val.jsonl"] < 1:
        errors.append(msg("empty_val", lang))
    if family_counts["table"]["train"] == 0:
        warnings.append(msg("no_table_train_w", lang))

    repo_train = Path(config.TRAIN_REPO) / "parsing" / "train" if config.TRAIN_REPO else None
    if not config.TRAIN_REPO:
        errors.append(msg("repo_not_configured", lang))
    elif not repo_train.is_dir():
        errors.append(msg("repo_invalid", lang, path=repo_train))
    if config.TRAIN_PYTHON:
        if not Path(config.TRAIN_PYTHON).exists():
            errors.append(msg("python_not_found", lang, path=config.TRAIN_PYTHON))
    elif not shutil.which("conda"):
        errors.append(msg("conda_not_found", lang))
    gpus = gpu_snapshot()
    if not gpus:
        errors.append(msg("no_gpu_w", lang))
    requested = str(cfg.get("gpus", "0"))
    if gpus and requested:
        available = {str(g["index"]) for g in gpus}
        requested_ids = {x.strip() for x in requested.split(",") if x.strip()}
        missing = sorted(requested_ids - available)
        if missing:
            errors.append(msg("gpu_missing", lang, list=", ".join(missing)))
        for gpu in gpus:
            if str(gpu["index"]) not in requested_ids:
                continue
            free_mb = gpu["memory_total"] - gpu["memory_used"]
            gpu["memory_free"] = free_mb
            if free_mb < 2048:
                errors.append(
                    msg(
                        "gpu_busy",
                        lang,
                        index=gpu["index"],
                        free=f"{free_mb / 1024:.1f}",
                        total=f"{gpu['memory_total'] / 1024:.1f}",
                    )
                )
            elif free_mb < 6144:
                warnings.append(
                    msg(
                        "gpu_low_vram_w",
                        lang,
                        index=gpu["index"],
                        free=f"{free_mb / 1024:.1f}",
                    )
                )

            # La VRAM libera da sola non dice niente: quello che conta è se la
            # *configurazione* ci sta. Il preset ufficiale (batch 4, 16384
            # token) chiede ~26 GB perché i logit sono B×S×151936×2 byte, e su
            # una scheda da 8 GB va in OOM dopo aver scaricato i pesi e
            # costruito il dataset. Meglio saperlo adesso.
            need = vram.estimate(cfg, model_path=str(cfg.get("model_path") or ""))
            gpu["vram_estimate"] = need.to_dict()
            if not vram.fits(need.total_mib, free_mb):
                suggested = vram.largest_fitting_length(
                    cfg, free_mb, model_path=str(cfg.get("model_path") or "")
                )
                if suggested <= 0:
                    warnings.append(
                        msg(
                            "vram_no_length_fits_w",
                            lang,
                            index=gpu["index"],
                            free=f"{free_mb / 1024:.1f}",
                        )
                    )
                else:
                    errors.append(
                        msg(
                            "vram_too_small",
                            lang,
                            index=gpu["index"],
                            need=f"{need.total_mib / 1024:.1f}",
                            free=f"{free_mb / 1024:.1f}",
                            logits=f"{need.terms['logits'] / 1024:.1f}",
                            vocab=need.assumptions["vocab_size"],
                            suggest=suggested,
                        )
                    )
            elif need.total_mib * 1.5 > free_mb:
                warnings.append(
                    msg(
                        "vram_tight_w",
                        lang,
                        index=gpu["index"],
                        need=f"{need.total_mib / 1024:.1f}",
                        free=f"{free_mb / 1024:.1f}",
                    )
                )
    output_dir = Path(str(cfg.get("output_dir") or _project_dir(project_id) / "runs"))
    try:
        free_gb = shutil.disk_usage(output_dir.parent if output_dir.parent.exists() else _project_dir(project_id)).free / (1024**3)
        if free_gb < 5:
            errors.append(msg("disk_blocking", lang, gb=f"{free_gb:.1f}"))
        elif free_gb < 20:
            warnings.append(msg("low_disk", lang, gb=f"{free_gb:.1f}"))
    except OSError:
        warnings.append(msg("disk_unknown_w", lang))
    checkpoints = list(output_dir.glob("checkpoint-*")) if output_dir.exists() else []
    return {
        "ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "dataset": {
            "dir": str(dataset_source),
            "snapshot_id": snapshot_id,
            "counts": counts,
            "families": family_counts,
        },
        "training_repo": str(repo_train) if repo_train else None,
        "python": config.TRAIN_PYTHON or config.TRAIN_ENV,
        "gpus": gpus,
        "output_dir": str(output_dir),
        "existing_checkpoints": [str(path) for path in sorted(checkpoints)],
    }


def _run_paths(project_id: int, run_id: str) -> tuple[Path, Path, Path]:
    run_dir = runs_dir(project_id) / run_id
    return run_dir, run_dir / "train.log", run_dir / "metrics.jsonl"


def _read_run_meta(run_dir: Path) -> dict | None:
    meta_path = run_dir / "run.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (TypeError, ValueError):
        return None


# --- gestione run --------------------------------------------------------------
_ACTIVE: dict[int, dict] = {}


def start_run(project_id: int, cfg: dict) -> dict:
    check = preflight(project_id, cfg)
    if not check["ready"]:
        raise ValueError("; ".join(check["errors"]))
    run_dir = runs_dir(project_id) / f"run_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)
    train_file, val_file = prepare_training_files(project_id, run_dir / "dataset")

    script = generate_script(cfg, run_dir, train_file, val_file)
    (run_dir / "train.sh").write_text(script, encoding="utf-8")

    run_id = run_dir.name
    snapshot_id = check["dataset"].get("snapshot_id")
    meta = {
        "run_id": run_id,
        "state": "running",
        "started_at": _now(),
        "config": cfg,
        "dataset_snapshot_id": snapshot_id,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    log_file = run_dir / "train.log"
    try:
        proc = subprocess.Popen(
            ["bash", str(run_dir / "train.sh")],
            stdout=log_file.open("wb"),
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:  # noqa: BLE001
        meta["state"] = "error"
        meta["error"] = str(exc)
        (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    _ACTIVE[project_id] = {"run_id": run_id, "proc": proc, "run_dir": run_dir, "log_file": log_file}
    threading.Thread(target=_metrics_writer, args=(project_id,), daemon=True).start()
    threading.Thread(target=_monitor, args=(project_id,), daemon=True).start()
    return _status(project_id)


def _metrics_writer(project_id: int) -> None:
    """Materializza metriche JSONL durante il run per replay e SSE."""
    handle = _ACTIVE.get(project_id)
    if not handle:
        return
    log_file = handle["log_file"]
    metrics_file = handle["run_dir"] / "metrics.jsonl"
    offset = 0
    seen: set[str] = set()
    while True:
        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(offset)
                lines = fh.readlines()
                offset = fh.tell()
            if lines:
                with metrics_file.open("a", encoding="utf-8") as out:
                    for line in lines:
                        metric = parse_metrics_line(line)
                        if not metric:
                            continue
                        key = json.dumps({k: metric[k] for k in metric if k != "t"}, sort_keys=True)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.write(json.dumps(metric, ensure_ascii=False) + "\n")
            proc = handle["proc"]
            if proc.poll() is not None:
                break
        except (OSError, ValueError):
            break
        time.sleep(1.0)


def _monitor(project_id: int) -> None:
    handle = _ACTIVE.get(project_id)
    if not handle:
        return
    proc, run_dir = handle["proc"], handle["run_dir"]
    code = proc.wait()
    state = "finished" if code == 0 else "failed"
    meta = _read_run_meta(run_dir) or {}
    meta["state"] = state
    meta["ended_at"] = _now()
    meta["exit_code"] = code
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _ACTIVE.pop(project_id, None)


def _status(project_id: int) -> dict:
    handle = _ACTIVE.get(project_id)
    if handle:
        run_dir = handle["run_dir"]
        meta = _read_run_meta(run_dir) or {"run_id": handle["run_id"], "state": "running"}
        tail = _log_tail(handle["log_file"])
        metrics = _metrics(run_dir)
        return {
            "active": True,
            "run": meta,
            "log_tail": tail,
            "metrics": metrics,
            "gpu": gpu_snapshot(),
        }

    rd = runs_dir(project_id)
    if rd.exists():
        dirs = sorted([d for d in rd.iterdir() if (d / "run.json").exists()], key=lambda d: d.name, reverse=True)
        if dirs:
            run_dir = dirs[0]
            meta = _read_run_meta(run_dir) or {"state": "unknown"}
            return {
                "active": False,
                "run": meta,
                "log_tail": _log_tail(run_dir / "train.log"),
                "metrics": _metrics(run_dir),
                "gpu": gpu_snapshot(),
            }
    return {"active": False, "run": None, "log_tail": "", "metrics": [], "gpu": gpu_snapshot()}


def stop_run(project_id: int) -> dict:
    handle = _ACTIVE.get(project_id)
    if not handle:
        return _status(project_id)
    proc = handle["proc"]
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    meta = _read_run_meta(handle["run_dir"]) or {"state": "stopped"}
    meta["state"] = "stopped"
    meta["ended_at"] = _now()
    (handle["run_dir"] / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _ACTIVE.pop(project_id, None)
    return _status(project_id)
