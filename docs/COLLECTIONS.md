# Collections inside a project — specification

Written 3 November 2026. Status: decisions confirmed by the owner on 3 November 2026 (data model, archive unwrapping, ZIP limits, scope chosen when preparing the data).

## Why

One project often holds several kinds of document. In the *Lloyds* project, registers, letters
and lists are different layouts: a model fine-tuned on all of them at once is not always the best
model for one of them, and the page wall mixes them. A **collection** is a named group of pages
inside one project, typically one document type.

## Data model

```sql
-- migration 21
CREATE TABLE collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_collections_name ON collections(project_id, name COLLATE NOCASE);
ALTER TABLE pages ADD COLUMN collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL;
CREATE INDEX idx_pages_collection ON pages(project_id, collection_id);
```

- A project has zero or more collections; a page belongs to **at most one**.
- `pages.collection_id IS NULL` is the declared state **"Senza collezione"** (No collection,
  Sans collection), not a missing value (R5). The API names it with the filter key `none`, and it
  appears in the counts as its own row, so it is never silently hidden or merged with "all".
- A collection belongs to its project: moving a page into a collection of another project is
  refused (checked in the code, and the page and collection project ids are compared in one
  statement).
- Names are unique per project, case-insensitive, trimmed, 1–120 characters.
- `ON DELETE SET NULL` is a safety net only. Deleting a collection always goes through the API,
  which makes the user say what happens to the pages (see below), never by a default.
- The migration is additive. As for every migration, `_guard_upgrade` copies the database to
  `backups/tabularium-<time>-pre-migration-v20.db` before it runs, and the version is recorded in
  the same transaction as the DDL.
- The old `split_strategy="collection"` read a free `meta_json.collection` key that nothing
  writes (a silent absence). It now groups by the real `collection_id`; pages without a
  collection form one group.

## API

All routes are under the project and use `require_resource` like the other project routes:
reading needs read access, everything else needs write access (owner, editor member, admin);
other people's projects answer 404 (R12).

| Method and path | Access | What it does |
|---|---|---|
| `GET /api/projects/{id}/collections` | read | Collections with per-state page counts, plus the `none` row and project totals |
| `POST /api/projects/{id}/collections` | write | Create `{name}`; 409 if the name exists |
| `PATCH /api/projects/{id}/collections/{cid}` | write | Rename `{name}` |
| `DELETE /api/projects/{id}/collections/{cid}?pages=unassign\|delete&confirm=true` | write (+ owner/admin for `delete`) | `pages` is required, there is no default; `delete` also needs `confirm=true` |
| `POST /api/projects/{id}/pages/collection` | write | Move `{page_ids, collection_id: int \| null}`; `null` means "Senza collezione" |
| `GET /api/projects/{id}/pages?collection=<cid>\|none` | read | Existing page listing, filtered |

Counts by state use the same states the page wall shows (new, draft, in review, verified),
so a collection row reads e.g. *Registri — 120 pages: 80 new · 30 draft · 10 verified*.

Deleting pages with their collection reuses the existing page deletion path (thumbnails,
blocks, tables, work state) and is audited like any destructive operation (R9). Deleting
pages is restricted to the project owner or an admin, like deleting the project.

## ZIP import

### Transport

The live instance sits behind Cloudflare, which refuses request bodies over 100 MB. A ZIP is
therefore uploaded **in chunks** of 16 MB:

1. `POST /api/projects/{id}/zip-uploads {filename, size}` → `{upload_id}` (size checked
   against the limit before a byte is sent);
2. `PUT /api/projects/{id}/zip-uploads/{upload_id}?offset=N` with the raw chunk, in order;
   the browser shows real upload progress. A chunk that fails (dropped connection, proxy
   timeout, 5xx, or 409 when its answer was lost) is retried with growing pauses, from the
   offset `GET …/zip-uploads/{upload_id}` reports;
3. `POST /api/projects/{id}/zip-uploads/{upload_id}/inspect` → the plan (below), without
   extracting anything;
4. `POST /api/projects/{id}/zip-uploads/{upload_id}/import {layout: "collections" | "flat",
   collection_id?: int | null, collection_name?: str}` (a flat import goes into
   `collection_id`, or into the collection named `collection_name`, created if needed) → a background job, polled like the folder scan, with
   *n of N files* progress, then the final summary.

The same drop zone on the Pages tab accepts images, PDFs and ZIPs. Plain images and PDFs keep the
current upload. Unfinished uploads are removed after 24 hours.

### Safety

Checked on the central directory **before** extracting, then again while writing:

| Threat | Rule |
|---|---|
| Path traversal (zip-slip) | Entry names are normalised (`\` → `/`); absolute paths, drive letters, `..` segments and names that resolve outside the extraction folder are refused and reported. The resolved target is checked against the folder before every write. |
| Symlinks | Entries whose Unix mode is a link (or any non-regular file) are skipped and reported. Nothing is ever extracted as a link. |
| Zip bomb | Limits: archive ≤ 2 GB, ≤ 5,000 entries, ≤ 4 GB uncompressed in total, ≤ 200 MB per file, compression ratio ≤ 100:1 per file. Declared sizes are checked first; the bytes actually written are counted while streaming, so a lying header stops at the limit. Exceeding a global limit refuses the whole archive with a clear message; nothing is imported. |
| Encrypted entries | Skipped and reported. |
| Nested archives | Not opened: skipped and reported. |
| Non-image files | Skipped and reported by name (first 50 names, then a count). PDFs follow the existing PDF handling (one page per PDF page). |
| System clutter | `__MACOSX/`, `.DS_Store`, `Thumbs.db`, `desktop.ini` and dot-files are ignored and counted, not listed as errors. |

The limits are declared once in `config.py` (R8), with environment overrides.

### Where the pages go

The archive is first **unwrapped**: while the top level holds exactly one folder and no
supported files, that folder is treated as the root (zipping the folder *Lloyds* produces
`Lloyds/…`; the user means what is inside it). Then:

The import always asks where the pages go before anything is extracted (owner's request, 3
November 2026: one archive per collection, now or later):

| What the root contains | What the dialog asks |
|---|---|
| Only files, or files in exactly one folder | **Which collection**: a new one, named after the wrapping folder or the archive (editable), an existing one, or "No collection". The collection being viewed is preselected. |
| Two or more folders with supported files | **One collection per folder** (named after the folder) or **all together**; *all together* then asks which collection, as above. |
| Deeper nesting | A collection is a **first-level** folder; everything below it goes into that collection, and the full path stays in the page name (`rel_path`), so nothing is lost. |
| Files next to the folders | Go to "No collection" in the per-folder layout; the dialog says so. |

A new collection whose name already exists in the project (in any case) is reused, so a second
archive can go into the same collection by typing its name or choosing it from the list.
If a collection with the folder's name already exists in the project, the pages are added to
it (re-importing a type adds to it, it does not create *Registri (2)*). The summary says which
collections were created and which already existed.

### Summary

At the end: pages imported (per collection), duplicates already in the project, files skipped
with the reason (not an image, encrypted, link, unsafe path, too large, corrupt image), and
ignored system files.

## Fine-tuning scope

The scope is chosen in step 1, *Prepare the data*, because the dataset is what training and
evaluation read:

- *Pages: the whole project* (default) or *Only these collections* with a checklist of the
  collections (and "Senza collezione") showing verified pages per collection. The line only
  appears when the project has at least one collection.
- `POST /datasets/build` takes `scope: {kind: "project"} | {kind: "collections",
  collection_ids: [int], include_unassigned: bool}`. An empty selection is refused.
- The scope is written in `dataset/report.json` as `scope` with the collection **names and ids
  at build time** (a later rename does not rewrite history).
- Training copies the dataset snapshot, so the run's `run.json` records the same `scope`; the
  run list, the run detail and a model registered from the run show it (*Trained on: Registri,
  Lettere*).
- Evaluation reads the scope from the dataset report and evaluates only pages in it; its report
  records the scope too. The "split by collection" strategy groups by the real collection.
- Recognitions: *Which pages* gets a collection filter (*Not yet recognized in Registri*), and
  the run records the collection filter it was started with.
- Exports: an optional collection filter on the export of a project.

## Collection manager (UI)

On the Pages tab, consistent with `docs/UX_SIMPLIFICATION.md` (one main action, plain language).
Of the two mockups shown on 3 November 2026 (tabs above the wall, or a column beside it) the
owner chose **the column**:

- A *Collections* column left of the pages: *All*, each collection, *No collection*, each row
  with its page count and a line of states (*120 new · 12 drafts · 8 verified*), then
  *+ New collection*. Selecting a row filters the wall and the table; the choice is in the URL
  (`?collezione=<id>|none`) next to the view. The main action (recognize, review) follows the
  collection being viewed.
- Each collection row has a ⋯ menu: *Rename*, *Delete…*. Deleting asks what to do with the pages:
  **move them to "No collection"** (the default) or **delete them too**, which is offered only to
  the owner (or an admin) and needs the collection's name typed.
- Thumbnails have a checkbox (never shown on hover only), and *Select all (N)* above the wall
  selects every page shown after the collection, state and search filters; the table keeps its own; with a
  selection, a bar offers *Move to…* (the collections, *No collection*, *New collection with these
  pages*). Dragging a thumbnail onto a row moves it, or the whole selection if it is selected.
- Files and ZIPs dropped while a collection is being viewed go into that collection.
- **Cover** (migration 22, owner's request 3 November 2026): each collection row shows its cover
  page as a small thumbnail, or a dashed block with the initial when it has none (`cover_page_id
  NULL`, the declared "no cover"). Selecting one page of a collection offers *Use as the cover of
  "X"*; the row menu has *Remove the cover*. The cover must be a page of the collection
  (`PUT …/collections/{cid}/cover {page_id|null}`, 422 otherwise); moving it out clears it.
- With no collections the column is not shown: a project that does not use them looks as before,
  with one quiet *Create a collection* link next to the view switch.

## Delivery

1. Collections: migration, store, API, counts, page filter, split strategy (backend).
2. ZIP import: chunked upload, safe extraction, plan and job, drop zone.
3. Fine-tuning, evaluation, recognition and export scope.
4. Collection manager UI.

Each is a PR on `main` with tests first and `scripts/check.sh` green.
