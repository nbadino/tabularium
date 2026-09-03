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


def _credential(payload: dict, raw_key: str = "api_key", ref_key: str = "credential_ref") -> str:
    """Risolvi un credential dal vault senza mai copiarlo nella risposta."""
    ref = str(payload.get(ref_key) or "").strip()
    if ref:
        from ..services import vault
        value = vault.get(ref)
        if not value:
            raise HTTPException(status_code=400, detail="credential vault non configurato o non valido")
        return value
    return str(payload.get(raw_key) or "").strip()


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
            owner_id=_admin.get("id"),
        )
        # La porta locale può essere scelta dinamicamente (per evitare
        # collisioni con un runtime locale). Persistiamo subito l'endpoint
        # reale: la pagina Riconosci non deve restare ancorata alla vecchia
        # 8888 quando il tunnel è attivo su un'altra porta.
        from ..services import inference
        inference.save_inference_config({
            "url": f"http://127.0.0.1:{st.local_port}/v1",
            "provider": "vast",
        })
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

    api_key = _credential(payload)
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key di Vast.ai obbligatoria.")

    try:
        items = cloud_manager.list_vast_instances(api_key, owner_id=_admin.get("id"))
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/vast/offers")
def search_vast_offers(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Cerca offerte Vast.ai senza avviare un noleggio."""
    from ..services import cloud_manager

    try:
        items = cloud_manager.search_vast_offers(
            _credential(payload),
            gpu_name=payload.get("gpu_name", ""),
            num_gpus=payload.get("num_gpus", 1),
            max_dph=payload.get("max_dph"),
            min_reliability=payload.get("min_reliability", 0.95),
            instance_type=payload.get("instance_type", "on-demand"),
            disk_gb=int(payload.get("disk_gb") or 40),
            min_gpu_ram_gb=payload.get("min_gpu_ram_gb"),
            min_inet_down=payload.get("min_inet_down"),
            min_cuda=payload.get("min_cuda"),
            verified_only=bool(payload.get("verified_only", False)),
        )
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/vast/rent")
def rent_vast(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Noleggia l'offerta selezionata, dopo conferma esplicita nella UI."""
    from ..services import cloud_manager

    try:
        offer_id = int(payload.get("offer_id"))
        result = cloud_manager.rent_vast_instance(
            _credential(payload),
            offer_id,
            image=payload.get("image", ""),
            cuda_max_good=payload.get("cuda_max_good"),
            adapter_id=payload.get("adapter_id") or "",
            disk_gb=payload.get("disk_gb", 40),
            model=payload.get("model", "zenosai/MonkeyOCRv2-B-Parsing"),
            port=payload.get("port", 8888),
            api_key_for_server=_credential(payload, "server_api_key", "server_credential_ref"),
            monkeyocr_ref=payload.get("monkeyocr_ref", ""),
            tabularium_ref=payload.get("tabularium_ref", ""),
            prepare_server=bool(payload.get("prepare_server", False)),
        )
        if result.get("contract_id") is not None:
            cloud_manager.track_cloud_resource(
                "vast", result["contract_id"], owner_id=_admin.get("id"),
                hourly_rate=payload.get("dph_total"),
                metadata={"offer_id": offer_id},
            )
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/vast/control")
def control_vast(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Avvia o mette in pausa un'istanza Vast.ai da UI."""
    from ..services import cloud_manager

    api_key = _credential(payload)
    instance_id = payload.get("instance_id")
    action = payload.get("action", "stop").strip()

    if not api_key or not instance_id:
        raise HTTPException(status_code=400, detail="API Key e ID istanza obbligatori.")

    try:
        res = cloud_manager.control_vast_instance(api_key, int(instance_id), action)
        cloud_manager.track_cloud_resource("vast", instance_id, owner_id=_admin.get("id"), state=action)
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Vast.ai: prima configurazione guidata -------------------------------------
@router.post("/api/system/cloud/vast/account")
def vast_account(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Preflight: valida la API Key e riporta il credito residuo dell'account."""
    from ..services import cloud_manager

    try:
        return cloud_manager.vast_account(_credential(payload))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/system/cloud/vast/ssh-key")
def get_vast_ssh_key(_admin: dict = Depends(_admin)) -> dict:
    """Stato della chiave SSH dedicata al cloud (mai la privata nella risposta)."""
    from ..services import cloud_manager

    try:
        key = cloud_manager.local_ssh_key()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "exists": key["exists"],
        "fingerprint": key["fingerprint"],
        "key_type": key["key_type"],
        "public_key": key["public_key"],
        "key_path": key["key_path"],
    }


@router.post("/api/system/cloud/vast/ssh-key")
def ensure_vast_ssh_key(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Genera la chiave dedicata e la registra sull'account Vast.ai.

    Idempotente: rieseguirla su un account già configurato non crea duplicati.
    Con `instance_id` la allega anche a un'istanza creata prima della chiave.
    """
    from ..services import cloud_manager

    api_key = _credential(payload)
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key di Vast.ai obbligatoria.")
    try:
        result = cloud_manager.ensure_vast_ssh_key(api_key)
        instance_id = payload.get("instance_id")
        if instance_id:
            result["attached"] = cloud_manager.attach_vast_ssh_key(api_key, int(instance_id))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/api/system/cloud/vast/instance")
def get_vast_instance(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Stato di una singola istanza: il wizard lo interroga in polling."""
    from ..services import cloud_manager

    api_key = _credential(payload)
    instance_id = payload.get("instance_id")
    if not api_key or not instance_id:
        raise HTTPException(status_code=400, detail="API Key e ID istanza obbligatori.")
    try:
        return cloud_manager.get_vast_instance(api_key, int(instance_id), owner_id=_admin.get("id"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/vast/hostkey")
def pin_vast_host_key(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Fissa la host key dell'istanza nel known_hosts usato dal tunnel."""
    from ..services import cloud_manager

    host = str(payload.get("host") or "").strip()
    port = payload.get("port")
    if not host or not port:
        raise HTTPException(status_code=400, detail="Host e porta SSH obbligatori.")
    try:
        return cloud_manager.pin_ssh_host_key(host, int(port))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/system/cloud/vast/monkeyocr-ref")
def resolve_monkeyocr_ref(_admin: dict = Depends(_admin)) -> dict:
    """Ref pin-nabile del runner ufficiale, così l'utente non digita SHA a mano."""
    from ..services import cloud_manager

    try:
        return cloud_manager.resolve_monkeyocr_ref()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/system/cloud/vast/models")
def list_vast_models(_admin: dict = Depends(_admin)) -> dict:
    """Modelli servibili su GPU a noleggio, con la ricetta che li governa."""
    from ..services import serve_recipes

    return {"items": serve_recipes.remote_models()}


@router.post("/api/system/cloud/vast/provision")
def provision_vast_server(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Consegna lo script di setup all'istanza via SSH e lo avvia in background.

    Non serve alcun commit pubblicato: lo script inviato è quello del checkout
    locale, quindi il server remoto esegue esattamente il codice in uso qui.
    """
    from ..services import cloud_manager

    host = str(payload.get("host") or "").strip()
    port = payload.get("port")
    if not host or not port:
        raise HTTPException(status_code=400, detail="Host e porta SSH obbligatori.")
    try:
        return cloud_manager.provision_vast_server(
            host,
            int(port),
            user=str(payload.get("user") or "root"),
            model=payload.get("model") or "",
            adapter_id=payload.get("adapter_id") or "monkeyocrv2-parsing",
            remote_port=int(payload.get("remote_port") or 8888),
            monkeyocr_ref=str(payload.get("monkeyocr_ref") or ""),
            server_api_key=_credential(payload, "server_api_key", "server_credential_ref"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/system/cloud/vast/provision/log")
def read_provision_log(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Coda del log di setup remoto: è l'avanzamento mostrato nel wizard."""
    from ..services import cloud_manager

    host = str(payload.get("host") or "").strip()
    port = payload.get("port")
    if not host or not port:
        raise HTTPException(status_code=400, detail="Host e porta SSH obbligatori.")
    try:
        return cloud_manager.provision_log(
            host, int(port), user=str(payload.get("user") or "root"),
            lines=int(payload.get("lines") or 80),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Gestione Pod RunPod da UI -------------------------------------------------
@router.post("/api/system/cloud/runpod/pods")
def get_runpod_pods(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Recupera i Pod dell'account RunPod."""
    from ..services import cloud_manager

    api_key = _credential(payload, "api_key", "credential_ref")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key di RunPod obbligatoria.")

    try:
        items = cloud_manager.list_runpod_pods(api_key, owner_id=_admin.get("id"))
        return {"items": items}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/runpod/create")
def create_runpod(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Crea un Pod persistente dopo conferma esplicita dell'amministratore."""
    from ..services import cloud_manager

    try:
        result = cloud_manager.create_runpod_pod(
            _credential(payload),
            name=payload.get("name", "tabularium-training"),
            image=payload.get("image", "runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04"),
            gpu_type_ids=payload.get("gpu_type_ids") or [],
            volume_gb=payload.get("volume_gb", 40),
            ports=payload.get("ports") or ["8888/http", "22/tcp"],
            env=payload.get("env") or {},
            interruptible=bool(payload.get("interruptible", False)),
        )
        pod = result.get("pod") or {}
        if pod.get("id") is not None:
            cloud_manager.track_cloud_resource("runpod", pod["id"], owner_id=_admin.get("id"), state="running")
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/runpod/control")
def control_runpod(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Avvia o mette in pausa un Pod RunPod da UI."""
    from ..services import cloud_manager

    api_key = _credential(payload)
    pod_id = payload.get("pod_id")
    action = payload.get("action", "stop").strip()

    if not api_key or not pod_id:
        raise HTTPException(status_code=400, detail="API Key e ID Pod obbligatori.")

    try:
        res = cloud_manager.control_runpod_pod(api_key, str(pod_id), action)
        cloud_manager.track_cloud_resource("runpod", pod_id, owner_id=_admin.get("id"), state=action)
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Gestione Modal Serverless da UI -------------------------------------------
@router.get("/api/system/cloud/modal")
def get_modal_status(template: str | None = None, _admin: dict = Depends(_admin)) -> dict:
    """Stato Modal per una template: CLI, token, task in corso, endpoint dell'ultimo deploy."""
    from ..services import modal_manager

    try:
        return modal_manager.status(template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/system/cloud/modal/setup")
def start_modal_setup(_admin: dict = Depends(_admin)) -> dict:
    """Autenticazione Modal: apre il browser per approvare il token."""
    from ..services import modal_manager

    try:
      modal_manager.start_setup(owner_id=_admin.get("id"))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "status": modal_manager.status()}


@router.post("/api/system/cloud/modal/deploy")
def start_modal_deploy(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Deploy della template serverless vLLM scelta (immagine + pesi + endpoint)."""
    from ..services import modal_manager

    template_id = payload.get("template") or None
    try:
        modal_manager.start_deploy(
            template_id=template_id,
            api_key=_credential(payload) or None,
            keep_warm=bool(payload.get("keep_warm", False)),
            owner_id=_admin.get("id"),
        )
    except (RuntimeError, ValueError) as exc:
        code = 409 if isinstance(exc, RuntimeError) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"ok": True, "status": modal_manager.status(template_id)}


@router.post("/api/system/cloud/modal/stop")
def stop_modal_app(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Ferma l'app Modal selezionata e termina i suoi container."""
    from ..services import modal_manager

    template_id = payload.get("template") or None
    try:
        modal_manager.stop_app(template_id=template_id, owner_id=_admin.get("id"))
    except (RuntimeError, ValueError) as exc:
        code = 409 if isinstance(exc, RuntimeError) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return {"ok": True, "status": modal_manager.status(template_id)}
