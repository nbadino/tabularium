# Tabularium Frontend UX & Workflow Audit Track

**Status:** Resolved (99/99 Fixed)  
**Audit Date:** 2026-11-22  
**Commit SHA:** `5216840` (`frontend_ux_audit_tabularium`)  
**Target Instance:** `http://127.0.0.1:8789` (isolated `TABULARIUM_ROOT` in `./data`, `TABULARIUM_AUTH=on`)  
**Viewports Tested:** Desktop (`1440×900`), Medium Laptop (`1100×760`), Mobile/Narrow (`390×844`)  
**Locales Tested:** `it` (default), `en`, `fr`  
**Test Accounts Created:**
- `audit_admin` (`admin`, email: `admin@audit.local`) — created via first-run `SetupPage`
- `audit_editor` (`editor`) — invited via `/invito` link (Stage G), tested on shared Project 1 & Studio review submission
- `audit_viewer` (`viewer`) — created directly with `must_change_password` (Stage G), tested `SetPasswordPage`, `ProjectReader`, and read-only enforcement

---

## Summary Dashboard

### Counts by Severity
| Severity | Meaning | Open | Fixed | Total |
|---|---|---:|---:|---:|
| **P0** | Blocker / broken flow / JS crash / data loss risk | 0 | 1 | 1 |
| **P1** | Major friction, confusing UX, unclear critical action, or broken layout | 0 | 37 | 37 |
| **P2** | Moderate clarity issue, redundancy, missing affordance/tooltip, minor layout glitch | 0 | 53 | 53 |
| **P3** | Polish, microcopy nuance, minor visual alignment or convenience improvement | 0 | 8 | 8 |
| **Total** | | **0** | **99** | **99** |

### Counts by Category
| Category | Count |
|---|---:|
| `Bug/Error` | 29 |
| `Layout` | 18 |
| `Redundancy` | 7 |
| `Tedious/Friction` | 13 |
| `Unclear Button/Label` | 20 |
| `i18n/Copy` | 12 |

---

## Workflow Coverage Matrix

| Stage | Workflow Area | Primary Routes | Status | Notes |
|---|---|---|---|---|
| **A** | First-Run Setup, Auth, Shell & Global Nav | `/`, `/progetti`, `/nonexistent-route` | Completed | Tested SetupPage validation, `/progetti` empty state, CommandPalette, AccountMenu, IT/EN/FR locales, LoginPage, 404 |
| **B** | Projects, Archive, Import, Collections, Reader, Advanced | `/progetti`, `/progetti/:id`, `/progetti/:id/avanzate` | Completed | Tested browser/server projects, PNG/PDF/ZIP imports, collections, Miniature vs Tabella views, ShareDialog, Advanced |
| **C** | Inference / Recognition, Results, Export & Playground | `/progetti/:id/riconoscimenti`, `/progetti/:id/riconoscimenti/:runId`, `/playground` | Completed | Tested RapidOCR Runs #1 & #2, `ActiveRunsIndicator`, ResultsPage, all 6 exports (`.pdf`/`.md`/`.txt`/`.csv`/`.json`/raw `.txt`), `/modelli`, `/playground` |
| **D** | Annotation Studio & Review Workflow | `/progetti/:id/studio` | Completed | Tested layout, tools, `Vista`, reading order/`Rinumera`, block editing/drawing, Undo/Redo, `Opzioni`, `Altro`, `PageWorkControl` |
| **E** | Table Workspace (Spreadsheet, Structure, Guard) | `/tabella/:blockId` | Completed | Tested `PrefillDialog`, `ConventionsChecklist`, modal vs standalone `/tabella/:blockId`, `UniverSheet`, `Struttura`, `TableGuard`/checks, Page 1 approval |
| **F** | Fine-Tuning Pipeline (Overview, Dataset, Train, Eval, Benchmark) | `/progetti/:id/fine-tuning/*` | Completed | Tested Overview, Dataset (1-page & 2-page 50/50 split), Training (`?view=new`), Evaluation endpoint check, Benchmark (`Classifica`) |
| **G** | Models Hub, Settings, Users & Multi-Role Testing | `/modelli`, `/impostazioni`, `/utenti`, `/invito` | Completed | Tested `/modelli` (catalog, OCRia-1, cloud panels, manual), `/impostazioni` (all 4 tabs), `/utenti` + invites, `ShareDialog`, `audit_editor` & `audit_viewer` (`SetPasswordPage` + `ProjectReader`) |
| **H** | Responsive & Multi-Locale Pass | Key routes at `1100×760`, `390×844`; `IT`/`EN`/`FR` | Completed | Tested collaborative review flow (`Editor` submit -> `Admin` revision request -> re-submit -> approve), `1100×760` & `390×844` viewports, `EN` & `FR` locales |

---

## Detailed Findings Registry

### Stage A — First-Run Setup, Auth, Shell & Global Navigation

#### [UX-001] Blank white screen flash after Logout
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `AuthGate` (`/` / `/progetti` after clicking `Esci` / `Sign out` in `AccountMenu`)
- **Observation:** After `POST /api/auth/logout` returns HTTP 200, `#root` renders completely empty (white screen) while `LoginPage` lazy chunk loads (`Suspense fallback={null}`).
- **User Impact:** Users clicking Sign out briefly see a dead white screen, making it look like the app crashed or hung.
- **Proposed Improvement:** Provide a styled loading fallback (matching `auth.loading`) on `AuthGate`'s `<Suspense>` boundaries instead of `fallback={null}`.

#### [UX-002] Disabled `Accedi` button on LoginPage is bypassed by pressing Enter and has poor contrast
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `LoginPage` (`Accedi` / `Sign in` submit button)
- **Observation:** When username or password is empty, the submit button is `disabled` with faint white-on-light-gray contrast, yet pressing `Enter` in either input still submits the `<form>` and reveals the validation alert `Compila nome utente e password.`. Conversely, `SetupPage` keeps its submit button enabled on empty submit.
- **User Impact:** Visually contradicts form behavior (keyboard works while the button looks locked), yields low contrast, and creates inconsistent behavior across auth screens.
- **Proposed Improvement:** Keep the primary submit button enabled (matching `SetupPage`) and show clear inline field/form validation on submit instead of disabling the button.

#### [UX-003] SetupPage autofocuses Username instead of the first required field (`Setup token`) and omits token from blank-submit error
- **Severity:** P1 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `SetupPage` (`Setup token` & `Username` inputs, error banner)
- **Observation:** When opening `/` without `?setup_token=...` in the URL, `Username` steals `autoFocus` even though `Setup token` sits above it and is mandatory. Submitting an empty form displays `Enter username and password.` (`Compila nome utente e password.`), omitting the missing setup token.
- **User Impact:** First-time admins type username/password, hit submit, and only then discover `Setup token` was required, or get an error message that does not mention the token field.
- **Proposed Improvement:** Autofocus `Setup token` when empty (otherwise `Username`), and tailor empty-field validation messages to name the exact missing fields (including Setup token).

#### [UX-004] Raw lowercase backend error strings shown verbatim in auth forms (unlocalized & unpunctuated)
- **Severity:** P1 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `SetupPage` (short password HTTP 422), `LoginPage` (invalid credentials HTTP 401)
- **Observation:** Short password (`123`) waits for a server round-trip (`POST /api/auth/setup` 422) and renders raw lowercase `password too short (minimum 8 characters)` even in IT/FR or next to punctuated client-side alerts. Invalid login (`POST /api/auth/login` 401) displays raw lowercase Italian `credenziali non valide` regardless of active UI locale.
- **User Impact:** Users see inconsistent, developer-style error text, and non-Italian users see Italian backend text (`credenziali non valide`) on an English/French login flow.
- **Proposed Improvement:** Map auth error codes/HTTP statuses via frontend i18n keys with proper capitalization and punctuation, and validate minimum password length (`>= 8`) client-side before submission.

#### [UX-005] CommandPalette exposes project-scoped destinations when 0 projects exist, and `Ctrl+K` does not open it
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** Global Header (`Cerca ⌘K` / `Search ⌘K` button & keyboard shortcut)
- **Observation:** In an empty instance with 0 projects, opening CommandPalette lists 13 destinations including `Pages`, `Recognize`, `Results`, `Annotate`, `Fine-tuning`, `Dataset`, and `Evaluation`. Also, pressing `Ctrl+K` does not trigger the palette (only `Meta+K` works).
- **User Impact:** First-time users see dead-end project workflows before creating any project; Windows/Linux users or `Ctrl+K` muscle-memory users cannot open the palette via keyboard.
- **Proposed Improvement:** Hide or disable project-scoped items when no project exists (or label them as requiring a project), and support both `Meta+K` and `Ctrl+K`.

#### [UX-006] No language selector on `SetupPage` or `LoginPage`
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `SetupPage`, `LoginPage`
- **Observation:** `LocaleSwitch` (`IT · EN · FR`) only lives inside the authenticated `AccountMenu`. Before login or during initial setup, users cannot change the interface language.
- **User Impact:** Non-Italian researchers accessing a freshly deployed instance (or a browser persisted in another user's locale) must complete auth before switching to their preferred language.
- **Proposed Improvement:** Add a compact `IT · EN · FR` selector in the top-right corner of auth cards (`SetupPage`, `LoginPage`, `AcceptInvitePage`, `SetPasswordPage`).

#### [UX-007] Missing password visibility toggle on `SetupPage` and `LoginPage`
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `SetupPage` (`Password`, `Confirm password`), `LoginPage` (`Password`)
- **Observation:** Password inputs lack a show/hide toggle icon.
- **User Impact:** Since `SetupPage` explicitly warns that Tabularium sends no recovery emails, mistyping an initial admin password or complex team password is unnecessarily error-prone.
- **Proposed Improvement:** Add an accessible show/hide toggle button inside password fields.

#### [UX-008] No password-recovery or account-access guidance on `LoginPage`
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `LoginPage`
- **Observation:** Unlike `SetupPage` (which notes recovery limits), `LoginPage` gives no hint about what to do if a user forgets their password (contact instance administrator, or CLI `tabularium reset-password`) or needs an account when public registration is closed.
- **User Impact:** Locked-out researchers or new lab members hit a dead end without knowing how to regain access.
- **Proposed Improvement:** Add a subtle helper line below the form (e.g., *"Need an account or forgot your password? Contact your instance administrator (or run `tabularium reset-password` on the server)."*).

#### [UX-009] Duplicate `+ New project` / `New project` primary buttons and idle search bar on empty `/progetti`
- **Severity:** P2 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti` (`ProjectsPage` empty state)
- **Observation:** When `0 projects` exist, two identical primary red CTA buttons (`+ Nuovo progetto` top-right and `Nuovo progetto` in the empty card) appear simultaneously, alongside a `Cerca un progetto` search input over an empty list.
- **User Impact:** Visual clutter and redundant calls to action on the very first screen a user lands on.
- **Proposed Improvement:** Hide the search input (and optionally the top-right `+ Nuovo progetto` button) when the project count is 0, keeping one clear primary onboarding CTA.

#### [UX-010] `Tabularium` brand title in Row 1 header is not clickable
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** Global Header (`Layout.tsx` Row 1 `<span>Tabularium</span>`)
- **Observation:** Clicking the `Tabularium` brand name does nothing; it is static text rather than a link to `/` / `/progetti`.
- **User Impact:** Breaks standard web convention where clicking the app name/logo returns home from deep routes.
- **Proposed Improvement:** Make the brand wordmark a `<Link to="/progetti">` with subtle hover styling.

#### [UX-011] External `Help` / `Aiuto` link in `AccountMenu` gives no external-tab indicator
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `AccountMenu` (`Aiuto` / `Help` / `Aide` item)
- **Observation:** Opens `https://nbadino.github.io/tabularium/` in a new browser tab (`target="_blank"`), visually styled identically to internal app routes (`Utenti`, `Impostazioni`).
- **User Impact:** Users expect an in-app help modal/page and may be surprised by an external site opening in a new tab.
- **Proposed Improvement:** Append an external-link arrow icon (`↗`) and `title` noting it opens the online documentation in a new tab.

#### [UX-012] Inverted heading hierarchy and developer jargon on `NotFoundPage`
- **Severity:** P2 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `NotFoundPage` (`*` catch-all route, tested at `/nonexistent-route`)
- **Observation:** The eyebrow `<h2>Rotta inesistente</h2>` (`Route not found`) precedes `<h1>Questa pagina non esiste</h1>` in DOM order, and "Rotta" / "Route" is technical developer terminology.
- **User Impact:** Minor accessibility hierarchy defect for screen readers and overly technical phrasing for archival researchers.
- **Proposed Improvement:** Render the eyebrow as a `<p>` or `<span>` rather than `<h2>`, and soften the label to *"Pagina non trovata"* / *"Page not found"*.

#### [UX-013] Sparse top-left layout on desktop empty `/progetti` and `NotFoundPage`
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti` (0 projects), `/nonexistent-route` at `1440×900`
- **Observation:** Both views hug the top-left edge inside a wide workspace container, leaving >70% blank white canvas on a standard desktop screen.
- **User Impact:** Looks unfinished on first arrival after onboarding.
- **Proposed Improvement:** Center or balance the empty-state welcome card with a concise 3-step quickstart summary (Import scans → Recognize → Review & Export).

#### [UX-014] Mixed English terms in IT/FR UI (`Admin`, `Fine-tuning`, `Dataset`) and hardcoded Italian URL slugs
- **Severity:** P3 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `AccountMenu` role subtitle, `CommandPalette` entries, browser URLs
- **Observation:** `AccountMenu` displays `Admin` in IT/FR instead of a localized label (`Amministratore`); CommandPalette keeps `Fine-tuning` and `Dataset` untranslated in IT/FR; URL routes remain Italian (`/progetti`, `/modelli`, `/impostazioni`, `/utenti`) in all languages.
- **User Impact:** Slight language inconsistency when switching locales (URLs are mostly cosmetic, whereas `Admin` in AccountMenu is directly user-facing).
- **Proposed Improvement:** Localize the admin role badge (`Amministratore` / `Administrateur`) and consider localized parenthetical/clear explanations for `Dataset` / `Fine-tuning` in IT/FR.

#### [UX-015] Duplicate `/progetti` page appearance of `Nuovo progetto` vs `+ Nuovo progetto` copy difference
- **Severity:** P3 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti` header CTA vs empty state CTA
- **Observation:** Header button reads `+ Nuovo progetto` while empty-state button reads `Nuovo progetto` (without `+`), adding slight microcopy inconsistency alongside the duplicate button (see UX-009).
- **User Impact:** Minor visual/copy polish issue.
- **Proposed Improvement:** Remove the duplicate button on empty state (UX-009) or align the label consistently.

#### [UX-016] `LoginPage` disabled button uses same primary color wash with low text contrast
- **Severity:** P3 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `LoginPage` (`Accedi` when disabled)
- **Observation:** When disabled, white text over desaturated light gray/pinkish background fails visual clarity at a glance.
- **User Impact:** Button label is hard to read in its initial empty state (also mitigated by UX-002).
- **Proposed Improvement:** Keep submit enabled or give disabled buttons high-contrast muted surface + darker ink (`ink-3` on `fill-2`).

#### [UX-017] `ProjectsPage` English empty-state description lacks domain context ("images, results and corrections")
- **Severity:** P3 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti` empty state (`Create a project to collect images, results and corrections.`)
- **Observation:** For a brand-new user who has never run OCR yet, "results and corrections" is vague compared to "scanned pages, OCR transcriptions, and corrections".
- **User Impact:** Minor onboarding clarity gap.
- **Proposed Improvement:** Clarify to *"Create a project to organize scanned pages, OCR recognition runs, and your reviewed transcriptions."*

---

### Stage B — Projects, Archive, Import Modes, Collections, Reader & Project Advanced

#### [UX-018] `Tabella` view overflows horizontally at 1440×900 with Collections sidebar, clipping `Tipo`, `Stato`, and `Approva`
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id?vista=tabella` (`ProjectDetailPage` table view)
- **Observation:** With the Collections sidebar expanded on a 1440×900 desktop viewport, the 9-column table (`Checkbox`, `Pag.`, `File`, `Data`, `Annata / n.`, `N.`, `Tipo`, `Stato`, `Azione`) overflows horizontally: `Tipo` `<select>` is clipped on the right edge, and `Stato` + `Approva` sit offscreen. Horizontal scrolling hides the left identification columns while keeping `Approva` clipped.
- **User Impact:** Primary review actions (`Stato`, `Approva`) are unreachable or awkward in `Tabella` view on standard laptop/desktop screens.
- **Proposed Improvement:** Compact table column widths, hide/merge secondary metadata columns (`Data`, `Annata / n.`, `N.`) behind responsive columns or inline pills, or allow collapsing the Collections sidebar.

#### [UX-019] Pages cannot be opened from `Tabella` view
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id?vista=tabella` (thumbnail `<img>` and filename `<span>` in table rows)
- **Observation:** In `Tabella` view, neither the row thumbnail nor the filename is a `<Link>` or clickable action; clicking them does nothing. Users must switch back to `Miniature` view just to open a page in Studio.
- **User Impact:** Breaks the basic archive-to-editor workflow for researchers using table view to filter and inspect metadata.
- **Proposed Improvement:** Link both the thumbnail and filename directly to `/progetti/:id/studio?page=<pageId>` (and add a clear `Apri` / `Studio` action if helpful).

#### [UX-020] No single or bulk page deletion action in Project Archive
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (Miniature cards, Tabella rows, and Bulk Selection bar)
- **Observation:** Neither individual page cards/rows nor the bulk selection toolbar provide any action to remove/delete an uploaded or duplicate page from a project (only collection deletion and whole-project deletion exist).
- **User Impact:** Users who upload a wrong scan or test page cannot remove it without deleting the entire project.
- **Proposed Improvement:** Add a guarded delete action (single row/card menu + bulk `Rimuovi pagine` with explicit confirmation per R9).

#### [UX-021] Premature red validation error on `Aggiungi classe` before typing
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/avanzate` (`Classi` / `Tassonomia` -> `Aggiungi classe` inline form)
- **Observation:** Immediately upon clicking `Aggiungi classe`, before any user keystroke or blur, the untouched `Nome` input displays a red validation error: `"Nome non valido: lettera iniziale, poi lettere, cifre, spazio, . _ - (max 40)."`.
- **User Impact:** Looks like the form broke immediately on open instead of guiding the user gently.
- **Proposed Improvement:** Validate only after field interaction (`touched`/dirty) or submit attempt; display formatting rules as a neutral hint beforehand.

#### [UX-022] PDF warning exposes internal filename prefix, CLI `pip` command, and wrong server-folder instructions
- **Severity:** P1 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Referto di scansione` banner after uploading `sample_multipage.pdf`)
- **Observation:** Uploading a PDF without `pypdfium2` displays `"000000_sample_multipage.pdf: PDF richiede pypdfium2 (pip install pypdfium2)"` (with `pip install pypdfium2` wrapping awkwardly mid-word) and advises `"Cosa fare: Correggili nella cartella sorgente e riscansiona."` even inside a browser-upload project that has no server folder.
- **User Impact:** Exposes internal storage prefix (`000000_`), gives terminal commands to non-technical historians, and gives impossible remediation instructions for browser uploads.
- **Proposed Improvement:** Strip internal numeric prefixes in displayed filenames, replace raw Python instructions with user-friendly administrator guidance, and adapt `"Cosa fare"` based on project provenance (`browser` vs `server folder`).

#### [UX-023] Miniature cards severely truncate filenames (`Volume_19...`), overlap text with selection checkboxes, and lack card spacing
- **Severity:** P1 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Miniature` grid with Collections sidebar open; confirmed in host screenshot)
- **Observation:** Because 4 dense cards sit side-by-side with zero horizontal gap and wide `"Nuova"` status badges, filenames truncate to ~9 characters (`"000000_sh..."`, `"Volume_19..."`), making `Volume_1912/folio_01.png` and `Volume_1913/folio_02.png` visually identical. Furthermore, the top-left selection checkbox overlays directly onto the scanned image corner, obscuring document titles (`"AN"`, `"NO"`, `"VO"`).
- **User Impact:** Researchers cannot tell pages apart without hovering/opening each one, and the grid looks visually crowded.
- **Proposed Improvement:** Add card grid gaps, display clean relative/short filenames without `000000_` prefixes (e.g., `folio_01.png` or tooltip + 2-line wrap), move status badge or shorten it, and give the checkbox a subtle translucent pill/margin outside the document corner.

#### [UX-024] Server-folder project (`/progetti/2`) still leads with browser-upload dropzone and hides its source path
- **Severity:** P2 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (empty state for a project created with `"Da una cartella sul server"`)
- **Observation:** After explicitly selecting server-folder provenance during project creation, `/progetti/2` opens to the browser drag-and-drop upload zone (`"Scegli i file"`, `"Importa cartella"`), relegating `"Registra pagine dalla cartella"` to a third secondary button and omitting the configured `Cartella sorgente` path.
- **User Impact:** Confuses users who already configured a server path and now see a redundant browser-upload workflow prioritized over scanning.
- **Proposed Improvement:** For server-folder projects, show the configured folder path clearly and promote `Registra pagine dalla cartella` as the primary CTA (keeping browser upload secondary).

#### [UX-025] Page search and status filtering missing from `Miniature` view (status filter absent everywhere)
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Miniature` and `Tabella` toolbar)
- **Observation:** The `Filtra pagine` search input only appears in `Tabella` view, and neither `Miniature` nor `Tabella` provides status filters (e.g., `Nuova`, `Riconosciuta`, `Da rivedere`, `Approvata`).
- **User Impact:** Researchers with hundreds of pages in thumbnail grid cannot search by filename or quickly isolate unapproved/recognized pages without switching views.
- **Proposed Improvement:** Place search and status filter controls in the shared archive toolbar above both `Miniature` and `Tabella`.

#### [UX-026] `/progetti/:id/avanzate` is hard to discover, lacks breadcrumb return, and mixes save patterns
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Altre azioni` menu) and `/progetti/:id/avanzate` (`ProjectAdvancedPage`)
- **Observation:** `Avanzate` is buried in `Altre azioni` (and is the only menu item lacking an icon). Inside `/progetti/:id/avanzate`, `ProjectNav` leaves `Pagine` underlined with no `"Torna alle pagine"` breadcrumb, `Elimina progetto` lives on `/progetti/:id` rather than in Advanced danger zone, and save UX varies wildly (`Regole di trascrizione` auto-saves checkboxes vs separate `Salva classi`, `Salva`, `Salva protocollo` buttons).
- **User Impact:** Users struggle to find project settings or navigate back, and don't know whether changes are auto-saved or require clicking a section-specific Save button.
- **Proposed Improvement:** Promote `Impostazioni progetto` / `Avanzate` cleanly (with icon and back breadcrumb), unify or clarify save feedback, and consolidate destructive `Elimina progetto` in an explicit Danger Zone (or clearly separated menu item).

#### [UX-027] Multi-line wrapping distorts `<select>` layout and lacks accessible label in `Protocollo studio · v1`
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/avanzate` (`Protocollo studio · v1`)
- **Observation:** The domain profile `"Shipping registers — 20th-century maritime newspapers"` wraps to 3 lines inside its `<select>`, stretching the adjacent `MonkeyOCRv2-Parsing` `<select>` vertically with dead whitespace; that second `<select>` also lacks an accessible `<label>`/`aria-label`, and `Salva protocollo` floats in the center below `Al`.
- **User Impact:** Visual misalignment and missing screen-reader label on the model adapter selector.
- **Proposed Improvement:** Fix `Protocollo studio` grid alignment (`items-start` / fixed single-line control height), add an explicit label to the adapter `<select>`, and left- or right-align `Salva protocollo`.

#### [UX-028] ZIP support omitted from upload triggers; ZIP modal lacks folder checkboxes and has circular phrasing
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `Nuovo progetto` modal, `Aggiungi pagine` menu, `Importa <archive>.zip` modal
- **Observation:** `Nuovo progetto` mentions `"immagini o i PDF"` and `Aggiungi pagine` says `"Importa immagini o PDF"` / `"Importa cartella"` without mentioning `.zip`. In the ZIP import dialog, subfolders (`Volume_1912`, `Volume_1913`) are plain text with no checkboxes to skip folders, and the option reads `"Una collezione per cartella: le sottocartelle restano nella loro cartella"` (circular wording).
- **User Impact:** Users don't know ZIPs are supported from the menu, cannot exclude sub-folders on import, and must guess what the subfolder rule means.
- **Proposed Improvement:** Mention `ZIP` in upload copy (`"immagini, PDF o ZIP"`), support per-folder toggles, and clarify wording (e.g. `"Crea una collezione per ogni cartella di primo livello (mantiene i file delle sottocartelle uniti)"`).

#### [UX-029] Modals focus `[X]` close button on open; `Nuova collezione` duplicates title as submit label; raw hex color field
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `Importa sample_archive.zip`, `Nuova collezione`, and `Aggiungi classe` (`/progetti/:id/avanzate`)
- **Observation:** Opening `Importa sample_archive.zip` or `Nuova collezione` puts keyboard focus on the top-right `[X]` close button instead of the text input (`Nome`) or primary button. In `Nuova collezione`, both header and CTA say `"Nuova collezione"`. In `Aggiungi classe`, `Colore` requires typing a hex code (`#4a5560`) rather than a color picker/swatch.
- **User Impact:** Pressing `Enter` right away can inadvertently close a modal; repetitive label; tedious manual hex typing.
- **Proposed Improvement:** Focus the first input/CTA on modal open, rename submit CTA to `"Crea collezione"`, and add `<input type="color">` or preset swatches alongside hex input.

#### [UX-030] `Genera campione` enabled with 4 pages despite 30–50 recommendation; contradictory `Schema tabella` warning
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/avanzate` (`Pilot iniziale` and `Schema tabella`)
- **Observation:** `Pilot iniziale` explains `"30–50 pagine bilanciate"` while `"Genera campione"` stays active on a 4-page archive. `Schema tabella` displays `"Il modello attivo (MonkeyOCRv2-Parsing) usa un prompt di protocollo e ignora l’hint testuale"` while project status says `"Motore: OCR locale."` and the textarea remains editable.
- **User Impact:** Mixed signals about whether actions will work or be ignored.
- **Proposed Improvement:** Show an inline note when `< 30` pages exist (or disable with explanation), and reconcile `Schema tabella`'s warning with the currently active/project engine.

#### [UX-031] Stale `Referto di scansione` warning banner persists across subsequent successful imports
- **Severity:** P2 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Referto di scansione`)
- **Observation:** After a failed PDF upload triggers `Referto di scansione` (`1 file scartato`), running a subsequent valid ZIP import does not clear or supersede the earlier warning banner; it lingers until manually clicked away via `"Archivia"`.
- **User Impact:** Makes the user think the latest import also had errors; `"Archivia"` is also an ambiguous button name for dismissing a notification.
- **Proposed Improvement:** Auto-clear or collapse prior scan reports upon starting a new import, and rename `"Archivia"` to `"Chiudi"` or `"Nascondi avviso"`.

#### [UX-032] Untranslated English strings, examples, and slugs in Italian project UI
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `Nuovo progetto`, `ProjectNav`, `/progetti/:id/avanzate`
- **Observation:** Italian mode contains untranslated English placeholders, options, and technical terms: `"Es. Historic Shipping Index 1904"`, `"Es. Vessel-name"`, `"Es. Please output the text content from the image."`, `"table, Table_document, grid"`, `"collegano i vocabolari (bridge)"`, `"shipping-registers-1900s"`, `"Shipping registers — 20th-century maritime newspapers"`, `"adapter modello"`, `“Year”/“Tons”/“Gross”/“Net”`, and `"Fine-tuning"`.
- **User Impact:** Breaks localization quality for Italian-speaking archivists, especially in instructional examples (`Please output...`, `Shipping registers...`).
- **Proposed Improvement:** Localize domain profile titles, UI hints, and example snippets in `it.ts` / `fr.ts` / backend i18n catalogs.

#### [UX-033] Pluralization errors for single-page selection and non-admin-aware `ShareDialog` copy
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (bulk selection bar, `Sposta in…`, `Condividi` `ShareDialog`)
- **Observation:** Selecting 1 page reads `"1 pagina selezionata ... oppure trascinale su una collezione"` (plural `"trascinale"`) and `"+ Nuova collezione con queste pagine"`. Inside `ShareDialog`, even a logged-in `Admin` is told `"chiedi a un amministratore di creare un invito da Utenti"` with plain non-clickable `"Utenti"` and no explicit empty-state message when no other users exist.
- **User Impact:** Awkward Italian grammar and confusing instructions telling the Admin to ask an administrator instead of linking straight to `/utenti`.
- **Proposed Improvement:** Use proper singular/plural i18n forms (`trascinala` / `questa pagina`), show an explicit empty-users note in `ShareDialog`, and link directly to `/utenti` when the current user is an admin.

#### [UX-034] Low-contrast disabled modal buttons, 2-line bulk bar wrap on 1-page selection, and missing menu divider before `Elimina progetto`
- **Severity:** P3 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `Nuovo progetto`, `Nuova collezione`, `ShareDialog`, bulk selection bar, `Altre azioni` menu
- **Observation:** Disabled buttons (`Crea progetto e continua`, `Nuova collezione`, `Aggiungi al progetto`) render faint white text on gray; single-page selection wraps the bulk bar onto two lines because of `"Usa come copertina di «...»"`; `Elimina progetto` sits directly below `Avanzate` with no horizontal divider.
- **User Impact:** Visual polish and accidental-click hygiene issues.
- **Proposed Improvement:** Improve disabled button contrast, keep bulk bar single-line or cleanly two-row, and add `<hr className="menu-sep" />` above `Elimina progetto`.

#### [UX-035] `/progetti` project cards show only name and page count
- **Severity:** P3 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti` (`ProjectsPage` project cards)
- **Observation:** Project cards show only `"Registro Navale 1912"` / `"4 pagine"`, omitting provenance (browser vs server folder), collection count, or approval/recognition progress.
- **User Impact:** Harder to distinguish multiple archives or check work progress from the project overview.
- **Proposed Improvement:** Show subtle metadata chips (e.g., recognized/approved counts and provenance icon/label).

---

### Stage C — Inference / Recognition, Results, Export & Playground

#### [UX-036] Broken `"Riconosci e salva risultati"` link on `/playground` points to legacy `/` route
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/playground` (`Riconosci e salva risultati` link in `PlaygroundPage.tsx:111`)
- **Observation:** With Project 1 and Page 1 selected, `"Riconosci e salva risultati"` links to `/?project=1&pages=1` (and `/` when no page is selected). Clicking it drops the query parameters and redirects to `/progetti/1` instead of `/progetti/1/riconoscimenti`.
- **User Impact:** Users wanting to move from a quick test in Playground to saving a real recognition run land back on the project overview instead of Recognition.
- **Proposed Improvement:** Update the link target to `/progetti/${projectId}/riconoscimenti` (carrying optional page pre-selection query params that `RecognizePage` reads).

#### [UX-037] CSV and TXT exports omit table content for RapidOCR `Table` blocks without warning
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti/:runId` (`Esporta` -> `.csv`, `.txt`)
- **Observation:** RapidOCR creates a `Table` block with `content: ""` (storing cells/grid separately) while rendering a Markdown table for `.md`. Consequently, downloading `.csv` exports an empty `content` cell for the `Table` row, and `.txt` silently drops the entire table.
- **User Impact:** Historians exporting `.txt` or `.csv` lose tabular data silently while `.md`/`.pdf` appear populated.
- **Proposed Improvement:** Serialize structured table grid content into `.txt`/`.csv` exports (or surface an explicit note/option explaining table formatting per export format).

#### [UX-038] `ResultsPage` displays conflicting block counts (`34 blocchi trovati` vs `6 da verificare`)
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti/:runId` (`ResultsPage` page card summary)
- **Observation:** On `shipping_register_p1.png`, RapidOCR merges 28 table-cell lines into 1 `Table` block + 5 `Text` blocks = 6 blocks stored on the page, yet `ResultsPage` prints `"34 blocchi trovati in questo riconoscimento ... 6 da verificare"`.
- **User Impact:** Looks like 28 blocks went missing or failed to save between recognition and review.
- **Proposed Improvement:** Align the reported block count with the actual grouped blocks created on the page (or clarify `"34 segmenti OCR → 6 blocchi creati"`).

#### [UX-039] `Riconosci N pagine` triggers immediate run without choices, flashes empty state, and produces overlapping status filters
- **Severity:** P1 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Riconosci 4 pagine`), `/progetti/:id/riconoscimenti/:runId`, and `PageWall` status chips (`Tutte 4 · Con bozze 4 · Nuova 4`)
- **Observation:** Unlike the `Riconoscimenti` tab, `Riconosci 4 pagine` on `/progetti/1` immediately fires a backend run with the default engine and navigates to `/progetti/1/riconoscimenti/1`, where `"Nessun riconoscimento ancora."` flashes briefly before the run resolves. Once drafts exist, `PageWall` shows `Tutte 4`, `Con bozze 4`, and `Nuova 4`—three pills all counting the exact same 4 pages.
- **User Impact:** Unexpected immediate run start, confusing transient empty notice, and redundant/contradictory status filter badges (`Con bozze` vs `Nuova` both showing `4`).
- **Proposed Improvement:** Either route `Riconosci N pagine` to `/riconoscimenti` (or label it `Avvia OCR locale su N pagine`), show a loading skeleton on `ResultsPage` until run state arrives, and keep `Nuova` distinct from `Con bozze` in filter counts.

#### [UX-040] `Prova modello` (`/playground`) is hidden from navigation and lacks standalone image upload
- **Severity:** P1 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** Global Nav / `/modelli` / `/playground`
- **Observation:** `/playground` has no link in Row 2 nav or `/modelli` (only discoverable via `Cerca ⌘K` or URL, even though `/playground` highlights the `Modelli` nav tab). Inside `/playground`, there is no file uploader—users must select a page from an existing project.
- **User Impact:** Researchers wanting to quickly test an OCR model on a sample scan before creating a project cannot find the tool or upload a standalone image.
- **Proposed Improvement:** Add a visible `"Prova modello"` link/tab inside `/modelli` and allow direct drag-and-drop / file upload of an image in `/playground`.

#### [UX-041] Disabled `"Modello attivo: MonkeyOCRv2-Parsing"` in `Motore` dropdowns lacks explanation or link
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti` and `/playground` (`Motore` `<select>`)
- **Observation:** Both selectors show `"Modello attivo: MonkeyOCRv2-Parsing"` as a grayed-out disabled `<option>` when no VLM server is running, with no inline explanation of why it is disabled or link to `/modelli` to start/configure it.
- **User Impact:** Users see the model they expect to use grayed out with no actionable recovery path.
- **Proposed Improvement:** Add an inline hint + direct link to `/modelli` whenever the active VLM is not currently serving.

#### [UX-042] Duplicate `"Riconoscimenti recenti"` heading and two competing red primary buttons after run completion
- **Severity:** P2 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti` (post-completion run card & history list)
- **Observation:** Upon completion, the top status card renames itself `"Riconoscimenti recenti"`—matching the collapsible `"Riconoscimenti recenti"` section directly below—and displays two adjacent solid-red primary buttons (`Lavora nello Studio` and `Apri risultati`).
- **User Impact:** Visual competition between two equal-weight primary CTAs and confusing duplicate section titles on the same page.
- **Proposed Improvement:** Keep the top card titled `"Ultimo riconoscimento"` / `"Riconoscimento completato"`, and make only one action primary (`btn-primary`) and the other secondary.

#### [UX-043] `ResultsPage` lacks inline text/block preview, and `Esporta` collection filter includes unrun collections
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti/:runId` (`ResultsPage`)
- **Observation:** `ResultsPage` shows page filenames and block counts but offers no quick inline preview of recognized text, forcing users to export a file or open Studio. Meanwhile, `Esporta -> Pagine` lists every collection in the project (`Appendice B`, `Volume_1913`, etc.) even if none of its pages belong to the selected run.
- **User Impact:** Extra steps just to inspect OCR output quality, and empty exports if filtering by an excluded collection.
- **Proposed Improvement:** Add an expandable block/text preview per page row and filter the `Esporta -> Pagine` dropdown to collections represented in the current run.

#### [UX-044] Playground stacks image full-height above block list without zoom/fit or a Clear button
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/playground` (`Layout predetto` & `Blocchi`)
- **Observation:** `"Layout predetto"` renders at full column width/height above `"Blocchi"`, pushing the 34 detected blocks off-screen. Hovering a block highlights the image region out of view above the fold; there is also no `Pulisci` / reset button after running a test.
- **User Impact:** Cannot simultaneously view the highlighted bounding box and its text row without scrolling back and forth.
- **Proposed Improvement:** Place `"Layout predetto"` and `"Blocchi"` side-by-side (`lg:grid-cols-2`) with fit/zoom controls, and add a `Pulisci` action.

#### [UX-045] Accessible label collision on `/playground` (`Motore` hint matches `"Pagina"`)
- **Severity:** P2 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/playground` (`Motore` `<select>` & `Pagina` `<select>`)
- **Observation:** The `Motore` field's hint (`"Solo righe di testo, senza layout né tabelle: dice subito se la pagina si legge."`) is linked via `aria-describedby` / label wrapping so queries containing `"pagina"` collide across both `<select>` elements.
- **User Impact:** Minor assistive-technology / automation ambiguity between the Engine and Page selectors.
- **Proposed Improvement:** Keep field hints concise and ensure `<label>` associates strictly with the control title (`Motore` / `Pagina`).

#### [UX-046] Contradictory `"Solo righe di testo, senza layout né tabelle"` copy and opaque coordinate tuples
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/playground` (`OCR locale` hint & `Blocchi` list)
- **Observation:** `Playground` accurately states RapidOCR returns text lines without tables, yet `Riconoscimenti` runs the exact same engine (`rapidocr`) and produces `Table` blocks. Also, `Blocchi` renders raw coordinate tuples like `[118, 91, 855, 132]` with no explanation of coordinate space.
- **User Impact:** Confuses users about RapidOCR's actual table capabilities and displays developer coordinates.
- **Proposed Improvement:** Clarify the difference between Playground's line-level preview and full Recognition's table grouping, and hide raw bbox tuples behind a tooltip or toggle.

#### [UX-047] Minor layout polish on `/progetti/:id/riconoscimenti` (orphaned header link, full-width CTA, dialog `[X]` autofocus)
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti` and `"Le pagine hanno già delle bozze"` dialog
- **Observation:** `"Aggiungi pagine"` wraps below `H1` instead of aligning in the header right slot; `"Avvia riconoscimento"` stretches across the full 1440px card width; disabled buttons have faint contrast; the overwrite confirmation modal autofocuses `[X]` close instead of a safe action.
- **User Impact:** Weak visual hierarchy and accidental-Enter dismissal risk in the overwrite dialog.
- **Proposed Improvement:** Place `"Aggiungi pagine"` right-aligned in `PageHeader`, constrain CTA button width (`w-auto`), improve disabled contrast, and autofocus `Annulla` or `Sostituisci`.

#### [UX-048] Technical slugs, raw filename prefixes, and mixed English terms in Recognition & Playground
- **Severity:** P3 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti/:runId`, `/playground`
- **Observation:** Technical identifiers (`local-ocr`, `rapidocr`, `000000_` prefixes) and English terms (`Fine-tuning`, `Layout`, `OmniDocBench`, `Text`) appear in Italian UI; `ResultsPage` prints `"Pagina attuale: Nuova"` for pages that `/progetti/:id` classifies as `"Con bozze"`.
- **User Impact:** Inconsistent terminology between Archive and Results (`Nuova` vs `Con bozze`) and developer-centric identifiers.
- **Proposed Improvement:** Strip internal upload prefixes, use localized status wording (`Con bozze` / `Nuova con bozze`), and translate `Text` / `Layout` labels where appropriate.

---

### Stage D — Annotation Studio & Review Workflow

#### [UX-049] `Altro -> Vista -> Solo trascrizione` traps users without canvas or UI way back
- **Severity:** P0 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` -> `Altro -> Vista -> Solo trascrizione`)
- **Observation:** Selecting `Solo trascrizione` unmounts `StudioCanvas` along with the `Altro` menu—the **only** visible UI control containing `Vista` (`Tutto`, `Solo canvas`, `Solo trascrizione`). The mode persists across reloads, leaving no clickable control on screen to restore the scan/canvas unless the user knows the undocumented shortcut `1`.
- **User Impact:** Complete workflow trap for any researcher who clicks `Solo trascrizione` and wants to return to `Tutto` or `Solo canvas`.
- **Proposed Improvement:** Place `Vista` controls in `StudioHeader` (always mounted regardless of pane visibility) rather than inside `StudioCanvas`'s `Altro` menu.

#### [UX-050] Reading-order reordering and `Rinumera` produce 0-based indices (`0, 1, 2`)
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`ContentPane` reading-order arrows & `Opzioni -> Rinumera`)
- **Observation:** Moving the `Table` block up or clicking `Opzioni -> Rinumera` assigns `0, 1, 2` instead of `1, 2, 3`, whereas initial verified blocks started at `1, 2` (and manually drawing a new block inserted `3` after `5` -> `1, 2, 5, 3`).
- **User Impact:** Confusing 0-indexed reading order (`0`, `1`, `2`) that conflicts with human numbering and initial `1, 2` indices.
- **Proposed Improvement:** Normalize all reading-order displays and `Rinumera` outputs to 1-based numbering (`1, 2, 3, ...`).

#### [UX-051] Studio canvas narrows to ~310px at `1440×900`, wrapping toolbar onto 3 rows and colliding hints & Undo toast with zoom bar
- **Severity:** P1 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` toolbar, bottom-left hint pill, delete Undo toast, zoom bar; confirmed in host screenshot)
- **Observation:** With `PageSidebar` expanded at `1440×900`, `StudioCanvas` shrinks to ~310px wide: the toolbar wraps onto 3 rows (`Seleziona/draw`, `Riconosci/Confronto`, `Altro`), the bottom-left instruction pill wraps into a 10+ line vertical sliver (`"Trascina / un / blocco / per / spostarlo..."`) clipping directly into `- Riduci | Adatta · 25% | + Aumenta`, and deleting a block renders the Undo toast (`"Eliminato Note..."`) right on top of the zoom controls.
- **User Impact:** Overlapping UI controls make zoom buttons hard to click and instruction pills unreadable at normal desktop resolution.
- **Proposed Improvement:** Give `StudioCanvas` a sensible `min-width`, move hints out of the overlay (or hide when cramped), and anchor toasts above the zoom bar without overlap.

#### [UX-052] `Annulla` (`Ctrl+Z`) ignores text edits & block verification; `Ripeti` and `Salva` hidden inside `Altro`
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` top toolbar & `Altro` menu)
- **Observation:** Editing transcription text or toggling `Verificato` leaves `Annulla` (`Ctrl+Z`) disabled; Undo/Redo only tracks structural geometry/order edits. Moreover, `Ripeti` (Redo) and `Salva` are tucked inside `Altro` while `Annulla` sits outside on the main toolbar.
- **User Impact:** Researchers expecting `Ctrl+Z` to undo an accidental transcription edit or verification click lose changes or cannot recover easily, and Redo is hard to discover.
- **Proposed Improvement:** Track text/verification edits in the Studio undo stack (or clarify native textarea undo), and keep `Annulla` + `Ripeti` + save indicator grouped together in `StudioHeader`.

#### [UX-053] Stale draft badge in `PageSidebar`; `Verifica e vai alla successiva` triggers HTTP 409 & exposes DB block IDs
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`PageSidebar`, `PageWorkControl` -> `"Confermare le bozze e approvare?"`)
- **Observation:** Verifying blocks on Page 1 does not refresh the `6 bozze` count in `PageSidebar`, even after switching pages and returning. Clicking `Verifica e vai alla successiva` when unverified blocks exist fires `POST /api/pages/1/approve` (HTTP `409 Conflict`) to open `"Confermare le bozze e approvare?"`, which prints raw DB IDs (`"blocco 23: non confermato · blocco 25 (Table): 29/29 celle di testo con prefill non ancora verificate"`) and focuses `[X]`.
- **User Impact:** Sidebar shows misleading outdated progress; normal validation triggers a 409 error in devtools and overwhelms archivists with database primary keys.
- **Proposed Improvement:** Recompute `PageSidebar` draft counts dynamically from live Studio edits, check readiness client-side before `POST /approve`, replace DB IDs with reading-order labels (`Blocco #3 (Tabella)`), and autofocus `Annulla`.

#### [UX-054] Canvas jumps to scan bottom on `ContentPane` interactions; collapsed verified rows require clicking tiny badge
- **Severity:** P1 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` viewport & `ContentPane` verified block rows)
- **Observation:** Scrolling `ContentPane` or interacting with blocks caused `StudioCanvas` to jump to the bottom of the scan until `Adatta` was clicked again. When a block is checked `Verificato`, it collapses into a 1-line card where only clicking the small `✓ Verificato` badge re-expands it (clicking the block row body does nothing).
- **User Impact:** Disorienting viewport jumps and finicky click targets when reviewing verified blocks.
- **Proposed Improvement:** Preserve canvas pan/zoom state across block selection/editing, and make the whole collapsed block row toggle expansion.

#### [UX-055] `ResizeObserver loop` console errors, duplicate `<main>` landmarks, and aborted crop requests in Studio
- **Severity:** P2 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio`
- **Observation:** Console logged `"ResizeObserver loop completed with undelivered notifications."` repeatedly when toggling `Vista`; DOM contains two `<main>` elements (outer shell + `StudioCanvas`); rapid block/state changes produce aborted crop fetches (`net::ERR_ABORTED`).
- **User Impact:** Console noise, degraded screen-reader landmark structure, and wasted image requests.
- **Proposed Improvement:** Throttle/defer `ResizeObserver` canvas measurements with `requestAnimationFrame`, replace the inner `<main>` with `<section>`, and cancel/deduplicate crop URLs cleanly.

#### [UX-056] Toolbar layout shifts when activating `Seleziona`; inconsistent `Classe` selector types
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` tool buttons & `Classe` pickers)
- **Observation:** `Seleziona` expands to display text when active while `Rettangolo`, `Poligono`, and `Panoramica` stay icon-only, shifting adjacent toolbar buttons on every switch. Meanwhile, the drawing toolbar uses a native `<select>` for `Classe` whereas block cards use a custom combobox button.
- **User Impact:** Jarring horizontal button jumps and inconsistent dropdown interaction.
- **Proposed Improvement:** Keep tool button widths fixed (or show labels consistently in tooltips), and unify the `Classe` selector component.

#### [UX-057] Missing `PageSidebar` search/filter, `100%` zoom preset, and keyboard shortcut guide in Studio
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`PageSidebar` & `StudioCanvas` zoom bar)
- **Observation:** `PageSidebar` only offers prev/next arrows and scrolling (no filename search or status filter). `StudioCanvas` zoom bar provides `- Riduci`, `Adatta · 25%`, `+ Aumenta` with no `100%` preset, and no UI button/popover documents Studio shortcuts (`V`, `R`, `P`, `H`, `1/2/3`, `Space`, `Ctrl+Z`).
- **User Impact:** Slow page navigation in larger archives and hidden keyboard productivity features.
- **Proposed Improvement:** Add filter/search in `PageSidebar`, a `100%` zoom button, and a `?` keyboard-shortcuts popover in `StudioHeader`.

#### [UX-058] Inconsistent block/feature terminology across Studio (`Verificato` vs `Blocco verificato`, `Riconosci`, `Text` vs `Testo`)
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`ContentPane`, `StudioCanvas` toolbar, `Confronto modelli`)
- **Observation:** Text blocks label verification `Verificato` while Table blocks label it `Blocco verificato`; collapsed block summaries print English `Text` while the dropdown prints `Testo`; `Confronto modelli` refers to `"pagina Riconoscimento"` while the tab is `"Riconoscimenti"`; Studio calls prefill `✦ Riconosci`.
- **User Impact:** Minor cognitive friction from mismatched terms across adjacent UI controls.
- **Proposed Improvement:** Standardize verification checkbox wording (`Verificato`), always display localized class names (`Testo`, `Tabella`, `Nota`), and align `Riconoscimenti` naming.

#### [UX-059] Duplicate H2 heading in `Regole di trascrizione` modal and contradictory `Schema tabella` hint
- **Severity:** P2 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`Opzioni -> Regole`, `Opzioni -> Schema tabella`)
- **Observation:** Opening `Opzioni -> Regole` renders the `"Regole di trascrizione"` heading twice (modal header and inner section). `Opzioni -> Schema tabella` displays the MonkeyOCRv2 protocol warning even when OCR was run via `OCR locale`.
- **User Impact:** Redundant dialog chrome and confusing engine notice (echoing UX-030).
- **Proposed Improvement:** Remove the inner duplicate heading in `Regole` and reconcile `Schema tabella`'s notice with the project's active engine.

#### [UX-060] Untranslated English labels & ML jargon in Italian Studio (`Text`, `Table`, `Note`, `transfer gratuito`)
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (block list, undo toast, `Opzioni -> Classi`, approval modal)
- **Observation:** Italian Studio prints `"Eliminato Note"` when deleting a `Nota` block, `"Text"` in collapsed summaries, `"Table"` in approval error details, and developer/ML jargon (`"transfer gratuito"`, `"token da insegnare col fine-tuning"`) inside `Opzioni -> Classi`.
- **User Impact:** Mixed-language UI and intimidating ML terminology for historians.
- **Proposed Improvement:** Localize block label slugs via `labelName()` everywhere (including undo toasts and approval errors) and simplify `Classi` descriptions.

---

### Stage E — PrefillDialog, ConventionsChecklist, Table Workspace & Page Approval

#### [UX-061] Prefill notification banner (`"3 blocchi esistenti rimossi."`) stays stuck across page changes and approvals
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` / prefill status banner)
- **Observation:** Running `✦ Riconosci` (`PrefillDialog`) on Page 3 displays a green banner `"3 blocchi esistenti rimossi."` over the left edge of the canvas. The banner never auto-dismisses, has no close button, and remains stuck over the canvas even after switching to Page 1, approving Page 1, and advancing to Page 2.
- **User Impact:** Obscures part of the scanned image across subsequent pages until full reload.
- **Proposed Improvement:** Clear prefill status banners on page change, add a dismiss button, and auto-hide non-error confirmations after a few seconds.

#### [UX-062] `ConventionsChecklist` is hidden inside `Opzioni -> Regole` modal instead of accessible while transcribing
- **Severity:** P2 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`Opzioni -> Regole` modal vs `ContentPane`)
- **Observation:** Although 5/5 transcription conventions are enabled in `/progetti/1/avanzate`, `ConventionsChecklist` is not shown anywhere inside `ContentPane` while editing blocks—annotators must open the blocking `Opzioni -> Regole` modal to consult project rules.
- **User Impact:** Interrupts transcription flow whenever a researcher wants to check project formatting conventions.
- **Proposed Improvement:** Offer a collapsible non-blocking `Regole` summary drawer/accordion right inside `ContentPane` alongside the modal.

#### [UX-063] Editing an OCR table cell does not mark it verified; bulk approval silently confirms untouched garbled cells
- **Severity:** P1 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** Table Workspace (`UniverSheet` formula bar `✓` & `A1 · ✓ 0/29`) and Studio (`Conferma e approva`)
- **Observation:** Correcting a corrupted OCR cell (`Calcuta` -> `Calcutta`) leaves the cell unverified (`1/29` unchanged), forcing an extra manual click on `✓` (`Segna come verificata`) per edited cell. Conversely, clicking `"Conferma e approva"` in Studio marks all `29/29` table cells verified in bulk—even garbled OCR cells (`Tabl I rivals I`, `財品`, `Keaer`) that were never edited or checked.
- **User Impact:** Tedious double-action when carefully correcting cells, paired with silent risk of certifying corrupt OCR table data into training sets.
- **Proposed Improvement:** Auto-mark a cell verified upon manual edit (with undo/unverify option), add a clear `"Segna tutte come verificate"` button in Table Workspace, and warn explicitly in `"Conferma e approva"` when table cells are untouched OCR drafts.

#### [UX-064] `Struttura` menu hides cell merging inside `"Colonna"`, repeats `"Colonna"`, and uses mismatched copy
- **Severity:** P1 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** Table Workspace (`Struttura` dropdown -> `Colonna` `<select>` & merge warning banner)
- **Observation:** Cell-level operations (`Unisci celle`, `Separa la cella unita`) are buried inside a `<select>` labeled `"Colonna"` alongside column-level transforms (`Pulisci spazi`, `Riempi giù`, `MAIUSCOLE`, etc.). Furthermore, `"Colonna"` repeats identically as both the field label and placeholder option, and selecting `Unisci celle` on two non-empty cells displays a warning recommending `"Unisci con la colonna a destra"` while the actual menu option reads `"Unisci a destra"`.
- **User Impact:** Users looking to merge cells don't expect it under `"Colonna"`, and the warning points to an option name that doesn't match the menu.
- **Proposed Improvement:** Separate cell actions (`Unisci celle`, `Separa cella`) from column transforms into distinct controls/groups, use a descriptive placeholder (`"Scegli azione colonna…"`), and match the warning text verbatim to the option label.

#### [UX-065] Post-approval reading-order gap (`0..4, 6`), conflicting `"verificata"` vs `"Approvata"` terms, and inconsistent page sort
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (Page 1 post-approval) and `/progetti/:id` (`CollectionsPanel`, `PageWall`, `Tabella`)
- **Observation:** After approving Page 1, `ContentPane` blocks numbered `0, 1, 2, 3, 4, 6` (skipping `5`). Back on `/progetti/1`, `Senza collezione` reads `"1 con bozze · 1 verificata"` while the filter chip and badge say `"Approvata"`, `Nuova 3 · Con bozze 3` remain overlapping, and `Miniature` moves the approved page to the 4th card while `Tabella` keeps it first.
- **User Impact:** Confusing numbering gaps, inconsistent status terminology (`verificata` vs `approvata`), and pages unexpectedly shifting positions between views.
- **Proposed Improvement:** Keep 1-based contiguous reading order on confirm/approve, unify `"approvata"` terminology across `CollectionsPanel` and `PageWall`, and keep consistent page ordering (or expose an explicit sort control).

#### [UX-066] Standalone `/tabella/:blockId` exposes DB ID, hydrates slowly, and duplicates Studio tabs on `"Torna allo Studio"`
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/tabella/25?...` (`TableWorkspacePage`)
- **Observation:** Opening `"Apri in una nuova finestra"` renders `"Tabella #25"` (raw DB block ID), shows `"Griglia in caricamento…"` for ~2–4s after network idle while UniverJS initializes, and its `"Torna allo Studio"` button navigates the popup window to `/progetti/1/studio?page=1`, creating a duplicate Studio tab instead of closing/focusing the original tab.
- **User Impact:** Technical ID exposure, sluggish perceived load, and duplicated Studio tabs with competing autosave/WebSocket sessions.
- **Proposed Improvement:** Show page filename + table position (`"Tabella · Pagina 1"`) instead of `#25`, show an inline progress state for UniverJS boot, and offer `"Chiudi finestra"` when `window.opener` exists.

#### [UX-067] `PrefillDialog` gives no reason for disabled `"Riconoscimento della pagina disattivato"`, autofocuses `[X]`, and disagrees with sidebar
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`PrefillDialog`)
- **Observation:** Opening `✦ Riconosci` displays `"Riconoscimento della pagina disattivato"` in a disabled `<select>` without explaining how/where to enable a model (`/modelli`), autofocuses `[X]` close on open, and accurately counts `4` unconfirmed drafts while `PageSidebar` still displays stale `"6 bozze"`.
- **User Impact:** Unclear engine status and inconsistent draft counts right next to each other.
- **Proposed Improvement:** Add a helper note + link to `/modelli` under the disabled selector, focus the primary CTA/select on open, and sync `PageSidebar` draft counts.

#### [UX-068] Table column-sum checks return `0` checks with no UI explanation when `Total` row disagrees with column values
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** Table Workspace (`Salva` -> `POST /api/tables/checks`)
- **Observation:** Saving a table with an obvious `Total` row (`142 + 198 + 14 != 635`) ran `POST /api/tables/checks` returning `{"checks":[],"passed":0,"failed":0,...}` and displayed zero feedback or toggle in Table Workspace explaining whether arithmetic checks ran, passed, or require column configuration.
- **User Impact:** Researchers assume column sums were checked cleanly when in fact zero checks executed.
- **Proposed Improvement:** Show a subtle status badge (`"Nessun controllo numerico configurato — Imposta in Avanzate"`) or auto-detect `Total` rows with an explicit toggle inside Table Workspace.

#### [UX-069] Pluralization error in `"Salva intestazione nota"`, low-contrast disabled `"Salva"`, and faint active-cell verified tint
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** Table Workspace (`Salva intestazione selezionata…` modal, `UniverSheet`, `ContentPane`)
- **Observation:** Selecting 1 row × 2 columns opens `"Salva intestazione nota"` with `"Selezione: 1 righe × 2 colonne"` (plural `"righe"`) and low-contrast disabled `"Salva"`. Inside `UniverSheet`, active cell focus borders obscure the pale green `#eaf4ea` verified background until clicking away; Table blocks also stay expanded when verified whereas Text blocks collapse.
- **User Impact:** Minor grammar polish, hard-to-read disabled button, and subtle verification state visibility while editing a cell.
- **Proposed Improvement:** Fix singular/plural (`1 riga`), raise disabled button contrast, and show a clear verified checkmark chip/indicator for the active cell in the formula bar.

---

### Stage F — Fine-Tuning Pipeline (`FineTuningPage`, `DatasetPage`, `TrainingPage`, `EvaluationPage`, `BenchmarkPage`)

#### [UX-070] `BenchmarkPage` silently ranks unreachable offline VLM (`0.000` F1) as `#1` with zero error notice
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/confronta` (`Classifica` after `Esegui benchmark`)
- **Observation:** Running `Esegui benchmark` while `MonkeyOCRv2-Parsing` (`http://127.0.0.1:8888/v1`) is offline returns HTTP 200 in `0.1s` and populates `Classifica` ranking `MonkeyOCRv2-Parsing` `#1` with `F1@0.75: 0.000`, `F1@0.5: 0.000`, and `—` across remaining metrics, without any failed-request indicator or connectivity warning.
- **User Impact:** Researchers see `0.000` F1 as an actual benchmark measurement instead of knowing the model server could not be reached.
- **Proposed Improvement:** Mark failed/unreachable models explicitly in `Classifica` (or block benchmark launch when selected endpoints fail preflight) rather than displaying `0.000`.

#### [UX-071] `BenchmarkPage` (`Confronta modelli`) is omitted from `Percorso di fine-tuning` nav, `FineTuningPage`, and `EvaluationPage`
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning*` (`ProjectNav` `Percorso di fine-tuning` bar, `FineTuningPage`, `EvaluationPage`)
- **Observation:** There is no link to `/progetti/:id/fine-tuning/confronta` anywhere in `Percorso di fine-tuning` (`1 · Prepara i dati`, `2 · Addestra`, `3 · Valuta la qualità`), the Fine-tuning overview, or `EvaluationPage`. It is only reachable via `Cerca ⌘K` or URL, and highlights `3 · Valuta la qualità` when open.
- **User Impact:** Model-comparison benchmarking is effectively invisible inside the fine-tuning workflow.
- **Proposed Improvement:** Add `Confronta modelli` as a step tab or prominent sibling tab inside `3 · Valuta la qualità` and `FineTuningPage`.

#### [UX-072] Legacy `/dataset` and `/annotazione` links still live in Fine-Tuning and Table pages
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `TrainingConfigForm.tsx` (`<Link to="/dataset">`), `DatasetPage.tsx` (`Apri lo Studio` -> `/annotazione?...`), `EvaluationPage.tsx` (`/annotazione?...`), `TableWorkspacePage.tsx`
- **Observation:** While `TrainingPage` (`?view=new`) hydrates, it flashes a broken link `<a href="/dataset">Costruisci il dataset</a>`. On `DatasetPage`, `"Apri lo Studio"` still links to the legacy `/annotazione?project=1&page=3` path instead of `/progetti/1/studio?page=3`.
- **User Impact:** Flash of broken/legacy routes and inconsistent URL navigation across the workflow.
- **Proposed Improvement:** Migrate all remaining `/dataset` and `/annotazione` links to canonical `/progetti/:id/fine-tuning/dati` and `/progetti/:id/studio` routes.

#### [UX-073] Severe layout squeeze (~93px column) and borderless input in `TrainingPage` `2 · Avvio` Vast.ai card
- **Severity:** P1 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/addestra?view=new` (`2 · Avvio` Vast.ai card; confirmed in host screenshot `final_step_309.png`)
- **Observation:** The left description column (`GPU a noleggio su Vast.ai / Tabularium noleggia una GPU entro il budget...`) is squeezed to ~93px wide, wrapping into ~18 narrow lines beside the wide horizontal strip (`Budget ($/h)`, 3 checkboxes, `Limite di tempo (min)`, `Affitta e addestra`). Meanwhile, `Limite di tempo (min)` (`60`) lacks an input border, looking like static text until clicked.
- **User Impact:** Very hard to read the rental conditions and unclear that `60` is an editable field.
- **Proposed Improvement:** Give the Vast.ai description full width or a wider `minmax(240px, 1fr)` grid track above/beside wrapped form controls, and apply `.fld` border styling to `Limite di tempo (min)`.

#### [UX-074] Silent 2-click inline overwrite on `DatasetPage` (`+ Costruisci dataset`) and missing dataset delete action
- **Severity:** P1 · **Category:** `Tedious/Friction` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/dati` (`+ Costruisci dataset` once a dataset already exists)
- **Observation:** After building a dataset once, clicking `+ Costruisci dataset` triggers no network request and opens no modal—it silently swaps the button's text into `"Confermi la sovrascrittura? Un export esiste già e verrà sovrascritto. Premi di nuovo entro pochi secondi per confermare."`. A single click appears unresponsive unless the user reads the mutated button quickly; there is also no button to delete an unwanted dataset.
- **User Impact:** Users think the rebuild button failed or froze on first click, and cannot clean up built datasets.
- **Proposed Improvement:** Open a proper `Confirm` modal (`"Sovrascrivi dataset esistente?"`) per R9 instead of a timed 2-click button mutation, and add a guarded `"Elimina dataset"` button.

#### [UX-075] Missing Vast.ai key error uses plain text `"Modelli → Cloud"` and irrelevant Studio checklist `"Cosa fare"`
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/addestra?view=new` (clicking `Affitta e addestra` without Vast.ai API key)
- **Observation:** Submitting `Affitta e addestra` returns HTTP 400 `"API Key di Vast.ai non configurata: aggiungila in Modelli → Cloud."` where `"Modelli → Cloud"` is unclickable plain text, accompanied by an unrelated generic `"Cosa fare"` tip about `"checklist dello studio"`. Meanwhile `Usa una GPU già disponibile` hides `Controlli prima dell'avvio` inside a collapsed block.
- **User Impact:** Forces manual navigation away to find Cloud settings and confuses users with irrelevant Studio checklist advice.
- **Proposed Improvement:** Link directly to `/modelli` (`Configura chiave Vast.ai in Modelli`), show provider key readiness before submission, and contextualize `"Cosa fare"` errors.

#### [UX-076] Duplicate `"Cambia modello o esecuzione"` links in `EvaluationPage`; ambiguous step statuses on `FineTuningPage`
- **Severity:** P2 · **Category:** `Redundancy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/valuta` and `/progetti/:id/fine-tuning`
- **Observation:** `EvaluationPage` renders `"Cambia modello o esecuzione"` twice stacked directly on top of each other (in the active model block and again in `Modello non pronto`). On `FineTuningPage`, steps `1 · Prepara i dati` and `3 · Valuta la qualità` both report `"1 pagina approvata"` instead of distinct completion states (dataset built vs baseline evaluated) and omit GPU/model availability.
- **User Impact:** Repetitive UI links and unclear progress tracking on the Fine-tuning overview.
- **Proposed Improvement:** Keep a single `"Cambia modello"` link in `EvaluationPage`, and show per-step state (`"Dataset non costruito"` / `"Nessuna valutazione"`) on `FineTuningPage`.

#### [UX-077] Adapter mismatch between `DatasetPage` (5 adapters) and `TrainingPage` (10 base models)
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/dati` (`Adapter modello`) vs `/progetti/:id/fine-tuning/addestra?view=new` (`Modello base`)
- **Observation:** `DatasetPage` offers 5 adapter choices (plus a separate `Prepara dataset Paddle` toggle under `Viste per altri modelli`), whereas `TrainingPage` offers 10 base models (`Unlimited-OCR`, `Unlimited-OCR-2`, `dots.mocr`, `PaddleOCR-VL-1.6`, `Xiaomi-OCR-0`), without explaining which dataset family each model uses.
- **User Impact:** Researchers selecting `dots.mocr` or `Unlimited-OCR` in Training cannot tell which Dataset recipe to build beforehand.
- **Proposed Improvement:** Show the compatible dataset family directly beside each model in `TrainingPage` (and/or unify adapter selection in `DatasetPage`).

#### [UX-078] Layout misalignment in `DatasetPage` (`Parametri`) & `BenchmarkPage` (`Pagine`), bare range slider, low-contrast disabled button
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `DatasetPage` (`Parametri`), `BenchmarkPage` (`Pagine`), `EvaluationPage` (`Esegui valutazione`)
- **Observation:** In `DatasetPage`, `Solo campione pilot salvato` sits misaligned below `Adapter modello`'s helper text, and `Quota di train` uses a plain `<input type="range">` without quick presets (`80/20`, `70/30`). In `BenchmarkPage`, the `Pagine` input (`10`) and helper text wrap unevenly with checkboxes and `Esegui benchmark`. Disabled `Esegui valutazione` on `EvaluationPage` renders low-contrast washed-out red text.
- **User Impact:** Cluttered form alignment and imprecise split percentage adjustment.
- **Proposed Improvement:** Align grid rows properly, add quick split ratio chips beside the range slider, and raise disabled CTA contrast.

#### [UX-079] Deep 4-row navigation stack on Fine-tuning pages, `"Fine-tuning"` H1 on Training, and modal `[X]` autofocus
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/*` (`Layout` + `TrainingPage` + `"Snapshot del dataset non aggiornato"` modal)
- **Observation:** Fine-tuning sub-pages stack 4 header/nav bars (`Row 1`, `Row 2`, `ProjectNav`, `Percorso di fine-tuning`) consuming substantial vertical viewport height. `TrainingPage` titles its `<h1>` `"Fine-tuning"` instead of `"Addestramento"` (whereas `/dati` uses `"Dataset"` and `/valuta` uses `"Valutazione"`). The `"Snapshot del dataset non aggiornato"` modal focuses `[X]` close first.
- **User Impact:** Cramped above-the-fold space and inconsistent page naming.
- **Proposed Improvement:** Title `TrainingPage` `<h1>` `"Addestramento"`, compact header spacing on sub-routes, and focus primary modal CTA on open.

#### [UX-080] Heavy ML jargon (`F1@0.75`, `TEDS`, `CER`, `OTSL`, `gold set`) without tooltips; mixed `"approvate"` vs `"verificate"` & pluralization bug
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/fine-tuning/*`
- **Observation:** Evaluation/Benchmark tables and helper text rely on unexplained ML acronyms (`F1@0.75`, `F1@0.5`, `TEDS`, `CER`, `OTSL`, `gold set`, `checkpoint`, `Adapter modello`). `DatasetPage` prints `"1 pagine approvate"` (plural `"pagine"` for 1) and mixes `"2 pagine approvate"` in the header with `"Tutto il progetto 2 verificate"` in the scope radio.
- **User Impact:** Non-ML humanities researchers cannot interpret quality metrics without leaving the app.
- **Proposed Improvement:** Add plain-language `(?)` metric tooltips (`TEDS` = table structure similarity, `CER` = character error rate), fix `1 pagina` agreement, and unify `"approvate"`.

---

### Stage G — Models Hub, Settings, Users, Invite & Multi-Role Testing

#### [UX-081] `localStorage` scan report leaks across user sessions and renders on `404` unshared project URLs
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Referto di scansione` when `audit_editor` visits unshared `/progetti/2`)
- **Observation:** When `audit_editor` navigated directly to unshared `/progetti/2`, the API returned HTTP `404` (`progetto non trovato`), yet the page still rendered `audit_admin`'s previously cached `"Referto di scansione"` (`File trovati 2 · Registrati 2`) from `localStorage`.
- **User Impact:** Leaks project activity across accounts sharing a browser and displays stale success banners beside a `404` error.
- **Proposed Improvement:** Scope scan-report storage per user/session, clear it on logout, and never render cached project panels when the project fetch returns 403/404.

#### [UX-082] `/progetti/:id/riconoscimenti` leaves write actions active for `Viewer` (`Sola lettura`), causing `403` failure after modal
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/riconoscimenti` (`audit_viewer` with `Sola lettura` access)
- **Observation:** Unlike `/progetti/1` and Studio (`ProjectReader`), `/progetti/1/riconoscimenti` leaves `Aggiungi pagine`, `Avvia riconoscimento`, and `Confronta le pagine selezionate` enabled for a read-only `Viewer`. Clicking `Avvia riconoscimento` opens `"Le pagine hanno già delle bozze"` and confirming hits `POST /api/projects/1/recognition-runs -> 403 Forbidden` with `"controlla il log del backend nel terminale"`.
- **User Impact:** Confusing false affordance that fails mid-workflow with a backend permission error and developer log advice.
- **Proposed Improvement:** Gate write actions (`Avvia riconoscimento`, `Aggiungi pagine`, `Confronta`) cleanly on `canEdit`, showing a clear read-only banner as in Studio and Fine-tuning.

#### [UX-083] Non-admin users (`Editor` and `Viewer`) can open admin-only `/impostazioni` tabs (`Istanza`, `Dati e backup`, `Ambiente`)
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/impostazioni` (`Istanza`, `Dati e backup`, `Ambiente` tabs when logged in as `audit_editor` or `audit_viewer`)
- **Observation:** Non-admin accounts see and can switch into `Istanza`, `Dati e backup`, and `Ambiente`, exposing instance config, OIDC settings/placeholders, and DB WAL/environment info.
- **User Impact:** Clutters Settings for regular researchers and exposes instance-level administration info to non-admins.
- **Proposed Improvement:** Hide admin-only Settings tabs for `editor` and `viewer` accounts, showing only `Account` (and enforcing admin route checks cleanly).

#### [UX-084] `/utenti` table headers misalign (`Attivo` / `Creato il`) and `password da cambiare` squeezes usernames to `au...`
- **Severity:** P1 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/utenti` (`UsersPage` table at `1440×900`)
- **Observation:** The `Attivo` header aligns over `Creato il` dates (`2026-11-22`), `Creato il` aligns over row action buttons (`Invita`, `Reimposta password`, `Elimina`), and the inline `password da cambiare` badge crowds the username cell so `audit_editor` and `audit_viewer` both truncate to **`au...`**.
- **User Impact:** Broken column scanning and impossible to tell pending accounts apart by username.
- **Proposed Improvement:** Fix header/cell column count alignment in the Users table and place `password da cambiare` below the username or in its own compact status column.

#### [UX-085] `OCRia-1` empty API key validation alarms user with `"Errore imprevisto · Ricarica la pagina"`; `/modelli` fails silently for Viewers
- **Severity:** P1 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/modelli` (`OCRia-1` -> `"Salva e usa OCRia-1"`, and `"Scegli"` buttons as `audit_viewer`)
- **Observation:** Clicking `"Salva e usa OCRia-1"` with an empty API key displays `"Errore imprevisto · Cosa fare: Ricarica la pagina. Il lavoro già salvato resta sul disco."` above `"Inserisci una API key LlamaIndex..."`. For `audit_viewer`, `/modelli` renders active `"Scegli"` buttons that silently do nothing when clicked.
- **User Impact:** Alarming crash-style guidance for a simple blank field, and dead/silent buttons for read-only users.
- **Proposed Improvement:** Treat blank API key as inline form validation (never `"Errore imprevisto"`), and disable or hide model-selection write controls for Viewers.

#### [UX-086] Forced `SetPasswordPage` shows false `"Sei stato invitato..."` copy, re-asks temp password, and lands on `/impostazioni?s=dati`
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `SetPasswordPage` (first login of directly created `audit_viewer` with `must_change_password`)
- **Observation:** After logging in with temporary credentials while the previous browser URL was `/impostazioni?s=dati`, `SetPasswordPage` states `"Sei stato invitato come «audit_viewer»."` (false for admin-created accounts), forces re-typing the temporary `Password corrente`, lacks show/hide toggles, and after saving drops the Viewer onto `/impostazioni?s=dati` (`Dati e backup`) instead of `/progetti`.
- **User Impact:** Confusing false invitation wording, redundant password entry, and disorienting landing on an admin tab.
- **Proposed Improvement:** Distinguish admin-reset vs invite wording, avoid re-asking the password just submitted at login (or explain clearly), and redirect cleanly to `/` / `/progetti` when `must_change_password` clears.

#### [UX-087] Silent no-op on empty `Crea utente`; `ShareDialog` offers `"Rendi proprietario"` to Viewers & inconsistent font
- **Severity:** P2 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/utenti` (`Crea utente`), `ShareDialog` (`Condividi`)
- **Observation:** Submitting `Nuovo utente` with blank fields triggers neither inline error text nor browser focus validation. In `ShareDialog`, `"Rendi proprietario"` appears on read-only `Viewer` collaborators, and `Proprietario` renders in monospace font while other roles use sans-serif.
- **User Impact:** Dead-feeling form submit on `/utenti` and risky/confusing ownership transfer action on read-only Viewers.
- **Proposed Improvement:** Show clear inline required-field errors on `Crea utente`, restrict `"Rendi proprietario"` appropriately (or require confirmation), and normalize role typography.

#### [UX-088] English role names without capability explanations in `/utenti`, dialog `[X]` focus, and OIDC placeholder mismatch
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/utenti` (`Admin`/`Editor`/`Viewer`, `Invite` modal), `/impostazioni?s=istanza` (`Testo del pulsante`)
- **Observation:** Role dropdowns/badges use untranslated English (`Admin`, `Editor`, `Viewer`) in Italian UI with no explanation of permissions (`Editor` vs `Viewer` capabilities). The `Invite` link modal focuses `[X]` close first and omits link expiration info. In `Istanza`, `Testo del pulsante` contains `"SSO"` while its hint mentions `"Accedi con Unito"`.
- **User Impact:** Admins guessing role scopes and invite expiration; mismatched OIDC example vs default value.
- **Proposed Improvement:** Localize role names with brief scope subtitles, state invite expiry in the modal and focus `Copia`, and make `"SSO"` a placeholder rather than pre-filled value if intended as default hint.

#### [UX-089] Inconsistent Step 2/Step 3 labels in `/modelli`, unclear Modal Bearer option, and developer `"OpenCode"` copy
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** `/modelli` (`2 · Dove eseguirlo`, `3 · Configura` / `3 · Manuale`, `Modal`)
- **Observation:** Clicking `"Endpoint compatibile"` in Step 2 opens Step 3 titled `"3 · Manuale"`; `OCRia-1` changes label from `"OCRia-1 (LlamaIndex)"` (Step 1) to `"LlamaIndex OCRia-1"` (Step 2); `3 · Manuale` exposes internal/dev wording (`"o un gateway come OpenCode per Gemini 4 Argon"`); Modal `"API Key (opzionale)"` doesn't clarify whether the user invents the token or fetches one from Modal.
- **User Impact:** Naming jumps and unclear credential instructions during remote setup.
- **Proposed Improvement:** Align step/card labels across Steps 1–3, remove internal gateway references, and clarify `"Scegli una chiave Bearer segreta per proteggere il tuo endpoint Modal"`.

#### [UX-090] Missing password toggles across Settings/Users/Invite; truncated `Directory dati` path in `Ambiente`
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/impostazioni` (`Account`, `Ambiente`), `/utenti`, `/invito`, `SetPasswordPage`
- **Observation:** Password inputs across `Cambia password`, `Nuovo utente`, `Reimposta password`, `AcceptInvitePage`, and `SetPasswordPage` lack `Mostra` toggles (unlike `/modelli` API keys). In `/impostazioni?s=ambiente`, `Directory dati` truncates mid-path with `...` at `1440px` without wrapping or a Copy button.
- **User Impact:** Error-prone password creation and unreadable full data-directory path during diagnostics.
- **Proposed Improvement:** Add reusable show/hide toggles to all password fields and display `Directory dati` wrapped with a copy button.

#### [UX-091] Grammatical typo (`"Nessuna template"`), raw lowercase 422 on `/invito`, and misleading server-scan 404 advice
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `/modelli` (`OCRia-1` Modal card), `/invito`, unshared `/progetti/:id` (`404`)
- **Observation:** `OCRia-1` Step 2 prints `"Nessuna template Modal è definito"`; `/invito` renders raw unpunctuated lowercase `"password troppo corta (minimo 8 caratteri)"`; unshared project 404 displays `"Non trovato · progetto non trovato — Se è sparito davvero, ricostruiscilo con una nuova scansione della cartella sorgente."`
- **User Impact:** Typo and alarming/irrelevant "re-scan source folder" message when access is denied or unshared.
- **Proposed Improvement:** Fix `"Nessun template Modal è definito"`, format `/invito` password validation cleanly, and show `"Progetto non trovato o accesso non autorizzato"` on project 404.

#### [UX-092] Low-contrast disabled buttons across Settings, Invite, and ShareDialog
- **Severity:** P3 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/impostazioni` (`Salva profilo`, `Salva impostazioni`, `Cambia password`), `/invito`, `ShareDialog` (`Aggiungi al progetto`)
- **Observation:** Disabled buttons use faint white text on desaturated light-gray/pinkish surfaces with low contrast.
- **User Impact:** Reduced legibility of disabled CTA labels.
- **Proposed Improvement:** Apply accessible contrast styling (`ink-3` on muted fill) consistently across disabled `.btn-primary` controls.

---

### Stage H — Collaborative Review, Responsive & Multi-Locale Pass

#### [UX-093] Annotation Studio, Table Workspace, and Archive `Tabella` break at mobile viewport (`390×844`)
- **Severity:** P1 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio`, `/tabella/:blockId`, `/progetti/:id?vista=tabella` at `390×844`
- **Observation:** At `390×844`, Studio keeps all 3 panes (`PageSidebar`, `StudioCanvas`, `ContentPane`) side-by-side instead of switching to pane tabs/drawers, crushing the canvas and overlapping controls. `/tabella/:blockId` retains a 50/50 side-by-side split (~180px per side), and `/progetti/:id?vista=tabella` overflows horizontally (`714.8px` in `390px`).
- **User Impact:** Annotation Studio and Table Workspace are unusable on narrow screens/tablets.
- **Proposed Improvement:** Stack or tab panes cleanly below `768px` (or display an explicit mobile notice for dense canvas/spreadsheet editors) and make `Tabella` responsive or cleanly horizontally scrollable inside its wrapper.

#### [UX-094] Contradictory `"Tutte le pagine sono state lette e controllate."` banner shown while Page 4 is `"Nuova"`
- **Severity:** P1 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id` (`Registro Navale 1912` top summary banner)
- **Observation:** Before `audit_editor` submitted Page 4 (`Volume_1913/folio_02.png`)—while Page 4 still displayed badge `"Nuova"` and had not been approved—`/progetti/1` displayed `"Tutte le pagine sono state lette e controllate."`.
- **User Impact:** Misleads project leads into believing all pages are checked when unreviewed pages remain.
- **Proposed Improvement:** Base completion banner strictly on `approved_pages === total_pages` (and show `"In attesa di revisione"` counts when `in_review > 0`).

#### [UX-095] Ambiguous revision status (`"Da correggere"` vs `"Da rivedere"`), missing `/progetti` review queue cue, and mandatory submit modal
- **Severity:** P2 · **Category:** `Unclear Button/Label` · **Status:** `[x] Fixed`
- **Route & Element:** Studio `PageWorkControl`, `PageSidebar`, `/progetti/:id`, `/progetti`
- **Observation:** When an Admin clicks `Richiedi correzioni`, the Studio header reads `"Da correggere"` while `PageSidebar` and `/progetti/:id` filter chips read `"Da rivedere"` (which sounds like "awaiting review" rather than "changes requested"). Moreover, `/progetti` shows zero indicator of pages awaiting review for a reviewer, and `"Invia al revisore"` always forces an extra confirmation dialog.
- **User Impact:** Editors and reviewers misread `"Da rivedere"` vs `"In verifica"`, and reviewers must enter each project to discover pending review items.
- **Proposed Improvement:** Standardize `"Da correggere"` everywhere for revision-requested pages (`"In verifica"` for awaiting review), show pending-review badges on `/progetti` cards, and streamline submission.

#### [UX-096] Studio canvas toolbar wraps onto 2 rows at `1100×760` and in French at `1440×900`
- **Severity:** P2 · **Category:** `Layout` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`StudioCanvas` toolbar & bottom-left hint at `1100×760` IT and `1440×900` FR)
- **Observation:** At `1100×760`, `Confronto modelli` and `Altro` wrap onto a second toolbar row while the bottom-left pan/zoom hint nearly touches the zoom bar. At `1440×900` in French, longer labels (`"Comparaison de modèles"`, `"Plus"`) force the exact same 2-row toolbar wrap.
- **User Impact:** Reduced vertical canvas area and tight bottom overlay clearance on laptops and non-Italian locales.
- **Proposed Improvement:** Keep toolbar actions single-row via compact icon+tooltip buttons or overflow menu, and remove/collapse verbose bottom-left canvas hints when space is narrow.

#### [UX-097] Accessible label off-by-one on Studio block cards (`"Blocco 2: Text"` for displayed order `3`)
- **Severity:** P2 · **Category:** `Bug/Error` · **Status:** `[x] Fixed`
- **Route & Element:** `/progetti/:id/studio` (`ContentPane` block card `aria-label` on Page 4)
- **Observation:** A block displaying reading-order number `3` in the UI exposed accessible name `"Blocco 2: Text"` (0-based array index instead of 1-based reading order).
- **User Impact:** Screen-reader users hear mismatched block numbers compared to visual order numbers.
- **Proposed Improvement:** Derive accessible block labels from the same 1-based reading order shown visually (`Blocco 3: Testo`).

#### [UX-098] Unnatural & inconsistent English (`EN`) copy (`"Recognitions"`, `"Go to the next"`, Title Case drift)
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `ProjectNav`, Studio `PageWorkControl`, `ContentPane`, Table Workspace (`EN` locale)
- **Observation:** English localization contains awkward direct translations and casing drift: `"Recognitions"` tab label (unidiomatic plural in OCR tools vs `"Recognition"` / `"OCR Runs"`), `"Go to the next"` (missing noun `"page"`), `"Block checked"` vs `"Verified"`, and Title Case `"Open in New Window"` / `"Open Table"` alongside sentence-case buttons.
- **User Impact:** Feels machine-translated in core navigation and review buttons.
- **Proposed Improvement:** Polish English i18n strings (`"Recognition"`, `"Next page"`, `"Verified"`, consistent sentence case).

#### [UX-099] Untranslated English strings in French (`FR`) UI (`"Fine-tuning"`, `"Dataset"`, `"gold set"`, collapsed `"Text"`)
- **Severity:** P2 · **Category:** `i18n/Copy` · **Status:** `[x] Fixed`
- **Route & Element:** `ProjectNav`, `TrainingPage` (`"Évaluer sur le gold set"`), Studio `ContentPane` (`FR` locale)
- **Observation:** While French properly localizes user roles on `/utenti` (`Admin`, `Éditeur`, `Lecteur` — unlike Italian), it leaves `"Fine-tuning"`, `"Dataset"`, `"gold set"`, and Studio collapsed `"1 · Text"` in English while the dropdown/canvas show `"Texte"`.
- **User Impact:** Mixed English/French terminology in core tabs and Studio block summaries.
- **Proposed Improvement:** Localize block labels consistently (`Texte`), and either localize or clarify `"Fine-tuning"`, `"Dataset"`, and `"gold set"` (`"Jeu d'entraînement"`, `"Jeu de référence"`).

---

## Button & Microcopy Clarity Audit Table

| Current Label (IT / EN) | Location | Why Unclear / Confusing | Suggested Label / Tooltip | Finding ID |
|---|---|---|---|---|
| `Tabularium` (static span) | Global Header Row 1 | Looks like a clickable home link/logo, but clicking does nothing | Convert to home link (`to="/progetti"`) | UX-010 |
| `Cerca ⌘K` / `Search ⌘K` | Global Header & Palette | Lists project-level routes (`Pagine`, `Riconoscimenti`, `Dataset`) even with 0 projects; ignores `Ctrl+K` | Filter/disable project routes when 0 projects exist; support `Ctrl+K` | UX-005 |
| `Aiuto` / `Help` | `AccountMenu` | Gives no visual clue it navigates off-site in a new tab | `Aiuto ↗` / `Documentazione online ↗` | UX-011 |
| `Admin` | `AccountMenu` (IT/FR) | Remains English `Admin` even when UI is set to Italian or French | `Amministratore` (IT) / `Administrateur` (FR) | UX-014 |
| `Rotta inesistente` / `Route not found` | `NotFoundPage` | Technical routing jargon ("rotta" / "route") for non-technical researchers | `Pagina non trovata` / `Page not found` | UX-012 |
| `Compila nome utente e password.` / `Enter username and password.` | `SetupPage` | Shown even when `Setup token` is the missing required field | Mention `Setup token` explicitly when empty | UX-003 |
| `credenziali non valide` / `password too short...` | `LoginPage` / `SetupPage` | Uncapitalized, unpunctuated, and unlocalized raw backend text | Localize & format properly via frontend i18n | UX-004 |
| `+ Nuovo progetto` + `Nuovo progetto` | `/progetti` (empty state) | Two primary buttons for the exact same action on screen at once | Keep a single clear primary CTA when 0 projects exist | UX-009, UX-015 |
| `Archivia` | `/progetti/:id` (`Referto di scansione`) | Sounds like archiving data rather than dismissing a scan warning banner | `Chiudi` / `Nascondi avviso` | UX-031 |
| `Importa immagini o PDF` / `Importa cartella` | `/progetti/:id` (`Aggiungi pagine` menu) | Omits `.zip` support even though ZIP import works there | `Importa immagini, PDF o ZIP` | UX-028 |
| `Nuova collezione` (submit CTA) | `Nuova collezione` modal | Repeats the modal heading verbatim on the submit button | `Crea collezione` | UX-029 |
| `Avanzate` (no icon) | `/progetti/:id` (`Altre azioni` menu) | Generic name, lacks icon unlike sibling menu items, leaves `Pagine` underlined when active | `Impostazioni progetto` (with icon & back breadcrumb) | UX-026 |
| `Genera campione` | `/progetti/:id/avanzate` (`Pilot iniziale`) | Enabled on 4 pages despite adjacent text requiring 30–50 balanced pages | Disable or warn inline when `< 30` pages exist | UX-030 |
| `Una collezione per cartella: le sottocartelle restano nella loro cartella` | ZIP import modal | Circular wording that confuses how nested folders map to collections | `Crea una collezione per ciascuna cartella principale` | UX-028 |
| `chiedi a un amministratore di creare un invito da Utenti` | `ShareDialog` | Shown even to a logged-in admin, and `"Utenti"` is plain text instead of a link | Link directly to `/utenti` (`Gestisci utenti`) for admins | UX-033 |
| `Riconosci e salva risultati` | `/playground` | Links to broken `/?project=...&pages=...` instead of `/progetti/:id/riconoscimenti` | Route to `/progetti/:id/riconoscimenti` with page pre-selected | UX-036 |
| `Riconosci 4 pagine` | `/progetti/:id` | Starts an OCR run immediately without confirming engine/scope (unlike `Riconoscimenti` tab) | Open `/riconoscimenti` or rename to `Avvia OCR locale (4 pagine)` | UX-039 |
| `Modello attivo: MonkeyOCRv2-Parsing` (disabled) | `Riconoscimenti` & `Playground` (`Motore` select) | Grayed out with zero explanation or link to `/modelli` | Add inline note + link to `/modelli` explaining how to serve it | UX-041 |
| `Lavora nello Studio` + `Apri risultati` | `/progetti/:id/riconoscimenti` (run complete) | Two side-by-side solid-red primary CTAs compete for focus | Make `Apri risultati` (or `Lavora nello Studio`) primary and the other secondary | UX-042 |
| `Riconoscimenti recenti` (top card & bottom section) | `/progetti/:id/riconoscimenti` | Exact same heading used for both the just-finished card and the history accordion | Rename top card to `Ultimo riconoscimento` | UX-042 |
| `Altro -> Vista -> Solo trascrizione` | `/progetti/:id/studio` | Unmounts `StudioCanvas` (and `Altro` itself), leaving no UI button to restore canvas | Move `Vista` selector into `StudioHeader` | UX-049 |
| `Blocco verificato` vs `Verificato` | `/progetti/:id/studio` (`ContentPane`) | Table blocks use `Blocco verificato` while Text blocks use `Verificato` | Standardize on `Verificato` (with `"Celle da verificare nella tabella"` detail below) | UX-058 |
| `blocco 23: non confermato · blocco 25 (Table): ...` | Studio (`Confermare le bozze e approvare?`) | Exposes internal database row IDs and English `Table` class name | Show reading-order / position (`Blocco #3 (Tabella)`) | UX-053, UX-060 |
| `Ripeti` / `Salva` inside `Altro` | `/progetti/:id/studio` (`StudioCanvas`) | `Annulla` sits on the toolbar while `Ripeti` (Redo) and `Salva` are hidden inside `Altro` | Place `Annulla` and `Ripeti` side-by-side in `StudioHeader` | UX-052 |
| `Colonna` -> `Unisci celle` / `Unisci a destra` | Table Workspace (`Struttura`) | `Colonna` repeats as label & placeholder; mixes cell merges with column transforms; warning says `"Unisci con la colonna a destra"` | Separate cell merge buttons; match warning text to `"Unisci a destra"` | UX-064 |
| `Tabella #25` & `Torna allo Studio` | Standalone `/tabella/:blockId` | Shows raw DB ID `#25`; `"Torna allo Studio"` opens duplicate Studio inside popup | Use `"Tabella · Pagina 1"` and `"Chiudi scheda"` when opened from Studio | UX-066 |
| `Riconoscimento della pagina disattivato` | Studio `PrefillDialog` | Disabled engine dropdown offers no explanation or link to `/modelli` | Add helper text + link to `/modelli` to start/configure a model | UX-067 |
| `+ Costruisci dataset` (when dataset exists) | `/progetti/:id/fine-tuning/dati` | Mutates button text into an inline timed prompt on 1st click instead of opening a modal | Open a standard `Confirm` modal (`Sovrascrivi dataset?`) | UX-074 |
| `F1@0.75`, `F1@0.5`, `TEDS`, `CER`, `gold set` | `Fine-tuning` (`Valuta` & `Confronta`) | Unexplained ML evaluation acronyms and table headers | Add plain-language tooltips explaining each metric for historians | UX-080 |
| `Modelli → Cloud` (in Vast.ai 400 error) | `/progetti/:id/fine-tuning/addestra` | Plain unlinked text paired with irrelevant Studio checklist advice | Link directly to `/modelli` and show contextual remediation | UX-075 |
| `Fine-tuning` (H1 on Addestra) | `/progetti/:id/fine-tuning/addestra` | Repeats section name instead of matching `Dataset` / `Valutazione` page titles | Rename H1 to `Addestramento` | UX-079 |
| `Errore imprevisto` + `Ricarica la pagina` | `/modelli` (`OCRia-1` empty key) | Crash-level error message shown for simple blank input validation | Show inline field validation (`Inserisci una API key`) | UX-085 |
| `Endpoint compatibile` -> `3 · Manuale` | `/modelli` (Step 2 -> Step 3) | Destination card name changes unexpectedly in Step 3 header | Match heading (`3 · Endpoint compatibile`) | UX-089 |
| `Admin` / `Editor` / `Viewer` | `/utenti` | Untranslated English role labels with zero permission explanation | Localize roles + short scope hint (`Può modificare`, `Sola lettura`) | UX-088 |
| `Sei stato invitato come «...»` | `SetPasswordPage` | Shown to admin-created users who never received an invite | `Imposta la tua password personale per «...»` | UX-086 |
| `Da rivedere` vs `Da correggere` | `PageSidebar` / `/progetti/:id` vs Studio Header | Both labels represent the same `needs_revision` state, and `"Da rivedere"` sounds like `"to review"` instead of `"needs fixes"` | Use `Da correggere` everywhere for revision-requested pages | UX-095 |
| `Recognitions` / `Go to the next` | `ProjectNav` / Studio (`EN` locale) | Unnatural English plural & truncated `"Go to the next"` without `"page"` | `Recognition` / `Go to next page` | UX-098 |

---

## Cross-Cutting Themes & Top Priorities

1. **Eliminate Workflow Traps & Broken Legacy Links (Immediate P0/P1):**
   - Move Studio's `Vista` switcher out of `StudioCanvas`'s `Altro` menu into `StudioHeader` so `Solo trascrizione` cannot strand users without a canvas (`UX-049`).
   - Update all remaining legacy routes (`/playground`'s `/?project=...`, `DatasetPage` & `EvaluationPage`'s `/annotazione?...`, `TrainingConfigForm`'s `/dataset`) to canonical `/progetti/:id/...` paths (`UX-036`, `UX-072`).
   - Prevent silent failures: flag unreachable offline models in `BenchmarkPage` `Classifica` instead of displaying `0.000` rank `#1` (`UX-070`), and preserve table text when exporting `.csv`/`.txt` from RapidOCR `Table` blocks (`UX-037`).

2. **Enforce Consistent Multi-User Permissions & Isolate Client State (P1):**
   - Scope `localStorage` scan reports per user and never render cached reports on `404`/`403` responses (`UX-081`).
   - Hide/disable write actions in `/progetti/:id/riconoscimenti` and `/modelli` for read-only `Viewer` users instead of triggering `403` or silent no-ops (`UX-082`, `UX-085`), hide admin-only Settings tabs (`Istanza`, `Dati e backup`, `Ambiente`) from non-admins (`UX-083`), and fix `SetPasswordPage` redirect/copy (`UX-086`).

3. **Fix High-Impact Layout Overflows, Collisions & Truncations (P1):**
   - Give `StudioCanvas` enough breathing room (or move bottom hints outside the canvas overlay) so hints and Undo toasts never collide with the zoom bar at `1440×900` (`UX-051`), and stack Studio/Table panes cleanly below `768px` (`UX-093`).
   - Fix `ProjectDetailPage` `Tabella` horizontal overflow (`UX-018`), `PageWall` `xl:grid-cols-8` extreme truncation (`UX-023`), `TrainingPage`'s squeezed ~93px Vast.ai description column (`UX-073`), and `/utenti`'s misaligned table headers & `au...` username truncation (`UX-084`).

4. **Make Data Verification, Undo & Reading Order Trustworthy (P1):**
   - Fix 0-based reading-order numbering (`UX-050`, `UX-065`, `UX-097`), sync `PageSidebar` draft counts live (`UX-053`), auto-clear stuck prefill banners (`UX-061`), and include text/verification changes in Studio Undo (`UX-052`).
   - Auto-verify manually edited cells in Table Workspace while warning clearly before bulk-confirming untouched OCR-garbled cells (`UX-063`), and surface `TableGuard`/column-sum check feedback clearly (`UX-068`).

5. **Unify Terminology, Error Formatting & Onboarding Paths for Historians (P1–P2):**
   - Replace raw lowercase backend strings (`credenziali non valide`, `password too short...`), raw DB IDs (`blocco 23`, `Tabella #25`), and `pip install` commands with localized, human-friendly guidance (`UX-004`, `UX-022`, `UX-053`, `UX-066`).
   - Make `Prova modello` (`/playground`) and `Confronta modelli` (`/confronta`) discoverable in navigation (`UX-040`, `UX-071`), replace the timed 2-click Dataset overwrite button with an explicit `Confirm` modal (`UX-074`), separate cell merges from column transforms in Table `Struttura` (`UX-064`), and add plain-language tooltips for ML acronyms (`TEDS`, `CER`, `F1@0.75`, `gold set`) (`UX-080`).
