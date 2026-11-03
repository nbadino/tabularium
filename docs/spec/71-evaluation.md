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
not stop the others; the run ends `finished_with_errors`.

The metric is pure (`domain/consensus.py`), pinned by `tests/test_consensus.py`:

- **Matching**: blocks of different models pair at IoU ≥ 0.5, best overlaps first, one block per
  model per cluster.
- **Channels** per cluster: `text` = mean pairwise normalised character edit distance (NFC,
  whitespace collapsed, markdown `#`/`*`/`_`/`` ` `` decoration removed); `layout` = mean pairwise
  1 − IoU; `label_agree`.
- **Score** = max(text, layout, 0.5 if the labels differ); 1 when some model has no block there.
- **Tables** are compared on their grids, not their raw content: rows are aligned by
  Needleman–Wunsch on row text (gap cost 0.5), then columns on the aligned cells; each reference
  cell scores the mean distance to the other models' aligned cell (1 if unaligned); rows or
  columns only another model has count as disagreeing cells. The table score is the cluster's
  `text` channel.
- **Page agreement** = 1 − the area-weighted mean cluster score. With fewer than two finished
  models there is no comparison (`comparison: null`), not an invented agreement.

The Studio colours a score below 0.1 as agreement, below 0.35 as partial, above as disagreement.

---
