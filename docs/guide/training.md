# Fine-tuning

This page is the **register** of Tabularium's multi-model training: the recipe
matrix, a **repeatable** procedure to verify recipes on a GPU, and the **log of
tests** actually run. The design source of truth remains
[AGENTS.md](https://github.com/nbadino/tabularium/blob/main/AGENTS.md) §M18;
this page covers operations.

## Principles

1. **One recipe per adapter** (`services/train_recipes.py`): toolchain, official
   documentation, allowed training types, data view, maturity. The script
   generator (`trainer_script.py`) dispatches from the recipe and **fails
   explicitly** on anything it cannot run. It never produces a wrong script
   silently.
2. **Honest maturity**: `verified` only after a real run documented below;
   otherwise `experimental`. `planned` means no runnable recipe yet.
3. **One toolchain** (ms-swift) for almost every OCR model: the official
   ms-swift *Supported-models-and-datasets* table provides
   `Model Type | Default Template`.

## Recipe matrix

`--model_type` / `--template` values come from the official ms-swift table
(`main`).

| Adapter | Toolchain | `--model_type` | `--template` | Maturity |
|---|---|---|---|---|
| monkeyocrv2-parsing | ms-swift | `monkeyocrv2` | `monkeyocrv2` | verified |
| qwen3-vl-8b | ms-swift | `qwen3_vl` | `qwen3_vl` | experimental |
| unlimited-ocr | ms-swift | `unlimited_ocr` | `unlimited_ocr` | experimental |
| deepseek-ocr | ms-swift | `deepseek_ocr2` | `deepseek_ocr2` | experimental |
| glm-ocr | ms-swift | `glm_ocr` | `glm_ocr` | experimental |
| mineru2.5 | ms-swift | `qwen2_vl` | `qwen2_vl` | experimental |
| dots-ocr | ms-swift | `dots_ocr` | `dots_ocr` | experimental |
| paddleocr-vl | erniekit+paddlex | — | — | experimental (outside the ms-swift generator) |
| teleocr, lightonocr-3 | — | — | — | planned (not in ms-swift) |
| gemini-argon | — | — | — | not trainable (closed model) |

Endpoint: `GET /api/system/train-recipes` (`items`, `trainable`).

## Repeatable procedure (GPU host)

Prepare a fresh GPU host (Vast.ai or any CUDA box) with
`scripts/cloud/setup_cloud_train.sh`:

```bash
TABULARIUM_TRAIN_ROOT=$HOME/tabularium-train bash scripts/cloud/setup_cloud_train.sh
```

Then, from Tabularium, export the dataset and launch the generated script:

```bash
# 1) dataset (from the Dataset page, or via the API)
curl -X POST /api/projects/<id>/datasets/build -d '{"split_ratio":0.9}'
# 2) training script for the chosen adapter
python -c "from app.services import trainer_script; print(trainer_script.generate_script(cfg, ...))"
# 3) launch (Fine-tuning wizard → "This machine" / SSH / Vast)
```

Minimal smoke test (checks that the **recipe runs**, not quality): a synthetic
dataset of 2 samples, `--max_steps 1`, `--save_steps 1`. See the test log.

## Test log

Each entry: date, machine, adapter, command, outcome. Update it on every real
run; a recipe becomes `verified` **only** here.

### 2026-10-18: generator and dispatch (no GPU)

- **What**: `trainer_script.generate_script` produces the right script for
  `monkeyocrv2-parsing`, `qwen3-vl-8b` and `unlimited-ocr`; `paddleocr-vl`
  (erniekit) and `gemini-argon` (closed) fail explicitly.
- **Outcome**: ✅ `tests/test_train_recipes.py`.
- **Limit**: does not prove the script *runs* on a GPU.

Next test: a real smoke test on a GPU (Vast.ai RTX 3090).

### 2026-10-18: Vast provisioning — recipe installs, GPU not mapped

- **What**: `rent_vast_instance(prepare_training=True, tabularium_ref=main)` on
  an RTX 3090; the onstart runs `setup_cloud_train.sh`; then verification and a
  1-step LoRA attempt over SSH.
- **Found and fixed in the script**:
  1. the `ms-swift` submodule must be initialised
     (`git submodule update --init --recursive`), or `pip install -e ./ms-swift`
     fails;
  2. `parsing/train/ms-swift/setup.py` opens `README.md`, which the repo does
     **not** include (404): the script now creates it when missing;
  3. `ms-swift`'s dependency overwrote `torch` (cu130) — fine on driver 13.1;
  4. `swift --version` **does not exist** in this version (KeyError): the
     readiness check now uses `python -c "import swift"` **and**
     `torch.cuda.is_available()`.
- **Remaining blocker (Vast host)**: the container has **no `/dev/nvidia*`**, so
  `torch.cuda.is_available()` is `False` (`CUDA unknown error`) even though
  `nvidia-smi` works. Such a container cannot train; not fixable from inside.
  Another offer (id 38849861) failed to start with a CDI error
  (`failed to inject CDI devices gpu=1`). Two cheap hosts out of two with a
  mis-mapped GPU.
- **Outcome**: ❌ the **recipe provisioning now works** (ms-swift installed and
  importable), but the **real run is not verifiable** on these hosts. Credit
  spent ~$0.21; instances destroyed.
- **Next**: retry on a **verified** Vast host (or another GPU); a host exposing
  `/dev/nvidia*` with `torch.cuda.is_available() == True` is the minimum
  condition for training to start.

