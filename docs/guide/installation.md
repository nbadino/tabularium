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

## Install with uv (recommended)

[uv](https://docs.astral.sh/uv/) installs Tabularium in its own environment and
downloads a compatible Python if your system doesn't have one, so the version
of Python on your computer doesn't matter.

1. Install uv, once:

    ```bash
    # macOS / Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    ```powershell
    # Windows (PowerShell)
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

2. Install and start Tabularium:

    ```bash
    uv tool install tabularium
    tabularium
    ```

The browser opens on **<http://localhost:8787>**; the first visit asks you to
create an administrator account. Keep the terminal open while using the app and
press `Ctrl+C` to stop it.

| Command | What it does |
|---|---|
| `tabularium` | start the app and open the browser |
| `tabularium --no-browser` | start without opening the browser |
| `tabularium --port 9000` | use another port |
| `tabularium --data /path/to/folder` | keep data somewhere else than `~/Tabularium` |
| `tabularium --host 0.0.0.0` | accept connections from other computers (for a shared lab server) |
| `uv tool upgrade tabularium` | upgrade to the latest release |
| `uv tool uninstall tabularium` | remove the app (your data folder is kept) |

**Your data** (projects, database, models, settings) lives in `~/Tabularium`,
outside the installed package, so upgrades never touch it. The folder also
holds `.env` with the key that encrypts provider credentials saved from the
interface: keep it with the data when you back up or move it.

## Install from a release archive

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

## Verify the installation: `tabularium doctor`

Before opening the interface, ask the installation to introduce itself:

```bash
tabularium doctor
```

One line per check — data directory, free space, database integrity, schema
version, port, authentication, credential vault, process model, GPU, model
runtimes, model downloads, interface. `warn` is a legitimate state (no GPU, no
network, no database yet); `fail` means the installation is not ready and the
hint says what to change. `tabularium doctor --json` is the same report for a
monitoring script.

Two failures worth knowing in advance:

- **`process model` / `port`**: Tabularium is a single process. Downloads,
  training jobs, the live channel and the sessions live in memory, so a second
  worker duplicates jobs and splits sessions. `tabularium --workers 2` is
  refused, and `WEB_CONCURRENCY>1` stops the application at startup.
- **`credential vault`**: the vault key belongs to the data directory. A key
  generated with another key does not decrypt saved provider credentials; the
  app starts and says so, but re-entering them is the fix.

## Container

The image contains the whole application — interface, backend, OCR engine — and
no PyTorch: model runtimes and training are prepared separately, or run on a
rented GPU.

```bash
docker build -f docker/app/Dockerfile -t tabularium .
docker run -d --name tabularium -p 127.0.0.1:8787:8787 \
  -v tabularium-data:/data tabularium
```

Or with compose, which sets the same options explicitly:

```bash
docker compose -f docker/app/compose.yml up -d
```

The data directory is `/data` inside the container: the SQLite database, the
projects, the crops, the runs, the models and the vault key. Back it up as a
volume, not as a file inside the image. The interface listens on `0.0.0.0:8787`;
publish it on the loopback and let a reverse proxy terminate TLS, or change the
port mapping deliberately.
Inside the container a host proxy arrives from the bridge gateway, not from
`127.0.0.1`: set `TABULARIUM_FORWARDED_ALLOW_IPS` to that address (or `*` when only the
proxy can reach the port) so login limits see the real client, and
`TABULARIUM_SESSION_COOKIE_SECURE=1` once TLS is in place.

### Serving under a sub-path

The interface can live at `/tabularium/` behind a reverse proxy. Build it with
the same prefix, so assets, API calls and routes are generated under it:

```bash
TABULARIUM_BASE_PATH=/tabularium/ npm run build      # from a source checkout
docker build --build-arg TABULARIUM_BASE_PATH=/tabularium/ \
  -f docker/app/Dockerfile -t tabularium .
```

with compose, set `TABULARIUM_BASE_PATH=/tabularium/` in the environment. Then
configure the proxy to **strip the prefix** before forwarding: the backend keeps
serving `/api/...` and `/`, and the interface's URLs already include the prefix.

```nginx
location /tabularium/ {
    proxy_pass http://127.0.0.1:8787/;   # the trailing slash strips the prefix
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    client_max_body_size 200m;          # page uploads
}
```

Without stripping, the app would receive `/tabularium/api/...` and answer 404:
the prefix belongs to the proxy, not to the application.

## Offline installation

Two things normally come from the network: model checkpoints, and the OCR engine's own
models on first use.

**Checkpoints.** `tabularium models import` copies a directory that contains
`config.json` and the weights into `<data>/models/<adapter>`, with the same state a
finished download leaves behind — the registry, the UI and the serving scripts cannot
tell the difference, and no download is attempted afterwards:

```bash
tabularium models import /media/usb/MonkeyOCRv2-B-Parsing --adapter monkeyocrv2-parsing
tabularium models list            # what is installed, and where
tabularium models import --help   # --force replaces an existing installation
```

The copy is staged next to the destination and renamed at the end, so an interrupted
import never looks like a complete checkpoint.

**Hugging Face behind a mirror.** `huggingface_hub` honours `HF_ENDPOINT`, so an internal
mirror works without changing the application:

```bash
export HF_ENDPOINT=https://hf-mirror.internal.example
```

**OCR engine models.** The bundled RapidOCR engine downloads its own models the first
time it runs and keeps them in its cache, outside Tabularium's data directory; there is
no import command for them yet. On an air-gapped host, either run the engine once on a
connected machine and copy that cache, or use a model served from the network. This is
the remaining item of phase P5 in `docs/architecture/program.md`.

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
