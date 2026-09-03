"""Serving locale pluggable per adapter.

Ogni adapter descrive il proprio avvio con `serve_command(model_path, port)`
(v. `model_adapters.py`): un argv puro, senza assunzioni su come questo
modulo lo esegue. Un adapter con bisogni speciali (env dedicato, cwd) può
restituire l'invocazione di un proprio wrapper — MonkeyOCRv2Parsing delega a
`scripts/serve_model.sh` così — invece di richiedere logica speciale qui:
`serve_manager` resta lo stesso per qualunque adapter.

Stesso pattern già collaudato in `cloud_manager.py` per il tunnel SSH: un
solo processo attivo alla volta (una GPU consumer non ne regge due), stop
sempre prima di start, kill del gruppo di processi alla fermata.

Nessun passaggio manuale richiesto all'utente: se serve un ambiente vLLM e
non ce n'è uno già configurato (`TABULARIUM_SERVE_PYTHON`) o disponibile sul
`PATH`, `start()` lo prepara da sé (v. `local_runtime.py`); se l'adapter è
MonkeyOCRv2-Parsing e manca il checkout del repo ufficiale, lo clona da sé
(v. `vendor_repos.py`). Le variabili d'ambiente restano solo un override per
chi ha già un ambiente proprio, mai un requisito.
"""
from __future__ import annotations

import os
import json
import math
import re
import sqlite3
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config
from ..db import connect
from . import local_runtime, vendor_repos
from .model_adapters import get_adapter
from .model_registry import (
    ensure_draft,
    install_state,
    is_installed,
    mark_draft_unusable,
    models_dir,
)

_ACTIVE_PROC: subprocess.Popen | None = None
_ACTIVE_INFO: dict[str, Any] = {}
_STARTING_INFO: dict[str, Any] = {}
_STARTING_LOCK = threading.Lock()

# L'avvio è lungo (venv vLLM, clone del repo, pesi in GPU) e `start_async` lo
# porta fuori dalla richiesta HTTP: `_STARTING_INFO` è la prenotazione di quel
# lavoro — chi lo sta facendo, per quale porta, a che punto è. È l'unica fonte
# della fase; `GET /serve/status` la legge mentre il thread lavora, così chi
# guarda vede procedere qualcosa invece di un pulsante «Avvio…» fermo.
#
#: Le fasi che l'avvio attraversa. Non sono un conteggio «passo N di M»:
#: quali si presentino dipende da cosa manca davvero (repo, ambiente vLLM),
#: e annunciare cinque passi quando se ne faranno due sarebbe una finta.
PHASES = (
    "preparing",
    "preparing_repo",
    "preparing_runtime",
    "preparing_draft",
    "preparing_image",
    "launching",
    "loading",
    "ready",
)


@dataclass
class ServeStatus:
    running: bool
    starting: bool = False
    adapter_id: str | None = None
    port: int | None = None
    pid: int | None = None
    error: str | None = None
    phase: str | None = None
    log_tail: str = ""


def _set_phase(adapter_id: str, phase: str, error: str | None = None) -> None:
    """Annota a che punto è l'avvio in corso.

    Solo la prenotazione creata da `start_async` viene aggiornata: una `start()`
    chiamata direttamente (test, uso sincrono) non ha nessuno che la osserva
    mentre lavora, e non deve lasciare in giro uno stato «in avvio».
    """
    with _STARTING_LOCK:
        if _STARTING_INFO.get("adapter_id") == adapter_id:
            _STARTING_INFO["phase"] = phase
            _STARTING_INFO["phase_since"] = time.time()
            if error is not None:
                _STARTING_INFO["error"] = error


def probe_ready(port: int | None, timeout: float = 1.5) -> bool:
    """Il server risponde davvero sulla porta? Un PID vivo non basta: vLLM
    carica i pesi per minuti prima di accettare la prima richiesta, e dire
    «in servizio» in quella finestra è una bugia comoda."""
    if not port:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=timeout) as res:
            return 200 <= res.status < 500
    except urllib.error.HTTPError:
        return True  # risponde (401/404): il server è in piedi
    except Exception:  # noqa: BLE001
        return False


def progress(status: ServeStatus | None = None) -> dict[str, Any]:
    """Cosa sta facendo l'avvio adesso, in una forma mostrabile.

    Combina la fase dichiarata da `start()` con la realtà osservata: processo
    vivo, endpoint che risponde. Il log mostrato è quello della fase in corso
    (installazione dell'ambiente, oppure il server stesso).
    """
    st = status if status is not None else get_status()
    with _STARTING_LOCK:
        booked = dict(_STARTING_INFO)
    adapter_id = st.adapter_id or booked.get("adapter_id")
    error = st.error or booked.get("error")

    if st.running:
        # Un PID vivo non basta: vLLM carica i pesi per minuti prima di
        # rispondere, e chiamarlo «in servizio» in quella finestra è falso.
        phase = "ready" if probe_ready(st.port) else "loading"
    elif booked.get("phase") == "failed" or (error and not st.starting):
        phase = "failed"
    elif st.starting:
        phase = booked.get("phase") or "launching"
    else:
        phase = "idle"

    if phase == "preparing_runtime":
        tail = local_runtime.log_tail(2000)
    elif adapter_id:
        tail = log_tail(adapter_id, 2000)
    else:
        tail = ""

    started_at = booked.get("started_at")
    return {
        "phase": phase,
        "adapter_id": adapter_id,
        "elapsed_s": round(time.time() - started_at, 1) if started_at else None,
        "error": error,
        "log_tail": tail,
    }


def _log_file(adapter_id: str) -> Path:
    return models_dir(adapter_id) / ".serve.log"


def log_tail(adapter_id: str, n: int = 4000) -> str:
    log_file = _log_file(adapter_id)
    if not log_file.exists():
        return ""
    try:
        with log_file.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _persisted_running() -> dict[str, Any] | None:
    """Recupera un server locale sopravvissuto al riavvio del backend."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE kind='serve' AND state='running' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    pid = int(row["pid"] or 0)
    try:
        if pid <= 0:
            raise OSError("PID server assente")
        os.kill(pid, 0)
    except OSError:
        try:
            with connect() as conn:
                conn.execute(
                    "UPDATE jobs SET state='failed', ended_at=datetime('now'), "
                    "error=?, heartbeat_at=datetime('now') WHERE id=?",
                    ("processo server non più presente dopo il riavvio", row["id"]),
                )
        except sqlite3.OperationalError:
            pass
        return None
    try:
        command = json.loads(row["command_json"] or "{}")
    except (TypeError, ValueError):
        command = {}
    return {
        "id": row["id"],
        "pid": pid,
        "process_group": int(row["process_group"] or pid),
        "adapter_id": command.get("adapter_id"),
        "port": command.get("port"),
        "log_path": row["log_path"],
    }


def _mark_job(job_id: int | None, state: str, error: str | None = None, exit_code: int | None = None) -> None:
    if job_id is None:
        return
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE jobs SET state=?, ended_at=datetime('now'), exit_code=?, error=?, "
                "heartbeat_at=datetime('now') WHERE id=?",
                (state, exit_code, error, job_id),
            )
    except sqlite3.OperationalError:
        pass


def get_status() -> ServeStatus:
    global _ACTIVE_PROC, _ACTIVE_INFO
    with _STARTING_LOCK:
        starting = dict(_STARTING_INFO)
    if starting:
        adapter_id = starting.get("adapter_id")
        return ServeStatus(
            running=False,
            starting=starting.get("phase") != "failed",
            adapter_id=adapter_id,
            port=starting.get("port"),
            error=starting.get("error"),
            phase=starting.get("phase", "preparing"),
            log_tail=(local_runtime.log_tail() if starting.get("phase") == "preparing" else log_tail(adapter_id or "")),
        )
    if _ACTIVE_PROC is not None:
        code = _ACTIVE_PROC.poll()
        if code is None:
            return ServeStatus(
                running=True,
                adapter_id=_ACTIVE_INFO.get("adapter_id"),
                port=_ACTIVE_INFO.get("port"),
                pid=_ACTIVE_PROC.pid,
            )
        # Processo terminato da solo (crash, OOM, ecc.): non resta "in ascolto".
        adapter_id = _ACTIVE_INFO.get("adapter_id")
        error = _ACTIVE_INFO.get("last_error") or f"processo terminato con codice {code}"
        _mark_job(_ACTIVE_INFO.get("job_id"), "failed", error, code)
        _ACTIVE_PROC = None
        _ACTIVE_INFO = {}
        return ServeStatus(running=False, adapter_id=adapter_id, error=error)
    persisted = _persisted_running()
    if persisted is not None:
        return ServeStatus(
            running=True,
            adapter_id=persisted.get("adapter_id"),
            port=persisted.get("port"),
            pid=persisted["pid"],
        )
    return ServeStatus(running=False)


def start_async(adapter_id: str, port: int = 8888, owner_id: int | None = None) -> ServeStatus:
    """Prenota l'avvio e prepara vLLM fuori dalla richiesta HTTP."""
    global _STARTING_INFO
    get_adapter(adapter_id)
    if not is_installed(adapter_id):
        raise ValueError(f"'{adapter_id}' non è installato: scaricalo prima di servirlo")
    with _STARTING_LOCK:
        if _STARTING_INFO:
            if _STARTING_INFO.get("phase") == "failed":
                _STARTING_INFO = {}
            elif _STARTING_INFO.get("adapter_id") == adapter_id:
                return ServeStatus(
                    running=False, starting=True, adapter_id=adapter_id,
                    port=port, phase=_STARTING_INFO.get("phase", "preparing"),
                )
            else:
                raise RuntimeError("un altro server locale è già in fase di avvio")
        _STARTING_INFO = {
            "adapter_id": adapter_id,
            "port": port,
            "phase": "preparing",
            "started_at": time.time(),
        }

    def worker() -> None:
        global _STARTING_INFO
        attempt_dflash: bool | None = None
        while True:
            try:
                start(adapter_id, port=port, owner_id=owner_id, dflash=attempt_dflash)
            except Exception as exc:  # noqa: BLE001
                with _STARTING_LOCK:
                    _STARTING_INFO["phase"] = "failed"
                    _STARTING_INFO["error"] = str(exc)
                return
            used_dflash = bool(_ACTIVE_INFO.get("dflash"))
            with _STARTING_LOCK:
                _STARTING_INFO = {}
            if not used_dflash or not _died_before_serving(port):
                return
            # Il draft entra nel budget di `--gpu-memory-utilization`, quindi su
            # una GPU stretta può lasciare alla cache KV meno di quanto serve per
            # `--max-model-len` (misurato su una RTX 4060 8 GB: 2.53 GiB
            # disponibili contro 3.09 richiesti a 24576 token, ValueError in
            # `_check_enough_kv_cache_memory`). DFlash è un'accelerazione: se non
            # entra si riparte senza, invece di lasciare l'utente senza modello.
            #
            # E lo si ricorda: senza marcatore ogni avvio successivo ripagherebbe
            # lo stesso tentativo fallito (~90 s di caricamento pesi prima che
            # vLLM dimensioni la cache KV e si arrenda).
            mark_draft_unusable(
                adapter_id,
                "l'avvio con DFlash è terminato prima di servire: il draft non "
                "entra nel budget VRAM di questa macchina",
            )
            attempt_dflash = False
            with _STARTING_LOCK:
                _STARTING_INFO = {
                    "adapter_id": adapter_id,
                    "port": port,
                    "phase": "launching",
                    "started_at": time.time(),
                }

    threading.Thread(target=worker, name=f"serve-start-{adapter_id}", daemon=True).start()
    return ServeStatus(running=False, starting=True, adapter_id=adapter_id, port=port, phase="preparing")


# Margine osservato fra i pesi caricati e il budget effettivamente consumato
# (attivazioni + CUDA graph) su questo runtime vLLM, e cache KV minima perché il
# modello serva qualcosa di utile. Misurati su DeepSeek-OCR-2 / RTX 4060 8 GB:
# pesi 6.33 GiB, budget 6.80 GiB (0.85 x 8 GB), cache KV risultante -1.03 GiB;
# con `--cpu-offload-gb 3` i pesi su GPU scendono a 3.22 GiB e la cache KV sale
# a 2.08 GiB (36.368 token). Il rapporto è circa 1:1 fra GiB spostati e GiB di
# cache recuperati, da cui la formula sotto.
_SERVE_OVERHEAD_GIB = 1.5
_SERVE_MIN_KV_GIB = 2.0
_SERVE_UTILIZATION = 0.85


def cpu_offload_gib(weights_bytes: int | None) -> int:
    """GiB di pesi da tenere in RAM perché il modello entri in VRAM, o 0.

    `--cpu-offload-gb` di vLLM (backend UVA) mappa parte dei pesi su memoria
    CPU pinned e li legge in zero-copy a ogni forward. Costa banda PCIe, non
    qualità: è la differenza fra «non parte» e «parte più lentamente». Lo
    calcoliamo solo quando serve, così i modelli che entrano non pagano nulla.
    """
    if not weights_bytes:
        return 0
    try:
        from . import trainer_metrics

        gpus = trainer_metrics.gpu_snapshot()
    except Exception:  # noqa: BLE001
        return 0
    if not gpus:
        return 0
    # Capacità della scheda, non memoria libera adesso: `start()` ferma sempre
    # il server precedente prima di lanciare il nuovo (stop-before-start), e
    # `--gpu-memory-utilization` di vLLM è comunque una frazione del totale.
    # Misurare il libero qui darebbe un offload gonfiato dal modello ancora in
    # servizio, che sta per essere fermato.
    total_gib = max(g["memory_total"] for g in gpus) / 1024
    weights_gib = weights_bytes / (1024 ** 3)
    budget_gib = total_gib * _SERVE_UTILIZATION
    deficit = weights_gib + _SERVE_OVERHEAD_GIB + _SERVE_MIN_KV_GIB - budget_gib
    if deficit <= 0:
        return 0
    offload = math.ceil(deficit)
    # Oltre i pesi stessi non ha senso: se non basta, il modello non è per
    # questa macchina e l'avviso VRAM del registro lo dice già.
    return min(offload, int(weights_gib))


def docker_gpu_blocker() -> str | None:
    """Motivo per cui un adapter servito via Docker non può partire, o `None`.

    Il fallimento tipico è illeggibile: `docker run --gpus all` esce con codice
    125 e «could not select device driver "" with capabilities: [[gpu]]».
    Succede quando il runtime `nvidia` è dichiarato in `daemon.json` ma il
    binario di NVIDIA Container Toolkit non è installato — situazione comune
    dopo un aggiornamento di sistema. Meglio dirlo prima di spendere l'avvio.
    """
    if shutil.which("docker") is None:
        return "docker_missing"
    if shutil.which("nvidia-container-runtime") is None:
        return "nvidia_container_toolkit_missing"
    return None


def _port_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _port_holder(port: int) -> str | None:
    """PID che tiene la porta, quando `ss` è disponibile: serve all'utente per
    chiuderlo, non a noi per deciderlo."""
    try:
        out = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:  # noqa: BLE001
        return None
    match = re.search(r"pid=(\d+)", out.stdout or "")
    return match.group(1) if match else None


def _is_our_serving_process(pid: str | int) -> bool:
    """Vero se il processo sulla porta è un server di inferenza nostro.

    L'attribuzione guarda **solo** la riga di comando viva: `vllm serve`, il
    wrapper `serve.py` del repo MonkeyOCRv2, il `docker run` sull'immagine di un
    adapter, o comunque un comando che contiene la cartella dei modelli. Non ci
    si fida della tabella `jobs`: i numeri di PID vengono riciclati, e un PID
    registrato mesi fa può appartenere oggi a un processo qualunque dell'utente.
    Tutto il resto — un Jupyter, un server dell'utente — non si tocca.
    """
    pid = int(pid)
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        return False
    if str(config.MODELS_DIR) in cmdline:
        return True
    markers = ("vllm serve", "serve.py", "vllm/vllm-openai")
    return any(marker in cmdline for marker in markers)


def _reclaim_port(port: int, settle: float = 10.0) -> str | None:
    """Libera la porta prima del lancio. Ritorna un motivo solo se non ci riesce.

    Due casi, entrambi da gestire senza chiedere niente all'utente:
    il processo appena fermato che sta ancora chiudendo il socket (si aspetta),
    e un server nostro rimasto orfano da una sessione finita male (si termina).
    Un processo che non è nostro non viene toccato: quello sì che va detto.
    """
    deadline = time.time() + settle
    while time.time() < deadline:
        if not _port_in_use(port):
            return None
        time.sleep(0.5)

    holder = _port_holder(port)
    if holder is None:
        return (
            f"la porta {port} risulta occupata ma non si riesce a identificare il "
            "processo che la tiene: liberala e riprova."
        )
    if not _is_our_serving_process(holder):
        return (
            f"la porta {port} è occupata dal processo {holder}, che non è un server "
            "di inferenza avviato da Tabularium: non lo termino di iniziativa. "
            "Chiudilo, oppure configura un'altra porta."
        )
    # Residuo nostro: lo chiudiamo noi, è esattamente il lavoro dello
    # stop-before-start quando il registro dei job ha perso le sue tracce.
    _terminate(int(holder))
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not _port_in_use(port):
            return None
        time.sleep(0.5)
    return (
        f"la porta {port} resta occupata dal processo {holder} anche dopo averlo "
        "terminato: liberala e riprova."
    )


def _docker_image_present(image: str) -> bool:
    try:
        return subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=30,
        ).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _docker_pull(image: str, adapter_id: str) -> None:
    """Scarica l'immagine registrando l'avanzamento nel log del serving.

    Un fallimento non alza: `docker run` riproverà da sé e il suo errore è più
    informativo del nostro (registry irraggiungibile, spazio disco, permessi).
    """
    log_file = _log_file(adapter_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_file.open("wb") as fh:
            subprocess.run(
                ["docker", "pull", image],
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=3600,
            )
    except Exception:  # noqa: BLE001
        pass


def _died_before_serving(port: int, deadline: float = 900.0) -> bool:
    """Vero se il processo appena avviato è uscito senza mai rispondere.

    Distingue il fallimento tardivo — vLLM esce durante il dimensionamento
    della cache KV, decine di secondi dopo il lancio, quindi troppo tardi per
    il controllo immediato in `start()` — dall'avvio lento ma riuscito.
    """
    started = time.time()
    while time.time() - started < deadline:
        proc = _ACTIVE_PROC
        if proc is None:
            return False  # fermato da qualcun altro: non è un fallimento nostro
        if proc.poll() is not None:
            return not probe_ready(port)
        if probe_ready(port):
            return False
        time.sleep(2.0)
    return False


def reconcile_jobs() -> None:
    """Marca come falliti i server locali non più presenti al riavvio."""
    _persisted_running()


def start(
    adapter_id: str,
    port: int = 8888,
    owner_id: int | None = None,
    dflash: bool | None = None,
) -> ServeStatus:
    """Avvia il server locale per `adapter_id`, fermando prima quello attivo.

    Prepara da sé l'ambiente mancante (venv vLLM, checkout MonkeyOCRv2): può
    richiedere qualche minuto alla primissima chiamata per un dato adapter,
    è istantaneo alle successive.

    `dflash=False` forza l'avvio senza speculative decoding anche quando è
    abilitato in configurazione: serve al riavvio di ripiego di `start_async()`
    quando il draft non entra nel budget VRAM.
    """
    global _ACTIVE_PROC, _ACTIVE_INFO
    use_dflash = config.MONKEY_DFLASH if dflash is None else bool(dflash)
    draft = None

    adapter = get_adapter(adapter_id)  # ValueError se sconosciuto
    if not is_installed(adapter_id):
        raise ValueError(f"'{adapter_id}' non è installato: scaricalo prima di servirlo")

    model_path = str(models_dir(adapter_id))
    env = os.environ.copy()
    _set_phase(adapter_id, "launching")

    try:
        if adapter_id == "monkeyocrv2-parsing":
            # `scripts/serve_model.sh` legge queste due env var: se l'utente non
            # le ha già impostate (uso avanzato/ambiente esistente), le
            # prepariamo da sole. `env[...]` vale solo per questo sottoprocesso,
            # non tocca la config globale né sovrascrive un setup esistente.
            if not config.TRAIN_REPO:
                _set_phase(adapter_id, "preparing_repo")
                env["TABULARIUM_TRAIN_REPO"] = str(vendor_repos.ensure_monkeyocrv2_repo())
            if not config.TRAIN_PYTHON and not config.SERVE_PYTHON:
                _set_phase(adapter_id, "preparing_runtime")
                local_runtime.ensure_ready()
                env["TABULARIUM_TRAIN_PYTHON"] = str(local_runtime.python_bin())
            if use_dflash:
                # DFlash ufficiale: `serve.py -d <draft>` (README §Document
                # Parsing / vLLM Serving). Il draft è ~170 MB, quindi lo
                # scarichiamo qui alla prima messa in servizio invece di
                # chiederlo all'utente. Se manca o il download fallisce,
                # `ensure_draft` ritorna None e si serve senza accelerazione:
                # è un'ottimizzazione, non un requisito.
                _set_phase(adapter_id, "preparing_draft")
                draft = ensure_draft(adapter_id)
                if draft is not None:
                    env["TABULARIUM_MONKEY_DFLASH_DRAFT"] = str(draft)

        argv = adapter.serve_command(model_path, port)
        if argv is None:
            raise ValueError(
                f"adapter '{adapter_id}' non ha ancora un comando di serving implementato"
            )

        if argv[0] == "vllm" and not config.SERVE_PYTHON and not shutil.which("vllm"):
            # Serve command generico (qualunque adapter con `vllm serve`,
            # compresi i modelli custom): prepara l'ambiente condiviso la prima
            # volta, senza alcun passaggio manuale.
            _set_phase(adapter_id, "preparing_runtime")
            local_runtime.ensure_ready()

        if argv[0] == "docker":
            blocker = docker_gpu_blocker()
            if blocker == "docker_missing":
                raise RuntimeError(
                    f"'{adapter_id}' si serve solo dentro l'immagine Docker ufficiale "
                    "(l'architettura non è nella wheel vLLM stabile), ma Docker non è "
                    "installato su questa macchina."
                )
            if blocker == "nvidia_container_toolkit_missing":
                raise RuntimeError(
                    f"'{adapter_id}' gira in un container con accesso alla GPU, ma "
                    "NVIDIA Container Toolkit non è installato: `docker run --gpus all` "
                    "fallisce con «could not select device driver». Installalo con "
                    "`sudo apt install nvidia-container-toolkit && sudo nvidia-ctk runtime "
                    "configure --runtime=docker && sudo systemctl restart docker`, "
                    "oppure servi il modello su GPU remota (Cloud)."
                )

        image = adapter.capabilities.serve_image
        if image and argv[0] == "docker":
            # L'immagine vLLM dedicata (Unlimited-OCR) pesa una decina di GB e
            # al primo avvio `docker run` la scarica in silenzio: senza questa
            # fase l'utente vedrebbe "launching" fermo per parecchi minuti,
            # senza sapere perché. La scarichiamo noi, dichiarandolo.
            if not _docker_image_present(image):
                _set_phase(adapter_id, "preparing_image")
                _docker_pull(image, adapter_id)

        if "--gpu-memory-utilization" in argv and "--cpu-offload-gb" not in argv:
            # Un checkpoint più grande della VRAM disponibile non parte affatto
            # ("No available memory for the cache blocks"). Spostare in RAM la
            # parte eccedente lo rende servibile: più lento, ma servibile — e
            # non tocca la qualità. Un adapter che dichiara già il proprio
            # `--cpu-offload-gb` decide da sé.
            #
            # La condizione è la presenza di `--gpu-memory-utilization`, non
            # `argv[0] == "vllm"`: vale anche per gli adapter che lanciano vLLM
            # dentro l'immagine Docker ufficiale (Unlimited-OCR), dove gli
            # argomenti dopo il nome immagine sono comunque quelli di
            # `vllm serve`. Il wrapper di MonkeyOCRv2 non espone quel flag
            # nell'argv e resta fuori, correttamente: entra in VRAM da solo.
            offload = cpu_offload_gib(install_state(adapter_id).get("size_bytes"))
            if offload:
                argv = [*argv, "--cpu-offload-gb", str(offload)]

        # FlashInfer può compilare kernel CUDA al primo avvio. Su Ubuntu il
        # compilatore predefinito può essere più nuovo di quello supportato
        # dalla CUDA installata (qui GCC 15 vs GCC 13): senza questi override
        # il processo termina con codice 1 prima di esporre /v1/models.
        if argv[0] == "vllm":
            if shutil.which("gcc-13"):
                env.setdefault("CC", "gcc-13")
            if shutil.which("g++-13"):
                env.setdefault("CXX", "g++-13")
                # CUDA 12.x/nvcc seleziona il compiler host tramite
                # NVCC_CCBIN, non tramite CC/CXX. Senza questo parametro usa
                # il gcc di sistema (GCC 15 su Ubuntu recente) e FlashInfer
                # fallisce durante la compilazione JIT.
                env.setdefault("NVCC_CCBIN", shutil.which("g++-13") or "g++-13")
            # Se il compiler CUDA supportato non è installato, nvcc deve poter
            # usare quello di sistema (su questa macchina GCC 15). Senza il
            # flag il primo kernel FlashInfer fallisce; i modelli già in cache
            # nascondono il problema, rendendo l'errore apparentemente
            # intermittente per l'utente.
            env.setdefault("NVCC_PREPEND_FLAGS", "-allow-unsupported-compiler")
    except Exception as exc:  # noqa: BLE001
        _set_phase(adapter_id, "failed", str(exc))
        raise

    _set_phase(adapter_id, "launching")
    stop()  # stop-before-start: un solo modello servito alla volta

    blocked = _reclaim_port(port)
    if blocked:
        _set_phase(adapter_id, "failed", blocked)
        raise RuntimeError(blocked)

    if config.SERVE_PYTHON:
        env["PATH"] = f"{Path(config.SERVE_PYTHON).parent}:{env.get('PATH', '')}"
    elif argv[0] == "vllm" and local_runtime.is_ready():
        env["PATH"] = f"{local_runtime.bin_dir()}:{env.get('PATH', '')}"

    log_file = _log_file(adapter_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=log_file.open("wb"),
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    except Exception as exc:  # noqa: BLE001
        _set_phase(adapter_id, "failed", str(exc))
        raise RuntimeError(f"impossibile avviare il server: {exc}") from exc

    # Breve attesa per intercettare un fallimento immediato (argv non
    # eseguibile, porta occupata): l'utente lo vede senza dover interrogare
    # lo stato una seconda volta.
    time.sleep(0.6)
    if proc.poll() is not None:
        _ACTIVE_PROC = None
        _ACTIVE_INFO = {}
        error = f"avvio server fallito, codice di uscita {proc.returncode} — v. {log_file}"
        _set_phase(adapter_id, "failed", error)
        raise RuntimeError(error)

    # Il processo è vivo ma non serve ancora: i pesi si caricano adesso.
    _set_phase(adapter_id, "loading")

    process_group = proc.pid
    if hasattr(os, "getpgid"):
        try:
            process_group = os.getpgid(proc.pid)
        except OSError:
            # I test usano processi finti senza un PID del sistema.
            process_group = proc.pid
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, "
                "heartbeat_at, command_json, log_path, recovery_strategy) "
                "VALUES('serve', ?, 'local', ?, ?, 'running', datetime('now'), ?, ?, 'pid-process-group')",
                (
                    owner_id,
                    proc.pid,
                    process_group,
                    json.dumps({"adapter_id": adapter_id, "port": port, "argv": argv}),
                    str(log_file),
                ),
            )
            job_id = int(cur.lastrowid)
    except sqlite3.OperationalError as exc:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("tabella jobs non disponibile: eseguire init_db()") from exc
    _ACTIVE_PROC = proc
    _ACTIVE_INFO = {
        "adapter_id": adapter_id,
        "port": port,
        "job_id": job_id,
        "dflash": draft is not None,
    }
    return get_status()


def _pid_alive(pid: int) -> bool:
    """Vero se il processo è ancora in esecuzione. Uno zombie non lo è.

    Un figlio terminato ma non ancora raccolto resta visibile a
    `os.kill(pid, 0)` finché qualcuno non ne legge lo stato di uscita: senza
    questo controllo l'attesa di `_terminate` scadeva sempre — il processo era
    morto ma il PID risultava «vivo».
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # esiste, ma non è nostro
    except Exception:  # noqa: BLE001
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        return stat.rsplit(")", 1)[-1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _terminate(pid: int, process_group: int | None = None, timeout: float = 20.0) -> bool:
    """SIGTERM, attesa reale, poi SIGKILL. Ritorna True se il processo è morto.

    L'attesa non è cosmetica: `stop()` viene chiamato da `start()` come
    *stop-before-start*, e vLLM impiega qualche secondo a chiudere il socket
    dopo il SIGTERM. Tornare subito significava lanciare il modello nuovo su
    una porta ancora occupata, e vederlo morire con
    `OSError: [Errno 98] Address already in use`.
    """
    group = process_group
    if group is None and hasattr(os, "getpgid"):
        try:
            group = os.getpgid(pid)
        except Exception:  # noqa: BLE001
            group = None

    def signal_all(sig: int) -> None:
        sent = False
        if group is not None and hasattr(os, "killpg"):
            try:
                os.killpg(group, sig)
                sent = True
            except Exception:  # noqa: BLE001
                sent = False
        if not sent:
            try:
                os.kill(pid, sig)
            except Exception:  # noqa: BLE001
                pass

    signal_all(signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.3)
    # Non si è chiuso con le buone: un solo modello per volta è un vincolo di
    # VRAM, non una preferenza.
    signal_all(getattr(signal, "SIGKILL", signal.SIGTERM))
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.3)
    return False


def stop() -> ServeStatus:
    global _ACTIVE_PROC, _ACTIVE_INFO
    proc = _ACTIVE_PROC
    job_id = _ACTIVE_INFO.get("job_id")
    persisted = None if proc is not None else _persisted_running()
    pid = proc.pid if proc is not None else (persisted or {}).get("pid")
    process_group = (persisted or {}).get("process_group") if proc is None else None
    if pid and not _is_our_serving_process(pid):
        # Non si segnala un processo che non si riesce ad attribuire. Il PID può
        # arrivare da una riga in `jobs` scritta prima di un riavvio della
        # macchina, e i numeri di PID vengono riciclati: senza questo controllo
        # un SIGKILL poteva finire su un processo qualsiasi dell'utente.
        # Marcare il job come fermo va bene lo stesso: il server non c'è più.
        _mark_job(job_id or (persisted or {}).get("id"), "stopped")
        _ACTIVE_PROC = None
        _ACTIVE_INFO = {}
        return get_status()
    if pid:
        _terminate(pid, process_group)
        if proc is not None:
            try:
                proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
        _mark_job(job_id or (persisted or {}).get("id"), "stopped")
        _ACTIVE_PROC = None
    _ACTIVE_INFO = {}
    return get_status()
