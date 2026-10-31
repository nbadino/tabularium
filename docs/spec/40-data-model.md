# 6. Data model (SQLite)

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

- `projects(id, name, root_dir, owner_id, created_at, settings_json)` — settings keys actually
  written by the code: `labels` (custom classes + colors + prompts), `conventions` (transcription
  rules), `table_hint`, `known_table_headers`, `study_protocol` (+ `study_protocol_history`).
  Model choice, image caps and the split ratio are **not** stored here: they belong to the active
  compute profile / model settings, and the split ratio is a request parameter (see §7).
  `owner_id` = who created the project (see permissions below).
- `pages(id, project_id, rel_path, abs_path, width, height, issue_date, issue_no, page_no,
  page_type, status, meta_json)`.
- `blocks(id, page_id, label, bbox_px_json [x1,y1,x2,y2 pixels], content, order_idx, kind
  ['text','table','formula','picture','custom'], prefill_source, confirmed, updated_at)`.
- `tables(id, block_id, grid_json)` — `grid_json`: `{rows: n, cols: m, cells:[{r,c,rowspan,colspan,
  text, source, verified}], vlines:[...], hlines:[...]}` (physical grid; spans are the merges;
  `source` = who wrote the cell text, `verified` = human confirmation — only cells with text).
- `page_work` (migration 19): per-page annotator, reviewer, state
  (`annotation`/`pending`/`changes_requested`/`approved`) and optimistic version.
- Coordinates **stored in source pixels**; normalization to 0–1000 **only at export** (§7).
- **Auth**:
  - `users(id, username UNIQUE, password_hash, email, role ['admin','editor','viewer'], active,
    created_at, last_login_at, must_change_password, invite_token_hash, invite_expires_at)`;
  - `sessions(id, user_id, token_hash, expires_at)` — random 32-byte token, stored **only as a
    hash**; cookie `tab_session` (HttpOnly, SameSite=Strict);
  - `project_members(project_id, user_id, role ['editor','viewer'])` — shared access;
    `projects.owner_id` is the other side (only the owner deletes);
  - instance settings (`instance_name`, `allow_registration`, `default_new_user_role`) are a JSON
    value in `meta` (`services/settings.py`).
- Other tables: `compute_profiles` (active model/provider, v10+), `recognition_runs` /
  `recognition_run_items`, `jobs`, `page_reviews`, `custom_models`, `secrets` (vault),
  `audit_events`, `rate_limits`, `meta`.
- **Migrations**: up to **baseline 6** they are additive by column presence
  (`db.py::_ensure_columns`); from then on every schema change is a **versioned, ordered**
  migration in `db.py::_MIGRATIONS` (version → DDL), applied once to databases that are behind,
  with the version recorded in `meta` **in the same transaction as its DDL**
  (`db.py::_run_migration`; a foreign-key PRAGMA is applied outside it) and a visible startup
  error if the chain is incomplete. Restoring a backup migrates it at once.
  Current `SCHEMA_VERSION`: **20**. Upgrade tests in `tests/test_db_migrations.py`. The historical
  test suite runs with `TABULARIUM_AUTH=off`; `test_auth.py` turns it `on` with a fixture.

---
