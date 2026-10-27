# The Tabularium framework

Not a library and not a general-purpose architecture: the specific set of layers, owners
and invariants this application uses. It exists because the same failure kept recurring —
a change applied where it was first found, and forgotten everywhere else. The plan that
produced it is [`program.md`](program.md); this file is the result, for whoever writes the
next feature.

## Layers, and what each may import

| Layer | May import | Owns | Enforced by |
|---|---|---|---|
| `app/domain/` | stdlib, pure libraries (`apted`, `rapidfuzz`), `app.schemas` | decisions and formats: OTSL, geometry, VRAM estimate, header repair, similarity | `tests/test_architecture.py` |
| `app/adapters/` | the outside world | SQLite (`store.py`, `projects.py`, `blocks.py`, `runs.py`, `exports.py`), later the filesystem, HTTP, subprocess | review; the boundary is new |
| `app/models/` | `models.types`, `models.base` | one spec per model: serve, train, labels, cloud, settings | `tests/test_model_specs.py`, `scripts/surfaces.py --check` |
| `app/services/` | domain, adapters, models, the registries | orchestration: a job, a run, a request that takes several steps | review |
| `app/api/` | services, schemas | HTTP: parse, call, persist, permissions | `tests/test_auth.py` |
| `frontend/src/` | the API payload | rendering; no per-model rule | `tests/*.test.tsx`, i18n parity |

The rule is one sentence per arrow: **domain takes values and returns values; adapters
know the world; services decide the order; the API translates; the UI renders.**
`tests/test_architecture.py` rejects FastAPI, SQLite, HTTP or subprocess inside `domain/`,
and SQL inside the services that already went through `adapters/`.

## Ownership: one concept, one file

| Concept | Owner | Everything else |
|---|---|---|
| A model's capabilities, prompts, parsers | `services/model_adapters.py` | asks the adapter |
| A model's serving/training/labels/cloud/settings | `models/<id>.py` | derived views in the four registries |
| A project's classes and conventions | `project.settings_json` | read through the API |
| Instance settings, OIDC config | `services/settings.py` (+ vault for the secret) | `GET /api/settings` |
| A model's settings defaults and validation | its `SettingsSpec` (`WorkflowField`) | `model_settings`, the panel, the payload |
| Table format | `domain/otsl.py` | tested against the official oracle |
| Geometry decisions for prefill | `domain/prefill.py` | `services/prefill.py` orchestrates |

## The invariants

1. **Absence is declared.** `Declared(value)` or `NotApplicable(reason)`; a missing key is
   a failure, not a state. `tests/test_model_specs.py`.
2. **A model cannot be built incomplete.** All five surfaces are required fields:
   forgetting one is a `TypeError` at import.
3. **One owner per concept.** Registries are derived; the literal is deleted in the same
   change that migrates it. `tests/test_model_surface.py`, `scripts/surfaces.py --check`.
4. **The database is reached through an adapter.** `services/model_settings.py` and
   `services/oidc.py` contain no SQL and no `app.db` import.
5. **The UI is generated from the payload.** The settings panel renders `workflow_fields`;
   a new model's workflow needs no JSX. Asserted in the component test.
6. **The definition of done is a command.** `scripts/check.sh`.

## Playbooks

**Add a model.** Write `app/models/<id>.py` with the five surfaces (declaring what does not
apply and why), register its behaviour in `model_adapters.py`, add it to `models/__init__`.
Run `python scripts/surfaces.py --check`. No other file should need to change; if one does,
that file is a missing owner — fix the framework, not the call site.

**Add or change a setting.** Declare it as a `WorkflowField` (kind, bounds, values, label,
optional `requires`) in the model's `SettingsSpec`. Defaults, validation, the API payload
and the panel follow. If it is global rather than per model, it belongs in
`services/settings.py` and the instance panel.

**Add an endpoint.** A thin function in `app/api/`: parse with a schema, call a service,
return a schema. Protect it with `require_resource` (or an explicit check when the project
id is in the body). Business rules do not live here.

**Add an external provider.** An adapter or a service with an explicit protocol
(`IdentityProvider`, a cloud manager); configuration in settings, secrets in the vault,
never in the payload.

**Move code with a script.** Check the result with `tests/test_architecture.py::test_no_name_is_defined_twice_in_the_same_scope`
before trusting it: a range replacement that stops at the wrong marker shadows a method and
the tests still pass, because Python keeps the last definition.

**Add a model with a vendor pipeline.** Declare `native_prefill_mode` and
`native_runner` on the adapter and add `<runner>_native_page` to `VllmClient`. Do not add an
`adapter_id ==` branch to `prefill.py`: `test_prefill_dispatches_by_declaration_not_by_model_identity`
fails if you do.

**Change a data shape.** A versioned migration in `db.py` plus a test in
`tests/test_db_migrations.py`; update the one file in `docs/spec/` that states the shape.

## What is still porous

Stated here so nobody assumes the boundary is finished:

- No service that owns a concept reaches the database directly any more. What is left is
  `inference.py`, which still carries the transport for every vendor protocol (P4: the
  Google protocol and the streaming/tolerant parsers have moved to `domain/inference.py`),
  and the filesystem, which several services still touch directly (paths, crops, runs).
- `app/adapters/` has five modules (`store.py`, `projects.py`, `blocks.py`, `runs.py`,
  `exports.py`). HTTP and the filesystem will move there when a service needs them twice.
- The settings panel is generated. Five `adapter_id` comparisons remain in the UI and they
  are content, not capability: the "reference model" badge, the 8 GB preset that is only
  valid for MonkeyOCRv2's VRAM math, and a Paddle runtime warning. Turning those into
  payload flags would be a flag per sentence.

## For an agent

1. Read `AGENTS.md` (the contract) and this file. Open `docs/spec/` only for the fact you need.
2. Enumerate the surfaces the change touches: `python scripts/surfaces.py`.
3. Write the failing test first; make `scripts/check.sh` pass.
4. If the change needs a file that is not its owner, the framework is wrong: say so in the
   commit message and fix the owner in the same change.
