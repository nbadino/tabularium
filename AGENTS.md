# AGENTS.md — Tabularium

**This file is a contract, not a specification.** It contains the rules that anyone
(human or AI agent) must satisfy, the command that proves it, and where to find the
knowledge you need for a specific task. The verified technical facts live in
[`docs/spec/`](docs/spec/README.md) and are read **on demand**, never loaded wholesale.
The plan of record for the consolidation work is
[`docs/architecture/program.md`](docs/architecture/program.md).

If this file and the code disagree, the code wins: fix this file in the same commit.

## 1. What we are building

One tool that takes a researcher from an archive of scanned pages to structured text
and, when the base model is not good enough, to a fine-tuned model: **register →
recognise → annotate → export → train → evaluate**. Model-agnostic, self-hosted, one
instance per research team. The reference corpus is the *Historic Shipping Index*
(dense newspaper registers, borderless tables); the reference model is
MonkeyOCRv2-Parsing. Neither is a limit.

## 2. The rules

Each rule states how it is enforced. A rule without a check is a rule that will be
broken by the next change; when you add a rule that can be checked, add the check.

**R1 — Write in English.** Documentation, README, CHANGELOG, commit messages, code
comments, identifiers, logs. Existing Italian comments are translated when the
surrounding code is touched, never in bulk. The UI stays multilingual (R11).
*Enforced by:* review.

**R2 — Never modify a vendor repository.** MonkeyOCRv2 and the other models are
external checkouts; Tabularium generates commands from recipes and never vendors
their code. *Enforced by:* review; `config.py` is the only place that knows paths.

**R3 — Verify facts against the primary source.** Coordinates, prompts, OTSL, JSONL
and hyperparameters come from the vendor: the table for each is in
[`docs/spec/10-official-conventions.md`](docs/spec/10-official-conventions.md). When
in doubt, re-read upstream before writing code or prose. A fact that is not in the
spec and not in the code does not exist. *Enforced by:* review; tests that pin the
oracle (`tests/test_otsl.py`, `tests/test_datasets.py`).

**R4 — One concept, one owner.** A model, a setting, a label or a prompt is declared
in exactly one place; every other view derives from it. Before changing a concept,
run `python scripts/surfaces.py` and enumerate the surfaces you will touch, with
`file:line`. *Enforced by:* `backend/tests/test_model_surface.py`,
`scripts/surfaces.py --check`.

**R5 — Absence must be explicit.** "Not applicable" is a declared value with a
reason, never a missing key, a `None`, or a silent fallback. If you add a model
without a serving recipe, you must say why. *Enforced by:*
`backend/tests/test_model_surface.py`.

**R6 — Tests come before the change.** Backend: pytest. Frontend: `tsc --noEmit`,
Vitest, `vite build`. Risky flows (autosave and page change, prefill confirmation,
table structure) get a component test. A behaviour change adds a test that fails on
the previous commit. *Enforced by:* `scripts/check.sh`, CI.

**R7 — The definition of done is a command.** `scripts/check.sh` green, and the new
test red on the commit before. Nothing else counts as done.

**R8 — No hard-coded paths.** Everything comes from `config.py`:
`TABULARIUM_ROOT` for data, `config.RESOURCE_DIR` for files shipped with the app.
Anything read at runtime must be in the wheel's `force-include`.
*Enforced by:* `tests/test_packaging.py`.

**R9 — User data is not disposable.** No automatic destruction of projects, pages,
blocks, grids or checkpoints. Every destructive operation needs an explicit
confirmation in the UI and a guard in the code. *Enforced by:* review, plus the
confirmation tests for prefill and page approval.

**R10 — Coordinates and structure are validated against the oracle.** Table merges
and bboxes are checked against the official `otsl_to_html` algorithm in tests, never
against intuition. *Enforced by:* `tests/test_otsl.py`.

**R11 — Everything the user reads is localised (it/en/fr).** No hard-coded UI
string: text goes through the i18n dictionaries, `frontend/src/i18n/it.ts` is the
source of truth for the shape, `en.ts`/`fr.ts` must match it exactly (same keys,
same `{placeholders}`, same plural shape). Backend messages that reach the UI are
localised through `services/i18n.py`; codes and logs stay English.
*Enforced by:* `frontend/src/i18n/i18n.test.ts`, `backend/tests/test_i18n_catalog.py`.

**R12 — Auth is defence, not decoration.** Every new route is protected by its
`require_resource` (or an explicit check when the project id is in the body). No
self-demotion, no deleting the last admin, no self password reset, 404 (not 403) for
other people's projects. Passwords and tokens only hashed; `TABULARIUM_AUTH=off` only
in tests. *Enforced by:* `tests/test_auth.py`, `tests/test_page_work.py`.

**R13 — Runs are reproducible from disk.** Training state, generated scripts, logs
and metrics live in `<root>/projects/<project>/runs/<run_id>/` so the UI can replay
a run after it ended. Model caches are shared and persistent, never inside a run.
*Enforced by:* review; `tests/test_artifacts.py`.

**R14 — Release by the book.** `python3 scripts/version.py bump`, move `[Unreleased]`
notes under the new version, green CI, then tag. Never publish a release whose README
describes features it does not contain. *Enforced by:* `release.yml`.

**R15 — Documentation follows the code, not the other way round.** When a fact
changes, update the one file in `docs/spec/` that states it, in the same commit. When
a rule changes, update this file. Never grow this file past ~200 lines: if it needs
more, the content belongs in the spec.
*Enforced by:* review; `scripts/surfaces.py --check` for the model matrix.

## 3. Definition of done

```bash
scripts/check.sh            # everything CI runs, one command
scripts/check.sh --fast     # invariants only: seconds, run it before every commit
```

What `check.sh` runs: backend pytest, frontend typecheck + Vitest, the model surface
invariants, and (unless `--fast`) the production build.

## 4. Where the knowledge lives

| Need | Read |
|---|---|
| Why the repo is shaped this way, phases, decisions | [`docs/architecture/program.md`](docs/architecture/program.md) |
| The layers, the owners, the playbooks for a change | [`docs/architecture/framework.md`](docs/architecture/framework.md) |
| What was wrong, what it cost, what is still open | [`docs/architecture/analysis.md`](docs/architecture/analysis.md) |
| The model × surface matrix (generated) | [`docs/spec/models-matrix.md`](docs/spec/models-matrix.md) |
| Upstream conventions (prompts, OTSL, coordinates, JSONL, VRAM) | [`docs/spec/10-official-conventions.md`](docs/spec/10-official-conventions.md) |
| Data model and migrations | [`docs/spec/40-data-model.md`](docs/spec/40-data-model.md) |
| Dataset families | [`docs/spec/50-datasets.md`](docs/spec/50-datasets.md) |
| Labels, page genres, native vocabularies | [`docs/spec/60-taxonomy.md`](docs/spec/60-taxonomy.md) |
| Training recipes and environments | [`docs/spec/70-training-center.md`](docs/spec/70-training-center.md) |
| Add or change a model (serve, train, labels, cloud) | `backend/app/models/<id>.py`, then `python scripts/surfaces.py --check` |
| Where files live | [`docs/spec/31-repository-layout.md`](docs/spec/31-repository-layout.md) |
| Everything else in the spec | [`docs/spec/README.md`](docs/spec/README.md) |
| User-facing docs | `docs/guide/`, `mkdocs.yml` |

## 5. Failure modes we have already paid for

These are the shapes of the bugs that cost the most. Treat them as red flags in your
own plan, not as history.

1. **The half-change.** The setting/model/class was updated in the registry the agent
   found first. Guard: enumerate surfaces first (R4), and let `surfaces.py --check`
   prove it.
2. **The silent absence.** A model without a serving recipe, a project setting that
   nothing reads, a UI field with no backend. Guard: R5.
3. **The prose that drifted.** A document described behaviour the code no longer had.
   Guard: R15; the spec is a record, the test is the truth.
4. **The duplicated constant.** The same number with three meanings (e.g. the image
   cap in inference, settings and training). Guard: declare it once, near its owner.
5. **The "while I was there".** Unrelated refactors inside a behaviour change. Guard:
   one phase at a time, one acceptance test per phase (see `program.md`).

## 6. If you are unsure

Ask, or write the smallest change that makes an invariant hold. Do not invent a
convention that is not in the code, and do not "fix" a rule by deleting the test that
enforces it.
