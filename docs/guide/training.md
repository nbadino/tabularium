# Fine-tuning

Tabularium trains **the model you chose, with the toolchain its own documentation
prescribes**. There is no universal recipe: MonkeyOCRv2 is trained with its bundled
ms-swift fork, GLM-OCR with LLaMA-Factory, PaddleOCR-VL with PaddleFormers. The
training center reads one *recipe* per adapter (`services/train_recipes.py`) and
generates exactly that command — or refuses to start, pointing at the official
page it could not follow.

Two documents hold this knowledge:

- **this page** — what Tabularium does, how to run it, and what has actually been
  tested;
- **[official training docs](../training-official-docs.md)** — the vendor commands
  and hyperparameters copied verbatim, with sources, kept as the reference the
  recipes are checked against.

The design rationale is in [AGENTS.md](https://github.com/nbadino/tabularium/blob/main/AGENTS.md)
§10 and §M20.

## Principles

1. **One recipe per adapter, and the recipe is the vendor documentation.** The
   generator knows no model by name; it dispatches on the recipe's toolchain.
2. **No invented numbers.** When the vendor publishes hyperparameters, they are
   the defaults. When it does not (Unlimited-OCR, dots.ocr, DeepSeek-OCR-2), the
   recipe says so and falls back to the toolchain defaults, marked
   `experimental`.
3. **Fail explicitly.** A recipe that is documented but not runnable (MinerU2.5,
   whose official config ships only inside a ZIP) blocks in the preflight with the
   reason and the documentation link. It never emits a plausible-looking script.
4. **The target never changes between views.** Dataset views reshape containers
   and prompts; the transcription, the OTSL, and the image are the canonical
   annotation. A view that rewrote the target would be a different dataset.
5. **Honest maturity.** `verified` only after a real run recorded in the test
   log below; otherwise `experimental`; `planned` when the official path is not
   executable; `inference-only` when the vendor ships no training code.

## Recipe matrix

`GET /api/system/train-recipes` returns the same table, including defaults,
flags, official notes and `script_ready`.

| Adapter | Model | Toolchain | Training | Data view | Maturity |
|---|---|---|---|---|---|
| `monkeyocrv2-parsing` | `zenosai/MonkeyOCRv2-B-Parsing` | ms-swift (bundled fork) | LoRA, full | `ms-swift-jsonl` | **verified** (1-step run + merge) |
| `qwen3-vl-8b` | `Qwen/Qwen3-VL-8B-Instruct` | ms-swift | LoRA, full | `ms-swift-jsonl` | **verified** (3-step run + merge) |
| `unlimited-ocr` | `baidu/Unlimited-OCR` | ms-swift | LoRA, full | `ms-swift-jsonl` | **verified** (3-step run + merge) |
| `deepseek-ocr` | `deepseek-ai/DeepSeek-OCR-2` | ms-swift | LoRA, full | `ms-swift-jsonl` | **verified** (3-step run + merge) |
| `dots-ocr` | `dots-studio/dots.mocr` | ms-swift | LoRA, full | `ms-swift-jsonl` | **verified** (3-step run + merge) |
| `glm-ocr` | `zai-org/GLM-OCR` | LLaMA-Factory | LoRA, full | `llamafactory-sharegpt` | **verified** (2-step run + merge) |
| `paddleocr-vl` | `PaddlePaddle/PaddleOCR-VL-1.6` | PaddleFormers | LoRA, full | `paddleformers-messages` | **verified** (3-step run + merge) |
| `mineru2.5` | `opendatalab/MinerU2.5-Pro-2605-1.2B` | official `run_mineru.py` wrapper | — | `ms-swift-jsonl` | planned (bundle required) |
| `teleocr` | `StarDoc-AI/TeleOCR` | — | — | — | inference-only |
| `lightonocr-3` | `lightonai/LightOnOCR-3-1B` | — | — | — | inference-only |

`text_rec`, `table` and `formula` are the recognition families. `layout` is added
only for models whose protocol produces bounding boxes: GLM-OCR and PaddleOCR-VL
are trained on recognition only, because their outputs have no bbox.

## What each vendor says, and what Tabularium does

### MonkeyOCRv2 — ms-swift, bundled fork (verified)

Source: [`parsing/train/README.md`](https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/parsing/train).

- `--model_type monkeyocrv2 --template monkeyocrv2`; the fork is **required**:
  upstream ms-swift registers a different `monkeyocrv2` template for the
  encoder (`<|IMAGE|>`), while Parsing expects `<|image_pad|>` (id 151655).
- ViT frozen (`--freeze_vit True`), aligner and LLM trained; LoRA r8/α32 on
  `all-linear`; lr 1e-5, 1 epoch, batch 4, `max_length 16384`,
  `max_pixels 1003520`, `deepspeed zero1`, `packing False`,
  `padding_free True`, bf16, gradient checkpointing.
- The fork calls the flag `--train_type`; upstream ms-swift uses `--tuner_type`.
  The recipe carries the right one.
- Resume: the highest `checkpoint-N` in the output directory.
- Tabularium changes only what the product needs: a validation split
  (`--val_dataset`, official uses none) and a fallback from `flash_attention_2`
  to `eager` when flash-attn is missing, logged.

### Qwen3-VL — ms-swift (integration)

Source: [Qwen3-VL Best Practice](https://github.com/modelscope/ms-swift/blob/main/docs/source_en/BestPractices/Qwen3-VL-Best-Practice.md).

- LoRA lr 1e-4, 1 epoch, batch 1, grad-accum 2, `max_length 4096`,
  `--packing true`, `--deepspeed zero2`, `freeze_vit`/`freeze_aligner` true,
  `IMAGE_MAX_TOKEN_NUM=1024` (not `--max_pixels`).
- The vendor also publishes `qwen-vl-finetune` (torchrun + ZeRO-3). It is not
  used here because registering a dataset requires editing
  `qwenvl/data/__init__.py` in the checkout, while ms-swift accepts a JSONL
  path. The recipe notes this; the official lr there is 1e-6…2e-7 for full
  tuning, not for LoRA.

### GLM-OCR — LLaMA-Factory (vendor)

Source: [`examples/finetune/README.md`](https://github.com/zai-org/GLM-OCR/blob/main/examples/finetune/README.md).

- `template: glm_ocr`, ShareGPT (`messages` + `images`), registered in
  `dataset_info.json`; Tabularium writes both into the run directory and passes
  it as `dataset_dir`.
- Full: `freeze_vision_tower: true`, `freeze_multi_modal_projector: true`,
  `freeze_language_model: false`, lr 1e-5, grad-accum 2.
- LoRA: rank 8, `lora_target: all`, lr 1e-4, grad-accum 4.
- Both: batch 4, 3 epochs, cosine, warmup 0.1, bf16.
- Official prompts, kept verbatim: `Text Recognition:`, `Table Recognition:`,
  `Formula Recognition:`. The image comes before the prompt (`<image>…`).
- Deviation, documented: the official YAML is a *minimal* example with
  `cutoff_len: 2048`; a register table does not fit, so the default is 16384.
- Merge: `llamafactory-cli export --adapter_name_or_path … --export_dir …`.

### PaddleOCR-VL — PaddleFormers (vendor)

Source: [PaddleFormers best practices](https://github.com/PaddlePaddle/PaddleFormers/tree/develop/examples/best_practices/PaddleOCR-VL).

- `paddleformers-cli train <config> key=value`; the official configs for full
  and LoRA are used as-is, with only dataset/model/output/hyperparameters
  overridden.
- Dataset is `messages` (`<image>OCR:` / `<image>Table Recognition:` /
  `<image>Formula Recognition:`) with `template: paddleocr_vl` — the same shape
  as ms-swift, so no second exporter is needed.
- Full 5e-6, LoRA 5e-4; batch 8, grad-accum 8, 2 epochs, `max_seq_len 16384`,
  eval/save 400, bf16, `pre_alloc_memory` 24/16.
- No bbox in the official dataset: layout remains the PP-DocLayout detector,
  trained separately (PaddleX, dataset view `build-paddle`).
- PaddleOCR-VL-1.6 has no published fine-tuning config; the 1.5/0.9B configs are
  used because the vendor states the architecture is unchanged. This is the
  recipe's `experimental` caveat.
- Merge: `paddleformers-cli export <export yaml>` → `<output_dir>/export`.

### MinerU2.5 — official wrapper, planned

Source: [MinerU lesson 04](https://github.com/opendatalab/mineru-tutorials/tree/main/04%E8%AF%BE%EF%BC%9AMinerU%201.2B%20%E6%A8%A1%E5%9E%8B%E5%BE%AE%E8%B0%83).

- The vendor explicitly says **do not use `swift sft`**; use
  `python run_mineru.py sft --config configs/sft/sft_table_rec.yaml`. The code
  (`mineru_sft_code.zip`) and that YAML are attached to the lesson, not
  versioned in a repository.
- Tabularium therefore does not generate a script: the recipe is `planned`, the
  UI links to the lesson and blocks the start. The data view and the official
  prompts (`\nTable Recognition:`, etc.) are ready for when the bundle can be
  declared as a checkout.

### Unlimited-OCR, dots.ocr, DeepSeek-OCR-2 — ms-swift integration, experimental

- Unlimited-OCR's repository says only that the model “supports training with
  ms-swift”; it publishes no command or hyperparameters.
- dots.ocr's repository is inference-only; the community training script and
  ms-swift both exist, but neither is vendor documentation.
- DeepSeek-OCR-2 documents inference only. Unsloth publishes a notebook with a
  different data format (roles `<|User|>`/`<|Assistant|>`, r16/α16 on seven
  modules) and a collator that is not portable to ms-swift.

Tabularium offers the ms-swift path for these three because it is executable and
the framework registers them, but labels it `experimental`: the numbers are
toolchain defaults, not a vendor recipe.

### TeleOCR and LightOnOCR-3 — inference only

Neither repository ships training code or a runnable config. Their papers/blog
describe the method (LightOnOCR-3's layout reward is `min(IoU/0.75, 1)`, which
is why evaluation has a strict 0.75 threshold), but not a reproducible SFT
recipe. They stay `inference-only`; the UI says so instead of offering a run
that cannot work.

## Data views

`services/training_views.py` materialises the run's view from the immutable
dataset snapshot (`services/dataset_builder.py`):

| View | Files | Consumer |
|---|---|---|
| `ms-swift-jsonl` | `dataset/train.jsonl`, `val.jsonl` | all ms-swift models |
| `paddleformers-messages` | same `messages` shape | PaddleOCR-VL |
| `llamafactory-sharegpt` | `dataset/train.json`, `val.json`, `dataset_info.json` | GLM-OCR |

Every run writes `dataset/view.json`: which families entered, with which prompts,
how many samples, and which families were empty. `run.json` embeds it, so a
checkpoint carries its data provenance.

The view also **realigns the prompts to the model being trained**. A snapshot may have been built
with another adapter (say GLM's `Text Recognition:`), but a MonkeyOCRv2 run must see the prompts
MonkeyOCRv2 uses at inference. The recipe's training prompts win; otherwise the adapter's own
prompts are restored; otherwise the canonical ones are kept.

## Known limits

- **Table crops are trained in OTSL for every model.** It is the vendor-recommended format for
  MonkeyOCRv2 and PaddleOCR-VL. Qwen3-VL, dots.ocr, Unlimited-OCR and DeepSeek-OCR-2 generate HTML
  or grounded Markdown at inference, and their native path is full-page end-to-end: the per-crop
  OTSL recipe improves recognition when those prompts are used, but it does not train the native
  full-page parser. `verified` covers the per-crop recipe that was actually run, not the native
  parser; the fix is an end-to-end dataset family (full page + native HTML/Markdown target), not a
  silent format conversion.
- **GLM-OCR's table target is unspecified by the vendor.** The official README defines only the
  three prompt names; Tabularium teaches OTSL because it is the dataset's canonical table format.
- **LoRA merge runs where the toolchain lives.** For a remote run the merge now runs on the GPU
  host and only the merged model is downloaded; if that host is gone (or its run directory was
  cleaned) Tabularium falls back to a local merge, which still needs `swift` /
  `llamafactory-cli` / `paddleformers-cli` and the base weights on the dashboard. The plan shown
  before the click says which of the two will happen and what is missing.
- **Automatic provisioning is a fresh environment per rental.** It installs the recipe's
  toolchain and pins; a second rental starts from the image, not from a warm cache. `dots.ocr`'s
  flash-attn build can take 20–30 minutes and is the slowest profile. Manual rent still prepares
  only MonkeyOCRv2; other recipes use the automatic card.
- **Environment pins are per model, not per toolchain.** Qwen3-VL needs transformers ≥ 5;
  Unlimited-OCR and DeepSeek-OCR-2 only import on 4.46.3 (their remote code still uses
  `is_torch_fx_available` / `LlamaFlashAttention2`); GLM-OCR's LLaMA-Factory path needs
  transformers ≤ 5.8 (`warmup_ratio` is gone in 5.20); PaddleOCR-VL needs Triton. The recipe
  carries these pins in `environment`; installing one ms-swift env for all five models cannot work.

## Environment

Each toolchain has its own environment. A model is never trained with another
model's environment.

| Toolchain | Required | Environment variable |
|---|---|---|
| ms-swift, MonkeyOCRv2 | checkout with the bundled fork | `TABULARIUM_TRAIN_REPO=/path/to/MonkeyOCRv2` |
| ms-swift, other models | `swift` in the training env | `TABULARIUM_TRAIN_PYTHON`, or `swift` on `PATH` |
| LLaMA-Factory | checkout + `llamafactory-cli` | `TABULARIUM_LLAMAFACTORY_DIR` |
| PaddleFormers | checkout + `paddleformers-cli` | `TABULARIUM_PADDLEFORMERS_DIR` |

These are the toolchain environments. The **exact pins are per recipe** (`environment` in
`GET /api/system/train-recipes`), because two models can need incompatible transformers:
Qwen3-VL needs ≥ 5, Unlimited-OCR/DeepSeek-OCR-2 need 4.46.3 with ms-swift 4.6.0, GLM-OCR needs
≤ 5.8, dots.ocr needs 4.51.0 plus flash-attn, PaddleOCR-VL needs Triton. The measured values are
in the test log below.

MonkeyOCRv2 installation (official):

```bash
conda create -n monkeyocrv2-train python=3.11 -y
conda activate monkeyocrv2-train
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
pip install transformers==4.57.1 accelerate==1.11.0 qwen_vl_utils==0.0.14
pip install flash-attn==2.8.3 --no-build-isolation
pip install deepspeed==0.17.1   # the official lora.sh passes --deepspeed zero1
# from parsing/train:
git submodule update --init --recursive
pip install -e ./ms-swift
```

GLM-OCR installation (official LLaMA-Factory setup, plus the transformers
upgrade the GLM-OCR README requires):

```bash
git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git
pip install -e LLaMA-Factory
pip install -r LLaMA-Factory/requirements/metrics.txt
pip install --upgrade transformers
```

PaddleFormers installation: the official Paddle container (`paddle:3.3.0-gpu-cuda12.6-cudnn9.5`)
or `pip install -e '.[paddlefleet]'` from the PaddleFormers checkout.

Remote GPU hosts: `scripts/cloud/setup_cloud_train.sh` provisions the
MonkeyOCRv2/ms-swift environment and writes the `.train_ready` marker. It installs torch from the
cu128 index first (override with `TABULARIUM_TORCH_INDEX`); otherwise `pip` picks the newest torch,
which may not initialise on an older driver. The install is **conditional**: if the venv already has
a torch whose CUDA matches the index chosen for the host driver, 800 MB are not downloaded again.
The script also disables a `cuda-compat` userspace library when a real driver is present (it can
shadow the driver and make every CUDA call fail), and creates the empty
`swift/llm/dataset/data/dataset_info.json` that the MonkeyOCRv2 fork omits but imports at startup. For GLM
or Paddle, provision the toolchain's checkout on the host and point
`ssh_train_repo` at it; the marker check remains the readiness test.

## Workflow

1. **Annotate and approve.** The dataset is built from annotations; on approved
   pages only confirmed blocks count.
2. **Build the dataset.** Dataset page → *Build*. The snapshot is immutable.
3. **Fine-tuning → Training → New training.** Choose the model and training type.
   The official recipe supplies the parameters; Advanced lets you edit or restore them.
   MonkeyOCRv2 LoRA also has a measured 8 GB configuration in Advanced. The preflight tells you what is missing
   (toolchain path, GPU, VRAM, dataset families, an unavailable recipe).
4. **Rent a GPU on Vast.ai.** Set the hourly budget and press *Rent and train*. Tabularium
   searches Vast.ai within that ceiling, picks the best GPU (most VRAM, then newest compute
   capability, then reliability), rents it, installs the model's own environment on the host,
   waits for the readiness file and starts the run. The card shows rent → SSH → environment →
   training and a Cancel button. Open *Show logs* when you need setup details. No host, Python
   path or repository to type.
5. **Runs and results.** Select the run above its dashboard. Progress, loss and GPU use stay visible. Open *Show logs* for the
   streamed raw output. Stop and resume from the checkpoint.
6. **Prepare the checkpoint — automatically.** The finished panel shows the final train/eval
   loss, the steps, the duration and **where the weights are on this machine**, with their size.
   Preparation starts by itself at the end of the run (disable *Prepare and register when training
   ends* under Advanced): a LoRA adapter is merged with the toolchain's official command
   (`swift export`, `llamafactory-cli export`, `paddleformers-cli export`). For a remote run the
   merge happens **on the GPU host**, where the toolchain and the base weights live, and the
   merged model is downloaded into the run; only then is it registered. A local run merges here.
   The job has a phase (merge → download → register), and the HTTP request returns immediately.
   Open the preparation log for technical details. If the plan is not ready (host gone, no local toolchain), the panel says why and
   leaves the manual button.
7. **Serve and evaluate.** The registered checkpoint is a model artifact; choose its inference
   destination separately in Models (supported local CUDA, Apple Silicon MLX, or compatible remote
   GPU/endpoint options for that model). The training executor does not choose where inference runs.
   Evaluation then measures the active model on approved pages. The finished panel links to both
   steps and says that serving comes first.

The history selector in Runs and results lists every run of the project (date, model, state);
selecting one loads its metrics, log and checkpoint, and *Resume from this run* copies its
checkpoints into the next run. The form always configures a **new** run, which is why it does not
silently inherit the selected run's model. Selecting Resume explicitly copies that run's configuration.

For an existing local, Vast.ai, RunPod or SSH GPU, expand *Use an existing GPU*
inside the launch section. Local training requires NVIDIA CUDA; Apple Silicon MLX is an
inference destination for supported models. Remote telemetry never makes local CUDA available.
Training completion, checkpoint preparation and inference readiness are separate states.
The overview directs incomplete preparation back to the checkpoint, then opens the registered
model's own inference setup before offering evaluation. After training, evaluation requires
held-out validation pages; a dataset with no validation pages says so before launch.

Presets are GPU budgets, not recipes: they change epochs, batch, accumulation,
LoRA rank and length, and never the learning rate, which stays the one from the
official recipe.
### One-click environments (per recipe)

The automatic path installs a **different environment per model**, because the measured pins are
incompatible (transformers 4.46 vs 5.20, ms-swift fork vs upstream, LLaMA-Factory vs PaddleFormers).
`scripts/cloud/setup_train_env.sh` receives a profile and writes `ready.json` with the Python and
repository to use:

| Profile | Recipes | What is installed |
|---|---|---|
| `monkey` | MonkeyOCRv2 | **prebuilt image** `ghcr.io/nbadino/tabularium-train-monkey:monkey-v3`: torch 2.6.0, transformers 4.57.1, bundled ms-swift fork (no runtime pip) |
| `qwen` | Qwen3-VL | prebuilt `tabularium-train-qwen:qwen-v1`; upstream ms-swift 4.6, transformers 5.20 |
| `legacy` | Unlimited-OCR, DeepSeek-OCR-2 | prebuilt `tabularium-train-legacy:legacy-v1`; ms-swift 4.6, transformers 4.46.3 |
| `dots` | dots.ocr | **runtime provisioning** (flash-attn ABI still blocks a reliable image); ms-swift 4.6, transformers 4.57.1 |
| `glm` | GLM-OCR | prebuilt `tabularium-train-glm:glm-v1`; LLaMA-Factory, transformers 5.8 |
| `paddle` | PaddleOCR-VL | prebuilt `tabularium-train-paddle:paddle-v1`; PaddleFormers, Triton |

Every trainable profile except dots.ocr boots a versioned image instead of installing at rental; the
images are built by `.github/workflows/train-image-monkey.yml` and
`.github/workflows/train-images.yml` and are public on GHCR. dots.ocr still uses runtime
provisioning because its flash-attn wheel did not load against the torch 2.6 base (ABI mismatch). Only the **environment** is baked in for Qwen/legacy/dots/GLM/Paddle; their weights
come from HuggingFace at the first run, because an 8B checkpoint would exceed the GitHub runner
disk and produce a 20 GB image. MonkeyOCRv2 additionally has its 1.5 GB weights baked in.

The budget is the only infrastructure decision left to the user: the backend ranks offers by
**VRAM first**, then compute capability, reliability and price, so a slightly more expensive
24 GB card wins over a slower one at the same price, and a 40 GB card wins when it fits the budget.
The GPU is destroyed automatically if rental, provisioning or training fails; after the remote
merge/download and registration it is destroyed too, so the one-click path leaves no instance
running. The merged model stays locally and in Models.

The SSH/provider fields and the manual rent button remain under the advanced section for existing
instances; manual provision still prepares only MonkeyOCRv2, and says to use the automatic card
for the other recipes.


## Verifying a recipe

A recipe becomes `verified` only after a real run, recorded here:

```bash
# smoke test: the recipe runs, quality is irrelevant
# 2 synthetic samples, --max_steps 1, --save_steps 1
```

Each entry: date, machine, adapter, command, outcome, and what changed in the
code as a consequence.

### 2026-10-18 — recipe audit (no GPU)

- **What**: every supported model's official documentation was read and
  compared with the generated script. Findings that changed the code:
  1. GLM-OCR's vendor path is LLaMA-Factory, not ms-swift → new toolchain and
     ShareGPT view.
  2. PaddleOCR-VL's current vendor path is PaddleFormers, which supports LoRA
     and accepts the `messages` format → ERNIEKit full-only path removed.
  3. MinerU2.5 explicitly forbids `swift sft`; its config is only in a ZIP →
     recipe marked `planned`, no script generated.
  4. MonkeyOCRv2's bundled fork uses `--train_type`, upstream uses
     `--tuner_type`; the recipe carries the flag.
  5. The VRAM estimate used MonkeyOCRv2's geometry for every model → the
     recipe now declares its own, and an unknown model is not blocked on a
     guessed number.
  6. Merge/export was hardcoded to `swift export` → per toolchain.
- **Outcome**: recipe matrix and view tests pass (`tests/test_train_recipes.py`,
  `tests/test_training_views.py`); this does **not** prove a GPU run.
- **Next**: a real smoke test per toolchain on a verified CUDA host.

### 2026-10-18 — first real GPU run: generated script, 1 step, merge (RTX A5000)

- **Host**: existing Vast.ai instance, RTX A5000 24 GB, driver 555.58.02, torch 2.6.0+cu124,
  transformers 4.57.1, DeepSpeed 0.17.1, no flash-attn.
- **What**: a script generated by `trainer_script.generate_script` for `monkeyocrv2-parsing`
  (`executor=vast`, `ssh_train_repo=…/parsing/train`), run from the synced run directory with a
  2-sample micro-dataset, `max_length 512`, 1 epoch → 1 step.
- **Result**: ✅ `train_loss 0.8659`, 1.91 GiB peak, `checkpoint-1/adapter_model.safetensors`;
  `swift export --merge_lora true` produced a 1.4 GB merged model.
- **Executor end-to-end**: the same script launched through `training_executor.VastExecutor`
  (rsync → remote `bash train.sh` → log sync → exit 0 → `download_artifacts`): 38 files,
  checksums OK.
- **Found and fixed before it could run**:
  1. the fork omits `swift/llm/dataset/data/dataset_info.json`; `import swift.llm.dataset` raised
     `JSONDecodeError` in `register_dataset_info`. The setup now writes `[]` (local JSONL datasets
     need no registry entry).
  2. the setup installed `torch` only; the model needs `torchvision`.
  3. the official `lora.sh` passes `--deepspeed zero1` but the setup did not install `deepspeed`.
  4. remote scripts `cd` into the training repo, so relative `dataset/` and `checkpoints/` paths
     pointed into the repo. They now use `$RUN_DIR`.
  5. `SshExecutor.launch` synced into a `remote_root` that did not exist yet; rsync does not
     create parent directories, so the first remote launch failed with exit 11. It now runs
     `mkdir -p <run_dir>` before rsync.
  6. with DeepSpeed, `dataloader_num_workers=4` fails to pickle the engine hook
     (`Can't pickle ... _module_forward_post_hook`) at the first batch; the generated script uses
     0 workers when DeepSpeed is active. Without flash-attn, packing/padding-free are also turned
     off explicitly.
- **Limit**: one step on a synthetic dataset proves the recipe and plumbing, not quality; the
  non-Monkey recipes still await a real run.

### 2026-10-18 — Vast provisioning (RTX 3090): recipe installs, GPU not mapped

- **What**: `rent_vast_instance(prepare_training=True)`; setup ran; a 1-step
  LoRA attempt followed.
- **Found and fixed**: ms-swift submodule init; missing `README.md` in
  `parsing/train/ms-swift`; torch overwritten by ms-swift dependencies;
  `swift --version` absent (readiness now checks `import swift` and
  `torch.cuda.is_available()`).
- **Remaining blocker**: the container had no `/dev/nvidia*`, so
  `torch.cuda.is_available()` was false despite `nvidia-smi` working. Another
  offer failed with a CDI error. Two cheap hosts out of two with a mis-mapped
  GPU.
- **Outcome**: ❌ provisioning works, the real run is not verifiable on those
  hosts.

### 2026-10-20 — every trainable recipe on a real GPU (1–3 steps per adapter)

- **Hosts**: Vast.ai **A100 PCIE 40 GB** ($0.42/h, driver 595.71.05, Ubuntu 24.04, 150 GB disk) for
  the five ms-swift models and their merges; Vast.ai **RTX 3090 24 GB** ($0.157/h) with the
  `paddlepaddle/paddle:3.3.0-gpu-cuda12.6-cudnn9.5` image for PaddleOCR-VL.
- **Data**: the immutable snapshot of project 1, reduced to one train + one val row per family
  (2–3 samples per model). Images were the real crops; no synthetic
  data.
- **What ran**: the script generated by `trainer_script.generate_script` for each adapter, LoRA,
  `max_length 8192` (4096 for the smaller models), one epoch, batch 1, `save_steps 1`. Nothing was
  hand-edited except the environment (`ssh_python`) and, for dots.ocr, the measured pins below.
- **Evidence**: the training logs are in `data/smoke/logs/` (gitignored) and the commands in the
  table; every adapter produced `checkpoint-N/adapter_model.safetensors` and a merged checkpoint
  through its own export command.

| Adapter | Toolchain | Environment actually used | Steps | Train loss | Peak | Merge |
|---|---|---|---|---|---|---|
| `qwen3-vl-8b` | ms-swift | transformers 5.20.2, torch 2.11+cu128 | 3 | 1.476 (eval 0.693) | 24.2 GiB | `swift export` 17 GB |
| `unlimited-ocr` | ms-swift 4.6.0 | transformers 4.46.3, torch 2.11 | 3 | 3.287 (eval 2.447) | 19.1 GiB | `swift export` 6.3 GB |
| `deepseek-ocr` | ms-swift 4.6.0 | transformers 4.46.3, torch 2.11 | 3 | 2.523 | 15.6 GiB | `swift export` 6.4 GB |
| `dots-ocr` | ms-swift 4.6.0 | transformers 4.57.1, flash-attn 2.8.3, torch 2.11 | 3 | 2.170 (eval 2.983) | 16.9 GiB | `swift export` 5.7 GB |
| `glm-ocr` | LLaMA-Factory 0.9.6 | transformers 5.8.0, torch 2.11 | 2 | 5.406 (eval 0.682) | — | `llamafactory-cli export` 2.1 GB |
| `paddleocr-vl` | PaddleFormers | paddlepaddle-gpu 3.3.0, triton 3.8 | 3 | 0.271 | — | `paddleformers-cli export` 1.8 GB |

MonkeyOCRv2 is not repeated: the 2026-10-18 run above already covered its bundled fork. A 1–3 step
run proves the recipe, the data view, the model's collator and the checkpoint/merge path; **it does
not measure quality**.

**Found and fixed as a consequence**

1. **A per-model environment is mandatory.** One ms-swift venv cannot train all five:
   - Unlimited-OCR and DeepSeek-OCR-2 import `is_torch_fx_available` and `LlamaFlashAttention2`
     from transformers 4.46, removed in 5.x; they need `ms-swift==4.6.0` + `transformers==4.46.3`.
   - Qwen3-VL needs transformers ≥ 5.
   - GLM-OCR's LLaMA-Factory path uses `warmup_ratio`, removed in transformers 5.20: pin 5.8.0.
   - dots.ocr declares 4.51 but its custom code breaks on ms-swift 4.6 with 4.51; 4.57.1 works.
   The pins now live in `TrainRecipe.environment` and are returned by `GET /api/system/train-recipes`.
2. **dots.ocr cannot use `--attn_impl flash_attention_2`.** transformers rejects it
   (`DotsVisionTransformer does not support Flash Attention 2 yet`), even though the vision tower
   imports flash-attn directly. The recipe now sets `eager`; flash-attn must still be installed.
3. **dots.ocr needs an image cap.** A full 11 MP page made the forward pass request 30 GiB and OOM
   on a 40 GB card; `max_pixels 1000000` (added to its defaults) makes the same page train in
   16.9 GiB. On the recipe's `max_length 16384` a register crop can still be large: the cap is what
   keeps it bounded.
4. **A short `max_length` crashes the DeepSeek-derived models** instead of truncating: with 4096
   the vision tower emitted 4850/6839 tokens and the loss raised
   `The expanded size of the tensor (4096) must match the existing size`. Their recipe default is
   8192; the smoke test used 8192 and passed. Do not shrink it as a memory trick.
5. **PaddleOCR-VL needs Triton.** `paddleformers` imports `paddleformers.triton_kernels` at module
   load and dies with `NameError: name 'triton' is not defined`. The official Paddle image plus
   `triton>=3.1` and `use-triton-in-paddle` (or the image's own Triton) is the working setup.
6. **A remote run had no pixels.** The JSONL kept the local absolute crop paths, which do not exist
   on the GPU host. `training_views` now materialises the referenced images under
   `dataset/images/` when `images_prefix` is set and writes the host-side path; `start_run` sets it
   for SSH/Vast/RunPod. Verified end-to-end: 59 images synced, the remote JSONL pointed at
   `/tmp/tabularium-runs/<run>/dataset/images/…`, and the generated script started on the A100
   (the run was then stopped because it re-downloaded the 17 GB model into the run's own cache).
7. **The model cache was inside the run.** Every run re-downloaded the weights, and a remote run
   lives in `/tmp`, so a restart lost them. The cache is now shared
   (`$HOME/.cache/modelscope` remote, `<data>/cache/modelscope` local), overridable with
   `model_cache`; `use_hf` selects HuggingFace instead of ModelScope on hosts outside China.

**Not covered**: full SFT (LoRA only), multi-GPU, resume across a real epoch, and quality. MinerU2.5
remains `planned` (its config ships only in a ZIP), TeleOCR/LightOnOCR-3 remain `inference-only`.

### 2026-10-20 — one-click environments (implementation)

- **What**: `scripts/cloud/setup_train_env.sh` with six profiles, `auto_training` (rent → wait SSH →
  install → start run), `POST/GET /training/auto`, a budget field in the Training page and
  automatic checkpoint preparation at the end of the run.
- **Budget**: the backend ranks offers by VRAM, then compute capability, reliability and price;
  the hourly ceiling is a hard filter, not a target.
- **Verified**: unit tests for the profile map, onstart embedding, offer ranking, job start and
  SSH retry; the UI card was checked in Chrome with a seeded job (phases, log, budget, cancel,
  390 px layout). A real auto-start with the copied test database selected an A100 40 GB within
  the budget and was cancelled immediately, confirming the rent path and the destroy-on-cancel.
- **Not verified live**: the provisioning script itself. Two Vast.ai instances stayed in
  `loading` for more than 7 minutes with no SSH and were destroyed; that is a provider/host
  problem, but the profile commands still need one successful real run per toolchain before
  calling the profiles verified. The auto job's UI and state machine are covered by mocks.

### 2026-10-20 — training dashboard

The Training page now opens with a dashboard above the form: KPI cards (GPU, estimated cost, loss, eval
loss, steps, peak VRAM), a loss area chart, GPU utilization/memory history (sampled over SSH for
remote runs), a graphical download bar and area chart fed by `prepare_job.progress`, a performance
section (evaluation metrics, token accuracy and run comparison). Raw training, provisioning and
checkpoint logs are opened on demand with dedicated buttons. The download progress is persisted in `run.json` every second by
`checkpoint_prepare._download_with_progress`; no more guessing from the rsync tail.

### 2026-10-20 — prebuilt images, end-to-end on a real GPU

- **Image**: `ghcr.io/nbadino/tabularium-train-monkey:monkey-v3`, built by GitHub Actions from
  `docker/train-monkey/Dockerfile`. Base PyTorch 2.6.0/CUDA 12.4 runtime, bundled ms-swift fork,
  transformers 4.57.1 and the **model weights** baked in; no pip and no model download at rental.
- **Live run**: an RTX A100/4080 host pulled the image, exposed `ready.json` (torch 2.6.0+cu124,
  transformers 4.57.1), trained 4 steps (`train_loss 1.6068`, 13.5 GiB, checkpoint-4, 61.9 MB),
  merged on the host and downloaded a 1.46 GB model, registered it and **destroyed the instance**.
- **Bugs found by the run**:
  1. The SSH launch command left the background process attached to the SSH stdout/stderr, so
     `start_run` hung forever; all fds are now redirected and the launch has a timeout.
  2. The automatic path did not wait for the container to be `running` and did not mark the job
     `provisioning`/`running`; fixed.
  3. ModelScope stalled at 16 MB on the first attempt; the auto path now uses HuggingFace
     (`use_hf`) and the image carries the weights, so it is not on the critical path.
- **GLM-OCR**: `tabularium-train-glm:glm-v1` booted, trained 4 steps (`train_loss 6.0107`,
  `eval_loss 3.7909`), merged on the host and downloaded a 2.1 GB model; registered and the
  instance was destroyed. The SSH launch timeout (the channel stays open after the PID is
  printed) was fixed and covered by a test.
- **Images published**: qwen, legacy, glm, paddle (environment-only; weights from HuggingFace).
- **Not yet**: dots.ocr still uses runtime provisioning; its flash-attn wheel does not load
  against the torch 2.6 base (undefined `c10::Error` symbol), so the image was not published.

### 2026-10-20 — post-training UX in a real browser (isolated data root)

- **What**: Chrome via Playwright on an isolated copy of the database, with two finished run
  records (one remote, one local) and real checkpoint folders. No GPU was involved.
- **Verified**: the finished panel shows `train_loss`, `eval_loss`, steps, duration and the
  **absolute local checkpoint path with its size**; the history selector lists both runs; selecting
  an old one loads its own metrics/log; *Resume from this run* fills `resume_run_id`; the remote plan
  says the merge will run on the GPU host; a local plan with a stub `swift` ran the background job,
  showed the phases, registered a custom model and linked to Models and Evaluation; GLM-OCR + Vast
  hides the rent button and explains that provisioning is Monkey-only; the environment pins are
  visible; no horizontal overflow at 390 px.
- **Found while doing it**:
  1. macOS ships `/usr/bin/swift` (the Apple compiler); `shutil.which("swift")` mistook it for
     ms-swift, so the "local merge" path tried to run `swift export` and failed with a shell error.
     The probe now accepts only Python console scripts for `swift`; the local plan correctly says
     ms-swift is missing.
  2. A failed preparation rendered the raw `ApiError: API POST …` string and left the button
     enabled. It is now a titled *Preparation failed* notice with the job's log and a retry.
  3. The finished panel never said where the weights were; it now shows the run-relative path,
     bytes and whether a merged model exists.
  4. The config form could be showing a new run while the status panel described another one; run
     history makes the selection explicit and the form says it configures a new run.
- **Not exercised**: the remote merge/download against a live GPU (the command builder, plan,
  download and registration are covered by unit tests with a fake executor). The local success path
  was exercised with a stub `swift`, not a real export.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `il fork ms-swift ufficiale manca` | submodule not initialised | `git submodule update --init --recursive` in the MonkeyOCRv2 checkout |
| `ms-swift non trovato` | `swift` not on PATH and no `TABULARIUM_TRAIN_PYTHON` | install the env or set the variable |
| `LLaMA-Factory: imposta TABULARIUM_LLAMAFACTORY_DIR` | GLM-OCR needs its own checkout | set the variable to the LLaMA-Factory clone |
| `PaddleFormers: comando paddleformers-cli non trovato` | wrong env | install PaddleFormers in the env you point at |
| `la ricetta mineru-sft … non è ancora eseguibile` | official bundle missing | follow the MinerU lesson and use its `run_mineru.py`; Tabularium will not generate a surrogate |
| `stima VRAM non disponibile` | model geometry unknown | only free memory is checked; set `model_path` so the estimate can read `config.json` |
| OOM on a small card | logits dominate at long sequences | lower `max_length`, batch 1, raise `grad_accum`; the preflight suggests the largest fitting length for known geometries |
| `JSONDecodeError` in `register_dataset_info` when `swift sft` starts | the MonkeyOCRv2 fork does not ship `swift/llm/dataset/data/dataset_info.json` | re-run `scripts/cloud/setup_cloud_train.sh`: it creates the empty registry; local JSONL datasets do not need any entry |
| `ImportError: cannot import name 'is_torch_fx_available'` or `LlamaFlashAttention2` | Unlimited-OCR / DeepSeek-OCR-2 remote code predates transformers 5 | install the recipe's `environment` (`ms-swift==4.6.0`, `transformers==4.46.3`) |
| `ValueError: Some keys are not used by the HfArgumentParser: ['warmup_ratio']` | transformers newer than LLaMA-Factory supports | pin `transformers==5.8.0` for GLM-OCR |
| `NameError: name 'triton' is not defined` in PaddleOCR-VL modeling | PaddleFormers imports Triton kernels unconditionally | install `triton>=3.1` and `use-triton-in-paddle`, or use the official Paddle image |
| `expanded size ... must match the existing size` at the second step | `max_length` truncated the image tokens, the vision tower emitted more | use the recipe's `max_length` (8192 for Unlimited-OCR and DeepSeek-OCR-2) instead of shrinking it |
| The model is downloaded again on every run | old generated scripts put the cache inside the run | fixed: the cache is shared (`$HOME/.cache/modelscope` remote, `<data>/cache/modelscope` local); override with `model_cache` |
| The checkpoint cannot be prepared | no remote run left and the local toolchain is missing | the panel says which command/environment is missing; install it or keep the remote host until preparation ends |
| “run remota non è più raggiungibile” when preparing | the remote run directory was cleaned or the host is gone | do not clean before preparation, or install the toolchain locally and retry |

Locally, images stay at their absolute crop paths in every view, including the LLaMA-Factory
`dataset_info.json`: LLaMA-Factory accepts absolute paths and resolves relative ones against its
`dataset_dir`. For a **remote** run those paths do not exist on the GPU host, so the view copies the
referenced pixels into `dataset/images/` (hardlink when possible) and writes the path under which the
host will see them; the executor syncs them with the run. This is the only duplication, and only for
the pixels a run actually reads.

### 22 October 2026 — interrupted-run recovery

See [the researcher workflow trial](../VAST_WORKFLOW_TRIAL_20261022.md) for browser evidence, training metrics and the distinction between a saved LoRA checkpoint and a complete merged model. The automatic monitor reconciles provider state on restart; replay falls back to persisted logs if the metrics file is empty. The Monkey legacy launcher normalizes the vLLM 0.11.x version tuple before serving.

### Automatic held-out comparison before GPU release

For automatic MonkeyOCRv2 runs, checkpoint preparation now runs the base and merged models
sequentially on the training GPU before downloading/registering the model and releasing the
instance. It uses the frozen validation images and official prompts, records both predictions
and metric deltas, and shows sample progress with logs on demand. Inference uses a separate
environment on the same host. Training completion is distinct from evaluation success.
See [the recovery trial and comparison protocol](../TRAINING_COMPARISON_20261023.md).
