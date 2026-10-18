# Cloud GPUs

To serve a model on **your own GPU** instead, see
[Local inference](local-inference.md).

Tabularium can run **entirely on your computer** (scans, annotations, SQLite
database, web interface) while **delegating all heavy inference** (layout, OTSL
tables, OCR, playground) to a powerful **remote GPU** such as an NVIDIA RTX
4090 / 3090 / A5000 / A100.

## 1. How the hybrid setup works

```
┌─────────────────────────────────────────────────────────────┐
│                YOUR COMPUTER (laptop / desktop)             │
│  - FastAPI backend (port 8787)                              │
│  - React UI in the browser                                  │
│  - Historical scans and SQLite database (tabularium.db)     │
│  - No local GPU used                                        │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ HTTP/HTTPS requests with base64 crops
                               │ (over an SSH tunnel or an HTTPS proxy)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             CLOUD GPU INSTANCE (Vast.ai / RunPod)           │
│  - NVIDIA RTX 4090 (24 GB VRAM), ~$0.13–0.37 / hour         │
│  - OpenAI-compatible vLLM server with the chosen model      │
│  - Sub-second streaming responses (>80 tokens/s)            │
└─────────────────────────────────────────────────────────────┘
```

Benefits:

- **No local hardware requirements**: annotate and use the model from a
  MacBook Air, a laptop without a dedicated GPU, or a mini PC.
- **Speed**: a remote RTX 4090 produces layouts and tables almost instantly.
- **Low cost**: from ~$0.07/hour (RTX 3090). A hundred pages cost a few cents,
  and you stop the instance when you are done.

## 2. Method 1: Vast.ai from Tabularium (recommended)

Vast.ai is the cheapest GPU marketplace (checked 2026-08: RTX 3090 from
$0.07/h, median $0.15/h; RTX 4090 from $0.13/h, median $0.36/h).

**The only thing you do on the provider's site is create an account, add
credit and generate an API key.** Everything else (SSH key, choosing an offer,
renting, preparing the server, the tunnel) happens inside Tabularium: no
terminal, no web console, nothing to publish.

### Step 0: API key (once, on the website)

1. Create an account on [vast.ai](https://vast.ai/) and add credit ($5 is
   enough).
2. Go to **Account → API Keys** (`https://cloud.vast.ai/manage-keys/`) and
   create a key. Don't pick a template from the library: Tabularium sets the
   image and the startup script through the API.

### Step 1: First setup

In **Models**, choose a model, then **Vast.ai** as the destination. Paste the
API key and run the account check. In one step Tabularium:

- validates the key and shows your remaining credit;
- generates a dedicated ed25519 key pair in `data/ssh/tabularium_vast_ed25519`.
  The private key never leaves your machine; the API only exposes the
  fingerprint and the public key;
- registers the public key on your account, so every new instance receives it
  automatically. The operation is idempotent;
- resolves the official runner commit to pin.

### Step 2: Rent

Filter by GPU and maximum price, then **Rent** (always with an explicit cost
confirmation). The instance starts without an `onstart` hook: Tabularium
prepares it afterwards over SSH, polling every 8 seconds until the provider
publishes the SSH host and port.

### Step 3: Prepare and connect

**Prepare and connect** on the instance:

1. pins the host key in a dedicated `known_hosts` (`ssh-keyscan`), because the
   tunnel uses `StrictHostKeyChecking=yes`;
2. sends `scripts/cloud/setup_cloud_vllm.sh` **over SSH stdin, from your
   checkout**, and starts it in the background, logging to
   `/var/log/tabularium_setup.log`. The code running on the GPU is the one you
   have locally, not a copy downloaded from GitHub;
3. shows the remote log until vLLM is listening;
4. opens the tunnel, saves the inference configuration on
   `http://127.0.0.1:8888/v1` and tests it.

The four stages (account → GPU → server → connection) are derived from the
real state, not from your clicks: after a page refresh, a preparation in
progress resumes where it was.

### What the script does on the instance

`scripts/cloud/setup_cloud_vllm.sh`, in order, logging to
`/var/log/tabularium_setup.log` (which the UI shows and reads its stages from):

1. checks the GPU and its compute capability (minimum 8.0: vLLM runs in
   bfloat16);
2. installs system dependencies and clones the official runner at the pinned
   ref;
3. **creates a dedicated virtualenv** (`~/tabularium-venv`) with vLLM and
   PyTorch. Ubuntu 24.04 images have a distro-managed pip that refuses both
   self-upgrade (`RECORD file not found`) and system installs (PEP 668);
4. downloads the model weights and writes `cloud-manifest.json`;
5. starts the server with the serving flags.

Installed versions are **aligned with the verified local serving
environment** (`data/vllm-runtime`): vLLM 0.28.0, transformers 5.16.1, plus
`timm`, `einops`, `pillow`, `pydantic` and `huggingface_hub`.

### Local prerequisites

You need the OpenSSH binaries (`ssh`, `ssh-keygen`, `ssh-keyscan`);
`scripts/setup_backend.sh` warns if they are missing. On Windows use WSL2 or
Git for Windows.

### If SSH doesn't connect

Vast.ai exposes two SSH routes: the machine's direct IP and the
`ssh*.vast.ai` proxy. Tabularium prefers the direct one and never mixes the
two. Vast.ai sometimes reports no public IP for a running instance; in that
case the Vast panel has a **host/port override**, prefilled with the port
mapping, where you paste the IP shown in the Vast console ("Direct SSH
Connect"). Before handing anything over, Tabularium opens a bare SSH connection
and names the cause of a failure: key refused, host key changed, host not
resolvable, or port silent.

### Notes on the provider API

Vast.ai migrates route by route: as of 2026-09 the instance collection only
answers on **`/api/v1/instances/`** (v0 returns `410 deprecated_endpoint`),
while `users/current`, `ssh` and `search/asks` exist only on **v0**.
Tabularium uses v1 for the list and falls back to it for details if the v0
route disappears.

### Headless alternative: `onstart` hook

For non-interactive use, the backend API still supports the older path
(`prepare_server: true` on `/api/system/cloud/vast/rent`): the instance
downloads the script from GitHub, and two pinned refs are required
(`monkeyocr_ref` and `tabularium_ref`, the latter an **already published**
commit). The interface does not use it, precisely so as not to depend on a
push.

## 3. Method 2: RunPod with an HTTPS proxy (no SSH)

RunPod provides built-in HTTPS proxies, with no SSH tunnel to open.

### Step 1: Rent a pod

1. Go to [runpod.io](https://runpod.io/) → **Pods**.
2. Choose **1× RTX 4090** or **1× A5000**.
3. Template: **RunPod PyTorch 2.4 / CUDA 12**.
4. Expose HTTP port `8888`.
5. Click **Deploy**.

### Step 2: Start the vLLM server

1. Open the **Web Terminal** or connect over SSH.
2. Run the script:
   ```bash
   export MONKEYOCR_REF=<verified-monkeyocr-commit-or-tag>
   export TABULARIUM_REF=<verified-tabularium-commit-or-tag>
   export TABULARIUM_SERVER_API_KEY=<server-token>
   curl -fsSL "https://raw.githubusercontent.com/nbadino/tabularium/${TABULARIUM_REF}/scripts/cloud/setup_cloud_vllm.sh" | bash -s -- --port 8888 --ref "$MONKEYOCR_REF"
   ```

### Step 3: Connect Tabularium to the RunPod HTTPS URL

RunPod assigns a public, TLS-protected URL:
`https://<POD_ID>-8888.proxy.runpod.net/v1`

1. Enter that URL in Tabularium.
2. Enter your secret **API key**.
3. Test the connection, then save the configuration.

## 4. Method 3: Vast.ai with a direct public IP and an API key

If you prefer an open port over an SSH tunnel:

1. When starting the Vast.ai container, open a direct port (e.g. `8888`).
2. Start vLLM with an API key:
   ```bash
   export MONKEYOCR_REF=<verified-commit-or-tag>
   export TABULARIUM_SERVER_API_KEY=<server-token>
   bash setup_cloud_vllm.sh --port 8888 --ref "$MONKEYOCR_REF"
   ```
3. In Tabularium, enter the public Vast.ai URL (e.g.
   `http://198.51.100.24:34567/v1`) and the API key.

## 5. Method 4: Modal serverless

The **pay-per-second** option. All Modal templates managed by the UI keep one
warm container (`min_containers=1`) to avoid cold starts and favour latency;
set `TABULARIUM_MODAL_MIN_CONTAINERS=0` to go back to pure pay-per-use, with a
cold start on the first request. The repository includes a ready template
(`scripts/cloud/modal_vllm.py`) that builds the container (official repo +
vLLM), downloads the weights to a persistent volume and exposes the server
exactly like the local `serve_model.sh`.

1. Install and authenticate Modal (once):
   ```bash
   pip install modal
   modal setup
   ```
2. Deploy the template:
   ```bash
   modal deploy scripts/cloud/modal_vllm.py
   ```
3. Copy the printed URL (`https://<WORKSPACE>--tabularium-vllm-serve.modal.run`)
   and use `.../v1` as the inference endpoint in Tabularium. The **Modal
   (serverless)** preset fills in the format.

Operational notes:

- **Performance profile**: by default it uses the
  **MonkeyOCRv2-B-Parsing-DFlash** draft model and the official vLLM
  **0.25.1 + CUDA 12.9** recipe. DFlash only supports the B-Parsing checkpoint;
  it keeps the same target model and speeds up decoding without changing the
  output. Disable it with `TABULARIUM_MODAL_DFLASH=0` when you need S-Parsing or
  a different fine-tuned checkpoint.
- **Concurrency**: `TABULARIUM_MODAL_MAX_INPUTS=4` caps simultaneous requests
  per container; raising it improves throughput but can worsen single-page
  latency.
- **Transport**: `.modal.run` URLs stream so you can watch tokens live. The
  client drains the response until `[DONE]` even when the parser already has
  the full list, which avoids a premature chunked close
  (`TransferEncodingError`).
- **Banded tables**: independent bands are requested in parallel on cloud
  endpoints (at most four) and reassembled in order; on the local machine they
  stay serial so as not to raise peak VRAM.
- **Speculative decoding**: `TABULARIUM_MODAL_DFLASH_TOKENS=16` is the draft's
  official value; leave it unless you are under VRAM pressure.
- **GPU**: the template uses an **L4** by default (≈ $0.80/h active; A10 ≈
  $1.10/h), **not a T4**: vLLM runs in bfloat16 and refuses GPUs with compute
  capability below 8.0 (T4 = 7.5, V100 = 7.0). Valid serverless options are
  Ampere or newer.
- **Cold start**: the first deploy/load takes a few minutes, then the container
  stays warm. To save money when idle, use `MIN_CONTAINERS=0` and accept the
  restart delay.
- **Credits**: Modal's Starter plan includes **$30 of credits per month**
  (checked 2026-08).
- **API key** (optional): `TABULARIUM_VLLM_API_KEY=... modal deploy ...`, then
  set the same key in Tabularium.

## 6. Method 5: RunPod serverless

Similar to Modal, with a Docker template managed by RunPod: you need a
container exposing vLLM's OpenAI-compatible API. The endpoint looks like
`https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1` (the **RunPod Serverless**
preset in Tabularium). Billing is per second of active worker, and **cold
starts are billed at full rate**: with little traffic Modal (free credits) is
usually cheaper; for long active sessions an on-demand pod is better.

## 7. Quick check from the terminal

Test any remote endpoint with the CLI tool:

```bash
# availability and latency
python3 scripts/cloud/test_cloud_connection.py --url http://127.0.0.1:8888/v1

# full test with a synthetic crop and tokens/second
python3 scripts/cloud/test_cloud_connection.py --url http://127.0.0.1:8888/v1 --sample
```

## 8. Keeping costs down

- **Stop the instance when you're not working**: on both Vast.ai and RunPod a
  stopped instance only pays for storage (~$0.05/day). Switching inference off
  from Tabularium stops the instance it rented, and only that one.
- **Batch your work**: register and prepare scans first (no GPU needed), then
  start the GPU, recognize everything in a 1–2 hour block (~$0.50 total), and
  stop it.
- **Prices checked in August 2026** (vast.ai/pricing, runpod.io/pricing,
  modal.com/pricing):

| GPU | Vast.ai on-demand | Serverless |
|---|---|---|
| RTX 3090 (24 GB) | from $0.07/h · median $0.15/h | — |
| RTX 4090 (24 GB) | from $0.13/h · median $0.36/h | — |
| L4 (24 GB) | — | ≈ $0.80/h active (Modal), $0 idle |
| A10 (24 GB) | — | ≈ $1.10/h active (Modal), $0 idle |
| T4 (16 GB) | — | ❌ **not usable**: vLLM refuses bfloat16 below compute capability 8.0 |

- **Spot / interruptible** instances on Vast.ai cost a further 40–70% less and
  suit fine-tuning with frequent checkpoints (Tabularium's training center
  saves resumable checkpoints). Note that the official fine-tuning defaults
  (batch 4, 16384 tokens) need ~26 GiB of VRAM and don't fit a 24 GB card: use
  the reduced-batch preset with gradient accumulation; Tabularium's VRAM
  preflight tells you what fits.
- **Fine-tuning** doesn't need serverless: 2–3 hours of an RTX 3090/4090 on
  Vast.ai is enough for a LoRA run on a typical corpus.
- **Suggested strategy**: hourly Vast.ai for recognition and training; Modal
  serverless for occasional playground use and evaluations.
