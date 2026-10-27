"""Preparazione del checkpoint dopo una run: merge e registrazione, senza bloccare l'HTTP.

Il merge LoRA è il comando ufficiale del toolchain e ha bisogno di quell'ambiente
e del modello base. Se la run ha girato su una GPU remota, quei due pezzi sono
*là*: eseguire il merge sulla macchina del dashboard (spesso un portatile senza
`swift`) è il modo più facile per rendere inutilizzabile una run riuscita. Qui
il merge gira sull'host remoto, il modello unito viene scaricato nella run e
solo allora registrato. In locale, dove non c'è un host remoto, si usa lo
stesso comando in un thread (non nella richiesta HTTP) e la UI mostra il log.

Lo stato vive in `run.json` (`prepare_job`), così un riavvio del backend non
perde l'informazione; il log in `prepare.log` è ciò che la UI mostra.
"""
from __future__ import annotations

import shlex
import shutil
import threading
import time
from pathlib import Path

from .. import config
from . import custom_models, remote_eval, train_recipes, trainer
from .trainer_script import _now, remote_shell_path
from .training_executor import SshExecutor, executor_from_config
from .trainer import REMOTE_PROVIDERS
from .trainer_metrics import _log_tail

_LOCK = threading.Lock()
_THREADS: dict[tuple[int, str], threading.Thread] = {}


def _key(project_id: int, run_id: str) -> tuple[int, str]:
    return (int(project_id), str(run_id))


def _load(project_id: int, run_id: str) -> tuple[Path, dict]:
    return trainer._load_run(project_id, run_id)  # noqa: SLF001


def _save(run_dir: Path, meta: dict, job: dict) -> None:
    meta["prepare_job"] = job
    trainer.write_run_meta(run_dir, meta)


def _plan_for(project_id: int, run_id: str) -> dict:
    run_dir, meta = _load(project_id, run_id)
    if meta.get("state") != "finished":
        raise ValueError("il checkpoint si prepara solo da una run terminata con successo")
    cfg = meta.get("config") or {}
    adapter_id = str(meta.get("adapter_id") or cfg.get("adapter_id") or "").strip()
    recipe = train_recipes.get_recipe(adapter_id)
    if recipe is None:
        raise ValueError("adapter della run non riconosciuto")
    checkpoint = trainer.checkpoint_info(run_dir)
    if checkpoint is None:
        raise ValueError("la run non contiene un checkpoint completo")
    train_type = str(cfg.get("train_type") or "lora").lower()
    merged = checkpoint.get("merged_path")
    needs_merge = train_type == "lora" and not merged
    remote_possible = bool(
        str(cfg.get("executor") or "").lower() in REMOTE_PROVIDERS
        and cfg.get("ssh_host")
        and not meta.get("remote_cleaned_at")
    )
    local = custom_models.toolchain_status(recipe)
    blockers: list[str] = []
    if needs_merge and not remote_possible and not local["available"]:
        blockers.append(local["hint"])
    if needs_merge and remote_possible and not config.SSH_KNOWN_HOSTS.is_file():
        blockers.append(
            "manca il known_hosts SSH del training: apri la run dalla stessa installazione "
            "che l'ha lanciata, oppure esegui il merge locale."
        )
    mode = "register"
    if needs_merge:
        mode = "remote" if remote_possible else "local"
    return {
        "run_id": str(run_id),
        "adapter_id": adapter_id,
        "toolchain": recipe.toolchain,
        "train_type": train_type,
        "checkpoint": checkpoint,
        "needs_merge": needs_merge,
        "mode": mode,
        "ready": not blockers,
        "blockers": [item for item in blockers if item],
        "remote": remote_possible,
        "local_toolchain": local,
        "base_model": str(cfg.get("model_path") or cfg.get("model") or recipe.hf_repo or ""),
        "remote_cleaned": bool(meta.get("remote_cleaned_at")),
    }


def is_running(project_id: int, run_id: str) -> bool:
    key = _key(project_id, run_id)
    thread = _THREADS.get(key)
    return bool(thread and thread.is_alive())


def prepare_plan(project_id: int, run_id: str) -> dict:
    """Cosa serve per rendere servibile il checkpoint, senza toccare nulla."""
    return _plan_for(project_id, run_id)


def _job_status(project_id: int, run_id: str) -> dict:
    run_dir, meta = _load(project_id, run_id)
    job = dict(meta.get("prepare_job") or {})
    key = _key(project_id, run_id)
    if job.get("state") == "running" and not (key in _THREADS and _THREADS[key].is_alive()):
        job["state"] = "failed"
        job["error"] = "preparazione interrotta dal riavvio del backend"
        job["ended_at"] = _now()
        _save(run_dir, meta, job)
    model = None
    if job.get("model_id"):
        try:
            model = custom_models.get(str(job["model_id"]))
        except (ValueError, KeyError, TypeError):
            model = None
    plan = _plan_for(project_id, run_id)
    return {
        "run_id": str(run_id),
        "plan": plan,
        "job": job,
        "log_tail": _log_tail(run_dir / "prepare.log"),
        "model": model,
    }


def start_prepare(project_id: int, run_id: str) -> dict:
    """Avvia (o riprende a mostrare) la preparazione del checkpoint."""
    plan = _plan_for(project_id, run_id)
    if not plan["ready"]:
        raise ValueError("; ".join(plan["blockers"]) or "preparazione non possibile")
    key = _key(project_id, run_id)
    with _LOCK:
        run_dir, meta = _load(project_id, run_id)
        current = meta.get("prepare_job") or {}
        if current.get("state") == "running" and key in _THREADS and _THREADS[key].is_alive():
            return _job_status(project_id, run_id)
        log = run_dir / "prepare.log"
        log.write_text(
            f">> preparazione {run_id} · modalità {plan['mode']} · {_now()}\n",
            encoding="utf-8",
        )
        job = {
            "state": "running",
            "mode": plan["mode"],
            "phase": "merge" if plan["needs_merge"] else "register",
            "started_at": _now(),
            "ended_at": None,
            "error": None,
            "model_id": None,
        }
        _save(run_dir, meta, job)
        thread = threading.Thread(
            target=_run_job,
            args=(int(project_id), str(run_id), plan, run_dir),
            name=f"prepare-{run_id}",
            daemon=True,
        )
        _THREADS[key] = thread
        thread.start()
    return _job_status(project_id, run_id)


def _append(run_dir: Path, message: str) -> None:
    with (run_dir / "prepare.log").open("ab") as stream:
        stream.write((message.rstrip() + "\n").encode("utf-8"))


def _set_phase(run_dir: Path, meta: dict, job: dict, phase: str) -> None:
    job["phase"] = phase
    _save(run_dir, meta, job)


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _remote_tree_bytes(executor: SshExecutor, path: str) -> int:
    try:
        result = executor.run(f"du -sb {shlex.quote(path)}", timeout=30)
        if result.returncode == 0:
            return int((result.stdout or "0").split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _download_with_progress(
    executor: SshExecutor,
    remote: str,
    local: Path,
    run_dir: Path,
    meta: dict,
    job: dict,
    log_file: Path,
) -> None:
    """Scarica mostrando byte/percentuale/velocità, non solo una riga di log."""
    total = _remote_tree_bytes(executor, remote)
    job["progress"] = {
        "phase": "download", "done": _tree_bytes(local), "total": total,
        "percent": 0.0 if total else None, "bytes_per_s": 0,
    }
    _save(run_dir, meta, job)
    stop = threading.Event()

    def sample() -> None:
        last_done = _tree_bytes(local)
        last_time = time.monotonic()
        while not stop.wait(1.0):
            done = _tree_bytes(local)
            now = time.monotonic()
            speed = int(max(0, done - last_done) / max(0.001, now - last_time))
            progress = dict(job.get("progress") or {})
            progress.update({
                "phase": "download", "done": done,
                "total": total or progress.get("total") or 0,
                "percent": round(100.0 * done / total, 1) if total else None,
                "bytes_per_s": speed,
            })
            job["progress"] = progress
            _save(run_dir, meta, job)
            last_done, last_time = done, now

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        executor.download(remote, local, log_file=log_file)
    finally:
        stop.set()
        sampler.join(timeout=3)
    done = _tree_bytes(local)
    job["progress"] = {
        "phase": "download", "done": done, "total": total or done,
        "percent": 100.0, "bytes_per_s": 0,
    }
    _save(run_dir, meta, job)


def _run_job(project_id: int, run_id: str, plan: dict, run_dir: Path) -> None:
    run_dir, meta = _load(project_id, run_id)
    job = dict(meta.get("prepare_job") or {})
    try:
        if plan["mode"] == "remote":
            try:
                model = _prepare_remote(project_id, run_id, plan, run_dir, meta, job)
            except Exception as exc:  # noqa: BLE001
                if not plan["local_toolchain"]["available"]:
                    raise
                _append(
                    run_dir,
                    f"!! merge remoto non riuscito ({exc}); provo il merge locale",
                )
                _set_phase(run_dir, meta, job, "merge")
                model = custom_models.register_training_checkpoint(
                    project_id, run_id, log_file=run_dir / "prepare.log"
                )
        else:
            _set_phase(run_dir, meta, job, "merge" if plan["needs_merge"] else "register")
            _append(
                run_dir,
                ">> merge locale: il comando del toolchain gira su questa macchina"
                if plan["needs_merge"]
                else ">> checkpoint già unito: registrazione",
            )
            model = custom_models.register_training_checkpoint(
                project_id, run_id, log_file=run_dir / "prepare.log",
                merged_path=plan['checkpoint'].get('merged_path') if not plan['needs_merge'] else None,
            )
        job["state"] = "finished"
        job["phase"] = "done"
        job["progress"] = {"phase": "done", "done": 0, "total": 0, "percent": 100.0, "bytes_per_s": 0}
        job["ended_at"] = _now()
        job["model_id"] = model.get("id")
        job["model_name"] = model.get("display_name")
        job["error"] = None
        meta["checkpoint"] = trainer.checkpoint_info(run_dir)
        _save(run_dir, meta, job)
        _append(run_dir, f">> registrato: {model.get('display_name')} ({model.get('id')})")
    except Exception as exc:  # noqa: BLE001 - il job deve raccontare l'errore, non sparire
        job["state"] = "failed"
        job["ended_at"] = _now()
        job["error"] = str(exc)
        _save(run_dir, meta, job)
        _append(run_dir, f"!! {exc}")


def _merge_script(plan: dict, cfg: dict, remote_run: str) -> tuple[str, str, str]:
    """Comando remoto di merge e percorso dell'output (relativo alla run)."""
    checkpoint_rel = str(plan["checkpoint"]["relative_path"])
    kind = str(plan["toolchain"])
    output_rel = f"checkpoints/{plan['checkpoint']['name']}-merged"
    python = str(cfg.get("ssh_python") or "").strip()
    repo = str(cfg.get("ssh_train_repo") or "").strip()
    base = str(plan["base_model"])
    lines = ["set -euo pipefail"]
    if python and "/" in python:
        lines.append(f'export PATH="$(dirname {remote_shell_path(python)}):$PATH"')
    if cfg.get("use_hf"):
        lines.append("export USE_HF=1")
    cache = str(cfg.get("model_cache") or "").strip()
    if cache:
        lines.append(f"export MODELSCOPE_CACHE={remote_shell_path(cache)}")
    if repo:
        lines.append(f"cd {remote_shell_path(repo)}")
    lines += [
        f"RUN_DIR={shlex.quote(remote_run)}",
        f'CKPT="$RUN_DIR/{checkpoint_rel}"',
        f'OUT="$RUN_DIR/{output_rel}"',
        'rm -rf "$OUT"',
        'mkdir -p "$(dirname "$OUT")"',
    ]
    if kind == "ms-swift":
        lines.append('swift export --adapters "$CKPT" --merge_lora true --output_dir "$OUT"')
        actual = output_rel
    elif kind == "llama-factory":
        template = getattr(train_recipes.get_recipe(plan["adapter_id"]), "llamafactory_template", "") or "glm_ocr"
        lines.append(
            "llamafactory-cli export "
            f"--model_name_or_path {shlex.quote(base)} "
            '--adapter_name_or_path "$CKPT" '
            f"--template {shlex.quote(template)} "
            '--export_dir "$OUT" --trust_remote_code true'
        )
        actual = output_rel
    elif kind == "paddleformers":
        recipe = train_recipes.get_recipe(plan["adapter_id"])
        export_config = getattr(recipe, "paddleformers_export", "") or ""
        if not export_config:
            raise ValueError("ricetta PaddleFormers senza config ufficiale di export")
        lines.append(
            "paddleformers-cli export "
            f"{shlex.quote(export_config)} "
            f"model_name_or_path={shlex.quote(base)} "
            'output_dir="$OUT"'
        )
        actual = f"{output_rel}/export"
    else:
        raise ValueError(f"merge remoto non supportato per {kind}")
    lines.append(f'test -f "$RUN_DIR/{actual}/config.json"')
    lines.append("echo __MERGE_OK__")
    return "\n".join(lines), actual, output_rel


def _prepare_remote(
    project_id: int,
    run_id: str,
    plan: dict,
    run_dir: Path,
    meta: dict,
    job: dict,
) -> dict:
    cfg = meta.get("config") or {}
    executor = executor_from_config(cfg, known_hosts=config.SSH_KNOWN_HOSTS)
    if not isinstance(executor, SshExecutor):
        raise ValueError("executor remoto non disponibile")
    remote_run = executor.remote_dir(run_id)
    probe = executor.run(f"test -d {shlex.quote(remote_run)}", timeout=30)
    if probe.returncode != 0:
        raise ValueError("la run remota non è più raggiungibile: usa il merge locale o riaddestra")
    _set_phase(run_dir, meta, job, "merge")
    job["progress"] = {"phase": "merge", "done": 0, "total": 0, "percent": None, "bytes_per_s": 0}
    _save(run_dir, meta, job)
    script, actual_rel, _output_rel = _merge_script(plan, cfg, remote_run)
    _append(run_dir, f">> merge remoto su {executor.host} ({plan['toolchain']})")
    process = executor.stream(script, run_dir / "prepare.log")
    code = process.wait()
    if code != 0:
        raise RuntimeError(f"merge remoto fallito (exit {code})")

    if cfg.get("auto_evaluate") and plan.get("adapter_id") == "monkeyocrv2-parsing":
        _set_phase(run_dir, meta, job, "evaluate")
        job["progress"] = {"phase": "evaluate", "done": 0, "total": 0, "percent": None, "bytes_per_s": 0}
        _save(run_dir, meta, job)
        _append(run_dir, f">> Compare base and trained weights on the training GPU before download ({remote_run}/{actual_rel})")
        try:
            from . import training_comparison
            sample_count = len(training_comparison.validation_samples(run_dir))
            meta['comparison_job'] = {'state': 'running', 'phase': 'environment', 'started_at': _now(), 'completed': 0, 'total': sample_count * 2}

            def comparison_progress(**values):
                target = values.pop('target')
                done = values.pop('completed')
                per_target = values.pop('total')
                if values.get('phase') and values['phase'] != meta['comparison_job'].get('phase'):
                    meta['comparison_job']['phase_started_at'] = _now()
                meta['comparison_job']['updated_at'] = _now()
                meta['comparison_job'].update(values, target=target,
                    phase=values.get('phase', 'scoring'),
                    completed=(sample_count if target == 'fine_tuned' else 0) + done,
                    target_completed=done, target_total=per_target)
                _save(run_dir, meta, job)

            meta["evaluation"] = remote_eval.evaluate_on_remote(
                executor=executor,
                cfg=cfg,
                project_id=project_id,
                merged_remote=f"{remote_run}/{actual_rel}",
                lang=str(cfg.get("lang") or "it"),
                log_file=run_dir / "prepare.log",
                on_progress=comparison_progress,
            )
            meta['comparison_job'].update(state='finished', phase='done', ended_at=_now(), completed=sample_count * 2,
                                           verdict=meta['evaluation']['comparison']['verdict'])
            _append(run_dir, ">> Automatic paired comparison completed")
        except Exception as exc:  # noqa: BLE001
            meta["evaluation"] = {"error": str(exc)[-1000:]}
            meta['comparison_job'] = {**meta.get('comparison_job', {}), 'state': 'failed', 'phase': 'failed', 'ended_at': _now(), 'error': str(exc)[-1000:]}
            _append(run_dir, f"!! Automatic paired comparison failed: {exc}")
        _save(run_dir, meta, job)

    local_merged = run_dir / "checkpoints" / f"{plan['checkpoint']['name']}-merged"
    if local_merged.exists() and not custom_models._has_weights(local_merged):  # noqa: SLF001
        shutil.rmtree(local_merged, ignore_errors=True)
    if not custom_models._has_weights(local_merged):  # noqa: SLF001
        _set_phase(run_dir, meta, job, "download")
        _append(run_dir, f">> scarico il modello unito in {local_merged}")
        try:
            _download_with_progress(
                executor,
                f"{remote_run}/{actual_rel}",
                local_merged,
                run_dir,
                meta,
                job,
                run_dir / "prepare.log",
            )
        except Exception as exc:  # noqa: BLE001
            # Il primo rsync può cadere se l'host ha appena chiuso la sessione
            # (255). Un secondo tentativo evita di buttare una run già addestrata.
            _append(run_dir, f"!! download interrotto ({exc}); riprovo una volta")
            time.sleep(2)
            _download_with_progress(
                executor,
                f"{remote_run}/{actual_rel}",
                local_merged,
                run_dir,
                meta,
                job,
                run_dir / "prepare.log",
            )
    if not custom_models._has_weights(local_merged):  # noqa: SLF001
        raise ValueError("il modello unito scaricato è incompleto")
    _set_phase(run_dir, meta, job, "register")
    job["progress"] = {"phase": "register", "done": 0, "total": 0, "percent": 100.0, "bytes_per_s": 0}
    _save(run_dir, meta, job)
    model = custom_models.register_training_checkpoint(
        project_id,
        run_id,
        merged_path=local_merged,
        registered_type="full",
    )
    # Il merged remoto è solo un passaggio: l'adapter resta per riprovare.
    executor.run(f"rm -rf {shlex.quote(remote_run + '/' + actual_rel)}", timeout=60)
    return model
