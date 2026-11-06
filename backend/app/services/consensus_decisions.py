"""Consensus decisions: what people settled in a model comparison, shared and revertible.

Every choice made while working through a comparison is a row of `consensus_decisions`
(owned by `adapters/consensus.py`): an area settled or reopened, a model's reading used
for an area or a cell, the user's own reading confirmed (one cell or a whole group), a
model's table added, a model's reading adopted for the page. Each row keeps who, when,
the value before and after, and an optional comment. The settled state of an area is
derived from the rows, so every browser shows the same one.

A revert puts the value before back, but only where the value after is still there:
if someone changed the cell or the block since, the revert is refused (409) rather
than overwriting their work (R9). The revert is a decision itself, so it can be
reverted in turn. Decisions that replaced blocks wholesale (a model's reading adopted
for the page, the user's blocks removed or swapped for the model's) are recorded and
commentable but not revertible from the history: the before value is not a value the
server can put back.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from ..adapters import annotations as annotationstore
from ..adapters import consensus as runstore
from ..adapters import pages as pagestore
from .i18n import msg

# Kinds a client records; `add_table` and `adopt_page` are written by the adopt
# endpoints, `revert` by the revert endpoint.
CLIENT_KINDS = ("resolved", "reopened", "use_model", "removed", "confirm_mine", "group_confirm", "comment")
_CELL_KEYS = ("text", "verified", "source")


def _now_iso(value: str | None) -> str | None:
    # SQLite's datetime('now') is UTC without a zone: say so to the browser.
    if not value or "T" in value:
        return value
    return value.replace(" ", "T") + "Z"


def _publish(page_id: int, message: dict[str, Any]) -> None:
    # A lost live event must not fail the write that caused it.
    try:
        from . import live  # noqa: PLC0415

        live.hub.publish(page_id, message)
    except Exception:  # noqa: BLE001
        pass


def _author(actor: dict | None) -> dict[str, Any]:
    return {"id": (actor or {}).get("id"), "username": (actor or {}).get("username")}


def _announce(page_id: int, run_id: int, decision: dict[str, Any], actor: dict | None) -> None:
    _publish(page_id, {
        "type": "consensus_decision", "page_id": page_id, "run_id": run_id,
        "decision_id": decision["id"], "kind": decision["kind"], "author": _author(actor),
    })


# --- derived state -------------------------------------------------------------


def target_kind(decision: dict[str, Any], by_id: dict[int, dict[str, Any]]) -> str:
    """The kind a revert acts on (a revert of a revert of `resolved` acts on `resolved`)."""
    seen: set[int] = set()
    current = decision
    while current["kind"] == "revert" and current.get("about_id") in by_id and current["id"] not in seen:
        seen.add(current["id"])
        current = by_id[current["about_id"]]
    return current["kind"]


def resolved_clusters(decisions: list[dict[str, Any]]) -> list[int]:
    """The areas settled now: the last standing decision on each area says so."""
    state: dict[int, bool] = {}
    for decision in decisions:
        after = decision.get("after")
        if decision.get("cluster_id") is None or decision.get("reverted_by") is not None:
            continue
        if isinstance(after, dict) and "resolved" in after:
            state[int(decision["cluster_id"])] = bool(after["resolved"])
    return sorted(cluster for cluster, on in state.items() if on)


def _cells(state: Any) -> list[dict[str, Any]] | None:
    cells = state.get("cells") if isinstance(state, dict) else None
    return cells if isinstance(cells, list) and cells else None


def _block_patch(decision: dict[str, Any]) -> tuple[int, dict, dict] | None:
    """A model's reading put into one block in place: (block id, before, after)."""
    before, after = decision.get("before"), decision.get("after")
    blocks_before = before.get("blocks") if isinstance(before, dict) else None
    blocks_after = after.get("blocks") if isinstance(after, dict) else None
    if not (isinstance(blocks_before, list) and isinstance(blocks_after, list)):
        return None
    if len(blocks_before) != 1 or len(blocks_after) != 1:
        return None
    if str(blocks_before[0].get("id")) != str(blocks_after[0].get("id")):
        return None
    try:
        return int(str(blocks_before[0].get("id"))), blocks_before[0], blocks_after[0]
    except ValueError:
        return None  # a block never saved: no row to put the value back in


def revertible(decision: dict[str, Any], kind: str) -> bool:
    if decision["kind"] == "comment" or decision.get("reverted_by") is not None:
        return False
    if kind in ("resolved", "reopened"):
        return True
    if kind in ("confirm_mine", "group_confirm"):
        return _cells(decision.get("after")) is not None
    if kind == "use_model":
        return _cells(decision.get("after")) is not None or _block_patch(decision) is not None
    if kind == "add_table":
        # Deleting the table again; putting a deleted table back is not offered.
        return decision["kind"] == "add_table"
    return False


def _describe(decision: dict[str, Any], by_id: dict[int, dict], models: dict[str, str]) -> dict[str, Any]:
    kind = target_kind(decision, by_id)
    out = {key: value for key, value in decision.items() if key not in {"user_id", "username"}}
    out["created_at"] = _now_iso(decision.get("created_at"))
    out["target_kind"] = kind
    out["model_name"] = models.get(decision.get("model_key") or "")
    out["user"] = {"id": decision.get("user_id"), "username": decision.get("username")}
    out["revertible"] = revertible(decision, kind)
    return out


def _models(run: dict[str, Any]) -> dict[str, str]:
    return {str(model["key"]): str(model["name"]) for model in run.get("models") or []}


def page_state(run_id: int | None, page_id: int) -> dict[str, Any]:
    """The history of a run on a page (newest first) and the areas settled now."""
    if run_id is None:
        return {"run_id": None, "items": [], "resolved": []}
    run = runstore.get(run_id)
    decisions = runstore.page_decisions(run_id, page_id)
    by_id = {decision["id"]: decision for decision in decisions}
    models = _models(run or {})
    return {
        "run_id": run_id,
        "items": [_describe(decision, by_id, models) for decision in reversed(decisions)],
        "resolved": resolved_clusters(decisions),
    }


def resolved_for(run_id: int | None, page_id: int) -> list[int]:
    if run_id is None:
        return []
    return resolved_clusters(runstore.page_decisions(run_id, page_id))


# --- recording ------------------------------------------------------------------


def _run_of_page(run_id: int, page_id: int, lang: str | None) -> dict[str, Any]:
    run = runstore.get(run_id)
    if run is None or int(run["project_id"]) != pagestore.project_of_page(page_id):
        raise HTTPException(status_code=404, detail=msg("consensus_run_not_found", lang))
    return run


def _invalid(lang: str | None) -> HTTPException:
    return HTTPException(status_code=422, detail=msg("consensus_decision_invalid", lang))


def _single(decision: dict[str, Any], run: dict[str, Any], page_id: int) -> dict[str, Any]:
    decisions = runstore.page_decisions(int(run["id"]), page_id)
    by_id = {item["id"]: item for item in decisions}
    return _describe(by_id[decision["id"]], by_id, _models(run))


def record(page_id: int, payload: dict[str, Any], actor: dict | None, lang: str | None = None) -> dict[str, Any]:
    """Record one decision taken in the Studio. Returns the decision (None when it
    would change nothing, such as settling an area already settled) and the areas
    settled now."""
    kind = payload["kind"]
    if kind not in CLIENT_KINDS:
        raise _invalid(lang)
    run = _run_of_page(int(payload["run_id"]), page_id, lang)
    run_id = int(run["id"])
    cluster_id, block_id = payload.get("cluster_id"), payload.get("block_id")
    model_key = payload.get("model_key")
    comment = (payload.get("comment") or "").strip() or None
    before, after = payload.get("before"), payload.get("after")

    if block_id is not None:
        with annotationstore.writing() as writer:
            block = writer.block(int(block_id))
        if block is None or int(block["page_id"]) != page_id:
            raise HTTPException(status_code=404, detail=msg("consensus_block_not_found", lang))
    if model_key is not None and model_key not in _models(run):
        raise HTTPException(status_code=404, detail=msg("consensus_model_not_found", lang))

    decisions = runstore.page_decisions(run_id, page_id)
    settled = set(resolved_clusters(decisions))
    about_id = None
    if kind in ("resolved", "reopened"):
        if cluster_id is None:
            raise _invalid(lang)
        on = kind == "resolved"
        if (int(cluster_id) in settled) == on:
            return {"decision": None, "resolved": sorted(settled)}
        before, after = {"resolved": not on}, {"resolved": on}
        block_id = model_key = None
    elif kind == "use_model":
        if model_key is None or (cluster_id is None) == (block_id is None):
            raise _invalid(lang)
        if cluster_id is not None:
            blocks_before = (before or {}).get("blocks")
            blocks_after = (after or {}).get("blocks")
            if not isinstance(blocks_before, list) or not isinstance(blocks_after, list):
                raise _invalid(lang)
            before = {"resolved": int(cluster_id) in settled, "blocks": blocks_before}
            after = {"resolved": True, "blocks": blocks_after}
        elif _cells(before) is None or _cells(after) is None:
            raise _invalid(lang)
        else:
            before, after = {"cells": _cells(before)}, {"cells": _cells(after)}
    elif kind == "removed":
        if cluster_id is None:
            raise _invalid(lang)
        before = {"resolved": int(cluster_id) in settled, "blocks": (before or {}).get("blocks") or []}
        after = {"resolved": True, "blocks": []}
        block_id = None
    elif kind in ("confirm_mine", "group_confirm"):
        if block_id is None or _cells(before) is None or _cells(after) is None:
            raise _invalid(lang)
        if len(_cells(before) or []) != len(_cells(after) or []):
            raise _invalid(lang)
        before, after = {"cells": _cells(before)}, {"cells": _cells(after)}
        cluster_id = None
    else:  # comment
        if comment is None:
            raise _invalid(lang)
        about_id = payload.get("about_id")
        if about_id is not None and not any(item["id"] == about_id for item in decisions):
            raise HTTPException(status_code=404, detail=msg("consensus_decision_not_found", lang))
        before = after = None
        model_key = None

    with annotationstore.writing() as writer:
        decision_id = runstore.insert_decision(writer.conn, {
            "run_id": run_id, "page_id": page_id, "cluster_id": cluster_id, "block_id": block_id,
            "kind": kind, "model_key": model_key, "before": before, "after": after,
            "comment": comment, "user_id": (actor or {}).get("id"), "about_id": about_id,
        })
        writer.commit()
    decision = _single({"id": decision_id}, run, page_id)
    _announce(page_id, run_id, decision, actor)
    return {"decision": decision, "resolved": resolved_for(run_id, page_id)}


def record_adoption(
    run: dict[str, Any], page_id: int, kind: str, model_key: str, *,
    block_id: int | None = None, after: dict[str, Any] | None = None, actor: dict | None = None,
) -> None:
    """The adopt endpoints' own decisions: a model's table added, a model's reading adopted."""
    with annotationstore.writing() as writer:
        decision_id = runstore.insert_decision(writer.conn, {
            "run_id": int(run["id"]), "page_id": page_id, "block_id": block_id, "kind": kind,
            "model_key": model_key, "before": None, "after": after,
            "user_id": (actor or {}).get("id"),
        })
        writer.commit()
    _announce(page_id, int(run["id"]), {"id": decision_id, "kind": kind}, actor)


# --- revert ---------------------------------------------------------------------


def _changed(lang: str | None) -> HTTPException:
    return HTTPException(status_code=409, detail=msg("consensus_decision_changed", lang))


def _same_cell(current: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if current is None:
        return False
    if "text" in expected and (current.get("text") or "") != (expected.get("text") or ""):
        return False
    return not ("verified" in expected and bool(current.get("verified")) != bool(expected.get("verified")))


def _revert_cells(writer, block_id: int | None, before: list[dict], after: list[dict], lang: str | None) -> None:
    row = writer.table(block_id) if block_id is not None else None
    grid = json.loads(row["grid_json"] or "{}") if row is not None else None
    if not grid:
        raise _changed(lang)
    at = {(int(cell["r"]), int(cell["c"])): cell for cell in grid.get("cells") or []}
    for expected in after:
        if not _same_cell(at.get((int(expected["r"]), int(expected["c"]))), expected):
            raise _changed(lang)
    for previous in before:
        cell = at.get((int(previous["r"]), int(previous["c"])))
        if cell is None:
            raise _changed(lang)
        for key in _CELL_KEYS:
            if key in previous:
                cell[key] = previous[key]
    writer.upsert_table(block_id, grid)


def _revert_add_table(writer, after: Any, lang: str | None) -> None:
    block_id = after.get("block_id") if isinstance(after, dict) else None
    block = writer.block(int(block_id)) if block_id is not None else None
    if block is None:
        return  # already deleted by hand: nothing left to take back
    row = writer.table(int(block_id))
    grid = json.loads(row["grid_json"] or "{}") if row is not None else {}
    touched = any(cell.get("verified") or cell.get("source") == "manual" for cell in grid.get("cells") or [])
    # Someone worked on the table since it was added: never delete that (R9).
    if block.get("confirmed") or touched:
        raise _changed(lang)
    writer.delete_block(int(block_id))


def revert(
    page_id: int, decision_id: int, comment: str | None, actor: dict | None, lang: str | None = None,
) -> dict[str, Any]:
    """Put back the value a decision replaced, and record that as a decision."""
    comment = (comment or "").strip() or None
    revision = None
    with annotationstore.writing() as writer:
        conn = writer.conn
        target = runstore.get_decision(decision_id, conn)
        if target is None or int(target["page_id"]) != page_id:
            raise HTTPException(status_code=404, detail=msg("consensus_decision_not_found", lang))
        decisions = runstore.page_decisions(int(target["run_id"]), page_id, conn)
        by_id = {item["id"]: item for item in decisions}
        kind = target_kind(target, by_id)
        if target.get("reverted_by") is not None:
            raise HTTPException(status_code=409, detail=msg("consensus_decision_reverted", lang))
        if not revertible(target, kind):
            raise HTTPException(status_code=409, detail=msg("consensus_decision_not_revertible", lang))
        before, after = target.get("before"), target.get("after")
        touched = False
        if target.get("cluster_id") is not None and isinstance(after, dict) and "resolved" in after:
            settled = int(target["cluster_id"]) in resolved_clusters(decisions)
            if settled != bool(after["resolved"]):
                raise _changed(lang)
        if kind == "add_table":
            _revert_add_table(writer, after, lang)
            touched = True
        elif _cells(after) is not None:
            _revert_cells(writer, target.get("block_id"), _cells(before) or [], _cells(after) or [], lang)
            touched = True
        elif kind == "use_model":
            patch = _block_patch(target)
            assert patch is not None  # revertible() said so
            block_id, previous, current = patch
            block = writer.block(block_id)
            if block is None or int(block["page_id"]) != page_id:
                raise _changed(lang)
            if (block.get("content") or "") != (current.get("content") or "") or (
                "label" in current and block.get("label") != current.get("label")
            ):
                raise _changed(lang)
            updates = {"content": previous.get("content") or ""}
            if previous.get("label"):
                updates["label"] = previous["label"]
            writer.update_fields(block_id, updates)
            touched = True
        new_id = runstore.insert_decision(conn, {
            "run_id": int(target["run_id"]), "page_id": page_id,
            "cluster_id": target.get("cluster_id"),
            "block_id": None if kind == "add_table" else target.get("block_id"),
            "kind": "revert", "model_key": target.get("model_key"),
            "before": after, "after": before, "comment": comment,
            "user_id": (actor or {}).get("id"), "about_id": target["id"],
        })
        if not runstore.mark_reverted(conn, target["id"], new_id):
            raise HTTPException(status_code=409, detail=msg("consensus_decision_reverted", lang))
        if touched:
            writer.bump_revision(page_id)
            revision = writer.revision(page_id)
        writer.commit()
    run = runstore.get(int(target["run_id"])) or {"id": target["run_id"], "models": []}
    decision = _single({"id": new_id}, run, page_id)
    if revision is not None:
        # The editors open on the page reload what changed under them.
        _publish(page_id, {
            "type": "annotations_changed", "page_id": page_id, "revision": revision,
            "action": "consensus_revert", "author": _author(actor),
        })
    _announce(page_id, int(target["run_id"]), decision, actor)
    return {
        "decision": decision,
        "resolved": resolved_for(int(target["run_id"]), page_id),
        "annotation_revision": revision,
    }
