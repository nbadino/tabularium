# 6. Data model (SQLite)

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

- `projects(id, name, root_dir, owner_id, created_at, settings_json)` — settings keys actually
  written by the code: `labels` (custom classes + colors + prompts), `conventions` (transcription
  rules), `table_hint`, `table_guard` (bool; absent = the domain profile's default),
  `known_table_headers`, `study_protocol` (+ `study_protocol_history`).
  Model choice, image caps and the split ratio are **not** stored here: they belong to the active
  compute profile / model settings, and the split ratio is a request parameter (see §7).
  `owner_id` = who created the project (see permissions below).
- `pages(id, project_id, rel_path, abs_path, width, height, issue_date, issue_no, page_no,
  page_type, status, meta_json, content_sha256, file_size, consensus_cleared_through)`.
  `content_sha256`/`file_size` (v27)
  describe the source file (the whole PDF for PDF pages): an upload, zip import or scan skips a
  file whose content is already an available page of the project and names it in the report's
  `already_present` (`services/content_identity.py`). Pages older than v27 are hashed by a
  background backfill at start-up (`TABULARIUM_CONTENT_HASH_BACKFILL=0` turns it off); until
  then a same-name, same-size page is hashed on the spot. Uploads keep their own file name as
  `rel_path`; only a second file of the same name in one request gets the `000000_` counter.
  `consensus_cleared_through` (v28, additive, backfilled from `recognition_runs` with
  `mode='replace_all'`): highest consensus `run_id` superseded by a full-page overwrite so Studio and
  the Table Editor ignore older comparisons while keeping explicit `?run_id=` history intact.
- `blocks(id, page_id, label, bbox_px_json [x1,y1,x2,y2 pixels], content, order_idx, kind
  ['text','table','formula','picture','custom'], prefill_source, confirmed, updated_at)`.
- `tables(id, block_id, grid_json)` — `grid_json`: `{rows: n, cols: m, cells:[{r,c,rowspan,colspan,
  text, source, verified}], vlines:[...], hlines:[...]}` (physical grid; spans are the merges;
  `source` = who wrote the cell text, `verified` = human confirmation — only cells with text;
  `structure_repairs` = what the structure guard changed in the model's draft, kept on save,
  see `20-requirements.md` "table structure guard").
- `collections(id, project_id, name, created_at, updated_at)` (migration 21): named groups of
  pages inside a project, name unique per project (case-insensitive). `pages.collection_id`
  (nullable, `ON DELETE SET NULL` as a safety net) puts a page in at most one collection;
  `NULL` is the declared state "no collection" (`none` in the API). Deleting a collection goes
  through the API, which requires `pages=unassign|delete` (see `docs/COLLECTIONS.md`).
  `collections.cover_page_id` (migration 22, nullable, `ON DELETE SET NULL`) is the page shown
  for the collection; it must belong to the collection and is cleared when that page moves out.
- `page_work` (migration 19): per-page annotator, reviewer, state
  (`annotation`/`pending`/`changes_requested`/`approved`) and optimistic version. An `approved`
  page goes back to `changes_requested` (and `pages.status='review'`) through a review that
  requests changes, an edit after approval, or `POST /api/pages/{id}/work/reopen` (reviewer or
  project manager, note required; logged as `page.reopened` and as a failed `page_reviews` row).
- Coordinates **stored in source pixels**; normalization to 0–1000 **only at export** (§7).
- **Auth**:
  - `users(id, username UNIQUE, password_hash, email, role ['admin','editor','viewer'], active,
    created_at, last_login_at, must_change_password, invite_token_hash, invite_expires_at,
    email_confirmed)` — `email_confirmed` is 1 when an administrator or the SSO provider wrote the
    email, 0 when the owner typed it; SSO links a sign-in only to a confirmed email;
  - `sessions(id, user_id, token_hash, expires_at)` — random 32-byte token, stored **only as a
    hash**; cookie `tab_session` (HttpOnly, SameSite=Strict);
  - `project_members(project_id, user_id, role ['editor','viewer'])` — shared access;
    `projects.owner_id` is the other side (only the owner deletes);
  - instance settings (`instance_name`, `allow_registration`, `default_new_user_role`) are a JSON
    value in `meta` (`services/settings.py`).
- Other tables: `compute_profiles` (active model/provider, v10+; inactive rows are the saved
  models of a consensus run; `key_required` (v26) marks those that reach a paid API; a recognition run can
  read with one too: its `profile_id` is in the job's `command_json`, `adapters/runs.py::run_profile_id`,
  no migration; such a run never stops the active model and does not wait for its runs),
  `recognition_runs` / `recognition_run_items`, `consensus_runs` / `consensus_outputs` (v24: each model's blocks for a
  page, never written to `blocks`/`tables`), `consensus_decisions` (v25: what people settled in a
  comparison, shared by the project: run, page, area `cluster_id` or table `block_id`, `kind`
  ∈ resolved/reopened/use_model/removed/confirm_mine/group_confirm/add_table/adopt_page/revert/comment,
  `model_key`, `before_json`/`after_json`, `comment`, `user_id`, `created_at`; `about_id` is the
  decision a revert or a comment is about, `reverted_by` the revert that took it back; owned by
  `adapters/consensus.py`, rules in `services/consensus_decisions.py`), `user_model_keys` (v26: each
  user's own API key for a saved model, `(user_id, profile_id)` → vault Fernet `ciphertext`, cascades on
  either side; owned by `adapters/model_keys.py`, rules in `services/model_keys.py`: a run on a saved
  model uses the key of the user who starts or retries it, never another user's nor the machine's
  OpenCode key, and no route returns a key. Migration 26 moved each saved model's
  `vault:consensus_profile_*` key, still encrypted, to the admin who saved it (audit trail, else the
  first active admin); with no user at all it stays on the profile, read only in local mode), `jobs`, `page_reviews`, `custom_models`, `secrets` (vault),
  `audit_events`, `rate_limits`, `meta`.
- **Migrations**: up to **baseline 6** they are additive by column presence
  (`db.py::_ensure_columns`); from then on every schema change is a **versioned, ordered**
  migration in `db.py::_MIGRATIONS` (version → DDL), applied once to databases that are behind,
  with the version recorded in `meta` **in the same transaction as its DDL**
  (`db.py::_run_migration`; a foreign-key PRAGMA is applied outside it) and a visible startup
  error if the chain is incomplete. Restoring a backup migrates it at once.
- **Upgrade guard** (`db.py::_guard_upgrade`, before `init_db` writes anything): a database whose
  `schema_version` is newer than `SCHEMA_VERSION` is refused with an error; one that is behind (or
  has no version) is first copied to `backups/tabularium-<time>-pre-migration-v<version>.db`.
  `tabularium restore-db` puts a backup back offline without migrating it (the rollback path);
  `tabularium backup` archives the data root plus `config.ENV_FILE` (`--runs` drops the
  `checkpoints/` of the other runs; paths outside the root are listed, not archived);
  `tabularium restore --replace` moves the root aside to `<root>.pre-restore-<time>`, extracts
  with tarfile's `data` filter, carries `*-runtime/`, `vendor/`, `cache/` over, swaps only the
  `TABULARIUM_VAULT_KEY` line of `config.ENV_FILE`, and runs `init_db`.
- **Data root relocation** (`db.py::_follow_data_root`): paths are stored absolute
  (`projects.root_dir`, `projects.archive_dir`, `pages.abs_path`, `pages.thumb_path`,
  `jobs.log_path`, `custom_models.source_path`, `pages.meta_json.extracted_path`). `meta.data_root` records the root the
  database last ran from; when it differs from `config.ROOT_DIR`, paths under the old root are
  rewritten after a `pre-relocation` backup. Paths outside the root (server folders) are left
  alone, and so are paths inside `jobs.command_json`.
  Current `SCHEMA_VERSION`: **27**. Upgrade tests in `tests/test_db_migrations.py`. The historical
  test suite runs with `TABULARIUM_AUTH=off`; `test_auth.py` turns it `on` with a fixture.

---
