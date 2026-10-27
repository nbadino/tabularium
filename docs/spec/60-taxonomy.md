# 8. Historic Shipping Index taxonomy & prompt map (project default)

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

| Label (internal) | Exported | Recognition prompt (non-layout class) |
|---|---|---|
| Title | `Title` | text |
| Page-header | `Page-header` | text |
| Issue-number | `Issue-number` | text |
| Issue-date | `Issue-date` | text |
| Column | `Column` | — (layout only; helper) |
| Headline | `Headline` | text |
| Byline | `Byline` | text |
| Text | `Text` | text |
| Section-header | `Section-header` | text |
| List-item | `List-item` | text |
| Advertisement | `Advertisement` | text |
| Note/Adder | `Note` | text |
| Table | `Table` | OTSL |
| Formula | `Formula` | LaTeX |
| Picture | `Picture` | — |

**Why issue number and date are separate classes** (and why there is no `Date` class): the issue
date is the most important field of the corpus — every ship movement is relative to it — and the
corpus spans at least three masthead formats between 1940 and 1973 (large masthead with the number
on the left and the date in the centre; a single line "HISTORIC SHIPPING INDEX, Mon., May 20,
1940."; Voyage Supplement with the date on the left and a thumb index + page number on the right).
Labelling it directly avoids an extraction regex per format. A generic `Date` class would be
inconsistent: dates inside cells number in the hundreds per page and no layout label can reach them.

**Boundary rule**: a layout class never splits a typographic line. Where date and masthead share a
line (1940 format) the date stays inside `Title` and is recovered from the transcription; drawing a
box halfway through a line at a comma is the boundary layout models get wrong most often.

### 8.1 Page genres (`pages.page_type`)

The corpus is not homogeneous and the two genres need different recipes. The type is inferred from
the file name prefix (`backend/app/services/page_meta.py`) and must be set **before** annotating.

| `page_type` | Prefix | Structure | Recipe |
|---|---|---|---|
| `index` | `LSI` | one full-page table, no rules | `Title`, `Issue-number`, `Issue-date`, `Page-header`, `Table` + OTSL |
| `voyage-supplement` | `LSIVS` | parallel columns of ship entries | `Page-header`, `Issue-date`, `Column`, `Section-header`, `List-item` — **no OTSL** |

`issue_date` is not inferred from the file name and **must never come from EXIF**: that is the
digitization date. It goes into `meta_json.scan_date`.

The map is **per project** and lives in `projects.settings_json["labels"]` (additive model: defaults
are always available, custom classes are added/removed). Recognizable classes (with an
`ALL_PROMPT`-style head, §2.3) appear in LAYOUT and in crop JSONL; pure-structure classes only in
LAYOUT.

### 8.2 Classes: origin, classes known per model, native vocabulary

A class has an **origin**: `public` if it belongs to the public parsing taxonomy (§2.7; the base
model already knows it → free transfer) or `custom` (a new token to teach). The origin is computed
from `labeling.PUBLIC_LABELS`, not stored.

"Already known", however, **is not an absolute property of the class: it belongs to the pair
class + model.** Every adapter has a native vocabulary (PP-DocLayout `number`, MinerU `doc_title`,
…) that it translates into the canonical one. Those maps are declared once in
the model's spec (`app/models/<id>.py`) and re-exported by `services/model_labels.py`, which
computes `known_models_for(name)`
says which models already emit a class. `GET /api/projects/{id}/labels` returns `origin` and
`known_models` for each class; `PUT` (admin/editor) replaces the project's **custom** classes with
strict validation (unique non-default name, `#RGB`/`#RRGGBB` color, allowed kind, prompt required
for text/table/formula); `POST` creates **one** class without rewriting the list (the quick gesture
from the Studio, which therefore does not overwrite another tab's work; 409 if the name exists). The
dataset builder builds `_LABEL_INFO` from the project (`_project_label_info`), so a custom class
really enters text_rec instead of being skipped silently.

**The native → canonical bridge** (`model_labels.resolve_native_label`) translates any model label
into the project vocabulary, normalizing spelling (lowercase; space/`_`/`-` equivalent:
`Table_document` ≡ `table document`). Resolution order, from most specific to most generic: exact
canonical name → **the adapter's native map** (the model's semantics win over the generic) →
**class aliases** (declared synonyms, custom classes included). Every class carries `aliases` in
`settings_json`; `LabelsEditor` edits them and prefill uses them. Resolution **fails loudly**: a label
outside the taxonomy never enters silently — prefill counts it in `summary.unmapped_labels` and the
block falls back to `Text`, so the UI warns and the user can add the alias or the class. Prefill
resolves against the **project's** classes (not only the defaults), so custom classes are no longer
coerced to `Text`. Evaluation and benchmark go through the same bridge.

**Consistency rule for fine-tuning**: training **always teaches the canonical vocabulary**; native
maps and aliases only serve to read the *base* model before fine-tuning. Adding a model = declaring
its label map in its spec (`app/models/<id>.py`); this is what makes the system model-agnostic.

**Annotator surface**: in the Studio the **Classes** button opens `LabelsEditor`
(`frontend/src/studio/components/LabelsEditor.tsx`) — it lists classes with an origin badge
(public/custom), marks those known to the active model (`knownByModel`) and lets users add/remove
the project's **custom** classes. The PUT replaces only custom classes; defaults (`builtin`) are not
touched. **New class** (`NewLabelDialog`, in the Options menu) is a quick gesture that creates
**one** class via `POST /labels` and confirms it with a notice, because the class applies to the
whole project. The form fields are shared (`LabelFields`) between the two surfaces. Pure logic lives
in `frontend/src/lib/labels.ts`.

---
