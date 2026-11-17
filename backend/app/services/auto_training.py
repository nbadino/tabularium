"""Un click: affitta la GPU, prepara l'ambiente del modello, avvia il training.

Il punto è che il modello sceglie il *proprio* ambiente (transformers 4.46 vs 5.20,
ms-swift fork vs LLaMA-Factory vs PaddleFormers): non esiste un venv unico.
Qui il profilo viene dalla ricetta, l'onstart di Vast installa quel profilo e
il backend aspetta `ready.json` prima di lanciare la run. Il dopo-training è già
automatico (`auto_prepare`), quindi l'utente vede solo le fasi e il modello che
compare in Models.

Lo stato del job vive in `<data>/auto-training/<job_id>.json`, non in memoria:
un riavvio del backend non fa perdere il noleggio né lo stato del provisioning.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import config
from . import cloud_manager, gold_eval, i18n, train_recipes, trainer
from .i18n import msg
from .trainer_script import _now

PROFILES = {
    "monkeyocrv2-parsing": "monkey",
    "qwen3-vl-8b": "qwen",
    # ms-swift 4.6.0 + transformers 5.20.2 meet the Qwen3.5 Best Practice pins.
    "xiaomi-ocr-0": "qwen",
    "unlimited-ocr": "legacy",
    "unlimited-ocr-2": "uocr2",
    "deepseek-ocr": "legacy",
    "dots-ocr": "dots",
    "glm-ocr": "glm",
    "paddleocr-vl": "paddle",
}

PADDLE_IMAGE = "paddlepaddle/paddle:3.3.0-gpu-cuda12.6-cudnn9.5"
# Immagini prebuilt versionate: niente pip al noleggio. Il profilo monkey è il
# primo; gli altri restano sul provisioning runtime finché non hanno la loro.
PREBUILT_IMAGES = {
    "monkey": "ghcr.io/nbadino/tabularium-train-monkey:monkey-v4",
    "qwen": "ghcr.io/nbadino/tabularium-train-qwen:qwen-v1",
    "legacy": "ghcr.io/nbadino/tabularium-train-legacy:legacy-v1",
    "uocr2": "ghcr.io/nbadino/tabularium-train-uocr2:uocr2-v1",
    "glm": "ghcr.io/nbadino/tabularium-train-glm:glm-v1",
    "paddle": "ghcr.io/nbadino/tabularium-train-paddle:paddle-v1",
}
PREBUILT_MARKER = "/opt/tabularium/ready.json"
# The CUDA toolkit each versioned image is built on (its base image tag; the
# torch build recorded in its ready.json): it is the minimum host driver, and
# below `cloud_manager.BLACKWELL_FIRST_TOOLKIT` the image cannot run on Blackwell.
PREBUILT_CUDA = {
    "monkey": 12.8,  # pytorch 2.8.0-cuda12.8
    "paddle": 12.6,  # paddlepaddle/paddle 3.3.0-gpu-cuda12.6
    "qwen": 12.4,  # pytorch 2.6.0-cuda12.4 (torch 2.6.0+cu124)
    "legacy": 12.4,
    "uocr2": 12.4,
    "glm": 12.4,
}
_LOCK = threading.Lock()
_CANCEL: dict[str, threading.Event] = {}
_THREADS: dict[str, threading.Thread] = {}


def profile_for(adapter_id: str) -> str | None:
    return PROFILES.get(str(adapter_id or ""))


def blackwell_ready(profile: str) -> bool:
    """Whether the profile's image has kernels for Blackwell GPUs.

    A prebuilt image is as new as its toolkit; the runtime-provisioned profile
    picks its `vastai/pytorch` image from the host's CUDA (`vast_image_for`).
    """
    if profile not in PREBUILT_IMAGES:
        return True
    return PREBUILT_CUDA[profile] + 1e-9 >= cloud_manager.BLACKWELL_FIRST_TOOLKIT


def best_offer(offers: list[dict], max_dph: float) -> dict | None:
    """La GPU migliore *dentro* il budget, non la più economica.

    Per il training la VRAM è il vincolo che decide se il run parte; a parità
    di VRAM conta la generazione (compute capability), poi l'affidabilità, e
    solo alla fine il prezzo. Così con 1 $/h si prende una 4090 da 24 GB invece
    di una 3090 altrettanto cara ma più lenta; se il budget arriva a una A100
    da 40 GB, quella vince perché può addestrare configurazioni più lunghe.
    """
    eligible = [
        offer for offer in offers
        if float(offer.get("dph_total") or 0) > 0 and float(offer.get("dph_total") or 0) <= float(max_dph)
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda offer: (
            float(offer.get("gpu_ram") or 0),
            float(offer.get("compute_cap") or 0),
            float(offer.get("reliability") or 0),
            -float(offer.get("dph_total") or 0),
        ),
    )


def expected_paths(profile: str) -> dict:
    root = f"/root/tabularium-train/{profile}"
    if profile == "monkey":
        repo = f"{root}/MonkeyOCRv2/parsing/train"
    elif profile == "glm":
        repo = f"{root}/LLaMA-Factory"
    elif profile == "paddle":
        repo = f"{root}/PaddleFormers"
    else:
        repo = root
    return {"python": f"{root}/venv/bin/python", "repo": repo, "root": root}


# Vast's create-instance API caps `onstart` at 4048 characters and suggests gzip+base64.
VAST_ONSTART_LIMIT = 4048


def _without_comments(script: str) -> str:
    """The script without comment-only and blank lines, heredoc bodies left untouched.

    Comments are most of `setup_train_env.sh`'s size; dropping them is what keeps the
    compressed script inside Vast's limit. Heredoc bodies (the Python block) are kept
    verbatim, because there a `#` or a blank line belongs to another language.
    """
    lines = script.splitlines()
    kept: list[str] = []
    terminator: str | None = None
    for index, line in enumerate(lines):
        if terminator is not None:
            kept.append(line)
            if line.strip() == terminator:
                terminator = None
            continue
        stripped = line.strip()
        if index > 0 and (not stripped or stripped.startswith("#")):
            continue
        kept.append(line)
        heredoc = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if heredoc:
            terminator = heredoc.group(1)
    return "\n".join(kept) + "\n"


def _onstart(profile: str, adapter_id: str) -> str:
    script = (config.RESOURCE_DIR / "scripts" / "cloud" / "setup_train_env.sh").read_text(encoding="utf-8")
    packed = gzip.compress(_without_comments(script).encode("utf-8"), compresslevel=9, mtime=0)
    encoded = base64.b64encode(packed).decode("ascii")
    return (
        "mkdir -p /root/tabularium-train && "
        f"echo {shlex.quote(encoded)} | base64 -d | gunzip > /tmp/setup_train_env.sh && "
        "TABULARIUM_TRAIN_ROOT=/root/tabularium-train "
        f"TABULARIUM_TRAIN_PROFILE={shlex.quote(profile)} "
        f"TABULARIUM_TRAIN_ADAPTER={shlex.quote(adapter_id)} "
        "bash /tmp/setup_train_env.sh || "
        f"(mkdir -p /root/tabularium-train/{profile} && "
        f"echo 'onstart fallito' > /root/tabularium-train/{profile}/.failed)"
    )


def _job_dir() -> Path:
    path = config.ROOT_DIR / "auto-training"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    if not job_id.startswith("auto_") or not job_id[5:].isdigit():
        raise FileNotFoundError("job di training automatico non trovato")
    return _job_dir() / f"{job_id}.json"


def _save(job: dict) -> None:
    path = _job_path(job["id"])
    fd, temporary = tempfile.mkstemp(prefix=".auto-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(job, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def get_job(job_id: str, *, project_id: int | None = None) -> dict:
    path = _job_path(job_id)
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FileNotFoundError("job di training automatico non trovato") from exc
    if project_id is not None and int(job.get("project_id") or 0) != int(project_id):
        raise FileNotFoundError("job di training automatico non trovato")
    return job


def _update(job: dict, **patch) -> None:
    now = _now()
    if patch.get("state") == "failed" and job.get("state") != "failed":
        job["failure_stage"] = job.get("phase")
    if patch.get("phase") and patch["phase"] != job.get("phase"):
        job.setdefault("phase_history", []).append({"phase": patch["phase"], "started_at": now})
        job["phase_started_at"] = now
    job.update(patch)
    job["updated_at"] = now
    _save(job)
    if patch.get('instance_destroyed') and job.get('run_id'):
        try:
            run_dir, meta = trainer._load_run(int(job['project_id']), str(job['run_id']))
            meta['remote_cleaned_at'] = _now()
            trainer.write_run_meta(run_dir, meta)
        except (OSError, ValueError):
            pass


def _ssh(host: str, port: int, command: str, *, timeout: int = 45) -> subprocess.CompletedProcess:
    args = cloud_manager._ssh_base_args(host, int(port), "root")  # noqa: SLF001
    return subprocess.run(
        args + [command], capture_output=True, text=True, timeout=timeout, check=False
    )


def _instance(api_key: str, instance_id: int) -> dict | None:
    """The instance as Vast reports it, or None when Vast says it does not exist.

    A per-instance lookup (the account-wide v0 listing answers 410 Gone); any
    provider error raises, so a failed lookup is never read as "gone".
    """
    return cloud_manager.observe_vast_instance(api_key, int(instance_id))


def reconcile_jobs() -> None:
    """Recover monitoring after restart without ever renting another GPU."""
    from . import checkpoint_prepare, vault

    api_key = vault.get('vault:vast_api_key')
    if not api_key:
        return
    active = []
    for path in _job_dir().glob('auto_*.json'):
        try:
            job = json.loads(path.read_text(encoding='utf-8'))
            if job.get('state') in {'queued', 'renting', 'waiting_ssh', 'provisioning', 'starting', 'running'}:
                active.append(job)
        except (OSError, ValueError):
            continue
    if not active:
        return
    for job in active:
        instance_id = job.get('instance_id')
        run_id = job.get('run_id')
        project_id = int(job['project_id'])
        gone = not instance_id
        if instance_id:
            try:
                gone = _instance(api_key, int(instance_id)) is None
            except Exception:
                # Unavailable provider is not proof that a resource was deleted:
                # leave the job as it is and look again at the next start.
                continue
        if gone:
            _update(job, state='failed', phase='prepare' if run_id else 'failed',
                    instance_destroyed=bool(instance_id),
                    cost_unknown=bool(instance_id), last_observed_at=job.get('updated_at'),
                    message='remote instance unavailable; local checkpoints are preserved',
                    error='The backend restarted and the remote instance is no longer available.')
            if run_id:
                try:
                    checkpoint_prepare._job_status(project_id, str(run_id))
                    run_dir, meta = trainer._load_run(project_id, str(run_id))
                    meta['remote_cleaned_at'] = _now()
                    trainer.write_run_meta(run_dir, meta)
                except (OSError, ValueError):
                    pass
            continue
        if not run_id:
            # Provisioning configuration is not persisted: never guess it or
            # leave an orphaned rental charged after a process restart.
            try:
                cloud_manager.control_vast_instance(api_key, int(instance_id), 'delete')
                deleted = _instance(api_key, int(instance_id)) is None
            except Exception:
                deleted = False
            _update(job, state='failed', phase='failed', instance_destroyed=deleted,
                    message='provisioning interrupted by backend restart',
                    error='Retry preparation. Verify the provider resource if cleanup is not confirmed.')
            continue
        try:
            _, meta = trainer._load_run(project_id, str(run_id))
            if meta.get('state') == 'finished' and (meta.get('prepare_job') or {}).get('state') in {'running', 'queued', 'starting'}:
                checkpoint_prepare._job_status(project_id, str(run_id))
                checkpoint_prepare.start_prepare(project_id, str(run_id))
            event = threading.Event()
            thread = threading.Thread(target=_monitor_run,
                args=(job, api_key, project_id, str(run_id), int(instance_id), event),
                name=f"resume-{job['id']}", daemon=True)
            with _LOCK:
                _CANCEL[job['id']] = event
                _THREADS[job['id']] = thread
            thread.start()
        except (OSError, ValueError):
            continue


def _destroy(api_key: str, instance_id: int | None) -> None:
    if not instance_id:
        return
    cloud_manager.control_vast_instance(api_key, int(instance_id), "delete")
    for _ in range(3):
        remaining = _instance(api_key, int(instance_id))
        if remaining is None or str(remaining.get('status') or '').lower() in {'deleted', 'destroyed', 'terminated'}:
            return
        time.sleep(2)
    raise RuntimeError('GPU deletion could not be confirmed; check the Vast.ai instance before leaving.')


def _active_for_project(project_id: int) -> dict | None:
    for path in _job_dir().glob("auto_*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if int(job.get("project_id") or 0) == int(project_id) and job.get("state") in {
            "queued", "renting", "waiting_ssh", "provisioning", "starting", "running",
        }:
            return job
    return None


def start_auto(
    project_id: int,
    cfg: dict,
    api_key: str,
    *,
    owner_id: int | None = None,
    lang: str = "it",
    max_dph: float = 1.0,
    min_gpu_ram_gb: int = 24,
    disk_gb: int = 100,
    gpu_name: str = "",
    allow_stale_dataset: bool = False,
    datacenter_only: bool = False,
    verified_only: bool = False,
    gold_eval_enabled: bool = True,
    gold_eval_max_minutes: int | None = gold_eval.DEFAULT_MAX_MINUTES,
) -> dict:
    """Noleggia e prepara in background; ritorna subito il job.

    `gpu_name` pins the search to one Vast GPU model (e.g. "A100 SXM4").
    A snapshot older than the latest approval or edit of its eligible pages
    (`dataset.stale` in the preflight) is refused unless `allow_stale_dataset`.
    `datacenter_only` rents only Vast.ai datacenter (Secure Cloud) hosts, and
    fails with an explicit message rather than fall back to community hosts.
    `verified_only` rents only hosts Vast marks verified (the `verified` filter).
    `gold_eval_enabled` scores the merged checkpoint on the project's gold tables on
    the training GPU before it is released (`services/gold_eval.py`), within
    `gold_eval_max_minutes`; the job records why when it does not (`gold_eval`).
    """
    adapter_id = str(cfg.get("adapter_id") or "").strip()
    recipe = train_recipes.get_recipe(adapter_id)
    if recipe is None:
        raise ValueError("modello senza ricetta di training")
    profile = profile_for(adapter_id)
    if profile is None:
        raise ValueError(
            f"il training automatico non conosce ancora l'ambiente di {recipe.display_name}; "
            "usa SSH e il provisioning manuale"
        )
    if not recipe.trainable or not recipe.script_ready:
        raise ValueError(f"{recipe.display_name} non è ancora addestrabile da Tabularium")
    train_type = str(cfg.get("train_type") or (recipe.train_types[0] if recipe.train_types else "lora"))
    if train_type not in recipe.train_types:
        raise ValueError(
            f"{recipe.display_name} non supporta {train_type}: scegli "
            + ", ".join(recipe.train_types)
        )
    if not api_key.strip():
        raise ValueError("API Key di Vast.ai non configurata")
    running = _active_for_project(int(project_id))
    if running is not None:
        raise ValueError(
            f"c'è già un avvio automatico in corso ({running.get('message') or running.get('phase')}): "
            "annullalo prima di noleggiare un'altra GPU"
        )
    try:
        source, _snapshot = trainer._dataset_source(int(project_id))  # noqa: SLF001
    except FileNotFoundError as exc:
        raise ValueError("costruisci prima il dataset di training (Dataset → Costruisci)") from exc
    if not any(source.glob("*_train.jsonl")) or not any(source.glob("*_val.jsonl")):
        raise ValueError("il dataset non contiene ancora una famiglia train/val: costruiscilo prima")
    coverage = trainer.preflight(project_id, {**cfg, 'executor': 'vast'}, lang=lang)
    data_errors = coverage.get('dataset_coverage_errors') or []
    if data_errors:
        raise ValueError('; '.join(data_errors))
    stale = (coverage.get('dataset') or {}).get('stale')
    if stale and not allow_stale_dataset:
        raise ValueError(msg(
            'dataset_stale', i18n.SOURCE, built=stale['built_at'], snapshot=stale['snapshot_pages'],
            eligible=stale['eligible_pages'], changed=stale['changed_pages'],
        ))
    gold = gold_eval.plan(int(project_id), adapter_id, enabled=bool(gold_eval_enabled),
                          max_minutes=gold_eval_max_minutes)
    job_id = f"auto_{int(time.time() * 1000)}"
    job = {
        "id": job_id,
        "project_id": int(project_id),
        "adapter_id": adapter_id,
        "profile": profile,
        "state": "queued",
        "phase": "rent",
        "message": "cerco una GPU adatta…",
        "error": None,
        "log": "",
        "offer": None,
        "instance_id": None,
        "host": None,
        "port": None,
        "run_id": None,
        "instance_destroyed": False,
        "gpu_name": str(gpu_name or "").strip() or None,
        "datacenter_only": bool(datacenter_only),
        "verified_only": bool(verified_only),
        "dataset_snapshot": (coverage.get('dataset') or {}).get('snapshot_id'),
        "dataset_stale": stale or None,
        "gold_eval": gold,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _save(job)
    event = threading.Event()
    with _LOCK:
        _CANCEL[job_id] = event
        thread = threading.Thread(
            target=_run,
            args=(job, dict(cfg), api_key, profile, owner_id, lang, max_dph, int(min_gpu_ram_gb), int(disk_gb), event),
            kwargs={
                "gpu_name": str(gpu_name or "").strip(),
                "datacenter_only": bool(datacenter_only),
                "verified_only": bool(verified_only),
                "gold": gold,
            },
            name=f"auto-{job_id}",
            daemon=True,
        )
        _THREADS[job_id] = thread
        thread.start()
    return job


def cancel_auto(job_id: str, api_key: str = "") -> dict:
    event = _CANCEL.get(job_id)
    if event is not None:
        event.set()
    job = get_job(job_id)
    if job.get("run_id"):
        try:
            trainer.stop_run(int(job["project_id"]))
        except Exception:  # noqa: BLE001
            pass
    elif api_key:
        _destroy(api_key, job.get("instance_id"))
        _update(job, instance_destroyed=True, message="istanza eliminata")
    _update(job, state="cancelled", phase="cancelled", message="annullato")
    return job


def _training_log_tail(run_dir: Path, lines: int = 120) -> str:
    try:
        text = (run_dir / "train.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])[-4000:]


def _monitor_run(
    job: dict,
    api_key: str,
    project_id: int,
    run_id: str,
    instance_id: int,
    cancel: threading.Event,
) -> None:
    """Segue la run fino al modello registrato e spegne la GPU.

    Senza questo, un training fallito lasciava l'istanza accesa a $0.7/h e il job
    diceva per sempre «training avviato». Il monitor chiude il cerchio:
    training → preparazione automatica → registrazione → destroy.
    """
    deadline = time.time() + 12 * 60 * 60
    prepare_wait_deadline = 0.0
    while time.time() < deadline:
        if cancel.is_set():
            _update(job, state="cancelled", phase="cancelled", message="annullato")
            _destroy(api_key, instance_id)
            _update(job, instance_destroyed=True)
            return
        try:
            run_dir, meta = trainer._load_run(project_id, run_id)  # noqa: SLF001
        except (OSError, FileNotFoundError, ValueError) as exc:
            _update(job, message=f"run non leggibile: {str(exc)[:160]}")
            time.sleep(15)
            continue
        state = str(meta.get("state") or "")
        if state in {"failed", "stopped"}:
            err = _training_log_tail(run_dir)
            _update(
                job,
                state="failed",
                phase="failed",
                message="training fallito; istanza eliminata",
                error=(meta.get("error") or err or f"training {state}")[-4000:],
            )
            _destroy(api_key, instance_id)
            _update(job, instance_destroyed=True)
            return
        if state == "finished":
            prepare = meta.get("prepare_job") or {}
            pstate = str(prepare.get("state") or "")
            if pstate == "finished":
                _update(
                    job,
                    state="finished",
                    phase="done",
                    message="modello preparato e registrato; GPU eliminata",
                    model_id=prepare.get("model_id"),
                )
                _destroy(api_key, instance_id)
                _update(job, instance_destroyed=True)
                return
            if pstate == "failed":
                _update(
                    job,
                    state="failed",
                    phase="prepare",
                    message="preparazione del checkpoint fallita; GPU eliminata",
                    error=(prepare.get("error") or "preparazione fallita")[-4000:],
                )
                _destroy(api_key, instance_id)
                _update(job, instance_destroyed=True)
                return
            if pstate in {"running", "queued", "starting"}:
                prepare_wait_deadline = 0.0
                if prepare.get("phase") == "gold_eval":
                    _update(job, phase="gold_eval", message="training completed; reading the gold set on this GPU…")
                else:
                    _update(job, phase="prepare", message="training completato; preparo il modello…")
            else:
                # auto_prepare non è ancora partito: dagliene il tempo, poi
                # chiudi comunque per non lasciare la GPU accesa.
                if prepare_wait_deadline == 0.0:
                    prepare_wait_deadline = time.time() + 10 * 60
                if time.time() > prepare_wait_deadline:
                    _update(
                        job,
                        state="failed",
                        phase="prepare",
                        message="training completato; preparazione automatica non avviata",
                        error="la preparazione automatica non è partita: il pannello mostra il piano locale",
                    )
                    _destroy(api_key, instance_id)
                    _update(job, instance_destroyed=True)
                    return
                _update(job, phase="prepare", message="training completato; attendo la preparazione…")
        else:
            _update(job, message="training in corso…")
        time.sleep(15)
    _update(job, state="failed", phase="timeout", message="timeout del monitor; GPU eliminata")
    _destroy(api_key, instance_id)
    _update(job, instance_destroyed=True)


# A host whose container never starts is replaced instead of waited on, a bounded
# number of times: each replacement is a new rental, so a run of bad hosts costs at
# most this many short rentals. A machine that failed (or whose job was cancelled
# before training started) is skipped for `FAILED_HOST_TTL_S`, by machine id:
# Vast lists one offer per GPU slot, so on prod a retry picked offer 29019280 on
# the same broken machine as offer 29019285.
MAX_HOST_ATTEMPTS = 3
FAILED_HOST_TTL_S = 24 * 60 * 60
SSH_WAIT_S = 60 * 60
SSH_POLL_S = 8


class HostProvisioningFailed(RuntimeError):
    """The rented instance will never start: replace the host."""

    def __init__(self, reason: str, machine_id: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.machine_id = machine_id


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _recently_failed_hosts(job: dict) -> tuple[set[int], set[int]]:
    """Offer ids and machine ids to skip, from the jobs of the last 24 hours.

    A host recorded in `failed_hosts` (its container never started) is skipped for
    every project. The offer of a job of the same project and model that failed or
    was cancelled before a training run existed is skipped too, with its machine:
    that is where a slow or broken host shows. Completed training runs do not
    exclude their hosts.
    """
    offers: set[int] = set()
    machines: set[int] = set()
    for previous_path in _job_dir().glob('auto_*.json'):
        try:
            if previous_path.stat().st_mtime < time.time() - FAILED_HOST_TTL_S:
                continue
            previous = json.loads(previous_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            continue
        if previous.get('id') == job.get('id'):
            continue
        for failed in previous.get('failed_hosts') or []:
            if not isinstance(failed, dict):
                continue
            for target, key in ((offers, 'offer_id'), (machines, 'machine_id')):
                value = _int_or_none(failed.get(key))
                if value is not None:
                    target.add(value)
        if (previous.get('project_id') == job['project_id']
                and previous.get('adapter_id') == job['adapter_id']
                and previous.get('state') in {'failed', 'cancelled'}
                and not previous.get('run_id')):
            offer = previous.get('offer') or {}
            for target, key in ((offers, 'id'), (machines, 'machine_id')):
                value = _int_or_none(offer.get(key))
                if value is not None:
                    target.add(value)
    return offers, machines


def _offer_summary(offer: dict) -> dict:
    return {k: offer.get(k) for k in ("id", "machine_id", "gpu_name", "dph_total", "cuda_max_good")}


def _gold_environment(job: dict) -> dict | None:
    """The evaluation environment the rental must boot, when the job evaluates on gold."""
    if (job.get("gold_eval") or {}).get("state") != "planned":
        return None
    return gold_eval.environment(str(job.get("adapter_id") or ""))


def _image_for(profile: str, offer: dict, gold_env: dict | None = None) -> str:
    # The evaluation image is the training image plus the evaluation venv
    # (`docker/eval-xiaomi`): training runs from it unchanged.
    if gold_env:
        return str(gold_env["image"])
    if profile in PREBUILT_IMAGES:
        return PREBUILT_IMAGES[profile]
    if profile == "paddle":
        return PADDLE_IMAGE
    if float(offer.get("compute_cap") or 0) >= 1200 or float(offer.get("cuda_max_good") or 0) < 12.8:
        # Blackwell (sm_120) richiede CUDA >= 12.9; un driver più vecchio
        # di 12.8 non regge l'immagine stabile. Negli altri casi si usa
        # 12.8.1, già presente su molte macchine e più rapidamente avviabile
        # della 13.0.
        return cloud_manager.vast_image_for(offer.get("cuda_max_good"))
    return "vastai/pytorch:cuda-12.8.1-auto"


def _rent_best_offer(
    job: dict,
    api_key: str,
    profile: str,
    max_dph: float,
    min_gpu_ram_gb: int,
    disk_gb: int,
    cancel: threading.Event,
    *,
    gpu_name: str,
    datacenter_only: bool,
    verified_only: bool,
    excluded_offers: set[int],
    excluded_machines: set[int],
    failed_hosts: int,
) -> tuple[dict, dict]:
    """Search, skip excluded offers and machines, rent the best: (offer, rental)."""
    gold_env = _gold_environment(job)
    min_cuda = PREBUILT_CUDA[profile] if profile in PREBUILT_IMAGES else None
    if gold_env:
        min_cuda = max(float(min_cuda or 0), float(gold_env["min_cuda"]))
    offers = cloud_manager.search_vast_offers(
        api_key,
        gpu_name=gpu_name,
        num_gpus=1,
        max_dph=max_dph,
        min_reliability=0.95,
        instance_type="on-demand",
        disk_gb=disk_gb,
        min_gpu_ram_gb=min_gpu_ram_gb,
        min_compute_capability=8.0,
        min_cuda=min_cuda,
        blackwell_ready=blackwell_ready(profile),
        datacenter_only=datacenter_only,
        verified_only=verified_only,
        limit=50,
    )
    offers = [
        offer for offer in offers
        if _int_or_none(offer.get('id')) not in excluded_offers
        and _int_or_none(offer.get('machine_id')) not in excluded_machines
    ]

    def none_left(cause: Exception | None = None) -> RuntimeError:
        if failed_hosts:
            return RuntimeError(msg('vast_no_other_host', i18n.SOURCE, dph=f"{max_dph:.2f}", failed=failed_hosts))
        if datacenter_only:
            return RuntimeError(msg('vast_no_datacenter_offer', i18n.SOURCE, dph=f"{max_dph:.2f}", vram=min_gpu_ram_gb))
        if cause is not None:
            return RuntimeError("nessuna offerta disponibile entro il budget")
        if not offers:
            return RuntimeError(
                f"nessuna GPU Vast.ai entro ${max_dph:.2f}/h con almeno {min_gpu_ram_gb} GB; "
                "alza il budget o il minimo VRAM"
            )
        return RuntimeError(f"nessuna GPU Vast.ai entro ${max_dph:.2f}/h")

    best = best_offer(offers, max_dph) if offers else None
    if best is None:
        raise none_left()
    prebuilt = profile in PREBUILT_IMAGES
    image = _image_for(profile, best, gold_env)
    _update(
        job,
        phase="rent",
        message=f"noleggio {best.get('gpu_name') or 'GPU'} a ${float(best.get('dph_total') or 0):.3f}/h",
        offer=_offer_summary(best),
    )
    for attempt in range(5):
        if cancel.is_set():
            raise RuntimeError("annullato")
        try:
            rented = cloud_manager.rent_vast_instance(
                api_key,
                int(best["id"]),
                image=image,
                cuda_max_good=best.get("cuda_max_good"),
                compute_cap=best.get("compute_cap"),
                location=best.get("location"),
                cpu_arch=best.get("cpu_arch"),
                gpu_name=best.get("gpu_name"),
                hosting_type=best.get("hosting_type"),
                datacenter_only=datacenter_only,
                disk_gb=disk_gb,
                onstart_script="" if prebuilt else _onstart(profile, job["adapter_id"]),
            )
            return best, rented
        except RuntimeError as exc:
            # Retry only a definitive refusal: network timeouts may hide a
            # successful rental and must never create a second instance.
            if 'no_such_ask' not in str(exc) or attempt == 4:
                raise
            offers = [offer for offer in offers if offer.get('id') != best['id']]
            best = best_offer(offers, max_dph)
            if best is None:
                raise none_left(exc) from exc
            if not prebuilt and profile != 'paddle' and not gold_env:
                image = cloud_manager.vast_image_for(best.get('cuda_max_good'))
            _update(job, message="offerta non più disponibile; provo la successiva…", offer=_offer_summary(best))
    raise RuntimeError("nessuna offerta disponibile entro il budget")  # pragma: no cover - loop returns or raises


def _wait_for_ssh_endpoint(job: dict, api_key: str, instance_id: int, cancel: threading.Event) -> tuple[str, int]:
    """Poll the instance until Vast publishes SSH; raise `HostProvisioningFailed` when
    `cloud_manager.vast_provisioning_failure` says the container will never start."""
    _update(job, state="waiting_ssh", phase="ssh", message="attendo che l'istanza esponga SSH…")
    deadline = time.time() + SSH_WAIT_S
    status: str | None = None
    status_since = time.time()
    while time.time() < deadline:
        if cancel.is_set():
            raise RuntimeError("annullato")
        inst = _instance(api_key, int(instance_id))
        if inst:
            _update(job, provider_status=inst.get("status"), provider_message=inst.get("status_message"),
                    message=f"attendo l'istanza (stato {inst.get('status') or 'unknown'})…")
            current = str(inst.get("actual_status") or inst.get("status") or "").lower()
            if current != status:
                status, status_since = current, time.time()
            failure = cloud_manager.vast_provisioning_failure(inst, time.time() - status_since)
            if failure:
                raise HostProvisioningFailed(failure, _int_or_none(inst.get("machine_id")))
            # `ssh_host`/`ssh_port` sono già la coppia coerente scelta da
            # Vast; incrociare `ssh_direct_host` con la porta proxy crea un
            # endpoint che non esiste (→ ssh-keyscan fallisce per sempre).
            host = inst.get("ssh_host")
            port = inst.get("ssh_port")
            if not (host and port) and inst.get("ssh_direct_host") and inst.get("ssh_direct_port"):
                host, port = inst.get("ssh_direct_host"), inst.get("ssh_direct_port")
            if host and port and inst.get("is_running"):
                return str(host), int(port)
        time.sleep(SSH_POLL_S)
    raise RuntimeError("l'istanza non ha esposto SSH entro 60 minuti")


def _run(
    job: dict,
    cfg: dict,
    api_key: str,
    profile: str,
    owner_id: int | None,
    lang: str,
    max_dph: float,
    min_gpu_ram_gb: int,
    disk_gb: int,
    cancel: threading.Event,
    *,
    gpu_name: str = "",
    datacenter_only: bool = False,
    verified_only: bool = False,
    gold: dict | None = None,
) -> None:
    instance_id: int | None = None
    try:
        if cancel.is_set():
            raise RuntimeError("annullato")
        _update(job, state="renting", phase="rent", message="cerco una GPU adatta…")
        excluded_offers, excluded_machines = _recently_failed_hosts(job)
        prebuilt = profile in PREBUILT_IMAGES
        host = port = None
        attempt = 0
        while True:
            attempt += 1
            if cancel.is_set():
                raise RuntimeError("annullato")
            best, rented = _rent_best_offer(
                job, api_key, profile, max_dph, min_gpu_ram_gb, disk_gb, cancel,
                gpu_name=gpu_name, datacenter_only=datacenter_only, verified_only=verified_only,
                excluded_offers=excluded_offers, excluded_machines=excluded_machines,
                failed_hosts=attempt - 1,
            )
            instance_id = rented.get("contract_id")
            if instance_id is None:
                raise RuntimeError("Vast.ai non ha restituito l'istanza noleggiata")
            job["instance_id"] = int(instance_id)
            job["instance_destroyed"] = False
            _save(job)
            cloud_manager.track_cloud_resource(
                "vast",
                int(instance_id),
                owner_id=owner_id,
                hourly_rate=best.get("dph_total"),
                metadata={"offer_id": best.get("id"), "machine_id": best.get("machine_id"),
                          "purpose": "auto-training", "profile": profile},
            )
            try:
                host, port = _wait_for_ssh_endpoint(job, api_key, int(instance_id), cancel)
                break
            except HostProvisioningFailed as failure:
                machine_id = failure.machine_id if failure.machine_id is not None else _int_or_none(best.get("machine_id"))
                job.setdefault("failed_hosts", []).append({
                    "machine_id": machine_id,
                    "offer_id": best.get("id"),
                    "instance_id": int(instance_id),
                    "reason": failure.reason[:500],
                    "at": _now(),
                })
                if best.get("id") is not None:
                    excluded_offers.add(int(best["id"]))
                if machine_id is not None:
                    excluded_machines.add(int(machine_id))
                _update(job, message=failure.reason[:300])
                _destroy(api_key, int(instance_id))
                _update(job, instance_destroyed=True)
                instance_id = None
                if attempt >= MAX_HOST_ATTEMPTS:
                    raise RuntimeError(msg(
                        "vast_hosts_exhausted", i18n.SOURCE, total=attempt, reason=failure.reason[:300],
                    )) from failure
                _update(job, state="renting", phase="rent", message=msg(
                    "vast_host_replaced", i18n.SOURCE, machine=machine_id if machine_id is not None else "?",
                    reason=failure.reason[:300], attempt=attempt + 1, total=MAX_HOST_ATTEMPTS,
                ))
        _update(job, host=host, port=int(port), message="verifico la chiave SSH…")
        # Vast pubblica host/porta prima che sshd accetti connessioni: il pin
        # della host key va ritentato, altrimenti si distrugge un'istanza
        # perfettamente sana solo perché ssh-keyscan è arrivato troppo presto.
        access: dict = {"ok": False}
        ssh_deadline = time.time() + 10 * 60
        while time.time() < ssh_deadline:
            if cancel.is_set():
                raise RuntimeError("annullato")
            try:
                cloud_manager.pin_ssh_host_key(host, int(port))
            except Exception as exc:  # noqa: BLE001
                _update(job, message=f"SSH non ancora pronto: {str(exc)[:160]}")
                time.sleep(10)
                continue
            access = cloud_manager.check_ssh_access(host, int(port))
            if access.get("ok"):
                break
            _update(job, message=(access.get("message") or "SSH non ancora pronto")[:200])
            time.sleep(10)
        if not access.get("ok"):
            raise RuntimeError(access.get("message") or "SSH non raggiungibile dopo 10 minuti")
        if prebuilt:
            _update(job, state="provisioning", phase="env", message="avvio l'immagine prebuilt già pronta…")
            deadline = time.time() + 10 * 60
            paths = expected_paths(profile)
            last_error = ""
            while time.time() < deadline:
                if cancel.is_set():
                    raise RuntimeError("annullato")
                proc = _ssh(host, int(port), f"cat {PREBUILT_MARKER} 2>/dev/null || true")
                output = (proc.stdout or "").strip()
                if output.startswith("{"):
                    paths = {**paths, **json.loads(output)}
                    _update(job, state="starting", phase="train", log=output[-2000:], message="immagine prebuilt pronta")
                    break
                last_error = (proc.stderr or output or "ready.json non trovato").strip()[-500:]
                time.sleep(5)
            else:
                raise RuntimeError(f"immagine prebuilt non pronta entro 10 minuti: {last_error}")
        else:
            _update(job, state="provisioning", phase="env", message=f"installo l'ambiente {profile}…")
            ready_path = f"/root/tabularium-train/{profile}/ready.json"
            failed_path = f"/root/tabularium-train/{profile}/.failed"
            log_path = f"/root/tabularium-train/{profile}/setup.log"
            deadline = time.time() + 75 * 60
            paths = expected_paths(profile)
            last_log = ""
            while time.time() < deadline:
                if cancel.is_set():
                    raise RuntimeError("annullato")
                proc = _ssh(
                    host,
                    int(port),
                    f"if [ -f {failed_path} ]; then echo __FAILED__; tail -n 80 {log_path}; exit 3; fi; "
                    f"cat {ready_path} 2>/dev/null || tail -n 20 {log_path} 2>/dev/null || true",
                )
                output = (proc.stdout or "").strip()
                if output.startswith("{"):
                    paths = {**paths, **json.loads(output)}
                    break
                if "__FAILED__" in output or proc.returncode == 3:
                    detail = output.replace("__FAILED__", "").strip()[-1500:]
                    raise RuntimeError(f"installazione dell'ambiente fallita:\n{detail}")
                if output:
                    last_log = output[-2000:]
                    _update(job, log=last_log, message="installazione in corso…")
                time.sleep(10)
            else:
                raise RuntimeError(
                    "l'ambiente non è pronto entro 75 minuti. Ultime righe:\n" + (last_log or "nessun log")
                )
        if cancel.is_set():
            raise RuntimeError("annullato")
        _update(job, phase="train", message="avvio il training…")
        remote_cfg = {
            **cfg,
            "executor": "vast",
            "ssh_host": host,
            "ssh_port": int(port),
            "ssh_user": "root",
            "ssh_python": str(paths.get("python") or ""),
            "ssh_train_repo": str(paths.get("repo") or ""),
            "ssh_root": str(cfg.get("ssh_root") or "/tmp/tabularium-runs"),
            "auto_prepare": True,
            # I repo delle ricette sono HuggingFace; su Vast fuori dalla Cina
            # ModelScope è solo un mirror più lento (e il download del modello
            # può bloccarsi al primo step).
            "use_hf": True,
            "auto_evaluate": True,
            "lang": lang,
            # Una GPU sola: zero1/zero2 non partizionano nulla e aggiungono
            # solo un optimizer wrapper che può rompere lo scheduler. La run
            # manuale può ancora usare DeepSpeed; l'avvio automatico no.
            "disable_deepspeed": True,
            # Gold-set evaluation on this GPU, after the merge and before the download.
            "gold_eval": {
                "enabled": (gold or {}).get("state") == "planned",
                "max_minutes": (gold or {}).get("max_minutes") or gold_eval.DEFAULT_MAX_MINUTES,
            },
        }
        started = trainer.start_run(int(job["project_id"]), remote_cfg, owner_id=owner_id, lang=lang,
            on_run_created=lambda rid: _update(job, run_id=rid, phase='sync', message='transferring dataset and checkpoints to the GPU'))
        run_id = (started.get("run") or {}).get("run_id")
        if not run_id:
            recent = trainer.list_runs(int(job["project_id"]), limit=1)
            run_id = recent[0]["run_id"] if recent else None
        _update(
            job,
            state="running",
            phase="training",
            message="training avviato; alla fine preparo e registro il modello",
            run_id=run_id,
        )
        if run_id and instance_id is not None:
            _monitor_run(job, api_key, int(job["project_id"]), str(run_id), int(instance_id), cancel)
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if cancel.is_set():
            _update(job, state="cancelled", phase="cancelled", message="annullato")
        else:
            _update(job, state="failed", phase="failed", error=message, message=message[:300])
        if instance_id is not None:
            _destroy(api_key, instance_id)
            _update(job, instance_destroyed=True)
    finally:
        with _LOCK:
            _CANCEL.pop(job["id"], None)
            _THREADS.pop(job["id"], None)
