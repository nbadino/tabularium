"""Gestione Cloud & Tunnel SSH automatizzata direttamente da UI.

Permette all'utente di:
1. Avviare e fermare un tunnel SSH locale con 1 click (senza terminale).
2. Interagire con le API di Vast.ai e RunPod per elencare, avviare,
   mettere in pausa e gestire le istanze GPU direttamente dall'interfaccia.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .. import config

# Stato singleton in memoria per il tunnel SSH attivo
_ACTIVE_TUNNEL_PROC: subprocess.Popen | None = None
_ACTIVE_TUNNEL_INFO: dict[str, Any] = {}


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


def get_tunnel_status() -> TunnelStatus:
    """Restituisce lo stato attuale del tunnel SSH gestito dall'app."""
    global _ACTIVE_TUNNEL_PROC, _ACTIVE_TUNNEL_INFO
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
            _ACTIVE_TUNNEL_PROC = None
            err = _ACTIVE_TUNNEL_INFO.get("last_error")
            return TunnelStatus(running=False, error=err or f"Processo terminato con codice {poll_res}")
    return TunnelStatus(running=False)


def start_ssh_tunnel(
    host: str,
    port: int,
    user: str = "root",
    key_path: str | None = None,
    local_port: int = 8888,
    remote_port: int = 8888,
) -> TunnelStatus:
    """Avvia un tunnel SSH in background inoltrando 127.0.0.1:local_port a remote_port."""
    global _ACTIVE_TUNNEL_PROC, _ACTIVE_TUNNEL_INFO

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
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
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
    }

    return get_tunnel_status()


def stop_ssh_tunnel() -> TunnelStatus:
    """Interrompe il tunnel SSH in background."""
    global _ACTIVE_TUNNEL_PROC, _ACTIVE_TUNNEL_INFO
    if _ACTIVE_TUNNEL_PROC is not None:
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
    _ACTIVE_TUNNEL_INFO = {}
    return get_tunnel_status()


# --- VAST.AI API INTEGRATION --------------------------------------------------

VAST_API_BASE = "https://console.vast.ai/api/v0"


def list_vast_instances(api_key: str) -> list[dict[str, Any]]:
    """Recupera l'elenco delle istanze noleggiate dall'account Vast.ai."""
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("API Key di Vast.ai non specificata.")

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{VAST_API_BASE}/instances", headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Errore API Vast.ai ({resp.status_code}): {resp.text}")
            data = resp.json()
    except Exception as exc:
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
    return out


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
