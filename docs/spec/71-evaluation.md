# 11. Evaluation

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

Table TEDS uses APTED tree edit distance on canonical OTSL table/row/cell trees, with
PubTabNet's tag/span/text edit costs and descendant-node normalization
(`domain/table_similarity.py`). The old row-aligned score is retained only as
`row_aligned_similarity`, never presented as TEDS. Exact CER/WER uses RapidFuzz Levenshtein
so full registers do not spend quadratic time in Python loops.

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

## Consensus between models

A consensus run (`services/consensus.py`) sends the same pages to two to six saved models
(inactive `compute_profiles`) through the normal prefill pipeline with a read-only writer
(`adapters/blocks.py::ReadOnlyPageWriter`): no block, grid or revision is written and the page
master is never rotated. Each model's blocks go to `consensus_outputs`. One model failing does
not stop the others; the run ends `finished_with_errors`. *Retry* (`POST …/consensus-runs/{id}/retry`)
requeues only the failed or cancelled outputs of that run.

The metric is pure (`domain/consensus.py`), pinned by `tests/test_consensus.py`:

- **Matching**: blocks of different models pair at IoU ≥ 0.5, best overlaps first, one block per
  model per cluster. A block left alone that lies ≥ 70 % inside another model's block joins that
  cluster as a *part* (also when the block already matched there is itself such a piece): the
  models read the area but segmented it differently (`split`). Without this, an advert read as
  one block by one model and as Title + Text pieces by the other showed every piece as missing.
- **Channels** per cluster: `text` = mean pairwise normalised character edit distance (NFC,
  whitespace collapsed, markdown `#`/`*`/`_`/`` ` `` decoration removed); for a split cluster
  the words are sorted first (`words_distance`), since pieces joined in reading order can
  interleave columns another model read one after the other; `layout` = mean pairwise
  1 − IoU; `label_agree`.
- **Score** = max(text, layout, 0.5 if the labels differ); for a split cluster max(text,
  layout, 0.3), with each model's parts joined in reading order for `text` and their union for
  `layout`, labels not compared; 1 when some model has no block there.
- **Tables** are compared on their grids, not their raw content: rows are aligned by
  Needleman–Wunsch on row text (gap cost 0.5), then columns on the aligned cells; each reference
  cell scores the mean distance to the other models' aligned cell (1 if unaligned); rows or
  columns only another model has count as disagreeing cells. The table score is the cluster's
  `text` channel.
- **Page agreement** = 1 − the area-weighted mean cluster score. With fewer than two finished
  models there is no comparison (`comparison: null`), not an invented agreement.

The Studio colours a score below 0.1 as agreement, below 0.35 as partial, above as disagreement.

**Verdict** (`studio/consensus.ts::verdictOf`), which decides colour and wording: `missing` when
a model has no block there; `text` when the text channel is ≥ 0.1 (red or amber by its band);
`structure` when the words agree but the class, the segmentation or the position (layout ≥ 0.35)
differs (grey: a choice, not a misreading); otherwise `agree`. The floors of the score (0.3 for
a split, 0.5 for a label) order the clusters and weigh the page agreement but are never shown
as a percentage of difference. *Next* goes through `text`, then `missing`, then `structure`.

**Settling a block in the Studio** (`studio/consensus.ts`, `AnnotationPage.tsx`). The user's
blocks and unverified drafts that read a cluster's area are found with the same thresholds as
the matching (IoU ≥ 0.5, or ≥ 70 % of the user's block inside the cluster; a block that merely
contains the cluster, a whole Column, is not the user's reading of it). *Use <model>* with one
block on each side writes the model's text and class and keeps the user's box; otherwise the
model's blocks replace the user's in that area. Canvas changes go through
`useAnnotationState.applyReading`, one undo step; drafts are patched or rejected on the server.
Tables are not replaced from the page view: their cells are settled in the table editor.

**Using a model as the base** (`POST …/consensus-runs/{id}/adopt`, `services/consensus.py::adopt_output`)
writes the model's stored blocks of the run, grids included, as unverified drafts with the
model's `prefill_source`, as a prefill would: the drafts already on the page are replaced
(`replace_drafts`). A page with blocks a person drew or verified (`prefill_source IS NULL OR
confirmed=1`) is skipped as `human_work`, unless `replace_human` asks to replace everything
(`replace_all`); a page the model has no finished output for is `no_result`. The coordinates are
those of the current page master: a consensus run never rotates it. The revision advances, so an
approval lapses and an open Studio reloads (live `annotations_changed`, action `consensus`).

**In the table editor** (`POST /api/blocks/{id}/consensus` with the editor's current grid,
`domain/consensus.py::annotate_grid`) the user's grid is the reference: each finished model's
Table block overlapping it best (IoU ≥ 0.3) is aligned onto it, and a cell's score is how much
the models disagree *with each other* (mean pairwise distance, 1 between a reading and none),
not with the user; a cell no model has scores none. The sheet paints partial and disagreeing
cells and shows the readings on hover.

---
