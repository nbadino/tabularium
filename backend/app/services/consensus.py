"""Consensus: send the same pages to several models and see where they disagree.

A model taking part is a saved compute profile (endpoint, served model, adapter and its
own credential), so comparing two models never switches the global inference config.
A run starts one worker per model; each sends its pages through the prefill pipeline
with a read-only writer (`blockstore.reading_only`) and keeps the emitted blocks in
`consensus_outputs`. The annotations are never written. The Studio reads one page back
with `page_comparison`, which applies the pure metric in `domain/consensus.py`.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from ..adapters import blocks as blockstore
from ..adapters import compute_profiles as profilestore
from ..adapters import consensus as runstore
from ..domain import consensus as metric
from . import audit, inference, prefill
from .compute_profiles import describe
from .i18n import msg
from .model_adapters import get_adapter
from .url_security import validate_endpoint

MIN_MODELS = 2
MAX_MODELS = 6
MAX_PAGES = 2000
# The row the pre-v10 configuration seeded: it mirrors the global config and is not a
# model the user saved, so it is never offered for a comparison.
LEGACY_PROFILE = "legacy-active"
_VAULT_PREFIX = "consensus_profile_"

_ACTIVE: dict[int, threading.Event] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- profiles -------------------------------------------------------------------


def list_profiles() -> list[dict[str, Any]]:
    """The saved models a comparison can use."""
    return [
        describe(row)
        for row in profilestore.list_all()
        if row["name"] != LEGACY_PROFILE and row["purpose"] in {"inference", "both"}
    ]


def save_current_as_profile(
    name: str, actor: dict | None = None, lang: str | None = None,
) -> dict[str, Any]:
    """Copy the configured model, key included, into a new inactive profile.

    The key gets its own vault entry: the global config has one slot, which the next
    model chosen in Models overwrites. A key the OpenCode gateway already keeps on
    this machine is not copied; the profile reads it from there when it runs.
    """
    clean = " ".join(str(name or "").split())
    if not clean or len(clean) > 80:
        raise HTTPException(status_code=422, detail=msg("consensus_profile_name", lang))
    if any(row["name"] == clean for row in profilestore.list_all()):
        raise HTTPException(status_code=409, detail=msg("consensus_profile_exists", lang))
    cfg = inference.get_inference_config()
    endpoint = validate_endpoint(str(cfg["url"]))
    adapter = get_adapter(str(cfg["adapter_id"]))
    key = str(cfg.get("api_key") or "")
    if key and getattr(adapter.capabilities, "serve_backend", "") == "opencode-gateway":
        from . import opencode_gateway

        if key == opencode_gateway.read_api_key():
            key = ""
    from . import vault

    credential_ref = vault.put(f"{_VAULT_PREFIX}{uuid.uuid4().hex[:16]}", key) if key else None
    provider = str(cfg.get("provider") or "custom")
    try:
        profile_id = profilestore.insert({
            "name": clean,
            # The profile table's CHECK lists physical providers; a remote gateway
            # such as OpenCode is stored as `custom`, as `save_inference_config` does.
            "provider": provider if provider in inference._PROFILE_PROVIDERS else "custom",
            "purpose": "inference",
            "model_adapter_id": adapter.adapter_id,
            "served_model_name": str(cfg["model"]),
            "endpoint": endpoint,
            "credential_ref": credential_ref,
            "runtime_recipe_id": "consensus",
            "generation_profile_id": "default",
            "image_profile_id": "default",
        })
    except Exception:
        if credential_ref:
            vault.delete(credential_ref)
        raise
    from ..db import connect  # noqa: PLC0415 - the audit trail joins the caller's transaction

    with connect() as conn:
        audit.record(
            conn, actor, "compute_profile.created",
            resource_type="compute_profile", resource_id=profile_id,
        )
    return describe(profilestore.get(profile_id))


def delete_profile(profile_id: int, actor: dict | None = None, lang: str | None = None) -> None:
    row = profilestore.get(profile_id)
    if row is None or row["name"] == LEGACY_PROFILE:
        raise HTTPException(status_code=404, detail=msg("consensus_profile_not_found", lang))
    if row["active"]:
        raise HTTPException(status_code=409, detail=msg("consensus_profile_active", lang))
    profilestore.delete(profile_id)
    ref = str(row.get("credential_ref") or "")
    if ref.startswith(f"vault:{_VAULT_PREFIX}"):
        from . import vault

        vault.delete(ref)
    from ..db import connect  # noqa: PLC0415

    with connect() as conn:
        audit.record(
            conn, actor, "compute_profile.deleted",
            resource_type="compute_profile", resource_id=profile_id,
        )


# --- runs -----------------------------------------------------------------------


def _model_key(profile_id: int) -> str:
    return f"p{profile_id}"


def get_run(run_id: int, lang: str | None = None) -> dict[str, Any]:
    run = runstore.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    counts = runstore.counts(run_id)
    run["total"] = sum(counts.values())
    run["succeeded"] = counts.get("finished", 0)
    run["failed"] = counts.get("failed", 0)
    run["cancelled"] = counts.get("cancelled", 0)
    run["pending"] = counts.get("queued", 0) + counts.get("running", 0)
    return run


def list_runs(project_id: int) -> list[dict[str, Any]]:
    return [get_run(int(run["id"])) for run in runstore.list_runs(project_id)]


def create_run(
    project_id: int,
    page_ids: list[int],
    profile_ids: list[int],
    *,
    owner_id: int | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    pages = list(dict.fromkeys(int(pid) for pid in page_ids))
    profiles = list(dict.fromkeys(int(pid) for pid in profile_ids))
    if not pages or len(pages) > MAX_PAGES:
        raise HTTPException(status_code=422, detail=msg("consensus_pages_range", lang, max=MAX_PAGES))
    if not MIN_MODELS <= len(profiles) <= MAX_MODELS:
        raise HTTPException(
            status_code=422,
            detail=msg("consensus_models_range", lang, min=MIN_MODELS, max=MAX_MODELS),
        )
    models = []
    for profile_id in profiles:
        row = profilestore.get(profile_id)
        if row is None or row["name"] == LEGACY_PROFILE:
            raise HTTPException(status_code=404, detail=msg("consensus_profile_not_found", lang))
        models.append({
            "key": _model_key(profile_id),
            "profile_id": profile_id,
            "name": row["name"],
            "adapter_id": row["model_adapter_id"],
            "model_name": row["served_model_name"],
        })
    try:
        run_id = runstore.create(project_id, pages, models, owner_id=owner_id)
    except runstore.MissingPages as exc:
        raise HTTPException(status_code=404, detail=msg("consensus_pages_missing", lang)) from exc
    _start(run_id)
    return get_run(run_id)


def _start(run_id: int) -> None:
    cancel = threading.Event()
    with _LOCK:
        _ACTIVE[run_id] = cancel
    threading.Thread(
        target=_coordinate, args=(run_id, cancel), name=f"consensus-{run_id}", daemon=True,
    ).start()


def _coordinate(run_id: int, cancel: threading.Event) -> None:
    """Run every model in parallel, then derive the run's final state."""
    try:
        run = runstore.get(run_id)
        if run is None:
            return
        runstore.set_state(run_id, "running", started=_now())
        workers = [
            threading.Thread(
                target=_run_model, args=(run, model, cancel),
                name=f"consensus-{run_id}-{model['key']}", daemon=True,
            )
            for model in run["models"]
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        counts = runstore.counts(run_id)
        if cancel.is_set():
            state = "cancelled"
        elif counts.get("failed", 0):
            state = "finished_with_errors"
        else:
            state = "finished"
        runstore.set_state(run_id, state, ended=_now())
    except Exception as exc:  # noqa: BLE001
        runstore.set_state(run_id, "failed", ended=_now(), error=str(exc))
    finally:
        with _LOCK:
            _ACTIVE.pop(run_id, None)


def _recognize(project_id: int, page_id: int, client, cancel: threading.Event):
    """One model on one page: the blocks it returned, or why it failed."""
    blocks: list[dict[str, Any]] = []
    failure: str | None = None
    done = False
    opts = prefill.PrelabelOptions(mode="merge", model_mode="native")
    try:
        for event in prefill.model_prelabel_events(
            project_id, [page_id], opts, client, "it",
            cancel_event=cancel, writer=blockstore.reading_only,
        ):
            if event["type"] == "block":
                block = dict(event["block"])
                block.pop("id", None)
                blocks.append(block)
            elif event["type"] == "page_done":
                done = True
            elif event["type"] == "error":
                failure = str(event.get("message") or "recognition failed")
    except HTTPException as exc:
        failure = str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        failure = str(exc) or type(exc).__name__
    if failure is None and not done:
        failure = "cancelled" if cancel.is_set() else "no result"
    return blocks, failure


def _run_model(run: dict[str, Any], model: dict[str, Any], cancel: threading.Event) -> None:
    run_id, project_id = int(run["id"]), int(run["project_id"])
    outputs = runstore.outputs_for_model(run_id, model["key"])
    try:
        profile = profilestore.get(int(model["profile_id"]))
        if profile is None:
            raise RuntimeError(f"profile '{model['name']}' was deleted")
        client = inference.client_for_profile(profile)
    except Exception as exc:  # noqa: BLE001
        for output in outputs:
            runstore.mark_output(output["id"], "failed", error=str(exc), ended_at=_now())
        return
    for output in outputs:
        if cancel.is_set():
            runstore.mark_output(output["id"], "cancelled", ended_at=_now())
            continue
        runstore.mark_output(output["id"], "running", started_at=_now())
        blocks, failure = _recognize(project_id, int(output["page_id"]), client, cancel)
        if failure is None:
            runstore.mark_output(
                output["id"], "finished",
                blocks_json=json.dumps(blocks, ensure_ascii=False), ended_at=_now(),
            )
        elif cancel.is_set():
            runstore.mark_output(output["id"], "cancelled", ended_at=_now())
        else:
            runstore.mark_output(output["id"], "failed", error=failure, ended_at=_now())


def cancel_run(run_id: int) -> dict[str, Any]:
    with _LOCK:
        event = _ACTIVE.get(run_id)
    if event is not None:
        event.set()
    return get_run(run_id)


def reconcile_runs() -> None:
    """A restart keeps the finished outputs and closes only the interrupted queue."""
    with _LOCK:
        active_ids = set(_ACTIVE)
    runstore.reconcile(
        active_ids,
        message="backend restarted during the consensus run; start it again for the missing pages",
        ended=_now(),
    )


# --- one page -------------------------------------------------------------------


def page_comparison(
    page_id: int, run_id: int | None = None, lang: str | None = None,
) -> dict[str, Any]:
    """Every model's blocks for the page and where they disagree.

    Uses the latest run with a finished output for the page unless `run_id` says
    otherwise. `comparison` is None (not an invented agreement) when fewer than two
    models returned a result.
    """
    chosen = run_id if run_id is not None else runstore.latest_run_for_page(page_id)
    if chosen is None:
        return {"run_id": None, "models": [], "outputs": {}, "comparison": None}
    run = runstore.get(chosen)
    if run is None:
        raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    by_key = {row["model_key"]: row for row in runstore.page_outputs(chosen, page_id)}
    models, outputs = [], {}
    for model in run["models"]:
        row = by_key.get(model["key"])
        state = row["state"] if row else "missing"
        models.append({**model, "state": state, "error": row["error"] if row else None})
        if row and state == "finished":
            outputs[model["key"]] = row["blocks"]
    comparison = metric.compare_page(outputs) if len(outputs) >= MIN_MODELS else None
    return {"run_id": chosen, "models": models, "outputs": outputs, "comparison": comparison}
