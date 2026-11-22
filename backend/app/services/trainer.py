"""Orchestrazione training (ms-swift).

Gestisce il ciclo di vita dei run: start, monitor, stop, resume.
Delega la generazione script a `trainer_script` e la telemetria a `trainer_metrics`.
"""
from __future__ import annotations

import json
import os
import signal
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from collections.abc import Callable

from .. import config
from .dataset_builder import _project_dir
from ..domain import vram
from .trainer_metrics import _log_tail, _log_tail_logfile, _metrics, gpu_snapshot, parse_metrics_line, summarize_log
from .trainer_script import (
    FAMILIES,
    _dataset_source,
    _now,
    generate_script,
    prepare_training_files,
    resolve_config,
)
from . import train_recipes
from .training_executor import RemoteProcess, SshExecutor, TrainingRecipe, executor_from_config
from .artifacts import write_manifest, verify_manifest
from .i18n import msg
from ..adapters import jobs as jobstore
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


REMOTE_EXECUTORS = {"ssh", "vast", "runpod"}
# Prints True when the training framework sees a GPU. PaddleX (the layout recipe) has no torch.
_CUDA_CHECK = {
    "torch": "import torch;print(torch.cuda.is_available())",
    "paddle": "import paddle;print(paddle.device.cuda.device_count() > 0)",
}


def _remote_train_ready(cfg: dict, *, require_marker: bool = True, framework: str = "torch") -> dict | None:
    """Sonda l'host di training remoto: ambiente pronto (`.train_ready`) e GPU.

    Ritorna `{"ready": bool, "cuda": bool}` oppure `None` quando non si riesce a
    contattare l'host (SSH assente o non ancora salito): in quel caso il
    preflight non blocca, perché potrebbe essere solo l'inizio del provisioning.
    """
    host = str(cfg.get("ssh_host") or "").strip()
    if not host:
        return None
    user = str(cfg.get("ssh_user") or "root").strip() or "root"
    try:
        port = int(cfg.get("ssh_port") or 22)
    except (TypeError, ValueError):
        port = 22
    key = str(cfg.get("ssh_key_path") or "").strip()
    if not key and str(cfg.get("executor") or "").lower() in {"vast", "runpod"}:
        from .cloud_manager import ssh_key_path

        if ssh_key_path().exists():
            key = str(ssh_key_path())
    marker = (
        'test -f "$HOME/tabularium-train/.train_ready" && echo READY; '
        if require_marker
        else "echo READY; "
    )
    interpreter = str(cfg.get("ssh_python") or "").strip() or "python3"
    command = (
        marker
        + f"{shlex.quote(interpreter)} -c '{_CUDA_CHECK[framework]}' 2>/dev/null"
    )
    argv = [
        "ssh", "-p", str(port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
    ]
    if key:
        argv += ["-i", key]
    argv += [f"{user}@{host}", command]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = res.stdout or ""
    return {"ready": "READY" in out, "cuda": "True" in out}


def _cli_available(directory: str, command: str) -> bool:
    """Il comando del toolchain esiste nel checkout o nel PATH corrente."""
    candidate = Path(directory) / ".venv" / "bin" / command if directory else None
    if candidate and candidate.exists():
        return True
    return shutil.which(command) is not None


def _check_local_toolchain(
    toolchain: str,
    adapter_id: str,
    errors: list[str],
    warnings: list[str],
    lang: str,
) -> None:
    """Controlla il *toolchain della ricetta*, non quello di MonkeyOCRv2.

    Prima ogni modello veniva validato contro `TABULARIUM_TRAIN_REPO` e
    l'ambiente conda `monkeyocrv2-train`: un training GLM-OCR (LLaMA-Factory)
    o PaddleOCR-VL (PaddleFormers) veniva respinto per una cartella che non
    gli serve.
    """
    if config.TRAIN_PYTHON and not Path(config.TRAIN_PYTHON).exists():
        errors.append(msg("python_not_found", lang, path=config.TRAIN_PYTHON))
        return
    if toolchain == "ms-swift" and adapter_id == "monkeyocrv2-parsing":
        repo_train = Path(config.TRAIN_REPO) / "parsing" / "train" if config.TRAIN_REPO else None
        if not config.TRAIN_REPO:
            errors.append(msg("repo_not_configured", lang))
        elif not repo_train.is_dir():
            errors.append(msg("repo_invalid", lang, path=repo_train))
        elif not (repo_train / "ms-swift").is_dir():
            # Il fork ufficiale è un submodule: senza, `pip install -e
            # ./ms-swift` non esiste e un ms-swift upstream userebbe il
            # template *encoder*, non quello di Parsing.
            errors.append(msg("monkey_swift_submodule_missing", lang, path=repo_train))
        if not config.TRAIN_PYTHON and not shutil.which("conda"):
            errors.append(msg("conda_not_found", lang))
        return
    if toolchain == "ms-swift":
        # Il comando deve esistere *dove* girerà lo script: il PATH del
        # backend non è l'ambiente di training. Se è indicato un interprete,
        # il suo `swift` è quello che conta.
        candidate = Path(config.TRAIN_PYTHON).with_name("swift") if config.TRAIN_PYTHON else None
        found = shutil.which("swift")
        if found is not None:
            try:
                with Path(found).open("rb") as stream:
                    is_script = stream.read(2) == b"#!"
            except OSError:
                is_script = False
            # Su macOS `swift` è il compilatore Apple, non ms-swift.
            if not is_script:
                found = None
        if not (found or (candidate is not None and candidate.exists())):
            errors.append(msg("swift_not_found", lang))
        return
    if toolchain == "llama-factory":
        if not config.LLAMAFACTORY_DIR:
            errors.append(msg("toolchain_dir_missing", lang, toolchain="LLaMA-Factory", env="TABULARIUM_LLAMAFACTORY_DIR"))
        elif not _cli_available(config.LLAMAFACTORY_DIR, "llamafactory-cli"):
            errors.append(msg("toolchain_cli_missing", lang, toolchain="LLaMA-Factory", command="llamafactory-cli"))
        return
    if toolchain == "paddleformers":
        if not config.PADDLEFORMERS_DIR:
            errors.append(msg("toolchain_dir_missing", lang, toolchain="PaddleFormers", env="TABULARIUM_PADDLEFORMERS_DIR"))
        elif not _cli_available(config.PADDLEFORMERS_DIR, "paddleformers-cli"):
            errors.append(msg("toolchain_cli_missing", lang, toolchain="PaddleFormers", command="paddleformers-cli"))
        return
    if toolchain == "paddlex":
        if not config.PADDLEX_DIR:
            errors.append(msg("toolchain_dir_missing", lang, toolchain="PaddleX", env="TABULARIUM_PADDLEX_DIR"))
        elif not (Path(config.PADDLEX_DIR) / "main.py").is_file():
            errors.append(msg("toolchain_cli_missing", lang, toolchain="PaddleX", command="main.py"))
        return


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

    # La ricetta decide quali famiglie entrano, con quale toolchain e con
    # quali iperparametri. Un adapter non addestrabile non deve arrivare al
    # clic: l'errore si legge qui, con la doc ufficiale da consultare.
    adapter_id = str(cfg.get("adapter_id") or "monkeyocrv2-parsing")
    recipe = train_recipes.get_recipe(adapter_id)
    train_type = str(
        cfg.get("train_type")
        or (recipe.train_types[0] if recipe is not None and recipe.train_types else "lora")
    )
    if recipe is None or not recipe.trainable:
        reason = (recipe.note if recipe else "") or "nessuna ricetta di training"
        errors.append(msg("train_recipe_unavailable", lang, id=adapter_id, reason=reason))
        recipe_families = FAMILIES
    elif not recipe.script_ready:
        errors.append(
            msg(
                "train_recipe_not_ready",
                lang,
                id=adapter_id,
                toolchain=recipe.toolchain,
                doc=recipe.official_doc,
            )
        )
        recipe_families = recipe.families or FAMILIES
    elif train_type not in recipe.train_types:
        errors.append(
            msg(
                "train_type_unsupported",
                lang,
                type=train_type,
                allowed=", ".join(recipe.train_types),
                doc=recipe.official_doc,
            )
        )
        recipe_families = recipe.families or FAMILIES
    else:
        recipe_families = recipe.families or FAMILIES

    family_counts: dict[str, dict[str, int]] = {}
    counts = {"train.jsonl": 0, "val.jsonl": 0}
    found_any = False
    for family in recipe_families:
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
    coverage_errors: list[str] = []
    report_path = dataset_source / 'report.json'
    if report_path.is_file() and 'table' in recipe_families:
        report = json.loads(report_path.read_text(encoding='utf-8'))
        excluded = report.get('table_exclusions') or []
        # Legacy snapshots only recorded exclusion messages; keep them visible too.
        legacy_excluded = [warning for warning in report.get('warnings', [])
                           if '(Table)' in warning and any(marker in warning for marker in ('skipped', 'esclus', 'exclu'))]
        if report.get('approved_only') and (excluded or legacy_excluded):
            coverage_errors.append(msg('train_tables_excluded', lang, n=len(excluded) or len(legacy_excluded)))
            errors.extend(coverage_errors)
    if "table" in family_counts and family_counts["table"]["train"] == 0:
        warnings.append(msg("no_table_train_w", lang))
    stale = None
    if report_path.is_file():
        from .dataset_builder import snapshot_staleness  # noqa: PLC0415

        try:
            stale = snapshot_staleness(project_id, json.loads(report_path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            stale = None
    if stale:
        warnings.append(msg(
            "dataset_stale_w", lang, built=stale["built_at"], snapshot=stale["snapshot_pages"],
            eligible=stale["eligible_pages"], changed=stale["changed_pages"],
        ))

    # Dove gira il training decide che cosa va controllato qui. Prima questi
    # controlli guardavano sempre *questa* macchina: con un executor SSH/Vast
    # /RunPod un Mac senza CUDA né repo di training veniva respinto per una
    # GPU che non doveva avere, e il training remoto non partiva mai.
    executor_kind = str(cfg.get("executor") or "local").strip().lower()
    remote = executor_kind in REMOTE_EXECUTORS
    repo_train = Path(config.TRAIN_REPO) / "parsing" / "train" if config.TRAIN_REPO else None
    toolchain = recipe.toolchain if recipe is not None else "none"
    gpus: list[dict] = []
    if remote:
        if not str(cfg.get("ssh_host") or "").strip():
            errors.append(msg("ssh_host_missing", lang))
        if not str(cfg.get("ssh_train_repo") or "").strip():
            errors.append(msg("ssh_repo_missing", lang))
        if not str(cfg.get("ssh_python") or "").strip():
            warnings.append(msg("ssh_python_missing_w", lang))
        # "Preparato" è un fatto su disco, non un'assunzione: l'host deve avere
        # il marcatore `.train_ready` (installazione finita) e una GPU visibile.
        # Se l'host non risponde ancora, non si blocca: il provisioning è in corso.
        # Il marcatore `.train_ready` è del provisioning MonkeyOCRv2; un host
        # preparato a mano per LLaMA-Factory/PaddleFormers non ce l'ha, e non
        # deve essere respinto per questo. La GPU, invece, serve sempre.
        probe = _remote_train_ready(
            cfg,
            require_marker=(toolchain == "ms-swift" and adapter_id == "monkeyocrv2-parsing"),
            framework="paddle" if toolchain == "paddlex" else "torch",
        )
        if probe is not None:
            if not probe["ready"]:
                errors.append(msg("train_env_not_ready", lang))
            elif not probe["cuda"]:
                errors.append(msg("train_gpu_not_visible", lang))
    else:
        _check_local_toolchain(toolchain, adapter_id, errors, warnings, lang)
        gpus = gpu_snapshot()
        if not gpus and toolchain == "paddlex":
            from .hardware import is_apple_silicon  # noqa: PLC0415

            # CPU is a registered training device of PaddleX's layout module, but on Apple
            # Silicon (paddle 3.3.1, 2026-11-20) the first backward pass already returns inf
            # gradients (`silu_grad`): the run would diverge, so it is refused here.
            if is_apple_silicon():
                errors.append(msg("train_cpu_apple_e", lang))
            else:
                warnings.append(msg("train_cpu_w", lang))
        elif not gpus:
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
            # costruito il dataset. Meglio saperlo adesso. La geometria è
            # quella del modello scelto: prima si stimava sempre MonkeyOCRv2.
            # PaddleFormers non stima: *riserva* (`pre_alloc_memory`). Se la
            # riserva ufficiale supera la memoria libera, il run muore subito.
            prealloc = (recipe.flags.get("pre_alloc_memory") if recipe is not None else None) or {}
            prealloc_gb = prealloc.get(train_type) if isinstance(prealloc, dict) else prealloc
            if prealloc_gb and int(prealloc_gb) * 1024 > free_mb:
                errors.append(
                    msg(
                        "vram_prealloc_too_small",
                        lang,
                        index=gpu["index"],
                        need=int(prealloc_gb),
                        free=f"{free_mb / 1024:.1f}",
                    )
                )
                continue

            model_path = str(cfg.get("model_path") or "")
            known_arch = recipe.architecture if recipe is not None else None
            if not known_arch and not model_path:
                warnings.append(msg("vram_unknown_w", lang, model=adapter_id))
                gpu["vram_estimate"] = None
                continue
            effective = dict(recipe.defaults_for(train_type)) if recipe is not None else {}
            effective.update(cfg)
            need = vram.estimate(effective, model_path=model_path, architecture=known_arch)
            gpu["vram_estimate"] = need.to_dict()
            if not vram.fits(need.total_mib, free_mb):
                suggested = vram.largest_fitting_length(
                    effective, free_mb, model_path=model_path, architecture=known_arch
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
        "dataset_coverage_errors": coverage_errors,
        "dataset": {
            "dir": str(dataset_source),
            "snapshot_id": snapshot_id,
            "counts": counts,
            "families": family_counts,
            # Not `None` when eligible pages were approved or edited after the
            # snapshot: the one-click Vast start refuses it unless confirmed.
            "stale": stale,
        },
        "executor": executor_kind,
        "remote": remote,
        "adapter_id": adapter_id,
        "toolchain": toolchain,
        "recipe": {
            "maturity": recipe.maturity,
            "script_ready": recipe.script_ready,
            "data_view": recipe.data_view,
            "official_doc": recipe.official_doc,
        } if recipe is not None else None,
        "training_repo": str(cfg.get("ssh_train_repo") or "") if remote else (
            config.LLAMAFACTORY_DIR if toolchain == "llama-factory"
            else config.PADDLEFORMERS_DIR if toolchain == "paddleformers"
            else str(repo_train) if repo_train else None
        ),
        "python": str(cfg.get("ssh_python") or "") if remote else (config.TRAIN_PYTHON or config.TRAIN_ENV),
        "gpus": gpus,
        "output_dir": str(output_dir),
        "existing_checkpoints": [str(path) for path in sorted(checkpoints)],
    }


def _run_paths(project_id: int, run_id: str) -> tuple[Path, Path, Path]:
    run_dir = runs_dir(project_id) / run_id
    return run_dir, run_dir / "train.log", run_dir / "metrics.jsonl"


def write_run_meta(run_dir: Path, meta: dict) -> None:
    """Scrittura atomica di run.json.

    Lo stato viene letto dalla UI mentre il job di preparazione lo aggiorna:
    un `write_text` diretto può lasciare il file vuoto per un istante e far
    sembrare la run sparita. `os.replace` pubblica solo il file completo.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".run-", suffix=".json", dir=run_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(meta, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, run_dir / "run.json")
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


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


def start_run(
    project_id: int, cfg: dict, *, owner_id: int | None = None, lang: str = "it",
    on_run_created: Callable[[str], None] | None = None,
) -> dict:
    check = preflight(project_id, cfg, lang=lang)
    if not check["ready"]:
        raise ValueError("; ".join(check["errors"]))
    # Ricetta risolta una volta sola: decide vista dati, script e metadati.
    recipe, _effective, train_type = resolve_config(cfg)
    run_dir = runs_dir(project_id) / f"run_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name
    executor = executor_from_config(cfg, known_hosts=config.SSH_KNOWN_HOSTS)
    # Su host remoto i percorsi assoluti dei ritagli locali non esistono: la
    # vista copia i pixel nella run e scrive il percorso con cui l'host li
    # vedrà. In locale resta il percorso del crop, senza duplicare byte.
    images_prefix = None
    if isinstance(executor, SshExecutor):
        remote_root = str(cfg.get("ssh_root") or "/tmp/tabularium-runs").rstrip("/")
        images_prefix = f"{remote_root}/{run_id}/dataset/images"
    train_file, val_file = prepare_training_files(
        project_id,
        run_dir / "dataset",
        adapter_id=recipe.adapter_id,
        train_type=train_type,
        images_prefix=images_prefix,
    )
    view_manifest: dict | None = None
    view_path = run_dir / "dataset" / "view.json"
    if view_path.is_file():
        try:
            view_manifest = json.loads(view_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            view_manifest = None

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
        # A resumed run must merge its own final adapter. Never copy an old
        # merged export or rsync's incomplete temporary files into the new run.
        shutil.copytree(previous_checkpoints, run_dir / "checkpoints", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*-merged', 'export', '.*'))

    script = generate_script(cfg, run_dir, train_file, val_file)
    script_path = run_dir / "train.sh"
    script_path.write_text(script, encoding="utf-8")

    snapshot_id = check["dataset"].get("snapshot_id")
    if isinstance(executor, SshExecutor):
        # Stessa politica del tunnel Vast: la host key si fissa alla prima
        # connessione (trust-on-first-use), e da lì ogni cambiamento fallisce.
        # Senza, l'executor rifiutava qualunque host mai visto prima.
        from . import cloud_manager

        try:
            cloud_manager.pin_ssh_host_key(executor.host, executor.port)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"host SSH non raggiungibile per fissarne la chiave: {exc}") from exc
    meta = {
        "run_id": run_id,
        "state": "running",
        "started_at": _now(),
        "config": cfg,
        "adapter_id": recipe.adapter_id,
        "toolchain": recipe.toolchain,
        "data_view": recipe.data_view,
        "data_view_manifest": view_manifest,
        "dataset_snapshot_id": snapshot_id,
        # Which pages the model learns from: the scope of the dataset snapshot.
        "collection_scope": dataset_collection_scope(project_id),
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
    write_run_meta(run_dir, meta)

    log_file = run_dir / "train.log"
    if on_run_created is not None:
        on_run_created(run_id)
    try:
        proc = executor.launch(recipe, log_file)
    except Exception as exc:  # noqa: BLE001
        meta["state"] = "error"
        meta["error"] = str(exc)
        write_run_meta(run_dir, meta)
        raise

    meta["job_id"] = jobstore.start_training_job(
        owner_id=owner_id,
        project_id=project_id,
        provider=executor.provider,
        pid=proc.pid if executor.provider == "local" else None,
        remote_job_id=str(proc.pid) if executor.provider in REMOTE_PROVIDERS else None,
        command={"run_id": run_id, "script": str(run_dir / "train.sh"), "config": cfg},
        log_path=str(log_file),
        recovery_strategy=(
            "reconcile-local-pid" if executor.provider == "local"
            else "reconcile-ssh-session"
        ),
        heartbeat_at=_now(),
    )
    if executor.provider == "local":
        meta["pid"] = proc.pid
    else:
        meta["remote_job_id"] = str(proc.pid)
    write_run_meta(run_dir, meta)
    _ACTIVE[project_id] = {"run_id": run_id, "proc": proc, "run_dir": run_dir, "log_file": log_file, "job_id": meta["job_id"]}
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


def _auto_prepare(project_id: int, run_id: str) -> None:
    """Avvia merge/registrazione appena la run finisce, se è possibile.

    L'utente non deve sapere che il checkpoint LoRA non è servibile così com'è:
    se il piano è pronto (host remoto ancora raggiungibile o toolchain locale)
    il lavoro parte da solo. Se non lo è, il pannello mostrerà il motivo e il
    pulsante manuale: meglio un avviso chiaro che un job destinato a fallire.
    """
    try:
        from . import checkpoint_prepare

        plan = checkpoint_prepare.prepare_plan(project_id, run_id)
        if plan.get("ready"):
            checkpoint_prepare.start_prepare(project_id, run_id)
    except Exception:  # noqa: BLE001 - il training non deve fallire per questo
        pass


def _monitor(project_id: int) -> None:
    handle = _ACTIVE.get(project_id)
    if not handle:
        return
    proc, run_dir = handle["proc"], handle["run_dir"]
    code = proc.wait()
    training_exit_code = code
    state = "finished" if code == 0 else "failed"
    artifact_error = None
    artifact_warning = None
    post_training: dict = {"training_exit_code": training_exit_code}
    if hasattr(proc, "download_artifacts"):
        post_training.update(downloaded=False, manifest=None)
        try:
            result = proc.download_artifacts(run_dir / "checkpoints")
            post_training.update(
                downloaded=bool(result.get("downloaded", True)),
                manifest=result.get("manifest"),
            )
            if result.get("manifest") == "unreachable":
                # The files arrived (rsync checks them end to end) and only the
                # bookkeeping read of the remote manifest kept timing out: a
                # finished training is not failed for a network hiccup.
                artifact_warning = "; ".join(result.get("errors") or ["manifest remoto irraggiungibile"])
            elif not result.get("ok"):
                artifact_error = "; ".join(result.get("errors") or ["checksum artefatti remoto fallito"])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            artifact_error = str(exc)
    if artifact_error:
        state = "failed"
        code = 1
        post_training["error"] = artifact_error
    artifact_manifest = write_manifest(run_dir / "checkpoints")
    artifact_check = verify_manifest(run_dir / "checkpoints", artifact_manifest)
    meta = _read_run_meta(run_dir) or {}
    meta["state"] = state
    meta["ended_at"] = _now()
    meta["exit_code"] = code
    meta["artifacts"] = artifact_check
    meta["summary"] = summarize_log(
        _log_tail_logfile(run_dir),
        started_at=meta.get("started_at"),
        ended_at=meta.get("ended_at"),
    )
    meta["checkpoint"] = checkpoint_info(run_dir)
    meta["post_training"] = post_training
    if artifact_error:
        meta["error"] = artifact_error
    if artifact_warning:
        meta["artifact_warning"] = artifact_warning
    write_run_meta(run_dir, meta)
    if state == "finished" and (meta.get("config") or {}).get("auto_prepare", True):
        _auto_prepare(project_id, run_dir.name)
    if meta.get("job_id") is not None:
        jobstore.settle(
            meta["job_id"], state, ended=meta["ended_at"], exit_code=code
        )
    _ACTIVE.pop(project_id, None)


_LEGACY_MANIFEST_MARKER = "__TABULARIUM_MANIFEST_BEGIN__"


def recovery_check(run_dir: Path, meta: dict, *, verify: bool = True) -> dict:
    """Whether a failed run can be marked finished: the training succeeded,
    the checkpoints are here and intact, and only a later step failed.

    Runs written since `post_training` exists say so directly. Older runs
    only kept the error text: the remote manifest read is the step *after* a
    completed rsync, so a failure there plus a log that reached its last step
    is the same evidence. `verify=False` skips hashing the checkpoints (for
    listings); `recover_run` always verifies.
    """
    if meta.get("state") != "failed":
        return {"ok": False, "reason": "only a failed run can be recovered"}
    post = meta.get("post_training")
    if isinstance(post, dict):
        if post.get("training_exit_code") != 0:
            return {"ok": False, "reason": "the training command did not exit 0"}
        if not post.get("downloaded"):
            return {"ok": False, "reason": "the checkpoints were not fully downloaded"}
        if post.get("manifest") not in {"verified", "unreachable"}:
            return {"ok": False, "reason": "the checkpoints do not match the remote manifest"}
        evidence = "post_training"
    else:
        summary = meta.get("summary") or summarize_log(
            _log_tail_logfile(run_dir), started_at=meta.get("started_at"), ended_at=meta.get("ended_at"),
        )
        steps, total = (summary or {}).get("steps"), (summary or {}).get("total_steps")
        if not (isinstance(steps, int) and isinstance(total, int) and total > 0 and steps >= total):
            return {"ok": False, "reason": "the training log does not reach its last step"}
        if _LEGACY_MANIFEST_MARKER not in str(meta.get("error") or ""):
            return {"ok": False, "reason": "the run did not fail on the post-training manifest read"}
        evidence = "legacy_manifest_read"
    if not verify:
        return {"ok": checkpoint_info(run_dir) is not None, "evidence": evidence, "reason": "the run has no complete checkpoint"}
    manifest = run_dir / "artifacts.sha256"
    check = verify_manifest(run_dir / "checkpoints", manifest)
    if not check.get("ok") or not check.get("checked"):
        return {"ok": False, "reason": "the local checkpoints fail their checksum manifest"}
    if checkpoint_info(run_dir) is None:
        return {"ok": False, "reason": "the run has no complete checkpoint"}
    return {"ok": True, "evidence": evidence, "artifacts": check}


def recover_run(project_id: int, run_id: str, *, reason: str = "") -> dict:
    """Mark a failed run finished when only post-training bookkeeping failed.

    The previous state, error and exit code are kept under `recovery`, so the
    run's history still shows what went wrong. Nothing is deleted. Then the
    usual auto-prepare runs, as it would have at the end of the training.
    """
    run_dir, meta = _load_run(project_id, run_id)
    check = recovery_check(run_dir, meta)
    if not check["ok"]:
        raise ValueError(f"run not recoverable: {check['reason']}")
    meta["recovery"] = {
        "at": _now(),
        "reason": str(reason or "post-training step failed after a successful training").strip()[:500],
        "evidence": check["evidence"],
        "previous_state": meta.get("state"),
        "previous_error": meta.get("error"),
        "previous_exit_code": meta.get("exit_code"),
    }
    meta["state"] = "finished"
    meta["exit_code"] = 0
    meta.pop("error", None)
    meta["artifacts"] = check["artifacts"]
    meta["checkpoint"] = checkpoint_info(run_dir)
    write_run_meta(run_dir, meta)
    if (meta.get("config") or {}).get("auto_prepare", True):
        _auto_prepare(project_id, run_dir.name)
    return _decorate_run(_read_run_meta(run_dir) or meta, run_dir)


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def _looks_like_model(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "adapter_config.json").exists() and any(
        path.glob(pattern) for pattern in ("adapter_model*.safetensors", "adapter_model*.bin")
    ):
        return True
    return (path / "config.json").exists() and any(
        path.glob(pattern) for pattern in ("*.safetensors", "*.bin")
    )


def layout_checkpoint(run_dir: Path) -> dict | None:
    """The exported PP-DocLayoutV3 inference model of a PaddleX run, with its evaluations.

    It is loaded as it is (`create_model(model_dir=...)`): nothing to merge or register.
    """
    from ..models import pp_doclayoutv3 as detector

    for path in sorted((run_dir / "checkpoints").glob(f"*/{detector.INFERENCE_DIR}")):
        if all((path / name).is_file() for name in detector.INFERENCE_FILES):
            evaluations = {}
            for key in ("eval_base", "eval_trained"):
                try:
                    evaluations[key] = json.loads((run_dir / f"{key}.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    evaluations[key] = None
            return {
                "path": str(path),
                "relative_path": path.relative_to(run_dir).as_posix(),
                "name": "best_model",
                "bytes": _dir_bytes(path),
                "train_type": "full",
                "merged_path": None,
                "merged_relative_path": None,
                "merged_bytes": 0,
                "needs_merge": False,
                "layout": True,
                **evaluations,
            }
    return None


def layout_checkpoints() -> list[dict]:
    """Every finished PaddleX run with an exported model, across projects (newest first).

    The layout model is a global Xiaomi-OCR-0 setting, so the choice spans projects; the
    value stored is `<project_id>/<run_id>`, resolved by `layout_model_dir`.
    """
    items: list[dict] = []
    for meta_path in config.DATA_DIR.glob("*/runs/*/run.json"):
        run_dir = meta_path.parent
        meta = _read_run_meta(run_dir) or {}
        if meta.get("toolchain") != "paddlex" or meta.get("state") != "finished":
            continue
        checkpoint = layout_checkpoint(run_dir)
        if checkpoint is None:
            continue
        project_id = run_dir.parent.parent.name
        items.append({
            "id": f"{project_id}/{run_dir.name}",
            "project_id": int(project_id) if project_id.isdigit() else project_id,
            "run_id": run_dir.name,
            "ended_at": meta.get("ended_at"),
            "eval_base": checkpoint["eval_base"],
            "eval_trained": checkpoint["eval_trained"],
        })
    return sorted(items, key=lambda item: str(item["ended_at"] or ""), reverse=True)


def layout_model_dir(choice: str) -> str | None:
    """`official` -> None (the vendor weights); `<project_id>/<run_id>` -> its model dir.

    A choice that no longer resolves is an error, never a silent fall-back to the
    official weights: the user picked a model and would read pages from another one.
    """
    choice = str(choice or "official").strip()
    if choice == "official":
        return None
    project, _, run_id = choice.partition("/")
    run_dir = (config.DATA_DIR / project / "runs" / run_id).resolve()
    if run_dir.parent.parent.parent != config.DATA_DIR.resolve() or not run_id:
        raise ValueError(f"layout model '{choice}' is not a training run")
    checkpoint = layout_checkpoint(run_dir)
    if checkpoint is None:
        raise ValueError(f"layout model '{choice}': the run has no exported PP-DocLayoutV3 model")
    return checkpoint["path"]


def checkpoint_info(run_dir: Path) -> dict | None:
    """Il checkpoint locale più recente, con peso su disco e merge disponibile.

    La ricerca è la stessa della registrazione (`custom_models`), ma qui non
    unisce nulla: dice all'utente *dove sono i pesi* e se il merge è già stato
    fatto. Un adapter LoRA senza merge non è servibile, quindi la UI deve
    saperlo prima di offrire il pulsante.
    """
    meta = _read_run_meta(run_dir) or {}
    if meta.get("toolchain") == "paddlex":
        return layout_checkpoint(run_dir)
    cfg = meta.get("config") or {}
    train_type = str(cfg.get("train_type") or "lora").strip().lower()
    root = run_dir / "checkpoints"
    candidates: list[Path] = []
    if root.is_dir():
        for path in root.rglob("checkpoint-*"):
            if path.is_dir() and _looks_like_model(path):
                candidates.append(path)
    if not candidates:
        return None
    checkpoint = max(
        candidates,
        key=lambda path: int(path.name.split("-", 1)[1]) if path.name.split("-", 1)[1].isdigit() else -1,
    )
    merged = None
    for candidate in (
        checkpoint.parent.parent / f"{checkpoint.name}-merged",
        checkpoint.parent / "export",
    ):
        from . import custom_models
        if custom_models._has_weights(candidate):
            merged = candidate
            break
    return {
        "path": str(checkpoint),
        "relative_path": checkpoint.relative_to(run_dir).as_posix(),
        "name": checkpoint.name,
        "bytes": _dir_bytes(checkpoint),
        "train_type": train_type,
        "merged_path": str(merged) if merged else None,
        "merged_relative_path": merged.relative_to(run_dir).as_posix() if merged else None,
        "merged_bytes": _dir_bytes(merged) if merged else 0,
        "needs_merge": train_type == "lora" and merged is None,
    }


def _decorate_run(meta: dict, run_dir: Path) -> dict:
    """Aggiunge riepilogo e checkpoint a una run terminale, senza riscriverla."""
    activity_path = run_dir / "activity.json"
    if activity_path.exists():
        try:
            meta["activity"] = json.loads(activity_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if meta.get('comparison_job'):
        meta['comparison_job'] = {**meta['comparison_job'], 'log_tail': _log_tail(run_dir / 'comparison.log')}
    if meta.get("state") in {"running", "finished", "failed", "stopped"}:
        if meta.get("state") == "running" or not meta.get("summary"):
            meta["summary"] = summarize_log(
                _log_tail_logfile(run_dir),
                started_at=meta.get("started_at"),
                ended_at=meta.get("ended_at"),
            )
        meta["checkpoint"] = checkpoint_info(run_dir)
    if meta.get("state") == "failed":
        meta["recoverable"] = recovery_check(run_dir, meta, verify=False)["ok"]
    return meta


def _load_run(project_id: int, run_id: str) -> tuple[Path, dict]:
    run_id = str(run_id or "").strip()
    root = runs_dir(project_id).resolve()
    run_dir = (root / run_id).resolve()
    if not run_id or run_id in {".", ".."} or run_dir.parent != root or not run_dir.is_dir():
        raise FileNotFoundError("run di training non trovata")
    meta = _read_run_meta(run_dir)
    if meta is None:
        raise FileNotFoundError("metadati della run non leggibili")
    return run_dir, meta


def dataset_collection_scope(project_id: int) -> dict:
    """The collection scope of the dataset snapshot a new run trains on.

    Snapshots built before collections existed covered the whole project.
    """
    whole = {"kind": "project", "collections": [], "include_unassigned": True}
    try:
        source, _snapshot = _dataset_source(project_id)
        report = json.loads((source / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return whole
    scope = report.get("collection_scope")
    return scope if isinstance(scope, dict) else whole


def gold_eval_summary(record: dict | None) -> dict | None:
    """The gold-set result of a run without its per-table detail, for the history."""
    if not isinstance(record, dict):
        return None
    return {key: record.get(key) for key in ("state", "complete", "aggregate", "error", "ended_at")}


def list_runs(project_id: int, *, limit: int = 50) -> list[dict]:
    """Le run del progetto, dalla più recente: servono allo storico e al resume."""
    root = runs_dir(project_id)
    if not root.is_dir():
        return []
    items: list[dict] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or not (run_dir / "run.json").is_file():
            continue
        meta = _read_run_meta(run_dir) or {"run_id": run_dir.name, "state": "unknown"}
        cfg = meta.get("config") or {}
        items.append(
            {
                "run_id": str(meta.get("run_id") or run_dir.name),
                "state": meta.get("state") or "unknown",
                "started_at": meta.get("started_at"),
                "ended_at": meta.get("ended_at"),
                "exit_code": meta.get("exit_code"),
                "adapter_id": meta.get("adapter_id") or cfg.get("adapter_id"),
                "toolchain": meta.get("toolchain"),
                "train_type": cfg.get("train_type") or "lora",
                "summary": meta.get("summary") or summarize_log(
                    _log_tail_logfile(run_dir),
                    started_at=meta.get("started_at"),
                    ended_at=meta.get("ended_at"),
                ),
                "remote_cleaned_at": meta.get("remote_cleaned_at"),
                "gold_eval": gold_eval_summary(meta.get("gold_eval")),
                # Runs from before collections trained on the whole project.
                "collection_scope": meta.get("collection_scope")
                or {"kind": "project", "collections": [], "include_unassigned": True},
            }
        )
    items.sort(key=lambda item: (item.get("started_at") or "", item["run_id"]), reverse=True)
    return items[: max(1, int(limit))]


def _status(project_id: int, run_id: str | None = None) -> dict:
    handle = _ACTIVE.get(project_id)
    if handle and (not run_id or handle["run_id"] == run_id):
        run_dir = handle["run_dir"]
        meta = _read_run_meta(run_dir) or {"run_id": handle["run_id"], "state": "running"}
        tail = _log_tail(handle["log_file"])
        metrics = _metrics(run_dir)
        return {
            "active": True,
            "run": _decorate_run(meta, run_dir),
            "log_tail": tail,
            "metrics": metrics,
            "gpu": (
                handle["proc"].gpu_snapshot()
                if hasattr(handle.get("proc"), "gpu_snapshot")
                else gpu_snapshot()
            ),
        }

    if run_id:
        try:
            run_dir, meta = _load_run(project_id, run_id)
        except FileNotFoundError:
            return {"active": False, "run": None, "log_tail": "", "metrics": [], "gpu": gpu_snapshot()}
        return {
            "active": False,
            "run": _decorate_run(meta, run_dir),
            "log_tail": _log_tail(run_dir / "train.log"),
            "metrics": _metrics(run_dir),
            "gpu": gpu_snapshot(),
        }

    # Recovery dopo un riavvio del backend: l'handle Python è perso, ma il
    # PID/job manifest persistito resta sufficiente per osservare e fermare la
    # run locale.
    job = jobstore.latest(project_id, "training")
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
            jobstore.mark_state(
                job["id"], "failed", ended=ended,
                error="job remoto non più raggiungibile dopo il riavvio",
            )

        alive = False
        if job["pid"]:
            try:
                os.kill(int(job["pid"]), 0)
                alive = True
            except OSError:
                alive = False
        if not alive:
            jobstore.mark_state(
                job["id"], "failed", ended=_now(),
                error="processo non più presente dopo il riavvio",
            )
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
                "run": _decorate_run(meta, run_dir),
                "log_tail": _log_tail(run_dir / "train.log"),
                "metrics": _metrics(run_dir),
                "gpu": gpu_snapshot(),
            }
    return {"active": False, "run": None, "log_tail": "", "metrics": [], "gpu": gpu_snapshot()}


def stop_run(project_id: int) -> dict:
    handle = _ACTIVE.get(project_id)
    if not handle:
        running_jobs = jobstore.of_project(project_id, "training", state="running")
        job = running_jobs[0] if running_jobs else None
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
            jobstore.mark_state(job["id"], "stopped", ended=ended)
        elif job is not None and job["pid"]:
            try:
                os.killpg(int(job["process_group"] or job["pid"]), signal.SIGTERM)
            except OSError:
                pass
            ended = _now()
            jobstore.mark_state(job["id"], "stopped", ended=ended)
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
    write_run_meta(handle["run_dir"], meta)
    if handle.get("job_id") is not None:
        jobstore.mark_state(handle["job_id"], "stopped", ended=meta["ended_at"])
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
    from . import checkpoint_prepare

    if checkpoint_prepare.is_running(project_id, run_id):
        raise ValueError("preparazione del checkpoint in corso: attendere prima del cleanup remoto")
    rows = jobstore.of_project(project_id, "training")
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
    write_run_meta(run_dir, meta)
    return {"ok": True, "run_id": run_id, "remote_cleaned_at": meta["remote_cleaned_at"]}


def reconcile_jobs() -> None:
    """Riconcilia i training locali marcati running all'avvio del backend."""
    rows = jobstore.of_kind_state("training", "running")
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
            jobstore.mark_state(
                row["id"], "failed", ended=_now(),
                error="processo terminato durante il riavvio",
            )
