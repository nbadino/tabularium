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

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .. import config
from . import cloud_manager, train_recipes, trainer
from .trainer_script import _now

PROFILES = {
    "monkeyocrv2-parsing": "monkey",
    "qwen3-vl-8b": "qwen",
    "unlimited-ocr": "legacy",
    "deepseek-ocr": "legacy",
    "dots-ocr": "dots",
    "glm-ocr": "glm",
    "paddleocr-vl": "paddle",
}

PADDLE_IMAGE = "paddlepaddle/paddle:3.3.0-gpu-cuda12.6-cudnn9.5"
# Immagini prebuilt versionate: niente pip al noleggio. Il profilo monkey è il
# primo; gli altri restano sul provisioning runtime finché non hanno la loro.
PREBUILT_IMAGES = {
    "monkey": "ghcr.io/nbadino/tabularium-train-monkey:monkey-v3",
}
PREBUILT_MARKER = "/opt/tabularium/ready.json"
_LOCK = threading.Lock()
_CANCEL: dict[str, threading.Event] = {}
_THREADS: dict[str, threading.Thread] = {}


def profile_for(adapter_id: str) -> str | None:
    return PROFILES.get(str(adapter_id or ""))


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


def _onstart(profile: str, adapter_id: str) -> str:
    script = (config.RESOURCE_DIR / "scripts" / "cloud" / "setup_train_env.sh").read_text(encoding="utf-8")
    encoded = __import__("base64").b64encode(script.encode("utf-8")).decode("ascii")
    return (
        "mkdir -p /root/tabularium-train && "
        f"echo {shlex.quote(encoded)} | base64 -d > /tmp/setup_train_env.sh && "
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
    job.update(patch)
    job["updated_at"] = _now()
    _save(job)


def _ssh(host: str, port: int, command: str, *, timeout: int = 45) -> subprocess.CompletedProcess:
    args = cloud_manager._ssh_base_args(host, int(port), "root")  # noqa: SLF001
    return subprocess.run(
        args + [command], capture_output=True, text=True, timeout=timeout, check=False
    )


def _instance(api_key: str, instance_id: int) -> dict | None:
    for item in cloud_manager.list_vast_instances(api_key):
        if int(item.get("id") or 0) == int(instance_id):
            return item
    return None


def _destroy(api_key: str, instance_id: int | None) -> None:
    if not instance_id:
        return
    try:
        cloud_manager.control_vast_instance(api_key, int(instance_id), "delete")
    except Exception:  # noqa: BLE001
        pass


def _active_for_project(project_id: int) -> dict | None:
    for path in _job_dir().glob("auto_*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if int(job.get("project_id") or 0) == int(project_id) and job.get("state") in {
            "queued", "renting", "waiting_ssh", "provisioning", "starting",
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
) -> dict:
    """Noleggia e prepara in background; ritorna subito il job."""
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
                _update(job, phase="prepare", message="training completato; preparo il modello…")
            else:
                # auto_prepare non è ancora partito: dagliene il tempo, poi
                # chiudi comunque per non lasciare la GPU accesa.
                if prepare_wait_deadline == 0.0:
                    prepare_wait_deadline = time.time() + 10 * 60
                if time.time() > prepare_wait_deadline:
                    _update(
                        job,
                        state="finished",
                        phase="done",
                        message="training completato; preparazione manuale necessaria",
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
) -> None:
    instance_id: int | None = None
    try:
        if cancel.is_set():
            raise RuntimeError("annullato")
        _update(job, state="renting", phase="rent", message="cerco una GPU adatta…")
        offers = cloud_manager.search_vast_offers(
            api_key,
            num_gpus=1,
            max_dph=max_dph,
            min_reliability=0.95,
            instance_type="on-demand",
            disk_gb=disk_gb,
            min_gpu_ram_gb=min_gpu_ram_gb,
            min_compute_capability=8.0,
            min_cuda=(12.4 if profile in PREBUILT_IMAGES else None),
            limit=50,
        )
        if not offers:
            raise RuntimeError(
                f"nessuna GPU Vast.ai entro ${max_dph:.2f}/h con almeno {min_gpu_ram_gb} GB; "
                "alza il budget o il minimo VRAM"
            )
        best = best_offer(offers, max_dph)
        if best is None:
            raise RuntimeError(f"nessuna GPU Vast.ai entro ${max_dph:.2f}/h")
        prebuilt = profile in PREBUILT_IMAGES
        if prebuilt:
            image = PREBUILT_IMAGES[profile]
        elif profile == "paddle":
            image = PADDLE_IMAGE
        elif float(best.get("compute_cap") or 0) >= 1200 or float(best.get("cuda_max_good") or 0) < 12.8:
            # Blackwell (sm_120) richiede CUDA >= 12.9; un driver più vecchio
            # di 12.8 non regge l'immagine stabile. Negli altri casi si usa
            # 12.8.1, già presente su molte macchine e più rapidamente avviabile
            # della 13.0.
            image = cloud_manager.vast_image_for(best.get("cuda_max_good"))
        else:
            image = "vastai/pytorch:cuda-12.8.1-auto"
        _update(
            job,
            phase="rent",
            message=f"noleggio {best.get('gpu_name') or 'GPU'} a ${float(best.get('dph_total') or 0):.3f}/h",
            offer={k: best.get(k) for k in ("id", "gpu_name", "dph_total", "cuda_max_good")},
        )
        rented = cloud_manager.rent_vast_instance(
            api_key,
            int(best["id"]),
            image=image,
            cuda_max_good=best.get("cuda_max_good"),
            disk_gb=disk_gb,
            onstart_script="" if prebuilt else _onstart(profile, job["adapter_id"]),
        )
        instance_id = rented.get("contract_id")
        if instance_id is None:
            raise RuntimeError("Vast.ai non ha restituito l'istanza noleggiata")
        job["instance_id"] = int(instance_id)
        _save(job)
        cloud_manager.track_cloud_resource(
            "vast",
            int(instance_id),
            owner_id=owner_id,
            hourly_rate=best.get("dph_total"),
            metadata={"offer_id": best.get("id"), "purpose": "auto-training", "profile": profile},
        )

        _update(job, state="waiting_ssh", phase="ssh", message="attendo che l'istanza esponga SSH…")
        deadline = time.time() + 60 * 60
        host = port = None
        while time.time() < deadline:
            if cancel.is_set():
                raise RuntimeError("annullato")
            inst = _instance(api_key, int(instance_id))
            if inst:
                _update(job, message=f"attendo l'istanza (stato {inst.get('status') or 'unknown'})…")
                # `ssh_host`/`ssh_port` sono già la coppia coerente scelta da
                # Vast; incrociare `ssh_direct_host` con la porta proxy crea un
                # endpoint che non esiste (→ ssh-keyscan fallisce per sempre).
                host = inst.get("ssh_host")
                port = inst.get("ssh_port")
                if not (host and port) and inst.get("ssh_direct_host") and inst.get("ssh_direct_port"):
                    host, port = inst.get("ssh_direct_host"), inst.get("ssh_direct_port")
                if host and port and inst.get("is_running"):
                    break
            time.sleep(8)
        if not host or not port:
            raise RuntimeError("l'istanza non ha esposto SSH entro 60 minuti")
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
            # Una GPU sola: zero1/zero2 non partizionano nulla e aggiungono
            # solo un optimizer wrapper che può rompere lo scheduler. La run
            # manuale può ancora usare DeepSpeed; l'avvio automatico no.
            "disable_deepspeed": True,
        }
        started = trainer.start_run(int(job["project_id"]), remote_cfg, owner_id=owner_id, lang=lang)
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
        if instance_id is not None and not job.get("run_id"):
            _destroy(api_key, instance_id)
            _update(job, instance_destroyed=True)
    finally:
        with _LOCK:
            _CANCEL.pop(job["id"], None)
            _THREADS.pop(job["id"], None)
