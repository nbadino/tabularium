"""Endpoint cloud (tunnel SSH, istanze Vast.ai) — riservati all'admin.

Staccati da `system.py` durante la modularizzazione self-hosted: controllano
infrastruttura (tunnel, fatturazione cloud) e richiedono autenticazione + ruolo
admin, a differenza di health/info che restano pubblici.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..services import auth as authsvc

router = APIRouter(tags=["cloud"], dependencies=[Depends(authsvc.get_current_user)])


def _admin(user: dict = Depends(authsvc.get_current_user)) -> dict:
    return authsvc.require_admin(user)


# --- Gestione Tunnel SSH da UI ------------------------------------------------
@router.get("/api/system/cloud/tunnel")
def get_cloud_tunnel(_admin: dict = Depends(_admin)) -> dict:
    """Restituisce lo stato del tunnel SSH gestito dal backend."""
    from ..services import cloud_manager

    st = cloud_manager.get_tunnel_status()
    return {
        "running": st.running,
        "host": st.host,
        "port": st.port,
        "user": st.user,
        "local_port": st.local_port,
        "remote_port": st.remote_port,
        "pid": st.pid,
        "error": st.error,
    }


@router.post("/api/system/cloud/tunnel/start")
def start_cloud_tunnel(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Avvia un tunnel SSH in background."""
    from ..services import cloud_manager

    host = payload.get("host", "").strip()
    port = payload.get("port")
    user = payload.get("user", "root").strip() or "root"
    key_path = payload.get("key_path")
    local_port = int(payload.get("local_port", 8888))
    remote_port = int(payload.get("remote_port", 8888))

    if not host or not port:
        raise HTTPException(status_code=400, detail="Host e porta SSH obbligatori.")

    try:
        st = cloud_manager.start_ssh_tunnel(
            host=host,
            port=int(port),
            user=user,
            key_path=key_path,
            local_port=local_port,
            remote_port=remote_port,
        )
        return {
            "ok": True,
            "running": st.running,
            "host": st.host,
            "port": st.port,
            "local_port": st.local_port,
            "pid": st.pid,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/tunnel/stop")
def stop_cloud_tunnel(_admin: dict = Depends(_admin)) -> dict:
    """Ferma il tunnel SSH in background."""
    from ..services import cloud_manager

    st = cloud_manager.stop_ssh_tunnel()
    return {"ok": True, "running": st.running}


# --- Gestione Istanze Vast.ai da UI -------------------------------------------
@router.post("/api/system/cloud/vast/instances")
def get_vast_instances(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Recupera le istanze dell'account Vast.ai."""
    from ..services import cloud_manager

    api_key = payload.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key di Vast.ai obbligatoria.")

    try:
        items = cloud_manager.list_vast_instances(api_key)
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/vast/control")
def control_vast(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Avvia o mette in pausa un'istanza Vast.ai da UI."""
    from ..services import cloud_manager

    api_key = payload.get("api_key", "").strip()
    instance_id = payload.get("instance_id")
    action = payload.get("action", "stop").strip()

    if not api_key or not instance_id:
        raise HTTPException(status_code=400, detail="API Key e ID istanza obbligatori.")

    try:
        res = cloud_manager.control_vast_instance(api_key, int(instance_id), action)
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
