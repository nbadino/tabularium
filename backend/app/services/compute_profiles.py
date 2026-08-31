"""Profili compute atomici per inferenza e training."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from fastapi import HTTPException

from .. import config
from ..db import connect
from . import audit
from .inference import VllmClient
from .model_adapters import get_adapter
from .url_security import validate_endpoint


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _out(row) -> dict:
    d = dict(row)
    d["hardware_profile"] = json.loads(d.pop("hardware_profile_json") or "{}")
    d["active"] = bool(d["active"])
    ref = d.get("credential_ref")
    if ref and str(ref).startswith("vault:"):
        from . import vault
        d["has_credential"] = vault.has(ref)
    elif ref and str(ref).startswith("env:"):
        d["has_credential"] = bool(os.environ.get(str(ref)[4:]))
    else:
        d["has_credential"] = bool(ref)
    d.pop("credential_ref", None)
    d["last_health_ok"] = None if d["last_health_ok"] is None else bool(d["last_health_ok"])
    return d


def _credential(ref: str | None) -> str:
    if not ref:
        return ""
    if ref.startswith("env:"):
        return os.environ.get(ref[4:], "")
    if ref.startswith("vault:"):
        from . import vault
        return vault.get(ref)
    # Secret values never belong in a profile row. A missing/unknown ref is
    # deliberately treated as no credential instead of being echoed.
    return ""


def ensure_legacy_profile() -> None:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM compute_profiles LIMIT 1").fetchone():
            return
        from . import inference
        cfg = inference.get_inference_config()
        conn.execute(
            "INSERT INTO compute_profiles(name,provider,purpose,model_adapter_id,served_model_name,endpoint,credential_ref,runtime_recipe_id,generation_profile_id,image_profile_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("legacy-active", "local", "both", cfg.get("adapter_id", "monkeyocrv2-parsing"), cfg.get("model", "MonkeyOCRv2"), cfg.get("url", config.VLLM_URL), "vault:inference", "legacy", "default", "default"),
        )
        conn.execute("UPDATE compute_profiles SET active=1")


def list_profiles() -> list[dict]:
    ensure_legacy_profile()
    with connect() as conn:
        return [_out(row) for row in conn.execute("SELECT * FROM compute_profiles ORDER BY active DESC, name").fetchall()]


def create_profile(payload: dict, actor: dict | None = None) -> dict:
    validate_endpoint(payload["endpoint"])
    get_adapter(payload["model_adapter_id"])
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO compute_profiles(name,provider,purpose,model_adapter_id,model_revision,served_model_name,endpoint,credential_ref,runtime_recipe_id,generation_profile_id,image_profile_id,hardware_profile_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (payload["name"], payload["provider"], payload["purpose"], payload["model_adapter_id"], payload.get("model_revision"), payload["served_model_name"], payload["endpoint"], payload.get("credential_ref"), payload.get("runtime_recipe_id"), payload.get("generation_profile_id"), payload.get("image_profile_id"), json.dumps(payload.get("hardware_profile") or {})),
        )
        row = conn.execute("SELECT * FROM compute_profiles WHERE id=?", (cur.lastrowid,)).fetchone()
        audit.record(conn, actor, "compute_profile.created", resource_type="compute_profile", resource_id=cur.lastrowid)
    return _out(row)


def activate(profile_id: int, actor: dict | None = None) -> dict:
    ensure_legacy_profile()
    with connect() as conn:
        row = conn.execute("SELECT * FROM compute_profiles WHERE id=?", (profile_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="profilo compute non trovato")
        endpoint = validate_endpoint(row["endpoint"])
        adapter = get_adapter(row["model_adapter_id"])
        key = _credential(row["credential_ref"])
        if row["credential_ref"] == "meta:inference_api_key":
            from . import inference
            key = inference.get_inference_config().get("api_key", "")
        client = VllmClient(url=endpoint, model=row["served_model_name"], api_key=key, timeout=10, adapter=adapter)
        health = client.test_connection(timeout=10)
        now = _now()
        if not health.get("ok"):
            conn.execute("UPDATE compute_profiles SET last_health_check=?, last_health_ok=0, last_health_error=?, updated_at=? WHERE id=?", (now, health.get("error", "health check fallito"), now, profile_id))
            raise HTTPException(status_code=409, detail={"message": "profilo non attivato: health check fallito", "health": health})
        conn.execute("UPDATE compute_profiles SET active=0, updated_at=?", (now,))
        conn.execute("UPDATE compute_profiles SET active=1, last_health_check=?, last_health_ok=1, last_health_error=NULL, updated_at=? WHERE id=?", (now, now, profile_id))
        # Aggiorna la vista legacy in una stessa transazione, mantenendo i
        # consumer esistenti mentre il frontend migra ai profili.
        for key, value in (("inference_url", endpoint), ("inference_model", row["served_model_name"]), ("inference_adapter_id", row["model_adapter_id"])):
            conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        audit.record(conn, actor, "compute_profile.activated", resource_type="compute_profile", resource_id=profile_id, payload={"previous": "rollback_available_in_history"})
        return _out(conn.execute("SELECT * FROM compute_profiles WHERE id=?", (profile_id,)).fetchone())
