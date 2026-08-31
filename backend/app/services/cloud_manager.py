"""Gestione Cloud & Tunnel SSH automatizzata direttamente da UI.

Permette all'utente di:
1. Avviare e fermare un tunnel SSH locale con 1 click (senza terminale).
2. Interagire con le API di Vast.ai e RunPod per elencare, avviare,
   mettere in pausa e gestire le istanze GPU direttamente dall'interfaccia.
"""
from __future__ import annotations

import os
import json
import shlex
import signal
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .. import config
from ..db import connect

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
        return True
    except (OSError, ProcessLookupError):
        return False


def _is_ssh_process(pid: int) -> bool:
    """Evita di segnalare un PID riciclato dopo un riavvio del backend."""
    if os.name != "posix":
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\\x00", b" ").decode(errors="ignore")
    except (OSError, UnicodeError):
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

    # Argomenti del comando SSH
    cmd = [
        "ssh",
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
        process_group = os.getpgid(pid) if hasattr(os, "getpgid") else pid
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

VAST_API_BASE = "https://console.vast.ai/api/v0"


def search_vast_offers(
    api_key: str,
    *,
    gpu_name: str = "",
    num_gpus: int = 1,
    max_dph: float | None = None,
    min_reliability: float = 0.95,
    instance_type: str = "on-demand",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """Cerca offerte noleggiabili, senza creare alcuna istanza."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di Vast.ai non specificata.")
    filters: dict[str, Any] = {
        "num_gpus": {"eq": max(1, int(num_gpus))},
        "reliability": {"gte": max(0.0, min(1.0, float(min_reliability)))},
        "rentable": {"eq": True},
        "order": [["dph_total", "asc"]],
        "limit": max(1, min(50, int(limit))),
    }
    if gpu_name.strip():
        filters["gpu_name"] = {"eq": gpu_name.strip()}
    if max_dph is not None and float(max_dph) > 0:
        filters["dph_total"] = {"lte": float(max_dph)}
    if instance_type == "interruptible":
        filters["type"] = "bid"
    else:
        filters["type"] = "ondemand"

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.put(f"{VAST_API_BASE}/search/asks/", headers=headers, json=filters)
            if resp.status_code != 200:
                raise RuntimeError(f"Errore ricerca offerte Vast.ai ({resp.status_code}): {resp.text}")
            data = resp.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Impossibile contattare Vast.ai: {exc}") from exc

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
        })
    return _with_cost("vast", [item for item in result if item["id"] is not None])


def rent_vast_instance(
    api_key: str,
    offer_id: int,
    *,
    image: str = "vastai/pytorch:cuda-12.4.1-auto",
    disk_gb: int = 40,
    model: str = "zenosai/MonkeyOCRv2-B-Parsing",
    port: int = 8888,
    api_key_for_server: str = "",
    prepare_server: bool = False,
) -> dict[str, Any]:
    """Noleggia un'offerta esplicita. L'azione è separata dalla ricerca."""
    token = api_key.strip()
    if not token:
        raise ValueError("API Key di Vast.ai non specificata.")
    if int(disk_gb) < 10:
        raise ValueError("Il disco deve essere almeno 10 GB.")
    body: dict[str, Any] = {
        "image": image,
        "disk": int(disk_gb),
        "runtype": "ssh_direct",
        # Vast documenta `env` come stringa di opzioni container; il vecchio
        # dict non era interpretato come port mapping dall'API REST.
        "env": f"-p {int(port)}:{int(port)}/http -p 22:22/tcp",
    }
    if prepare_server:
        secret = api_key_for_server.strip()
        safe_key = f" --api-key {shlex.quote(secret)}" if secret else ""
        body["onstart"] = (
            "apt-get update -qq && apt-get install -y -qq git curl && "
            "curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/"
            "scripts/cloud/setup_cloud_vllm.sh | bash -s --"
            f" --port {int(port)} --model {shlex.quote(str(model))}{safe_key}"
        )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.put(f"{VAST_API_BASE}/asks/{int(offer_id)}/", headers=headers, json=body)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Errore noleggio Vast.ai ({resp.status_code}): {resp.text}")
            data = resp.json() if resp.content else {}
            if isinstance(data, dict) and data.get("success") is False:
                raise RuntimeError(data.get("msg") or data.get("error") or "Vast.ai ha rifiutato il noleggio")
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Impossibile contattare Vast.ai: {exc}") from exc
    return {
        "ok": True,
        "offer_id": int(offer_id),
        "contract_id": data.get("new_contract") if isinstance(data, dict) else None,
        "instance": data,
    }


def list_vast_instances(api_key: str) -> list[dict[str, Any]]:
    """Recupera l'elenco delle istanze noleggiate dall'account Vast.ai."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di Vast.ai non specificata.")

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        # Trailing slash obbligatoria: Vast.ai risponde 301 senza, e httpx non
        # segue i redirect di default — la richiesta falliva sempre (301 !=
        # 200), non solo con chiave sbagliata. Riprodotto e confermato in
        # isolamento (curl senza slash → 301; con slash → risposta reale).
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{VAST_API_BASE}/instances/", headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Errore API Vast.ai ({resp.status_code}): {resp.text}")
            data = resp.json()
    except httpx.HTTPError as exc:
        # Solo errori di rete/connessione qui: un errore applicativo (status
        # non-200) è già un RuntimeError col messaggio giusto e non va
        # re-incartato in un generico "impossibile contattare" che ne
        # cancella il dettaglio (es. "401: chiave non valida").
        raise RuntimeError(f"Impossibile contattare Vast.ai: {exc}") from exc

    instances = data.get("instances", [])
    out = []
    for inst in instances:
        out.append({
            "id": inst.get("id"),
            "status": inst.get("actual_status") or inst.get("status_msg") or "unknown",
            "gpu_name": inst.get("gpu_name"),
            "num_gpus": inst.get("num_gpus", 1),
            "dph_total": inst.get("dph_total"),
            "ssh_host": inst.get("ssh_host"),
            "ssh_port": inst.get("ssh_port"),
            "is_running": (inst.get("actual_status") == "running"),
            "label": inst.get("label") or f"{inst.get('num_gpus', 1)}x {inst.get('gpu_name', 'GPU')}",
            "ports": inst.get("ports", {}),
        })
    return _with_cost("vast", out)


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


# --- RUNPOD API INTEGRATION -----------------------------------------------------
# Stesso schema di Vast.ai: elenco/avvio/pausa/distruzione dei Pod già
# noleggiati dall'utente (non crea nuovi Pod). Verificato contro
# docs.runpod.io/api-reference: base REST, Bearer auth, `desiredStatus`
# RUNNING/EXITED/TERMINATED.

RUNPOD_API_BASE = "https://rest.runpod.io/v1"


def list_runpod_pods(api_key: str) -> list[dict[str, Any]]:
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
        out.append({
            "id": pod.get("id"),
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
