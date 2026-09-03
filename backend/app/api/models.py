"""API registro modelli: elenco, download, cancellazione.

Ricalca `api/cloud.py`: lettura per ogni utente loggato, scrittura (download,
cancellazione) riservata all'admin — modifica lo stato dell'istanza (disco,
processi), non di un singolo progetto.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..services import auth as authsvc
from ..services import custom_models as custom_models_svc
from ..services import inference, model_registry, serve_manager, huggingface_auth
from ..services.model_adapters import get_adapter

router = APIRouter(tags=["models"], dependencies=[Depends(authsvc.get_current_user)])


def _admin(user: dict = Depends(authsvc.get_current_user)) -> dict:
    return authsvc.require_admin(user)


@router.get("/api/models")
def list_models() -> dict:
    return {"items": model_registry.list_models()}


@router.post("/api/models/custom")
def add_custom_model(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Aggiunge un modello a piacere (repo Hugging Face qualsiasi): stesso
    principio di LM Studio, nessun blocco per dimensione — solo l'avviso di
    `model_registry.vram_warning` una volta scaricato."""
    try:
        return custom_models_svc.create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/models/custom/{adapter_id}")
def remove_custom_model(adapter_id: str, _admin: dict = Depends(_admin)) -> dict:
    """Rimuove del tutto un modello custom (definizione + pesi se scaricati).

    Diverso da `DELETE /api/models/{adapter_id}`, che su un modello custom
    cancella solo i pesi lasciando la definizione riusabile per un nuovo
    download."""
    try:
        custom_models_svc.delete(adapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/api/models/huggingface/auth")
def huggingface_auth_status(_admin: dict = Depends(_admin)) -> dict:
    return huggingface_auth.status()


@router.post("/api/models/huggingface/auth/start")
def huggingface_auth_start(_admin: dict = Depends(_admin)) -> dict:
    return huggingface_auth.start()


@router.post("/api/models/huggingface/auth/token")
def huggingface_auth_token(payload: dict, _admin: dict = Depends(_admin)) -> dict:
    """Collega l'account con un token utente: unico meccanismo esposto dal Hub."""
    try:
        return huggingface_auth.connect(str(payload.get("token") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/models/huggingface/auth/logout")
def huggingface_auth_logout(_admin: dict = Depends(_admin)) -> dict:
    return huggingface_auth.disconnect()


@router.post("/api/models/{adapter_id}/download")
def download_model(adapter_id: str, _admin: dict = Depends(_admin)) -> dict:
    try:
        return model_registry.start_download(adapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/models/{adapter_id}/download/cancel")
def cancel_model_download(adapter_id: str, _admin: dict = Depends(_admin)) -> dict:
    try:
        return model_registry.cancel_download(adapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/models/{adapter_id}/download/stream")
def stream_model_download(adapter_id: str, _user: dict = Depends(authsvc.get_current_user)) -> StreamingResponse:
    async def gen():
        last = None
        idle = 0
        while True:
            try:
                state = model_registry.install_state(adapter_id)
            except ValueError as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
                break
            tail = model_registry.download_log_tail(adapter_id)
            payload = json.dumps({**state, "log_tail": tail}, ensure_ascii=False)
            if payload != last:
                yield f"data: {payload}\n\n"
                last = payload
                idle = 0
            else:
                idle += 1
            if not state["downloading"] and state["state"] in ("installed", "failed", "cancelled"):
                if idle >= 2:
                    yield "data: {\"state\": \"done\"}\n\n"
                    break
            await asyncio.sleep(1.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.delete("/api/models/{adapter_id}")
def delete_model(adapter_id: str, _admin: dict = Depends(_admin)) -> dict:
    try:
        return model_registry.delete_model(adapter_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Serving locale (Fase 2) ---------------------------------------------------
# Un solo modello alla volta: `serve_manager.start` ferma sempre quello attivo
# prima di avviarne un altro (una GPU consumer non ne regge due).

@router.get("/api/models/serve/status")
def serve_status() -> dict:
    """Stato del server locale **e** avanzamento dell'avvio.

    `POST /serve/start` avvia il lavoro in background e la prima volta può metterci minuti
    (venv vLLM, clone del repo ufficiale, caricamento dei pesi): questa rotta
    è quella che la UI interroga nel frattempo per dire a che punto è.
    `running` significa «processo vivo», `ready` significa «risponde».
    """
    st = serve_manager.get_status()
    prog = serve_manager.progress(st)
    return {
        "running": st.running,
        "starting": st.starting,
        "adapter_id": st.adapter_id or prog["adapter_id"],
        "port": st.port,
        "pid": st.pid,
        "error": st.error or prog["error"],
        "phase": prog["phase"],
        "ready": prog["phase"] == "ready",
        "elapsed_s": prog["elapsed_s"],
        "log_tail": prog["log_tail"],
    }


@router.post("/api/models/{adapter_id}/serve/start", status_code=202)
def serve_start(adapter_id: str, payload: dict | None = None, _admin: dict = Depends(_admin)) -> dict:
    """Avvia il server locale per `adapter_id` e lo punta come endpoint di
    inferenza attivo — stesso effetto di configurarlo a mano in Impostazioni,
    ma con un click dal registro modelli."""
    port = int((payload or {}).get("port") or 8888)
    try:
        st = serve_manager.start_async(adapter_id, port=port, owner_id=_admin.get("id"))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    adapter = get_adapter(adapter_id)
    inference.save_inference_config({
        "enabled": True,
        "url": f"http://127.0.0.1:{port}/v1",
        "model": adapter.capabilities.served_model_name or adapter_id,
        "adapter_id": adapter_id,
    })
    return {
        "running": st.running,
        "starting": st.starting,
        "adapter_id": st.adapter_id,
        "port": st.port,
        "pid": st.pid,
        "phase": st.phase,
    }


@router.post("/api/models/serve/stop")
def serve_stop(_admin: dict = Depends(_admin)) -> dict:
    st = serve_manager.stop()
    return {"running": st.running}
