# Tabularium

**A local-first studio for fine-tuning document-parsing models on archives of
dense tables.**

Tabularium guides you through the complete path from a folder of page scans to
a fine-tuned parsing checkpoint:

```
register pages → annotate → prefill (optional) → export dataset → fine-tune → evaluate → try it
```

It was built for — and proven on — historical registers (shipping indexes,
1900s–1970s): pages whose tables have **no ruling lines**, hand-set columns
that drift down the page, and hundreds of cells per page. Nothing in it is
specific to that corpus: any document archive with table-heavy pages works the
same way.

> *Tabularium* — the public archive where ancient Rome kept its *tabulae*:
> the records **and** the tables.

| Home | Annotation studio | Project pages |
|---|---|---|
| ![Home](docs/screenshots/home.png) | ![Annotation studio](docs/screenshots/annotation-studio.png) | ![Project pages](docs/screenshots/project-pages.png) |

---

## What it does

### 1. Register your archive

Point Tabularium at a folder of scans (images or multi-page PDFs). It
registers every page with the metadata your domain needs — issue date, issue
number, page number, page type — and generates previews on demand, however
large the scans are.

### 2. Annotate with a purpose-built studio

A zoom/pan canvas for high-resolution pages with:

- **Blocks** — rectangles and polygons, a label palette, drag/resize, undo/redo,
  keyboard shortcuts, autosave;
- **Reading order** — explicit ordering of blocks with a "flow" preview that
  walks the page the way a reader would;
- **Transcription** — per-block text with a live conventions checklist, so the
  dataset stays consistent across annotators;
- **A table editor for borderless tables** — rows, columns, merged cells,
  phantom columns where the ruling faded, cell-by-cell transcription, live
  HTML/OTSL preview. The grid is drawn by you; the text can be pre-filled.

### 3. Prefill instead of starting from zero

Optional pseudo-labeling, with two engines you choose between explicitly:

- **Local OCR** (RapidOCR / PaddleOCR, CPU): detects text lines and proposes
  blocks. With table promotion on, the largest cluster of lines whose geometry
  proves a grid becomes a `Table` block with the cells pre-filled **cell by
  cell** — columns are never fused.
- **The parsing model itself** (MonkeyOCRv2 via vLLM, GPU): returns blocks
  already classified, in one official END2END pass or in two stages.

Every prefilled block and cell is born `verified: false` and carries its
provenance. The dataset export report tells you how much of your dataset is
still draft — corrections are the training signal, not the raw model output.

### 4. Export the dataset the training stack accepts

One click builds the JSONL families the official ms-swift training expects:

- **layout** — full page + prompt, blocks with coordinates normalized 0–1000,
  in reading order;
- **text recognition** — block crops + transcriptions;
- **table recognition** — table crops + **OTSL** (the encoding the official
  pipeline speaks), always the full table, with optional verified-row bands as
  an augmentation.

Splits are deterministic (ratio + seed) and split **by page**, never by crop,
so no page leaks between train and validation. A health report lists counts
per class, out-of-page boxes, missing transcriptions and unverified content.

### 5. Train with the official scripts — safely

Tabularium generates and launches the *official* `swift sft` scripts (LoRA or
full SFT) from templates, in a dedicated GPU environment, with:

- a **VRAM preflight** that refuses to start runs that cannot fit your GPU and
  proposes the largest sequence length that will;
- live log streaming, loss/learning-rate charts, GPU telemetry, stop and
  resume from the last checkpoint;
- immutable run recipe and SHA-256 artifact manifests, including remote
  checkpoint download and verification. LoRA merge into a vLLM-servable
  checkpoint is intentionally not advertised yet: it remains a tracked
  follow-up until the official export command is integrated and tested.

### 6. Evaluate, iterate, and try it live

Held-out pages are scored on layout (IoU per label), reading-order distance,
per-cell CER/WER and table structure — with a GT-vs-prediction overlay and a
"worst failures" list that sends you back to the editor. The playground runs
the fine-tuned (or base) model on any page, without saving results.

### Multi-user, self-hosted

Optional but on by default: first-run setup creates the instance
administrator; login via HttpOnly session cookies; global roles
(admin / editor / viewer) plus per-project sharing; instance settings and user
management. Passwords and session tokens are stored only as hashes.

---

## Requirements

| Component | Requirement |
|---|---|
| OS | Linux, macOS or Windows |
| Python | 3.11–3.13 (backend; CI-verified) |
| Node.js | ≥ 20 (used by `run.sh`/`run.ps1` to build or refresh the frontend) |
| Storage | SQLite (bundled), plus disk space for your scans |
| GPU | NVIDIA, ≥ 8 GB VRAM — **only** for model prefill / inference / fine-tuning |

Annotation and local-OCR prefill run entirely on CPU. The dashboard process
never imports PyTorch: training and model inference run in separate
environments it orchestrates.

### Platform boundaries

| Activity | Linux | macOS | Windows |
|---|---:|---:|---:|
| Dashboard, annotation, projects and dataset export | Yes | Yes | Yes |
| Local OCR prefill on CPU | Yes | Yes | Yes |
| Local NVIDIA/CUDA model serving and training | Yes | No | Via WSL2 |
| Remote GPU through SSH, Vast.ai or RunPod | Yes | Yes | Yes |

On macOS and Windows the application remains fully usable for archive
preparation and annotation. GPU-dependent model serving/training must run on
Linux, either directly, through WSL2 on Windows, or on a remote Linux GPU.

## Quickstart

```bash
# 1. backend (venv + dependencies)
./scripts/setup_backend.sh          # Windows: scripts\setup_backend.ps1

# 2. frontend (built once; the backend then serves it)
./scripts/setup_frontend.sh         # Windows: scripts\setup_frontend.ps1

# 3. run everything on http://localhost:8787
./scripts/run.sh                    # Windows: scripts\run.ps1
```

The backend installers accept Python 3.11, 3.12 or 3.13 and explicitly refuse
versions outside the verified matrix.

On first launch the app shows the **setup screen** where you create the
instance administrator; every later session starts at the login page.

For a single-user local machine without authentication, start the backend with
`TABULARIUM_AUTH=off`. Registration is closed by default; the administrator can
open it from **Settings**.

### Connecting a model (optional)

Model-driven prefill, evaluation and the playground need a vLLM server. This
is entirely point-and-click from the app — no shell commands, no environment
variables, nothing to install by hand:

1. Open **Registro Modelli** (Model Registry).
2. Pick a model (`MonkeyOCRv2-Parsing`, `MinerU2.5`, `dots.mocr`,
   `PaddleOCR-VL`, `Unlimited-OCR`, `GLM-OCR`, `DeepSeek-OCR-2`, `Qwen3-VL`,
   or **add your own** Hugging Face repo) → **Download**.
3. Once downloaded → **Start as local server**.

The adapter catalog includes experimental/download-only entries as well as
models verified on the current machine. See [benchmarks.md](benchmarks.md) for
the tested GPU profiles and the exact local serving status; a catalog entry is
not by itself a promise that every checkpoint fits an 8 GB GPU.

The first time you start *any* model, Tabularium creates a dedicated Python
environment and installs vLLM into it by itself (a couple of minutes,
one-time only); for `MonkeyOCRv2-Parsing` it also clones the
[official repo](https://github.com/Yuliang-Liu/MonkeyOCRv2) it needs for
serving, automatically. `TABULARIUM_TRAIN_REPO` / `TABULARIUM_TRAIN_PYTHON` /
`TABULARIUM_SERVE_PYTHON` still exist as **optional overrides** if you already
have your own checkout or environment — they are never required.

### Vast.ai: prepare, reconnect, change model

From **Cloud → Vast.ai**, save the Vast API key, select a running instance and
choose the model from the recipe selector. **Prepare and connect** is
idempotent: an already-ready server with the same model is reused; selecting a
different model restarts only the remote serving process, reuses cached weights
when available, and recreates/verifies the SSH tunnel. The UI shows provisioning
logs and connection status. Archive scans run as background jobs with a real
file counter, so refreshing the page does not lose the scan status.

```bash
./scripts/train_on_gpu.sh    # fine-tuning helper (separate from serving)
```

Exact vLLM flags verified per model, and how the size warning works when a
checkpoint may not fit your GPU, are documented in
[docs/LOCAL_INFERENCE_GUIDE.md](docs/LOCAL_INFERENCE_GUIDE.md).

To compare already-running local endpoints with the same page, use the
non-destructive benchmark (one `--target` per server):

```bash
PYTHONPATH=backend data/vllm-runtime/bin/python scripts/benchmark_models.py \
  --image test/1502-a-BANCO-SAN-GIORGIO-originale.jpg \
  --target monkeyocrv2-parsing,http://127.0.0.1:8888/v1,MonkeyOCRv2 \
  --target mineru2.5,http://127.0.0.1:8889/v1,mineru2.5 \
  --task layout --repeat 2 --output data/benchmarks/run.json
```

The report records success/failure, wall latency, TTFT, tokens/s, output
validity and token usage. Use `--task end2end` for adapters whose layout
protocol is not verified; benchmark results are never written as labels.

A GPU can also be rented on demand — Tabularium has built-in cloud instance
management (SSH tunnel, RunPod proxy, serverless) documented in
[docs/CLOUD_INFERENCE_GUIDE.md](docs/CLOUD_INFERENCE_GUIDE.md).

### Optional extras

- **Page rectification** — *Align and compare* in the studio proposes a
  corrected page without touching your annotations; you accept or reject it.
  The only neural rectifier is the **official MonkeyOCRv2 preprocessor**, the
  same stage the model's own pipeline runs on every page before parsing (see
  [docs/LOCAL_INFERENCE_GUIDE.md](docs/LOCAL_INFERENCE_GUIDE.md) §2.2). It
  needs no extra install: it reuses the checkpoint's weights and the vLLM
  runtime. Alongside it there are only rotation-only deskew and the manual
  perspective/mesh corrections. The earlier third-party engines (UVDoc,
  DocScanner-L) were substitutes chosen before the model's own preprocessor was
  reachable, and have been removed: preparing pages differently from how the
  model expects to receive them is a liability, not an option.
- **Alternative OCR engines** — see
  [docs/OCR_MODEL_ALTERNATIVES.md](docs/OCR_MODEL_ALTERNATIVES.md).

## Configuration

Everything is controlled by environment variables; nothing is hard-coded.

| Variable | Default | Purpose |
|---|---|---|
| `TABULARIUM_ROOT` | `<repo>/data` | data root (projects, database, crops, runs) |
| `TABULARIUM_HOST` / `TABULARIUM_PORT` | `127.0.0.1` / `8787` | server bind |
| `TABULARIUM_AUTH` | `on` | `off` disables authentication (single-user local mode) |
| `TABULARIUM_REGISTRATION_OPEN` | closed | `1` opens self-registration |
| `TABULARIUM_SESSION_TTL_DAYS` | `30` | session lifetime |
| `TABULARIUM_OCR_ENGINE` | `auto` | `rapidocr` or `paddleocr` |
| `TABULARIUM_VLLM_URL` | `http://127.0.0.1:8888/v1` | inference server endpoint |
| `TABULARIUM_VLLM_MODEL` | `MonkeyOCRv2` | served model name |
| `TABULARIUM_VLLM_MAX_PIXELS` | unset | cap on pixels sent to the layout model |
| `TABULARIUM_TRAIN_REPO` | unset | official MonkeyOCRv2 checkout (training) |
| `TABULARIUM_TRAIN_ENV` | `monkeyocrv2-train` | conda/venv name for training |
| `TABULARIUM_MODELS_DIR` | `<repo>/models` | local model checkpoints |

## How it's built

One backend process serves both the API and the built frontend:

```
backend  (FastAPI + SQLite + Pillow/NumPy; vLLM client; subprocess for training)
  services/   table detection · OTSL encoding · dataset builder · vLLM client
              trainer orchestration · VRAM preflight · auth/permissions · prefill

frontend (React 19 + Vite + TypeScript + Tailwind; Konva canvas)
  studio/     zoom/pan canvas · blocks · reading order · table editor
  app/        auth gate · layout · i18n (English / Italiano / Français)
```

Design principles:

- **The data comes first.** Annotations are stored in source pixels with full
  provenance; the model-specific formats (0–1000 coordinates, OTSL, JSONL) are
  produced only at export. Your annotations never get locked to one model.
- **Nothing destructive is implicit.** Autosave preserves identifiers, and
  every operation that can remove work requires explicit confirmation.
- **The official stack is used, not forked.** Training scripts, prompts,
  coordinate conventions and the OTSL encoder all match the official
  MonkeyOCRv2 repo, which is referenced as an external checkout and never
  modified.

## Documentation

- **[AGENTS.md](AGENTS.md)** — the complete product spec, data conventions and
  the reasoning behind every design decision (Italian; the source of truth).
- **[PRODUCT.md](PRODUCT.md)** — what the product promises and its constraints.
- **[DESIGN.md](DESIGN.md)** — the interface design system.
- **[docs/](docs/)** — cloud inference guide, OCR engine alternatives.

## Development

```bash
# backend tests
cd backend && python -m pytest tests -q

# frontend: typecheck, unit/component tests, build
cd frontend && npm run typecheck && npm test && npm run build

# end-to-end smoke (needs a running instance and Chromium)
npm run test:e2e
```

Continuous integration runs the backend tests, the frontend checks and the
end-to-end workflow (see `.github/workflows/ci.yml`).

## Troubleshooting

**I forgot the administrator password.**
Reset it from the terminal with the bundled tool — it uses the same hashing
and session invalidation as the app itself:

```bash
backend/.venv/bin/python scripts/reset_password.py          # list users
backend/.venv/bin/python scripts/reset_password.py admin    # interactive (hidden input)
```

Pass a second argument to set the password non-interactively. The data root
follows `TABULARIUM_ROOT` (default `<repo>/data`).

**The app doesn't start / the page is blank.**
Check that the frontend was built at least once (`scripts/setup_frontend.sh`,
or `cd frontend && npm run build`): the backend serves `frontend/dist`. When
started with `scripts/run.sh`/`run.ps1`, the frontend is rebuilt automatically
if its sources are newer than `dist/index.html` (or when
`TABULARIUM_BUILD_FRONTEND=1` is set). The server log reports the health
endpoint on `/api/health`.

**Model prefill or playground says the model is unavailable.**
Those features need the vLLM server (`scripts/serve_model.sh`); everything
else works without it. Cloud instances are covered in
[docs/CLOUD_INFERENCE_GUIDE.md](docs/CLOUD_INFERENCE_GUIDE.md).

## License

[MIT](LICENSE) © Nicolò Badino

## Acknowledgments

- [MonkeyOCRv2](https://github.com/Yuliang-Liu/MonkeyOCRv2) — the parsing
  model and training stack Tabularium fine-tunes. This repository never
  modifies the official checkout; it drives it.
- [ms-swift](https://github.com/modelscope/ms-swift) — training framework
  (official fork bundled with MonkeyOCRv2).
- [RapidOCR](https://github.com/RapidAI/RapidOCR) /
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) — CPU prefill engines.
