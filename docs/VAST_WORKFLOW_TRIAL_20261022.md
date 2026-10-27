# Vast.ai researcher workflow trial — 22 October 2026

## Scope and authorization

Real fine-tuning through the browser, with an authorized ceiling of EUR 1/hour.
The submitted ceiling was USD 0.90/hour. The active project was **Tabularium Smoke**
(project 1); its annotations were not changed for this trial.

## Dataset and configuration

The dataset was rebuilt through the UI: 11 approved pages, split by page into nine
training pages and two validation pages (seed 42). It contains 11 layout and 37 text
samples. No verified table samples were available; excluded unverified cells remained
excluded. This trial validates execution and UX, not table recognition quality.

- Model: `zenosai/MonkeyOCRv2-B-Parsing`, official bundled ms-swift recipe.
- LoRA: rank 8, alpha 32; frozen ViT; one epoch; learning rate 1e-5.
- Measured 8 GB configuration: batch 1, accumulation 4, sequence length 8192.
- Image cap: official 1003520 pixels; validation every five steps.
- Automatic checkpoint preparation and remote evaluation enabled by the automatic executor.
- Job: `auto_1792676312722`, created at 13:38:32 UTC.
- Instance: `57218638`, RTX PRO 6000 Max-Q, quoted USD 0.72592593/hour.
- Image: `ghcr.io/nbadino/tabularium-train-monkey:monkey-v4`.

## Observations and fixes

1. Before the new run id existed, the monitor showed loss, charts and a checkpoint
   notice belonging to the previous run. The provisioning job now isolates its display
   from previous run data.
2. Reloading `/training?view=new` restored polling but left the configuration visible.
   An active restored job now opens its monitor automatically.
3. Empty metrics and performance sections appeared while the container was starting.
   They now appear when their relevant run/data exist.
4. Provisioning messages exposed untranslated backend diagnostics. The primary monitor
   uses localized phase names; exact messages remain behind **Show logs**.
5. The backend uses `training` while the progress timeline uses `train`; the monitor now
   normalizes that phase. Terminal automatic jobs also stop their elapsed timer.
6. The provider spent several minutes downloading the prebuilt image. This is a real
   user wait: SSH is not available yet. Provider state confirmed `loading`, with image
   checksum/download progress, rather than a dataset or browser failure.

7. The hourly budget reset to the default on reload. The explicitly entered ceiling
   is now retained in browser storage.

8. The Monkey v4 image uses CUDA 12.8, while the automatic offer filter still
   defaulted to 12.4. The minimum is now 12.8 for this image. The rented host
   already met it. Active-job duplicate detection now also includes running jobs.

## Evidence

- `screenshots/vast-workflow-config-20261022.png`
- `screenshots/vast-workflow-stale-metrics-20261022.png`
- `screenshots/vast-workflow-clean-monitor-20261022.png`

## Outcome

## Completed training and recovery

The later run `run_1792680203611` completed on CMP 170HX instance `57226361`
(USD 0.47703704/hour): 10/10 steps, train loss 3.31678948, validation loss
1.6496222, token accuracy 0.52017937, peak VRAM 14.67 GiB. The training runtime
reported by the trainer was 18.4025 seconds. Its persisted configuration used
batch 4, accumulation 1 and maximum sequence length 16384 (official preset);
the earlier 8 GB configuration described above belongs to the first attempt. These are execution metrics, not
an OCR quality benchmark.

The LoRA `checkpoint-10` and optimizer state were downloaded. Remote merge
succeeded, but automatic evaluation failed because the pinned official
`serve.py` compares the installed `(0, 11, 0)` tuple against `(0, 11)`. The
legacy-family check now uses `installed[:2]`; serving still uses the official
launcher/model registration. The Docker source carries the same correction
for future builds; existing images are corrected before automatic evaluation.

The merged-model download stopped at 10.3%. At resumption the provider API and
browser console both confirmed no instances. Its exact destruction time and
final bill cannot be inferred from the interrupted local monitor. The UI must
show an unknown estimate in this recovery case, not bill the intervening hours.
The LoRA checkpoint remains intact. No completed merged model was claimed.

Additional fixes:
- Empty `metrics.jsonl` falls back to parsing the persisted training log.
- Automatic job monitoring reconciles on backend startup without renting a GPU.
  Missing provider instances stop stale jobs and preserve local checkpoints.
- Confirmed `no_such_ask` refusals try the next available offer within budget;
  ambiguous network errors never retry a rental, avoiding duplicate charges.
- Recently failed provisioning offers are excluded from immediate retries.
- Rental cost estimates include provisioning/preparation time as well as training.

A recovery run was launched through **Resume from this run**, ceiling USD 0.60/hour:
job `auto_1792704114609`, CMP 170HX instance `57275507`, quoted USD
0.42777778/hour. Follow its preparation, evaluation and cleanup below.


### Recovery defects measured in the browser

The first resumed run (`run_1792704596322`) correctly restored step 10 and did
not train additional steps (trainer runtime 0.0135 seconds). Its displayed zero
train loss is therefore not an improvement over the source run.
Preparation then failed: `trainer.checkpoint_info` treated `config.json` plus
an rsync temporary `.model.safetensors.*` file as a complete merged export,
while registration correctly refused it. The GPU was deleted, confirmed via
provider API. The checkpoint remains intact.

Corrections:
- Merged export detection uses the same actual-weight check as registration.
- Preparation rereads checkpoint files instead of trusting stale cached metadata.
- A resumed run copies training checkpoints, excluding merged exports and hidden
  temporary transfer files; it must merge its own final adapter.
- Registration of an already merged checkpoint passes its actual merged path.
- The automatic launcher publishes the run id before synchronous dataset upload.
  The browser also discovers the matching new run during this transition, so logs
  and run details remain accessible before training starts.
- The timeline distinguishes dataset/checkpoint transfer, and model-file download
  exposes real file counts from Hugging Face progress output. No overall-time
  percentage is inferred from file counts.
- Resource deletion is checked against the provider before being marked confirmed;
  run metadata also records that the remote training host is no longer available.

Second recovery: `auto_1792705051476`, instance `57277167`, CMP 170HX at USD
0.47703704/hour, same USD 0.60/hour ceiling. Result: completed preparation and registration; see the final outcome below.


## Final outcome

- Recovery run: `run_1792705094270`, resumed from the saved step-10 checkpoint.
  No additional optimization steps were performed; the UI explicitly says so
  and does not show restore loss zero as a quality improvement.
- Remote merge, complete model download and registration succeeded.
- Registered id: `custom-monkeyocrv2-parsing-run-1792705094270`.
- Local weights: `data/projects/1/runs/run_1792705094270/checkpoints/checkpoint-10-merged/model.safetensors`.
- Safetensors header offsets require **1,444,759,792 bytes**; the actual file
  contains exactly **1,444,759,792 bytes**. This verifies complete transfer,
  not inference quality.
- Preparation ended at 21:43:30 UTC. The final automatic job is `finished`
  with `instance_destroyed=true`.
- A separate provider API read confirmed that final instance `57277167` and
  earlier trial instances `57218638`, `57221162`, `57224378`, `57226361` and
  `57275507` were all absent. No trial GPU was left rented.
- Automatic OCR evaluation remains **failed**, not passed. After correcting
  version-tuple detection, the pinned official legacy module imported
  `vllm.attention.backends.registry`, which vLLM 0.11.0 lacks. The module exists
  in upstream vLLM 0.11.2, making a tested image/toolchain upgrade the next
  candidate; compatibility of the whole model has not been claimed or guessed.

### Progress UI acceptance

The browser exposed another race: status polling could stop between training
completion and automatic checkpoint preparation. It now continues for the
whole automatic job and includes queued/starting preparation states. Without
reloading, the displayed download advanced from **54.5% to 85.2%**, including
transferred bytes, total size, current speed and a chart. The preparation
sequence now includes evaluation; no download percentage is displayed for a
phase with no measurable download progress. Rental/startup diagnostics and
training logs have distinct controls. Empty after-training/performance panels
are hidden until relevant. Confirmed GPU release has an explicit completion
message.

Builds succeeded after these changes. Python modules compiled. No additional
unit tests were run for this browser workflow trial.

Evidence: `screenshots/vast-sync-details-20261022.png`,
`screenshots/vast-live-download-20261022.png` and
`screenshots/vast-workflow-complete-20261022.png`.

Sources for the remaining compatibility issue:
- [Official MonkeyOCRv2 launcher](https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/serve.py)
- [vLLM 0.11.2 attention registry](https://github.com/vllm-project/vllm/blob/v0.11.2/vllm/attention/backends/registry.py)

Final browser review confirmed the registered model, the explicit GPU-release message,
absence of a remote-cleanup action after resource deletion, one primary serving link,
and a distinct inference-environment warning for the failed evaluation. The final
frontend build passed.
