# The consolidation work, measured

This is the record of what was wrong, what was built, what it cost and what is still open.
It exists so the next person (human or agent) does not have to rediscover it, and so that
claims about the state of the repo can be checked against numbers instead of memory.

The plan of record is [`program.md`](program.md); the rules are
[`framework.md`](framework.md) and the root `AGENTS.md`. This file is the analysis.

## 1. The starting point

The complaint was "every agent improves one part of the program and forgets the others".
The measurement confirmed it:

| Symptom | Evidence |
|---|---|
| Six registries listing the same models | `serve_recipes.py` (320 lines), `train_recipes.py` (562), `model_labels.py` (290), plus adapter factories, settings panels, dataset families |
| The same model known in a dozen places | Adding one model meant editing up to 38 files |
| Instructions too large to be loaded | `AGENTS.md` was 1 394 lines / 107 KB; the agent instruction budget cut it at line 929, while the rules began at byte 102 168 — so the rules were invisible |
| Services owning the database | 18 of 19 service modules imported `connect()`; 24 contained SQL |
| UI branching on identity | 26 `adapter_id ==` comparisons in `model_settings`, seven hand-written settings panels |

The diagnosis was not "the language is wrong" and not "the runtime is slow". It was
**ownership**: a concept had no single home, so every surface had to be remembered, and
whoever remembered a subset produced a half-change.

## 2. What was built

**One declaration per model** (`backend/app/models/<id>.py`): serve recipe, training
recipe, labels, cloud behaviour and settings, with required keyword-only fields. Absence is
declared (`Declared(...)` / `NotApplicable(reason="...")`), never missing. Twelve specs;
one registry; the other views derive.

**Six invariants, each with a test**:

1. a model without a complete spec does not construct;
2. absence is explicit (`NotApplicable` with a reason);
3. one owner per concept (mutation tests in `test_settings_ownership.py` and the surface
   matrix);
4. services reach data through `adapters/`, `domain/` stays pure (`test_architecture.py`);
5. the settings panel is generated from the payload (`workflow_fields`), 0 per-model
   branches in `model_settings`;
6. done = `scripts/check.sh`.

**One command as the definition of done**: `scripts/check.sh` (full) and `--fast`
(invariants only, seconds).

**Documents with a division of labour**: `AGENTS.md` is the contract (150 lines, down from
1 394); `docs/spec/` holds the verified facts (14 files, read on demand);
`docs/architecture/program.md` is the plan of record; `framework.md` is the map of layers,
owners and playbooks for a change.

## 3. Phase by phase

| Phase | What changed | Evidence |
|---|---|---|
| P0 | `AGENTS.md` 1 394 → 150 lines; the facts split into `docs/spec/`; rules each with an enforcement | `scripts/check.sh` |
| P1 | `ModelSpec` and 12 model files; `scripts/surfaces.py`; the six registries reduced to one | `test_model_specs.py`, `test_model_surface.py` |
| P2 | Settings panel generated from `workflow_fields`; `WorkflowFields.tsx`; 7 panels and 26 branches removed | `ModelSettingsSection.test.tsx`, parity tests |
| P3a | Layering: `domain/` and `adapters/store.py`; settings and OIDC lose the database | `test_architecture.py` |
| P3b | Prefill, recognition, dataset builder, evaluate: decisions in the service, tables in `adapters/{projects,blocks,runs,exports}.py` | `test_architecture.py` covers six services |
| P4 | Protocol halves out of `inference.py` (`openai_*`, `google_*`, `llamaindex_*`, scanner, tolerant parser); one native transport for GLM/MinerU/TeleOCR | `test_inference_protocol.py` (17 tests) |
| P5 | Dockerfile, compose, `doctor`, single-process guard, CI container job | `test_doctor.py`, CI |
| P6 | Nested `backend/AGENTS.md` and `frontend/AGENTS.md`; generated `docs/spec/models-matrix.md` | `surfaces.py --write` |

## 4. The bugs this work found

Each of these was live in the tree, and each is now either fixed or guarded. They are the
best evidence of why the invariants exist.

1. **The rules were never read by an agent.** `AGENTS.md` was truncated at line 929 of
   1 394, before the rules at byte 102 168. Every "the agent ignored rule R…" incident has
   this as a candidate cause.
2. **A stale specification.** The old document said project settings hold the base model,
   the pixel cap and the page split; they hold labels, conventions, table hint, known
   headers and the study protocol. Corrected in `docs/spec/40-data-model.md`.
3. **Error messages drifted from the validation.** After the validators were generalised,
   six tests still asserted hand-written wording ("Detection o Segmentation", "limite
   ufficiale TeleOCR"). The messages are now derived from the schema; the tests assert the
   contract, not the sentence.
4. **A derived field that lied.** `oidc_client_secret_set` was computed before the vault
   write and reported `false` after the secret was stored.
5. **Invented column names.** Rewriting the counters from memory produced `done_pages`; the
   schema has `completed_pages`, `succeeded_pages`, `failed_pages`. The worker failed with
   "no such column" and the run reported `failed` with no explanation.
6. **A check in the wrong order.** Moving recognition's checks into the adapter put the
   endpoint probe before "the model is already busy", so the user read "endpoint
   unreachable" when the real problem was another session. Fixed by separating `check()`
   from `create()`.
7. **Eight shadowed method definitions.** A scripted extraction cut a range at the wrong
   marker and left dead copies of `_generate_content`, `_chat`, `qwen_native_page`,
   `glmocr_native_page`, `mineru_native_page`, `is_cloud`, `is_modal` in the class — 490
   lines. Python keeps the last definition, so every test passed. Found by inspecting
   method boundaries; removed; `test_no_name_is_defined_twice_in_the_same_scope` now fails
   on the pattern.
8. **A constant left behind.** `LLAMAINDEX_BASE_URL` stayed in the service after its only
   user moved to `domain/`; the first `None` argument would have raised `NameError`.
9. **A frontend import that broke the mocks.** `API_BASE` lived in `lib/api`, which tests
   mock wholesale; it moved to `lib/endpoints.ts`.

10. **A test that patched half of the launch path.** `launch()` syncs with `Popen` (rsync
    reports one file at a time, which is how the progress bar moves) and launches with
    `run(timeout=60)`; the test patched only `run`, so a real rsync ran first and the test
    could not pass on a machine without the GPU host. The streaming loop became
    `_stream(command, stream, on_line)`, the test replaces that seam, and a second test
    pins what the seam promises: a `TABULARIUM_FILE:` line increments the file count and a
    directory announcement does not. This was the last red in the suite.

## 5. Numbers, before and after

| Metric | Before | After |
|---|---|---|
| `AGENTS.md` | 1 394 lines / 107 KB | 150 lines |
| Model registries | 6 | 1 (`models/__init__.py`) |
| Per-model branches in `model_settings` | 26 | 0 |
| `model_settings.py` | 649 lines | 511 |
| `inference.py` | ~2 000 lines (2 476 with the shadow duplicates) | 1 956 |
| Services importing the database | 18 of 19 | 0 of the ones that own a concept |
| `domain/` + `adapters/` | did not exist | 1 786 lines, 10 modules |
| Backend tests | no architectural invariants | 690 passing, 0 failing, 6 skipped |
| Model specs | — | 12, all surfaces declared |

## 6. What was deliberately not done

- **The HTTP transport in `inference.py`.** The tests patch `requests` inside the client
  module; moving the socket would add a mock indirection in fifteen places and buy a layer
  that is not where the bugs were. The protocol *shapes* moved, the socket did not.
- **The five remaining `adapter_id` comparisons in the UI.** They are content, not
  capability: a reference-model badge, an 8 GB preset valid only for one model's VRAM math,
  a runtime-state warning. A payload flag per sentence would be worse.
- **OCR engine models without an offline import.** RapidOCR downloads to its own cache;
  there is no import path to share, and pretending otherwise would be a lie in the spec.
- **A rewrite in another language.** The defect was ownership, not runtime.

## 7. How to work in this repo now

1. Read `AGENTS.md` (150 lines) and the playbook for your kind of change in
   `framework.md`.
2. Enumerate the surfaces with `python scripts/surfaces.py` before touching a concept.
3. Write the failing test first; run `scripts/check.sh --fast` before every commit.
4. When a document and the code disagree, the code wins: fix the document in the same
   commit.
5. After a scripted move, run the duplicate-definition test before trusting the result.

## 8. Open work

- **Product**: the OCR offline import; OIDC "test connection" and group→role mapping; the
  remaining screens that could read more from the payload.
- **Structure**: `table_detect` is pure but deprecated and still in `services/`; the
  adapters could be read as one layer once the filesystem moves there too (paths, crops,
  run directories are still touched directly).
- **Process**: a CI check that regenerates `docs/spec/models-matrix.md` and diffs it, so the
  generated file cannot drift.
