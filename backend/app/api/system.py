"""Endpoint di sistema: health e informazioni ambiente.

Pubblici: `health`, `info` (usati da script e dalla barra di stato prima del
login). Riservati (richiedono login): catalogo modelli/profili/plugin e la
configurazione di inferenza — che è globale all'istanza e la scrive solo l'admin.
Gli endpoint cloud (tunnel, Vast.ai) vivono in `api/cloud.py`.
"""
from __future__ import annotations

import platform
import sys

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..services.model_adapters import list_adapters
from ..services.domain_profiles import list_profiles
from ..services.pipeline import list_plugins
from ..services.url_security import validate_endpoint
from ..services import backup as backupsvc
from ..services import compute_profiles as profilesvc
from ..schemas import ComputeProfileIn, ComputeProfileOut, SecretIn, SecretOut

from .. import config
from ..db import connect
from ..services import auth as authsvc

router = APIRouter(tags=["system"])


def _admin(user: dict = Depends(authsvc.get_current_user)) -> dict:
    return authsvc.require_admin(user)


@router.get("/api/system/backup", dependencies=[Depends(authsvc.get_current_user)])
def backup_status() -> dict:
    return {"integrity": backupsvc.integrity(), "items": backupsvc.list_backups()}


@router.post("/api/system/backup")
def create_backup(user: dict = Depends(_admin)) -> dict:
    return backupsvc.create_backup(reason="manual")


@router.get("/api/system/backup/{name}")
def download_backup(name: str, user: dict = Depends(_admin)) -> FileResponse:
    path = (backupsvc.backup_dir() / name).resolve()
    if path.parent != backupsvc.backup_dir().resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="backup non trovato")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.post("/api/system/backup/{name}/restore")
def restore_backup(name: str, confirm: bool = False, user: dict = Depends(_admin)) -> dict:
    if not confirm:
        raise HTTPException(status_code=409, detail="restore distruttivo: conferma richiesta")
    try:
        before = backupsvc.create_backup(reason="pre-restore")
        restored = backupsvc.restore(name)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"pre_backup": before, **restored}


@router.post("/api/system/secrets", response_model=SecretOut)
def put_secret(payload: SecretIn, user: dict = Depends(_admin)) -> SecretOut:
    """Salva o sostituisce un credential nel vault, senza risposta plaintext."""
    from ..services import vault
    try:
        ref = vault.put(payload.name, payload.value)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        from ..services import audit as auditsvc
        auditsvc.record(conn, user, "secret.updated", resource_type="secret", payload={"name": payload.name})
    return SecretOut(name=payload.name, ref=ref)


@router.delete("/api/system/secrets/{name}", response_model=SecretOut)
def delete_secret(name: str, user: dict = Depends(_admin)) -> SecretOut:
    """Revoca un credential; il valore non viene mai restituito."""
    from ..services import vault
    try:
        vault.delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with connect() as conn:
        from ..services import audit as auditsvc
        auditsvc.record(conn, user, "secret.deleted", resource_type="secret", payload={"name": name})
    return SecretOut(name=name.removeprefix("vault:"), ref=f"vault:{name.removeprefix('vault:')}", configured=False)


@router.get("/api/system/jobs")
def list_jobs(user: dict = Depends(_admin)) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, owner_id, project_id, provider, pid, remote_job_id, state, "
            "heartbeat_at, command_json, log_path, recovery_strategy, started_at, ended_at, exit_code, error "
            "FROM jobs ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return [dict(row) for row in rows]


@router.get("/api/system/compute-profiles", response_model=list[ComputeProfileOut])
def compute_profiles(user: dict = Depends(authsvc.get_current_user)) -> list[ComputeProfileOut]:
    authsvc.require_role(user, "editor")
    return [ComputeProfileOut(**item) for item in profilesvc.list_profiles()]


@router.post("/api/system/compute-profiles", response_model=ComputeProfileOut, status_code=201)
def create_compute_profile(payload: ComputeProfileIn, user: dict = Depends(_admin)) -> ComputeProfileOut:
    return ComputeProfileOut(**profilesvc.create_profile(payload.model_dump(), actor=user))


@router.post("/api/system/compute-profiles/{profile_id}/activate", response_model=ComputeProfileOut)
def activate_compute_profile(profile_id: int, user: dict = Depends(_admin)) -> ComputeProfileOut:
    return ComputeProfileOut(**profilesvc.activate(profile_id, actor=user))


@router.get("/api/system/model-adapters", dependencies=[Depends(authsvc.get_current_user)])
def model_adapters() -> dict:
    """Elenco delle capacità modello disponibili per il wizard.

    `export_ready` è sondato come le modalità prefill (`supports_export`):
    un adapter che non ha prompt verificati per tutte le famiglie d'export
    non deve comparire nel selettore della pagina Dataset — l'utente
    sceglierebbe un'opzione che lato builder finirebbe sempre in
    `NotImplementedError`."""
    from ..services.model_adapters import get_adapter, supports_export

    items = []
    for item in list_adapters():
        item["export_ready"] = supports_export(get_adapter(item["adapter_id"]))
        items.append(item)
    return {"items": items}


@router.get("/api/system/domain-profiles", dependencies=[Depends(authsvc.get_current_user)])
def domain_profiles() -> dict:
    return {"items": list_profiles()}


@router.get("/api/system/plugins", dependencies=[Depends(authsvc.get_current_user)])
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
        "capabilities": {
            "dashboard": True,
            "cpu_ocr": True,
            # Il serving/training CUDA locale è supportato direttamente su
            # Linux; su Windows il percorso supportato è WSL2, non il Python
            # nativo. macOS non espone CUDA NVIDIA.
            "local_cuda": platform.system() == "Linux",
            "remote_gpu": True,
            "cuda_note": "WSL2" if platform.system() == "Windows" else None,
        },
    }


@router.get("/api/system/prefill-engines", dependencies=[Depends(authsvc.get_current_user)])
def prefill_engines() -> dict:
    """Motori di prefill disponibili, e quale conviene usare di default."""
    from ..services import inference
    from ..services import ocr as ocrmod

    from ..services.model_adapters import get_adapter, supported_prefill_modes

    ocr_engine = ocrmod.available_engine()
    cfg = inference.get_inference_config()
    enabled = cfg.get("enabled", True)

    # Sonda cosa l'adapter attivo sa fare DAVVERO (v. `supported_prefill_modes`),
    # non un elenco dichiarativo che può disallinearsi: indipendente dalla
    # connettività, quindi calcolato anche a inferenza disattivata.
    try:
        adapter = get_adapter(cfg.get("adapter_id") or "monkeyocrv2-parsing")
    except ValueError:
        from ..services.model_adapters import MonkeyOCRv2ParsingAdapter

        adapter = MonkeyOCRv2ParsingAdapter()
    modes = supported_prefill_modes(adapter)

    if not enabled:
        return {
            "ocr": {"available": bool(ocr_engine), "engine": ocr_engine},
            "model": {
                "available": False,
                "enabled": False,
                "url": cfg["url"],
                "model": cfg["model"],
                "adapter_id": cfg.get("adapter_id", "monkeyocrv2-parsing"),
                "is_cloud": False,
                "latency_ms": None,
                "models_available": [],
                **modes,
            },
            "recommended": "ocr" if ocr_engine else None,
        }

    # Questo endpoint viene interrogato all'apertura dello Studio. Non deve
    # congelare la UI per il cold start di un endpoint serverless: la salute
    # del modello è un'indicazione, non una generazione. La chiamata esplicita
    # "Test connessione" mantiene invece il timeout scelto dall'utente.
    client = inference.get_vllm_client(timeout=3)
    test_res = client.test_connection(timeout=3.0)
    model_up = test_res["ok"]

    return {
        "ocr": {"available": bool(ocr_engine), "engine": ocr_engine},
        "model": {
            "available": model_up,
            "enabled": True,
            "url": client.url,
            "model": client.model,
            "adapter_id": cfg.get("adapter_id", "monkeyocrv2-parsing"),
            "is_cloud": client.is_cloud,
            "latency_ms": test_res.get("latency_ms"),
            "models_available": test_res.get("models_available", []),
            **modes,
        },
        # Il modello vince quando c'è ed è attivo; l'OCR resta il ripiego senza GPU.
        "recommended": "model" if model_up else ("ocr" if ocr_engine else None),
    }


@router.get("/api/system/inference", dependencies=[Depends(authsvc.get_current_user)])
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
            "adapter_id": cfg.get("adapter_id", "monkeyocrv2-parsing"),
            "has_api_key": bool(cfg.get("api_key")),
            # Gli header possono contenere token: restano server-only.
            "extra_headers": {},
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
        "adapter_id": cfg.get("adapter_id", "monkeyocrv2-parsing"),
        "has_api_key": bool(cfg.get("api_key")),
        "extra_headers": {},
        "timeout": cfg.get("timeout", 180),
        "max_pixels": cfg.get("max_pixels"),
        "is_cloud": client.is_cloud,
        "available": test_res["ok"],
        "latency_ms": test_res.get("latency_ms"),
        "models_available": test_res.get("models_available", []),
        "error": test_res.get("error"),
    }


@router.put("/api/system/inference")
def update_inference_settings(
    payload: dict,
    user: dict = Depends(authsvc.get_current_user),
) -> dict:
    """Aggiorna e salva la configurazione di inferenza (globale: solo admin)."""
    authsvc.require_admin(user)
    from ..services import inference

    if payload.get("url"):
        payload = {**payload, "url": validate_endpoint(str(payload["url"]))}
    inference.save_inference_config(payload)
    if "api_key" in payload:
        from ..services import audit as auditsvc
        with connect() as conn:
            auditsvc.record(
                conn,
                user,
                "inference.credential.deleted" if not str(payload.get("api_key") or "").strip() else "inference.credential.updated",
                resource_type="inference",
                payload={"configured": bool(str(payload.get("api_key") or "").strip())},
            )
    return get_inference_settings()


@router.post("/api/system/inference/test")
def test_inference_endpoint(payload: dict, user: dict = Depends(authsvc.get_current_user)) -> dict:
    """Testa un endpoint di inferenza generico (locale o cloud) senza salvare."""
    from ..services import inference

    authsvc.require_admin(user)
    url = validate_endpoint(payload.get("url") or config.VLLM_URL)
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
