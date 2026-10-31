# 10. Training center

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

- Each trainable model has a **training block** in its spec (`app/models/<id>.py`), assembled
  into a recipe by `services/train_recipes.py`: the toolchain the model's
  own vendor documentation uses, the official documentation URL, allowed types (`lora`/`full`), the
  data view, the official hyperparameters/flags and the maturity. Closed models (Gemini 4 Argon)
  have no weights and no recipe; `inference-only` models (TeleOCR, LightOnOCR-3, Surya OCR 2.5, Xiaomi-OCR-0) declare why they
  cannot be trained instead of disappearing.
- **The toolchain is per model, not one global choice**:
  - `ms-swift` — MonkeyOCRv2 (the **bundled fork** in `parsing/train/ms-swift`, where the flag is
    `--train_type`; upstream uses `--tuner_type`), Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2,
    dots.ocr. The other four use upstream ms-swift; MonkeyOCRv2 must use its fork, whose
    `<|image_pad|>` template is not upstream's `<|IMAGE|>` encoder template.
  - `llama-factory` — GLM-OCR, as `examples/finetune/README.md` documents; ShareGPT view + the
    official prompts `Text Recognition:` / `Table Recognition:` / `Formula Recognition:`.
  - `paddleformers` — PaddleOCR-VL, `paddleformers-cli train <config> key=value`, full **and**
    LoRA; the `messages` dataset is the same shape as ms-swift.
  - `mineru-sft` — MinerU2.5: its lesson explicitly forbids `swift sft` and ships the config only
    inside `mineru_sft_code.zip`; the recipe is `planned` and blocks with the documentation link.
- `services/training_views.py` materialises the run's data view from the immutable dataset
  snapshot. Views reshape containers/prompts, never the target: `ms-swift-jsonl`,
  `paddleformers-messages`, `llamafactory-sharegpt`. Each run writes `dataset/view.json` (families,
  prompts, counts, empty families) and embeds it in `run.json`; the view also realigns the
  prompts to the model being trained, whatever adapter built the snapshot. Recognition-only models
  (GLM-OCR, PaddleOCR-VL) do not get the layout family: their protocol has no bbox.
- **The environment is per model, not per toolchain.** The recipe declares the pins measured on
  a real run (`environment`): Qwen3-VL needs transformers ≥ 5; Unlimited-OCR and DeepSeek-OCR-2
  only import on 4.46.3 (their remote code still calls `is_torch_fx_available` and
  `LlamaFlashAttention2`) with ms-swift 4.6.0; GLM-OCR needs transformers ≤ 5.8 (`warmup_ratio`);
  dots.ocr needs flash-attn; PaddleOCR-VL needs Triton. One ms-swift venv cannot train all five,
  and the preflight must not pretend otherwise.
- `services/trainer_script.py` generates the script from the recipe and **fails explicitly** on
  anything it cannot run — never a wrong script silently. The VRAM estimate uses the recipe's own
  geometry (`TrainRecipe.architecture`); an unknown geometry is not blocked on a guessed number.
  The model cache is **shared and persistent** (`$HOME/.cache/modelscope` remote,
  `<data>/cache/modelscope` local, override with `model_cache`), never a directory inside the run:
  a checkpoint is immutable and a remote run under `/tmp` loses it. `use_hf` selects HuggingFace
  instead of ModelScope when the host is outside China.
- **A remote run carries its pixels.** For ms-swift/Paddle the local views keep the absolute crop
  paths; with `images_prefix` (set by `start_run` for SSH/Vast/RunPod) they materialise the
  referenced images under `dataset/images/` (hardlink when possible) and write the path the GPU
  host will see. **GLM/LLaMA-Factory is different**: its docs require image paths relative to the
  `dataset_dir` (`images/<file>`), so the GLM view always materialises the pixels in the run, for
  local and remote runs alike.
- **Page approval includes table content.** Explicit `confirm_drafts` accepts both block drafts
  and every table cell, records `verification_source=page_approval`, advances the annotation
  revision and notifies connected editors. Strict readiness rejects unverified table text cells;
  assigned pages can be submitted to a reviewer with cell warnings, and explicit reviewer approval
  verifies those cells. Existing approved data are reconciled only on a user-authorized repair
  with a backup and audit trail, never through a silent startup migration. Dataset reports list
  `table_exclusions`; training preflight and automatic rental reject approved-only snapshots
  that excluded tables. Missing optional band boundaries are informational when the full table
  was exported successfully.
- **Approved-only builds never train on a prefill.** A table containing text cells with
  `verified=false` is skipped (and reported) when `approved_only` is on; with an exploratory
  `approved_only=false` build it is included but the report counts the unverified cells. The UI
  defaults to approved-only.
- Execution in a dedicated environment on the GPU machine — this machine, SSH, Vast.ai or RunPod
  (`services/training_executor.py`; `scripts/cloud/setup_cloud_train.sh` prepares a remote host and
  writes a `.train_ready` marker). The preflight checks **the recipe's** toolchain, not always
  MonkeyOCRv2: `TABULARIUM_TRAIN_REPO` for MonkeyOCRv2, `TABULARIUM_LLAMAFACTORY_DIR` for GLM-OCR,
  `TABULARIUM_PADDLEFORMERS_DIR` for PaddleOCR-VL. The dashboard launches, **streams logs via
  SSE**, parses loss and learning rate, samples GPU telemetry (`nvidia-smi`), stops and resumes.
- Run state, scripts and logs in `<root>/projects/<project>/runs/<run_id>/` (the reproducibility rule, `AGENTS.md` R13).
- **Post-training is a job, not a button that blocks.** A finished run shows the final
  train/eval metrics from the log, the local checkpoint path and its size. Preparing a LoRA
  checkpoint (`POST /training/prepare-checkpoint`) runs `swift export` / `llamafactory-cli export`
  / `paddleformers-cli export` **on the GPU host** for a remote run, downloads the merged model
  into the run, then registers it with `custom_models.register_training_checkpoint`; a local run
  merges locally. The job has phases (merge → download → register), a log (`prepare.log`) and
  state in `run.json`, so the HTTP request never waits for the merge. It starts automatically when
  the run finishes (`auto_prepare`, on by default and disableable in Advanced). If the remote host
  is gone, the plan says the local fallback is required and lists the missing command.
- **Evaluate before transferring or releasing the training GPU.** Automatic MonkeyOCRv2 runs
  merge on the rented host, serve the base and merged checkpoint sequentially on that same GPU,
  and compare the exact frozen validation images/prompts. Only then download/register the model
  and release the instance. Serving has a separate environment on the same host; it must not
  change the training environment or the official checkout. Save paired predictions, input hashes,
  CER/WER/layout deltas and complete startup diagnostics. Missing verified table samples means
  no table-content quality claim. A failed comparison must remain visibly failed even when the
  checkpoint itself was saved successfully. `scripts/compare_training.py` recovers an already
  saved run whose original GPU is gone; it is not the normal post-training path.
- **Run history and selection.** `GET /training/runs` lists every run of the project;
  `GET /training/status?run_id=` reads an old one without pretending it is active. The UI selects a
  run to see its metrics/log/checkpoint and offers *Resume from this run* (fills `resume_run_id`).
  The config form always configures a new run and says so.
- **One-click training is the non-developer path.** `POST /training/auto` takes the current
  config plus an hourly budget, rents the best Vast.ai offer within that ceiling (VRAM, then compute
  capability, reliability and price), boots the profile's versioned GHCR image (MonkeyOCRv2,
  Qwen, legacy, GLM, Paddle; dots.ocr still provisions with `scripts/cloud/setup_train_env.sh`),
  waits for `/opt/tabularium/ready.json`, then calls `start_run`. The job is persisted under
  `<data>/auto-training/`, shows phases/log in the UI and destroys the instance if rental,
  provisioning or training fails, and after registration. Profiles: `monkey`, `qwen`, `legacy` (Unlimited/DeepSeek), `dots`,
  `glm`, `paddle`. The manual rent endpoint still prepares only MonkeyOCRv2 and says so; SSH and
  the pins remain under Advanced, not in the main flow.
- Official hyperparameters, per-model notes and the real-run log: `docs/guide/training.md`; the
  verbatim vendor commands: `docs/training-official-docs.md`.

---
