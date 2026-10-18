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
| `qwen3-vl-8b` | `Qwen/Qwen3-VL-8B-Instruct` | ms-swift | LoRA, full | `ms-swift-jsonl` | experimental |
| `unlimited-ocr` | `baidu/Unlimited-OCR` | ms-swift | LoRA, full | `ms-swift-jsonl` | experimental |
| `deepseek-ocr` | `deepseek-ai/DeepSeek-OCR-2` | ms-swift | LoRA, full | `ms-swift-jsonl` | experimental |
| `dots-ocr` | `dots-studio/dots.mocr` | ms-swift | LoRA, full | `ms-swift-jsonl` | experimental |
| `glm-ocr` | `zai-org/GLM-OCR` | LLaMA-Factory | LoRA, full | `llamafactory-sharegpt` | experimental |
| `paddleocr-vl` | `PaddlePaddle/PaddleOCR-VL-1.6` | PaddleFormers | LoRA, full | `paddleformers-messages` | experimental |
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
  full-page parser. Those recipes are `experimental` for this reason; the fix is an end-to-end
  dataset family (full page + native HTML/Markdown target), not a silent format conversion.
- **GLM-OCR's table target is unspecified by the vendor.** The official README defines only the
  three prompt names; Tabularium teaches OTSL because it is the dataset's canonical table format.
- **LoRA merge runs where Tabularium runs.** A checkpoint trained on a remote GPU is downloaded,
  but `llamafactory-cli` / `paddleformers-cli` / `swift` must exist on the dashboard machine too.
- **No GPU run has verified a non-Monkey recipe yet.** The matrix says `experimental`; the test log
  below is the place where that changes.

## Environment

Each toolchain has its own environment. A model is never trained with another
model's environment.

| Toolchain | Required | Environment variable |
|---|---|---|
| ms-swift, MonkeyOCRv2 | checkout with the bundled fork | `TABULARIUM_TRAIN_REPO=/path/to/MonkeyOCRv2` |
| ms-swift, other models | `swift` in the training env | `TABULARIUM_TRAIN_PYTHON`, or `swift` on `PATH` |
| LLaMA-Factory | checkout + `llamafactory-cli` | `TABULARIUM_LLAMAFACTORY_DIR` |
| PaddleFormers | checkout + `paddleformers-cli` | `TABULARIUM_PADDLEFORMERS_DIR` |

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
3. **Fine-tuning → Training.** Choose the model: selecting it applies its
   recipe's defaults and train types. The preflight tells you what is missing
   (toolchain path, GPU, VRAM, dataset families, an unavailable recipe).
4. **Run.** Logs stream via SSE; loss and GPU usage are charted. Stop and resume
   from the checkpoint.
5. **Register the checkpoint.** After a finished run, the checkpoint is
   registered as a servable model. LoRA is merged with the toolchain's own
   command: `swift export` (ms-swift), `llamafactory-cli export`
   (LLaMA-Factory), `paddleformers-cli export` (PaddleFormers).

Presets are GPU budgets, not recipes: they change epochs, batch, accumulation,
LoRA rank and length, and never the learning rate, which stays the one from the
official recipe.

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
| LoRA merge fails after a remote run | the merge runs where Tabularium runs, not on the GPU host | install LLaMA-Factory/PaddleFormers there too, or merge on the GPU host and register the resulting path |

Images stay at their absolute crop paths in every view, including the LLaMA-Factory
`dataset_info.json`: LLaMA-Factory accepts absolute paths and resolves relative ones against its
`dataset_dir`. Copying the crops per run would duplicate gigabytes without changing a byte.
