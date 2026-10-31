# Changelog

Notable changes to Tabularium. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- The SPA fallback no longer serves files outside `frontend/dist` (encoded `..` read any file).
- Admins cannot reset their own password; OIDC links an existing account only when the
  provider asserts `email_verified`; a server `archive_dir` is admin-only.
- A warning is logged at startup when `TABULARIUM_AUTH=off`.
- SSH training targets starting with `-` are refused (they were read as ssh options).
- OIDC: the state is bound to the browser that started the flow (login CSRF), and two
  people signing in at once no longer cancel each other's state.
- Login rate limit keyed on client + username, with a per-client failure budget; a
  missing user costs the same PBKDF2 time as a wrong password. Register and invite
  limits are per client instead of global.
- Logout ends Bearer sessions too; the live channel refuses a password still to change;
  `/api/system/info` needs login and shows server paths to admins only; scan jobs are
  read only through their own project.

### Fixed
- Training start, first thumbnail and custom model deletion no longer fail with a
  NameError; pyflakes now guards the backend against undefined names.
- Restoring the oldest backup no longer prunes it first.
- `html_to_otsl` keeps rowspans in their column on short rows.
- Prefill defaults to `merge`; `replace`/`replace_all` require `confirm=true`.
- Prefill advances `annotation_revision`: an approved page loses its approval and a
  concurrent editor gets a conflict instead of overwriting the drafts.
- Prefill no longer auto-deskews a page that keeps blocks (only `replace_all` may).
- Each migration and its schema version commit together; restoring an older backup
  migrates it immediately; the transformed master no longer leaks a file handle.

### Changed
- CI and release workflows: least-privilege permissions, concurrency, release gated on
  tests and on non-empty CHANGELOG notes; training images build only from `main`;
  Dependabot; `CITATION.cff` follows `VERSION`.
- Supply chain: every GitHub Action pinned to a commit SHA, app image base images pinned
  by digest, and `backend/constraints.txt` (universal lock) installed by the image and CI.

## [0.4.0] - 2026-10-27

### Fixed
- Training monitoring names the current and next stage, separates phase/rental time, reports
  measured transfer counts and real ms-swift optimizer progress, and exposes the comparison
  verdict in the primary summary. Training/validation loss are separate series; token accuracy
  stays in diagnostics. The logs button follows the current phase, and monitor navigation
  survives refresh. Remote exit/manifest parsing tolerates provider banners, retrieves saved
  checkpoints even after command failure, and rejects empty artifact manifests.
- Page approval now explicitly verifies table-cell content as well as blocks, synchronizes
  annotation revisions and refreshes table editors. Readiness and approved-only export use the
  same cell verification rule; assigned-page review accepts cells on explicit reviewer approval.
- Dataset reports expose excluded tables, and automatic training checks coverage before renting
  a GPU. Full-table export no longer emits one warning per table for unused optional bands.
- Table evaluation now computes actual tree-edit TEDS with APTED; the previous row-aligned
  approximation has a separate name. RapidFuzz accelerates exact CER/WER on full tables.
- Training progress keeps polling through automatic preparation, exposes the run during dataset sync, shows model download file counts, and reports verified GPU release. Resume excludes stale merged exports and rsync temporary files; registration uses the validated merged path.
- Vast.ai automatic training retries definitive unavailable offers, avoids recently failed preparation offers, requires CUDA 12.8 for Monkey v4, and reconciles interrupted jobs on startup. Empty metric files replay the training log; Monkey legacy vLLM version detection accepts 0.11.0. Unknown recovery billing is not displayed as a fabricated total.
- Automatic training monitoring isolates provisioning from older run metrics, restores the active monitor after reload, hides empty performance sections, and keeps localized phase messages with diagnostic logs on demand.

### Added
- **P7 is closed, and the four rules are now invariants.** The API layer holds 6 SQL statements
  (all in `auth.py`), the service layer 12 in three declared exceptions (`auth`, `vault`, `audit`),
  prefill and the MLX serving dispatch name no model, and the reference model lives once as
  `DEFAULT_MODEL_ID`. Each rule is a test that fails on the pattern; the two pending lists are
  self-expiring and empty. `scripts/check.sh` green: 698 backend tests, 258 frontend tests, the
  production build.
- **The service layer is SQL-free too (P7.1 complete).** `adapters/users.py` now owns the account
  rows — creation, the partial update, the invite token, activation, the two counts — and
  `services/users.py` keeps the validation and the guards (the last admin, your own account, the
  projects of a user who must transfer them first). `app/services/` went from 145 statements in 21
  modules to 12 in 3, all three declared exceptions; `SERVICES_SQL_PENDING` is empty.
- **Readiness and approval stop writing their own SQL (P7.1).** `adapters/pages.py` gained the
  confirm-blocks step, the table grid read/write with the revision bump, the passing-review check and
  the status change; `api` and `pages` now ask the same owner for the same rows. 145 → 31 statements in
  4 modules.
- **The annotation→review handoff moves to `adapters/assignment.py` (P7.1).** The `page_work` rows
  and the page-status transition that accompanies each step (`annotated` on reassignment, `qa` on
  submission, `approved`/`review` on the verdict) are one unit; the service keeps when each is
  allowed — version match, role, readiness — and its `BEGIN IMMEDIATE`. 145 → 46 statements in 5
  modules.
- **The SSH tunnel and cloud-resource rows leave the cloud manager (P7.1).** `jobs.py` gained
  `stop_running`, `start_tunnel_job`, `touch`, `latest_running`, `latest_of_kind`, `cloud_upsert` and
  `cloud_usage` — the last one returns hours *and* rate together, because a cost without its rate is
  a number with no meaning. The manager keeps the tunnel liveness check and the rule that a failed
  start is not an explicit stop. 145 → 61 statements in 6 modules.
- **The training runs use the jobs adapter (P7.1).** `start_training_job`, `of_project`, `latest`,
  `of_kind_state`, `settle` and `mark_state` cover the twelve statements the trainer had for its
  runs. What stayed in the service is the judgement: a run is interrupted when its PID is gone, a
  remote job is only unreachable after a restart, a stopped run writes its own manifest. 145 → 71
  statements in 7 modules.
- **The custom model registry moves to an adapter (P7.1).** `adapters/custom_models.py` owns the two
  insert shapes (a registered HF model and a fine-tuned checkpoint, which carries the wider column
  set), the lookup by source run that makes a second import idempotent, and the listing. The service
  keeps the naming rules and the checkpoint merge. 145 → 84 statements in 8 modules.
- **Page registration leaves the scan service (P7.1).** `adapters/pages.py` gained the insert of an
  image page and of a PDF page (returning `None` for a duplicate, which is also where a page marked
  *missing* is reactivated — a restored file gets its annotations back), plus `set_thumb`, `set_meta`
  and the missing-path list. `scan` keeps the archive walk, the report and the PDF rendering. 145 → 92
  statements in 9 modules.
- **The serve path stops naming models (P7.4), and its job rows move out (P7.1).**
  `ServeRecipe.local_serve` declares which local runtime serves a model (`""`, `mlx-mineru`,
  `mlx-unlimited`); `serve_manager._local_runner` maps the declaration to the module for the log tail,
  the readiness step and the argv, so the three `adapter_id` branches are gone and a new model with its
  own MLX server is a declaration plus one table entry. The `jobs` rows of the serve path use
  `adapters/jobs.py`. 145 → 99 statements in 10 modules.
- **The compute profiles table has one owner (P7.1).** `adapters/compute_profiles.py` holds the
  insert, the activation switch and the health stamps; the service keeps validity, the endpoint check
  and the decision to activate. Two lessons were paid for in this move: `store.put` now takes the
  caller's connection (a second writer inside a transaction is a deadlock, not a slowdown) and
  `store.put_raw` exists because the legacy `inference_*` keys are read as raw strings — JSON-encoding
  them turned `1` into `"1"`. 145 → 103 statements in 11 modules.
- **The inference configuration has one owner (P7.1).** `adapters/inference_config.py` reads the
  legacy `inference_*` keys and the active compute profile, and writes both in one transaction —
  the half-written pair (one provider's URL with another's adapter) is the bug this area already
  paid for. The service keeps the meaning: which provider, which adapter, whether the identity
  changed. 145 → 116 statements in 12 modules.
- **The rate limiter and Modal’s job rows move out, and the limiter learns who owns its
  transaction (P7.1).** `adapters/rate_limits.py` owns the window row and `adapters/jobs.py` the
  `jobs` table. The limiter now owns its connection: a rejected login must not clear the evidence of
  the attempts that made it suspicious, so the counter cannot live inside the caller's rollback —
  and for the same reason the four call sites check the limit *before* opening their write
  transaction, or two writers deadlock on SQLite. 145 → 123 statements in 13 modules.
- **Six services lose their SQL (P7.1, second half).** `corpus`, `pilot`, `settings`, `doctor` and
  `model_registry` read through `adapters/` (`pages.all_for_project`, `store`, `users.count_*`,
  `models.clear_source_path`), and `page_meta` was only a docstring: the counter now excludes prose,
  so a module cannot stay on the pending list for saying "UPDATE" in a sentence. 145 → 134
  statements in 15 modules.
- **`app/api/` no longer speaks SQL, and the service layer is now measured (P7.1).** The two scan
  paths moved into `services/scan.py::run_scan` / `register_uploads`, which own the transaction; the
  API went 105 → 6 statements, all in `auth.py` (the declared exception for the session boundary).
  A second pair of tests does for `app/services/` what the first does for the API: 145 statements in
  21 modules, three motivated exceptions (`vault`, `auth`, `audit`) and eighteen pending entries with
  a note each — plus the self-expiring check that forces the list to shrink.
- **The admin surfaces leave the router too (P7.1).** `adapters/users.py` owns the three counts
  (owned projects, admins, session purge) — connection-aware, because a second writer inside a
  transaction deadlocks — and `adapters/system.py` the jobs list and the schema version. The API
  pending list is down to the two scan statements in `projects.py`.
- **Workflow, activity, queue, pilot sample and project deletion leave the router (P7.1).**
  `api/projects.py` is at 2 statements (from 40); the two left are the scan paths, which hand a
  connection to `services/scan.py` and therefore wait for the services pass. The deletion keeps its
  rule where it belongs: only the owner may delete, and the page thumbnails go before the rows.
- **The project list stops being an N+1 (P7.1).** `projectstore.summaries` computes page counts,
  owner name, active members and the caller's role in one statement for the whole list; before, each
  row ran four queries. Creation and listing go through the adapter too. `api/projects.py`:
  21 → 12 statements.
- **Membership and ownership are a service, not three router functions (P7.1).** The rules —
  the owner cannot also be a member, only an active account can be added, a transfer leaves the
  previous owner as an editor — live in `services/project_members.py`; the statements in
  `adapters/projects.py::MemberWriter`. `api/projects.py`: 34 → 21 statements.
- **The project settings endpoints read and write through the adapter (P7.1).** Study protocol
  (with the versioned history), gold set (which also removes the page from the pilot sample),
  conventions, table hint, known headers and the QA summary now go through
  `adapters/projects.py`; the router keeps the rules and the response shapes. `api/projects.py`:
  40 → 34 statements.
- **`api/pages.py` is SQL-free, and approvals own their transaction (P7.1).** The page reads,
  reviews, deskew and transform endpoints go through `adapters/pages.py` and
  `adapters/annotations.py`; readiness and draft confirmation now take an optional connection, and the
  approvals moved into `pagesvc.approve_page` / `approve_pages`, so the router can no longer write
  “approved” without the content it claims. `app/api/pages.py`: 22 statements → 0.
- **The page reads leave the API layer, and the real obstacle appears (P7.1).** `list_pages`,
  `sum_failures`, the page update and the lookups go through `adapters/pages.py`. The remaining
  endpoints of `api/pages.py` and `api/projects.py` hand a live connection to services
  (`compute_readiness(conn, …)`, `worksvc.configured(conn, …)`): moving the SQL alone would leave
  the router opening the connection, so the plan now records the unit-of-work interface those
  services need instead of another mechanical move.
- **Authorization and the small reads leave the API layer (P7.1).** `api/deps.py` resolves the
  project of a page or a block through `adapters/pages.py`; `api/live.py` and `api/playground.py`
  do the same for the page and the project rows. The pending list is down to four modules, and the
  self-expiring test forced removing the three that are clean. The plan also records a correction:
  P7.4 is not about provider specs — providers are already separate modules — but about the
  per-model local serving runners declared on the model.
- **`api/blocks.py` no longer speaks SQL (P7.1).** Every annotation, label and table endpoint now
  goes through `adapters/annotations.py` and `adapters/projects.py`; the file's 22 statements are
  zero, and the label lookup no longer imports a private helper from another API module. The
  architecture test gained the rule the plan asked for — no SQL in `app/api/` — with `auth.py` as the
  one recorded exception and a *self-expiring* pending list: finishing a module makes the test fail
  until its name is removed.
- **The autosave writes through an adapter (P7, thin-API item, first slice).**
  `adapters/annotations.py` owns the statements and the transaction of
  `services/blocks.py`; the service keeps the decisions — which prefill drafts survive an
  autosave, when a stale revision is a 409, that an explicit delete is the only way to remove
  a draft. `tests/test_architecture.py` now requires it to stay SQL-free, which is what the rule
  for `app/api/` will also look like once its three modules are migrated.
- **Prefill dispatches on a declared runner; one default model id (P7, items 2–3).** The adapter now
  declares `native_runner` next to `native_prefill_mode`, and `prefill.native_page_items` calls
  `VllmClient.<runner>_native_page`: the eleven `adapter_id` branches are gone, PaddleOCR-VL's
  pipeline is a client method like the others, and the matrix gained a `prefill` column
  (`official (mineru)`). Guards: the runner vocabulary, *official ⇒ runner*, the method must exist,
  and no identity comparison in `prefill.py`. The frontend’s reference model lives once, in
  `lib/constants.ts` as `DEFAULT_MODEL_ID` (twelve literals removed).
- **The remote training launch is testable, and the suite is finally all green.** `SshExecutor.launch`
  streams rsync through `_stream(command, stream, on_line)` — the progress bar needs one file at a
  time, so `Popen` stays — while the test that recovers the remote PID replaces that seam instead of
  patching half of `subprocess`. A second test pins the seam: a `TABULARIUM_FILE:` line counts, a
  directory announcement does not.
- **The consolidation work is written down as an analysis.** `docs/architecture/analysis.md` records the
  starting measurement, the phase-by-phase changes, the nine defects found on the way (including eight
  method definitions shadowed by a scripted move), the before/after numbers and the work deliberately
  left undone — so the next agent can check claims instead of trusting memory. Nested
  `backend/AGENTS.md` and `frontend/AGENTS.md` state only the local rules, and
  `python scripts/surfaces.py --write` generates the model × surface matrix.
- **TeleOCR joins the shared native transport (phase P4 complete as far as it pays).** The vendor
  pipelines now share one header builder and one POST; TeleOCR contributes its layout-mode header
  instead of a third copy of the request. The HTTP transport and the SSE loop stay in the client on
  purpose — the tests patch `requests` there, and the socket code is cohesive — which is recorded in
  the program with the reasoning.
- **The OpenAI request body is data now (phase P4).** `openai_content` and `openai_request` build
  the chat completion payload: message order, system prompt, streaming usage, and the
  `no_repeat_ngram_size` knob that travels as `extra_args` for vLLM and as `vllm_xargs` for
  MinerU2.5. The sampling dict is copied, not mutated. The client keeps the socket, the retries and
  the SSE loop.
- **A shadowed duplicate in the inference client, found and removed.** A scripted extraction had
  cut a range at the wrong marker and left a dead copy of `_generate_content`, `_chat`,
  `qwen_native_page`, `glmocr_native_page`, `mineru_native_page`, `is_cloud` and `is_modal` in the
  class. Python kept the last definition, so every test passed while 490 lines of dead, misleading
  code stayed in the file. `tests/test_architecture.py` now fails on any name defined twice in the
  same scope, and the guide tells the next person to run it after a scripted move.
- **One transport for the vendor pipelines (phase P4).** GLM-OCR and MinerU were sending the same
  request twice, with the same five header lines; `_native_vendor_headers` and
  `_native_parse_page` now own it, so a third native model adds a call instead of a copy. Behaviour is
  unchanged — the vendor headers, the timeout, the JSON error messages and the payload key stay the
  same.
- **The LlamaCloud request mapping is data too (phase P4).** `llamaindex_configuration` turns a
  validated workflow into the vendor’s parse request; the client keeps the HTTP calls. Four tests pin
  the choices that matter: tables stay HTML (Markdown loses merged cells), the job timeout is derived
  from the settings, optional blocks are sent only when enabled, and `cost_optimizer` is refused
  outside the agentic tier.
- **LlamaCloud’s response shapes leave the client too (phase P4).** `llamaindex_base_url`,
  `llamaindex_pages` and `llamaindex_versions` moved next to the Google protocol in
  `domain/inference.py`, with the default host as a constant: three API shapes and one answer, tested
  without a socket.
- **Vendor protocols start leaving the inference client (phase P4, first slice).**
  `domain/inference.py` now owns the Google `generateContent` request and response (including the
  OpenAI→Google sampling names), the streaming JSON-array scanner and the parser that survives
  truncated or category-labelled output. The client keeps the transport; the shapes are testable
  without a socket.
- **Every service that owns a concept now reads through an adapter (phase P3 complete).**
  `evaluate` moved its two project lookups to `adapters/projects.py`, joining model settings, OIDC,
  prefill, recognition and the dataset builder. `tests/test_architecture.py` enforces the boundary
  for all six and the purity of `domain/`; the only structural work left is `inference.py` (P4).
- **The dataset builder reads through an adapter (phase P3b).** `adapters/exports.py` answers
  “what is annotated” — pages with blocks, their grids, the page count — while the builder keeps the
  families, the page split, the crops and the report. The rule that an unconfirmed draft arriving
  after a page was approved is *not* gold stayed in the query, with the comment that explains the
  cost of getting it wrong.
- **Recognition sessions persist through an adapter too (phase P3b).** `adapters/runs.py` owns the
  run/item tables — availability checks, the job/run/items insert, the worker's start/finish/fail,
  per-item state, counters and reviewed blocks — so `services/recognition.py` keeps the decisions
  (probe the endpoint, choose the provider, derive the final state) and contains no SQL. The
  architecture test now enforces the boundary for it as well.
- **The framework is written down, and prefill no longer touches the database (phase P3b).**
  `docs/architecture/framework.md` states the layers, what each may import, who owns each concept,
  the six invariants and a playbook per kind of change. Prefill’s geometry now lives in
  `domain/prefill.py` with its thresholds, its project reads in `adapters/projects.py` and its
  writes in `adapters/blocks.py` — which owns the per-block commit that makes an emitted event
  true. `tests/test_architecture.py` enforces the boundary for it, so the selection can be
  tested without a page, a database or an inference server.
- **Pure logic has a place, and the dependency direction is enforced (phase P3a).** `app/domain/`
  now holds the modules that were already values-in/values-out — OTSL, the VRAM estimate, header
  repair, table similarity — and `tests/test_architecture.py` refuses FastAPI, SQLite, HTTP,
  subprocess or any application layer inside it. `app/adapters/store.py` owns the `meta` key/value
  store: model settings and the OIDC state rows no longer import the database or contain SQL, and
  `audit.record_event()` records without handing a connection to the caller.
- **Institutional login with OpenID Connect, configured by the administrator (phase P5).**
  Settings gains an “Identity and access” block — issuer, client ID, client secret (vault, write-only),
  scopes, allowed email domains, button text — and the login screen shows the provider's button only
  when it is ready. The flow is authorization code + PKCE with single-use state rows (ten minutes);
  the first login provisions the account with the instance's default role, an existing email keeps its
  account and role, and unverified emails, foreign domains or disabled accounts are refused with a
  code the page translates. Local accounts keep working.
- **Sub-path and offline installs (phase P5b).** One build can be served under `/tabularium/`:
  `TABULARIUM_BASE_PATH` drives the asset base, the API base (`lib/endpoints.ts`, replacing the
  `/api/...` strings scattered across sixteen components) and the router basename, and the
  container accepts it as a build argument. `tabularium models import <dir> --adapter <id>` installs
  a checkpoint on a host that cannot reach Hugging Face, with the same state a finished download
  leaves; `tabularium models list` reports what is there.
- **Installation hardening (phase P5a).** `tabularium doctor` checks twelve things before the
  interface is opened (data directory, disk, database integrity, schema, port, admins, vault key,
  process model, GPU, runtimes, model downloads, interface) with one verdict per line and
  `--json` for monitoring. The single-process model is enforced: `--workers 2` is refused and
  `WEB_CONCURRENCY>1` stops the app. The application ships as a container
  (`docker/app/Dockerfile`, compose file, non-root user, `/data` volume, healthcheck), built and
  smoke-tested by CI.
- **LlamaIndex OCRia-1 is a first-class remote OCR provider.** Users can store
  their own LlamaCloud key in the backend vault and configure native
  Cost-effective/Agentic routing, effort, aggressive table extraction,
  languages, camera correction, crop, watermark and Markdown options, custom
  prompt, cache policy, timeouts and failure controls. Results are normalized
  to Tabularium blocks with HTML tables preserved for the editable grid.
- **Model surface invariants and a generated manifest** (`docs/architecture/program.md`).
  Every model must declare its capabilities, serving recipe, training recipe, native label map
  and settings; a legitimate absence is declared with a reason instead of being a missing key.
  `python scripts/surfaces.py --check` proves it, `scripts/check.sh` is the single definition of
  done, and `AGENTS.md` is a 150-line contract whose verified technical facts moved to
  `docs/spec/` (read on demand) instead of a 107 KB file that agents never finished reading.
- **Model settings are declared, not branched (phase P2a).** A fifth spec surface,
  `settings=Declared(SettingsSpec(...))`, owns each model's image caps, vendor workflow
  defaults and the flags that gate generic fields (unsupported sampling parameters, n-gram
  processors, workflow changes that need a restart). `model_settings._defaults()` reads the
  spec and no longer compares adapter ids; the Paddle/Unlimited/LlamaCloud workflow
  vocabularies moved to `app/models/types.py` so defaults and validators share one source.
  `tests/test_settings_ownership.py` keeps the chain from coming back. The vendor workflow is
  now a declared field schema (`WorkflowField`: kind, bounds, enum values, option hints,
  cross-field requirements): defaults, validation and the API payload derive from it, the eight
  hand-written vendor validators are one generic function, and `model_settings` contains no
  `adapter_id` comparison. The settings API also returns the schema, and the panel is generated
  from it (`WorkflowFields`): the seven hand-written per-model JSX panels are gone, so a new
  model's settings need no frontend change. The settings API now
  reports `mlx_available` and `generation_unsupported`, so the panel derives which MLX and
  sampling fields to show from the payload instead of keeping its own per-model lists.
- **One model, one file (phase P1).** `app/models/` owns every model's data: one module per
  model, each declaring `Declared(value)` or `NotApplicable(reason)` for serving, training, native
  labels and cloud template. All four surfaces are required, so an incomplete model is a
  `TypeError` at import. `serve_recipes`, `train_recipes`, `model_labels` and `model_registry` now
  derive their dictionaries from the specs and hold no per-model literal; the dataset families,
  data views, VRAM architectures and vendor training prompts moved to `app/models/types.py`
  because the training blocks are written in terms of them. Adding a model touches one spec module
  plus its adapter, and `scripts/surfaces.py --check` proves nothing else is missing.
- Automatic MonkeyOCRv2 post-training compares base and merged weights on the same rented GPU
  before downloading or releasing it. Frozen held-out samples, paired predictions, input hashes,
  CER/WER/layout deltas, changed-output counts, training sample/step context and comparison progress are saved; logs remain optional. A recovery
  command supports checkpoints whose original instance is already gone.
- **Fine-tuning follows each vendor's own recipe.** The training center now dispatches per model:
  ms-swift (MonkeyOCRv2's bundled fork, Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2, dots.ocr),
  LLaMA-Factory (GLM-OCR), PaddleFormers (PaddleOCR-VL). Recipes carry official ids, prompts,
  hyperparameters, flags and the documentation URL; the generator refuses what it cannot run
  (MinerU2.5 stays `planned`), and `docs/training-official-docs.md` keeps the vendor commands
  verbatim.
- **Data views per toolchain** (`services/training_views.py`): `ms-swift-jsonl`,
  `paddleformers-messages`, `llamafactory-sharegpt`. Each run writes `dataset/view.json` with the
  families, prompts and counts it used.
- Per-toolchain LoRA merge on checkpoint registration (`swift export`, `llamafactory-cli export`,
  `paddleformers-cli export`).
- The VRAM estimate uses the selected model's geometry; unknown models are not blocked on a guess.
- **MonkeyOCRv2 verified on a real GPU**: a generated script ran 1 LoRA step on an RTX A5000
  (`train_loss 0.866`, `checkpoint-1`) and `swift export --merge_lora` produced a loadable merged
  checkpoint. The full executor path was verified too (rsync, remote launch, log, 38 downloaded
  artifacts with checksums). Remote scripts now use `$RUN_DIR` for dataset/checkpoints, the SSH
  executor creates the remote run directory before rsync, and the provisioning installs
  `torchvision`/`deepspeed` and the fork's missing `dataset_info.json`.
- **All trainable recipes smoke-tested on Vast.ai** (A100 40 GB and RTX 3090 24 GB, all under
  $0.42/h): Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2, dots.ocr, GLM-OCR and PaddleOCR-VL each ran
  1–3 LoRA steps from Tabularium's generated script and exported a merged checkpoint. Results,
  commands and failures are in `docs/guide/training.md`.
- **Per-recipe environment pins** (`TrainRecipe.environment`): the measured transformers/toolchain
  versions are part of the recipe and returned by `GET /api/system/train-recipes`. Two models can
  need incompatible transformers, so "one environment per toolchain" was wrong and is now
  documented.
- **Remote runs carry their crops**: the data view materialises the referenced images under
  `dataset/images/` and writes the host-side paths when `images_prefix` is set, so a run no longer
  references local absolute paths that do not exist on the GPU host.
- `TABULARIUM_LLAMAFACTORY_DIR` and `TABULARIUM_PADDLEFORMERS_DIR` for the new toolchains.
- **Prebuilt training images for the profiles** (`ghcr.io/nbadino/tabularium-train-{qwen,legacy,glm,paddle}`, plus `tabularium-train-monkey:monkey-v3`; dots.ocr still uses runtime provisioning):
  environment-only for Qwen/legacy/dots/GLM/Paddle (weights from HuggingFace at first run),
  MonkeyOCRv2 also has its weights baked in. Built and published by GitHub Actions; the automatic
  Vast path boots the image and never runs pip. Runtime provisioning remains the fallback.
- **One-click training for non-developers** (`POST /training/auto`): insert an hourly budget,
  press *Rent and train*; Tabularium picks the best Vast.ai GPU within that ceiling (VRAM, compute
  capability, reliability, then price), installs the recipe-specific environment with
  `scripts/cloud/setup_train_env.sh` (`monkey`, `qwen`, `legacy`, `dots`, `glm`, `paddle`), waits for
  `ready.json` and starts training. The job persists phases and logs, survives a page reload, and
  destroys the instance if rental, provisioning or training fails, and after registration.
- **Training dashboard**: KPI cards, loss and GPU history charts, model-download progress, evaluation metrics and run comparison. Training, automatic-provisioning and checkpoint-preparation logs are available on demand from dedicated buttons; status, progress and failures stay visible without exposing raw command output by default.
- **Post-training panel with real numbers**: final train/eval loss, steps, duration and peak VRAM
  from the run log; the **local checkpoint path and size**; links to serving and evaluation.
- **Checkpoint preparation starts automatically when training ends** (`auto_prepare`, on by
  default, disableable under Advanced); the manual button remains for a blocked or resumed plan.
- **Checkpoint preparation as a background job** (`POST/GET /training/prepare-checkpoint`): for a
  remote run the LoRA merge runs on the GPU host and the merged model is downloaded into the run;
  a local run merges locally; the panel shows the plan (remote/local/register), the phase and the
  log. A failed preparation no longer returns a bare shell error after a blocking request.
- **Cleaner training monitor**: keep one compact run summary, remove duplicate post-run metrics and make all raw logs opt-in. Failed automatic jobs show a concise status while retaining detailed logs behind *Show logs*.
- **Run history** (`GET /training/runs`, `GET /training/status?run_id=`): every run of the project
  can be selected, inspected and resumed, instead of only the latest.
- **Environment pins in the training form** (`TrainRecipe.environment`) and a **toolchain-aware rent
  guard**: automatic Vast provisioning is offered only for MonkeyOCRv2/ms-swift, because installing
  the wrong environment is worse than asking for the SSH fields.

### Changed
- The table workspace now has a compact two-level header: back, undo/redo, block verification
  and one save-state control stay visible, while row/column operations and grid details move under
  *Structure*. Crop/full-page switching uses icons in the scan header; the generic dialog close
  control is icon-only instead of printing “Esc”.
- Model setup steps now use plain text navigation and lightweight status markers; removed the
  duplicate recognition action from the completed local checklist.
- Reconciled stale local startup progress with the verified active endpoint when running
  process, adapter and loopback port match. Startup logs remain available after readiness and
  never open automatically. Localized MinerU workflow descriptions and parameter labels while
  retaining official field identifiers in tooltips. Invalid invitations now provide a return
  path; onboarding forms have dedicated spacing and registration exposes the password minimum.
- Streamlined archive and project navigation with full-collection page search and incremental
  rendering (80 archive thumbnails / 50 registry rows initially), retaining bulk operations.
  Results use a compact selector for all saved runs, with loading feedback. Benchmark initially
  shows ready pages and exposes the remaining pages on request; it blocks launching without
  ready pages or model targets. Model settings start with the active inference model when listed.
  New-user creation is disclosed on request. Loading no longer masquerades as an empty registry.
- Corrected Archive's fine-tuning next step when checkpoint preparation is incomplete, and
  prevented reading unassigned team workflow state from visually demoting an approved page.
- Simplified shared work surfaces: removed panel frames and colored heading plates, softened
  navigation, and constrained standard pages to a readable width while keeping document editors
  full width. Training shows available metrics without individual boxes, puts charts before
  evaluation details and hides empty historical GPU panels. Run comparison, recognition history,
  generated files and checkpoint paths open on request. Removed duplicate tool links from Models
  and the temporary-model shortcut from Recognize.
- Browser journey review: recognition results retain their original detected counts after later
  corrections, Playground carries the selected page into recognition, and contextual navigation
  retains the selected run. Studio shows session context once and distinguishes table-block
  verification from verified cells.
- Training now separates Runs and results from New training, starts with the model's official
  recipe, and shows manual GPU configuration on demand. Removed generic Quick/Balanced/Quality
  presets and chat-style rental copy; the measured MonkeyOCRv2 8 GB configuration is in Advanced.
  Finished-run duration stops at its end time, local CUDA capability stays separate from remote
  telemetry, and checkpoint preparation precedes model setup and evaluation.
- Dataset warnings show guidance before optional sample details and offer a direct next step.
  Datasets without validation pages declare this; post-training evaluation requires held-out pages.
- The primary navigation now groups work into Archive, Recognize, Annotate, Fine-tuning and Models.
  Results, benchmark and Playground stay one contextual tab away in their respective workflows.
- The training form is recipe-driven: choosing a model applies its official defaults and allowed
  train types; presets no longer override the learning rate.
- Training progress now separates run status from the selected executor; raw logs open only on
  demand, and the dashboard avoids duplicate post-run metrics. Evaluation's unavailable-model
  actions now use their localized labels and guidance.
- The preflight checks the chosen toolchain instead of always MonkeyOCRv2.
- The ms-swift model cache is **shared and persistent** (`$HOME/.cache/modelscope` remote,
  `<data>/cache/modelscope` local, `model_cache` override) instead of a directory inside the run,
  which re-downloaded tens of GB per run and vanished with a remote `/tmp`; `use_hf` selects
  HuggingFace over ModelScope.
- dots.ocr uses `eager` attention (transformers rejects FlashAttention2 for its vision tower) and
  caps images at 1 MP; Unlimited-OCR/DeepSeek-OCR-2 keep their 8192-token length, below which the
  image-token count crashes the loss.
- Dataset page: removed the per-model "prepare training" buttons; training preparation belongs to
  the Training Center. Removed `alternative_training.py`, `paddle_training.py` and the ERNIEKit VLM
  export (`vlm_*`) — superseded by the recipe views.
- The bundled domain profile is now `shipping-registers-1900s`, in English and without corpus
  names. Projects that saved the old id keep working: it maps to the new one.
- `AGENTS.md` (product spec and agent rules) is now in English and up to date: Univer table
  workspace, current repository structure and schema, distribution with uv, releases, the
  English-only rule for new code, docs and commits.

### Fixed
- **Provisioning no longer touches the system pip/wheel.** The onstart upgraded pip/wheel with
  the image's Debian Python before creating the venv; Debian's `wheel` has no RECORD and the
  upgrade aborted the install (`Cannot uninstall wheel ... installed by debian`). The venv is now
  created first and all package installation happens inside it.
- **GLM-OCR dataset images are relative to the run's dataset directory** (`images/<file>`), as
  LLaMA-Factory documents; the canonical absolute crop paths were not the documented layout. The
  GLM view always materialises the pixels, local or remote.
- **Approved-only datasets skip tables with unverified prefill cells** instead of training on the
  model's own unverified text; the report names the skipped table and the unverified count.
- Remote paths starting with `$HOME` (the ones the Vast rent flow returns for the venv and the
  training repo) were quoted literally, so `cd` and `dirname` looked for a directory named
  `$HOME`; generated training and merge scripts now expand the variable and still quote spaces.
- **Apple `swift` was mistaken for ms-swift** on macOS (`shutil.which("swift")`), so a local merge
  tried to run `Xcode`'s compiler and failed with a shell error. The probe now accepts only a
  Python console script; the panel says the toolchain is missing instead.
- A failed checkpoint preparation rendered the raw `ApiError: API POST …` and left the button
  enabled; it is now a titled notice with the job log, and the job itself never blocks the request.
- `scripts/cloud/setup_cloud_train.sh` installs torch from the index matching the host driver
  before the pinned recipe dependencies, and **skips it when the venv already has that CUDA** so a
  re-provisioning does not download ~800 MB again. It disables a shadowing `cuda-compat` library
  when a real driver is present.
- The provisioning script creates the empty `swift/llm/dataset/data/dataset_info.json` that the
  MonkeyOCRv2 ms-swift fork omits but imports at startup; without it `swift sft` failed in
  `register_dataset_info` with `JSONDecodeError` (local JSONL datasets need no entry).

## [0.3.0] - 2026-10-18

### Added
- **Install with one command**: `uv tool install tabularium`, then `tabularium`.
  uv provides a compatible Python, the interface is prebuilt, and the command
  opens the browser. Data lives in `~/Tabularium` (or `--data`), outside the
  package, and the credential encryption key is created on first start. Options:
  `--port`, `--host`, `--data`, `--no-browser`, `--version`.
- Releases also ship the wheel, checked by installing and starting it in CI.

## [0.2.0] - 2026-10-18

### Added
- **Releases with the interface prebuilt.** Installing from a GitHub release needs
  Python only: no Node.js, no git. Archives come with SHA-256 checksums.
- **Documentation site** at <https://nbadino.github.io/tabularium/>, in English:
  installation, configuration, supported models, local inference, cloud GPUs,
  fine-tuning, teams, troubleshooting and development.
- **Annotator / reviewer workflow.** Each page can be assigned to a distinct annotator
  and reviewer, with personal "to annotate" and "to review" queues, submission,
  change requests and approval checked by the server. An edit after approval
  invalidates it.
- **Team features.** A Share dialog for people and permissions (edit or read only),
  a read-only project view, ownership transfer and an activity feed. Admins can
  invite people by link; invited users choose their own password, and accounts
  created or reset by an admin must change it at first login. Users can edit their
  own profile.
- **Live presence** in the studio: see who else is on the page and who is editing,
  and load their changes without reloading. Unsaved work is never discarded.
- **Multi-model benchmark** on a dedicated page: pick models and approved pages, and
  compare layout (IoU 0.5 / 0.75), reading order, table structure and CER/WER side
  by side. Local models are started and stopped one at a time without touching the
  active inference profile; results appear as each model finishes, and a
  tables-only mode ignores layout and labels. Reports are written to disk, never to
  the database. Pages can be approved in bulk.
- **Training recipes per model**: every open-weight model declares its toolchain,
  official documentation, training types, data format and maturity. Wired through
  ms-swift: MonkeyOCRv2, Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2, GLM-OCR,
  MinerU2.5, dots.ocr. A setup script prepares a GPU host for training, and on
  Vast.ai renting an instance can prepare it automatically.
- **Gemini 4 Argon** through the OpenCode gateway, as an external endpoint: one
  END2END generation returns header, issue number, date and the table in OTSL, so
  it works in Recognize and in the Playground.
- **Unlimited-OCR on Apple Silicon** through MLX (`mlx-vlm` ≥ 0.6.4), with `bf16`
  and `8bit` variants, an anti-loop guard and a dedicated MLX server.
- **Native folder picker**: Browse opens the system folder dialog when the server
  runs on your computer, and falls back to the web browser otherwise.
- Table schema hints per project, passed to models that accept them.
- A single version for backend and frontend, managed by `scripts/version.py`.

### Changed
- README rewritten for researchers; detailed guides moved to the documentation site.
  Italian-only guides were replaced by English ones.
- Studio: views always reachable, Recognize always visible, secondary commands under
  Options; changing page or project waits for canvas and drafts to be saved. The
  table workspace now runs on Univer.
- Models hub: choosing "This Mac" for a model with several MLX variants asks which
  quantization to use; the choice can also be changed later in Settings → Models.
- Studio, Results and Projects: clearer page status at a glance; the transcription
  view no longer traps you without a way back.

### Fixed
- MLX variants and memory: the "runs on this Mac" verdict only looked at the default
  variant, so on an 8 GB Mac Unlimited-OCR was reported as not fitting even though
  the 8-bit variant does. Each variant now declares its own size, and without a
  saved choice the largest one that fits is used.
- Unlimited-OCR on 8 GB Macs: MLX's buffer cache held on to freed memory and the GPU
  timed out on the first page. Up to 12 GB the server now starts with the cache
  off and clears it after each page.
- Recognition with a local model: if the server dies mid-page, the session restarts
  it on the same port and retries that page once, instead of failing it and every
  page after it.
- Approved pages and model drafts: re-running recognition on an approved page added
  unconfirmed drafts that evaluation and export treated as ground truth. On approved
  pages only confirmed blocks now count.
- The backend setup script no longer warns that OCR is missing when it is installed.

## [0.1.0]

### Added
- Recognition, annotation, dataset export, training and evaluation workflow.
