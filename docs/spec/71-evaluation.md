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

---
