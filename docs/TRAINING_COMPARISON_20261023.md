# Automatic comparison on the training GPU

## Normal workflow

1. Train using the selected model’s recipe.
2. Merge the LoRA checkpoint on the same GPU host.
3. Prepare a separate serving environment on that host, retaining the training environment.
4. Serve the base checkpoint and evaluate the frozen validation samples.
5. Stop the base server, serve the merged checkpoint and evaluate exactly the same samples.
6. Save paired predictions, input hashes and metric deltas.
7. Download and register the checkpoint, then release and verify the rented instance.

No model transfer to a second GPU is needed. Training and serving execute sequentially so
training allocations do not compete with inference. The active user inference endpoint is
independent. Official model repositories are not edited; a launcher outside the checkout
handles the upstream version-detection guard in both the API process and spawned workers.
The compatibility module is scoped to the temporary server process through `PYTHONPATH`.

## Recovery trial

Source training: `run_1792680203611`; saved merged checkpoint: `run_1792705094270`.
The original instance was already deleted, so recovery requires uploading the saved checkpoint
to one temporary instance. The stricter recovery ceiling is USD 0.60/hour, below the authorized
EUR 1/hour ceiling. Only one trial instance is rented at a time; release is verified against
the provider API.

The training log identifies base revision `e8871a0057c98378219b369f924a25ba10f392bf`;
the recovery server resolved that exact revision too. Future comparisons resolve the base
revision recorded in the training log rather than silently taking a newer model revision.

The immutable run contains two held-out page layouts and six text crops. Training and
validation full-page filenames are disjoint. All 40 training images and all eight validation
images have disjoint SHA-256 hashes, including crops. Text crops cover masthead,
issue number and date, so results cannot establish body-text accuracy. No held-out verified
OTSL table samples exist, so TEDS and table-transcription improvement cannot be concluded.

Sampling uses temperature 0, top-p 1 and seed 42, the official MonkeyOCRv2 prompts, and the
same 1,003,520-pixel cap for both checkpoints. CER/WER use character/word-weighted edit
counts; layout includes label-aware matching and matched-box IoU. This is a small descriptive
comparison, not evidence of statistical generalization.

The first recovery comparison failed during vLLM engine startup after successful model
registration; it scored no samples. Instance `57280637` was verified absent. Startup failures
now preserve the full server log locally before cleanup. The second attempt exposed the actual cause: spawned workers re-import the official launcher
and lose a patch applied only in the parent process. Instance `57281965` was verified absent.
The corrected launcher propagates its compatibility module to worker startup. Recovery now
checks engine readiness before uploading the saved merged checkpoint, retains the same instance
for at most two bounded retries, and uses eager execution with one concurrent sequence.

## Artifacts

Run journals: `comparison_job.json`; installation/progress details: `comparison.log`;
startup diagnostics: `serve-base.log` / `serve-fine-tuned.log`;
paired predictions and metrics: `comparison_base.json`, `comparison_fine_tuned.json`,
`comparison.json`. Failed comparisons never count as measured improvement.

The Dockerfile now preinstalls the separate evaluation runtime for subsequent image builds.
This source change has not been published as a new GHCR image during the trial; the existing
`monkey-v4` image still installs that runtime on the rented host.

## Completed paired comparison

All 16 evaluations completed on instance `57282735` (CMP 170HX, USD 0.46963/hour).
The instance was released and the provider API returned no remaining instances.

| Metric | Base | Fine-tuned | Change |
| --- | ---: | ---: | ---: |
| Layout F1 | 37.50% | 37.50% | 0 pp |
| IoU of matched blocks | 95.70% | 95.70% | 0 pp |
| Text CER | 4.41% | 4.41% | 0 pp |
| Text WER | 21.43% | 21.43% | 0 pp |

The six text predictions are identical. The three character errors across 68 reference
characters are two wrong page digits (`6 → 9`, `8 → 5`) and one missing final period.
One layout prediction changes one bbox coordinate, in a block that fails the matching
threshold both before and after training; it does not affect the aggregate metrics.

The original and recovered LoRA adapter SHA-256 values are identical
(`5954ce2b0946c848d536f0f8b64179f775b97dc847e427dc6ad18c95ed3def4a`). All 198 LoRA
B matrices are nonzero (maximum absolute coefficient about 5e-5). The training log records
nonzero gradients and ten optimizer steps. Thus the training occurred and the recovery
preserved its adapter, but this comparison establishes **no measured quality improvement**.
Small updates need not change greedy output tokens. One epoch, nine training pages and an
initial learning rate of 1e-5 are a plausible explanation; this is not a demonstrated unique
cause. The validation set is too narrow to assess the actual table-heavy archive use case.

## Approved-table exclusion found after the comparison

The project does contain 11 approved pages with complete Table blocks, saved grids and OTSL
content. They were excluded from table-recognition training because page/block approval did not
propagate to cell verification: all text cells still have `verified=false`. The frozen snapshot
report explicitly lists every skipped table (538, 513, 629, 484, 583, 509, 543, 538, 520, 595 and
627 text cells respectively). Layout samples still include the Table bounding boxes.

Therefore zero table samples must not be described as missing table annotations. The concrete
problem is an inconsistency between page/block approval and approved-only export eligibility.
The existing approval readiness checks validate the grid but do not check cell verification,
whereas the builder does. Existing annotations and verification flags were left unchanged;
a fix must make approval scope explicit and show exclusions before a paid training run.

The subsequent user-authorized approval/export reconciliation and corrected table-inclusive
trial are recorded in [the repair report](APPROVAL_EXPORT_REPAIR_20261023.md).
