# Installation

Tabularium runs as a local web app: one backend process serves the API and the
dashboard at `http://localhost:8787`. You do not need a GPU to organize scans,
annotate pages or build datasets; model inference and fine-tuning can use a
remote GPU.

## Requirements

| Component | Requirement |
|---|---|
| OS | Linux, macOS or Windows |
| Python | 3.11–3.13 (backend; CI-verified) |
| Node.js | 22 (CI-verified; used by `run.sh`/`run.ps1` to build the frontend) |
| Storage | SQLite (bundled), plus disk space for your scans |
| GPU | Only for model recognition, evaluation and fine-tuning: NVIDIA with ≥ 8 GB, Apple Silicon for MLX-ported models, or a remote GPU |

Annotation, review, export and local-OCR prefill run entirely on CPU. The
dashboard process never imports PyTorch: model serving and training run in
separate environments it orchestrates.

## Platform boundaries

| Activity | Linux | macOS | Windows |
|---|:-:|:-:|:-:|
| Archive, annotation, review, export | Yes | Yes | Yes |
| Local OCR prefill on CPU | Yes | Yes | Yes |
| Local model serving on NVIDIA/CUDA, fine-tuning | Yes | No | Via WSL2 |
| Local model serving with MLX (selected models) | No | Apple Silicon | No |
| Remote GPU through SSH, Vast.ai, RunPod or Modal | Yes | Yes | Yes |

Which models run locally depends on the pair *machine + model*, and the Models
screen says so per model. The measured matrix is in
[Local inference](local-inference.md) §0.

## Install from source

Prebuilt `.app` and `.exe` installers are not published yet. Clone the
repository (or **Code → Download ZIP** on GitHub) and open a terminal in the
`tabularium` folder.

```bash
# macOS / Linux
./scripts/setup_backend.sh
./scripts/setup_frontend.sh
./scripts/run.sh
```

On Windows, run these PowerShell scripts in order:

```powershell
scripts\setup_backend.ps1
scripts\setup_frontend.ps1
scripts\run.ps1
```

Then visit **<http://localhost:8787>**. The first visit asks you to create an
administrator account; later sessions start at the login page. Keep the
terminal open while using the app and press `Ctrl+C` to stop it.

To start again later, run `./scripts/run.sh` (Windows: `scripts\run.ps1`); it
rebuilds the frontend when needed. Project data is kept in `data/` by default
and stays on your machine.

The backend installers accept Python 3.11, 3.12 or 3.13 and explicitly refuse
versions outside the verified matrix.

### Linux prerequisites

The bundled OCR engine depends on `opencv-python`, which imports
`libGL`/`glib`; on a headless box the setup prints the remedy if the import
fails:

```bash
sudo apt-get install -y libgl1 libglib2.0-0   # Debian/Ubuntu; equivalent elsewhere
```

### Single-user mode

For a single-user machine without authentication, start the backend with
`TABULARIUM_AUTH=off`. Registration is closed by default; the administrator can
open it from **Settings**. All options are in [Configuration](configuration.md).

## Next steps

1. **Projects** → create a project and import scans (from the browser, or from
   a folder on the server).
2. **Models** → pick a model and where it runs (skip this to use local OCR only).
3. **Recognize** → select pages and start.
4. **Results** → check, open pages in **Annotate** to correct, export.

Model serving details are in [Local inference](local-inference.md);
rented GPUs (Vast.ai, RunPod, Modal, SSH tunnels) in
[Cloud GPUs](cloud-gpus.md); tested GPU profiles in
[benchmarks.md](https://github.com/nbadino/tabularium/blob/main/benchmarks.md). A catalog entry is not by itself a promise
that every checkpoint fits an 8 GB GPU.
