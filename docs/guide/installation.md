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
| Node.js | Only when installing from source: 22 (CI-verified) |
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

## Install from a release (recommended)

Releases ship with the web interface already built, so you only need Python.

1. Download the latest release from
   [GitHub Releases](https://github.com/nbadino/tabularium/releases/latest):
   `tabularium-<version>.zip` on Windows, `tabularium-<version>.tar.gz` on
   macOS and Linux. `SHA256SUMS.txt` lists the checksums.
2. Extract it and open a terminal in the `tabularium-<version>` folder.
3. Set up the backend once, then start the app:

```bash
# macOS / Linux
./scripts/setup_backend.sh
./scripts/run.sh
```

```powershell
# Windows (PowerShell)
scripts\setup_backend.ps1
scripts\run.ps1
```

Then visit **<http://localhost:8787>**. The first visit asks you to create an
administrator account; later sessions start at the login page. Keep the
terminal open while using the app and press `Ctrl+C` to stop it. To start
again later, run `./scripts/run.sh` (Windows: `scripts\run.ps1`).

Project data is kept in `data/` inside the folder by default and stays on your
machine. To upgrade, extract the new release and point it at the same data with
`TABULARIUM_ROOT` (see [Configuration](configuration.md)), or move `data/` and
`.env` into the new folder. Keep `.env`: it holds the key that encrypts the
provider credentials saved from the interface.

The backend installer accepts Python 3.11, 3.12 or 3.13 and explicitly refuses
versions outside the verified matrix.

## Install from source

For development, or to run unreleased changes. This also needs **Node.js 22**
to build the interface.

```bash
git clone https://github.com/nbadino/tabularium.git
cd tabularium
./scripts/setup_backend.sh
./scripts/setup_frontend.sh
./scripts/run.sh
```

On Windows, run `scripts\setup_backend.ps1`, `scripts\setup_frontend.ps1` and
`scripts\run.ps1`. From source, `run.sh` rebuilds the interface whenever its
sources change.

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
