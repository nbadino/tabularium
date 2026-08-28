# Tabularium

**A local, measurement-driven studio for fine-tuning document-parsing VLMs on dense-table archives.**
Built and proven on *Historic Shipping Index* shipping registers (1900s–1970s): borderless, whitespace-aligned tables with thousands of cells per page.

> *Tabularium* — the public archive where ancient Rome kept its *tabulae*: the records **and** the tables.

---

## What it is

One local process (FastAPI + React) that carries you from a folder of scans to a fine-tuned
`MonkeyOCRv2-Parsing` checkpoint:

```
register pages → guided annotation → export ms-swift JSONL → LoRA fine-tune → evaluate → playground
```

- **Register** pages (images / multi-page PDFs) with issue date, number, page type.
- **Annotate** with a canvas studio: blocks, reading order, transcription conventions — and a
  dedicated **table editor** for grids without ruling lines: merges, phantom columns, per-cell
  transcription, live OTSL preview.
- **Prefill** with pseudo-labeling: local OCR (per-cell, columns never fused) or MonkeyOCRv2 via
  vLLM (two-stage or official END2END). Registers are auto-promoted to `Table` blocks; every
  prefilled cell carries `source` + `verified` flags, and the export report tells you how much of
  the dataset is still draft.
- **Export** the only format the official training accepts: JSONL `messages` with coordinates
  normalized to 0–1000 and tables in **OTSL**, split per page (never per crop).
- **Train** with the official `swift sft` scripts, generated and parameterized for you, with a
  VRAM preflight that *refuses to start* runs that cannot fit your GPU.
- **Evaluate** on held-out pages: layout IoU, reading-order distance, per-cell CER, table
  structure — then jump from the worst failures back into the editor.

## Highlights

- **Borderless table detection, measured on real scans.** Rows come from glyph baselines
  (connected components), not ink-profile autocorrelation — the projection approach worked on
  1 page out of 4 and broke in three distinct ways (harmonic lock-in, saturated profile,
  skew smearing). Shear is estimated and *compensated without rotating the image*, so
  boundaries stay in the crop's reference frame. Internal boundaries bend row by row to follow
  hand-set columns: on the reference scans, snapped boundaries cut **0.5 %** of values versus
  **5.7 %** with straight cuts.
- **The 2 MP operating point, demonstrated.** Feeding the layout model more pixels makes it
  *worse*, measurably: 2 MP → correct structure, 4 MP → 141 fragments, 6 MP → label vocabulary
  collapses to `Text`, 9.5 MP → degenerate duplicated output. The vision encoder emits one token
  per 28×28 px; the model's notion of "a block" was learned at a specific tokens-per-page scale.
  Layout runs at 2 MP; content recognition always crops from the **native-resolution** scan.
- **VRAM honesty.** The dominant training term is not the weights (0.7 B ≈ 2 GiB) but the
  **logits**: `batch × seq × vocab_size × 2` = 4.6 GiB at full length for a 152 k vocabulary.
  The official preset (batch 4 × 16384 tokens) wants 26 GiB; the studio's preflight computes
  the real requirement and suggests the largest length that fits yours.
- **Data safety by construction.** Autosave preserves block IDs (bulk PUT never cascades away
  your table grids); destructive actions require explicit confirmation; splits are deterministic
  (ratio + seed) and split **by page** so crops never leak between train and val.

## Quickstart

Requirements: Python ≥ 3.11, Node.js (only to build the frontend), SQLite. GPU (NVIDIA, ≥ 8 GB)
only for fine-tuning and model inference; annotation and OCR prefill run on CPU.

```bash
# backend (venv + deps)
./scripts/setup_backend.sh

# frontend (build once; the backend serves the built bundle)
./scripts/setup_frontend.sh

# run everything on http://localhost:8787
./scripts/run.sh
```

For model inference / fine-tuning you also need the
[MonkeyOCRv2](https://github.com/Yuliang-Liu/MonkeyOCRv2) checkout with the
`MonkeyOCRv2-B-Parsing` checkpoint and a vLLM environment:

```bash
export TABULARIUM_TRAIN_REPO=/path/to/MonkeyOCRv2
export TABULARIUM_TRAIN_PYTHON=/path/to/envs/MonkeyOCRv2Parsing/bin/python
./scripts/serve_model.sh          # vLLM on :8888
./scripts/train_on_gpu.sh         # fine-tuning helper
```

## How it works

```
backend (FastAPI, SQLite, Pillow/NumPy, vLLM client)
  ├── services/table_detect.py   borderless grid detection (baselines, shear, snapping)
  ├── services/otsl.py           grid ⇄ OTSL, oracle-tested against the official converter
  ├── services/dataset_builder.py  3 JSONL families + health report (per-cell provenance)
  ├── services/inference.py      vLLM client: layout, END2END, band-wise table recognition
  ├── services/trainer.py        official swift sft scripts, resume, SSE logs, GPU telemetry
  └── services/vram.py           VRAM preflight (logits dominate; bisection on max_length)
frontend (React 18 + Vite + TS + Tailwind + Zustand + Konva)
  └── studio/                    canvas, layers, reading order, table editor (draggable rules)
```

The full product spec, the measured constraints, the data conventions (coordinates 0–1000,
OTSL encoding, JSONL shapes) and every design decision with its justification live in
**[AGENTS.md](AGENTS.md)** (Italian) — treat it as the source of truth.

## Status

Milestones M0–M8 are implemented and in daily use on the Historic Shipping Index corpus: projects,
annotation studio, table editor, dataset builder, training center, evaluation, playground,
pseudo-labeling. The model-facing parts (vLLM, training) target the
`zenosai/MonkeyOCRv2-B-Parsing` / `-S-Parsing` checkpoints and the ms-swift fork bundled with
the official repo.

## License

[MIT](LICENSE) © Nicolò Badino

## Acknowledgments

- [MonkeyOCRv2](https://github.com/Yuliang-Liu/MonkeyOCRv2) — the parsing model and training
  stack this studio fine-tunes. This repository never modifies the official checkout; it only
  drives it.
- [ms-swift](https://github.com/modelscope/ms-swift) — training framework (official fork).
- [RapidOCR](https://github.com/RapidAI/RapidOCR) / [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — CPU prefill engines.
- Transkribus — the benchmark this project measures itself against for historical archives.
