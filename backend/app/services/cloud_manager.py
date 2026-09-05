"""Gestione Cloud & Tunnel SSH automatizzata direttamente da UI.

Permette all'utente di:
1. Avviare e fermare un tunnel SSH locale con 1 click (senza terminale).
2. Interagire con le API di Vast.ai e RunPod per elencare, avviare,
   mettere in pausa e gestire le istanze GPU direttamente dall'interfaccia.
"""
from __future__ import annotations

import os
import base64
import json
import re
import shlex
import signal
import socket
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .. import config
from ..db import connect
from . import process_probe

# Stato singleton in memoria per il tunnel SSH attivo
_ACTIVE_TUNNEL_PROC: subprocess.Popen | None = None
_ACTIVE_TUNNEL_INFO: dict[str, Any] = {}
_ACTIVE_TUNNEL_JOB_ID: int | None = None


@dataclass
class TunnelStatus:
    running: bool
    host: str | None = None
    port: int | None = None
    user: str | None = None
    local_port: int = 8888
    remote_port: int = 8888
    pid: int | None = None
    error: str | None = None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        # Un processo zombie accetta comunque kill(pid, 0), ma non può più
        # inoltrare alcuna connessione. Trattarlo come vivo lasciava il job
        # `ssh_tunnel` running per sempre e conservava un endpoint locale ormai
        # morto.
        if process_probe.is_zombie(pid):
            return False
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_ssh_process(pid: int) -> bool:
    """Evita di segnalare un PID riciclato dopo un riavvio del backend."""
    if os.name != "posix":
        return False
    command = process_probe.process_cmdline(pid)
    if command is None:
        return False
    return Path(command.split(" ", 1)[0]).name == "ssh" or " ssh " in f" {command} "


def _persist_tunnel_job(info: dict[str, Any], pid: int) -> int:
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state='stopped', ended_at=datetime('now'), heartbeat_at=datetime('now') "
            "WHERE kind='ssh_tunnel' AND state='running'"
        )
        cur = conn.execute(
            "INSERT INTO jobs(kind, owner_id, provider, pid, process_group, state, heartbeat_at, command_json, recovery_strategy) "
            "VALUES('ssh_tunnel', ?, 'ssh', ?, ?, 'running', datetime('now'), ?, 'pid-process-group')",
            (info.get("owner_id"), pid, os.getpgid(pid) if hasattr(os, "getpgid") else pid, json.dumps(info)),
        )
        return int(cur.lastrowid)


def _update_tunnel_job(job_id: int | None, state: str, error: str | None = None) -> None:
    if job_id is None:
        return
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET state=?, heartbeat_at=datetime('now'), ended_at=CASE WHEN ? IN ('stopped','failed') THEN datetime('now') ELSE ended_at END, error=? WHERE id=?",
            (state, state, error, job_id),
        )


def _persisted_tunnel() -> tuple[int, dict[str, Any]] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, pid, process_group, command_json FROM jobs "
            "WHERE kind='ssh_tunnel' AND state='running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    pid = int(row["pid"] or 0)
    if not _pid_alive(pid):
        _update_tunnel_job(int(row["id"]), "failed", "processo SSH non più presente")
        return None
    try:
        info = json.loads(row["command_json"] or "{}")
    except (TypeError, ValueError):
        _update_tunnel_job(int(row["id"]), "failed", "parametri tunnel corrotti")
        return None
    info["pid"] = pid
    info["process_group"] = int(row["process_group"] or pid)
    return int(row["id"]), info


def get_tunnel_status() -> TunnelStatus:
    """Restituisce lo stato attuale del tunnel SSH gestito dall'app."""
    global _ACTIVE_TUNNEL_PROC, _ACTIVE_TUNNEL_INFO, _ACTIVE_TUNNEL_JOB_ID
    if _ACTIVE_TUNNEL_PROC is not None:
        poll_res = _ACTIVE_TUNNEL_PROC.poll()
        if poll_res is None:
            return TunnelStatus(
                running=True,
                host=_ACTIVE_TUNNEL_INFO.get("host"),
                port=_ACTIVE_TUNNEL_INFO.get("port"),
                user=_ACTIVE_TUNNEL_INFO.get("user"),
                local_port=_ACTIVE_TUNNEL_INFO.get("local_port", 8888),
                remote_port=_ACTIVE_TUNNEL_INFO.get("remote_port", 8888),
                pid=_ACTIVE_TUNNEL_PROC.pid,
            )
        else:
            # Processo terminato
            _update_tunnel_job(_ACTIVE_TUNNEL_JOB_ID, "failed", f"Processo terminato con codice {poll_res}")
            _ACTIVE_TUNNEL_PROC = None
            _ACTIVE_TUNNEL_JOB_ID = None
            err = _ACTIVE_TUNNEL_INFO.get("last_error")
            return TunnelStatus(running=False, error=err or f"Processo terminato con codice {poll_res}")
    recovered = _persisted_tunnel()
    if recovered is not None:
        job_id, info = recovered
        _ACTIVE_TUNNEL_JOB_ID = job_id
        return TunnelStatus(
            running=True,
            host=info.get("host"),
            port=info.get("port"),
            user=info.get("user"),
            local_port=info.get("local_port", 8888),
            remote_port=info.get("remote_port", 8888),
            pid=info.get("pid"),
        )
    return TunnelStatus(running=False)


def start_ssh_tunnel(
    host: str,
    port: int,
    user: str = "root",
    key_path: str | None = None,
    local_port: int = 8888,
    remote_port: int = 8888,
    owner_id: int | None = None,
) -> TunnelStatus:
    """Avvia un tunnel SSH in background inoltrando 127.0.0.1:local_port a remote_port."""
    global _ACTIVE_TUNNEL_PROC, _ACTIVE_TUNNEL_INFO, _ACTIVE_TUNNEL_JOB_ID

    # Se c'è già un tunnel attivo, fermalo prima
    stop_ssh_tunnel()

    host = host.strip()
    if not host:
        raise ValueError("Host SSH non valido.")
    port = int(port)
    local_port = int(local_port)
    if local_port == 0:
        # Lascia scegliere al sistema una porta libera. Il frontend usa questa
        # modalità per non fallire quando 8888 è già occupata da un runtime
        # locale o da un tunnel precedente rimasto fuori dal nostro processo.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            local_port = int(probe.getsockname()[1])
    if not (0 < local_port < 65536):
        raise ValueError("Porta locale non valida.")
    remote_port = int(remote_port)
    if not (0 < remote_port < 65536):
        raise ValueError("Porta remota non valida.")

    # Argomenti del comando SSH
    cmd = [
        "ssh",
        # Alcune distribuzioni/container installano un ssh_config.d con
        # symlink non posseduti da root; OpenSSH rifiuta allora l'intera
        # configurazione prima ancora di leggere i nostri parametri. Il
        # tunnel usa solo opzioni esplicite, quindi ignorare il config globale
        # è sicuro e rende il collegamento riproducibile dopo un riavvio.
        "-F",
        "/dev/null",
        "-N",  # Non eseguire comandi remoti
        "-L",
        f"{local_port}:127.0.0.1:{remote_port}",
        "-p",
        str(port),
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={config.SSH_KNOWN_HOSTS}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=10",
    ]

    if key_path and Path(key_path).expanduser().exists():
        cmd.extend(["-i", str(Path(key_path).expanduser())])
    elif ssh_key_path().exists():
        # Senza chiave esplicita si usa la dedicata dell'app: è quella che il
        # provisioning attacca all'istanza. Ripiegare sulle chiavi personali
        # dell'utente funziona solo se lui stesso le ha autorizzate su Vast.
        cmd.extend(["-i", str(ssh_key_path())])
    else:
        # Se presente ~/.ssh/id_rsa o id_ed25519, usalo automaticamente
        for default_key in ["~/.ssh/id_rsa", "~/.ssh/id_ed25519", "~/.ssh/id_ecdsa"]:
            p = Path(default_key).expanduser()
            if p.exists():
                cmd.extend(["-i", str(p)])
                break

    target = f"{user}@{host}"
    cmd.append(target)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
    except Exception as exc:
        raise RuntimeError(f"Impossibile avviare il comando SSH: {exc}") from exc

    # Breve attesa per verificare se fallisce subito (es. porta occupata o connessione rifiutata)
    time.sleep(0.6)
    poll_res = proc.poll()
    if poll_res is not None:
        _, stderr = proc.communicate()
        _ACTIVE_TUNNEL_PROC = None
        _ACTIVE_TUNNEL_INFO = {"last_error": stderr.strip() or f"Errore SSH (codice {poll_res})"}
        raise RuntimeError(f"Avvio tunnel SSH fallito: {stderr.strip() or f'codice {poll_res}'}")

    _ACTIVE_TUNNEL_PROC = proc
    _ACTIVE_TUNNEL_INFO = {
        "host": host,
        "port": port,
        "user": user,
        "local_port": local_port,
        "remote_port": remote_port,
        "key_path": key_path,
        "owner_id": owner_id,
    }
    _ACTIVE_TUNNEL_JOB_ID = _persist_tunnel_job(_ACTIVE_TUNNEL_INFO, proc.pid)

    return get_tunnel_status()


def stop_ssh_tunnel() -> TunnelStatus:
    """Interrompe il tunnel SSH in background."""
    global _ACTIVE_TUNNEL_PROC, _ACTIVE_TUNNEL_INFO, _ACTIVE_TUNNEL_JOB_ID
    job_id = _ACTIVE_TUNNEL_JOB_ID
    pid: int | None = None
    process_group: int | None = None
    if _ACTIVE_TUNNEL_PROC is not None:
        pid = _ACTIVE_TUNNEL_PROC.pid
        try:
            process_group = os.getpgid(pid) if hasattr(os, "getpgid") else pid
        except OSError:
            # Il processo può essere terminato tra poll e getpgid. Fermare un
            # tunnel già morto deve comunque ripulire lo stato, non bloccare
            # l'apertura del successivo.
            process_group = pid
        try:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                try:
                    os.killpg(os.getpgid(_ACTIVE_TUNNEL_PROC.pid), signal.SIGTERM)
                except Exception:
                    _ACTIVE_TUNNEL_PROC.terminate()
            else:
                _ACTIVE_TUNNEL_PROC.terminate()
            _ACTIVE_TUNNEL_PROC.wait(timeout=2.0)
        except Exception:
            try:
                _ACTIVE_TUNNEL_PROC.kill()
            except Exception:
                pass
        _ACTIVE_TUNNEL_PROC = None
    else:
        recovered = _persisted_tunnel()
        if recovered is not None:
            job_id, info = recovered
            pid = int(info.get("pid") or 0)
            process_group = int(info.get("process_group") or pid)
            if pid and _is_ssh_process(pid):
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(process_group, signal.SIGTERM)
                    else:
                        os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            elif pid:
                _update_tunnel_job(job_id, "failed", "PID persistito non verificabile come processo SSH")
                raise RuntimeError("Il tunnel persistito non è stato fermato: PID non verificabile come processo SSH")
    _update_tunnel_job(job_id, "stopped")
    _ACTIVE_TUNNEL_JOB_ID = None
    _ACTIVE_TUNNEL_INFO = {}
    return get_tunnel_status()


def reconcile_tunnel() -> None:
    """All'avvio ripristina il tunnel che era attivo prima del riavvio.

    Senza, un riavvio dell'app lascia l'inferenza cloud "collegata" a parole ma
    senza la strada: il tunnel viveva nel processo morto e l'utente dovrebbe
    ricordarsi di riaprirlo a mano. È best-effort: se l'host non risponde più
    (istanza distrutta) l'app parte comunque e il job resta segnato fallito.
    Il job va letto PRIMA di ogni controllo di liveness: `_persisted_tunnel`
    marca lo stantio come 'failed' e cancellebbe l'intento da ripristinare.
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id, pid, state, error, command_json FROM jobs WHERE kind='ssh_tunnel' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return  # mai partito, o fermato/visto fallire esplicitamente
    # Un fallimento di avvio o la morte del processo non sono uno stop
    # esplicito: conserviamo l'intento e riproviamo al successivo boot.
    retryable_failure = row["state"] == "failed" and str(row["error"] or "").startswith((
        "Avvio tunnel SSH fallito", "processo SSH non più presente",
    ))
    if row["state"] != "running" and not retryable_failure:
        return
    try:
        info = json.loads(row["command_json"] or "{}")
    except (TypeError, ValueError):
        return
    # L'SSH è sopravvissuto al riavvio dell'app? Allora non va ucciso e ricreato.
    pid = int(row["pid"] or 0)
    if pid and _pid_alive(pid) and _is_ssh_process(pid):
        return
    host = str(info.get("host") or "").strip()
    if not host:
        return
    last_error: Exception | None = None
    # Dopo un riavvio Vast può impiegare qualche secondo a riaprire la porta
    # SSH.  Un solo tentativo trasformava un normale cold-start in «connessione
    # persa» e costringeva l'utente a ripetere il wizard. Ritentiamo in modo
    # limitato, senza tenere bloccata l'app indefinitamente.
    for attempt in range(3):
        try:
            status = start_ssh_tunnel(
                host,
                int(info.get("port") or 22),
                user=str(info.get("user") or "root"),
                key_path=info.get("key_path"),
                local_port=int(info.get("local_port") or 8888),
                remote_port=int(info.get("remote_port") or 8888),
                owner_id=info.get("owner_id"),
            )
            # Mantieni il client inferenza allineato alla porta realmente
            # ripristinata, anche se il browser aveva conservato una config
            # precedente o il sistema aveva scelto una porta dinamica.
            from . import inference

            inference.save_inference_config({
                "url": f"http://127.0.0.1:{status.local_port}/v1",
                "provider": "vast",
            })
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(2.0)
    _update_tunnel_job(int(row["id"]), "failed", str(last_error or "ripristino tunnel fallito"))


def track_cloud_resource(
    provider: str,
    remote_id: str | int,
    *,
    owner_id: int | None = None,
    hourly_rate: float | None = None,
    state: str = "running",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Registra una risorsa fatturabile senza conservare credenziali."""
    remote_id = str(remote_id).strip()
    if not remote_id:
        return
    cost = dict(metadata or {})
    if hourly_rate is not None and float(hourly_rate) >= 0:
        cost["hourly_rate"] = float(hourly_rate)
    terminal = state in {"stopped", "deleted", "failed"}
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE kind='cloud_resource' AND provider=? AND remote_job_id=? "
            "ORDER BY id DESC LIMIT 1",
            (provider, remote_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO jobs(kind, owner_id, provider, remote_job_id, state, heartbeat_at, cost_json, recovery_strategy, ended_at) "
                "VALUES('cloud_resource', ?, ?, ?, ?, datetime('now'), ?, 'provider-api', CASE WHEN ? THEN datetime('now') ELSE NULL END)",
                (owner_id, provider, remote_id, state, json.dumps(cost), terminal),
            )
        else:
            conn.execute(
                "UPDATE jobs SET state=?, heartbeat_at=datetime('now'), cost_json=?, ended_at=CASE WHEN ? THEN COALESCE(ended_at, datetime('now')) ELSE NULL END WHERE id=?",
                (state, json.dumps(cost), terminal, row["id"]),
            )


def cloud_resource_cost(provider: str, remote_id: str | int) -> dict[str, float] | None:
    """Calcola il costo stimato della risorsa dal suo record persistito."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT started_at, ended_at, cost_json FROM jobs "
                "WHERE kind='cloud_resource' AND provider=? AND remote_job_id=? ORDER BY id DESC LIMIT 1",
                (provider, str(remote_id).strip()),
            ).fetchone()
    except sqlite3.OperationalError:
        # Pure contract tests may exercise the provider adapter before the
        # application lifecycle has created the optional jobs table.
        return None
    if row is None:
        return None
    try:
        cost = json.loads(row["cost_json"] or "{}")
        rate = float(cost.get("hourly_rate"))
    except (TypeError, ValueError, KeyError):
        return None
    with connect() as conn:
        elapsed = conn.execute(
            "SELECT MAX(0.0, (julianday(COALESCE(?, datetime('now'))) - julianday(?)) * 24.0)",
            (row["ended_at"], row["started_at"]),
        ).fetchone()[0]
    hours = float(elapsed or 0.0)
    return {"hours": hours, "hourly_rate": rate, "estimated_usd": hours * rate}


def _with_cost(provider: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        if item.get("id") is None:
            continue
        estimate = cloud_resource_cost(provider, item["id"])
        if estimate is not None:
            item["cost_estimate"] = estimate
    return items


# --- VAST.AI API INTEGRATION --------------------------------------------------

# Immagini PyTorch ufficiali di Vast.ai, dal toolkit più recente al più vecchio
# (tag verificati su hub.docker.com/r/vastai/pytorch). L'offerta dichiara
# `cuda_max_good`, cioè il massimo CUDA che il driver dell'host regge: sapendolo
# *prima* del noleggio si sceglie l'immagine giusta invece di rimediare a
# runtime installando toolkit sull'istanza.
VAST_CUDA_IMAGES: tuple[tuple[float, str], ...] = (
    (13.0, "vastai/pytorch:cuda-13.0.3-auto"),
    (12.9, "vastai/pytorch:cuda-12.9.2-auto"),
    (12.8, "vastai/pytorch:cuda-12.8.1-auto"),
    (12.6, "vastai/pytorch:cuda-12.6.3-auto"),
    (12.4, "vastai/pytorch:cuda-12.4.1-auto"),
)

# Le GPU sm_120 (Blackwell) non sono compilabili con toolkit più vecchi: nvcc
# non ne riconosce la capability e FlashInfer rifiuta di costruire i kernel.
MIN_CUDA_FOR_BLACKWELL = 12.9


def vast_image_for(cuda_max_good: float | None, *, minimum: float = MIN_CUDA_FOR_BLACKWELL) -> str:
    """Immagine più recente che il driver dell'host regge, non sotto il minimo.

    Senza `cuda_max_good` si resta sul minimo: è il valore che fa funzionare
    anche le GPU nuove, e i driver che non lo reggono sono ormai rari.
    """
    try:
        ceiling = float(cuda_max_good) if cuda_max_good is not None else 0.0
    except (TypeError, ValueError):
        ceiling = 0.0
    if ceiling <= 0:
        return next(image for version, image in VAST_CUDA_IMAGES if version <= minimum + 1e-9 or version == minimum)
    for version, image in VAST_CUDA_IMAGES:
        if version <= ceiling + 1e-9 and version >= minimum - 1e-9:
            return image
    # Host troppo vecchio per il minimo richiesto: si prende comunque la sua
    # immagine massima, e il preflight dello script dirà cosa non va.
    for version, image in VAST_CUDA_IMAGES:
        if version <= ceiling + 1e-9:
            return image
    return VAST_CUDA_IMAGES[-1][1]

VAST_API_BASE = "https://console.vast.ai/api/v0"
# Vast.ai sta migrando per rotte, non in blocco: al 2026-09 solo la collection
# delle istanze è passata a v1 (la v0 risponde 410 deprecated_endpoint), mentre
# `users/current`, `ssh` e `search/asks` esistono solo su v0 — verificato
# sondando entrambe le versioni.
VAST_API_V1 = "https://console.vast.ai/api/v1"


class VastDeprecatedEndpoint(RuntimeError):
    """Rotta rimossa dal provider: chi la chiama può ricadere sull'alternativa."""



def search_vast_offers(
    api_key: str,
    *,
    gpu_name: str = "",
    num_gpus: int = 1,
    max_dph: float | None = None,
    min_reliability: float = 0.95,
    instance_type: str = "on-demand",
    limit: int = 12,
    disk_gb: int = 40,
    min_gpu_ram_gb: float | None = None,
    min_inet_down: float | None = None,
    min_cuda: float | None = None,
    verified_only: bool = False,
) -> list[dict[str, Any]]:
    """Cerca offerte noleggiabili, senza creare alcuna istanza."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di Vast.ai non specificata.")
    # Forma verificata sul sorgente della CLI ufficiale (`search__offers`): la
    # query sta sotto `q`, con `order`, `type` e `limit` al suo interno; la
    # busta esterna accetta solo `q` e `select_cols`. Mandare i filtri al primo
    # livello ora fallisce con "Extra inputs are not permitted".
    query: dict[str, Any] = {
        "num_gpus": {"eq": max(1, int(num_gpus))},
        "reliability": {"gte": max(0.0, min(1.0, float(min_reliability)))},
        "rentable": {"eq": True},
        # Default della CLI: le macchine "external" non seguono lo stesso
        # ciclo di vita e non sono adatte al provisioning automatico.
        "external": {"eq": False},
        "order": [["dph_total", "asc"]],
        "limit": max(1, min(50, int(limit))),
        # Il prezzo mostrato include il costo del disco richiesto.
        "allocated_storage": float(max(5, int(disk_gb))),
    }
    if gpu_name.strip():
        query["gpu_name"] = {"eq": gpu_name.strip()}
    if max_dph is not None and float(max_dph) > 0:
        query["dph_total"] = {"lte": float(max_dph)}
    if min_gpu_ram_gb:
        # `gpu_ram` è in MB nel catalogo del provider.
        query["gpu_ram"] = {"gte": float(min_gpu_ram_gb) * 1024.0}
    if disk_gb:
        query["disk_space"] = {"gte": float(disk_gb)}
    if min_inet_down:
        query["inet_down"] = {"gte": float(min_inet_down)}
    if min_cuda:
        query["cuda_max_good"] = {"gte": float(min_cuda)}
    if verified_only:
        query["verified"] = {"eq": True}
    query["type"] = "bid" if instance_type == "interruptible" else "on-demand"

    # Due forme entrambe in uso nella CLI ufficiale: `/search/asks/` vuole la
    # query sotto `q` (e rifiuta `select_cols: ["*"]`, che non passa il pattern
    # dei nomi colonna), mentre il vecchio `/bundles/` la prende al primo
    # livello. Si prova la nuova e si ricade sulla legacy: il provider sta
    # cambiando schema sotto di noi e la ricerca non deve morire per questo.
    try:
        data = _vast_call(api_key, "PUT", "/search/asks/", json_body={"q": query}, timeout=20.0)
    except VastDeprecatedEndpoint:
        raise
    except RuntimeError as exc:
        try:
            data = _vast_call(api_key, "POST", "/bundles/", json_body=query, timeout=20.0)
        except RuntimeError:
            raise exc

    if isinstance(data, list):
        offers = data
    else:
        offers = data.get("offers", data.get("asks", []))
    result = []
    for offer in offers:
        result.append({
            "id": offer.get("id") or offer.get("ask_contract_id"),
            "gpu_name": offer.get("gpu_name") or offer.get("gpu_name_display"),
            "num_gpus": offer.get("num_gpus", 1),
            "gpu_ram": offer.get("gpu_ram") or offer.get("gpu_ram_mb"),
            "dph_total": offer.get("dph_total") or offer.get("dph"),
            "reliability": offer.get("reliability"),
            "location": offer.get("geolocation") or offer.get("location") or offer.get("country"),
            "disk_cost": offer.get("storage_cost") or offer.get("disk_space_cost"),
            "verified": offer.get("verified"),
            "disk_space": offer.get("disk_space"),
            "inet_down": offer.get("inet_down"),
            "cuda_max_good": offer.get("cuda_max_good"),
        })
    return _with_cost("vast", [item for item in result if item["id"] is not None])


def rent_vast_instance(
    api_key: str,
    offer_id: int,
    *,
    # Vuota: l'immagine viene scelta dal modello o da `cuda_max_good`.
    image: str = "",
    cuda_max_good: float | None = None,
    adapter_id: str = "",
    disk_gb: int = 40,
    model: str = "zenosai/MonkeyOCRv2-B-Parsing",
    port: int = 8888,
    api_key_for_server: str = "",
    monkeyocr_ref: str = "",
    tabularium_ref: str = "",
    prepare_server: bool = False,
) -> dict[str, Any]:
    """Noleggia un'offerta esplicita. L'azione è separata dalla ricerca."""
    token = api_key.strip()
    if not token:
        raise ValueError("API Key di Vast.ai non specificata.")
    if int(disk_gb) < 10:
        raise ValueError("Il disco deve essere almeno 10 GB.")
    pinned_ref = str(monkeyocr_ref).strip()
    if prepare_server and not pinned_ref:
        raise ValueError("monkeyocr_ref obbligatorio per preparare il server con una recipe riproducibile")
    pinned_tabularium_ref = str(tabularium_ref).strip()
    if prepare_server and not pinned_tabularium_ref:
        raise ValueError("tabularium_ref obbligatorio per preparare il server con una recipe riproducibile")
    if pinned_ref and any(ch in pinned_ref for ch in " \t\r\n;|&`$\\'"):
        raise ValueError("monkeyocr_ref non valido")
    if pinned_tabularium_ref and any(ch in pinned_tabularium_ref for ch in " \t\r\n;|&`$\\'"):
        raise ValueError("tabularium_ref non valido")
    # Un modello che vive in un'immagine propria la impone all'istanza: su
    # Vast.ai l'immagine si sceglie qui, ed è l'unico momento in cui si può.
    chosen_image = image.strip()
    if not chosen_image and adapter_id:
        from . import serve_recipes

        recipe = serve_recipes.recipe_for(adapter_id)
        chosen_image = recipe.docker_image
    body: dict[str, Any] = {
        "image": chosen_image or vast_image_for(cuda_max_good),
        "disk": int(disk_gb),
        "runtype": "ssh_direct",
        # Vast documenta `env` come stringa di opzioni container; il vecchio
        # dict non era interpretato come port mapping dall'API REST.
        "env": f"-p {int(port)}:{int(port)}/http -p 22:22/tcp",
    }
    if prepare_server:
        secret = api_key_for_server.strip()
        if secret:
            body["env"] += f" -e TABULARIUM_SERVER_API_KEY={shlex.quote(secret)}"
        body["onstart"] = (
            "apt-get update -qq && apt-get install -y -qq git curl && "
            "curl -fsSL https://raw.githubusercontent.com/nbadino/tabularium/"
            f"{shlex.quote(pinned_tabularium_ref)}/"
            "scripts/cloud/setup_cloud_vllm.sh | bash -s --"
            f" --port {int(port)} --model {shlex.quote(str(model))} --ref {shlex.quote(pinned_ref)}"
        )
    data = _vast_call(token, "PUT", f"/asks/{int(offer_id)}/", json_body=body, timeout=30.0)
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(data.get("msg") or data.get("error") or "Vast.ai ha rifiutato il noleggio")
    return {
        "ok": True,
        "offer_id": int(offer_id),
        "contract_id": data.get("new_contract") if isinstance(data, dict) else None,
        "instance": data,
    }


def list_vast_instances(api_key: str, *, owner_id: int | None = None) -> list[dict[str, Any]]:
    """Recupera l'elenco delle istanze noleggiate dall'account Vast.ai."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di Vast.ai non specificata.")

    instances = _fetch_vast_instances(api_key)
    out = []
    for inst in instances:
        item = _normalize_vast_instance(inst)
        status = item["status"]
        resource_id = item["id"]
        out.append(item)
        if resource_id is not None:
            normalized = str(status).lower()
            state = "running" if item["is_running"] else (
                "deleted" if normalized in {"deleted", "destroyed", "terminated"}
                else "stopped" if normalized in {"stopped", "exited", "paused"}
                else "running"
            )
            track_cloud_resource(
                "vast", resource_id, owner_id=owner_id,
                hourly_rate=item["dph_total"], state=state,
                metadata={"provider_status": status},
            )
    return _with_cost("vast", out)


def _vast_instances_payload(data: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Normalizza la busta della lista: v1 pagina, v0 no, i nomi dei campi variano."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], None
    if not isinstance(data, dict):
        return [], None
    for key in ("instances", "results", "data", "items"):
        items = data.get(key)
        if isinstance(items, list):
            token = data.get("next_token") or data.get("next") or None
            return [item for item in items if isinstance(item, dict)], (str(token) if token else None)
    return [], None


def _fetch_vast_instances(api_key: str, *, max_pages: int = 10) -> list[dict[str, Any]]:
    """Elenco istanze dalla rotta v1 paginata, con fallback alla v0 se assente."""
    collected: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"limit": 25}
        if token:
            params["next_token"] = token
        try:
            data = _vast_call(api_key, "GET", "/instances/", params=params, timeout=15.0, version="v1")
        except VastDeprecatedEndpoint:
            raise
        except RuntimeError:
            # Provider in transizione: se la v1 non risponde, la v0 resta valida
            # per gli account non ancora migrati.
            if collected:
                raise
            return _vast_instances_payload(_vast_call(api_key, "GET", "/instances/", timeout=10.0))[0]
        page, token = _vast_instances_payload(data)
        collected.extend(page)
        if not token or not page:
            break
    return collected


def control_vast_instance(api_key: str, instance_id: int, action: str) -> dict[str, Any]:
    """Avvia ('start'), mette in pausa ('stop') o distrugge ('delete') un'istanza Vast.ai."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di Vast.ai non specificata.")

    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=15.0) as client:
        if action == "start":
            resp = client.put(f"{VAST_API_BASE}/instances/{instance_id}/", headers=headers, json={"state": "running"})
        elif action == "stop":
            resp = client.put(f"{VAST_API_BASE}/instances/{instance_id}/", headers=headers, json={"state": "stopped"})
        elif action == "delete":
            resp = client.delete(f"{VAST_API_BASE}/instances/{instance_id}/", headers=headers)
        else:
            raise ValueError(f"Azione Vast.ai non supportata: {action}")

        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Operazione fallita su Vast.ai ({resp.status_code}): {resp.text}")
        return {"ok": True, "action": action, "instance_id": instance_id}


# --- VAST.AI: PRIMA CONFIGURAZIONE (account, chiave SSH, host key) -------------
# Tutto ciò che serve per portare un account Vast.ai appena creato fino a un
# tunnel SSH funzionante senza passare dalla console web del provider.
# Endpoint verificati su docs.vast.ai (api-reference):
#   GET  /users/current/          → identità e credito residuo
#   GET  /ssh/                    → chiavi pubbliche registrate sull'account
#   POST /ssh/                    → registra una chiave (propagata alle istanze)
#   POST /instances/{id}/ssh/     → allega la chiave a una singola istanza
#   GET  /instances/{id}/         → stato, ssh_host e ssh_port dell'istanza

_HOSTNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,253}[A-Za-z0-9])?$")


def _vast_headers(api_key: str) -> dict[str, str]:
    token = api_key.strip()
    if not token:
        raise ValueError("API Key di Vast.ai non specificata.")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _vast_call(
    api_key: str,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: float = 15.0,
    version: str = "v0",
) -> Any:
    """Chiamata REST a Vast.ai con errori applicativi distinti da quelli di rete."""
    headers = _vast_headers(api_key)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    base = VAST_API_V1 if version == "v1" else VAST_API_BASE
    try:
        # follow_redirects: Vast.ai risponde 301 sulle rotte senza slash finale
        # e httpx non lo segue di default (vedi list_vast_instances).
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.request(method, f"{base}{path}", headers=headers, json=json_body, params=params)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Impossibile contattare Vast.ai: {exc}") from exc
    if resp.status_code == 401:
        raise RuntimeError("Vast.ai ha rifiutato la API Key (401): controlla di aver copiato la chiave intera.")
    if resp.status_code == 410 or "deprecated_endpoint" in resp.text:
        raise VastDeprecatedEndpoint(f"Rotta Vast.ai non più disponibile ({path}): {resp.text[:200]}")
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"Errore Vast.ai ({resp.status_code}) su {path}: {resp.text[:300]}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Risposta non JSON da Vast.ai su {path}") from exc


def vast_account(api_key: str) -> dict[str, Any]:
    """Preflight dell'account: valida la chiave e riporta il credito residuo."""
    data = _vast_call(api_key, "GET", "/users/current/", timeout=10.0)
    if not isinstance(data, dict):
        raise RuntimeError("Risposta inattesa da Vast.ai per l'account corrente")
    # Vast.ai espone il saldo come `credit`; `balance` resta come alias storico
    # e su alcuni account vale 0 anche con credito disponibile.
    balance = 0.0
    for field in ("credit", "balance"):
        try:
            value = float(data.get(field))
        except (TypeError, ValueError):
            continue
        if value:
            balance = value
            break
    return {
        "id": data.get("id"),
        "email": data.get("email"),
        "balance": balance,
        # Il credito minimo non è imposto da noi: è la UI a segnalare che
        # sotto ~1 $ il noleggio verrà rifiutato dal provider.
        "balance_ok": balance > 0.0,
    }


# --- Chiave SSH dedicata ------------------------------------------------------

def ssh_key_dir() -> Path:
    return config.ROOT_DIR / "ssh"


def ssh_key_path() -> Path:
    """Chiave dedicata al cloud: mai la chiave personale dell'utente."""
    return ssh_key_dir() / "tabularium_vast_ed25519"


def _normalized_pubkey(value: str) -> str:
    """Confronta le chiavi su tipo+materiale, ignorando il commento finale."""
    parts = str(value or "").strip().split()
    if len(parts) < 2:
        return ""
    return f"{parts[0]} {parts[1]}"


def _run(cmd: list[str], *, timeout: float = 20.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Comando non disponibile sul sistema: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timeout eseguendo {cmd[0]}") from exc


def local_ssh_key(*, create: bool = False) -> dict[str, Any]:
    """Stato (ed eventuale creazione) della coppia di chiavi locale.

    La chiave privata resta sul disco dell'utente e non lascia mai il backend:
    verso l'esterno esponiamo solo pubblica e fingerprint.
    """
    key_path = ssh_key_path()
    pub_path = key_path.with_suffix(".pub")
    if not pub_path.exists() and create:
        key_dir = ssh_key_dir()
        key_dir.mkdir(parents=True, exist_ok=True)
        try:
            key_dir.chmod(0o700)
        except OSError:
            pass
        # Una chiave orfana (privata senza pubblica o viceversa) bloccherebbe
        # ssh-keygen: si rigenera la coppia da zero.
        for stale in (key_path, pub_path):
            if stale.exists():
                stale.unlink()
        res = _run([
            "ssh-keygen", "-t", "ed25519", "-N", "", "-q",
            "-C", "tabularium-vast", "-f", str(key_path),
        ], timeout=30.0)
        if res.returncode != 0 or not pub_path.exists():
            raise RuntimeError(f"Generazione chiave SSH fallita: {(res.stderr or res.stdout).strip()}")
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
    if not pub_path.exists():
        return {"exists": False, "key_path": str(key_path), "public_key": "", "fingerprint": "", "key_type": ""}
    public_key = pub_path.read_text(encoding="utf-8").strip()
    fp = _run(["ssh-keygen", "-lf", str(pub_path)], timeout=10.0)
    # `ssh-keygen -l` stampa "256 SHA256:… commento (ED25519)": ai client serve
    # l'impronta, non la riga intera.
    raw = fp.stdout.strip() if fp.returncode == 0 else ""
    parts = raw.split()
    fingerprint = next((token for token in parts if token.startswith("SHA256:")), "")
    key_type = parts[-1].strip("()") if parts else ""
    return {
        "exists": True,
        "key_path": str(key_path),
        "public_key": public_key,
        "fingerprint": fingerprint,
        "key_type": key_type,
    }


def list_vast_ssh_keys(api_key: str) -> list[dict[str, Any]]:
    """Chiavi pubbliche già registrate sull'account."""
    data = _vast_call(api_key, "GET", "/ssh/", timeout=10.0)
    if isinstance(data, dict):
        items = data.get("ssh_keys") or data.get("results") or []
    else:
        items = data or []
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        raw = item.get("public_key") or item.get("ssh_key") or item.get("key") or ""
        out.append({"id": item.get("id"), "public_key": str(raw).strip()})
    return out


def ensure_vast_ssh_key(api_key: str) -> dict[str, Any]:
    """Genera la chiave locale (se manca) e la registra sull'account Vast.ai.

    Idempotente: se la stessa chiave pubblica è già sull'account non ne crea
    una seconda. È il passo che elimina il giro dalla console web del provider.
    """
    key = local_ssh_key(create=True)
    public_key = key["public_key"]
    normalized = _normalized_pubkey(public_key)
    if not normalized:
        raise RuntimeError("Chiave pubblica locale non valida")
    existing = list_vast_ssh_keys(api_key)
    already = any(_normalized_pubkey(item["public_key"]) == normalized for item in existing)
    if not already:
        _vast_call(api_key, "POST", "/ssh/", json_body={"ssh_key": public_key}, timeout=20.0)
    return {
        "key_path": key["key_path"],
        "public_key": public_key,
        "fingerprint": key["fingerprint"],
        "already_registered": already,
        "registered": True,
        "account_keys": len(existing) + (0 if already else 1),
    }


def attach_vast_ssh_key(api_key: str, instance_id: int) -> dict[str, Any]:
    """Allega la chiave locale a un'istanza già in esecuzione.

    Serve per le istanze create prima della registrazione della chiave: quelle
    nuove la ricevono automaticamente dall'account.
    """
    key = local_ssh_key(create=True)
    _vast_call(
        api_key, "POST", f"/instances/{int(instance_id)}/ssh/",
        json_body={"ssh_key": key["public_key"]}, timeout=20.0,
    )
    return {"ok": True, "instance_id": int(instance_id), "fingerprint": key["fingerprint"]}


# --- Stato istanza e host key --------------------------------------------------

def _vast_ssh_endpoint(inst: dict[str, Any]) -> tuple[str | None, int | None]:
    """Estrae l'endpoint SSH da entrambe le forme restituite da Vast.ai.

    La CLI ufficiale usa prima ``ssh_host``/``ssh_port`` e poi ripiega su
    ``public_ipaddr`` + ``ports['22/tcp'][0]['HostPort']``. La collection v1
    può pubblicare solo la seconda forma: ignorarla lasciava un'istanza
    realmente raggiungibile bloccata per sempre su «avvio» nella UI.
    """
    raw_ports = inst.get("ports") or {}
    port_value: Any = inst.get("ssh_port")
    if not port_value and isinstance(raw_ports, dict):
        ssh_mapping = raw_ports.get("22/tcp") or raw_ports.get("22")
        if isinstance(ssh_mapping, list) and ssh_mapping:
            first = ssh_mapping[0]
            if isinstance(first, dict):
                port_value = first.get("HostPort") or first.get("host_port")
        elif isinstance(ssh_mapping, dict):
            port_value = ssh_mapping.get("HostPort") or ssh_mapping.get("host_port")
        elif ssh_mapping:
            port_value = ssh_mapping
    try:
        ssh_port = int(port_value) if port_value else None
    except (TypeError, ValueError):
        ssh_port = None
    if ssh_port is not None and not (0 < ssh_port < 65536):
        ssh_port = None
    host_value = inst.get("ssh_host") or inst.get("public_ipaddr")
    ssh_host = str(host_value).strip() if host_value else None
    return (ssh_host or None), ssh_port


def _normalize_vast_instance(inst: dict[str, Any]) -> dict[str, Any]:
    is_running = str(inst.get("actual_status") or "").lower() == "running"
    ssh_host, ssh_port = _vast_ssh_endpoint(inst)
    return {
        "id": inst.get("id"),
        "status": inst.get("actual_status") or inst.get("status_msg") or "unknown",
        "gpu_name": inst.get("gpu_name"),
        "num_gpus": inst.get("num_gpus", 1),
        "dph_total": inst.get("dph_total"),
        "ssh_host": ssh_host,
        "ssh_port": ssh_port,
        "is_running": is_running,
        "label": inst.get("label") or f"{inst.get('num_gpus', 1)}x {inst.get('gpu_name', 'GPU')}",
        "ports": inst.get("ports", {}),
    }


def get_vast_instance(api_key: str, instance_id: int, *, owner_id: int | None = None) -> dict[str, Any]:
    """Dettaglio di una singola istanza: è ciò che il wizard interroga in polling."""
    try:
        data = _vast_call(api_key, "GET", f"/instances/{int(instance_id)}/", timeout=10.0)
        inst = data.get("instances") if isinstance(data, dict) else None
        if isinstance(inst, list):
            inst = inst[0] if inst else None
        if not isinstance(inst, dict):
            inst = data if isinstance(data, dict) else {}
    except VastDeprecatedEndpoint:
        # Se anche il dettaglio sparisce dalla v0, l'istanza si ritrova nella
        # lista v1: il polling del wizard non deve fermarsi per questo.
        inst = next(
            (item for item in _fetch_vast_instances(api_key) if str(item.get("id")) == str(instance_id)),
            {},
        )
    item = _normalize_vast_instance(inst)
    if item["id"] is None:
        item["id"] = int(instance_id)
    # `ssh_ready` distingue "istanza accesa" da "istanza raggiungibile": Vast
    # pubblica host e porta solo quando il container ha davvero il forwarding.
    item["ssh_ready"] = bool(item["is_running"] and item["ssh_host"] and item["ssh_port"])
    normalized = str(item["status"]).lower()
    state = "running" if item["is_running"] else (
        "deleted" if normalized in {"deleted", "destroyed", "terminated"}
        else "stopped" if normalized in {"stopped", "exited", "paused"}
        else "running"
    )
    track_cloud_resource(
        "vast", item["id"], owner_id=owner_id,
        hourly_rate=item["dph_total"], state=state,
        metadata={"provider_status": item["status"]},
    )
    estimate = cloud_resource_cost("vast", item["id"])
    if estimate is not None:
        item["cost_estimate"] = estimate
    return item


def pin_ssh_host_key(host: str, port: int) -> dict[str, Any]:
    """Registra la host key dell'istanza nel known_hosts dedicato.

    Trust-on-first-use esplicito: l'alternativa non è "più sicurezza" ma
    `StrictHostKeyChecking=no`, che accetterebbe *qualsiasi* host a ogni
    riconnessione. Qui la chiave viene fissata una volta e ogni cambiamento
    successivo fa fallire il tunnel, che è il comportamento voluto.
    """
    host = str(host or "").strip()
    port = int(port)
    if not _HOSTNAME.fullmatch(host):
        raise ValueError("Host SSH non valido.")
    if not (0 < port < 65536):
        raise ValueError("Porta SSH non valida.")
    known_hosts = Path(config.SSH_KNOWN_HOSTS)
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.touch(exist_ok=True)
    try:
        known_hosts.chmod(0o600)
    except OSError:
        pass
    scan = _run(["ssh-keyscan", "-T", "10", "-p", str(port), host], timeout=25.0)
    lines = [line for line in scan.stdout.splitlines() if line.strip() and not line.startswith("#")]
    if scan.returncode != 0 or not lines:
        raise RuntimeError(
            f"Host key non ottenibile da {host}:{port} — l'istanza potrebbe non aver ancora avviato SSH. "
            f"{(scan.stderr or '').strip()[:200]}"
        )
    # Rimuove eventuali chiavi precedenti dello stesso host:porta (istanza
    # ricreata sullo stesso endpoint) prima di riscrivere quella corrente.
    _run(["ssh-keygen", "-R", f"[{host}]:{port}", "-f", str(known_hosts)], timeout=15.0)
    stale_backup = known_hosts.with_name(known_hosts.name + ".old")
    if stale_backup.exists():
        stale_backup.unlink()
    with known_hosts.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line.rstrip("\n") + "\n")
    key_types = sorted({parts[1] for parts in (line.split() for line in lines) if len(parts) >= 3})
    return {
        "ok": True,
        "host": host,
        "port": port,
        "known_hosts": str(known_hosts),
        "key_types": key_types,
        "entries": len(lines),
    }


# --- VAST.AI: provisioning del server via SSH ---------------------------------
# Alternativa all'hook `onstart`: invece di far scaricare all'istanza il nostro
# script da GitHub (che obbligherebbe a pushare un commit prima di ogni
# noleggio), lo consegniamo sullo standard input di SSH dal checkout locale.
# Vantaggi: nessuna dipendenza dal repo pubblico, e lo script eseguito è
# esattamente quello che l'utente ha davanti. Il pin resta esplicito per il
# runner ufficiale MonkeyOCRv2, che è codice di terzi.

MONKEYOCR_REPO = "Yuliang-Liu/MonkeyOCRv2"
SETUP_SCRIPT = "scripts/cloud/setup_cloud_vllm.sh"
REMOTE_SETUP_PATH = "/root/tabularium_setup_cloud_vllm.sh"
REMOTE_LOG_PATH = "/var/log/tabularium_setup.log"

_SSH_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def resolve_monkeyocr_ref(*, repo: str = MONKEYOCR_REPO) -> dict[str, Any]:
    """Risolve un ref pin-nabile del runner ufficiale (tag più recente o HEAD).

    Serve a togliere all'utente la digitazione di uno SHA: il valore resta
    esplicito e viene registrato nella recipe, quindi la riproducibilità non
    si perde — cambia solo chi lo va a cercare.
    """
    base = f"https://api.github.com/repos/{repo}"
    headers = {"Accept": "application/vnd.github+json"}
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            tags = client.get(f"{base}/tags", headers=headers, params={"per_page": 1})
            if tags.status_code == 200:
                items = tags.json()
                if isinstance(items, list) and items:
                    tag = items[0]
                    return {
                        "ref": str(tag.get("name") or "").strip(),
                        "sha": str((tag.get("commit") or {}).get("sha") or "")[:40],
                        "kind": "tag",
                        "repo": repo,
                    }
            head = client.get(f"{base}/commits/HEAD", headers=headers)
            if head.status_code != 200:
                raise RuntimeError(f"GitHub ha risposto {head.status_code} per {repo}")
            sha = str((head.json() or {}).get("sha") or "").strip()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Impossibile risolvere il ref di {repo}: {exc}") from exc
    if not sha:
        raise RuntimeError(f"Nessun commit trovato per {repo}")
    return {"ref": sha, "sha": sha, "kind": "commit", "repo": repo}


def _ssh_base_args(host: str, port: int, user: str) -> list[str]:
    """Argomenti SSH comuni, con la stessa verifica host key del tunnel."""
    host = str(host or "").strip()
    user = str(user or "root").strip() or "root"
    port = int(port)
    if not _HOSTNAME.fullmatch(host):
        raise ValueError("Host SSH non valido.")
    if not _SSH_USER.fullmatch(user):
        raise ValueError("Utente SSH non valido.")
    if not (0 < port < 65536):
        raise ValueError("Porta SSH non valida.")
    args = [
        "ssh",
        "-F", "/dev/null",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={config.SSH_KNOWN_HOSTS}",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
    ]
    key = ssh_key_path()
    if key.exists():
        args.extend(["-i", str(key), "-o", "IdentitiesOnly=yes"])
    args.append(f"{user}@{host}")
    return args


def probe_vast_server(host: str, port: int, *, user: str = "root", remote_port: int = 8888) -> dict[str, Any]:
    """Controllo remoto non distruttivo: server vLLM già pronto o no."""
    remote_port = int(remote_port)
    cmd = _ssh_base_args(host, port, user) + [
        f"curl -fsS --max-time 4 http://127.0.0.1:{remote_port}/v1/models"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"ready": False, "model": ""}
    if proc.returncode != 0:
        return {"ready": False, "model": ""}
    try:
        data = json.loads(proc.stdout)
        entries = data.get("data") if isinstance(data, dict) else None
        model = str((entries or [{}])[0].get("id") or "") if isinstance(entries, list) else ""
    except (TypeError, ValueError, AttributeError):
        return {"ready": False, "model": ""}
    return {"ready": bool(model), "model": model}


REMOTE_MODEL_ROOT = "/root/models"
# Un ambiente per modello: le ricette pinnano versioni di vLLM diverse (0.12
# per DeepSeek, 0.19 per GLM, 0.21 per MinerU…), incompatibili fra loro nello
# stesso site-packages. Separandoli, la stessa istanza li ospita tutti e
# cambiare modello non ricomincia da capo: pesi e ambiente restano sul disco.
REMOTE_ENV_ROOT = "/root/tabularium/envs"


def build_provision_recipe(
    adapter_id: str,
    *,
    model: str = "",
    remote_port: int = 8888,
    server_api_key: str = "",
) -> dict[str, Any]:
    """Traduce la ricetta ufficiale del modello in istruzioni per l'istanza.

    La conoscenza resta in `serve_recipes.py` — versione di vLLM, dipendenze
    extra e flag che determinano la precisione — e lo script remoto si limita a
    eseguirle. Così una ricetta si corregge qui, con i test, non in bash.
    """
    from . import serve_recipes

    recipe = serve_recipes.recipe_for(adapter_id)
    hf_repo = str(model or "").strip() or recipe.hf_repo
    if not _MODEL.fullmatch(hf_repo):
        raise ValueError("Nome modello non valido.")
    model_dir = f"{REMOTE_MODEL_ROOT}/{hf_repo.rsplit('/', 1)[-1]}"
    return {
        "adapter_id": recipe.adapter_id,
        "runtime": recipe.runtime,
        "hf_repo": hf_repo,
        "model_dir": model_dir,
        "vllm_version": recipe.vllm_version,
        # Con l'immagine dedicata vLLM è già installato: rimpiazzarlo con una
        # wheel pip cancellerebbe proprio l'architettura per cui è stata scelta.
        "install_vllm": recipe.installs_vllm,
        "venv_dir": f"{REMOTE_ENV_ROOT}/{recipe.adapter_id}",
        "docker_image": recipe.docker_image,
        "pip_extra": list(recipe.pip_extra),
        "needs_monkeyocr_repo": recipe.runtime == "monkeyocr",
        "argv": serve_recipes.serve_argv(
            recipe, model_path=model_dir, port=int(remote_port), api_key=server_api_key,
        ),
        "served_model_name": recipe.served_model_name,
    }


def provision_vast_server(
    host: str,
    port: int,
    *,
    user: str = "root",
    model: str = "",
    adapter_id: str = "monkeyocrv2-parsing",
    remote_port: int = 8888,
    monkeyocr_ref: str,
    server_api_key: str = "",
    gpu_mem: str = "0.90",
) -> dict[str, Any]:
    """Copia lo script di setup sull'istanza e lo avvia in background.

    Il comando remoto ritorna subito: il setup dura minuti (pesi del modello) e
    viene seguito da `provision_log`. La chiave API del server viaggia come
    variabile d'ambiente, non come argomento, per non finire in `ps`.
    """
    ref = str(monkeyocr_ref or "").strip()
    if not _REF.fullmatch(ref):
        raise ValueError("monkeyocr_ref obbligatorio e senza metacaratteri (commit SHA o tag).")
    # Costruisci prima la ricetta: un server già attivo va riusato solo se
    # espone proprio il modello richiesto; cambiando modello bisogna riavviare
    # il processo remoto, senza reinstallare l'ambiente inutilmente.
    recipe = build_provision_recipe(
        adapter_id, model=model, remote_port=remote_port, server_api_key=server_api_key,
    )
    existing = probe_vast_server(host, port, user=user, remote_port=remote_port)
    if existing["ready"] and existing["model"] == recipe["served_model_name"]:
        return {
            "ok": True,
            "already_ready": True,
            "host": host,
            "port": int(port),
            "remote_port": int(remote_port),
            "served_model_name": existing["model"],
            "message": "server già configurato: nessuna reinstallazione eseguita",
        }
    remote_port = int(remote_port)
    if not (0 < remote_port < 65536):
        raise ValueError("Porta remota non valida.")
    script = config.REPO_DIR / SETUP_SCRIPT
    if not script.exists():
        raise RuntimeError(f"Script di setup non trovato: {script}")

    secret = str(server_api_key or "").strip()
    env_prefix = f"TABULARIUM_SERVER_API_KEY={shlex.quote(secret)} " if secret else ""
    # La ricetta viaggia in base64: contiene JSON con virgolette e graffe
    # (es. `--speculative-config`), che in una riga di comando remota sarebbero
    # un campo minato di quoting.
    recipe_b64 = base64.b64encode(json.dumps(recipe).encode("utf-8")).decode("ascii")
    env_prefix += f"RECIPE_B64={shlex.quote(recipe_b64)} "
    # `cat` deve completare *prima* di lanciare lo script: mettere in background
    # l'intera catena chiude la sessione SSH mentre il file è ancora in arrivo e
    # lascia uno script troncato. Solo l'avvio va in background, dentro le graffe;
    # `test -s` rifiuta il caso in cui il trasferimento sia comunque fallito.
    remote = (
        f"cat > {REMOTE_SETUP_PATH}; chmod 700 {REMOTE_SETUP_PATH}; "
        f"if [ ! -s {REMOTE_SETUP_PATH} ]; then echo tabularium-provision-empty; exit 1; fi; "
        f"echo \"[tabularium] setup avviato $(date -u +%FT%TZ)\" > {REMOTE_LOG_PATH}; "
        f"{{ {env_prefix}nohup setsid bash {REMOTE_SETUP_PATH}"
        f" --port {remote_port} --model {shlex.quote(recipe['hf_repo'])} --ref {shlex.quote(ref)}"
        f" --gpu-mem {shlex.quote(str(gpu_mem))}"
        f" >> {REMOTE_LOG_PATH} 2>&1 < /dev/null & }}; "
        "echo tabularium-provision-started"
    )
    cmd = _ssh_base_args(host, port, user) + [remote]
    proc = None
    # Dopo `attach ssh` Vast.ai può impiegare alcuni secondi a propagare la
    # chiave al container già acceso. Un singolo tentativo rendeva
    # «Prepara e connetti» apparentemente rotto sulle istanze esistenti.
    for attempt in range(3):
        try:
            with script.open("rb") as payload:
                proc = subprocess.run(
                    cmd, stdin=payload, capture_output=True, text=True, timeout=90, check=False,
                )
        except FileNotFoundError as exc:
            raise RuntimeError("Comando ssh non disponibile sul sistema") from exc
        except subprocess.TimeoutExpired as exc:
            if attempt < 2:
                time.sleep(2)
                continue
            raise RuntimeError("Timeout nella connessione SSH all'istanza") from exc
        if proc.returncode == 0:
            break
        # 255 è il codice SSH per mancata autenticazione/connessione. Gli
        # errori applicativi del comando remoto non vanno ritentati alla cieca.
        if proc.returncode != 255 or attempt == 2:
            break
        time.sleep(2)
    assert proc is not None
    if "tabularium-provision-empty" in proc.stdout:
        raise RuntimeError("Script di setup arrivato vuoto sull'istanza: riprova la preparazione.")
    if proc.returncode != 0 or "tabularium-provision-started" not in proc.stdout:
        raise RuntimeError(
            f"Provisioning non avviato (codice {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    return {
        "ok": True,
        "host": host,
        "port": int(port),
        "remote_port": remote_port,
        "model": recipe["hf_repo"],
        "adapter_id": recipe["adapter_id"],
        "served_model_name": recipe["served_model_name"],
        "monkeyocr_ref": ref,
        "log_path": REMOTE_LOG_PATH,
    }


# Fasi riconosciute dal log di `setup_cloud_vllm.sh`, dall'ultima alla prima:
# servono alla UI per dire a che punto è, invece di mostrare solo righe che
# scorrono. L'ordine conta: si prende la fase più avanzata già raggiunta.
_PROVISION_PHASES = (
    ("Avvio vLLM su", "serving"),
    ("Download pesi modello", "weights"),
    ("Installazione dipendenze Python", "python"),
    ("Clonazione repository", "clone"),
    ("Installazione dipendenze di sistema", "system"),
    ("setup avviato", "starting"),
)

# Righe che il server stampa quando è davvero in ascolto.
_PROVISION_READY = ("Application startup complete", "Uvicorn running", "Server vLLM")

_PHASE_SECTION = "---tabularium-phases---"
_TAIL_SECTION = "---tabularium-tail---"
_ALIVE_MARKER = "tabularium-alive"
_DEAD_MARKER = "tabularium-dead"

# Errori che compaiono quando muore il server, non lo script: `serve.py` esce
# con un traceback Python e nessuna diagnostica "!!", quindi senza questi
# marcatori un processo morto sembrerebbe ancora in preparazione.
_PROVISION_ERRORS = ("Engine core initialization failed", "RuntimeError:", "Error:", "Traceback (most recent call last)")


def provision_log(host: str, port: int, *, user: str = "root", lines: int = 80) -> dict[str, Any]:
    """Ultime righe del log di setup remoto, per seguire l'avanzamento da UI."""
    lines = max(1, min(500, int(lines)))
    # Due sezioni in una sola connessione: i marcatori di fase cercati su tutto
    # il file (vLLM stampa centinaia di righe e li spingerebbe fuori dalla coda,
    # facendo *regredire* la fase mostrata) e la coda vera per il pannello log.
    # Il sentinella distingue "mai preparata" da "preparazione appena avviata".
    markers = "|".join(marker for marker, _ in _PROVISION_PHASES) + "|" + "|".join(_PROVISION_READY)
    # La sonda di liveness è la parte decisiva: senza, un processo morto a metà
    # resta indistinguibile da uno lento, e la UI mente per sempre.
    cmd = _ssh_base_args(host, port, user) + [
        # Le parentesi quadre impediscono al pattern di trovare la sonda stessa:
        # `pgrep -f` confronta le righe di comando, inclusa la propria shell.
        f"if pgrep -f '[t]abularium_setup_cloud_vllm.sh|[s]erve[.]py|[v]llm.entrypoints' > /dev/null 2>&1; "
        f"then echo {_ALIVE_MARKER}; else echo {_DEAD_MARKER}; fi; "
        f"if [ -f {REMOTE_LOG_PATH} ]; then "
        f"echo {_PHASE_SECTION}; grep -E {shlex.quote(markers)} {REMOTE_LOG_PATH} | tail -n 40; "
        f"echo {_TAIL_SECTION}; tail -n {lines} {REMOTE_LOG_PATH}; "
        "else echo tabularium-log-missing; fi"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("Comando ssh non disponibile sul sistema") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Timeout leggendo il log remoto") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"Log remoto non leggibile: {(proc.stderr or '').strip()[:300]}")
    output = proc.stdout.splitlines()
    alive = any(line.strip() == _ALIVE_MARKER for line in output)
    if any("tabularium-log-missing" in line for line in output):
        return {"lines": [], "ready": False, "phase": "absent", "failed": False, "error": "", "present": False}
    try:
        phase_start = output.index(_PHASE_SECTION)
        tail_start = output.index(_TAIL_SECTION)
        markers_seen = output[phase_start + 1:tail_start]
        log = output[tail_start + 1:]
    except ValueError:
        # Formato inatteso (shell diversa, comando troncato): la coda resta
        # leggibile anche senza la sezione dei marcatori.
        markers_seen, log = output, output
    ready = any(marker in line for line in markers_seen for marker in _PROVISION_READY)
    phase = "" if ready else next(
        (name for marker, name in _PROVISION_PHASES if any(marker in line for line in markers_seen)),
        "",
    )
    # Prima le diagnostiche dello script ("!!"), poi l'ultimo errore del server.
    failure = next((line for line in reversed(log) if line.startswith("!!")), "")
    if not failure:
        failure = next(
            (line for line in reversed(log) if any(marker in line for marker in _PROVISION_ERRORS)),
            "",
        )
    # Nessun processo vivo e nessun server in ascolto: la preparazione è finita
    # male, anche quando il log si interrompe senza dirlo.
    failed = bool(failure) or (not ready and not alive)
    if failed and not failure:
        failure = log[-1] if log else "il processo di setup non è più in esecuzione"
    # Il processo morto senza riga di errore è il caso peggiore: lo script è
    # uscito per un comando fallito sotto `set -e` e il log finisce a metà.
    return {
        "lines": log,
        "ready": ready,
        "phase": "failed" if failed and not ready else ("ready" if ready else (phase or "starting")),
        "failed": failed,
        "error": failure[:400],
        "present": True,
        "alive": alive,
    }


# --- RUNPOD API INTEGRATION -----------------------------------------------------
# Stesso schema di Vast.ai: elenco/avvio/pausa/distruzione dei Pod già
# noleggiati dall'utente (non crea nuovi Pod). Verificato contro
# docs.runpod.io/api-reference: base REST, Bearer auth, `desiredStatus`
# RUNNING/EXITED/TERMINATED.

RUNPOD_API_BASE = "https://rest.runpod.io/v1"


def list_runpod_pods(api_key: str, *, owner_id: int | None = None) -> list[dict[str, Any]]:
    """Recupera l'elenco dei Pod noleggiati dall'account RunPod."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di RunPod non specificata.")

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{RUNPOD_API_BASE}/pods", headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Errore API RunPod ({resp.status_code}): {resp.text}")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Impossibile contattare RunPod: {exc}") from exc

    # La lista è la root della risposta (non annidata in "pods" come Vast.ai).
    pods = data if isinstance(data, list) else data.get("pods", [])
    out = []
    for pod in pods:
        gpu = pod.get("gpu") or {}
        ports: dict[str, int] = pod.get("portMappings") or {}
        public_ip = pod.get("publicIp")
        ssh_port = ports.get("22")
        status = pod.get("desiredStatus") or "UNKNOWN"
        resource_id = pod.get("id")
        out.append({
            "id": resource_id,
            "status": status,
            "gpu_name": gpu.get("displayName"),
            "num_gpus": gpu.get("count", 1),
            "dph_total": pod.get("costPerHr"),
            "ssh_host": public_ip if ssh_port else None,
            "ssh_port": ssh_port,
            "is_running": status == "RUNNING",
            "label": pod.get("name") or f"{gpu.get('count', 1)}x {gpu.get('displayName', 'GPU')}",
            "ports": ports,
        })
        if resource_id is not None:
            normalized = str(status).lower()
            state = "running" if normalized == "running" else (
                "deleted" if normalized in {"terminated", "deleted"}
                else "stopped" if normalized in {"exited", "stopped", "paused"}
                else "running"
            )
            track_cloud_resource(
                "runpod", resource_id, owner_id=owner_id,
                hourly_rate=pod.get("costPerHr"), state=state,
                metadata={"provider_status": status},
            )
    return _with_cost("runpod", out)


def create_runpod_pod(
    api_key: str,
    *,
    name: str = "tabularium-training",
    image: str = "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04",
    gpu_type_ids: list[str] | None = None,
    volume_gb: int = 40,
    ports: list[str] | None = None,
    env: dict[str, str] | None = None,
    interruptible: bool = False,
) -> dict[str, Any]:
    """Crea un Pod persistente secondo il contratto REST RunPod corrente."""
    token = api_key.strip()
    if not token:
        raise ValueError("API Key di RunPod non specificata.")
    if not image.strip() or int(volume_gb) < 10:
        raise ValueError("immagine e almeno 10 GB di volume sono obbligatori")
    body = {
        "name": name.strip() or "tabularium-training",
        "imageName": image.strip(),
        "gpuTypeIds": [str(item).strip() for item in (gpu_type_ids or []) if str(item).strip()],
        "volumeInGb": int(volume_gb),
        "volumeMountPath": "/workspace",
        "ports": ports or ["8888/http", "22/tcp"],
        "env": {str(k): str(v) for k, v in (env or {}).items()},
        "interruptible": bool(interruptible),
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{RUNPOD_API_BASE}/pods", headers=headers, json=body)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Errore creazione Pod RunPod ({resp.status_code}): {resp.text}")
            data = resp.json() if resp.content else {}
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Impossibile contattare RunPod: {exc}") from exc
    return {"ok": True, "pod": data}


def control_runpod_pod(api_key: str, pod_id: str, action: str) -> dict[str, Any]:
    """Avvia ('start'), mette in pausa ('stop') o distrugge ('delete') un Pod RunPod."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di RunPod non specificata.")
    pod_id = str(pod_id).strip()
    if not pod_id:
        raise ValueError("ID Pod RunPod non specificato.")

    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=15.0) as client:
        if action == "start":
            resp = client.post(f"{RUNPOD_API_BASE}/pods/{pod_id}/start", headers=headers)
        elif action == "stop":
            resp = client.post(f"{RUNPOD_API_BASE}/pods/{pod_id}/stop", headers=headers)
        elif action == "delete":
            resp = client.delete(f"{RUNPOD_API_BASE}/pods/{pod_id}", headers=headers)
        else:
            raise ValueError(f"Azione RunPod non supportata: {action}")

        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Operazione fallita su RunPod ({resp.status_code}): {resp.text}")
        return {"ok": True, "action": action, "pod_id": pod_id}
