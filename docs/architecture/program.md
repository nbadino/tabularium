# Program: one concept, one owner

Status: accepted · Owner: maintainer · Scope: backend, docs, agent workflow
This document is the plan of record for the consolidation effort. It is not a
specification: the facts it relies on are in `docs/spec/`, and the invariants it
commits to are in code (`backend/tests/test_model_surface.py`,
`scripts/surfaces.py`).

## 1. The problem, measured

The complaint that started this program: an improvement to "model selection" or
"a setting" gets applied to one part of the program and not the others. The
diagnosis is not agent negligence. It is that the same concept is declared in
several places, none of which can fail when the others are left behind.

- The same model id is a key in six registries: capabilities
  (`model_adapters.py`), serving (`serve_recipes.py`), training
  (`train_recipes.py`), native labels (`model_labels.py`), settings defaults
  (`model_settings.py`, a ternary chain), cloud templates (`model_registry.py`).
- One cross-cutting change — "one official recipe per model" (commit `880dcb8`)
  touched 38 files and added 3 760 lines.
- The image cap `1003520` exists in eight files with five distinct meanings
  (layout minimum, end-to-end cap, settings default, training hyperparameter,
  frozen comparison protocol).
- `AGENTS.md` was 1 394 lines / 107 KB. With a 64 KB instruction budget the cut
  lands at line 929; the operating rules begin at byte 102 168. Agents never
  read the rules.
- `AGENTS.md` §6 described `projects.settings_json` keys that the code no longer
  writes (base model, max pixels, split ratio). A prose spec drifts silently.

The common shape: **absence is silent**. A model without a serving recipe, a
setting without a default, a class without a prompt are all indistinguishable
from "no one has done it yet".

## 2. North star

> One concept has one owner. Every invariant is executable. The definition of
> done is a command, not a paragraph.

Four laws:

1. **Ownership.** A concept's data lives in exactly one place. Everything else
   derives from it.
2. **Explicit absence.** "Not applicable" is a declared value with a reason,
   never a missing key or a `None` that means three things.
3. **Executable invariants.** Every rule that a reviewer would enforce is a test
   that fails. Prose documents what is true; tests keep it true.
4. **Generated views.** Matrices, tables and manifests are produced by code. A
   hand-maintained list is a second source of truth waiting to drift.

## 3. Decisions

### D1 — The control plane stays Python. No port.

vLLM, ms-swift, LLaMA-Factory, PaddleFormers, mlx-vlm and transformers are
Python; a Go or Rust backend would remain an orchestrator of Python processes,
with an RPC boundary, a second toolchain, a second CI and the same six
registries on the Python side. The backend's work is I/O (HTTP, subprocess,
SQLite, filesystem, SSE); Python is the right tool, not a compromise.

Rejected: Go/Rust rewrite (4-6 months, no fix for the actual defect).
Revisit only if the product needs horizontal scaling (see D8) — and then only
the HTTP layer would move, not the model/ML logic.

### D2 — TypeScript stays; no third language.

Adding a language is anti-compaction: it doubles the build, the test matrix and
the places an agent must look. Rust may later replace the image pipeline (deskew,
tiling, grid detection) as an extension behind the existing interface. Not now.

### D3 — `domain/` is framework-free; `adapters/` own the outside world.

Target backend layout:

```
backend/app/
  domain/          # pure: models, settings, taxonomy, otsl, metrics. No fastapi, no sqlite, no fs.
  adapters/        # sqlite (db.py), filesystem, http clients, process runners, cloud providers
  api/             # thin routers: parse, call domain, persist through adapters
  models/          # one module per model: spec (data) + behaviour (prompts, parsers, serve argv)
  services/        # only what is genuinely a service (auth sessions, jobs, live hub)
```

Acceptance: an architecture test fails if any module under `domain/` imports
`fastapi`, `sqlite3`, `app.db` or `subprocess`.

Today 18 modules in `services/` import the database directly. That coupling is
why "change a setting" touches four layers.

### D4 — One spec per concept.

`ModelSpec` becomes the only place where a model's data is declared; the six
registries become derived views with the same public API. Same pattern later for
settings and for the label taxonomy.

### D5 — `NotApplicable(reason)` instead of silence.

Phase 0 (this milestone) does it as a declared table validated by a test.
Phase 1 turns it into a type.

### D6 — Generated manifests.

`scripts/surfaces.py` prints the model × surface matrix from the code.
`docs/models.md` tables are generated from it in CI. No hand-written matrix.

### D7 — `AGENTS.md` is a contract, not a spec.

≤ 200 lines: mission, rules with the check that enforces each one, definition of
done, pointers. The verified technical facts move to `docs/spec/` and are read
on demand, never loaded wholesale.

### D8 — Deployment: appliance now, service later, and declared.

One process, one SQLite file, one data volume, one GPU runner. This is right for
a research group and it is what the code does (in-process threads, in-memory
job state, a live hub bound to one event loop). A university that wants
replicas and rolling restarts needs the service model; that is a separate
project, gated on a real request.

Consequences to enforce now: refuse `--workers > 1` explicitly; document the
volume and backup model; add a `tabularium doctor`; ship a container image for
the app (today only training images exist).

## 4. Phases

Each phase is independently shippable, leaves `main` green, and has an
acceptance test that fails before it.

### P0 — Harness (this milestone)

- `backend/app/services/model_surface.py`: the model × surface projection and
  the declared reasons for legitimate absence.
- `backend/tests/test_model_surface.py`: every required surface is declared;
  every excuse is still true; every protocol value is in a declared vocabulary.
- `scripts/surfaces.py`: generated manifest, `--check` for CI.
- `scripts/check.sh`: one command for humans and agents.
- `docs/spec/`: the AGENTS.md content, split by topic; `AGENTS.md` becomes the
  contract.
- `docs/architecture/program.md`: this file.

Acceptance: `python scripts/surfaces.py --check` exits 0 and prints the matrix;
adding a model to `model_adapters.py` without a serving recipe makes it exit 1.

### P1 — `ModelSpec` — done

Move per-model data next to the model; the six registries become derivations.

**Result.** `backend/app/models/` holds one module per model (twelve), each exporting a
`SPEC` with four required surfaces: `serve`, `train`, `labels`, `cloud`, each either
`Declared(value)` or `NotApplicable(reason)`. A spec that forgets one raises
`TypeError` at import. `ServeRecipe` moved to `models/types.py` so a spec can build one
without importing the registry that consumes it; the dataset families, the data views,
the VRAM architectures and the vendor training prompts moved there too, because the
training blocks are written in terms of them.

The registries hold no per-model literal any more:

| Registry | Before | Now |
|---|---|---|
| `serve_recipes.RECIPES` | literal dict | `declared_values("serve")` |
| `train_recipes.TRAIN_RECIPES` | `_SPECS` + maturity/verified tables | built from `declared_values("train")` |
| `model_labels.NATIVE_LABELS` | literal dict of maps | `{id: spec.value("labels")}` |
| `model_registry._CLOUD_TEMPLATES` | literal dict | `declared_values("cloud")` |

`model_labels.DIRECT_CANONICAL` and `model_surface.CANONICAL_VOCABULARY` are now
derived from the `labels=NotApplicable` specs, so an excuse cannot outlive the fact it
describes.

**Acceptance.** Adding a model touches one spec module plus its adapter; the surface
check fails if a registered model has no spec (`tests/test_model_specs.py`); the full
suite is green with the registries producing byte-identical values.

**Not absorbed yet.** Three model-keyed tables still live outside the specs, and each is
a candidate for P2/P5 rather than an oversight: the 26 `adapter_id == ...` branches in
`model_settings._defaults` (P2), the Vast/Modal image profile map in
`auto_training.PROFILES`, and the native-gateway script names in `cloud_manager`. The
capability flags (prompts, parsers, `serve_command`) stay in `model_adapters` until P4,
because they are behaviour.

### P2 — Settings — done

Settings are the fifth spec surface: `settings=Declared(SettingsSpec(...))`.

**What moved.** `SettingsSpec` declares the upstream image caps, which image fields the
model honours, the allowed settings sections, whether the vendor exposes `dtype`, the
three generic flags (`generation_unsupported`, `no_repeat_ngram`,
`workflow_needs_restart`) and the vendor workflow as a **field schema**: `WorkflowField`
carries `key`, `kind` (`bool`, `int`, `float`, `enum`, `version`, `str`, `str_list`,
`int_pair`, `crop_box`), bounds, enum values, option hints and an optional cross-field
`requires`.

**What derives from it.**

- the defaults: `model_settings._defaults()` reads the schema, and the old central
  tables (`_NO_REPEAT_ADAPTERS`, `_UNSUPPORTED_GENERATION_FIELDS`, the restart set) are
  gone;
- the validation: one ~90-line generic function replaced eight hand-written vendor
  validators and the 200-line if/elif chain. `model_settings` contains **zero**
  `adapter_id` comparisons (it had 26 at the start of P2), enforced by
  `tests/test_settings_ownership.py`;
- the API payload: `workflow_fields`, `workflow_intro`, `sections`, `mlx_available`,
  `serving_dtype`, `no_repeat_ngram`, `generation_unsupported`;
- the panel: `WorkflowFields.tsx` renders one control per field kind, so
  `ModelSettingsSection` has no per-model JSX and no `adapter_id === '...'` literal
  (asserted in its component test). The seven hand-written panels are gone.

**Effect on adding a model.** Its settings are declared in its own spec; the panel, the
validation and the payload follow without a frontend change.

### P3 — Domain / infrastructure split — P3a done, P3b next

**P3a (done).** The direction is now enforced instead of hoped for.

- `app/domain/` holds the pure modules that were already values-in/values-out:
  `otsl`, `vram`, `table_headers`, `table_similarity`. They had no dependency on the
  web app; they now have a place that says so, and `tests/test_architecture.py` fails if
  one of them imports FastAPI, SQLite, HTTP, `subprocess` or any application layer.
- `app/adapters/store.py` owns the `meta` key/value store. `model_settings` and `oidc`
  no longer import the database or contain SQL; they ask for a key and get a value.
  `audit.record_event()` lets a service write an audit row without holding a connection.
- `tests/test_architecture.py` also checks that the two migrated services contain no
  `SELECT`/`INSERT` and no `app.db` import, and that every domain module is really there.

The move itself was mechanical (imports in `app/`, `tests/` and `scripts/`), which is the
point: the boundary was already latent, it just was not declared.

**P3b (done).** Six services now reach their data through `app/adapters/`:

| Service | Adapter | What stayed in the service |
|---|---|---|
| `model_settings` | `store` | defaults, validation, the panel payload |
| `oidc` | `store` | discovery, PKCE, provisioning |
| `prefill` | `projects`, `blocks` | selection, geometry (`domain/prefill`), per-block commit choice |
| `recognition` | `runs` | probe, provider, final state |
| `dataset_builder` | `exports`, `projects` | families, split, crops, report |
| `evaluate` | `projects` | metrics, matching, report |

`table_detect` never touched the database; it is pure computation that is also deprecated
(§2.3.5), so it stays where the deletion will find it. `tests/test_architecture.py`
enforces the boundary for the six migrated services and the purity of `domain/`.

### P4 — `inference.py` — done, as far as it pays

The client was 2 000 lines that knew every vendor protocol. Splitting it is not cosmetic:
it is what makes "add a provider" a new module instead of another branch in the same file.

**Moved to `domain/inference.py`** (all pure, all tested without a socket):

- `google_request` / `google_response` / `google_usage`: the `generateContent` payload,
  the meaning of the answer, and the OpenAI→Google sampling names;
- `openai_content` / `openai_request`: message order, system prompt, streaming usage, and
  the `no_repeat_ngram_size` that travels as `extra_args` for vLLM and as `vllm_xargs`
  for MinerU2.5; the sampling dict is copied, never mutated;
- `llamaindex_base_url` / `llamaindex_pages` / `llamaindex_versions` /
  `llamaindex_configuration`: any endpoint the user pastes, three response shapes, and
  the settings→request mapping (tables stay HTML, the job timeout is derived);
- `OuterListScanner` and `tolerant_items`: the streaming JSON-array scanner and the
  parser that survives truncated or category-labelled output.

**Unified in the client.** `_native_vendor_headers` and `_native_parse_page` are one
transport for the vendor pipelines: GLM-OCR, MinerU and TeleOCR used to repeat the same
POST, timeout and error block; now a fourth native model adds a call.

**Deliberately not moved: the HTTP transport.** Every request goes through `requests` in
the client module, and the tests patch it there (`infmod.requests`). Moving the socket to
an adapter would buy a layer at the cost of a mock indirection in fifteen places, and the
transport is cohesive by nature: it is where retries, streaming and timeouts live. The
same applies to `_chat`'s SSE loop and to `qwen_native_page`, which reuses it.

A scripted extraction during this phase left eight shadowed method definitions in the
class (Python keeps the last, so tests passed); `tests/test_architecture.py` now refuses a
name defined twice in the same scope.

### P7 — Close the gaps the framework did not reach

The philosophy was applied where concepts are declared; P7 applies it where they are
consumed. Four items, tracked here:

1. **Thin API and thin services — done.** `app/api/` went from 105 statements to **6**, all
   in `auth.py`. `app/services/` went from **145 in 21 modules** to **12 in 3** — and those
   three are the declared exceptions, each because the queries *are* the boundary:

   | Module | Why it keeps its SQL |
   |---|---|
   | `vault.py` (5) | the encrypted store: the queries are the cryptography boundary |
   | `auth.py` (6) | sessions, passwords and tokens: the authentication boundary |
   | `audit.py` (1) | the trail must be written inside the caller's transaction |

   Both rules are invariants, not lists: `test_the_api_layer_does_not_speak_sql`,
   `test_the_service_layer_does_not_speak_sql`, and two *self-expiring* checks that force
   an exemption to disappear once the module is clean. `SERVICES_SQL_PENDING` is empty;
   the mechanism stays for the next service that needs one.

   The migration found the shapes worth keeping: services that own their transaction
   (`approve_page`, `run_scan`), helpers that take the caller's connection and must not
   open a second writer (`delete_sessions`, `store.put`), and the rule that a persisted
   format is part of the contract (`put_raw` for the legacy `inference_*` keys).

2. **Prefill dispatches by declaration — done.** The adapter declares `native_runner`
   (`teleocr`, `glmocr`, `mineru`, `llamaindex`, `qwen`, `paddle`) next to
   `native_prefill_mode`; `prefill.native_page_items` looks up
   `VllmClient.<runner>_native_page`. The eleven `adapter_id ==` branches are gone,
   PaddleOCR-VL's pipeline is a client method like the others, and the matrix carries a
   `prefill` column (`official (mineru)`). Guards: `NATIVE_RUNNERS` vocabulary, the rule
   *official ⇒ runner*, the runner's method must exist, and
   `test_prefill_dispatches_by_declaration_not_by_model_identity`.
3. **`DEFAULT_MODEL_ID` — done.** `frontend/src/lib/constants.ts` holds the reference
   model once; twelve literals across six files are gone. Typecheck and 258 tests green.
4. **Serving dispatch by declaration — done for the MLX runners.** The recipe declares
   `local_serve` (`""` = the standard MLX server, `mlx-mineru`, `mlx-unlimited`), and
   `serve_manager._local_runner` maps it to the module that implements the log tail, the
   readiness step and the argv. The three `adapter_id ==` branches in the serve path are
   gone; `test_the_serving_dispatch_names_no_model` fails if a model name comes back.
   What remains named is MonkeyOCRv2's vLLM wrapper and its DFlash draft — that is the
   model's documented serving, not a runtime choice — and the shared env plumbing around it.

**P7, closing state.** The four items are done and each is guarded by a test that fails on
the pattern, not on a file list:

| Item | Proof |
|---|---|
| Thin API | `app/api/`: 105 → **6** statements, all in `auth.py`; `test_the_api_layer_does_not_speak_sql` |
| Thin services | `app/services/`: 145 in 21 modules → **12 in 3**, the declared exceptions (`auth`, `vault`, `audit`); `test_the_service_layer_does_not_speak_sql` |
| Prefill by declaration | `native_runner` on the adapter, 11 `adapter_id ==` gone, `prefill` column in the matrix; `test_prefill_dispatches_by_declaration_not_by_model_identity` |
| Serving by declaration | `local_serve` on the recipe (`mlx-mineru`, `mlx-unlimited`), dispatch table, model names gone from the MLX path; `test_the_serving_dispatch_names_no_model` |
| One default model | `DEFAULT_MODEL_ID` in `frontend/src/lib/constants.ts`; twelve literals removed |

Both exemption lists carry a reason and are *self-expiring*: a cleaned module must leave the
pending list or the suite fails. `scripts/check.sh` is green.

### P5 — Deployment hardening — done

The appliance model (D8) is enforced rather than assumed, and an institution can install,
verify, expose and log in without touching the code.

- **Single process.** `tabularium --workers 2` exits with an explanation;
  `WEB_CONCURRENCY>1` raises in the lifespan, so the mistake is caught when uvicorn is
  launched directly.
- **`tabularium doctor`** (`services/doctor.py`): twelve checks, one verdict per line,
  `--json` for monitoring; it never imports the web app, so it works when the app does
  not start.
- **Application image** (`docker/app/Dockerfile`, compose, `.dockerignore`): multi-stage,
  non-root, `/data` volume, healthcheck; built and smoke-tested by CI.
- **Sub-path.** `TABULARIUM_BASE_PATH` drives the Vite base, the API base
  (`lib/endpoints.ts`, which replaced the `/api/...` strings in sixteen components) and
  the router basename; the container takes it as a build arg and the guide shows the
  proxy that strips the prefix.
- **Offline checkpoints.** `tabularium models import <dir> --adapter <id>` stages a copy
  into `<data>/models/<id>` and leaves the state a finished download leaves; `--force`
  replaces, a failed copy leaves nothing. `HF_ENDPOINT` covers an internal mirror. The
  OCR engine's own models are still outside this path.
- **Institutional login (OIDC).** `services/oidc.py` implements authorization code +
  PKCE with single-use state rows; the administrator enables it in Settings (issuer,
  client id, secret in the vault, scopes, allowed domains, button label). The first login
  provisions the account with the instance's default role; an existing email keeps its
  account; unverified emails, foreign domains and disabled accounts are refused with a
  code the login page translates. Local accounts remain the fallback.

### P6 — Docs and agent workflow (partially done in P0)

- `AGENTS.md` ≤ 200 lines.
- Per-directory `backend/AGENTS.md`, `frontend/AGENTS.md`.
- Task template: enumerate surfaces before coding; write the failing test first.
- Generated model table in `docs/models.md`.

## 5. Metrics

| Metric | Before | Target |
|---|---|---|
| Registries per model concept | 6 | 1 (+ derived views) |
| Files to touch to add a model | ~12-38 | 2 (spec module + adapter) |
| `AGENTS.md` | 107 KB, rules truncated | ≤ 20 KB, rules first |
| Rules enforced by a test | a handful | every rule in the contract |
| Commands to verify a change | pytest + tsc + vitest + build | `scripts/check.sh` |

## 6. What we will not do

- Rewrite the frontend or change frameworks. It is not the source of the drift.
- Replace SQLite with Postgres now. It is correct for the appliance model.
- Introduce a job queue, microservices or an event bus. They solve a scaling
  problem the product does not have yet.
- Build "a framework". A fourth abstraction layer would be a fourth registry.
- Port the backend to another language.

## 7. How we work (agents included)

1. Read this file and `AGENTS.md`. Do not read `docs/spec/` wholesale; open the
   one file the task points to.
2. Before coding, run `python scripts/surfaces.py` and list the surfaces the
   change touches, with `file:line`.
3. Write the test that fails today. Show it failing.
4. Make `scripts/check.sh` pass.
5. If the change makes a rule in `AGENTS.md` obsolete, change the rule in the
   same commit.
