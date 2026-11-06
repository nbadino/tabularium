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
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from ..adapters import blocks as blockstore
from ..adapters import compute_profiles as profilestore
from ..adapters import consensus as runstore
from ..domain import consensus as metric
from . import audit, consensus_decisions, inference, prefill
from .compute_profiles import describe
from .i18n import localize_detail, msg
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


# Page summaries of a run, by the outputs they were computed from (a retry that
# finishes a model again changes the key). Bounded: a run has at most MAX_PAGES.
_SUMMARIES: dict[tuple, dict[str, Any]] = {}


def _page_summary(run_id: int, page_id: int, outputs: dict[str, list[dict]]) -> dict[str, Any]:
    comparison = metric.compare_page(outputs)
    open_cells = {"digits": 0, "text": 0, "missing": 0}
    for cluster in comparison["clusters"]:
        table = cluster["table"]
        if cluster["missing"]:
            open_cells["missing"] += 1
        elif table is not None:
            for kind in open_cells:
                open_cells[kind] += table["kinds"][kind]
        elif cluster["text_kind"] in ("digits", "text"):
            open_cells[cluster["text_kind"]] += 1
    return {
        "agreement": comparison["agreement"],
        "open": open_cells,
        "conventions": comparison["conventions"],
    }


def run_pages(run_id: int, lang: str | None = None) -> list[dict[str, Any]]:
    """Each page of a run: every model's state and, once two models have read it,
    the agreement and what is left to look at (numbers, words, missing readings)."""
    get_run(run_id, lang)
    pages: dict[int, dict[str, Any]] = {}
    for row in runstore.run_page_states(run_id):
        page = pages.setdefault(row["page_id"], {
            "page_id": row["page_id"], "name": row["rel_path"], "states": {}, "errors": {},
            "agreement": None, "open": None, "conventions": None, "_ended": [],
        })
        page["states"][row["model_key"]] = row["state"]
        if row["error"]:
            page["errors"][row["model_key"]] = localize_detail(row["error"], lang)
        if row["state"] == "finished":
            page["_ended"].append((row["model_key"], row["ended_at"]))
    out = []
    for page in pages.values():
        ended = tuple(sorted(page.pop("_ended")))
        if len(ended) >= MIN_MODELS:
            key = (run_id, page["page_id"], ended)
            summary = _SUMMARIES.get(key)
            if summary is None:
                outputs = {
                    output["model_key"]: output["blocks"]
                    for output in runstore.page_outputs(run_id, page["page_id"])
                    if output["state"] == "finished"
                }
                summary = _page_summary(run_id, page["page_id"], outputs)
                if len(_SUMMARIES) > MAX_PAGES:
                    _SUMMARIES.clear()
                _SUMMARIES[key] = summary
            page.update(summary)
        out.append(page)
    return out


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


# A model failing this many pages in a row with the same error stops: the rest of
# its pages are marked failed at once instead of each costing a request.
STOP_AFTER_SAME_ERRORS = 3


def _error_signature(error: str) -> str:
    """Two errors are the same when they differ only in numbers (request ids, times)."""
    return re.sub(r"\d+", "#", error.strip().lower())[:160]


def _run_model(run: dict[str, Any], model: dict[str, Any], cancel: threading.Event) -> None:
    run_id, project_id = int(run["id"]), int(run["project_id"])
    outputs = runstore.outputs_for_model(run_id, model["key"])
    if not any(output["state"] == "queued" for output in outputs):
        return
    try:
        profile = profilestore.get(int(model["profile_id"]))
        if profile is None:
            raise RuntimeError(f"profile '{model['name']}' was deleted")
        client = inference.client_for_profile(profile)
    except Exception as exc:  # noqa: BLE001
        for output in outputs:
            if output["state"] == "queued":
                runstore.mark_output(output["id"], "failed", error=str(exc), ended_at=_now())
        return
    last_error: str | None = None
    streak = 0
    stopped: str | None = None
    for output in outputs:
        if output["state"] != "queued":
            continue  # finished in an earlier attempt of this run
        if cancel.is_set():
            runstore.mark_output(output["id"], "cancelled", ended_at=_now())
            continue
        if stopped is not None:
            runstore.mark_output(output["id"], "failed", error=stopped, ended_at=_now())
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
            signature = _error_signature(failure)
            streak = streak + 1 if signature == last_error else 1
            last_error = signature
            if streak >= STOP_AFTER_SAME_ERRORS:
                # Out of credits, a revoked key, a model gone: every page left would
                # fail the same way, one request at a time. Stop and say why; a retry
                # sends them again once the cause is fixed.
                stopped = msg("consensus_model_stopped", None, n=streak, error=failure)
            continue
        last_error, streak = None, 0


def cancel_run(run_id: int) -> dict[str, Any]:
    with _LOCK:
        event = _ACTIVE.get(run_id)
    if event is not None:
        event.set()
    return get_run(run_id)


def retry_failed(run_id: int, lang: str | None = None) -> dict[str, Any]:
    """Send again only what failed or was cancelled; finished outputs are kept."""
    run = get_run(run_id, lang)
    with _LOCK:
        active = run_id in _ACTIVE
    if active or run["state"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail=msg("consensus_run_active", lang))
    if not runstore.requeue_failed(run_id):
        raise HTTPException(status_code=409, detail=msg("consensus_nothing_to_retry", lang))
    _start(run_id)
    return get_run(run_id, lang)


def adopt_output(
    run_id: int,
    model_key: str,
    page_ids: list[int] | None = None,
    *,
    replace_human: bool = False,
    actor: dict | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Write one model's saved reading as the drafts of the pages, without asking it again.

    The run already holds what the model returned, so the comparison doubles as the
    recognition: the blocks (grids included) go in as unverified drafts with the
    model's `prefill_source`, as a prefill would write them. The drafts already there
    are replaced. A page with blocks a person drew or verified is skipped and named,
    unless `replace_human` says to replace those too (the UI asks first). The page
    revision advances, so an approval lapses and an open Studio reloads.
    """
    run = get_run(run_id, lang)
    model = next((m for m in run["models"] if m["key"] == model_key), None)
    if model is None:
        raise HTTPException(status_code=404, detail=msg("consensus_model_not_found", lang))
    outputs = runstore.finished_outputs(run_id, model_key, page_ids)
    found = {output["page_id"] for output in outputs}
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = [
        {"page_id": pid, "reason": "no_result", "blocks": 0} for pid in (page_ids or []) if pid not in found
    ]
    with blockstore.writing() as writer:
        for output in outputs:
            page_id = output["page_id"]
            human = writer.human_blocks(page_id)
            if human and not replace_human:
                skipped.append({"page_id": page_id, "reason": "human_work", "blocks": human})
                continue
            writer.replace_blocks(page_id, "replace_all" if replace_human else "replace_drafts")
            for index, block in enumerate(output["blocks"], start=1):
                block_id = writer.insert_block(
                    page_id,
                    label=str(block.get("label") or "Text"),
                    points=block["points"],
                    content=str(block.get("content") or ""),
                    order_idx=int(block.get("order_idx") or index),
                    prefill_source=str(block.get("prefill_source") or f"model:{model['name']}"),
                )
                grid = block.get("grid")
                if grid and grid.get("rows"):
                    writer.upsert_grid(block_id, grid)
            revision = writer.bump_revision(page_id)
            written.append({"page_id": page_id, "blocks": len(output["blocks"]), "annotation_revision": revision})
    for page in written:
        consensus_decisions.record_adoption(
            run, page["page_id"], "adopt_page", model_key, actor=actor,
            after={"blocks": page["blocks"], "replace_human": replace_human},
        )
    if written:
        audit.record_event(
            "consensus.adopted", actor, resource_type="consensus_run", resource_id=run_id,
            payload={"model": model["name"], "pages": [w["page_id"] for w in written], "replace_human": replace_human},
        )
    return {"run_id": run_id, "model": model, "written": written, "skipped": skipped}


def adopt_block(
    run_id: int,
    model_key: str,
    page_id: int,
    index: int,
    *,
    actor: dict | None = None,
    lang: str | None = None,
) -> dict[str, Any]:
    """Add one block of a model's saved reading to the page, as an unverified draft.

    For a table only the models read: the page view does not replace tables (they
    are corrected cell by cell), so the table, grid included, goes in as a draft
    the table editor can open. Nothing on the page is removed or changed. The
    revision advances, so an open Studio reloads.
    """
    run = get_run(run_id, lang)
    model = next((m for m in run["models"] if m["key"] == model_key), None)
    if model is None:
        raise HTTPException(status_code=404, detail=msg("consensus_model_not_found", lang))
    output = next(iter(runstore.finished_outputs(run_id, model_key, [page_id])), None)
    if output is None or not 0 <= index < len(output["blocks"]):
        raise HTTPException(status_code=404, detail=msg("consensus_block_not_found", lang))
    block = output["blocks"][index]
    with blockstore.writing() as writer:
        block_id = writer.insert_block(
            page_id,
            label=str(block.get("label") or "Text"),
            points=block["points"],
            content=str(block.get("content") or ""),
            order_idx=int(block.get("order_idx") or index + 1),
            prefill_source=str(block.get("prefill_source") or f"model:{model['name']}"),
        )
        grid = block.get("grid")
        if grid and grid.get("rows"):
            writer.upsert_grid(block_id, grid)
        revision = writer.bump_revision(page_id)
    consensus_decisions.record_adoption(
        run, page_id, "add_table", model_key, block_id=block_id, actor=actor,
        after={"block_id": block_id, "index": index},
    )
    audit.record_event(
        "consensus.block_adopted", actor, resource_type="consensus_run", resource_id=run_id,
        payload={"model": model["name"], "page_id": page_id, "index": index, "block_id": block_id},
    )
    return {"run_id": run_id, "model": model, "page_id": page_id, "block_id": block_id, "annotation_revision": revision}


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
        return {"run_id": None, "models": [], "outputs": {}, "comparison": None, "resolved": []}
    run = runstore.get(chosen)
    if run is None:
        raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    by_key = {row["model_key"]: row for row in runstore.page_outputs(chosen, page_id)}
    models, outputs = [], {}
    for model in run["models"]:
        row = by_key.get(model["key"])
        state = row["state"] if row else "missing"
        error = localize_detail(row["error"], lang) if row and row["error"] else None
        models.append({**model, "state": state, "error": error})
        if row and state == "finished":
            outputs[model["key"]] = row["blocks"]
    comparison = metric.compare_page(outputs) if len(outputs) >= MIN_MODELS else None
    # The areas settled so far, by anyone: shared, never kept in one browser.
    resolved = consensus_decisions.resolved_for(chosen, page_id)
    return {"run_id": chosen, "models": models, "outputs": outputs, "comparison": comparison, "resolved": resolved}


# Overlap a model's table needs with the user's table block to be "the same table".
TABLE_MATCH_IOU = 0.3


def _box(points: list) -> list[float]:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def block_comparison(block_id: int, grid: dict, lang: str | None = None) -> dict[str, Any]:
    """The models' readings of the table the user is editing, cell by cell.

    `grid` is the editor's current grid (it may hold unsaved edits), so the
    alignment follows inserted rows and columns. Each finished model of the page's
    latest run contributes the Table block that overlaps the user's block best.
    """
    empty = {"run_id": None, "models": [], "cells": [], "extra_rows": []}
    geometry = runstore.block_geometry(block_id)
    if geometry is None or len(geometry["points"]) < 2:
        return empty
    run_id = runstore.latest_run_for_page(geometry["page_id"])
    run = runstore.get(run_id) if run_id is not None else None
    if run is None:
        return empty
    mine = _box(geometry["points"])
    by_key = {row["model_key"]: row for row in runstore.page_outputs(run_id, geometry["page_id"])}
    models, grids = [], {}
    for model in run["models"]:
        row = by_key.get(model["key"])
        if not row or row["state"] != "finished":
            continue
        best, best_iou = None, TABLE_MATCH_IOU
        for block in row["blocks"]:
            candidate = block.get("grid")
            if not candidate or not candidate.get("rows") or len(block.get("points") or []) < 2:
                continue
            value = metric.iou(mine, _box(block["points"]))
            if value >= best_iou:
                best, best_iou = candidate, value
        if best is not None:
            models.append({"key": model["key"], "name": model["name"]})
            grids[model["key"]] = best
    if len(grids) < MIN_MODELS:
        return {**empty, "run_id": run_id, "models": models}
    return {"run_id": run_id, "models": models, **metric.annotate_grid(grid, grids)}

