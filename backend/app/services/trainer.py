"""Orchestrazione training (ms-swift).

Gestisce il ciclo di vita dei run: start, monitor, stop, resume.
Delega la generazione script a `trainer_script` e la telemetria a `trainer_metrics`.
"""
from __future__ import annotations

import json
import os
import signal
import shutil
import sqlite3
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
from .training_executor import RemoteProcess, SshExecutor, TrainingRecipe, executor_from_config
from .artifacts import write_manifest, verify_manifest
from .i18n import msg
from ..db import connect

REMOTE_PROVIDERS = {"ssh", "vast", "runpod"}

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
    "TrainingRecipe",
    "executor_from_config",
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


def start_run(project_id: int, cfg: dict, *, owner_id: int | None = None) -> dict:
    check = preflight(project_id, cfg)
    if not check["ready"]:
        raise ValueError("; ".join(check["errors"]))
    run_dir = runs_dir(project_id) / f"run_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    train_file, val_file = prepare_training_files(project_id, run_dir / "dataset")

    resume_run_id = str(cfg.get("resume_run_id") or "").strip()
    if resume_run_id:
        # L'ID è un nome di directory generato dal backend: non consentire
        # percorsi arbitrari o checkpoint provenienti da un altro progetto.
        previous_dir = (runs_dir(project_id) / resume_run_id).resolve()
        runs_root = runs_dir(project_id).resolve()
        if previous_dir.parent != runs_root or not previous_dir.is_dir():
            raise ValueError("run di resume non valida per questo progetto")
        previous_checkpoints = previous_dir / "checkpoints"
        if not previous_checkpoints.is_dir() or not any(path.is_file() for path in previous_checkpoints.rglob("*")):
            raise ValueError("la run di resume non contiene checkpoint")
        shutil.copytree(previous_checkpoints, run_dir / "checkpoints", dirs_exist_ok=True)

    script = generate_script(cfg, run_dir, train_file, val_file)
    script_path = run_dir / "train.sh"
    script_path.write_text(script, encoding="utf-8")

    run_id = run_dir.name
    snapshot_id = check["dataset"].get("snapshot_id")
    executor = executor_from_config(cfg, known_hosts=config.SSH_KNOWN_HOSTS)
    meta = {
        "run_id": run_id,
        "state": "running",
        "started_at": _now(),
        "config": cfg,
        "dataset_snapshot_id": snapshot_id,
    }
    if resume_run_id:
        meta["resumed_from"] = resume_run_id
    recipe = TrainingRecipe(
        run_id=run_id,
        run_dir=run_dir,
        script=script_path,
        train_dataset=train_file,
        val_dataset=val_file,
        dataset_snapshot_id=snapshot_id,
        config=cfg,
        provider=executor.provider,
    )
    recipe.write_manifest()
    meta["recipe"] = recipe.manifest()
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    log_file = run_dir / "train.log"
    try:
        proc = executor.launch(recipe, log_file)
    except Exception as exc:  # noqa: BLE001
        meta["state"] = "error"
        meta["error"] = str(exc)
        (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    with connect() as conn:
        job = conn.execute(
            "INSERT INTO jobs(kind, owner_id, project_id, provider, pid, process_group, remote_job_id, state, "
            "heartbeat_at, command_json, log_path, recovery_strategy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("training", owner_id, project_id, executor.provider,
             proc.pid if executor.provider == "local" else None,
             proc.pid if executor.provider == "local" else None,
             str(proc.pid) if executor.provider in REMOTE_PROVIDERS else None,
             "running", _now(),
             json.dumps({"run_id": run_id, "script": str(run_dir / "train.sh"), "config": cfg}), str(log_file),
             "reconcile-local-pid" if executor.provider == "local" else "reconcile-ssh-session"),
        )
        meta["job_id"] = job.lastrowid
        if executor.provider == "local":
            meta["pid"] = proc.pid
        else:
            meta["remote_job_id"] = str(proc.pid)
        (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    _ACTIVE[project_id] = {"run_id": run_id, "proc": proc, "run_dir": run_dir, "log_file": log_file, "job_id": job.lastrowid}
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
    artifact_error = None
    if code == 0 and hasattr(proc, "download_artifacts"):
        try:
            result = proc.download_artifacts(run_dir / "checkpoints")
            if not result.get("ok"):
                artifact_error = "; ".join(result.get("errors") or ["checksum artefatti remoto fallito"])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            artifact_error = str(exc)
    if artifact_error:
        state = "failed"
        code = 1
    artifact_manifest = write_manifest(run_dir / "checkpoints")
    artifact_check = verify_manifest(run_dir / "checkpoints", artifact_manifest)
    meta = _read_run_meta(run_dir) or {}
    meta["state"] = state
    meta["ended_at"] = _now()
    meta["exit_code"] = code
    meta["artifacts"] = artifact_check
    if artifact_error:
        meta["error"] = artifact_error
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, ended_at=?, exit_code=?, heartbeat_at=? WHERE id=?",
            (state, meta["ended_at"], code, _now(), meta.get("job_id")),
        )
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

    # Recovery dopo un riavvio del backend: l'handle Python è perso, ma il
    # PID/job manifest persistito resta sufficiente per osservare e fermare la
    # run locale.
    try:
        with connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? AND kind='training' ORDER BY id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
    except sqlite3.OperationalError as exc:
        # Supporta anche una directory dati appena creata prima della prima
        # migrazione: lo status vuoto resta interrogabile e l'avvio normale
        # continua a passare da init_db().
        if "no such table: jobs" not in str(exc):
            raise
        job = None
    if job is not None and job["state"] == "running":
        try:
            command = json.loads(job["command_json"] or "{}")
        except (TypeError, ValueError):
            command = {}
        if job["provider"] in REMOTE_PROVIDERS and job["remote_job_id"]:
            run_dir = runs_dir(project_id) / str(command.get("run_id", ""))
            meta = _read_run_meta(run_dir) or {}
            cfg = command.get("config") or meta.get("config") or {}
            try:
                executor = executor_from_config(cfg, known_hosts=config.SSH_KNOWN_HOSTS)
                if not isinstance(executor, SshExecutor):
                    raise ValueError("executor SSH non disponibile")
                recipe = TrainingRecipe(
                    run_id=str(command.get("run_id", run_dir.name)),
                    run_dir=run_dir,
                    script=run_dir / "train.sh",
                    train_dataset=run_dir / "dataset" / "train.jsonl",
                    val_dataset=run_dir / "dataset" / "val.jsonl",
                    dataset_snapshot_id=meta.get("dataset_snapshot_id"),
                    config=cfg,
                    provider=executor.provider,
                )
                proc = executor.recover(recipe, Path(job["log_path"]), int(job["remote_job_id"]))
                if proc.poll() is None:
                    _ACTIVE[project_id] = {"run_id": recipe.run_id, "proc": proc, "run_dir": run_dir, "log_file": Path(job["log_path"]), "job_id": job["id"]}
                    threading.Thread(target=_metrics_writer, args=(project_id,), daemon=True).start()
                    threading.Thread(target=_monitor, args=(project_id,), daemon=True).start()
                    return {"active": True, "run": meta, "log_tail": _log_tail(Path(job["log_path"])), "metrics": _metrics(run_dir), "gpu": gpu_snapshot(), "recovered": True}
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                pass
            ended = _now()
            with connect() as conn:
                conn.execute("UPDATE jobs SET state='failed', ended_at=?, error=?, heartbeat_at=? WHERE id=?", (ended, "job remoto non più raggiungibile dopo il riavvio", ended, job["id"]))

        alive = False
        if job["pid"]:
            try:
                os.kill(int(job["pid"]), 0)
                alive = True
            except OSError:
                alive = False
        if not alive:
            ended = _now()
            with connect() as conn:
                conn.execute("UPDATE jobs SET state='failed', ended_at=?, error=?, heartbeat_at=? WHERE id=?", (ended, "processo non più presente dopo il riavvio", ended, job["id"]))
        else:
            run_dir = runs_dir(project_id) / str(command.get("run_id", ""))
            meta = _read_run_meta(run_dir) or {"run_id": command.get("run_id"), "state": "running", "pid": job["pid"]}
            return {"active": True, "run": meta, "log_tail": _log_tail(Path(job["log_path"])), "metrics": _metrics(run_dir), "gpu": gpu_snapshot(), "recovered": True}

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
        with connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE project_id=? AND kind='training' AND state='running' ORDER BY id DESC LIMIT 1", (project_id,)).fetchone()
        if job is not None and job["provider"] in REMOTE_PROVIDERS and job["remote_job_id"]:
            try:
                command = json.loads(job["command_json"] or "{}")
                cfg = command.get("config") or {}
                executor = executor_from_config(cfg, known_hosts=config.SSH_KNOWN_HOSTS)
                if not isinstance(executor, SshExecutor):
                    raise ValueError("executor SSH non disponibile")
                run_dir = runs_dir(project_id) / str(command.get("run_id", ""))
                recipe = TrainingRecipe(str(command.get("run_id", run_dir.name)), run_dir, run_dir / "train.sh", run_dir / "dataset" / "train.jsonl", run_dir / "dataset" / "val.jsonl", None, cfg, executor.provider)
                proc = executor.recover(recipe, Path(job["log_path"]), int(job["remote_job_id"]))
                proc.terminate()
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                pass
            ended = _now()
            with connect() as conn:
                conn.execute("UPDATE jobs SET state='stopped', ended_at=?, heartbeat_at=? WHERE id=?", (ended, ended, job["id"]))
        elif job is not None and job["pid"]:
            try:
                os.killpg(int(job["process_group"] or job["pid"]), signal.SIGTERM)
            except OSError:
                pass
            ended = _now()
            with connect() as conn:
                conn.execute("UPDATE jobs SET state='stopped', ended_at=?, heartbeat_at=? WHERE id=?", (ended, ended, job["id"]))
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
    with connect() as conn:
        conn.execute("UPDATE jobs SET state='stopped', ended_at=?, heartbeat_at=? WHERE id=?", (meta["ended_at"], _now(), handle.get("job_id")))
    _ACTIVE.pop(project_id, None)
    return _status(project_id)


def cleanup_remote_run(project_id: int, run_id: str) -> dict:
    """Elimina una run remota solo su richiesta esplicita dell'utente."""
    run_id = str(run_id or "").strip()
    root = runs_dir(project_id).resolve()
    run_dir = (root / run_id).resolve()
    if not run_id or run_id in {".", ".."} or run_dir.parent != root or not run_dir.is_dir():
        raise ValueError("run non valida per questo progetto")
    if _ACTIVE.get(project_id):
        raise ValueError("fermare il training prima del cleanup remoto")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE project_id=? AND kind='training' ORDER BY id DESC",
            (project_id,),
        ).fetchall()
    job = None
    for candidate in rows:
        try:
            command = json.loads(candidate["command_json"] or "{}")
        except (TypeError, ValueError):
            continue
        if command.get("run_id") == run_id:
            job = candidate
            break
    if job is None or job["provider"] not in REMOTE_PROVIDERS or not job["remote_job_id"]:
        raise ValueError("run remota non trovata")
    try:
        command = json.loads(job["command_json"] or "{}")
        cfg = command.get("config") or {}
        executor = executor_from_config(cfg, known_hosts=config.SSH_KNOWN_HOSTS)
        if not isinstance(executor, SshExecutor):
            raise ValueError("executor remoto non disponibile")
        recipe = TrainingRecipe(
            run_id=run_id,
            run_dir=run_dir,
            script=run_dir / "train.sh",
            train_dataset=run_dir / "dataset" / "train.jsonl",
            val_dataset=run_dir / "dataset" / "val.jsonl",
            dataset_snapshot_id=None,
            config=cfg,
            provider=executor.provider,
        )
        executor.recover(recipe, Path(job["log_path"]), int(job["remote_job_id"])).cleanup()
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ValueError(f"cleanup remoto fallito: {exc}") from exc
    meta = _read_run_meta(run_dir) or {"run_id": run_id}
    meta["remote_cleaned_at"] = _now()
    (run_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "run_id": run_id, "remote_cleaned_at": meta["remote_cleaned_at"]}


def reconcile_jobs() -> None:
    """Riconcilia i training locali marcati running all'avvio del backend."""
    with connect() as conn:
        rows = conn.execute("SELECT id, pid, provider FROM jobs WHERE kind='training' AND state='running'").fetchall()
        for row in rows:
            # Un PID remoto non è verificabile con os.kill locale. Il job SSH
            # resta running e viene controllato quando l'utente chiede status;
            # marcarlo failed qui perderebbe il training sopravvissuto al
            # riavvio del backend.
            if row["provider"] in REMOTE_PROVIDERS:
                continue
            alive = False
            if row["pid"]:
                try:
                    os.kill(int(row["pid"]), 0)
                    alive = True
                except OSError:
                    pass
            if not alive:
                conn.execute("UPDATE jobs SET state='failed', ended_at=?, error=? WHERE id=?", (_now(), "processo terminato durante il riavvio", row["id"]))
