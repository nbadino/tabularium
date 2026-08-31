"""Modelli aggiunti liberamente dall'utente: un repo Hugging Face qualsiasi,
non uno degli adapter con protocollo OCR verificato in `model_adapters.py`.

Stesso principio di LM Studio: chiunque può digitare un repo e provarlo.
Nessun blocco per dimensione — solo un avviso (v. `model_registry.vram_warning`)
quando il checkpoint rischia di non entrare nella GPU rilevata. Il download e
il serve locale riusano `model_registry`/`serve_manager` esistenti: l'unica
differenza è che l'adapter non è uno dei singleton fissi in `_ADAPTERS`, ma
costruito al volo da questa tabella (v. `model_adapters.get_adapter`).
"""
from __future__ import annotations

import re
import shutil
import uuid

from .. import config
from ..db import connect

_ID_PREFIX = "custom-"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "model"


def _unique_id(conn, base_slug: str) -> str:
    candidate = f"{_ID_PREFIX}{base_slug}"
    if not conn.execute("SELECT 1 FROM custom_models WHERE id=?", (candidate,)).fetchone():
        return candidate
    # Collisione (stesso repo/nome aggiunto due volte): un suffisso corto
    # basta, non serve un contatore leggibile.
    return f"{candidate}-{uuid.uuid4().hex[:6]}"


def _out(row) -> dict:
    d = dict(row)
    d["trust_remote_code"] = bool(d["trust_remote_code"])
    return d


def list_all() -> list[dict]:
    # `model_adapters.list_adapters()` chiama questa funzione anche da
    # contesti che non hanno mai inizializzato il DB (es. test puri sugli
    # adapter): un DB non ancora migrato equivale a "nessun modello custom",
    # non a un errore (stesso principio di `model_registry.install_state`).
    try:
        with connect() as conn:
            return [
                _out(row)
                for row in conn.execute("SELECT * FROM custom_models ORDER BY created_at DESC").fetchall()
            ]
    except Exception:  # noqa: BLE001
        return []


def get(adapter_id: str) -> dict | None:
    if not adapter_id.startswith(_ID_PREFIX):
        return None
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM custom_models WHERE id=?", (adapter_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _out(row) if row else None


def create(payload: dict) -> dict:
    hf_repo = str(payload.get("hf_repo") or "").strip()
    if not hf_repo or "/" not in hf_repo:
        raise ValueError("hf_repo deve essere un repo Hugging Face nel formato 'org/nome'")
    display_name = str(payload.get("display_name") or hf_repo).strip()
    served_model_name = str(payload.get("served_model_name") or hf_repo.split("/")[-1]).strip()
    max_model_len = payload.get("max_model_len")
    gpu_memory_utilization = payload.get("gpu_memory_utilization")
    with connect() as conn:
        adapter_id = _unique_id(conn, _slug(display_name or hf_repo))
        conn.execute(
            "INSERT INTO custom_models "
            "(id, display_name, hf_repo, hf_revision, served_model_name, trust_remote_code, "
            " max_model_len, gpu_memory_utilization, extra_args) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                adapter_id,
                display_name,
                hf_repo,
                str(payload.get("hf_revision") or "").strip() or None,
                served_model_name,
                1 if payload.get("trust_remote_code") else 0,
                int(max_model_len) if max_model_len else None,
                float(gpu_memory_utilization) if gpu_memory_utilization else None,
                str(payload.get("extra_args") or "").strip(),
            ),
        )
        row = conn.execute("SELECT * FROM custom_models WHERE id=?", (adapter_id,)).fetchone()
    return _out(row)


def delete(adapter_id: str) -> None:
    row = get(adapter_id)
    if row is None:
        raise ValueError(f"modello custom sconosciuto: {adapter_id}")
    weights_dir = config.MODELS_DIR / adapter_id
    if weights_dir.exists():
        shutil.rmtree(weights_dir)
    with connect() as conn:
        conn.execute("DELETE FROM custom_models WHERE id=?", (adapter_id,))
