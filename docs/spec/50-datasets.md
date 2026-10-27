# 7. Dataset export — JSONL families

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

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
