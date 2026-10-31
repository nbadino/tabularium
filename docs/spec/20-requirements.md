# 3. Functional requirements (user view)

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

1. **Projects**: create a project, import images/PDFs from the browser or pick a source folder on
   the server (native folder dialog when the server runs locally), register pages, metadata
   date/issue/page/page type, preview.
2. **Annotation studio** (the core of the product):
   - **reviewable geometric rectification**: *Align and compare* produces a proposal without
     touching annotations or the active master; the only neural rectifier is `monkeyocr`, the
     model's official preprocessor (§2.3.3), alongside rotation-only `deskew` and manual
     four-corner perspective and mesh corrections. Original and proposal stay side by side until
     *Accept as master*; only acceptance, after confirmation, removes blocks whose coordinates
     would become invalid. The master is a lossless PNG and becomes the single source for canvas,
     prefill, crops and export; JPEG only for previews/tiles. Metadata and API declare requested
     and actual engine, fallback, warnings and diagnostic errors. Prefill never re-runs deskew on
     the original when an accepted master exists;
   - smooth zoom/pan on high-resolution scans (the server serves downsampled tiles/previews).
     Gesture conventions (as in document viewers): wheel/two fingers = pan, Ctrl/Cmd+wheel (and
     pinch) = zoom around the pointer, Space/middle-drag = temporary pan, Ctrl+0 = fit, Ctrl± =
     zoom. Wheel deltas are normalized to pixels (`wheelDeltaPixels`) and the zoom factor is
     exponential with a clamp (`wheelZoomFactor`, tested in `canvasGeometry.test.ts`);
   - the three studio columns (pages | canvas | content) are resizable with `Splitter` (drag,
     arrows ±16px, double click = reset); widths persist in
     `localStorage['tabularium.studio.split']`. Views (all / canvas only / transcription only) are
     always reachable from a selector;
   - block tools: rectangle and polygon; colored class palette; edit/resize/delete;
   - **table workspace**: rows/columns, **merged cells**, "phantom" columns where rules have faded,
     cell-by-cell transcription from the keyboard, column actions (trim, fill down, split, join…),
     arithmetic checks on column sums, live HTML preview — in the studio or on a second screen
     (`/tabella/:blockId`). Its two-level header keeps back, undo/redo, block verification and one
     save-state control visible; grid details and column operations live under *Structure*, while
     crop/full-page switching is an icon control in the scan header;
   - **reading order**: numbering of page blocks + order inside tables; a "flow" preview (arrows)
     that follows the order for visual validation;
   - **transcription** of text blocks with the conventions checklist in view;
   - **prefill via pseudo-labeling**, distinct and non-interchangeable engines/modes:
     - `ocr` (RapidOCR/PaddleOCR): detects **text lines** and labels them `Text`. It has no notion
       of tables, but with `table_promote=True` (default) the largest cluster of consecutive lines
       whose geometry proves a grid is **promoted to a `Table` block** with the grid and cells
       prefilled cell by cell (merged columns are not needed); lines outside the cluster
       (masthead, issue number) stay `Text`. Cells with text start as `source=ocr`,
       `verified=false`. Useful on prose pages and, with promotion, on registers too; can be
       turned off per request.
     - `model/two_stage` (e.g. MonkeyOCRv2 via vLLM): layout, then recognition per block;
     - `model/end2end`: one official bbox+label+content generation. If the `Table` content is
       valid OTSL it is saved as is; otherwise it explicitly falls back to the table crop;
     - `model/native`: adapters with an official vendor pipeline (Qwen3-VL, PaddleOCR-VL,
       MinerU2.5, GLM-OCR, TeleOCR) run that pipeline (`prefill.native_page_items`).
     In every case the training data are the user's **corrections**, not the raw output: every
     prefilled cell/block starts unverified, and the export report tells the two apart
     (`table_cells.verified` / `prefill_unverified`). Feeding predictions back without correcting
     them teaches the model its own mistakes;
     - **explicit replacement modes**: prefill never deletes without the user choosing. `merge`
       adds; `replace_drafts` removes only unconfirmed drafts (`prefill_source` not null **and**
       `confirmed=0`), never human work; `replace_all` deletes everything, grids included, and
       requires confirmation in the UI (`replace` remains as a legacy alias of `replace_all`). The
       response declares `replaced_blocks`/`replaced_grids`; on a non-empty page the Prefill button
       opens a confirmation dialog (`PrefillDialog`);
     - **where drafts live**: in the content panel, not on the image — nothing generated appears on
       the canvas until someone verifies it. A draft's **class** is changed from the panel
       (`onDraftLabel` → `PATCH /blocks/{id}`): since it is not on the canvas, that is where it is
       relabelled, for example when a date ends up inside `Page-header` and must become
       `Issue-date`. **Tables** are the exception, for a measured reason: the region is what layout
       gets wrong most often (on the 11.3 MP sample `index` page, PaddleOCR-VL proposes only the
       left column as `Table` and leaves *From/For/Latest Report* out as `Text`), and the grid lives
       **inside** the crop, which is the block's bbox: without a box on the image the correction is
       impossible. A table draft therefore sits on the canvas, dashed and lighter than an
       annotation, and is resized like any block. On resize the grid **is redone** on the new region
       (`/table/detect`, OCR fill when a local engine exists): keeping it would be worse than losing
       it, because `vlines`/`hlines` are normalized to the crop and on a different crop point at
       different ink. If a transcribed cell is already `verified`, the redo does not start by
       itself: a notice asks for permission;
     - **drafts never delete themselves**: since they are not on the canvas they never reach the
       autosave payload, so `sync_annotations` cannot infer their deletion from their absence (it
       used to, and the first canvas edit after a prefill run destroyed every draft with its grid).
       They are discarded with `DELETE /blocks/{id}`, with `replace_drafts`, or by listing them in
       `deleted_ids` in the bulk save — this is how the canvas deletes a table: whoever deletes says
       so, and an id that comes back among the `items` (undo) wins over the deletion;
     - **page revision**: every write that does not go through autosave — `PUT /blocks/{id}/table`,
       `PATCH /blocks/{id}` — advances `annotation_revision` and returns it
       (`TableSaveOut.annotation_revision`, `BlockOut.annotation_revision`). The client realigns
       with `useAnnotationState.syncRevision`: without it, the next autosave would be rejected with
       a 409 for a conflict that does not exist;
     - **table schema hint**: a project can describe the register header (e.g. "Year and Tons are
       merged on a row above") in `settings_json["table_hint"]` (`GET/PUT
       /api/projects/{id}/table-hint`; in the Studio's dedicated dialog). The text is appended to
       the table recognition prompt **only** by models that declare it acceptable
       (`ModelCapabilities.accepts_prompt_hint`): today only Qwen3-VL, the only one runnable
       locally on Apple Silicon with this property and therefore the only one verifiable without a
       remote endpoint. The natural-language prompt family (MonkeyOCRv2/GLM-OCR/TeleOCR) is the
       natural candidate but stays off until measured; protocol-prompt or native-pipeline engines
       (PaddleOCR-VL, MinerU2.5, DeepSeek-OCR-2, Unlimited-OCR) ignore it by construction, and a
       wrong gate would have sent out-of-distribution text to a model that does not understand it.
       The hint is a suggestion, not a truth: it sits alongside the deterministic header repair
       (`domain/table_headers.py::repair_flat_header`), which does not depend on the model
       obeying. Experimental: to be measured on an `index` page before calling it useful;
   - autosave, per-page progress, keyboard shortcuts;
   - **autosave and navigation**: changing page/project waits for the store's `flush()`
     (`useAnnotationState` exposes `dirty`/`getDirty`/`flush`); a failed flush blocks the change.
     Unmounting with dirty work triggers an emergency save. Debounce alone only protects closing the
     browser.
3. **Dataset builder**: generates the JSONL families (§7), split **by page** and never by crop,
   0–1000 normalization, class map, stats and validations (boxes outside the page, classes without
   a prompt, etc.).
4. **Training center**: a wizard that generates the training script from the selected model's
   recipe (§10), system checks (GPU/VRAM/CUDA), launch on this machine or a remote GPU, **log
   streaming (SSE) + loss chart + GPU usage**, stop, resume from checkpoints.
   The primary monitor names the current phase and next action, separates phase time
   from total rental time, and shows dataset composition, actual steps/total, loss,
   validation loss and measured transfer progress when available. Container image
   download, verification, extraction and SSH readiness are separate observations.
   Never invent percentages or ETA when a provider does not supply them. Training
   completion, checkpoint recovery, paired evaluation, registration and verified GPU
   deletion are distinct outcomes. Raw logs stay behind an explicit button; failures
   must be understandable without opening them. A nonzero remote exit must not skip
   recovering existing checkpoints before the rented resource is released.
5. **Evaluation & iteration**: GT vs prediction overlay, §11 metrics, failure analysis; a
   **benchmark** compares several models or checkpoints on the same gold pages (M17).
6. **Try a model** (`/playground`): a temporary test of the active model or local OCR on one page;
   to save, correct and export, use Recognize.
7. **Self-hosted instance** (auth/users/settings):
   - **first start**: a setup screen creates the instance administrator (one account, then normal
     logins); registration **closed by default** (`TABULARIUM_REGISTRATION_OPEN=0`), which the admin
     can open in Settings;
   - **login/logout**: HttpOnly + SameSite=Strict cookie sessions (`tab_session`) with an
     `Authorization: Bearer` fallback; resetting a user's password invalidates all their sessions;
   - **global roles**: `admin` (governs everything, users/settings included), `editor` (creates and
     works on projects), `viewer` (reads). The backend is the source of truth: the UI only disables
     commands that would end in an error;
   - **project access**: `projects.owner_id` + `project_members` to share a project with other users
     (editor/viewer). Inside a project the **project role decides**, not the global one: a global
     `viewer` added as project editor writes there, and demoting a user does not change the projects
     they own (pinned by `test_project_capabilities_follow_project_role_not_global_role`). The global
     role governs instance actions only (creating projects, users, settings). Sensitive rules enforced server-side: no self-demotion, no deleting the last
     admin, 404 for other people's projects (existence hidden);
   - **invitations and onboarding** (M13), **per-page annotator/reviewer workflow** (M19), **live
     presence** (M11);
   - **instance settings** (admin only): name, open/closed registration, default role for new
     accounts;
   - **user management** (admin only): create, change role/activation, reset password, delete.

---
