# Tabularium

**State-of-the-art OCR for historical documents, made simple for researchers.**
Recognize your archive with the best open-weight models, correct the results,
and fine-tune a model on your own pages, all from one app and without writing
a line of code.

[![CI](https://github.com/nbadino/tabularium/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nbadino/tabularium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue)
![Platforms](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)

![Table workspace: a borderless sea-borne trade table, scan on the left, its corrected grid on the right](docs/screenshots/table-workspace.jpg)

*A page of the 1912–13 Annual Statement of Sea-borne Trade, recognized and then
corrected in Tabularium: 68 × 12 cells, 672 of 677 verified, 24 of 25 column
sums adding up.*

## Why

Document AI moves fast. Every few months a new open-weight OCR model beats the
last one, and each comes with its own serving stack, prompt format, output
conventions and training recipe. For a historian, an archivist or a
computational social scientist, the gap between "this model is state of the
art" and "this model is reading my archive" is weeks of engineering.

Historical sources make it harder still. Old typefaces, faded ink, curved
pages, dense multi-column layouts and tables with no ruling lines are exactly
where off-the-shelf models stumble, and where a small amount of fine-tuning on
your own pages pays off the most.

Tabularium closes that gap:

- **Simple**: pick a model and where it runs (your laptop or a rented GPU),
  then recognize, review and export. Runtimes install themselves.
- **State of the art**: it drives each model with its official prompts,
  pipeline and training recipe, so you get the result the authors measured
  rather than an approximation.
- **Model-agnostic**: your annotations are stored independently of any model.
  When a better model comes out, add it and keep everything you have done.
- **No GPU? No problem**: rent one on Vast.ai, RunPod or Modal, or use any
  server you can reach over SSH. Tabularium sets it up, serves the model and
  stops it when you switch inference off, all from the interface.
- **Built for research teams**: multi-user from day one. Share projects, assign
  pages to annotators and reviewers, and see who is working where, live.
- **Yours**: self-hosted. Your scans and annotations stay on your server, and
  go to a GPU only when you decide to send them.

> *Tabularium*: the public archive where ancient Rome kept its *tabulae*,
> the records **and** the tables.

## Two paths, use one or both

```
Inference     import scans → choose a model → recognize → review → export
Fine-tuning   correct pages → build dataset → train → evaluate → recognize with your model
```

Many projects only ever need inference. When the base model isn't good enough
on your sources, the pages you corrected along the way are already your
training set.

## Features

**Inference**
- Bulk recognition of scans and multi-page PDFs in persistent sessions that
  keep running if you close the browser.
- Runs on this machine (NVIDIA, or Apple Silicon for MLX-ported models) or on
  external GPU providers: **Vast.ai, RunPod, Modal** or any SSH host. Pick a
  GPU, and Tabularium sets up the server and tunnel; stopping inference stops
  the instance you rented, so you aren't billed for idle time. Each option says
  *before* you click whether it can host the chosen model.
- A CPU OCR engine for when you have no GPU at all.
- Export as JSON, CSV or plain text, either the raw model output or your
  corrected version.

**Review and annotation**
- A studio for layout blocks, reading order and transcription, with
  project-specific labels and transcription rules.
- A spreadsheet-style workspace for tables, including the hard ones: no ruling
  lines, drifting columns, merged cells, arithmetic checks on column sums. Its
  compact toolbar keeps back, undo/redo and save in reach; structural actions
  stay under *Structure* and the crop/full-page switch sits on the scan.
- Model output is a **draft** until someone confirms it; reports always say how
  much is still unverified.

**Fine-tuning and evaluation**
- One-click datasets in the exact format each model's official trainer
  expects, split by page.
- Training with each model's official defaults (LoRA or full), a separate view for new
  runs and run history, loss charts and logs on demand. A VRAM check blocks configurations
  that cannot fit the selected GPU.
- Benchmarks of several models, or several checkpoints, on your own approved
  pages: layout IoU, reading order, TEDS for tables, CER/WER for text.

**Fine-tuning on rented GPUs**
- Training can run on this machine or on a remote GPU (SSH, Vast.ai, RunPod),
  with the same live logs and charts.

**Multi-user, for research teams**
- One instance for the whole lab: admin / editor / viewer roles, invitations
  by link, and per-project sharing with edit or read-only access.
- Annotator → reviewer workflow per page: submit, request changes, approve,
  with a personal "to annotate / to review" queue.
- Live presence in the studio: see who is on the same page, and get their
  changes without reloading.
- Interface in English, Italian and French.

| Recognize | Review | Annotate |
|---|---|---|
| ![Recognize](docs/screenshots/recognize.jpg) | ![Results](docs/screenshots/results.jpg) | ![Annotation studio](docs/screenshots/annotation-studio.jpg) |

## Supported models

Tabularium adds new open-weight OCR models as they are released. A model is
listed as verified only after it has been run on real pages.

| Model | NVIDIA (vLLM) | Apple Silicon (MLX) |
|---|:-:|:-:|
| MonkeyOCRv2-Parsing | ✅ | — |
| PaddleOCR-VL | ✅ | ✅ |
| MinerU2.5 | ✅ | 🧪 |
| Qwen3-VL | ✅ | 🧪 |
| Unlimited-OCR | ✅ | 🧪 |
| DeepSeek-OCR-2, GLM-OCR, dots.mocr | ✅ | — |
| Any Hugging Face repo you register | ✅ | — |

✅ verified on real pages · 🧪 experimental · — not available locally (use a
remote GPU). Every model also runs remotely. The measured matrix, and the
reason behind each cell, is in
[local inference guide](docs/guide/local-inference.md); training
support per model is in [training guide](docs/guide/training.md).

**Hosted OCR:** **LlamaIndex OCRia-1** is available under Models → LlamaIndex
OCRia-1. Each user supplies their own LlamaCloud API key, stored in the backend
vault; the panel exposes the native routing, effort, table-quality, language,
image, output and job-control settings that apply to single-page parsing.

Missing a model? [Open an issue](https://github.com/nbadino/tabularium/issues/new?template=feature_request.yml).

## Quickstart

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv tool install tabularium
tabularium
```

That's it: uv fetches a compatible Python by itself, and `tabularium` opens the
app in your browser at <http://localhost:8787>, where you create the
administrator account. Your data lives in `~/Tabularium`. No GPU, Node.js or git
needed.

To upgrade later: `uv tool upgrade tabularium`. Other ways to install (release
archive, from source) are in the [installation guide](docs/guide/installation.md).

## Documentation

- [Installation](docs/guide/installation.md) ·
  [Configuration](docs/guide/configuration.md) ·
  [Teams and review](docs/guide/collaboration.md) ·
  [Troubleshooting](docs/guide/troubleshooting.md)
- [Local inference](docs/guide/local-inference.md) ·
  [Cloud GPUs](docs/guide/cloud-gpus.md) ·
  [Training](docs/guide/training.md)
- [Development](docs/guide/development.md) ·
  [Product spec and design rationale](AGENTS.md)

## Design principles

- **The data comes first.** Annotations are stored in source pixels with full
  provenance; model-specific formats are produced only at export, so your work
  is never locked to one model.
- **Generated is not verified.** Nothing a model proposes becomes training data
  until a person confirms it.
- **Nothing destructive is implicit.** Every operation that can remove work
  asks first.
- **Official stacks are used, not forked.** Prompts, coordinate conventions,
  OTSL and training scripts match each model's upstream repository.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md)
and the [changelog](CHANGELOG.md). If you use Tabularium in research, please
cite it ([CITATION.cff](CITATION.cff)).

## License

[MIT](LICENSE) © Nicolò Badino

## Acknowledgments

[MonkeyOCRv2](https://github.com/Yuliang-Liu/MonkeyOCRv2) and
[ms-swift](https://github.com/modelscope/ms-swift) (parsing model and training
stack), [RapidOCR](https://github.com/RapidAI/RapidOCR) and
[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) (CPU engines), and the
authors of every model Tabularium can drive.

### Workspace presentation

Work pages use plain section headings and a readable content width. Recognition history,
run comparisons, generated files and checkpoint paths open on request. Training leads with
metrics and charts; logs remain available through an explicit control. Studio and table
editors retain the full workspace width.

Archive and registry searches cover every page, with additional rows loaded on request.
Results keep all recognition runs in one selector. Benchmark page selection starts with ready
pages and can also show pages needing review. These presentation choices preserve bulk actions,
verification requirements and model-specific configuration.

Local model setup reconciles startup progress with the verified active endpoint on the same
model and port. Server logs stay available on request after startup. Model-specific settings
keep official parameter names in tooltips alongside localized labels. Invalid invitation links
include a return to the application.

Real GPU workflow evidence: [Vast.ai trial, 22 October 2026](docs/VAST_WORKFLOW_TRIAL_20261022.md).

### Automatic comparison on the training GPU

Automatic MonkeyOCRv2 training compares the base and fine-tuned checkpoints sequentially
on the same rented GPU, using the run’s frozen held-out samples, before downloading the model
and releasing the instance. The monitor shows progress and metric deltas, with logs on demand.
Training success and comparison success are reported separately. See
[the comparison workflow](docs/TRAINING_COMPARISON_20261023.md).

Approval verifies table content as well as layout. Dataset coverage is checked before GPU rental;
see [the approved-table workflow repair](docs/APPROVAL_EXPORT_REPAIR_20261023.md).
