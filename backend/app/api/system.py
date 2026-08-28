"""Endpoint di sistema: health e informazioni ambiente."""
from __future__ import annotations

import platform
import sys

from fastapi import APIRouter

from ..services.model_adapters import list_adapters
from ..services.domain_profiles import list_profiles
from ..services.pipeline import list_plugins

from .. import config
from ..db import connect

router = APIRouter(tags=["system"])


@router.get("/api/system/model-adapters")
def model_adapters() -> dict:
    """Elenco delle capacità modello disponibili per il wizard."""
    return {"items": list_adapters()}


@router.get("/api/system/domain-profiles")
def domain_profiles() -> dict:
    return {"items": list_profiles()}


@router.get("/api/system/plugins")
def plugins() -> dict:
    return {"items": list_plugins()}


@router.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "app": config.APP_NAME,
        "version": config.VERSION,
    }


@router.get("/api/system/info")
def system_info() -> dict:
    with connect() as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
    return {
        "app": config.APP_NAME,
        "version": config.VERSION,
        "data_dir": str(config.DATA_DIR),
        "db_path": str(config.DB_PATH),
        "schema_version": version["value"] if version else None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }


@router.get("/api/system/prefill-engines")
def prefill_engines() -> dict:
    """Motori di prefill disponibili, e quale conviene usare di default."""
    from ..services import inference
    from ..services import ocr as ocrmod

    ocr_engine = ocrmod.available_engine()
    cfg = inference.get_inference_config()
    enabled = cfg.get("enabled", True)

    if not enabled:
        return {
            "ocr": {"available": bool(ocr_engine), "engine": ocr_engine},
            "model": {
                "available": False,
                "enabled": False,
                "url": cfg["url"],
                "model": cfg["model"],
                "is_cloud": False,
                "latency_ms": None,
                "models_available": [],
            },
            "recommended": "ocr" if ocr_engine else None,
        }

    client = inference.get_vllm_client(timeout=2)
    test_res = client.test_connection(timeout=2.0)
    model_up = test_res["ok"]

    return {
        "ocr": {"available": bool(ocr_engine), "engine": ocr_engine},
        "model": {
            "available": model_up,
            "enabled": True,
            "url": client.url,
            "model": client.model,
            "is_cloud": client.is_cloud,
            "latency_ms": test_res.get("latency_ms"),
            "models_available": test_res.get("models_available", []),
        },
        # Il modello vince quando c'è ed è attivo; l'OCR resta il ripiego senza GPU.
        "recommended": "model" if model_up else ("ocr" if ocr_engine else None),
    }


@router.get("/api/system/inference")
def get_inference_settings() -> dict:
    """Restituisce la configurazione runtime di inferenza con stato di connessione."""
    from ..services import inference

    cfg = inference.get_inference_config()
    enabled = cfg.get("enabled", True)

    if not enabled:
        return {
            "enabled": False,
            "url": cfg["url"],
            "model": cfg["model"],
            "has_api_key": bool(cfg.get("api_key")),
            "extra_headers": cfg.get("extra_headers") or {},
            "timeout": cfg.get("timeout", 180),
            "max_pixels": cfg.get("max_pixels"),
            "is_cloud": False,
            "available": False,
            "latency_ms": None,
            "models_available": [],
            "error": "Inferenza GPU / Cloud disattivata manualmente",
        }

    client = inference.VllmClient(
        url=cfg["url"],
        model=cfg["model"],
        api_key=cfg["api_key"],
        extra_headers=cfg["extra_headers"],
        timeout=min(cfg.get("timeout", 180), 5),
    )
    test_res = client.test_connection(timeout=3.0)

    return {
        "enabled": True,
        "url": cfg["url"],
        "model": cfg["model"],
        "has_api_key": bool(cfg.get("api_key")),
        "extra_headers": cfg.get("extra_headers") or {},
        "timeout": cfg.get("timeout", 180),
        "max_pixels": cfg.get("max_pixels"),
        "is_cloud": client.is_cloud,
        "available": test_res["ok"],
        "latency_ms": test_res.get("latency_ms"),
        "models_available": test_res.get("models_available", []),
        "error": test_res.get("error"),
    }


@router.put("/api/system/inference")
def update_inference_settings(payload: dict) -> dict:
    """Aggiorna e salva la configurazione di inferenza nel database SQLite meta."""
    from ..services import inference

    inference.save_inference_config(payload)
    return get_inference_settings()


@router.post("/api/system/inference/test")
def test_inference_endpoint(payload: dict) -> dict:
    """Testa un endpoint di inferenza generico (locale o cloud) senza salvare."""
    from ..services import inference

    url = payload.get("url") or config.VLLM_URL
    model = payload.get("model") or config.VLLM_MODEL
    api_key = payload.get("api_key") or ""
    extra_headers = payload.get("extra_headers") or {}
    timeout = float(payload.get("timeout", 10.0))

    client = inference.VllmClient(
        url=url,
        model=model,
        api_key=api_key,
        extra_headers=extra_headers,
        timeout=int(timeout),
    )
    return client.test_connection(timeout=timeout)


# --- Gestione Tunnel SSH da UI ------------------------------------------------
@router.get("/api/system/cloud/tunnel")
def get_cloud_tunnel() -> dict:
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
def start_cloud_tunnel(payload: dict) -> dict:
    """Avvia un tunnel SSH in background."""
    from fastapi import HTTPException
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
def stop_cloud_tunnel() -> dict:
    """Ferma il tunnel SSH in background."""
    from ..services import cloud_manager

    st = cloud_manager.stop_ssh_tunnel()
    return {"ok": True, "running": st.running}


# --- Gestione Istanze Vast.ai da UI -------------------------------------------
@router.post("/api/system/cloud/vast/instances")
def get_vast_instances(payload: dict) -> dict:
    """Recupera le istanze dell'account Vast.ai."""
    from fastapi import HTTPException
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
def control_vast(payload: dict) -> dict:
    """Avvia o mette in pausa un'istanza Vast.ai da UI."""
    from fastapi import HTTPException
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


