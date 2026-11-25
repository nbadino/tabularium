"""Consensus: send the same pages to several models and see where they disagree.

A model taking part is a saved compute profile (endpoint, served model, adapter), so
comparing two models never switches the global inference config. Each model is reached
with the API key of the user who starts (or retries) the run (`model_keys`).
A run starts one worker per model; each sends its pages through the prefill pipeline
with a read-only writer (`blockstore.reading_only`) and keeps the emitted blocks in
`consensus_outputs`. The annotations are never written. The Studio reads one page back
with `page_comparison`, which applies the pure metric in `domain/consensus.py`.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from ..adapters import blocks as blockstore
from ..adapters import compute_profiles as profilestore
from ..adapters import consensus as runstore
from ..adapters import projects as projectstore
from ..domain import consensus as metric
from ..domain import otsl
from . import audit, consensus_decisions, inference, model_keys, prefill, table_guard
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
_VAULT_PREFIX = model_keys._VAULT_PREFIX

_ACTIVE: dict[int, threading.Event] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- profiles -------------------------------------------------------------------


def list_profiles(user: dict | None = None) -> list[dict[str, Any]]:
    """The saved models a comparison can use, with whether `user` has a key for each.

    `has_credential` (the profile's own key) is not shown: what counts is the key of
    the user looking, and never its value.
    """
    rows = [
        row for row in profilestore.list_all()
        if row["name"] != LEGACY_PROFILE and row["purpose"] in {"inference", "both"}
    ]
    keys = model_keys.statuses(rows, user)
    out = []
    for row in rows:
        item = describe(row)
        item.pop("has_credential", None)
        out.append({**item, **keys[int(row["id"])]})
    return out


def save_current_as_profile(
    name: str, actor: dict | None = None, lang: str | None = None,
) -> dict[str, Any]:
    """Copy the configured model into a new inactive profile; its key becomes the
    saving administrator's own key for it.

    Every other user adds their own key before using the model (`model_keys`). With
    users, the OpenCode key this machine keeps is the administrator's and is copied
    too: runs never fall back to it. In local mode it is not copied; the profile
    reads it from the machine when it runs.
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
    provider = str(cfg.get("provider") or "custom")
    if getattr(adapter.capabilities, "serve_backend", "") == "opencode-gateway" or provider == "opencode":
        from . import opencode_gateway

        machine_key = opencode_gateway.read_api_key()
        if model_keys._local():
            key = "" if key == machine_key else key
        else:
            key = key or machine_key
    profile_id = profilestore.insert({
        "name": clean,
        # The profile table's CHECK lists physical providers; a remote gateway
        # such as OpenCode is stored as `custom`, as `save_inference_config` does.
        "provider": provider if provider in inference._PROFILE_PROVIDERS else "custom",
        "purpose": "inference",
        "model_adapter_id": adapter.adapter_id,
        "served_model_name": str(cfg["model"]),
        "endpoint": endpoint,
        "credential_ref": None,
        "runtime_recipe_id": "consensus",
        "generation_profile_id": "default",
        "image_profile_id": "default",
        "key_required": model_keys.requires_key(adapter.adapter_id, provider, bool(key)),
    })
    if key:
        try:
            model_keys.save_owner_key(profile_id, actor, key)
        except Exception:
            profilestore.delete(profile_id)
            raise
    from ..db import connect  # noqa: PLC0415 - the audit trail joins the caller's transaction

    with connect() as conn:
        audit.record(
            conn, actor, "compute_profile.created",
            resource_type="compute_profile", resource_id=profile_id,
        )
    return next(item for item in list_profiles(actor) if item["id"] == profile_id)


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
        model_keys.require_usable(row, owner_id, lang)
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
    _start(run_id, owner_id)
    return get_run(run_id)


def _start(run_id: int, key_user_id: int | None) -> None:
    """Run in the background; every model is reached with `key_user_id`'s keys."""
    cancel = threading.Event()
    with _LOCK:
        _ACTIVE[run_id] = cancel
    threading.Thread(
        target=_coordinate, args=(run_id, cancel, key_user_id),
        name=f"consensus-{run_id}", daemon=True,
    ).start()


def _coordinate(run_id: int, cancel: threading.Event, key_user_id: int | None = None) -> None:
    """Run every model in parallel, then derive the run's final state."""
    try:
        run = runstore.get(run_id)
        if run is None:
            return
        runstore.set_state(run_id, "running", started=_now())
        workers = [
            threading.Thread(
                target=_run_model, args=(run, model, cancel, key_user_id),
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


def _recognize(
    project_id: int, page_id: int, client, cancel: threading.Event,
    opts: prefill.PrelabelOptions | None = None,
):
    """One model on one page: the blocks it returned, or why it failed."""
    blocks: list[dict[str, Any]] = []
    failure: str | None = None
    done = False
    opts = opts or prefill.PrelabelOptions(mode="merge", model_mode="native")
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

# Pages each model reads at the same time. One at a time left Gemini at about one
# page a minute (11 hours for a 560-page comparison); a busy gateway's 429s are
# waited out (`TRANSIENT_WAITS`), so a few requests in flight are safe.
PAGES_IN_PARALLEL = 4

# A gateway that answers "too many requests" or "overloaded" is busy, not broken: the
# same page is sent again after these waits (seconds) before it counts as failed. Three
# 429s in seven seconds used to stop Gemini on all 549 pages of a run.
TRANSIENT_WAITS: tuple[float, ...] = (15, 30, 60, 120, 240, 480, 600, 600, 600)
_TRANSIENT = re.compile(
    r"\b(429|503)\b|too many requests|rate.?limit|resource.?exhausted|overloaded|"
    r"service unavailable|try again later",
    re.IGNORECASE,
)


def is_transient(error: str) -> bool:
    """A busy or rate-limited model, worth waiting for; not a bad key or no credits."""
    return bool(_TRANSIENT.search(error or ""))


def _recognize_patiently(project_id: int, page_id: int, client, cancel: threading.Event):
    """`_recognize`, waiting out a busy gateway (`TRANSIENT_WAITS`) before giving up."""
    blocks, failure = _recognize(project_id, page_id, client, cancel)
    for wait in TRANSIENT_WAITS:
        if failure is None or cancel.is_set() or not is_transient(failure):
            break
        if cancel.wait(wait):
            break
        blocks, failure = _recognize(project_id, page_id, client, cancel)
    return blocks, failure


def _error_signature(error: str) -> str:
    """Two errors are the same when they differ only in numbers (request ids, times)."""
    return re.sub(r"\d+", "#", error.strip().lower())[:160]


def _run_model(
    run: dict[str, Any], model: dict[str, Any], cancel: threading.Event, key_user_id: int | None = None,
) -> None:
    run_id, project_id = int(run["id"]), int(run["project_id"])
    outputs = runstore.outputs_for_model(run_id, model["key"])
    if not any(output["state"] == "queued" for output in outputs):
        return
    try:
        profile = profilestore.get(int(model["profile_id"]))
        if profile is None:
            raise RuntimeError(f"profile '{model['name']}' was deleted")
        api_key = model_keys.key_for(profile, key_user_id)
        pending = [output for output in outputs if output["state"] == "queued"]
        # One client per worker: a client keeps per-conversation state (the gateway session).
        clients = [
            inference.client_for_profile(profile, api_key=api_key)
            for _ in range(max(1, min(PAGES_IN_PARALLEL, len(pending))))
        ]
    except Exception as exc:  # noqa: BLE001
        # Stored in Italian, the source language `localize_detail` reads back.
        error = (
            msg("saved_model_key_missing", "it", name=exc.name)
            if isinstance(exc, model_keys.MissingKey) else str(exc)
        )
        for output in outputs:
            if output["state"] == "queued":
                runstore.mark_output(output["id"], "failed", error=error, ended_at=_now())
        return
    queue = iter(pending)
    lock = threading.Lock()
    # The stop rule is the model's, not a worker's: the streak counts failures in the
    # order they end, whichever worker sent the page.
    state: dict[str, Any] = {"last_error": None, "streak": 0, "stopped": None}

    def take() -> dict[str, Any] | None:
        with lock:
            return next(queue, None)

    def work(client) -> None:
        while (output := take()) is not None:
            if cancel.is_set():
                runstore.mark_output(output["id"], "cancelled", ended_at=_now())
                continue
            with lock:
                stopped = state["stopped"]
            if stopped is not None:
                runstore.mark_output(output["id"], "failed", error=stopped, ended_at=_now())
                continue
            runstore.mark_output(output["id"], "running", started_at=_now())
            blocks, failure = _recognize_patiently(project_id, int(output["page_id"]), client, cancel)
            if failure is None:
                runstore.mark_output(
                    output["id"], "finished",
                    blocks_json=json.dumps(blocks, ensure_ascii=False), ended_at=_now(),
                )
                with lock:
                    state["last_error"], state["streak"] = None, 0
            elif cancel.is_set():
                runstore.mark_output(output["id"], "cancelled", ended_at=_now())
            else:
                runstore.mark_output(output["id"], "failed", error=failure, ended_at=_now())
                signature = _error_signature(failure)
                with lock:
                    state["streak"] = state["streak"] + 1 if signature == state["last_error"] else 1
                    state["last_error"] = signature
                    if state["streak"] >= STOP_AFTER_SAME_ERRORS and state["stopped"] is None:
                        # Out of credits, a revoked key, a model gone: every page left would
                        # fail the same way, one request at a time. Stop and say why; a retry
                        # sends them again once the cause is fixed.
                        state["stopped"] = msg(
                            "consensus_model_stopped", None, n=state["streak"], error=failure,
                        )

    workers = [
        threading.Thread(target=work, args=(client,), name=f"consensus-{run_id}-{model['key']}-{i}", daemon=True)
        for i, client in enumerate(clients)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()


def cancel_run(run_id: int) -> dict[str, Any]:
    with _LOCK:
        event = _ACTIVE.get(run_id)
    if event is not None:
        event.set()
    return get_run(run_id)


def retry_failed(
    run_id: int, lang: str | None = None, *, user_id: int | None = None,
) -> dict[str, Any]:
    """Send again only what failed or was cancelled; finished outputs are kept.

    Whoever retries starts the requests: the models are reached with their keys.
    """
    run = get_run(run_id, lang)
    with _LOCK:
        active = run_id in _ACTIVE
    if active or run["state"] in {"queued", "running"}:
        raise HTTPException(status_code=409, detail=msg("consensus_run_active", lang))
    for model in run["models"]:
        row = profilestore.get(int(model["profile_id"]))
        if row is not None:
            model_keys.require_usable(row, user_id, lang)
    if not runstore.requeue_failed(run_id):
        raise HTTPException(status_code=409, detail=msg("consensus_nothing_to_retry", lang))
    _start(run_id, user_id)
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
    """A restart resumes the interrupted runs: finished outputs are kept, the rest goes on.

    A deploy or a crash used to fail every page still in the queue and leave the user to
    start the run again by hand. The run carries on with its owner's keys, as a retry
    by the owner would.
    """
    with _LOCK:
        active_ids = set(_ACTIVE)
    for run in runstore.interrupted(active_ids):
        _start(int(run["id"]), run.get("owner_id"))


# --- a second reading made elsewhere ------------------------------------------------

# Model keys of an imported comparison. No saved model stands behind them, so the
# run's models carry `profile_id: None`: there is nothing to retry or to call.
IMPORT_FIRST = "imported-first"
IMPORT_SECOND = "imported-second"


def _cell_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _open_cell(
    grid: dict[str, Any], cells: dict[tuple[int, int], dict[str, Any]], row: int, column: int,
) -> dict[str, Any] | None:
    """An empty cell of its own at a place of the table that has none; None if there is no such place.

    Under a merged cell, the merge is split into single cells and its text stays in
    the first one. Past the last column, the table is widened with empty cells
    (`otsl.grid_to_otsl` refuses a table grown too large). Both change `grid` and
    `cells` in place.
    """
    rows, cols = int(grid["rows"]), int(grid["cols"])
    if not (0 <= row < rows and column >= 0):
        return None

    def add(r: int, c: int) -> None:
        cells[(r, c)] = {"r": r, "c": c, "rowspan": 1, "colspan": 1, "text": ""}
        grid["cells"].append(cells[(r, c)])

    if column >= cols:
        for r in range(rows):
            for c in range(cols, column + 1):
                add(r, c)
        grid["cols"] = column + 1
        return cells[(row, column)]
    for cover in list(grid["cells"]):
        r0, c0 = int(cover["r"]), int(cover["c"])
        height, width = int(cover.get("rowspan", 1)), int(cover.get("colspan", 1))
        if r0 <= row < r0 + height and c0 <= column < c0 + width:
            cover["rowspan"] = cover["colspan"] = 1
            for r in range(r0, r0 + height):
                for c in range(c0, c0 + width):
                    if (r, c) != (r0, c0):
                        add(r, c)
            return cells[(row, column)]
    return None


def _second_reading(
    blocks: list[dict[str, Any]], doubts: list[dict[str, Any]], structure_guard, model: str,
) -> list[dict[str, Any]]:
    """The first reading's blocks with every doubted cell as the second recognizer read it.

    A doubt names a cell by row and column of the table as the model delivered it
    (before the structure guard moves anything) and gives the text delivered and the
    other reading; with no column it is a whole row the second reading does not
    have, which is emptied. So the cells are replaced in the delivered table, and
    that table then becomes a grid the way the prefill makes the first one. A doubt
    may name a place where the first reading has no cell of its own and so no text:
    under a merged cell, or in a column past the last one. The second reading gets
    that cell (`_open_cell`). A doubt whose text is not what the table has there
    was computed on another table: ValueError, and nothing is stored for the page.
    """
    out = copy.deepcopy(blocks)
    tables = [block for block in out if (block.get("grid") or {}).get("rows")]
    if len(tables) != 1:
        raise ValueError(f"the doubts name cells of one table, the page has {len(tables)}")
    table = tables[0]
    grid = prefill.grid_from_table_content(str(table.get("content") or ""))
    if grid is None:
        raise ValueError("the table's own content describes no table")
    cells = {(int(cell["r"]), int(cell["c"])): cell for cell in grid["cells"]}
    for doubt in doubts:
        row, column = int(doubt["row"]), doubt.get("col")
        if column is None:
            if not 0 <= row < int(grid["rows"]):
                raise ValueError(f"a doubt names row {row}, which the table does not have")
            for (r, _c), cell in cells.items():
                if r == row:
                    cell["text"] = ""
            continue
        cell = cells.get((row, int(column)))
        if cell is None and not _cell_text(doubt.get("text")):
            cell = _open_cell(grid, cells, row, int(column))
        if cell is None or _cell_text(cell.get("text")) != _cell_text(doubt.get("text")):
            raise ValueError(
                f"the doubt at row {row}, column {column} does not match the table: "
                "it was computed on another reading"
            )
        cell["text"] = str(doubt.get("other") or "")
    grid["cells"].sort(key=lambda cell: (int(cell["r"]), int(cell["c"])))
    table["content"] = otsl.grid_to_otsl(grid)
    table["grid"] = prefill.finished_model_grid(grid, structure_guard, model)
    return out


def import_second_reading(
    project_id: int,
    source: str | Path,
    *,
    adapter_id: str,
    second_name: str,
    statuses: tuple[str, ...] = ("new",),
    owner_id: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    again: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Store a second reading made elsewhere as finished comparisons of this project.

    `source` holds one `<page id>.json` per page: what the Indra gateway's reader
    returns for it, plus `doubts`, the table cells a second recognizer read
    differently. Each page with doubts gets two readings in a consensus run: the
    file's own (`IMPORT_FIRST`, named after the model) and the same with the doubted
    cells as the second recognizer read them (`IMPORT_SECOND`, named `second_name`).
    The Studio then shows those cells as cells to check. Nothing on a page is written.

    Left out, and counted in the report: files of no page of the project, pages
    whose status is not in `statuses`, straightened pages (the boxes were read on the
    original pixels), pages without doubts, pages a comparison between other models
    already covers (the newest run is the one shown, so theirs would be hidden) and
    pages already imported (`again` imports those once more). A run holds at most
    `MAX_PAGES` pages. `dry_run` returns the plan and writes nothing. Raises
    ValueError for a request that cannot be honoured at all.
    """
    from ..adapters import pages as pagestore
    from . import pages as pagesvc
    from . import recognition

    folder = Path(source).expanduser()
    if not folder.is_dir():
        raise ValueError(f"not a directory: {folder}")
    second_name = second_name.strip()
    if not second_name:
        raise ValueError("the second reading needs a name")
    adapter = get_adapter(adapter_id)
    if getattr(adapter, "native_runner", None) != "indra":
        raise ValueError(
            f"'{adapter_id}': a second reading can be imported for the models of the Indra gateway only"
        )
    files = {
        int(path.stem): path for path in folder.iterdir()
        if path.suffix == ".json" and path.stem.isdigit()
    }
    pages = {
        int(page["id"]): page for page in pagestore.all_for_project(project_id)
        if int(page["id"]) in files
    }
    compared = runstore.compared_pages(project_id)
    skipped_status: dict[str, int] = {}
    skipped = {"transformed": 0, "already_imported": 0, "compared": 0, "no_doubts": 0}
    selected: list[int] = []
    for page_id in sorted(pages):
        page = pages[page_id]
        keys = compared.get(page_id, set())
        if page["status"] not in statuses:
            skipped_status[page["status"]] = skipped_status.get(page["status"], 0) + 1
        elif pagesvc.has_active_transform(page):
            skipped["transformed"] += 1
        elif IMPORT_SECOND in keys and not again:
            skipped["already_imported"] += 1
        elif keys - {IMPORT_FIRST, IMPORT_SECOND}:
            skipped["compared"] += 1
        else:
            selected.append(page_id)
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "files": len(files),
        "selected": 0,
        "skipped": {"not_in_project": len(files) - len(pages), "status": skipped_status, **skipped},
        "runs": [],
        "imported": 0,
        "failed": 0,
        "doubts": 0,
    }
    model = str(adapter.capabilities.served_model_name or adapter_id)
    models = [
        {"key": IMPORT_FIRST, "profile_id": None, "name": adapter.capabilities.display_name,
         "adapter_id": adapter_id, "model_name": model},
        {"key": IMPORT_SECOND, "profile_id": None, "name": second_name,
         "adapter_id": adapter_id, "model_name": model},
    ]
    opts = prefill.PrelabelOptions(
        mode="merge", model_mode="native", keep_source_pixels=True, table_promote=False,
    )
    structure_guard = table_guard.resolve(projectstore.settings_json(project_id))
    cancel = threading.Event()
    run_id, in_run = None, 0
    for page_id in selected:
        if limit is not None and report["selected"] >= max(0, int(limit)):
            break
        failure: str | None = None
        readings: dict[str, list[dict[str, Any]]] = {}
        doubts: list = []
        try:
            payload = recognition._imported_payload(files[page_id], pages[page_id])
            doubts = payload.get("doubts") or []
            if not isinstance(doubts, list):
                raise ValueError(f"{files[page_id].name}: `doubts` is not a list")
        except ValueError as exc:
            failure = str(exc)
        if failure is None and not doubts:
            report["skipped"]["no_doubts"] += 1
            continue
        report["selected"] += 1
        if dry_run:
            continue
        if failure is None:
            client = recognition._ImportedPage(adapter, model, payload)
            blocks, failure = _recognize(project_id, page_id, client, cancel, opts)
            if failure is None:
                try:
                    readings = {
                        IMPORT_FIRST: blocks,
                        IMPORT_SECOND: _second_reading(blocks, doubts, structure_guard, model),
                    }
                except (ValueError, KeyError, TypeError) as exc:
                    failure = str(exc) or type(exc).__name__
        if failure is None:
            if run_id is None or in_run >= MAX_PAGES:
                run_id, in_run = runstore.create_finished(project_id, models, owner_id=owner_id, at=_now()), 0
                report["runs"].append(run_id)
            runstore.add_finished_outputs(run_id, page_id, readings, at=_now())
            in_run += 1
            report["imported"] += 1
            report["doubts"] += len(doubts)
        else:
            report["failed"] += 1
        if progress is not None:
            progress(page_id, failure)
    return report


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

