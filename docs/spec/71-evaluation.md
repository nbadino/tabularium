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
requeues only the failed or cancelled outputs of that run. A model that fails
`STOP_AFTER_SAME_ERRORS` (3) pages in a row with the same error (numbers ignored: request ids,
times) is stopped: its remaining pages are marked failed at once with `consensus_model_stopped`
("out of credits or invalid key?") instead of each costing a request; a retry sends them again.
Stored errors are Italian catalog messages, localized on read (`run_pages`, `page_comparison`).
The Recognition page shows each model's progress (pages read, failed) from the run's pages.

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
  Needleman–Wunsch on row text (gap cost 0.5), then columns on the aligned cells; then each
  pair of rows cell by cell (`_align_row`), the table-wide column map only breaking ties
  (+0.05 off it; two gaps cost a little more than one total misreading). A model's grid can be
  ragged (OCRia opens an empty column half-way down a Lloyd's page): one map for the whole
  table compared the year with the tonnage from there on. Two more moves join cells, only when
  the join explains the text exactly: one cell against two joined (`Br *` against `Br` | `*`)
  or two against one that cuts between two words into exactly the two; such cells need the
  `cells` rule (a table-only convention, `RULES`). The filled cells nothing took in a row
  both models have count against the table; an empty column does not; each reference
  cell scores the mean distance to the other models' aligned cell (1 if unaligned); rows or
  columns only another model has count as disagreeing cells. The table score is the cluster's
  `text` channel.
- **Kinds of difference** (`classify_difference`). Two readings can differ only in how the same
  print is written down. A difference is `convention` only when the named rules explain *all* of
  it: `dash` (any dash or minus → `-`), `quotes` (curly → straight), `leaders` (two or more dots,
  middle dots or an ellipsis), `spacing` (all whitespace), `ligature` (æ œ ﬁ ﬂ ß), `small_caps`
  (ʀ ɪ ı ᴠ … → capitals), `case`; it lists only the rules it cannot do without. Otherwise it is
  `digits` when the digit sequences differ (also when one side is empty or has no aligned cell:
  a dropped year is a number, not a blank), `missing` when one side is empty, else `text`. Over
  several models a cell takes the worst pairwise kind, in the order digits > text > missing >
  convention > same, and the union of the rules.
- **Distances set the conventions aside**: `text`, the table cell `score` and the row/column
  alignment use `content_distance` (the edit distance after every rule). The distance with the
  conventions counted is kept as `text_raw` and `raw_score`. Each cluster has `text_kind` and
  `conventions`; each table counts its cells by kind (`kinds`) and by rule (`conventions`); the
  page sums the rules (`comparison.conventions`, table cells one by one, other blocks once).
  On Lloyd's Daily Index (1 April 1920, p. 3, OCRia-1 against Gemini 4 Argon) 64 of the 105
  differing cells were conventions; the 26 `digits` cells were the real errors (a year dropped
  on every row with an empty *Reg*, continuation lines on the wrong row).
- **Page agreement** = 1 − the area-weighted mean cluster score.
- **Run pages** (`GET …/consensus-runs/{id}/pages`, `services/consensus.py::run_pages`): each
  page's file, every model's state and error and, once two models have read it, the agreement
  and the open cells by kind (a table's `digits`/`text`/`missing` cells, a missing block, a
  block whose text differs); cached by the outputs' end times. The Recognition page lists them
  with a link to each page.
- **A table only the models read** (`POST …/consensus-runs/{id}/adopt-block`, `adopt_block`)
  goes in as one unverified draft with its grid, nothing else on the page touched, so the table
  editor can open it; the page view offers it as *Add <model>'s table*. With fewer than two finished
  models there is no comparison (`comparison: null`), not an invented agreement.

The Studio colours a score below 0.1 as agreement, below 0.35 as partial, above as disagreement.

**Verdict** (`studio/consensus.ts::verdictOf`), which decides colour and wording: `missing` when
a model has no block there; `text` when a number differs (`text_kind` `digits`, red however
short), when a table has a `text` or `missing` cell or a row or column only one model has, or
when the text channel is ≥ 0.1 (red or amber by its band); `structure` when the words agree
but the class, the segmentation or the position (layout ≥ 0.35) differs (grey: a choice, not a
misreading); `convention` when the text differs only by convention rules (light grey, not
counted as open: it is settled by its rule, not block by block); otherwise `agree`. The floors
of the score (0.3 for a split, 0.5 for a label) order the clusters and weigh the page agreement
but are never shown as a percentage of difference. *Next* goes through `text` (numbers first),
then `missing`, then `structure`. A table cell is coloured by its kind (`cellBand`): `digits`
and `missing` red, `text` amber or red by its distance, `convention` grey; *Next cell* and the
table editor's painted cells cover `digits`, `text` and `missing` only, in that order.

**Settling a block in the Studio** (`studio/consensus.ts`, `AnnotationPage.tsx`; each choice is a
shared decision, below). The user's
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
It does not wait for the run to end: while it is going, only the pages the model has already
read are written, and repeating it at the end writes the rest.

**In the table editor** (`POST /api/blocks/{id}/consensus` with the editor's current grid,
`domain/consensus.py::annotate_grid`) the user's grid is the reference: each finished model's
Table block overlapping it best (IoU ≥ 0.3) is aligned onto it, and a cell's score is how much
the models disagree *with each other* (mean pairwise distance, 1 between a reading and none),
not with the user; a cell no model has scores none. Each cell carries its `kind` and
`conventions` as above; a convention-only cell is not painted, its readings still show.
The editor's consensus bar counts the open cells by kind (numbers, words, missing; the
convention-only cells apart) and works through them: *Next cell* (Alt+N,
`studio/consensus.ts::nextDisputedCell`) selects the next open disputed cell, numbers first;
*My reading is right* (Alt+V) marks the selected cell verified and moves on (never while a
cell is being typed in, never an empty cell). The selected disputed cell's row is framed on
the table's scan as an **estimated** band (`estimateRowBand`): the models give no cell
geometry, so the row is placed by its index with 4 % of the rows (at least two) either side,
full width, since blank and continuation lines make the proportion drift by a few rows and a
column cannot be placed from the text; the frame says so (table view only, not the whole
page). Open `digits` cells where the user's reading is a number one model read too and the
other models left empty are grouped by column (`confirmGroups`, two cells or more): the group
can be visited or confirmed at once after a confirmation that names the column, the count and
the models (one undo step). On the right click a model that read nothing is offered last, as
*Empty the cell*. The `spacing` rule keeps a space between two digits: `1523` and `15 23`
are a `digits` difference. The sheet paints partial and disagreeing
cells and shows the readings on hover.

**Shared decisions** (`consensus_decisions`, `services/consensus_decisions.py`). Every choice made
while working through a comparison is recorded on the server with who, when, the value before
and after, and an optional comment, so another person on the page (or the same person on another
device) sees it. The Studio records `resolved`/`reopened` (*Fine as it is*, A), `use_model` (an
area: the user's and the model's blocks; a table cell: the cell before and the model's text,
from the right-click choice), `removed` (X), `confirm_mine` (Alt+V) and `group_confirm` through
`POST /api/pages/{id}/consensus/decisions` (write access); the adopt endpoints record `add_table`
and `adopt_page` themselves. Nothing is kept in the browser: the settled areas are derived from
the decisions (the last standing decision on an area with `after.resolved` says whether it is
settled) and come with the page's comparison (`resolved` in `GET /api/pages/{id}/consensus`) and
with the history (`GET /api/pages/{id}/consensus/decisions`, newest first, read access). A comment
is a decision of kind `comment`, on the page or `about_id` another decision.

*Revert* (`POST …/consensus/decisions/{id}/revert`, optional comment) puts the value before back
and is a decision itself (kind `revert`, before and after swapped, `about_id` the decision), so it
can be reverted in turn; a decision is reverted once (409 after). It is refused (409) when the
value after is no longer there — the area was reopened, the cell's text or verified flag, or the
block's text, changed since — rather than overwrite later work (R9). Revertible: settling and
reopening, a model's reading put into one block in place, every cell decision, and an added
table (deleted again only while no cell is verified or typed in and the block is not confirmed).
Not revertible from the history: `adopt_page`, `removed` and a model's blocks swapped for the
user's (the before value is not one the server can put back); they stay recorded and commentable.
A revert that changes the annotations advances the page revision. Every decision is announced on
the page's live channel as `consensus_decision` (the histories reload); a revert that changed the
annotations also sends `annotations_changed` (action `consensus_revert`), so open editors reload.
The history panel sits at the foot of the comparison in the content rail (`ConsensusHistory.tsx`):
the revert asks first, inline, with an optional reason. The audit events `consensus.adopted` and
`consensus.block_adopted` are still written.

---
