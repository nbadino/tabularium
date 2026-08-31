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
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import config
from . import local_runtime, vendor_repos
from .model_adapters import get_adapter
from .model_registry import is_installed, models_dir

_ACTIVE_PROC: subprocess.Popen | None = None
_ACTIVE_INFO: dict[str, Any] = {}


@dataclass
class ServeStatus:
    running: bool
    adapter_id: str | None = None
    port: int | None = None
    pid: int | None = None
    error: str | None = None


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


def get_status() -> ServeStatus:
    global _ACTIVE_PROC, _ACTIVE_INFO
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
        _ACTIVE_PROC = None
        _ACTIVE_INFO = {}
        return ServeStatus(running=False, adapter_id=adapter_id, error=error)
    return ServeStatus(running=False)


def start(adapter_id: str, port: int = 8888) -> ServeStatus:
    """Avvia il server locale per `adapter_id`, fermando prima quello attivo.

    Prepara da sé l'ambiente mancante (venv vLLM, checkout MonkeyOCRv2): può
    richiedere qualche minuto alla primissima chiamata per un dato adapter,
    è istantaneo alle successive.
    """
    global _ACTIVE_PROC, _ACTIVE_INFO

    adapter = get_adapter(adapter_id)  # ValueError se sconosciuto
    if not is_installed(adapter_id):
        raise ValueError(f"'{adapter_id}' non è installato: scaricalo prima di servirlo")

    model_path = str(models_dir(adapter_id))
    env = os.environ.copy()

    if adapter_id == "monkeyocrv2-parsing":
        # `scripts/serve_model.sh` legge queste due env var: se l'utente non
        # le ha già impostate (uso avanzato/ambiente esistente), le
        # prepariamo da sole. `env[...]` vale solo per questo sottoprocesso,
        # non tocca la config globale né sovrascrive un setup esistente.
        if not config.TRAIN_REPO:
            env["TABULARIUM_TRAIN_REPO"] = str(vendor_repos.ensure_monkeyocrv2_repo())
        if not config.TRAIN_PYTHON and not config.SERVE_PYTHON:
            local_runtime.ensure_ready()
            env["TABULARIUM_TRAIN_PYTHON"] = str(local_runtime.python_bin())

    argv = adapter.serve_command(model_path, port)
    if argv is None:
        raise ValueError(
            f"adapter '{adapter_id}' non ha ancora un comando di serving implementato"
        )

    if argv[0] == "vllm" and not config.SERVE_PYTHON and not shutil.which("vllm"):
        # Serve command generico (qualunque adapter con `vllm serve`,
        # compresi i modelli custom): prepara l'ambiente condiviso la prima
        # volta, senza alcun passaggio manuale.
        local_runtime.ensure_ready()

    stop()  # stop-before-start: un solo modello servito alla volta

    if config.SERVE_PYTHON:
        env["PATH"] = f"{Path(config.SERVE_PYTHON).parent}:{env.get('PATH', '')}"
    elif argv[0] == "vllm" and local_runtime.is_ready():
        env["PATH"] = f"{local_runtime.bin_dir()}:{env.get('PATH', '')}"

    log_file = _log_file(adapter_id)
    try:
        proc = subprocess.Popen(
            argv,
            stdout=log_file.open("wb"),
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"impossibile avviare il server: {exc}") from exc

    # Breve attesa per intercettare un fallimento immediato (argv non
    # eseguibile, porta occupata): l'utente lo vede senza dover interrogare
    # lo stato una seconda volta.
    time.sleep(0.6)
    if proc.poll() is not None:
        _ACTIVE_PROC = None
        _ACTIVE_INFO = {}
        raise RuntimeError(
            f"avvio server fallito, codice di uscita {proc.returncode} — v. {log_file}"
        )

    _ACTIVE_PROC = proc
    _ACTIVE_INFO = {"adapter_id": adapter_id, "port": port}
    return get_status()


def stop() -> ServeStatus:
    global _ACTIVE_PROC, _ACTIVE_INFO
    if _ACTIVE_PROC is not None:
        try:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                try:
                    os.killpg(os.getpgid(_ACTIVE_PROC.pid), signal.SIGTERM)
                except Exception:  # noqa: BLE001
                    _ACTIVE_PROC.terminate()
            else:
                _ACTIVE_PROC.terminate()
            _ACTIVE_PROC.wait(timeout=10.0)
        except Exception:  # noqa: BLE001
            try:
                _ACTIVE_PROC.kill()
            except Exception:  # noqa: BLE001
                pass
        _ACTIVE_PROC = None
    _ACTIVE_INFO = {}
    return get_status()
