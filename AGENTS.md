# AGENTS.md — Tabularium

Tabularium is software for **researchers who want to run OCR on historical documents and,
when the base model is not good enough, fine-tune it** — simply, but with state-of-the-art
results. It is **model-agnostic** and adds open-weight OCR models over time; any model can run
on the user's machine (CUDA or Apple Silicon) or on an external GPU provider (Vast.ai, RunPod,
Modal, SSH), and one self-hosted instance serves a whole research team.

The reference corpus is a series of historical newspapers, called **"Historic Shipping Index"
(1900s–1970s)** in this document: **dense multi-column layouts and borderless tables**
(ship movement registers, *casualties*, *maritime intelligence*). The reference model is
**MonkeyOCRv2-Parsing**, whose official conventions (§2) shaped the data model. Neither is a
limit: any table-heavy archive and any supported model work the same way.

This document is the source of truth for anyone (human or AI agent) working on this repo:
product needs, technical constraints verified against official repositories, architecture,
data conventions, roadmap and operating rules.

---

## 1. Mission

One tool that takes the user from an archive of images to structured text and, when needed,
to a fine-tuned model. The primary UX does not depend on infrastructure: model and provider are
chosen once, then recognition, review and export work the same locally, on Vast.ai, on Modal or
on any compatible endpoint.

1. **Register** archive pages (scans, multi-page PDFs) with metadata (date, issue, page).
2. **Recognize** one or many pages in persistent sessions, exportable or reviewable later,
   even with inference switched off.
3. **Annotate** the document structure in a guided, very detailed way: semantic blocks +
   **complex tables with merged cells** + **reading order** + **transcription**.
4. **Export** results and datasets in the exact format each model's official training accepts
   (for MonkeyOCRv2: ms-swift JSONL, coordinates normalized to 0–1000, tables in OTSL).
5. **Train** open-weight models (LoRA or full SFT) by generating and launching the official
   scripts of each model, with live logs, loss and GPU monitoring.
6. **Evaluate** on unseen pages (layout/IoU, reading order, TEDS for tables, CER/WER for text),
   compare models side by side, and **iterate**.

Cross-cutting constraint: **cross-platform** (Windows/Linux/macOS) for the UI and data
preparation. Training needs an NVIDIA CUDA GPU (Linux; WSL2 on Windows), local or rented.

---

## 2. Official technical context (verified facts — do NOT question without proof)

These facts describe the reference model, MonkeyOCRv2. Other models declare their own
conventions in their adapter (`services/model_adapters.py`), serving recipe
(`services/serve_recipes.py`) and training recipe (`services/train_recipes.py`).

### 2.1 Model and training stack
- Model: `zenosai/MonkeyOCRv2-B-Parsing` (0.7B: frozen 113M ViT + 0.6B LLM) or `-S` (0.6B: 28M ViT-S).
- Training stack: the **monkeyocrv2-compatible ms-swift** bundled in
  `parsing/train/ms-swift` of the official `Yuliang-Liu/MonkeyOCRv2` repository.
- Canonical command: `swift sft --model zenosai/MonkeyOCRv2-B-Parsing --model_type monkeyocrv2 --template monkeyocrv2 ...`
- The ViT stays **frozen** by default (`--freeze_vit True`); unfreeze it only if the visual
  shift of period sheets requires it (with a reduced `--vit_lr`).

### 2.2 Dataset format (absolute, non-negotiable)
One JSONL line = one sample, ms-swift multimodal `messages` format:

```json
{"messages": [{"role": "user", "content": "<image><PROMPT>"}, {"role": "assistant", "content": "<CONTENT>"}], "images": ["/absolute/path/to/image.jpg"]}
```

- Number of `<image>` tokens in `content` = number of items in `images`. Paths are **absolute**.

### 2.3 Official prompts (authoritative: from `parsing/core_runner.py` → `ALL_PROMPT`)
| Task | PROMPT | CONTENT |
|---|---|---|
| Layout | `Please output the categories and coordinates of the document elements in reading order.` | List of `{'bbox': [x1,y1,x2,y2], 'label': '...'}` in reading order |
| Text | `Please output the text content from the image.` | Plain text, no Markdown |
| Formula | `Please write out the expression of the formula in the image using LaTeX format.` | LaTeX in `$...$` |
| Table | `Please extract the table from the image and represent it in OTSL format.` | OTSL (see §2.5) |
| END2END | `List the document elements in reading order, including their categories, coordinates, and the content of each element.` | records with bbox+label+content |

### 2.3.1 Two official paths: two-stage and END2END
The default runner uses `get_layout()` with `ALL_PROMPT["LAYOUT"]` and gets bbox + label; the
content comes from a second call per block. The repository also exposes the official `END2END`
prompt, which returns bbox + label + content for the whole page in a single generation. The
two-stage path must therefore not be described as the only supported one.

Practical consequence: **if the two-stage layout labels a register as `Text`, it does not mean
the model cannot read tables.** Cropping that block and querying it with the Table prompt gives
correct OTSL — separate columns, including one- or two-character cells. The two things must be
evaluated separately.

On the Historic Shipping Index corpus, END2END at 2 MP already fixed the masthead, title and
subtitle of a sample page compared with the two-stage path, but it produces very long output
(~9k tokens) and its behaviour on a full OTSL register is still to be measured. The UI exposes
both: neither becomes the default on the strength of one page.

### 2.3.2 Image resolution (`min_pixels` ≠ `max_pixels`)
In the official `load_image()`, `min_pixels` **upscales** small images and never downscales;
`get_layout()` passes `1003520`. The upper bound is the `MOCR2_MAX_PIXELS` env var, which the
official CLI `parse.py` **always** sets from its `--max-pixels` (default `1003520`) and
propagates to every call, END2END included. Minimum and maximum coincide: **the official
layout works at 1 MP.**

Not to be confused with `max_pixels 1003520` among the *training* hyperparameters (§2.6): a
different parameter of a different phase, even though the value is the same.

Archive scans are far larger than the size the pipeline is designed for, and the layout
degrades. Measured on a sample `index` page (2864×3952 = 11.3 MP), everything else equal:

| Pixels sent | Blocks | Outcome |
|---|---|---|
| 11.3 MP (no cap) | 2 | overlapping boxes, unusable |
| 6.0 MP | 16 | fragmented |
| 4.0 MP | 137 | exploded down to cells |
| 2.0 MP | 5 | `Title`, number, date, body — correct |
| **1.0 MP** | **4** | correct, less granular — **official default** |

The MonkeyOCRv2 default is the official value, `1003520`, consistent with `MOCR2_MAX_PIXELS`.
This cap belongs to the MonkeyOCRv2 recipe and must not be applied to other VLMs, which keep
their own native preprocessor when their recipe declares no cap. The extra granularity at 2 MP
was our own deviation, measured on one page, not the checkpoint's protocol: to get it back set
`TABULARIUM_VLLM_MAX_PIXELS=2000000` (global override) or the per-model *max pixels* field in
Settings. `TABULARIUM_VLLM_MAX_PIXELS=0` removes the cap; `LAYOUT_MAX_PIXELS` (2 MP) then
remains as the only safety net for layout, because at 11 MP it collapses.

### 2.3.3 Official page preprocessor
`parsing/core_runner.py` never sends the raw page to the VLM: it builds a `Preprocessor`
(weights `preprocessor1.pth`/`preprocessor2.pth`, shipped inside the checkpoint) and runs every
page through it before layout and recognition. The official CLI skips it only with
`--skip-preprocess`, documented as "this may lead to worse accuracy but faster speed".

In Tabularium it is the `monkeyocr` page transformation engine (`services/monkey_preprocess.py`),
**not** a hidden stage inside `inference.py`: the preprocessor changes the geometry, and the
0–1000 normalized bboxes refer to the image the model received, so applying it silently would
put blocks on different pixels than the canvas. Going through the page master keeps the contract
"prefill, crops and export see the same pixels as the canvas", which is also what the official
pipeline does (it saves the preprocessed pages and works only on those).

It is the **only** neural rectifier: UVDoc and DocScanner-L were removed. They were stand-ins
chosen before the model's own preprocessor was reachable, and keeping them alongside meant pages
could be prepared differently from how the model expects them.

### 2.3.4 Large tables: the whole table is the truth, bands are an experiment
Blind chunks are not a safe strategy: they can split rows, rowspans and column context. The
dataset always keeps the full table crop. As an optional augmentation it can add overlapping
bands of logical rows, but only when the annotation contains verified `hlines`; boundaries that
cross a rowspan are forbidden. Visible rules are not used. The geometric detector (§2.3.5) is
only a **proposal to correct**, not a truth: `hlines` count as verified only after an annotator
has checked them.

At inference time the banded path remains an experimental fallback of crop recognition. The
first row is no longer implicitly treated as a header (on many registers it is already a ship);
a repeated header must be declared explicitly with `header_rows`. END2END at 2 MP must be
compared with this fallback on gold pages without overwriting them.

> Note: the official fine-tuning README suggests annotating tables in **HTML** and converting
> them with `html2otsl.py`; at inference the model is queried **directly in OTSL**. Training data
> must therefore contain OTSL. The builder exports OTSL generated from the annotation grid.

### 2.3.5 Grid detection: contract and domain — **DEPRECATED**
> **Deprecated.** The whole geometric path is being phased out: Tabularium is model-agnostic,
> and models do not give us the crop geometry (`vlines`/`hlines`/`row_columns`) — out of 248
> saved grids only one carries it. Structure is corrected in the sheet (merge/split, rows/columns),
> not by moving boundaries on pixels. Only the Studio's "Detect grid" button / re-detection on
> resize and the promotion to `Table` in OCR prefill remain in service, marked in the code. The
> boundary editing surface on the crop (`TableGridOverlay`) and its i18n keys are deprecated and
> not mounted. The text below is kept as a technical record: do not build on this module.

`services/table_detect.detect_grid()` proposes the structure of a `Table` crop. It saves nothing
and is not a truth: it is a draft the annotator accepts, moves or rejects.

**Rows from baselines, not from the profile.** Line pitch and rows come from connected
components (a histogram of glyph *bottoms*), not from the autocorrelation of the ink profile.
The projection path held on one page in four and broke in three distinct ways, all reproduced in
regression tests (`tests/test_table_detect.py`; sample archive pages are labelled with letters in
the rest of this document): period harmonic (page `B`, 156 px instead of 39), saturated profile
from tight leading (page `C`), smearing from skew (page `D`, −1.43°).

| Field | Meaning |
|---|---|
| `vlines` / `hlines` | Boundaries normalized 0–1 **on the crop**, increasing, `cols+1` and `rows+1`. |
| `column_support` | On how many rows each vertical boundary is attested. The two outer ones equal the row count by definition: they are not attested, they are the content edges. |
| `warnings` | Stable codes, never prose: the backend speaks English, the UI translates (§13.11). Today only `skewed`. |
| `diagnostics` | Measurements that make the result verifiable: `pitch_px`, `glyph_height_px`, `glyphs`, `otsu`, `shear`, `skew_deg`, `row_bands`, `leader_dots_suppressed`, `gutters`, `content_x`. |

**Otsu binarization**, not a fixed threshold: on the corpus the threshold falls between 142 and
152, so 128 systematically lost thin strokes. Convention `gray <= t`, as in `cv2`.

**Skew is compensated, not rotated.** The `tan θ` shear corrects glyph coordinates and shifts
row profiles; the image is never rotated, so returned boundaries stay in the crop's frame. But an
`hline` is horizontal by contract: beyond ~0.2° it can no longer follow the text line, and the
detector emits `skewed` because the page must be straightened with *⇱ Deskew* before annotating.
The warning is a declared limit, not an error.

**Boundaries bend.** A column in these registers does not stay put: hand-set and photographed
from a curved page, it drifts horizontally going down. Measured on the sample, every inner
boundary moves 25–77 px from top to bottom, i.e. 0.6–2.0 line pitches, while a digit is about
twenty pixels wide — so **no straight line can describe the table**: a straight cut at the
median lands inside a number and sends half of it to the next cell. On page `B` this happened on
10% of cuts.

`snap_boundaries()` therefore moves each boundary, row by row, into the best whitespace gap
within 1.5 pitches, weighing gap width against distance from the line. Where a gap exists — 86–96%
of cuts on the sample — **no cut splits a value**; where none exists, the line stays and the cut is
marked unproven. Overall, split values went from 5.7% to 0.5%.

Two contract consequences:
- `diagnostics.row_columns` carries the bent boundaries (0–1, per row) and `row_columns_proven`
  says which ones a gap attests; `row_columns_unproven` counts them. They are used to **draw** them
  and to fill cells, and are not persisted yet: the saved grid carries only the straight lines.
- `fill_cells(..., snap=True)` uses the bent boundaries and marks `uncertain` the cells whose
  boundary is unproven. `shear` must be passed too, otherwise gaps are searched in the wrong frame.

There is no "the column drifts" warning: drift happens on all four sample pages (7% to 41% of
cuts unproven), so it would always fire. Drift is not a condition of the scan but a per-cell
property, and it is counted and marked as such.

**Domain.** Valid on a crop that is **a single space-aligned table**. On a multi-column newspaper
page (page `D`, a five-column supplement) it returns a meaningless grid and **cannot tell**: both
testable hypotheses were measured and rejected — gutter width is inverted relative to expectations
(0.62 pitches there against 1.07 for the widest *inner* gutter of a single table) and baselines do
not misalign, because the five columns are set on the same typographic grid. A warning that fires
on the wrong pages teaches people to ignore warnings, so there is none. The workflow solves it: on
such a page the user draws five `Column` blocks, not one `Table` block.

**Dependencies.** Only `numpy` and `Pillow`, like the rest of the backend: connected components
are implemented in the module (runs + union-find) and checked against `cv2` in tests, without
making `cv2` a declared dependency.

### 2.4 Coordinates (CRITICAL — verified in `core_runner.py::_map_bbox_to_image`)
- The model emits bboxes **normalized to 0–1000 per axis** (x and y independently).
- At inference, values are mapped back to pixels with `x / 1000 * width`, `y / 1000 * height`.
- **Therefore**: annotations are stored in source pixels, and export converts to 0–1000:
  `round(px / width * 1000)`, `round(py / height * 1000)`.
- Integer coordinates; always `x1<x2`, `y1<y2` (points are top-left/bottom-right).

### 2.5 OTSL (syntax taken from `core_runner.py::otsl_to_html`)
- Cell tokens: `<fcel>` + text (filled cell), `<ecel>` (empty but *valid* cell),
  `<lcel>` (extends the `colspan` of the valid cell on the left by 1),
  `<ucel>` (extends the `rowspan` of the valid cell above by 1), `<xcel>` (invalid cell).
- Row separator: `<nl>`. Official OTSL has **no closing tags** (`<fcel>text<lcel><nl>…`).
- Encoding a grid (= physical matrix): **row-major** traversal; the first cell of a merged region
  is `fcel`/`ecel`, covered cells to the right are `lcel`, those below are `ucel`.
- Physical cells covered by a 2D merge (both left and above) need care → dedicated `otsl.py`
  module, tested against the official `otsl_to_html`.

### 2.6 Official hyperparameters (canonical defaults — from `parsing/train/scripts/*.sh`)
```
max_length 16384 | max_pixels 1003520 | num_train_epochs 1 | learning_rate 1e-5
lr_scheduler cosine | warmup_ratio 0.05 | deepspeed zero1 | torch_dtype bfloat16
gradient_checkpointing True | train_type lora (rank 8, alpha 32, target all-linear) | freeze_vit True
per_device_train_batch_size 4 | gradient_accumulation_steps 1 | save_steps 500 | save_total_limit 2
```
- The official `eval_steps` is huge (= no evaluation during training); we add a val split.
- The resume logic (finding the latest `checkpoint-N` in `output_dir`) must be preserved.
- These are the **reference model's** values. Every other model takes its own from its recipe
  (§10); copying them across models is a bug (e.g. Qwen3-VL uses `--tuner_type`, packing and zero2;
  GLM-OCR uses LLaMA-Factory hyperparameters).

### 2.6.1 How much VRAM it really needs (and why the official preset does not fit)
The §2.6 hyperparameters are the repository's canonical ones and remain the reference, but they
are written for the card the model was trained on, not for any card. The estimate, implemented and
tested in `services/vram.py`:

| Term | Formula | batch 1, 16384 tokens |
|---|---|---|
| **Logits** | `B × S × vocab × 2` | **4.6 GiB** |
| Weights (bf16, from disk) | — | 2.0 GiB |
| Saved activations (checkpointing) | `L × B × S × H × 2` | 0.9 GiB |
| Recomputing one layer | `B × S × (H + 3·I) × 2` | 0.3 GiB |
| LoRA + Adam (rank 8) | fp32 on adapters | ~0.1 GiB |

The dominant term is the **vocabulary**, not the weights: `MonkeyOCRv2-B-Parsing` has
`vocab_size` 151936 for `hidden_size` 1024, so the logits matrix is 148 times wider than the
hidden state. "It's a 0.7B model, it fits easily" is a wrong intuition: the weights fit easily,
not the output head at full length.

Consequence on an 8 GB GPU (RTX 4060 Laptop, the reference case):

| Configuration | Estimate | Outcome |
|---|---|---|
| Official: batch 4, 16384 | 26.2 GiB | does not fit |
| batch 1, 16384 | 8.3 GiB | does not fit |
| **batch 1, 8192, grad_accum 4** | **5.4 GiB** | **fits** (`gpu8` preset) |
| batch 1, 4096 | 3.9 GiB | fits |
| Full SFT, batch 1, 4096 | 15.5 GiB | does not fit |

Full SFT is not an option on 8 GB and shortening the sequence does not change that: the
optimizer states on all weights are what does not fit. LoRA is the right choice anyway at the
expected data volumes.

`trainer.preflight()` computes the estimate and **blocks** the run when it does not fit,
suggesting the largest `max_length` that does (bisection on the same estimate, so the advice
cannot contradict it). The estimate is deliberately a **lower bound**: it ignores allocator
fragmentation, DeepSpeed buffers, the fp32 cross-entropy peak and the CUDA context. It is meant
to say "it does not fit" with certainty, not "it fits" with certainty — hence a 25% margin
before declaring a run fine.

On `--deepspeed zero1`: on a single GPU it partitions nothing and adds a dependency and buffers.
It stays in the script for fidelity to the official repository, but it is not what makes an
8 GB run possible.

### 2.7 Public parsing labels
`Caption, Footnote, List-item, Page-footer, Page-header, Section-header, Text, Title, Formula, Table, Picture`
→ extended with newspaper custom classes (see §8): `Issue-number, Issue-date, Column, Headline, Byline, Advertisement, Note`.
Custom classes are tokens the model learns during fine-tuning. Every non-text class needs a
recognition prompt defined in the project configuration.

**Always prefer the public label when it describes the same physical thing**: the base model
already knows it, so transfer is free, while a custom class starts from zero and takes examples
away from the others. `Title`, `Page-header`, `Section-header` and `List-item` entered the
taxonomy for this reason.

---

## 3. Functional requirements (user view)

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
     (`/tabella/:blockId`);
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
       (`services/table_headers.py::repair_flat_header`), which does not depend on the model
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
     (editor/viewer). Sensitive rules enforced server-side: no self-demotion, no deleting the last
     admin, 404 for other people's projects (existence hidden);
   - **invitations and onboarding** (M13), **per-page annotator/reviewer workflow** (M19), **live
     presence** (M11);
   - **instance settings** (admin only): name, open/closed registration, default role for new
     accounts;
   - **user management** (admin only): create, change role/activation, reset password, delete.

---

## 4. Stack & architecture

Chosen design: a **local web app** = Python **FastAPI** backend + **React/Vite/TS** frontend.
The backend is the only process the user starts; it serves the UI and orchestrates everything.
No PyTorch in the dashboard process: model serving and training run in separate environments
(`<root>/vllm-runtime`, `<root>/mlx-runtime`, `<root>/paddle-runtime`, remote GPUs) that
Tabularium prepares itself.

### Distribution
- Installed with **`uv tool install tabularium`** (PyPI) or from a GitHub release archive; from
  source with `scripts/setup_backend.sh` + `scripts/run.sh` (§13.6).
- `pyproject.toml` (repository root) builds a wheel with the `app` package and, in
  `app/_bundle/`, the resources a checkout keeps at the repository root: `VERSION`, the prebuilt
  `frontend/dist`, `conf/`, `scripts/cloud/`, `scripts/local/`, `scripts/serve_model.sh`.
- `config.py` resolves them through `config.RESOURCE_DIR` (`config.INSTALLED` tells the two modes
  apart). Installed, the data root defaults to `~/Tabularium`, `.env` lives there, and the vault key
  is generated on first start (never over credentials saved with another key). Any code that reads
  a repository file at runtime **must** go through `RESOURCE_DIR` and be listed in the wheel's
  `force-include` (checked by `tests/test_packaging.py`).
- The `tabularium` command (`app/cli.py`) starts the server and opens the browser
  (`--port`, `--host`, `--data`, `--no-browser`, `--version`).

### Backend
- Python 3.11–3.13, FastAPI, Uvicorn, Pydantic v2.
- Storage: **SQLite** (§6) for projects/annotations; filesystem for images and datasets.
- Pillow + numpy for image processing (thumbnails, tiles, crops, deskew).
- `subprocess` to orchestrate serving, training and evaluation; `nvidia-smi` for GPU telemetry.
- HTTP clients (requests/httpx) towards OpenAI-compatible servers (**vLLM**, `mlx-vlm`) and
  provider APIs (Vast.ai, RunPod, Modal).
- OCR engine: `rapidocr` 3.x + `onnxruntime` (core dependency); `paddleocr` optional
  (`requirements-uvdoc.txt`).
- **Authentication** (stdlib only): PBKDF2-HMAC-SHA256 for passwords, session tokens stored as
  SHA-256 hashes in SQLite, HttpOnly + SameSite=Strict cookies. Local mode `TABULARIUM_AUTH=off`
  for tests. Global-role and per-project permissions in `services/auth.py` +
  `api/deps.py::require_resource`; routes protected at router level.
- Provider credentials are encrypted at rest (`services/vault.py`, Fernet key
  `TABULARIUM_VAULT_KEY` in `.env`).

### Frontend
- React 19 + Vite + TypeScript + Tailwind CSS 4 + React Router 7 + TanStack Query + Recharts.
  Global state via **small modular stores** (`useSyncExternalStore`, no Zustand): `i18n`,
  `app/auth` — `useSyncExternalStore` is the project's canonical pattern.
- Annotation canvas: **Konva.js** (`react-konva`; shape editing, transformer) for blocks.
- Table workspace: **Univer** (`@univerjs/presets`, sheets core) —
  `studio/components/UniverSheet.tsx` + `TableWorkspacePanel.tsx`. The data model is not Univer's:
  `lib/univerGrid.ts` translates `TableGrid` ↔ sheet both ways, `lib/univerEvents.ts` serializes
  only data mutations, the server generates OTSL from our model, and per-cell metadata
  (`source`/`verified`) stay ours. **Structural** changes do not go through the library: the new
  structure is computed by `lib/grid.ts` (`insertTrack`/`deleteTrack`, which know what happens to a
  merged cell crossing the insertion point) and the sheet is remounted; the duplicated native
  structural commands are hidden.
- No all-in-one UI framework: the UI is built from our own components.
- **Auth in the UI**: `AuthGate` decides what to show at start (admin setup, login, forced password
  change, app), `api.ts` resets the user on 401, `Layout` exposes the user menu (name + role +
  logout, Settings/Users for admins only).

### Reused components (not forks)
- `ms-swift` and each model's official training scripts and tools (the app generates
  parametrized scripts; the official repositories stay external checkouts).
- The official `html2otsl.py` (HTML→OTSL converter).
- Official data conventions §2 (JSONL, OTSL, coordinates).

---

## 5. Repository structure

```
tabularium/
  AGENTS.md  README.md  CHANGELOG.md  DESIGN.md  PRODUCT.md  VERSION
  pyproject.toml            # installable package (wheel with app/_bundle, `tabularium` command)
  mkdocs.yml                # documentation site (docs/ → GitHub Pages)
  backend/
    app/
      main.py               # FastAPI entry, serves the built frontend, health
      cli.py                # `tabularium` command
      config.py             # settings, RESOURCE_DIR, data root, .env, vault key
      db.py                 # SQLite schema, versioned migrations
      schemas.py            # Pydantic models
      api/                  # auth, users, settings, projects, pages, blocks, page_work, prelabel,
                            # recognition, datasets, training, evaluate, benchmark, playground,
                            # models, cloud, system, live (WebSocket), deps (permissions)
      services/             # one module per concern; the main ones:
        model_adapters.py   #   per-model prompts, parsers, capabilities, serve commands
        model_labels.py     #   native → canonical label bridge
        model_registry.py   #   catalog, downloads, local runnability
        serve_recipes.py / serve_manager.py / local_runtime.py / mlx_runtime.py / paddle_runtime.py
        train_recipes.py / trainer.py / trainer_script.py / training_views.py
        training_executor.py / vram.py
        cloud_manager.py / modal_manager.py   # Vast.ai, RunPod, SSH tunnels, Modal
        inference.py        #   OpenAI-compatible client (+ Gemini generateContent)
        prefill.py / recognition.py / ocr.py / table_detect.py (deprecated, §2.3.5)
        otsl.py / table_checks.py / table_headers.py / dataset_builder.py / paddle_dataset.py
        evaluate.py / benchmark.py
        auth.py / security.py / users.py / vault.py / live.py / page_work.py / i18n.py
    tests/                  # pytest
    requirements.txt        # runtime deps (mirrored in pyproject.toml);
                            # requirements-dev.txt, requirements-uvdoc.txt (PaddleOCR)
  frontend/
    src/
      app/                  # shell: Layout, AuthGate, auth, ui, models hub logic, live, share dialog
      pages/                # one per route (Recognize, Results, Projects, Annotation, Dataset,
                            # Training, Evaluation, Benchmark, Models, Playground, Settings, Users…)
      studio/               # canvas, annotation state + history, components (ContentPane,
                            # UniverSheet, TableWorkspacePanel, PageSidebar, PrefillDialog…)
      lib/                  # api.ts, grid.ts, univerGrid.ts, coords, labels, dates, avatar…
      i18n/                 # it.ts (structure source of truth), en.ts, fr.ts
  conf/profiles/            # domain profiles
  docs/                     # documentation site (docs/guide/*), screenshots, internal notes
  scripts/
    setup_backend.sh/.ps1  setup_frontend.sh/.ps1  run.sh/.ps1  build_frontend.sh/.ps1
    serve_model.sh          # MonkeyOCRv2 serving wrapper
    version.py              # VERSION bump, synced to frontend/package.json
    cloud/                  # scripts sent to remote GPUs (vLLM/training setup, Modal apps…)
    local/                  # local runtime helpers (MinerU MLX gateway)
    e2e_*.py / e2e_smoke.sh # browser end-to-end tests
  .github/workflows/        # ci.yml, docs.yml (GitHub Pages), release.yml (GitHub + PyPI)
  data/                     # (gitignored) local data root when running from source
```

---

## 6. Data model (SQLite)

- `projects(id, name, root_dir, owner_id, created_at, settings_json)` — settings: classes + colors +
  prompt map, transcription conventions, base model, max_pixels/max_length, split ratio, table hint;
  `owner_id` = who created the project (see permissions below).
- `pages(id, project_id, rel_path, abs_path, width, height, issue_date, issue_no, page_no,
  page_type, status, meta_json)`.
- `blocks(id, page_id, label, bbox_px_json [x1,y1,x2,y2 pixels], content, order_idx, kind
  ['text','table','formula','picture','custom'], prefill_source, confirmed, updated_at)`.
- `tables(id, block_id, grid_json)` — `grid_json`: `{rows: n, cols: m, cells:[{r,c,rowspan,colspan,
  text, source, verified}], vlines:[...], hlines:[...]}` (physical grid; spans are the merges;
  `source` = who wrote the cell text, `verified` = human confirmation — only cells with text).
- `page_work` (migration 19): per-page annotator, reviewer, state
  (`annotation`/`pending`/`changes_requested`/`approved`) and optimistic version.
- Coordinates **stored in source pixels**; normalization to 0–1000 **only at export** (§7).
- **Auth**:
  - `users(id, username UNIQUE, password_hash, email, role ['admin','editor','viewer'], active,
    created_at, last_login_at, must_change_password, invite_token_hash, invite_expires_at)`;
  - `sessions(id, user_id, token_hash, expires_at)` — random 32-byte token, stored **only as a
    hash**; cookie `tab_session` (HttpOnly, SameSite=Strict);
  - `project_members(project_id, user_id, role ['editor','viewer'])` — shared access;
    `projects.owner_id` is the other side (only the owner deletes);
  - instance settings (`instance_name`, `allow_registration`, `default_new_user_role`) are a JSON
    value in `meta` (`services/settings.py`).
- Other tables: `compute_profiles` (active model/provider, v10+), `recognition_runs` /
  `recognition_run_items`, `jobs`, `page_reviews`, `custom_models`, `secrets` (vault),
  `audit_events`, `rate_limits`, `meta`.
- **Migrations**: up to **baseline 6** they are additive by column presence
  (`db.py::_ensure_columns`); from then on every schema change is a **versioned, ordered**
  migration in `db.py::_MIGRATIONS` (version → DDL), applied once to databases that are behind,
  with the version recorded in `meta` and a visible startup error if the chain is incomplete.
  Current `SCHEMA_VERSION`: **19**. Upgrade tests in `tests/test_db_migrations.py`. The historical
  test suite runs with `TABULARIUM_AUTH=off`; `test_auth.py` turns it `on` with a fixture.

---

## 7. Dataset export — JSONL families

Atomic per-project export; train/val split **by page** (default 90/10, fixed seed).

1. **`layout_train.jsonl` / `_val`** — full page image + LAYOUT prompt. CONTENT = list of dicts in
   reading order: `[{'bbox': [x1,y1,x2,y2], 'label': '...'}, ...]` with 0–1000 coordinates.
2. **`text_rec.jsonl`** — one sample per text/headline/etc. block: block crop (PNG/JPEG saved in
   `<root>/projects/<project>/crops/`) + text prompt + transcription.
3. **`table_otsl.jsonl`** — full table crop + OTSL prompt + OTSL generated from the grid. In
   experimental mode, crops of logical row bands are added, never instead of the full crop and only
   with verified `hlines`; report and counts distinguish `full` and `bands`.
4. (optional) `formula.jsonl`, `end2end.jsonl`.

Rules:
- Crops are generated once and cached (same absolute path in the JSONL).
- **Absolute** image paths; the builder writes a sanity report (counts per family/class, boxes
  outside the page, blocks without transcription, classes without a prompt).
- On approved pages only confirmed blocks count: re-running recognition on an approved page must
  not turn unconfirmed drafts into ground truth.
- Valid empty `ecel` cells are included; a table block without any text cell is not exported.
- Bulk autosave updates existing blocks preserving their IDs: deleting and re-inserting would
  trigger `ON DELETE CASCADE` and lose the attached table grids.

Other training formats are views derived from the same canonical annotations, never a second
source of truth: ShareGPT for LLaMA-Factory and the PaddleFormers `messages` view are written by
`services/training_views.py` at run time, and the PaddleX/PP-OCR views below are built separately.
Each training recipe (§10) points to its view; the builder never converts the target silently.

### Official Paddle views (`services/paddle_dataset.py`)

| View | Files | Model | Content |
|---|---|---|---|
| layout | `layout_{train,val}.json` + `annotations/` | PaddleX detector (COCO) | block bbox + class, reading order |
| line | `rec_{train,val}.txt` + `rec_dict.txt` + `cells/` | PP-OCR rec | **one sample per cell**: crop → text |

PaddleOCR-VL itself is trained by the Training Center from the `messages` view
(`training_views.py`), not from this module: PaddleFormers consumes the same JSONL shape as
ms-swift, so a separate VLM exporter would be a second source of truth.

The *line* view closes the loop on the **local** OCR engine (the one that fills cells in
`table_detect.fill_cells`): a hand-corrected cell comes back as a sample. The crop is the same the
recognizer sees in use — grid boundaries plus a quarter pitch of padding, native height (resizing
to 32/48 px is the trainer's job) — because a dataset cut differently from how it is read teaches a
framing that will never exist. With `approved_only` only `verified` cells are included: prefill text
is not gold, and the manifest declares how many were excluded (`cells.unverified_skipped`). The
character dictionary is the one observed in the corpus, not a generic one.

---

## 8. Historic Shipping Index taxonomy & prompt map (project default)

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
`services/model_labels.py` (adapters import them: a single source) and `known_models_for(name)`
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
its map in `model_labels.py`; this is what makes the system model-agnostic.

**Annotator surface**: in the Studio the **Classes** button opens `LabelsEditor`
(`frontend/src/studio/components/LabelsEditor.tsx`) — it lists classes with an origin badge
(public/custom), marks those known to the active model (`knownByModel`) and lets users add/remove
the project's **custom** classes. The PUT replaces only custom classes; defaults (`builtin`) are not
touched. **New class** (`NewLabelDialog`, in the Options menu) is a quick gesture that creates
**one** class via `POST /labels` and confirms it with a notice, because the class applies to the
whole project. The form fields are shared (`LabelFields`) between the two surfaces. Pure logic lives
in `frontend/src/lib/labels.ts`.

---

## 9. Transcription conventions (per-project config)

A checklist always visible in the annotator (Historic Shipping Index default example):
- Expand end-of-line *soft hyphens*, rejoining split words.
- Keep original spelling/abbreviations (`inst.`, `ult.`, `barq.`, `psgr. stmr.`, small caps).
- Ship names in italics → mark `*ship*` (configurable) or plain text.
- Faded column marks/rules: the phantom column is annotated, not transcribed.
- Dashes/row lines in tables: represented by the grid, never in the text.
Violations produce export warnings. They are central to dataset consistency.

---

## 10. Training center

- Each trainable model has a **recipe** in `services/train_recipes.py`: the toolchain the model's
  own vendor documentation uses, the official documentation URL, allowed types (`lora`/`full`), the
  data view, the official hyperparameters/flags and the maturity. Closed models (Gemini 4 Argon)
  have no weights and no recipe; `inference-only` models (TeleOCR, LightOnOCR-3) declare why they
  cannot be trained instead of disappearing.
- **The toolchain is per model, not one global choice**:
  - `ms-swift` — MonkeyOCRv2 (the **bundled fork** in `parsing/train/ms-swift`, where the flag is
    `--train_type`; upstream uses `--tuner_type`), Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2,
    dots.ocr. The other four use upstream ms-swift; MonkeyOCRv2 must use its fork, whose
    `<|image_pad|>` template is not upstream's `<|IMAGE|>` encoder template.
  - `llama-factory` — GLM-OCR, as `examples/finetune/README.md` documents; ShareGPT view + the
    official prompts `Text Recognition:` / `Table Recognition:` / `Formula Recognition:`.
  - `paddleformers` — PaddleOCR-VL, `paddleformers-cli train <config> key=value`, full **and**
    LoRA; the `messages` dataset is the same shape as ms-swift.
  - `mineru-sft` — MinerU2.5: its lesson explicitly forbids `swift sft` and ships the config only
    inside `mineru_sft_code.zip`; the recipe is `planned` and blocks with the documentation link.
- `services/training_views.py` materialises the run's data view from the immutable dataset
  snapshot. Views reshape containers/prompts, never the target: `ms-swift-jsonl`,
  `paddleformers-messages`, `llamafactory-sharegpt`. Each run writes `dataset/view.json` (families,
  prompts, counts, empty families) and embeds it in `run.json`; the view also realigns the
  prompts to the model being trained, whatever adapter built the snapshot. Recognition-only models
  (GLM-OCR, PaddleOCR-VL) do not get the layout family: their protocol has no bbox.
- `services/trainer_script.py` generates the script from the recipe and **fails explicitly** on
  anything it cannot run — never a wrong script silently. The VRAM estimate uses the recipe's own
  geometry (`TrainRecipe.architecture`); an unknown geometry is not blocked on a guessed number.
- Execution in a dedicated environment on the GPU machine — this machine, SSH, Vast.ai or RunPod
  (`services/training_executor.py`; `scripts/cloud/setup_cloud_train.sh` prepares a remote host and
  writes a `.train_ready` marker). The preflight checks **the recipe's** toolchain, not always
  MonkeyOCRv2: `TABULARIUM_TRAIN_REPO` for MonkeyOCRv2, `TABULARIUM_LLAMAFACTORY_DIR` for GLM-OCR,
  `TABULARIUM_PADDLEFORMERS_DIR` for PaddleOCR-VL. The dashboard launches, **streams logs via
  SSE**, parses loss and learning rate, samples GPU telemetry (`nvidia-smi`), stops and resumes.
- Run state, scripts and logs in `<root>/projects/<project>/runs/<run_id>/` (§13.10).
- After a run, registering the checkpoint as a model (`custom_models.register_training_checkpoint`)
  merges LoRA with the **toolchain's own** command: `swift export --merge_lora` (ms-swift),
  `llamafactory-cli export` (GLM-OCR), `paddleformers-cli export` (PaddleOCR-VL). The output must
  be a full checkpoint vLLM can serve.
- Official hyperparameters, per-model notes and the real-run log: `docs/guide/training.md`; the
  verbatim vendor commands: `docs/training-official-docs.md`.

---

## 11. Evaluation

On approved pages (§M6 on which pages), served with the base or fine-tuned checkpoint:
- **Layout**: per-label precision/recall/F1, mean IoU per block, label accuracy;
- **Reading order**: normalized Levenshtein between GT and predicted label sequences (+ share of
  perfect orders);
- **Tables**: **TEDS** (Tree Edit Distance Similarity) GT vs prediction, plus per-cell CER;
- **Text**: **CER/WER** per block.
Output: a JSON + HTML report, with GT vs prediction page overlays and a table of the worst failures
to guide the next annotation round.

Layout is measured at two thresholds: IoU 0.5 (`layout`) and **IoU 0.75** (`layout_strict`, the
layout reward rule of LightOnOCR-3), with overall and per-label precision/recall/**F1**. At 0.5 a
`Table` covering only the left column still passes; at 0.75 it does not. **END2END-only** models
(LightOnOCR-3, Unlimited-OCR, DeepSeek-OCR, dots.mocr) have no layout or crop prompt: they are
evaluated on their native generation, and text and tables are the content of the predicted block
matched at IoU 0.5 (missed blocks stay out of CER and are counted by recall).

---

## 12. Roadmap (milestones)

- **M0 — Scaffolding** ✅ — repo structure, AGENTS.md, README, requirements/package.json, run
  scripts, working FastAPI + Vite skeleton (health | ping | environment info).
- **M1 — Projects & pages** ✅ — project CRUD, folder scan (images + PDF, lazy PDF via pypdfium2),
  page registry with metadata (issue_date/issue_no/page_no/page_type/status), on-demand
  thumbnails/previews, Projects + Project detail pages (page grid, inline metadata edit).
  - **The page list never truncates silently.** `GET /api/projects/{id}/pages` returns `total`
    besides `items`, with a default and maximum `limit` of 10000 (the cap of a recognition session).
    The old default was 200: a 361-page corpus showed 200 on every screen, and the rest could be
    neither seen nor selected.
  - **Every page carries `blocks` and `drafts`** (generated, unconfirmed). `status` only says what
    a person did with it: a recognized page never reviewed stays `new`, and without `drafts` no list
    could tell where the drafts to verify are. Archive, Studio and Recognize show "N drafts" instead
    of "New" and filter "With drafts".
  - **Server dates are UTC without a timezone** (SQLite `datetime('now')`): read them with
    `lib/dates.ts::parseServerDate`, never with a bare `new Date(...)`, which takes them as local time.
- **M2 — Core annotation studio** ✅ — Konva canvas with zoom/pan, rectangle/polygon tools, class
  palette, label/transcription inspection, move/resize with a Transformer, **reading order** (up/down
  moves + automatic reordering), delete, undo/redo (Ctrl+Z/Y), autosave (bulk PUT, 700 ms debounce),
  keyboard shortcuts (V/R/P/H). Block API: GET/PUT `/api/pages/{id}/annotations`, PATCH/DELETE
  `/api/blocks/{id}`, GET `/api/projects/{id}/labels`. The bulk PUT keeps the received IDs and
  therefore the attached tables during autosave. Note: the module is named `blocks.py` (a module
  named `annotations` collides with `from __future__ import annotations`).
- **M3 — Tables + reading order + transcription** ✅ — `Table` block editor: rows/columns,
  **merged cells** (merge right/down, split), phantom columns, cell-by-cell transcription, column
  resize, our own localized context menu, autosave + explicit save; **OTSL generation**
  (`services/otsl.py`, round-trip tested against the official `otsl_to_html` oracle), per-block crop
  (`GET /api/blocks/{id}/crop`), per-block table (`GET/PUT /api/blocks/{id}/table`, OTSL in the PUT
  response), **conventions checklist** (`GET/PUT /api/projects/{id}/conventions`), **flow preview**
  of the reading order on the canvas.
  - **The sheet is not the source of truth.** The sheet library only displays and edits;
    `TableGrid` is translated both ways by pure, tested functions, `source`/`verified` and
    `vlines`/`hlines`/`row_columns`/`phantom_cols` pass through intact, and OTSL is generated by the
    server from our model.
  - **Structural changes are ours** (`lib/grid.ts`): the first sheet library (Jspreadsheet CE)
    destroyed every merge on row/column insertion, so inserting or removing tracks is computed by us
    and the sheet is remounted. The library was later replaced by Univer (M19); the rule stays.
- **M4 — Dataset builder** ✅ — `services/dataset_builder.py`: the JSONL families (full-page layout
  with **0–1000** coordinates and reading order, text_rec with crops + transcription, tables with
  OTSL, optional formulas), deterministic **split by page** (ratio + seed), crops on disk with absolute
  paths, full table crop + experimental logical bands only with verified `hlines`, sanity report saved
  in `dataset/report.json`. API: `POST/GET /api/projects/{id}/datasets[/build]`. Dataset page with
  split slider, seed, stats, warnings, files and JSONL row previews.
- **M5 — Training center** ✅ — `services/trainer.py`: script generation from the model's recipe
  (§10; `swift sft`, `llamafactory-cli` or `paddleformers-cli`) with `--val_dataset` and checkpoint
  resume, the run's data view (`training_views.py`),
  subprocess launch with a log file, metric parsing (loss/lr), GPU telemetry (nvidia-smi), stop,
  state persisted in `runs/<run_id>/{run.json,train.sh,train.log}`. API:
  `POST /api/projects/{id}/training/{start,stop}`, `GET …/status`, `GET …/stream` (SSE),
  `GET /api/system/gpu`. Training page: config wizard, loss chart, GPU cards, live log.
  - **Where to train is the first step of the form**, not the last: "This machine" is disabled with
    the reason when nvidia-smi finds no GPU, and Vast.ai instances running on the account are picked
    from a list (host and port filled in). `trainer.preflight()` distinguishes the executor: locally
    it checks repo, Python/conda, GPU and VRAM of *this* machine; with `ssh`/`vast`/`runpod` it asks
    for remote host and training repo and does not look at the local GPU. It used to check them
    always, so remote training from a Mac never started. The UI reads the preflight **live**
    (400 ms debounce) next to the button, which stays disabled while something blocks. On
    Vast/RunPod the default SSH key is the one Tabularium registers on the account; the host key is
    pinned at launch (TOFU, like the tunnel), and `UserKnownHostsFile` is quoted.
- **M6 — Evaluation & playground** ✅ — `services/inference.py` (OpenAI-compatible client: tolerant
  layout parsing, text/table recognition, ping), `services/evaluate.py` (metrics: layout IoU + labels,
  reading-order Levenshtein, text CER/WER, table TEDS + CER; report JSON in `eval/eval_<ts>/`). API:
  `POST /api/projects/{id}/evaluate`, `POST /api/playground/parse`. Pages: Evaluation (aggregate
  cards, per page, GT vs prediction overlay, page errors) and Playground (predicted layout as SVG
  overlay + blocks/content, copy as Markdown). Degrades with a warning if no model server is up.
  - **The truth of an evaluation is the approved pages only** (`approved`/`exported`), whatever the
    last dataset build says: a page with unverified drafts counted the model's output as the right
    answer. Without approved pages the API answers `no_gold_pages`. Evaluation **does not require a
    training run**: measuring the base model on one's corpus is the baseline fine-tuning must beat.
  - **Which pages are measured.** Until a project training run is `finished`, the model has seen no
    page, so **all** approved pages are measured (`report.scope = "all_approved"`): with only the val
    split an 11-page corpus measured 2. After the first finished run it goes back to the val split
    (`scope = "val"`), the only honest one.
- **M7 — Pseudo-labeling** ✅ — `services/ocr.py` (lazy RapidOCR/PaddleOCR import,
  `TABULARIUM_OCR_ENGINE`), `POST /api/projects/{id}/prelabel`: detects text lines on a page,
  filters by confidence/size + NMS-lite, inserts `Text` blocks with `prefill_source` and `order_idx`,
  replacement modes (§3). `BlockOut` exposes `prefill_source`. Model mode exposes both `two_stage` and
  the official `end2end`; valid END2END OTSL avoids a second call, otherwise the result declares the
  fallback on the table crop.
  - **Recognition sessions: payload size and review.** `GET …/recognition-runs/{id}` omits the raw
    per-page output (`result: null`) unless `?result_page=<page_id>`, which the Studio asks only for
    the open page; export still uses all of it. With every page's output a 200-page session weighed
    4.7 MB, re-read every 1.2 s by the Studio: now 60 KB. Results tells apart "completed" pages without
    blocks (a model that answers and finds nothing is not a success) and filters to verify / without
    blocks / with errors. The Studio keeps `?project=&page=` in the URL: a refresh reopens the same
    page. Model names in the UI come from `app/models/names.ts` (catalog name from the adapter), never
    from the served name.
- **M8 — Real use & polish** ✅ — frontend `dist` **served by the backend** (single process
  `scripts/run.sh`; `/assets` mount + SPA catch-all with deep links, API routes first), **lazy-loaded**
  pages (initial bundle −63%), `noUnusedLocals`/`noUnusedParameters` on, `build_frontend`/`run`
  scripts (bash + PowerShell).
- **M9 — Model, provider and destination** — the Models hub is the only place where model and
  provider are configured (Settings does not duplicate it: see `DESIGN.md` § Navigation). It is a
  **three-step path — model → where → configure** (`pages/ModelsHubPage.tsx` + `app/models/`), in the
  order the decision is made: each step shows only what it needs and the choice made stays written in
  its tab. State lives in the URL (`?modello=…&dove=…&passo=…`), so Back returns to the previous step;
  without parameters "Configure" reopens what is in use. Model ↔ destination compatibility is **a
  single pure function** (`app/models/registry.ts` → `destinationVerdict`, tested): selector,
  destinations and panels read the same answer, so no clickable option leads to a refusal and a model
  with no destination cannot be selected. The local step 3 is a checklist weights → server → check
  (`LocalSetup`); the remote one is the provider panel rendered in the page (`CloudControlModal
  inline onlyProvider=…`). On Vast.ai the panel is itself a list of four stages (account → GPU →
  server → connection) **derived from the real state**, not from clicks: after a refresh a preparation
  in progress resumes (`tabularium.vast.job` reminder + one read of the remote log per instance), and
  `provision_log` declares `adapter_id` because the log is one per instance — without it, one model's
  failure was attributed to the one just chosen. The rail exposes Recognize, Annotation, Results,
  Projects, Fine-tuning, Benchmark and Models; Fine-tuning has visible sub-sections Dataset →
  Training → Evaluation; Try a model lives next to configuration in Models. The active profile is the
  atomic source of truth (`compute_profiles`, DB v10+); `get_inference_config()` also exposes
  `provider`, `resource_id` and `source_profile_id`, and the client uses them so that `is_cloud` is
  not inferred from the URL (a Vast SSH tunnel listens on `localhost` but is cloud). Starting a
  recognition session probes the endpoint (localized 409 `model_endpoint_unreachable`) instead of
  queueing pages bound to fail; local VRAM is not invoked for remote runs; stopping with
  `disable_inference` stops the **identified** resource (`resource_id` + provider credential), never
  the first one found on the account.
- **M10 — Local compute depends on the hardware, and the app says so** — a single module decides,
  `services/hardware.py`: platform, memory (unified on Apple Silicon, VRAM on CUDA), and which serving
  runtimes the machine can host. `vllm` on Linux + CUDA (Windows via WSL2), `mlx-vlm` on Apple
  Silicon — where vLLM does not run at all, being CUDA-first. Every adapter declares
  `local_runtimes` and, when one exists, `local_mlx_repo` (plus `local_mlx_repo_choices` for published
  variants such as bf16/8-bit, chosen per model in Settings, each with its own weight factor
  `local_mlx_variant_weight_factors`; without a saved choice the largest variant that fits is served).
  The registry exposes for each model `local: {runnable, runtime, reason, mlx_repo,
  mlx_repo_choices}`, with **stable codes** the UI translates (`cuda_required`, `no_nvidia_gpu`,
  `apple_silicon_required`, `no_local_runtime`, `model_unsupported_locally`, `insufficient_memory`).
  Product consequence: on a Mac, PaddleOCR-VL has a verified MLX benchmark. Qwen3-VL passed MLX tests
  with the old prompt, but its prefill now follows the native `qwenvl html` parser and must be
  re-benchmarked on Metal (the old OTSL request produced invalid Markdown).
  - **Qwen3-VL on MLX: penalty windows must be raised.** The official Instruct-VL parameters
    (`temperature 0.7`, `top_p 0.8`, `top_k 20`, `repetition_penalty 1.0`, `presence_penalty 1.5`) are
    in `recommended_generation`, but `mlx-vlm` applies both penalties over a **20-token** window
    (`DEFAULT_REPETITION_CONTEXT_SIZE`): shorter than a register row (~25–40 tokens), so repetition
    stays invisible and generation runs to the cap (measured: an `index` page at 16384 tokens / 508 s,
    `finish_reason=length`). The adapter declares an MLX guard (`local_mlx_repetition_penalty` /
    `_context_size`, `local_mlx_presence_context_size`), applied per request in
    `inference._sampling_for` only on local Apple Silicon serving and adjustable in Settings (MLX
    section). The default is conservative and must be confirmed live: penalties reduce degenerate
    repetition but do **not** guarantee EOS from the 4-bit checkpoint on full-page requests.
  - **Which models are local, and why the others are not** — always a **measurement**, made by
    actually serving the model on a page. MinerU2.5 uses the vendor's isolated native `MinerUClient`
    MLX runtime (2.0.5), verified on corpus scans with a complete layout and valid HTML tables; dense
    tables are still to be evaluated against gold annotations. **Unlimited-OCR** runs locally since
    `mlx-vlm` 0.6.4 ships `unlimited_ocr` with `RingSlidingKVCache` (upstream R-SWA) — measured
    2026-10-13 on an M5 Pro / 24 GB with `mlx_vlm 0.7.1`: 265.6 tok/s, TTFT 1.2 s, 5.8 GB,
    grounded-markdown output with HTML `<table>`. Since the n-gram processor is still missing, its MLX
    recipe includes a repetition penalty with a 128-token window (without it generation loops: one
    line repeated 23 times), it is served by a dedicated server
    (`services/unlimited_ocr_mlx_server.py`, because `mlx_vlm.server` does not inject the image for
    this architecture), and on Macs up to 12 GB it starts with MLX's buffer cache off and clears it
    after each page (otherwise the GPU timed out on the first page). Remote only: **MonkeyOCRv2**
    (`mlx-vlm` has no such architecture), **DeepSeek-OCR-2** (its verified recipe relies on vLLM's
    n-gram logits processor), **dots.mocr** (END2END generation stops at 682 characters and the run
    fails), **GLM-OCR** (the run "succeeds" with **zero** blocks inserted — an empty success, worse than
    an error). **LightOnOCR-3-1B** is remote only because it has not been tried yet. The MLX column is
    a measurement, not a promise: a checkpoint that loads is not a path that works. Repeatable check:
    `scripts/e2e_local_inference.py`. The Apple runtime is prepared by Tabularium in
    `<root>/mlx-runtime` (`services/mlx_runtime.py`), like `<root>/vllm-runtime`. The MLX path does
    not go through the weights registry (`mlx-vlm` downloads from the HF cache) and the served name is
    the MLX checkpoint id. Matrix and measurements: `docs/guide/local-inference.md` §0.
  - **Recognition with a local model survives a dead server**: if the server dies mid-page, the
    session restarts it on the same port and retries that page once.
  - **PaddleOCR-VL's official path has a second runtime.** Its Paddle pipeline (PP-DocLayout +
    recognition) lives in `<root>/paddle-runtime`, installed by itself the first time the model is
    served. The installer is platform-aware: on Linux `paddlepaddle-gpu` from the CUDA index, on Apple
    Silicon the arm64 CPU wheel from PyPI.
  - **An installation's state is written after creating the venv.** `EnvBuilder(clear=True)` empties
    the folder: writing `.install_state.json` and the log before made them disappear, and during the
    minutes of installation the UI read `absent` with an empty log. This applies to all three runtimes
    (`local_runtime`, `mlx_runtime`, `paddle_runtime`).
  - **SSH to Vast.ai: two routes, never mixed.** An instance exposes the machine IP (`public_ipaddr` +
    `ports['22/tcp'][0].HostPort`, the console's "Direct SSH Connect" line) and the `ssh*.vast.ai`
    forwarders (`ssh_host`/`ssh_port`). `_vast_ssh_endpoint()` prefers the direct route — like
    `vastai ssh-url --direct` — and falls back to the proxy only with the complete pair: mixing one
    route's host with the other's port produces a non-existent endpoint. Vast.ai can keep
    `public_ipaddr` at `null` even on a running, reachable instance (verified on instance 50434880 on
    the v0 detail and v1 list routes; then not even the official CLI can build the direct URL, and no
    route available to renters publishes the address). The Vast panel therefore has a **host/port
    override**, prefilled with the port-22 mapping the API does publish: the user only pastes the IP
    from the console.
  - **SSH preflight before handing anything over** (`check_ssh_access`/`ensure_ssh_access`,
    `POST /api/system/cloud/vast/ssh-check`): opens a bare connection (`ssh … true`) and names the
    cause of failure — key refused, host key changed, host unresolvable, port silent. A refused key
    answers **409**, no longer a generic 502: it used to be indistinguishable from a provisioning error
    and diagnosing it cost a whole rental.
- **M11 — Live collaboration (first wave)** — real-time presence and resync on annotation, like a
  collaborative editor, without touching the data model.
  - **The WebSocket is push only, not a second write path.** Annotations are still written **only**
    through REST (`PUT /pages/{id}/annotations`, `PATCH/DELETE /blocks/{id}`, `PUT /blocks/{id}/table`):
    validation, audit and optimistic revision stay there. The channel (`services/live.py` → `LiveHub`,
    `api/live.py` → `WS /api/live/pages/{page_id}`) does two things: say who is on the page and tell
    the *others* it changed.
  - **Presence**: each connection carries `conn_id`, user and a stable color (derived from identity).
    The Studio shows colleagues' avatars and "{name} is editing…" while someone has unsaved changes
    (`editing`). Authentication uses the session cookie; project access is checked before joining the
    room (missing/foreign page → close code 4404, like the REST 404). Viewers join read-only.
  - **Resync**: after every REST write the endpoint publishes `annotations_changed` with the new
    revision and the author. The client ignores the **echo of its own** change (matching revision)
    and, if it has no dirty work, adopts the server version without a reload (`adoptRemote` on
    `useAnnotationState`, which resets history: merging two histories is a CRDT's job, not this
    layer's). With unsaved changes **nothing is discarded**: a notice says who changed what, with a
    "Load the remote version" button.
  - **One process.** The hub lives in memory: valid for the supported scenario (one dashboard
    process). Several workers would need an external broker — the boundary is `LiveHub.publish`, which
    synchronous endpoints (worker threads) call thread-safely.
  - The dev proxy (`vite.config.ts`) forwards WebSockets (`ws: true`); in production the channel is
    same-origin behind the same CSP `connect-src 'self'`.
  - Tests: `backend/tests/test_live.py`; `frontend/src/app/live.test.ts`.
- **M12 — Drive-style sharing** — a single surface for project access.
  - **"Share" is a dialog, not a form at the bottom of the page.** `app/ShareDialog.tsx` reuses the
    existing APIs (`members`, `member-candidates`, `owner`, `activity`): add people by name/email,
    Editor/Viewer roles, removal with confirmation, ownership transfer. Two tabs: **People** (avatars
    with initials and a stable color, as in the Studio) and **Activity**.
  - **Reuse, not a new permission model.** The backend stays the source of truth: whoever is neither
    owner nor admin sees the list read-only, and the server rejects writes with 403 anyway.
  - **Shared avatars**: `lib/avatar.ts` (`initialsOf`, `avatarColor`) is the single source of initials
    and color; the palette is `services/live.py`'s, so a user has the same color everywhere.
  - **One activity feed** (`app/ActivityFeed.tsx`) for the project page and the dialog.
  - Tests: `frontend/src/app/ShareDialog.test.tsx`.
- **M13 — Onboarding and profile** — invite links, forced password change at first login, profile.
  - **Invite links** (`POST /api/users` with `invite=true`, `POST /api/users/{id}/invite`): the admin
    creates the account without knowing its password; the server generates a token (stored **only as
    a hash** in `users.invite_token_hash`, with `invite_expires_at`, 72 h TTL) and returns the link
    once. The invitee opens `/invito?token=…` (`AcceptInvitePage`), chooses a password and stays
    logged in (`GET /api/auth/invite/{token}` exposes the state; `POST /api/auth/invite/accept`
    consumes the single-use token, activates the account and invalidates sessions).
  - **Password to change at first login**: `users.must_change_password` is true for accounts created
    by an admin and after a reset. `get_current_user` **blocks** every route except `/auth/me`,
    `/auth/change-password`, `/auth/logout` (403) until the user picks their own; `AuthGate` mounts
    `SetPasswordPage` before the app. `set_password(..., must_change=False)` is the only place that
    clears the flag.
  - **Profile**: `PATCH /api/auth/me` updates one's email (the username is immutable). No profile in
    local mode.
  - **Migration 18** (additive): the three columns on `users`.
  - Tests: `backend/tests/test_user_onboarding.py`.
- **M14 — Clear paths** — Projects and Fine-tuning are global entry points; sub-sections stay visible
  and keep the parent section active. Creating a project opens it and makes it active:
  `ProjectCreate.archive_dir` is optional (`null` for browser import), with import of single
  images/PDFs or a folder. Only projects linked to a source folder expose the server scan. Protocol,
  activity and pilot stay collapsed so import and the page registry stay in view. `datasetUsable`
  excludes empty exports; training and evaluation depend on their real prerequisites, not on
  completing the whole archive. "Try a model" is named consistently in every language, shows no
  endpoint errors when local OCR is used, and resets its result on page change.
  Check: `docs/UX_WORKFLOW_AUDIT.md`.
- **M15 — Orientation and usability (Mobbin references)** — the Fine-tuning rail item opens
  `/fine-tuning`: an overview with the next step derived from workflow, dataset and training state;
  tools stay reachable and read errors are not presented as missing data. Projects shows list and
  search first; `?nuovo=1` opens the creation dialog. `PageHeader` no longer truncates descriptions.
  Modals keep focus stable across renders and support `data-autofocus`. References and choices:
  `docs/UX_MOBBIN_REVIEW.md`.
- **M16 — Closed models through a gateway: Gemini 4 Argon** — a model without public weights is an
  external endpoint, not a download. The `gemini-argon` adapter declares Google's `generateContent`
  protocol (`chat_protocol`), reached through the OpenCode gateway (Zen/Go) or a Google endpoint;
  `services/opencode_gateway.py` reads the key from opencode's store
  (`~/.local/share/opencode/auth.json`, provider `opencode-go`/`opencode`) when the vault has none,
  with `TABULARIUM_OPENCODE_KEY`/`TABULARIUM_OPENCODE_AUTH` overrides. `VllmClient` routes the request
  to the `generateContent` branch (mandatory `x-opencode-session` header), leaving prompts, parsing and
  everything downstream unchanged. The verified path is **END2END**: a prompt with the project
  taxonomy and constrained OTSL returns, in one generation, 0–1000 bboxes, labels and content —
  masthead (`Title`), number (`Issue-number`), date (`Issue-date`) **and** the table in OTSL (measured
  live: 4 elements on an `index` page). It is therefore the native workflow (`native_mode` →
  `end2end`) and **works in prefill/Recognize** (`supports_native`) and the Playground. The Table
  prompt **constrains the canonical tokens** (`<fcel>/<ecel>/<lcel>/<ucel>/<xcel>/<nl>`, no closing
  tags): without that, the model emits `<ched>/<splt>/<uched>`, which `otsl.otsl_to_grid` does not
  recognize (measured live). Provider "opencode" is stored as `custom` in the profile (the
  `compute_profiles` `CHECK` does not allow it: it was a 500), and changing adapter resets the global
  pixel cap, a leftover of the MonkeyOCRv2 recipe. Gateway availability is a `generateContent`
  **without a body** (≈0.7 s): Argon "thinks" even for one token (2–20 s), so a minimal generation as
  a probe made `available` flicker and disabled starting recognition. Not trainable
  (`training_types` empty) and not downloadable (`hf_repo` empty).
  Tests: `test_opencode_gateway.py`, `test_model_registry.py`, `test_prelabel.py`,
  `test_recognition.py`, `frontend/src/app/models/registry.test.ts`.
- **M17 — Multi-model benchmark on gold pages** — Evaluation (M6) measures one model, the active
  one. The benchmark measures **N models on the same pages**, to answer "which model — or which
  checkpoint — is best *on my corpus*" and to show the fine-tuning delta.
  - **Gold = approved/exported pages**, not a new state: the truth of an evaluation stays the human
    annotation. A "perfect" page is an approved page.
  - **The reference is human annotation, never another model's output.** Comparing with Argon
    measures agreement with Argon, not correctness.
  - **Metrics reused from `services/evaluate.py`** (per-label IoU at 0.5 and `layout_strict` 0.75,
    reading-order Levenshtein, TEDS, CER/WER), invoked once per model on the same pages. The benchmark
    composes metrics, it does not reimplement them.
  - **Comparison goes through the native → canonical bridge** inside `evaluate._evaluate_page`:
    without it, a model speaking its own names (`header`, `table`) scored zero against canonical
    truth. The cache is versioned (`_CACHE_VERSION`), so a change in measurement logic invalidates it.
  - **Ephemeral.** Reports in `eval/bench_<ts>/{report.json,report.html}`; no block or grid is
    written to the DB. A benchmark can neither pollute the corpus nor become training data by mistake.
  - **Cache per (adapter_id, model, page_id, annotation_revision)** on disk: a refresh does not pay
    again for calls already made. The report declares latency, tokens and **estimated cost** per model
    — remote models are paid, and the cost must be readable before running again.
  - **Candidate orchestration.** A list of endpoints, not only the active profile. Local models are
    **started and stopped by the benchmark itself**, one at a time, on a dedicated port
    (`_start_local`): it **does not go through `serve_manager`**, which would change the active
    inference profile and break a recognition run in progress. If the given local endpoint already
    answers, it is reused. The started server's pid is stored (`serve_pid`) and
    `reconcile_benchmarks()` stops it at startup after a restart, so no orphans remain.
  - **Background execution with persisted state.** `POST …/benchmark` starts a thread and answers at
    once; `GET …/benchmark/status` (state on disk in `eval/bench_state.json`) reports
    `running`/`done`/`error` with `index`/`total`/`current_adapter`. A page refresh finds the state
    and follows it if running.
  - **Official-pipeline adapters are measured too** (Qwen3-VL, PaddleOCR-VL, MinerU2.5, GLM-OCR,
    TeleOCR) through the vendor pipeline used by prefill (`prefill.native_page_items`).
  - **The path in use is measured** — the adapter's prompts and parsers, what Tabularium would really
    use. `scripts/benchmark_models.py` stays the per-phase diagnostic tool.
  - **Its own page**, `/benchmark` (rail item): pick **models** (ticks, each with a **Local/Remote**
    badge, endpoint and served name editable) and **pages** (selectable only if every block is
    confirmed).
  - **Bulk approval** (`POST /api/projects/{id}/pages/approve`): approves all (or selected) pages.
    Without `confirm_drafts` it skips pages with unverified drafts; with the flag it confirms them — an
    explicit choice, because it makes the model its own teacher.
  - **Results as they come and a "tables only" mode.** Each finished model is written to the state
    (`results`); progress is per model **and per page** (`page_index`/`page_total`). `table_only`
    measures only table structure, ignoring layout and labels.
  - API: `GET /api/projects/{id}/benchmark/pages`, `POST /api/projects/{id}/benchmark`
    (admin/editor) with `{targets[], page_ids?, limit, with_text}`, `GET …/benchmark/latest`.
    Tests: `tests/test_benchmark.py`.
- **M18 — Explicit project access and team reading** — `ProjectOut.access` declares effective role,
  source and `can_edit`/`can_manage`, computed in the same connection as the read/creation. The
  global role does not replace the member role. The list separates own/shared projects, owner and
  active people; sharing requires person, role and confirmation, with viewer as default.
  `AnnotationEntry` mounts the editor only for those who can write; `ProjectReader` shows scans,
  blocks and tables without autosave. `TableEntry` resolves its context from the protected
  `GET /api/blocks/{id}`, never from the query string. `PageHeader` puts explanations under a
  collapsed Guide; menus have no decorative waits. Details and limits: `docs/UX_TEAM_WORKSPACE.md`.
- **M19 — Studio, assignments and review hand-off** — Migration 19 `page_work`: distinct annotator
  and reviewer, assignment by owner/admin without granting access, states
  annotation/pending/changes_requested/approved and optimistic versions. Submission and review check
  the current revision; an assigned approval does not go through the legacy endpoints. A later edit
  invalidates the approval. `PageWorkControl` and personal filters in the sidebar make the work
  visible; live updates on state changes. Canvas/draft/table saves are serialized, a failed flush
  blocks page/project changes, direct writes send the expected revision. View selector always
  reachable, Recognize visible, secondary commands under Options. The table workspace moves to
  **Univer**, which serializes only data mutations; duplicated native structural commands are hidden.
  Inventory and limits: `docs/UX_STUDIO_REVIEW.md`. No global reviewer role or page lock.
- **M20 — Training every model with its official recipe** — Tabularium must be able to train
  **every open-weight model** following that model's **official documentation and best practices**,
  not only MonkeyOCRv2. Closed models (Gemini 4 Argon) have no weights and are out of scope —
  `training_types` empty already says so.
  - **A registry, not scattered scripts.** `services/train_recipes.py` is the twin of
    `serve_recipes.py`: one recipe per adapter declares `toolchain` (ms-swift, llama-factory,
    paddleformers, mineru-sft, none), the official documentation, allowed types (`lora`/`full`),
    the **data view** the dataset builder must produce and the maturity. The training center reads
    the selected adapter's recipe; it does not know MonkeyOCRv2 by name.
  - **Recipe discipline (as for serving and evaluation).** A recipe is `verified` **only after a
    documented real run**; otherwise `experimental`. No recipe without official documentation behind
    it. The status matrix lives in `docs/guide/training.md`.
  - **Current state (to verify, not to assume).** The toolchain is the one the vendor documents:
    MonkeyOCRv2 uses its bundled ms-swift fork; Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2 and dots.ocr
    use upstream ms-swift (`qwen3_vl`, `unlimited_ocr`, `deepseek_ocr2`, `dots_ocr`); GLM-OCR uses
    LLaMA-Factory (`template: glm_ocr`); PaddleOCR-VL uses PaddleFormers (full and LoRA). MinerU2.5
    forbids `swift sft` and its config ships only in `mineru_sft_code.zip`, so it is `planned`.
    TeleOCR and LightOnOCR-3 ship inference only and are `inference-only`. Only MonkeyOCRv2 is
    `verified` (generator/dispatch, not yet a GPU run).
  - **Fine-tuning starts from benchmark data.** The benchmark (M17) says *where* the gap is: here
    the **taxonomy** (custom classes are not emitted: `Issue-date` 0/10, `Title` 0/3) and **table
    structure** (low TEDS), not reading (CER 0.006). The training recipe chooses accordingly.
  - **GPU provisioning for training.** `scripts/cloud/setup_cloud_train.sh` prepares a CUDA host
    (venv, ms-swift, MonkeyOCRv2 clone) and writes a `$ROOT/.train_ready` marker: "prepared" is a fact
    on disk, not an assumption. On Vast.ai renting runs it via `prepare_training` (onstart, no HTTP
    port), distinct from `prepare_server` (serving).
  - Tests: `tests/test_train_recipes.py`, `tests/test_training_views.py`,
    `GET /api/system/train-recipes`.
- **M21 — Distribution and public documentation** — Tabularium is installable by researchers who
  are not developers, and documented in English.
  - **`uv tool install tabularium`** (PyPI) installs a wheel that bundles the prebuilt interface and
    every runtime resource (§4 Distribution); uv provides a compatible Python. Data defaults to
    `~/Tabularium`, outside the package, so upgrades never touch it.
  - **Releases** (`.github/workflows/release.yml`): a `vX.Y.Z` tag matching `VERSION` builds the
    frontend, the wheel and source archives with the frontend prebuilt (a `frontend/dist/.prebuilt`
    marker stops `run.sh`/`run.ps1` from rebuilding), installs and starts the wheel as a check,
    publishes the GitHub release with the CHANGELOG section as notes, and publishes to PyPI through
    trusted publishing when the repository variable `PYPI_PUBLISH` is `true`.
  - **Documentation site** (MkDocs Material, `docs/` → GitHub Pages via `docs.yml`, built with
    `--strict`): installation, configuration, supported models, local inference, cloud GPUs,
    fine-tuning, teams, troubleshooting, development. Public documentation is English only; internal
    notes (`docs/UX_*.md`) are excluded from the site.
  - Tests: `tests/test_packaging.py` (dependencies and bundled resources stay in sync).

Every milestone ends with its checks: what to run to test it (see §13).

---

## 13. Operating rules for agents

1. **Language**: everything new is written in **English** — documentation, README, CHANGELOG,
   commit messages, code comments, identifiers and logs. Existing Italian comments are translated
   when the surrounding code is touched, not in bulk. The UI stays multilingual (rule 11).
2. **Never modify** the official repositories (`Yuliang-Liu/MonkeyOCRv2` and the other models'):
   reference them as external checkouts configurable in `config.py` (or cloned by Tabularium into
   `<root>/vendor/`); generate scripts from recipes, never vendor them.
3. **Check against the official sources**: 0–1000 coordinates, §2.3 prompts, §2.5 OTSL, §2.2 JSONL —
   and each model's own documentation for its recipe. When in doubt, re-read `core_runner.py`,
   `parsing/train/README.md`, `scripts/*.sh` of the official repository.
4. Work **in milestone order**; every change must update `AGENTS.md`, the README, the documentation
   site and `CHANGELOG.md` (under `[Unreleased]`) when conventions, structure or user-facing behaviour
   change.
5. **Tests**: backend with pytest (special care for `otsl.py`, `dataset_builder.py`, coordinate
   normalization, migrations, permissions); frontend with `tsc --noEmit` + `vite build` without errors
   and Vitest (unit + components with jsdom/testing-library for risky flows: autosave/page change,
   prefill confirmation). CI (`.github/workflows/ci.yml`) runs backend tests on Python 3.11–3.13, a
   macOS pass, frontend typecheck + tests + build, the browser end-to-end workflow
   (`scripts/e2e_workflow.py`), and checks that the build leaves no tracked artifacts. Keep the e2e
   test in step with UI changes.
6. **Running**: installed, `tabularium`; from source, `scripts/run.sh` (single process on port 8787)
   or `uvicorn app.main:app --port 8787` from `backend/` with `vite` as dev server proxying the backend.
   The app must NEVER depend on a training run being active.
7. **Paths**: never hard-coded; everything from `config.py` (`TABULARIUM_ROOT` for data,
   `config.RESOURCE_DIR` for files shipped with the app — §4 Distribution).
8. **Data safety**: user projects are real data; no automatic destruction of data; every destructive
   operation requires explicit confirmation in the UI and in the code.
9. Table merges/coordinates must be validated against the official `otsl_to_html` algorithm as the
   oracle in tests.
10. Training run logs and metrics live in `<root>/projects/<project>/runs/<run_id>/` (JSONL + .log +
    plot data), so the UI can replay a run after it ends.
11. **Localization (mandatory)**: the interface is multilingual **it/en/fr**. No hard-coded UI
    string: every text goes through the i18n dictionaries (`frontend/src/i18n/it.ts` is the source of
    truth of the structure; `en.ts`/`fr.ts` are identical in shape, typed as `Dict`). `{var}`
    placeholders identical in the three languages; plurals as `{one, other}` (French uses the singular
    for 0 and 1). The language switch is in the rail (persisted in
    `localStorage['tabularium.locale']`). The backend localizes messages that reach the UI
    (HTTPException details, dataset/eval warnings, readiness errors, queue reasons, training
    preflight) via `Accept-Language` and `backend/app/services/i18n.py` (default Italian;
    `localize_detail` recognizes existing details from the reverse catalog). The API client always
    sends `Accept-Language: <locale>`. Backend codes and logs stay English.
12. **Auth as defence, not decoration**: every new route is protected by its path's
    `require_resource` (or an explicit check when the project id is in the body). Rules never to be
    left to the client: no self-demotion/self-deactivation, no deleting the last admin, no password
    reset of one's own account, 404 (not 403) for other people's projects. Passwords and tokens only
    hashed in the DB; sessions expire; `TABULARIUM_AUTH` is `off` **only** for tests.
13. **Releases**: bump with `python3 scripts/version.py bump {patch|minor|major}`, move the
    `[Unreleased]` notes under the new version in `CHANGELOG.md`, commit, wait for green CI, then push
    the `vX.Y.Z` tag. The release workflow does the rest (GitHub release + PyPI). Never publish a
    release whose README describes features that are not in it.

---

## 14. References

- Model repository: https://github.com/Yuliang-Liu/MonkeyOCRv2
- Parsing fine-tuning: https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/parsing/train (README + scripts)
- Understanding fine-tuning: https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/understanding/train
- Parsing pipeline (coordinates/prompts/OTSL): `parsing/core_runner.py`, `parsing/parse.py`
- Models: https://huggingface.co/zenosai/MonkeyOCRv2-B-Parsing · `-S-Parsing`
- Paper: https://arxiv.org/abs/2607.11562
- Tabularium documentation: https://nbadino.github.io/tabularium/
