# 2. Official technical context (verified facts — do NOT question without proof)

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

These facts describe the reference model, MonkeyOCRv2. Other models declare their own
conventions in their spec (`backend/app/models/<id>.py`) — serving recipe, training
block and label map — plus their adapter behaviour in `services/model_adapters.py`.

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
| `warnings` | Stable codes, never prose: the backend speaks English, the UI translates (the localisation rule, `AGENTS.md` R11). Today only `skewed`. |
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
tested in `domain/vram.py`:

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
