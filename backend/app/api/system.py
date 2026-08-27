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
    """Motori di prefill disponibili, e quale conviene usare di default.

    Serve all'annotatore per non far ricadere l'utente sul motore peggiore per
    dimenticanza: `ocr` non ha alcuna nozione di tabella, `model` riconosce i
    blocchi e ne estrae la griglia. Quando il server di inferenza risponde, il
    default deve essere `model`.

    Il ping ha un timeout corto: questo endpoint viene chiamato all'apertura
    della pagina e non deve farla aspettare se vLLM non è acceso.
    """
    from ..services import inference
    from ..services import ocr as ocrmod

    ocr_engine = ocrmod.available_engine()
    client = inference.get_vllm_client(timeout=2)
    test_res = client.test_connection(timeout=2.0)
    model_up = test_res["ok"]

    return {
        "ocr": {"available": bool(ocr_engine), "engine": ocr_engine},
        "model": {
            "available": model_up,
            "url": client.url,
            "model": client.model,
            "is_cloud": client.is_cloud,
            "latency_ms": test_res.get("latency_ms"),
            "models_available": test_res.get("models_available", []),
        },
        # Il modello vince quando c'è; l'OCR resta il ripiego senza GPU.
        "recommended": "model" if model_up else ("ocr" if ocr_engine else None),
    }


@router.get("/api/system/inference")
def get_inference_settings() -> dict:
    """Restituisce la configurazione runtime di inferenza con stato di connessione."""
    from ..services import inference

    cfg = inference.get_inference_config()
    client = inference.VllmClient(
        url=cfg["url"],
        model=cfg["model"],
        api_key=cfg["api_key"],
        extra_headers=cfg["extra_headers"],
        timeout=min(cfg.get("timeout", 180), 5),
    )
    test_res = client.test_connection(timeout=3.0)

    return {
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

