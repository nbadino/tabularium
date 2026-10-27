# Local inference

This guide explains how to serve an OCR/VLM model **on your own machine**: the
machine decides the runtime (§0), and each model uses serving parameters
verified against its official documentation (August 2026). For remote GPUs see
[Cloud GPUs](cloud-gpus.md).

`VllmClient` (`backend/app/services/inference.py`) does not care whether a
server is local or remote: it always talks to an OpenAI-compatible endpoint.
The only local-specific part is **which server to start** (each adapter's
`serve_command()`, or the MLX server on Apple Silicon), and that is what this
guide is about.

**No model is blocked because of its size.** As in LM Studio, you can try any
model on your GPU; the registry only shows a ⚠ warning when a checkpoint may
not fit the free VRAM (see §11). Feasibility notes below (e.g. "tight on
8 GB") are informative, not limits enforced by the code.

**No terminal commands, no environment variables.** The whole flow is
Models → Download → Start. The first time you start a generic model
(`vllm serve ...`), Tabularium creates a dedicated Python environment
(`TABULARIUM_ROOT/vllm-runtime`) and installs vLLM in it: a couple of minutes
the first time, instant afterwards. For MonkeyOCRv2-Parsing it also clones the
official repository (`TABULARIUM_ROOT/vendor/MonkeyOCRv2`), which provides
`serve.py`. The environment variables mentioned below
(`TABULARIUM_TRAIN_REPO`, `TABULARIUM_TRAIN_PYTHON`, `TABULARIUM_SERVE_PYTHON`)
are only **optional overrides** for people who already have their own
environment or checkout.

Reference GPU used to tune the parameters: **RTX 4060 Laptop, 8 GB VRAM**
(Ada Lovelace, compute capability 8.9, which meets vLLM's bf16 requirement of
compute capability ≥ 8.0).

## 0. Where local inference runs depends on the hardware

Tabularium inspects the machine and says so. A single module makes the
decision, `backend/app/services/hardware.py`, and the model registry uses it to
say, model by model, what runs *here* and what doesn't. The same model is not
local everywhere, and "local model" without saying where is a button that
fails.

| Machine | Local runtime | What it covers |
|---|---|---|
| Linux + NVIDIA GPU | **vLLM** (`vllm serve`) | every model in the registry |
| Windows | **vLLM via WSL2** | same as Linux, inside WSL2 |
| Apple Silicon (M1+) | **MLX** (`mlx_vlm.server`) | only models with an MLX port |
| Linux without an NVIDIA GPU | — | no local serving: use a remote provider |

vLLM does not run on Apple Silicon: it is CUDA-first and has no native Metal
path. Local models there use MLX, but not all share the same server:
PaddleOCR-VL and Qwen use the OpenAI-compatible `mlx-vlm` endpoint; MinerU uses
its own `MinerUClient` with the `mlx-engine` backend in a separate
environment pinned to the official release.

| Model | vLLM (CUDA) | MLX (Apple Silicon) | Why not, when not |
|---|---|---|---|
| PaddleOCR-VL-1.6 | ✅ | ✅ `mlx-community/PaddleOCR-VL-1.6-4bit` | — |
| Qwen3-VL-8B | ✅ | 🧪 `mlx-community/Qwen3-VL-8B-Instruct-4bit` | new Qwen HTML workflow to re-benchmark |
| MonkeyOCRv2-Parsing | ✅ | ❌ | no MLX port of the architecture |
| MinerU2.5 | ✅ | 🧪 `MinerUClient` `mlx-engine` | native path with an isolated runtime; live test on the corpus still pending |
| DeepSeek-OCR-2 | ✅ | ❌ | the recipe needs vLLM's n-gram logits processor |
| Unlimited-OCR | ✅ (Docker image) | 🧪 `mlx-community/Unlimited-OCR-{bf16,8bit}` | `mlx-vlm` ≥ 0.6.4 ships `unlimited_ocr` with R-SWA; protocol verified, quality test on gold pages pending |
| dots.mocr | ✅ | ❌ | actually served: END2END stops at 682 characters, run fails |
| GLM-OCR | ✅ | ❌ | actually served: run "succeeds" with **zero** blocks inserted |

**How the MLX column was decided.** A checkpoint or a declared backend is not
enough: confirmation requires serving and recognizing real pages.
`paddleocr-vl` (35 blocks) and `qwen3-vl-8b` (137) passed earlier tests with
generic prompts. The Qwen flow has since been aligned with the official Qwen
HTML parser and must be re-benchmarked. MinerU has the vendor's native MLX
path but stays experimental until it completes the live benchmark; the others
don't run, for measured reasons. Two are worth remembering because they fail
in different ways:

- **dots.mocr** fails loudly: END2END generation stops at 682 characters
  (`finish=stop`) on a full page and the run errors out.
- **GLM-OCR** fails silently: the run "succeeds" and inserts **zero** blocks.
  An empty success is worse than an error, because it tells you nothing.

In both cases the checkpoint loads; what's missing is the path (prompt,
template, decoding). The local column is a measurement, not a promise.

Two practical differences of the MLX path:

- **Weights don't go through the registry.** `mlx-vlm` downloads them from the
  Hugging Face cache on first request, from the (4-bit) MLX checkpoint rather
  than the full-precision repo. That's why the catalog entry doesn't ask for a
  download before serving.
- **The model name is the MLX checkpoint id** (`/v1/models` of the MLX
  server). Start sets it automatically; the wrong name would be a 404 on every
  call.

**The architecture existing in MLX is not enough: the recipe must be
reproducible.** Up to `mlx-vlm` 0.6.3 Unlimited-OCR could not be served via MLX:
the architecture was missing and a page produced 12,288 characters of
`alpha.alpha.alpha…` up to the token limit. Since 0.6.4, `mlx-vlm` ships
`unlimited_ocr` with `RingSlidingKVCache`, the upstream R-SWA (full prefix + a
ring of the last 128 tokens), which makes the recipe reproducible.

One difference from vLLM remains: upstream's **n-gram logits processor**
(35-gram) is not provided by `mlx-vlm`, and the MLX server doesn't accept
`logits_processors` over JSON. The only available guard is the repetition
penalty. Measured on a sample index page (`gundam` mode, 3,000 tokens): with no
guard a line repeats 23 times; with `repetition_penalty=1.1` and the default
20-token window it repeats 80 times (the window doesn't cover a ~25-token
line); with `repetition_context_size=128` generation **ends on its own**
without repetition at ~250 tok/s. Both values are part of this model's MLX
recipe, and they are the defaults in Settings → Local generation (MLX).

**The standard MLX server isn't enough either.** `mlx_vlm.server` 0.7.1 does
not inject the image for the `unlimited_ocr` architecture (continuous-batching
backend: 6–7 `prompt_tokens` instead of 1,518, empty or hallucinated output),
whereas the `mlx_vlm.generate` API works. Tabularium therefore serves this
model with a **dedicated server**
(`backend/app/services/unlimited_ocr_mlx_server.py`, run in the MLX venv): the
same OpenAI interface, built on the API that works. Every other model uses
`mlx_vlm.server`.

**PaddleOCR-VL's official path has a second runtime.** The Paddle pipeline
(PP-DocLayout for layout + recognition) lives in `<root>/paddle-runtime` and
installs itself the first time the model is served. The installer picks the
build per platform: `paddlepaddle-gpu` from the CUDA index on Linux, the arm64
CPU wheel from PyPI on Apple Silicon.

On a Mac too, Tabularium prepares the environment itself: `<root>/mlx-runtime`
with `mlx-vlm` inside (`services/mlx_runtime.py`), just as for vLLM. The
dashboard process stays free of PyTorch: the MLX server is a subprocess.

Verified on an M5 Pro / 24 GB, from the app rather than the shell:
`mlx_vlm.server` loads `PaddleOCR-VL-1.6-4bit`, the saved configuration points
at the MLX checkpoint, a three-page recognition run finishes 3/3 and the
playground returns 16 elements with bbox and content in ~21 s.

## 1. How the local model registry works

1. **Download**: from the model registry (or `POST /api/models/{id}/download`),
   weights are downloaded from Hugging Face into
   `TABULARIUM_MODELS_DIR/<adapter_id>/` (default `data/models/<adapter_id>/`).
2. **Serve**: `POST /api/models/{id}/serve/start` calls
   `adapter.serve_command(model_path, port)` and launches the process
   (`backend/app/services/serve_manager.py`). One model at a time: starting a
   model always stops the previous one (a consumer GPU can't hold two).
3. **Automatic connection**: start also sets the active inference endpoint
   (`http://127.0.0.1:<port>/v1`) with the right adapter. Nothing to configure
   by hand.

You don't need to install `vllm` yourself: if it's not on the backend's `PATH`
(and `TABULARIUM_SERVE_PYTHON` is not set), start installs it in the shared
environment described above. MonkeyOCRv2 is the exception only for the serving
*code*: it delegates to a dedicated script in the same environment that reads
its own checkout of the official repository (§2, also cloned automatically).

### 1.1 Hugging Face authentication

Public repositories can be downloaded without authentication, but a Hugging
Face token is recommended for large checkpoints: anonymous requests have
stricter limits and can slow down or interrupt downloads. From the model
registry, the administrator can click **Connect Hugging Face**: Tabularium
starts the official OAuth device-code flow, opens the HF page in the browser
and shows the short code to authorize. The token is stored by
`huggingface_hub` in the backend user's cache and is never returned to the
browser, the database or the logs.

Without a browser UI, use the official command:

```bash
backend/.venv/bin/hf auth login
```

Alternatively, start Tabularium with `HF_TOKEN` in the backend's environment.
Never put the token in the repository, logs or API requests. After
authorizing, resume the download from the UI: partial files are reused.

### 1.2 Stop before start: one GPU, one model

A consumer GPU can't hold two models, so starting one always stops the one
being served. "Stopping" means *waiting for the process to die*, not just
signalling it: vLLM takes a few seconds to close its socket after SIGTERM, and
restarting immediately used to kill the new model with
`OSError: [Errno 98] Address already in use`. `stop()` therefore waits, and
escalates to SIGKILL if the process doesn't cooperate.

Before launching, the port is checked again and, if needed, **freed
automatically**: an orphaned server from a session that ended badly is
terminated without asking.

Two deliberate limits:

- a process that **can't be attributed** to Tabularium is never signalled.
  Attribution looks at the live command line (`vllm serve`, the `serve.py`
  wrapper, an adapter's Docker image, or a command containing the models
  folder), **not** at the jobs table: PIDs are recycled, and a row written
  before a reboot may point at any of your processes today. If a Jupyter
  server is on the port, start stops and says so;
- a *zombie* process counts as dead. Otherwise the wait always timed out: the
  child had exited but its PID stayed visible to `os.kill(pid, 0)` until
  someone read its exit status.

## 2. MonkeyOCRv2-B-Parsing

Serve command: `scripts/serve_model.sh` (not `vllm serve` directly).
Requirements verified against the official `Yuliang-Liu/MonkeyOCRv2`
repository:

- Python 3.11, vLLM installed following the official guide (0.25.1 for the
  DFlash draft, or legacy 0.11), plus `parsing/requirements.txt` (`timm`,
  `gradio`, `pypdfium2`). **No manual flash-attn build is needed for
  serving**, only for the training environment, which is separate.
- The official repository (the checkout with `parsing/` and `serve.py`) is
  separate from the weights: Tabularium's registry downloads the weights, and
  the serving code is **cloned automatically** on first start
  (`TABULARIUM_ROOT/vendor/MonkeyOCRv2`). If you already have a checkout,
  `TABULARIUM_TRAIN_REPO` replaces it; likewise
  `TABULARIUM_TRAIN_PYTHON`/`TABULARIUM_SERVE_PYTHON` for the Python
  environment.
- No official Dockerfile exists; the automatic clone is the only path, and it
  needs no manual action.
- **`gcc-13`, `NVCC_PREPEND_FLAGS=-allow-unsupported-compiler` and
  `MAX_JOBS=2` in the script are empirical workarounds** for one installation
  (compiler / CUDA JIT), not documented requirements; another machine may need
  none, or different ones.
- `serve.py` flags (`--gpu-memory-utilization 0.9 --max-model-len 24576
  --max-num-batched-tokens 24576 --max-num-seqs 8`): the official defaults are
  lower (0.5 / 16384 / 16384 / 128). The values in use are **tuned
  empirically** on this 8 GB GPU, not "corrections" to upstream.
- Minimum VRAM: not officially stated; verified empirically on 8 GB.

### 2.1 DFlash (speculative decoding)

The official README (news of 2026-07-24) publishes
`zenosai/MonkeyOCRv2-B-Parsing-DFlash` as a *draft* model for speculative
decoding, announced as "up to 2× faster inference" and supported only by
B-Parsing. It's on by default: the first time the model is served,
`serve_manager` downloads the draft (~170 MB, into
`models/monkeyocrv2-parsing-draft`) and passes it to `scripts/serve_model.sh`,
which hands it to `serve.py -d`. Notes:

- Requires vLLM ≥ 0.12 (the repo documents 0.25.1); with an older version
  `serve.py` refuses to start, so the script checks and starts **without**
  DFlash instead of failing.
- The draft counts against `--gpu-memory-utilization`: with the same flags the
  available KV cache shrinks accordingly.
- `TABULARIUM_MONKEY_DFLASH=0` disables it; if the draft is missing or fails to
  download, the model is served anyway, without acceleration.

### 2.2 Official page preprocessor

`parsing/core_runner.py` never sends the raw page to the VLM: it builds a
`Preprocessor` (weights `preprocessor1.pth` and `preprocessor2.pth`, included
in the checkpoint) and runs every page through it before layout and
recognition. The official CLI skips it only with `--skip-preprocess`,
documented as possibly leading to worse accuracy.

In Tabularium it is the **`monkeyocr`** page transformation engine
(`services/monkey_preprocess.py`, endpoint
`/api/pages/{id}/transform/candidate`), not a hidden stage inside
`inference.py`: the preprocessor changes the geometry, and the 0–1000
normalized bboxes the model returns refer to the image it received. Going
through the page master keeps the existing contract (prefill, crops and export
see the same pixels as the canvas), which is also what the official pipeline
does: it saves the preprocessed pages and works only on those.

It runs as a subprocess in the vLLM runtime (`data/vllm-runtime`), the only
environment aligned with `parsing/requirements.txt` (it needs `timm`). It runs
on CPU by default (`TABULARIUM_MONKEY_PREPROCESS_DEVICE`), because vLLM holds
90% of the VRAM while the model is served: both networks work on a fixed
512×512 input, so the cost doesn't depend on scan resolution (~5 s per page).

## 3. MinerU2.5 (`opendatalab/MinerU2.5-Pro-2605-1.2B`)

```
vllm serve <model_path> --port <port> \
  --logits-processors mineru_vl_utils:MinerULogitsProcessor \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.75 \
  --max-model-len 8192 \
  --max-num-seqs 4 \
  --max-num-batched-tokens 8192 \
  --served-model-name mineru2.5
```

- `--logits-processors` is **mandatory** (official
  `opendatalab/mineru-vl-utils` repo): without it the model loops on
  repetitions. Install `mineru-vl-utils` **without** the `[vllm]` extra (it
  declares vLLM `<0.22.0`; the cloud template uses 0.21.0, which is
  compatible).
- `--dtype`, `--gpu-memory-utilization`, `--max-num-seqs` and
  `--max-num-batched-tokens` are not recommended by any official guide for a
  consumer GPU (no minimum VRAM stated, only A100 benchmarks): they are values
  chosen for 8 GB, to be validated empirically. The checkpoint declares
  `max_position_embeddings=32768` at the top level but `8192` in
  `text_config`; vLLM uses the effective limit of 8192, and Tabularium respects
  it without `VLLM_ALLOW_LONG_MAX_MODEL_LEN`, which can produce NaNs or
  out-of-bounds CUDA accesses.
- ~2.5 GB checkpoint: comfortable on 8 GB even with the values above.

## 4. dots.mocr (`dots-studio/dots.mocr`)

```
vllm serve <model_path> --port <port> \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.80 \
  --max-model-len 16384 \
  --chat-template-content-format string \
  --trust-remote-code \
  --served-model-name dots-mocr
```

- Native architecture in **vLLM ≥ 0.11.0** (official PR #24645): the old
  `vllm==0.9.1` pin is no longer needed.
- `--chat-template-content-format string` is **mandatory** per the official
  README (it only controls text serialization for the chat template and
  doesn't interfere with OpenAI-style image input).
- **Actual checkpoint ~6.1 GB** (two safetensors shards). On 8 GB the margin is
  tight: `--gpu-memory-utilization 0.80` and `--max-model-len 16384` are a
  cautious choice, not vendor-verified.

## 5. PaddleOCR-VL-1.6 (`PaddlePaddle/PaddleOCR-VL-1.6`)

```
vllm serve <model_path> --port <port> \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --served-model-name PaddleOCR-VL-1.6
```

- Base command verified against the official vLLM recipe.
- **`--max-model-len` is essential**: `config.json` declares
  `max_position_embeddings=131072`, and without an explicit cap vLLM uses it as
  the default and preallocates a huge KV cache (vLLM issue #31372).
- Small checkpoint (~1.8 GB); maintainers only confirm an RTX 3060 12 GB as the
  minimum tested configuration. There's no official confirmation for 8 GB, but
  the arithmetic margin is large.
- **Prefill uses the official pipeline.** Prefill calls the official
  `PaddleOCRVL.predict` flow: layout detection, then vision-language
  recognition of the regions found. In **Settings → Models**, Automatic mode
  keeps PaddleX defaults; you can force the documented layout, orientation,
  unwarping, charts, seals, image OCR, Markdown, merge and queue options.
  Generation and image limits reach the same `predict` call.

## 6. Unlimited-OCR (`baidu/Unlimited-OCR`), via Docker

**The one exception to full automation**: the architecture is not in vLLM's
stable pip wheel, so the vLLM environment Tabularium prepares isn't enough.
You need **Docker + `nvidia-container-toolkit` installed on the machine**
(system dependencies Tabularium can't install for you). Once they're present,
Start works like for any other model.

Local serving uses the dedicated Docker image `vllm/vllm-openai:unlimited-ocr`:

```
docker run --rm --gpus all --network host --ipc host \
  -v <model_path>:/model \
  vllm/vllm-openai:unlimited-ocr \
  /model \
  --trust-remote-code \
  --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching --mm-processor-cache-gb 0 \
  --max-model-len 12288 \
  --port <port> --gpu-memory-utilization 0.85 \
  --served-model-name Unlimited-OCR
```

- The logits processor is **mandatory**: without it, long documents loop on
  `<|det|>` tokens. The flag uses underscores as in the official recipe; vLLM's
  `FlexibleArgumentParser` normalizes `_` to `-`, so it is valid.
- The `-v <model_path>:/model` mount points at weights already downloaded by
  the registry instead of downloading them again in the container.
- `--max-model-len 12288` is **not** in the official recipe, which keeps the
  checkpoint default (32768). On 8 GB that default doesn't fit even with
  offload: 32,768 tokens need 1.88 GiB of KV cache and 0.93 remain. At 12,288
  the cache is enough (16,432 tokens) and still above the adapter's output
  budget (8,192 tokens + ~1,300 for the image). It's a per-machine tuning, not a
  requirement: raise it on a larger GPU.
- 6.3 GiB of weights don't fit in 8 GB: `serve_manager` adds
  `--cpu-offload-gb 4` automatically (see §12), bringing resident weights down
  to 2.23 GiB.
- Stopping the server sends SIGTERM to the `docker run` process, which Docker
  forwards to the container. It's slower than a native process, which is
  normal: `stop()` waits for the process to actually end (see §1.2).

### 6.1 System prerequisite: NVIDIA Container Toolkit

`docker run --gpus all` requires the NVIDIA Container Toolkit. Without it,
start exits with code 125 and `could not select device driver "" with
capabilities: [[gpu]]`. Watch out for a tricky case: a distribution upgrade
(`do-release-upgrade`) **disables the NVIDIA repository** by renaming it to
`.save` and removes the package, but leaves the `nvidia` entry in
`daemon.json`, so Docker looks configured when it isn't. To restore it:

```bash
sudo cp /etc/apt/sources.list.d/nvidia-container-toolkit.list.save \
        /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

The model registry states the prerequisite before you click, and
`serve_manager` refuses to start with the install command rather than the raw
Docker error.

### 6.2 Field measurements (sample page `A`, RTX 4060 Laptop 8 GB)

| | MonkeyOCRv2 (resident) | Unlimited-OCR (4 GiB offload) |
|---|---|---|
| throughput | 152.6 tok/s | 13.3 tok/s |
| page layout | register labelled `Text` | register labelled **`table`** |
| table | 6 banded calls, unstable columns (12 vs the 9 printed) | 1 call, **76 complete rows**, header included |
| full page end2end | — | 5 blocks in 263 s, `finish_reason=stop` |

Table content arrives as **HTML** inside the `<|det|>table …` block, not as
Markdown as the model card suggests; prefill converts it with
`otsl.html_to_otsl` (verified: a 76×8 grid).

## 7. GLM-OCR (`zai-org/GLM-OCR`)

```
vllm serve <model_path> --port <port> \
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 3}' \
  --max-num-batched-tokens 32768 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.85 \
  --served-model-name glm-ocr
```

- Requires **vLLM ≥ 0.19.0**. Native architecture (Transformers ≥ 5.0.0):
  `--trust-remote-code` is not in the official command; if the server rejects
  the architecture on an older wheel, it's the first flag to add.
- `--max-num-batched-tokens 32768` avoids the known "exceeds pre-allocated
  encoder cache size" error on high-resolution images.
- Actual checkpoint ~2.7 GB: comfortable on 8 GB.
- **No OCR integration** (prompt/parsing) is implemented yet in Tabularium:
  the model can be deployed, but structured prefill can't use it yet.

## 8. DeepSeek-OCR-2 (`deepseek-ai/DeepSeek-OCR-2`)

```
vllm serve <model_path> --port <port> \
  --trust-remote-code \
  --logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching --mm-processor-cache-gb 0 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --served-model-name deepseek-ocr-2
```

- Requires **vLLM ≥ 0.12.0**. The architecture has a history of breaking
  changes across vLLM versions (vllm-project/vllm#33252): check
  `vllm serve --help` before a real deployment.
- License: **Apache-2.0**.
- "Gundam mode" (optical compression / crop tiling) is hardcoded in the vLLM
  integration: no flag controls it.
- Official card: "a single GPU ≥ 8 GB is typically sufficient for BF16
  inference", a tight but declared margin.
- No OCR integration implemented yet (like GLM-OCR).

## 9. Qwen3-VL-8B-Instruct (`Qwen/Qwen3-VL-8B-Instruct`)

```
vllm serve <model_path> --port <port> \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --limit-mm-per-prompt '{"image":4,"video":0}' \
  --served-model-name qwen3-vl-8b
```

- Native architecture in **vLLM ≥ 0.11.0**: no `--trust-remote-code`.
- **Actual checkpoint ~16.3 GB in bf16** (the "9B" on the card is the
  parameter count, not the size on disk). On 8 GB the size warning always
  fires; an official FP8 variant (`Qwen/Qwen3-VL-8B-Instruct-FP8`, ~9.9 GB) and
  community AWQ 4-bit ones (~7 GB) exist, but none leaves real room for KV
  cache + vision encoder on 8 GB. Nothing is blocked: you can still try.
- Prefill uses the official QwenVL parser `qwenvl html`: native `data-bbox`
  values (0–1000) become Tabularium blocks, and HTML tables are converted to
  OTSL through the internal grid. **Settings → Models** starts from the
  official image limits of the HTML cookbook (`min_pixels=512×32²`,
  `max_pixels=2048×32²`) and lets you change them.
- The previous MLX benchmark validated layout and text, but its table test was
  invalid: Qwen returned Markdown when asked for OTSL. The new `qwenvl html`
  path is implemented and awaits a live benchmark on Metal.

## 10. Any Hugging Face model

From the model registry → **Add custom model** (or `POST /api/models/custom`)
you can register any Hugging Face repository:

```json
{
  "display_name": "My model",
  "hf_repo": "org/model-name",
  "hf_revision": "main",
  "served_model_name": "my-model",
  "trust_remote_code": true,
  "max_model_len": 8192,
  "gpu_memory_utilization": 0.85,
  "extra_args": "--dtype bfloat16 --tensor-parallel-size 1"
}
```

Tabularium builds a generic `vllm serve <repo> ...` with the flags you
provide; `extra_args` passes extra vLLM flags not covered by the form. No OCR
protocol (prompt/parsing) is verified for these models: they can be
downloaded and served, but are not integrated in assisted prefill.

`DELETE /api/models/custom/{id}` removes the definition entirely (and the
weights, if downloaded); `DELETE /api/models/{id}` deletes only the weights,
keeping the definition for a new download.

## 11. How the size (VRAM) warning works

Every registry entry exposes `vram_warning` (string or `null`): it compares the
checkpoint size (real size on disk once downloaded, otherwise the declared
estimate) with the free VRAM reported by `nvidia-smi`, with a 35% margin for KV
cache and activations. **It's a warning, not a block**: unlike the training
preflight (`domain/vram.py`), you always decide whether to try anyway. If
the server runs out of memory, reduce `--max-model-len` (the per-request KV
cache), **not** `--gpu-memory-utilization`, which would shrink the budget
further. When the weights alone eat the budget, the warning says so and points
to a remote GPU: no parameter will save the situation.

## 12. Offloading weights to RAM (`--cpu-offload-gb`)

A checkpoint larger than the available VRAM doesn't start at all: vLLM
computes a negative KV cache and exits with `No available memory for the cache
blocks`. `--cpu-offload-gb N` (UVA backend) maps `N` GiB of weights to pinned
CPU memory and reads them zero-copy on every forward pass: it costs PCIe
bandwidth, **not quality**.

`serve_manager.cpu_offload_gib()` computes it and adds it only when needed,
both for `vllm serve` adapters and those launched inside the official Docker
image. The formula starts from the card's capacity (not the momentarily free
memory, since the previous server is stopped right after) and leaves ~2 GiB for
the KV cache. Measurements on an RTX 4060 Laptop 8 GB:

| Model | Weights | Without offload | With offload | Throughput |
|---|---|---|---|---|
| DeepSeek-OCR-2 | 6.33 GiB | KV cache **-1.03 GiB**, doesn't start | `-gb 3` → 3.22 GiB resident, KV 2.08 GiB | 16.7 tok/s |
| Unlimited-OCR | 6.3 GiB | doesn't start | `-gb 4` → 2.23 GiB resident, KV 0.94 GiB | 13.3 tok/s |
| MonkeyOCRv2 | 1.92 GiB | fits, no offload | — | 152.6 tok/s |

The ratio is roughly 1:1 between GiB moved and GiB of KV cache recovered. The
speed cost is **about 10×** compared with a fully resident model: usable for
comparisons and a few pages, not for processing a whole corpus.

## 13. Summary

| Model | Local | Cloud (Modal) | Checkpoint | Notes on 8 GB |
|---|---|---|---|---|
| MonkeyOCRv2-B-Parsing | dedicated script | `modal_vllm.py` | ~1.5 GB | tuned, in production |
| MinerU2.5 | `vllm serve` | `modal_mineru.py` | ~2.5 GB | comfortable |
| dots.mocr | `vllm serve` | `modal_dots_ocr.py` | ~6.1 GB | OOM measured on an RTX 4060 8 GB, even with offload |
| PaddleOCR-VL-1.6 | `vllm serve` | `modal_paddleocr_vl.py` | ~1.8 GB | comfortable |
| Unlimited-OCR | Docker | `modal_unlimited_ocr.py` | ~6 GB | tight |
| GLM-OCR | `vllm serve` | `modal_glm_ocr.py` | ~2.7 GB | OOM/cache issues measured on an RTX 4060 8 GB |
| DeepSeek-OCR-2 | `vllm serve` | `modal_deepseek_ocr.py` | ~6 GB | works with 4 GB CPU offload |
| Qwen3-VL-8B | `vllm serve` | `modal_qwen3_vl.py` | ~16.3 GB | beyond 8 GB, warning expected |
| Custom model | generic `vllm serve` | — | varies | warning based on actual size |

Detailed sources (model cards, READMEs, vLLM recipes, issues) are cited in the
docstrings of the adapters in `backend/app/services/model_adapters.py`.

## 14. TeleOCR (CUDA, cloud)

- **Upstream input and prompts.** Layout at 1036×1036, bbox parser, system
  prompt, per-task sampling and a no-repeat-ngram logits processor as upstream;
  text, table (OTSL) and formula crops use the upstream prompts.
- **Detection or Segmentation.** In **Settings → Models** the official
  `Detection` default can be switched to `Segmentation`, which upstream
  recommends for degraded scans.
- **Not equivalent to the full pipeline.** Tabularium's adapter orchestrates
  layout and crop requests itself; it doesn't yet run `infer.py`'s
  asynchronous per-page batching or the middle-JSON post-processing, so results
  are not declared equivalent to the full official pipeline.
- **Serving it.** The cloud recipe installs the root of the official
  repository, which registers the custom vLLM architecture and provides the
  processor, with the vLLM/Transformers versions pinned upstream. On Modal:
  `scripts/cloud/modal_teleocr.py`; on Vast: TeleOCR in the cloud model
  selector. Locally it needs a CUDA endpoint.
