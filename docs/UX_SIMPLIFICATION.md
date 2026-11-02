# Interface simplification — specification

Written 2 November 2026 as a draft for discussion.

## Status

Phases 1–6 are implemented and merged:

- Phase 1 — defaults that work, one name per concept, honest first run: https://github.com/nbadino/tabularium/pull/38
- Phase 2 — the project is the container of the work: https://github.com/nbadino/tabularium/pull/39
- Phase 3 — Pages tab with one next action, Advanced page: https://github.com/nbadino/tabularium/pull/40
- Phase 4 — Studio with one closing action: https://github.com/nbadino/tabularium/pull/41
- Phase 5 — fine-tuning as one path in three steps: https://github.com/nbadino/tabularium/pull/42
- Phase 6 — Models and Settings, no folded guides: https://github.com/nbadino/tabularium/pull/43

The rest of this document describes the problem and the target design as specified. Quoted UI
strings are from the Italian locale, with the English label in parentheses where useful.

## Why

The reviews of 17–23 October ([UX_WORKFLOW_AUDIT](UX_WORKFLOW_AUDIT.md),
[UX_USE_CASE_VERIFICATION](UX_USE_CASE_VERIFICATION.md), [UX_TEAM_WORKSPACE](UX_TEAM_WORKSPACE.md))
cleaned up the individual pages: fewer frames, collapsible sections, chunked loading. The
structure did not change, and that is where the complexity comes from.

A fresh install (0.5.0 on a server without a GPU, 2 November) shows the problem:

- the user lands on **Riconosci** (Recognize) with the warning *"L'endpoint
  http://127.0.0.1:8888/v1 non risponde"* (the endpoint is not responding); every page repeats in
  red *"MonkeyOCRv2-Parsing · Locale · Non disponibile"* (Local · Unavailable), while CPU OCR is
  ready and works;
- recognizing the first page takes about **10 clicks** across four screens: Recognize → New
  project → name → create → Import → file → Recognize → choose project → select all → change
  engine → start → open results;
- an empty project shows a *versioned* Study protocol, Corpus scope, *Domain profile / model
  adapter*, Initial pilot *30–50 balanced pages* and *Approve all* with 0 pages;
- the project is chosen in **11 different selectors**, one per page.

The interface is organized around the system's internal phases (recognition, annotation,
dataset, training, provider). The user thinks in terms of **an archive and its pages**.

## Goal

> Someone who installs Tabularium recognizes their first page in under a minute, without reading
> anything, and finds every advanced feature when they need it, not before.

Success measures, checked in the browser on a fresh install without a GPU:

| Measure | Today | Target |
|---|---:|---:|
| Clicks from "project created" to the first OCR result | ~8 | ≤ 3 |
| Items in the main navigation | 5 + 3 sub-menus | 2 |
| Project selectors | 11 | 1 (in the project header) |
| Errors or red warnings on first run, without a GPU | 2 per page | 0 |
| Visible controls in a project with no pages | ~25 | ≤ 4 |
| Synonyms for the same concept (see glossary) | 4 groups | 0 |

## Principles

1. **The project is the container of everything.** Recognizing, correcting, exporting and
   fine-tuning are actions *on a project*, not sections of the app. The project lives in the URL
   (`/progetti/:id/...`), not in a per-page selector.
2. **The default is what works.** Without a reachable model the engine is local OCR, and no error
   is shown. A model that is not configured is not a fault.
3. **One main action per screen**, chosen by state: with no pages it is *Import*, with
   unrecognized pages it is *Recognize*, with drafts it is *Review*, with everything verified it
   is *Export*.
4. **Show advanced features only when they are needed.** If a feature serves a minority, or is
   only useful after a certain point in the work, it sits behind an entry point with a clear name
   ("Avanzate" (Advanced), "Parametri" (Parameters)), never in view in the basic flow.
5. **The interface explains itself.** No closed "▸ Guida" (Guide) on every page: empty states
   that say what to do, labels in plain language, technical terms only in tooltips and details.
   The documentation stays reachable from a "Help" link in the user menu.
6. **One concept, one name, one place.** Every action has one main place; shortcuts (command
   palette, keyboard) are extra and do not replace the main place.
7. **Teamwork appears when there is a team.** Assignments, review, presence and access all stay,
   but they are shown when the project has more than one member.

What does **not** change: data semantics, the "generated is not verified" rule, confirmations on
destructive operations, export formats, training recipes, the visual system of
[DESIGN.md](../DESIGN.md). This is a revision of structure and copy, not a visual redesign.

## Glossary

One term per concept, in the three languages. The terms on the right disappear from the
interface.

| Concept | Term (IT · EN · FR) | To remove |
|---|---|---|
| Set of pages for one piece of work | **Progetto** · Project · Projet | archivio, corpus, archivio attivo |
| Running the model on several pages | **Riconoscimento** · Recognition · Reconnaissance | elaborazione, sessione, run, coda |
| Running the model on one page in the Studio | **Riconosci pagina** (Recognize page) | prefill, prefill della pagina, inferenza nativa |
| Model output not yet checked | **Bozza** · Draft · Brouillon | — (already used, to be made consistent) |
| Page checked by a person | **Verificata** · Verified · Vérifiée | approvata, confermata (see decision D4) |
| Page editor | **Studio** · Studio · Studio | annotazione, studio di annotazione |
| Box on the page | **Zona** · Region · Zone | blocco, block |
| Adapting the model to one's own pages | **Fine-tuning** · Fine-tuning · Fine-tuning | addestramento, training, refine |
| One fine-tuning session | **Addestramento** · Training run · Entraînement | run |
| Where the model runs | **Esecuzione** (this computer, rented GPU, external service) | destinazione, provider, endpoint |

Region classes (Title, Page-header, Byline…) are to be translated in the interface; the technical
name stays in the tooltip and in the export.

## New structure

### Navigation

```
Header:        Tabularium   [Projects]  [Models]           Search ⌘K   User ▾
                                                                       ├ Settings
                                                                       ├ Users (admin)
                                                                       ├ Help
                                                                       ├ Language: IT EN FR
                                                                       └ Sign out

Project:       ← Projects / Project name ▾      [Pages] [Recognitions] [Fine-tuning]   ⋯
                                                                              ⋯ = Share, Advanced, Delete
```

- **Projects** is the home page (`/`). With only one project, `/` opens that project directly.
- **Models** stays at the top because it is an instance setting, shared by all projects.
- The project name in the header is the only project selector (dropdown menu).
- The project has three tabs. **Fine-tuning** is a primary path (decision D2) and is always
  visible; with no verified pages it shows what is missing instead of an empty form.
- The model status pill in the header leaves the global navigation and becomes a line in the
  Recognitions tab, where it is needed.

### Old → new routes

All old routes remain as redirects, so links and bookmarks keep working.

| Today | After |
|---|---|
| `/` Riconosci (Recognize) | `/progetti/:id/riconoscimenti` |
| `/risultati` | `/progetti/:id/riconoscimenti/:runId` |
| `/archivio` Archivio (`HomePage`) | `/progetti/:id` Pages tab |
| `/progetti` | `/` (project list) |
| `/progetti/:id` (`ProjectDetailPage`) | `/progetti/:id` Pages tab |
| `/annotazione?project=&page=` | `/progetti/:id/studio/:pageId` |
| `/tabella/:blockId` | `/progetti/:id/studio/:pageId/tabella/:blockId` |
| `/fine-tuning`, `/dataset`, `/training`, `/valutazione` | `/progetti/:id/fine-tuning` (one stepped path) |
| `/benchmark` | `/progetti/:id/fine-tuning/confronta` |
| `/playground` Prova modello (Try model) | removed: *Try on a page* in the Models page |
| `/modelli` | `/modelli` |
| `/impostazioni`, `/utenti` | unchanged |

## Screen by screen

For each screen: what stays in view, what goes behind an entry point, what disappears.

### First run, sign-in

| Stays | Changes |
|---|---|
| Setup with username, password, confirmation | No flash of the login page while the state loads |
| Login with optional OIDC | If no admin exists yet, login leads to setup instead of answering "wrong credentials" |
| | Email: text *"Facoltativa, usata per recuperi e notifiche"* (Optional, used for recovery and notifications) → *"Facoltativa"* (Optional). No email sending exists; do not promise recovery |
| | Below the button: *"Conserva questa password: per reimpostarla serve l'accesso al server"* (Keep this password: resetting it requires access to the server), together with a `tabularium reset-password` command (backend, outside this spec) |
| | A tab left on setup after the admin was created elsewhere refreshes and switches to login |

### Projects (`/`)

- **No projects**: a single central area, *"Create your first project"*, with name and a file
  drop zone **on the same screen**. Creating the project and uploading pages is one step, not
  two.
- **With projects**: list with name, number of pages, progress (verified / total), date of last
  change. One main button, *New project*.
- The filters *All / Owned by me / Shared with me* appear only if the user has at least one
  shared project.
- "From a folder on the server" stays as the second option of the creation form, for admins only.
- Disappears: the "People and access" button on every row (reachable from the project).

### Project → Pages (initial tab)

Merges `HomePage` (thumbnail wall) and `ProjectDetailPage` (register). Today they are two
overlapping screens.

- **In view**: page thumbnails with status (new, draft, verified), status filter, search (above
  20 pages), a **main action bar** that follows principle 3:
  - 0 pages → drop zone *Add images or PDFs*;
  - unrecognized pages → *Recognize N pages* (with local OCR or the configured model, in one
    click, without going through the Recognitions tab);
  - drafts present → *Review N drafts* (opens the Studio on the first one);
  - everything verified → *Export*.
- **Table view** (thumbnails ↔ rows toggle): the register table with editable metadata (date,
  number, type) and multi-page editing. It is the view for people who curate metadata, not the
  initial one.
- **Project ⋯ menu**: Share · Advanced · Delete project.
- **Advanced** (dedicated project page, `/progetti/:id/avanzate`): Study protocol, Initial pilot,
  protected test pages (gold), region classes, transcription rules, table schema, recent
  activity, source folder on the server and rescan.
- Disappears from the basic view: Study protocol, Pilot, Recent activity, the absolute folder
  path, "Also confirm drafts", "Approve all" (stays in the table view, with confirmation).

### Project → Recognitions

Merges `RecognizePage` and `ResultsPage`.

- **At the top, one line**: *Engine: local OCR · Ready* or *Model: MinerU2.5 on Modal · Ready*,
  with a *Change* link. If a model is configured but does not respond: *The model is not
  responding. You can use local OCR or check in Models*. Never the endpoint URL.
- **New recognition**: which pages (*Not yet recognized (N)*, preselected; *All*; *Choose…* opens
  the selection grid), engine, *Start*. The grid of 100 thumbnails is no longer in view by
  default.
- **List of recognitions**: date, engine, pages done / total, status. Opening one shows progress,
  pages with problems, *Review in the Studio*, *Export* (format + original or corrected), *Retry
  failed pages*.
- "Shut down the model when finished" becomes an option inside *Start* (admin only, only for a
  rented model), and the duplicate "Shut down model" button disappears from the results.
- The "Replace drafts" confirmation box stays: it protects the work.

### Studio

The most used and most crowded page (`AnnotationPage.tsx`, 1,601 lines). Goal: the user sees the
page, the regions and the text; everything else opens when needed.

- **Header**: ← Pages · page name · *Page 3 of 40* · a single closing action **Verify and go to
  next** (Enter). Today closing a page means choosing between "Approve", "Next page", "Send to
  reviewer" and the status menu.
- **Toolbar**: Select · Rectangle · Polygon · Pan (with the letters V R P H), Undo, *Recognize
  page*. The class selector appears only while drawing or with a region selected. "New class",
  "Reading order", "Redo", "Save", "Start over" stay in the ⋯ menu (Save is automatic: the menu
  only shows the state).
- **Page sidebar**: closed by default on screens below 1440 px; the arrows and the J/K shortcut
  are enough to move through pages. The project selector disappears (the project is in the URL).
- **Content panel**: regions in reading order, editable text, *Verified* per region. The
  "Options" menu (Renumber, Rules, Classes, Table schema) moves into ⋯; Classes, Rules and Table
  schema are the same pages as the project's *Advanced*.
- **"View" selector** (All / Page only / Text only): becomes two resize buttons on the divider,
  as in a code editor.
- **Teamwork** (Assign, Send to reviewer, Request changes, "To review" queue): appears only if the
  project has more than one member (principle 7).
- **Recognize page** (today "Prefill della pagina", page prefill): engine preselected as in the
  Recognitions tab; the mode is *Add to existing regions* by default, *Replace drafts* and
  *Replace everything* stay with the current confirmation.
- **Table**: stays the current editor (subject of [UX_STUDIO_REVIEW](UX_STUDIO_REVIEW.md)); in
  this phase only the route and the labelled back button are aligned.

### Project → Fine-tuning

A primary path (decision D2), but presented as **one path in three steps**, not four pages with
their own selectors.

```
1 Prepare the data  →  2 Train  →  3 Evaluate
```

- At the top, a single **Next step** card with the status and a button (it already exists in
  `FineTuningPage`; it becomes the header of the whole path).
- **Prepare the data**: *Prepare the dataset* with the defaults (80 % training, split by page,
  verified pages only). In view: how many pages and regions go in, a warning if there are too
  few. Behind *Parameters*: ratio, split unit, seed, adapter, pilot sample only, formats for other
  models, preview of the generated files.
- **Train**: GPU choice in plain language (*This computer* if it has a suitable GPU, *Rented GPU
  (Vast.ai)* with an hourly budget, *A server of mine (SSH)*), base model, *Start*. Type (LoRA /
  full), epochs and learning rate behind *Parameters*, the 15 advanced parameters inside that.
  During training: chart, estimated time, cost, *Stop*; the log opens on request (as today).
- **Evaluate**: three numbers in plain language at the top (*Text: 2.1 % wrong characters*,
  *Regions found: 94 %*, *Tables correct: 81 %*), with the comparison against the base model.
  Precision, recall, IoU, TEDS, WER and the per-class table sit under *Details*.
- **Compare models** (today Benchmark): a section inside Evaluate. URLs and served names are
  chosen from the configured models, not typed.
- What disappears: the project selectors on Dataset, Training, Evaluation, Benchmark; the
  "Overview" tab (it becomes the Next step card); the duplicate "Stop the run" button.

### Models

- **In use now**: model name, where it runs, status, *Change*, *Try on a page* (replaces the Try
  model page).
- **Choose a model** (step 1): search and tabs stay; the *Run on this computer* filter is on by
  default when there is at least one. "Add a model from Hugging Face" becomes a link at the bottom
  of the list, not an always-open form.
- **Where to run it** (step 2): first the options possible on this machine, then the others. An
  impossible option (today *Local* is selected but "This machine cannot serve models locally") is
  never preselected; it goes to the bottom, greyed out, with the reason in one line.
  Descriptions without jargon:
  - *This computer* — "Uses this machine's GPU."
  - *Modal* — "You pay only for the seconds the model is working. Requires a Modal account."
  - *Vast.ai* — "You rent a GPU by the hour. Requires a Vast.ai account with credit."
  - *RunPod* — "You rent a GPU on RunPod. Requires a RunPod account."
  - *A server you already have* — "Enter the address of a compatible server."
  In the details (tooltip): vLLM, SGLang, SSH tunnel, HTTPS proxy.
- **Configure** (step 3): logic unchanged; model parameters currently in *Settings → Models*
  (presets, vLLM/MLX overrides) move here under *Model parameters*.
- The 1-2-3 indicator stays only during configuration, not when a model is already in use.

### Settings

- **Account**: name, role, email (once only, editable: today it appears twice), *Change
  password*. Language leaves this page: it stays in the user menu.
- **Instance** (admin): name, default role, registration, institutional sign-in (OIDC).
- **Data and backup** (admin): unchanged.
- **Environment**: unchanged, at the bottom.
- The **Models** tab disappears (see above). The "Manage users" link stays; Users is also in the
  user menu.

### Header and command palette

- Logo and tab title: **Tabularium** everywhere. Today the logo says "TabulariumLab" and the title
  changes from "Fine-tuning MonkeyOCRv2" (`index.html`) to "Riconoscimento documenti" (Document
  recognition).
- Command palette (⌘K): stays, with projects, pages and actions for the current project. Language
  and Sign out leave it (they are in the user menu); Compare models, missing today, is added.

## What is removed from the code

| Item | Reason |
|---|---|
| `HomePage.tsx` (`/archivio`) | absorbed by the Pages tab |
| `PlaygroundPage.tsx` | replaced by *Try on a page* in Models |
| `PageHeader` with `<details>` "Guida" (Guide) | replaced by empty states and inline text (principle 5) |
| 11 project selectors and the "project with the most pages" logic in `activeProject.ts` | the project is in the URL |
| `studio/components/ClassPalette.tsx` | unused |
| i18n keys `home.next*`, `home.env*`, `settings.tabCompute` | unused |
| model pill in the global header | moved to the Recognitions tab |
| Settings → Models tab | moved to Models |

No backend route is removed.

## Phases

Each phase is an independent PR, checked in the browser on a fresh install and on a project with
real data, with screenshots in the description. All six phases are implemented and merged.

1. **Defaults and copy** (small, no structural change) — implemented in
   [#38](https://github.com/nbadino/tabularium/pull/38)
   - default engine = local OCR when no model is reachable, no red warning on first run;
   - the name "Tabularium" everywhere, page title;
   - glossary applied to the strings in `i18n/*.ts`, classes translated;
   - first run: login flash, email text, login without admin → setup;
   - removal of dead code (table above).
2. **Project at the centre**: new routes with redirects, project header with its own selector,
   Pages and Recognitions tabs, removal of the per-page selectors and of `HomePage` —
   implemented in [#39](https://github.com/nbadino/tabularium/pull/39).
3. **Pages and Advanced**: main action bar, creation + upload in one step, table view, the
   project's Advanced page — implemented in [#40](https://github.com/nbadino/tabularium/pull/40).
4. **Studio**: single closing action, reduced toolbar, conditional teamwork, collapsible
   sidebar — implemented in [#41](https://github.com/nbadino/tabularium/pull/41).
5. **Stepped fine-tuning**: one path, parameters behind *Parameters*, evaluation in plain
   language, Compare models inside Evaluate — implemented in
   [#42](https://github.com/nbadino/tabularium/pull/42).
6. **Models and Settings**: order of the options, copy, Try on a page, moving the model
   parameters, Settings cleanup — implemented in
   [#43](https://github.com/nbadino/tabularium/pull/43).

Phases 1 and 6 do not depend on the others. Phase 2 is the prerequisite for 3, 4 and 5.

## Decisions

D2 is decided. The other questions have not been formally decided; the right-hand column is the
proposal written in the draft.

| # | Question | Status | Outcome or proposal |
|---|---|---|---|
| D1 | Main user: single researcher or team? | Open | Proposal: single; teamwork appears with more than one member |
| D2 | Weight of fine-tuning | **Decided** by the owner, 2 November 2026 | **Primary path**: a tab always visible in the project |
| D3 | Protocol, pilot, gold set, activity, benchmark: keep, hide, remove? | Open | Proposal: hide in the project's *Advanced*; benchmark inside Evaluate |
| D4 | Are "Verified" and "approved" two different states (region verified, page approved)? | Open | Proposal: unify as *Verified* for the single user; *Approved* only in the team review flow |
| D5 | With only one project, does `/` open the project directly? | Open | Proposal: yes |
| D6 | Default interface language | Open | Proposal: the browser's language, English if not available (today Italian) |
| D7 | Does the register's table view stay in the Pages tab or move to Advanced? | Open | Proposal: it stays, as a toggle |
