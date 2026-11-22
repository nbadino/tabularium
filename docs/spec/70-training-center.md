# 10. Training center

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

- Each trainable model has a **training block** in its spec (`app/models/<id>.py`), assembled
  into a recipe by `services/train_recipes.py`: the toolchain the model's
  own vendor documentation uses, the official documentation URL, allowed types (`lora`/`full`), the
  data view, the official hyperparameters/flags and the maturity. Closed models (Gemini 4 Argon)
  have no weights and no recipe; `inference-only` models (TeleOCR, LightOnOCR-3, Surya OCR 2.5) declare why they
  cannot be trained instead of disappearing. TeleOCR's reason is also architectural: a Qwen2.5-VL
  vision tower with a `Qwen3ForCausalLM` decoder, which ms-swift's `qwen2_5_vl` (stock
  transformers class) cannot load.
- **The toolchain is per model, not one global choice**:
  - `ms-swift` — MonkeyOCRv2 (the **bundled fork** in `parsing/train/ms-swift`, where the flag is
    `--train_type`; upstream uses `--tuner_type`), Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2,
    dots.ocr, Unlimited-OCR-2 and Xiaomi-OCR-0 (`qwen3_5`, experimental: ms-swift's Qwen3.5 Best
    Practice, since the vendor publishes no training code). The others use upstream ms-swift;
    MonkeyOCRv2 must use its fork, whose `<|image_pad|>` template is not upstream's `<|IMAGE|>`
    encoder template. Xiaomi-OCR-0 trains recognition only, on the vendor's task prompts, with
    `IMAGE_MIN_TOKEN_NUM=64`/`IMAGE_MAX_TOKEN_NUM=4096` matching its serving bounds and no packing.
  - `llama-factory` — GLM-OCR, as `examples/finetune/README.md` documents; ShareGPT view + the
    official prompts `Text Recognition:` / `Table Recognition:` / `Formula Recognition:`.
  - `paddleformers` — PaddleOCR-VL, `paddleformers-cli train <config> key=value`, full **and**
    LoRA; the `messages` dataset is the same shape as ms-swift.
  - `paddlex` — the **PP-DocLayoutV3 layout detector** (`models/pp_doclayoutv3.py`, recipe id
    `pp-doclayoutv3`, registered next to the adapters' recipes since it reads no text). View
    `paddlex-layout` (§7); the script runs `check_dataset`, evaluates the official weights on the
    val split (`eval_base.json`), trains, evaluates `best_model` (`eval_trained.json`), with
    `Train.num_classes=25`, `best_model` chosen on bbox AP, a snapshot every 5 epochs, and `Global.device` GPU when paddle has CUDA, else CPU (without a GPU the
    preflight warns, and refuses on Apple Silicon, where training diverged at the first step). `TABULARIUM_PADDLEX_DIR` is a PaddleX `release/3.7`
    checkout with `paddlex --install PaddleDetection` (its `.venv/bin` goes on `PATH` when present);
    one-click uses `ghcr.io/nbadino/tabularium-train-layout:layout-v1` (`docker/train-layout`).
    The exported `best_model/inference` needs no preparation: `checkpoint_prepare` refuses it,
    the one-click job releases the GPU once it is downloaded, and Xiaomi-OCR-0's `layout_model`
    setting (`official` or `<project_id>/<run_id>`, listed by `GET /api/system/layout-checkpoints`)
    loads it with `create_model(model_dir=...)`; a choice that no longer resolves is an error,
    never the official weights in disguise. Resume is not supported yet.
  - `mineru-sft` — MinerU2.5: its lesson explicitly forbids `swift sft` and ships the config only
    inside `mineru_sft_code.zip`, which no public source carries (checked 2026-11-16; the
    unmerged MetaX lesson's plain-`swift sft` stub is not adopted); the recipe is `planned`
    and blocks with the documentation link.
- `services/training_views.py` materialises the run's data view from the immutable dataset
  snapshot. Views reshape containers/prompts, never the target: `ms-swift-jsonl`,
  `paddleformers-messages`, `llamafactory-sharegpt`. Each run writes `dataset/view.json` (families,
  prompts, counts, empty families) and embeds it in `run.json`, together with the snapshot's
  `collection_scope` (which pages the model learns from, see §7); the view also realigns the
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
- **Bringing a remote run home.** When the remote command exits, `RemoteProcess.download_artifacts`
  rsyncs the checkpoints and then reads the remote `artifacts.sha256` over SSH to verify them. Both
  steps are retried on transport failures (timeout, `OSError`, ssh exit 255) with backoff
  (`POST_TRAINING_ATTEMPTS`, `POST_TRAINING_BACKOFF_S`); the manifest read has its own timeout
  (`MANIFEST_TIMEOUT_S`, 60 s). `run.json` records `post_training`: the training exit code, whether
  the download completed, and the manifest outcome (`verified`, `mismatch`, `missing`, `empty`,
  `unreachable`). A mismatch, a missing manifest or a failed download fails the run. A manifest that
  stays `unreachable` after a completed download does **not**: the run is `finished` with
  `artifact_warning`, since rsync `--checksum` already verified the copy.
- **Recovering a run failed only by bookkeeping.** `POST /training/recover {run_id, reason?}` marks a
  `failed` run `finished` when the training exited 0, the checkpoints were fully downloaded and
  still match the local `artifacts.sha256`, and only a later step failed. Runs written before
  `post_training` existed qualify when the log reached its last step and the error is the remote
  manifest read (that read only happens after a completed rsync). The previous state, error and
  exit code are kept under `recovery`; nothing is deleted. Auto-prepare then runs as at the end of
  a training, so prepare-checkpoint (a local merge when the instance is gone) and registration work
  as for any finished run. Failed runs carry `recoverable` in status and history (cheap check, no
  hashing); the route always re-verifies.
- **Evaluate before transferring or releasing the training GPU.** Automatic MonkeyOCRv2 runs
  merge on the rented host, serve the base and merged checkpoint sequentially on that same GPU,
  and compare the exact frozen validation images/prompts. Only then download/register the model
  and release the instance. Serving has a separate environment on the same host; it must not
  change the training environment or the official checkout. Save paired predictions, input hashes,
  CER/WER/layout deltas and complete startup diagnostics. Missing verified table samples means
  no table-content quality claim. A failed comparison must remain visibly failed even when the
  checkpoint itself was saved successfully. `scripts/compare_training.py` recovers an already
  saved run whose original GPU is gone; it is not the normal post-training path.
- **Gold-set evaluation on the training GPU** (`services/gold_eval.py`). A one-click run whose
  recipe declares `gold_eval` (today Xiaomi-OCR-0) on a project with approved gold pages
  (`study_protocol.gold_pages`, never in a snapshot) reads the gold Table blocks with the merged
  checkpoint after the remote merge and before the download, so no second rental is needed. The job
  rents the recipe's evaluation image instead of the training image
  (`ghcr.io/nbadino/tabularium-eval-xiaomi:v1`: the `qwen-v1` layers unchanged plus
  `/root/eval-env`, torch 2.14.0+cu130 and transformers 5.17.0, so the offer search asks for CUDA
  ≥ 13.0) and records the decision in `job.gold_eval` (`planned`, or `skipped`/`not_applicable`
  with `disabled`, `not_declared`, `no_gold_tables`). Inputs: crops cut by `pages.crop_block_jpeg`
  from the block bbox (the training crop, SHA-256 recorded), tables with an unverified cell left
  out; the recipe's table prompt and image-size bounds; greedy decoding, `max_new_tokens` 12288,
  1500 s per crop; `scripts/cloud/gold_infer.py` runs with the declared interpreter. Hitting the
  token limit, an unparsable answer, an exception or an image hash mismatch scores zero on every
  reference cell; nothing is repaired. Scoring is `evaluate.table_metrics` (rows aligned by
  Needleman-Wunsch, then exact cells, CER, missing/extra rows, header structure, TEDS), micro
  over cells. The remote stack must match the declared one or the evaluation fails. A time cap
  (`gold_eval_max_minutes`, 60 by default, 5–240) stops starting new crops; unread crops are
  `not_run` and the result is `complete: false`. `run.json` keeps `gold_eval` (state, aggregate,
  per-table metrics, environment with image digest and measured versions, protocol), the full
  outputs go to `gold_eval.json`, `GET /training/runs` carries the aggregate. Training runs have no
  database table: `run.json` is their record. Any evaluation error is stored in `gold_eval.error`
  and preparation goes on: the model is downloaded and registered.
- **Run history and selection.** `GET /training/runs` lists every run of the project;
  `GET /training/status?run_id=` reads an old one without pretending it is active. The UI selects a
  run to see its metrics/log/checkpoint and offers *Resume from this run* (fills `resume_run_id`).
  The config form always configures a new run and says so.
- **One-click training is the non-developer path.** `POST /training/auto` takes the current
  config plus an hourly budget, rents the best Vast.ai offer within that ceiling (VRAM, then compute
  capability, reliability and price; with `datacenter_only` only Vast datacenter hosts, filter
  `datacenter` plus a local `hosting_type == 1` check, and an explicit error when none fits; with `verified_only` the `verified` filter), skips for 24 h the `machine_id` of every host that failed or was cancelled before training (`failed_hosts`, `offer.machine_id`), and replaces, up to `MAX_HOST_ATTEMPTS` = 3 hosts, an instance whose `status_msg` contains "Error" or that stays `created`/`offline` for `VAST_STUCK_LIMIT_S` (10 min) (rule of vast-cli `wait_for_instance`) or still pulls its image (`loading`) after `VAST_LOADING_LIMIT_S` (20 min), boots the profile's versioned GHCR image (MonkeyOCRv2,
  Qwen, legacy, GLM, Paddle; dots.ocr still provisions with `scripts/cloud/setup_train_env.sh`),
  waits for `/opt/tabularium/ready.json`, then calls `start_run`. The job is persisted under
  `<data>/auto-training/`, shows phases/log in the UI and destroys the instance if rental,
  provisioning or training fails, and after registration. Profiles: `monkey`, `qwen`, `legacy` (Unlimited/DeepSeek), `dots`,
  `glm`, `paddle`. The manual rent endpoint still prepares only MonkeyOCRv2 and says so; SSH and
  the pins remain under Advanced, not in the main flow.
  An optional `gpu_name` pins the search to one Vast GPU model (`"A100 SXM4"`).
- **A stale snapshot is said, and refused for rental.** The preflight compares the snapshot's
  `built_at` with today's pages, replaying the snapshot's own selection (approved only, pilot,
  collection scope, gold excluded): a block or grid save, a review step (`page_work`,
  `page_reviews`) or a page audit event (direct approval) after the build, or a different number
  of eligible pages than the snapshot counted, sets `dataset.stale` (`snapshot_pages`,
  `eligible_pages`, `changed_pages`, `latest_change`) and adds a warning. `POST /training/auto`
  refuses such a snapshot unless `allow_stale_dataset: true`; the UI asks first and the job
  records `dataset_snapshot` and `dataset_stale`. A deleted block leaves no page timestamp and is
  caught only through the page count or a later edit.
- **Which Vast hosts an image can run on.** Every image is linux/amd64, so every Vast search asks
  for `cpu_arch = amd64` and drops any arm64 offer it still gets (an absent `cpu_arch` with a Grace
  name, GB10/GH200/GB200, counts as arm64); renting one is refused with a localised reason.
  NVIDIA CMP mining cards (`gpu_name` "CMP <n>HX": NVIDIA sells them as a "Dedicated GPU for
  Professional Mining", https://www.nvidia.com/en-us/cmp/; Vast lists the GA100 "CMP 170HX" with
  sm_8.0 and 40–64 GB) are dropped by the same local filter and refused by
  `cloud_manager.offer_unfit_reason`, for training and serving alike. An
  unknown or zero `gpu_ram` never meets a VRAM minimum. `auto_training.PREBUILT_CUDA` records the
  toolkit of each image; below CUDA 12.8 (`cloud_manager.BLACKWELL_FIRST_TOOLKIT`: torch 2.6+cu124
  and paddle cuda12.6) the auto search excludes Blackwell (`compute_cap` ≥ 1000).
- Official hyperparameters, per-model notes and the real-run log: `docs/guide/training.md`; the
  verbatim vendor commands: `docs/training-official-docs.md`.

---
