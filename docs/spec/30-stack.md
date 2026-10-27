# 4. Stack & architecture

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

Chosen design: a **local web app** = Python **FastAPI** backend + **React/Vite/TS** frontend.
The backend is the only process the user starts; it serves the UI and orchestrates everything.
No PyTorch in the dashboard process: model serving and training run in separate environments
(`<root>/vllm-runtime`, `<root>/mlx-runtime`, `<root>/paddle-runtime`, remote GPUs) that
Tabularium prepares itself.

### Distribution
- Installed with **`uv tool install tabularium`** (PyPI) or from a GitHub release archive; from
  source with `scripts/setup_backend.sh` + `scripts/run.sh`.
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
