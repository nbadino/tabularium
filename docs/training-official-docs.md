# Official fine-tuning docs — OCR/document VLMs (verified 2026-10-18)

This is the **verbatim vendor reference** behind `services/train_recipes.py`. The operational
guide (what Tabularium generates, environment variables, test log) is
[Fine-tuning](guide/training.md). Where the two disagree, the recipe and the guide say what is
executed and this page keeps the source quote.

Research notes: what each vendor *actually* documents for SFT, with verbatim commands.
Every row below was read from the vendor repository / official docs on 2026-10-18 (`main` branches).
Nothing here is reconstructed: where the vendor ships no recipe, the entry says `absent`.

Status legend: **vendor** = documented by the model owner · **integration** = documented by the
training framework that the model owner points to · **absent** = no official recipe exists.

---

## 1. MonkeyOCRv2 (Yuliang-Liu/MonkeyOCRv2, `parsing/train`)

**Status: vendor.** Dedicated fine-tuning README inside the repo, plus two runnable scripts,
bundling a MonkeyOCRv2-compatible ms-swift fork (`pip install -e ./ms-swift`).

- Source: <https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/train/README.md>
- Scripts: <https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/train/scripts/lora.sh> · <https://github.com/Yuliang-Liu/MonkeyOCRv2/blob/main/parsing/train/scripts/full.sh>

### Canonical identifiers

```
--model zenosai/MonkeyOCRv2-B-Parsing   (or -S-Parsing)
--model_type monkeyocrv2
--template monkeyocrv2
```

Registration in the bundled fork: `parsing/train/ms-swift/swift/llm/template/template/monkeyocrv2.py`
— `image_token_id = 151655`, `placeholder_tokens = ['<|image_pad|>']`,
`<|vision_start|><|image_pad|><|vision_end|>`; it also registers `monkeyocrv2_und` for the
understanding checkpoint.

### Exact command (LoRA) — verbatim from `scripts/lora.sh`

```bash
export MAX_PIXELS=1003520

swift sft \
    --model zenosai/MonkeyOCRv2-B-Parsing \
    --model_type monkeyocrv2 \
    --template monkeyocrv2 \
    --attn_impl flash_attention_2 \
    --dataset /path/to/train.jsonl \
    --load_from_cache_file True \
    --split_dataset_ratio 0.0 \
    --dataloader_num_workers 4 \
    --dataset_num_proc 4 \
    --dataset_shuffle True \
    --streaming False \
    --max_length 16384 \
    --truncation_strategy 'right' \
    --max_pixels $MAX_PIXELS \
    --packing False \
    --padding_free True \
    --train_type 'lora' \
    --lora_rank 8 \
    --lora_alpha 32 \
    --target_modules 'all-linear' \
    --freeze_vit True \
    --freeze_aligner False \
    --freeze_llm False \
    --torch_dtype 'bfloat16' \
    --deepspeed 'zero1' \
    --gradient_checkpointing True \
    --per_device_train_batch_size 4 \
    --per_device_eval_batch_size 2 \
    --gradient_accumulation_steps 1 \
    --num_train_epochs 1 \
    --learning_rate 1e-5 \
    --warmup_ratio 0.05 \
    --lr_scheduler_type 'cosine' \
    --eval_steps 400000 \
    --save_steps 500 \
    --save_total_limit 2 \
    --logging_steps 5 \
    --no_add_version \
    --output_dir $output_dir \
    $resume_options 2>&1 | tee -a "$LOG_FILE"
```

`full.sh` is identical except `--train_type 'full'` and no LoRA block.

### Hyperparameters

max_length 16384 · max_pixels 1003520 · epochs 1 · lr 1e-5 · cosine · warmup 0.05 ·
batch 4 · grad-accum 1 · bf16 · grad-checkpointing True · deepspeed zero1 ·
LoRA rank 8 / alpha 32 / `all-linear` · save_steps 500, save_total_limit 2 ·
`eval_steps 400000` (i.e. effectively no eval).

### Dataset

```json
{"messages": [{"role": "user", "content": "<image><PROMPT>"}, {"role": "assistant", "content": "<CONTENT>"}], "images": ["/absolute/path/to/image.jpg"]}
```

> "The number of `<image>` placeholders in `messages` must match the number of entries in `images`.
> Absolute image paths are recommended."

Prompts (verbatim):
- Text: `Please output the text content from the image.`
- Formula: `Please write out the expression of the formula in the image using LaTeX format.` (`$...$`)
- Table HTML: `This is the image of a table. Please output the table in HTML format.`
- Table OTSL: `Please extract the table from the image and represent it in OTSL format.` +
  `python html2otsl.py -i html.jsonl -o otsl.jsonl` — *"we recommend using the OTSL format"*
- Layout: `Please output the categories and coordinates of the document elements in reading order.`
  → list of `{'bbox': [...], 'label': ...}` dicts.

Labels: Caption, Footnote, List-item, Page-footer, Page-header, Section-header, Text, Title,
Formula, Table, Picture (everything except Formula/Table/Picture uses the Text prompt).

### Resume / export

Resume: **yes, built into both scripts** — `get_latest_checkpoint()` scans `output_dir` for
`checkpoint-[0-9]*` and passes `--resume_from_checkpoint $RESUME_CHECKPOINT_PATH`.
Export/merge: **not documented** in the training README (use ms-swift `swift export --merge_lora true`).

### ms-swift table rows

Upstream `modelscope/ms-swift` (`docs/source_en/Instruction/Supported-models-and-datasets.md`,
lines 1215–1216) lists only the **encoders**, not `-Parsing`:

```
|[zenosai/MonkeyOCRv2-S](https://modelscope.cn/models/zenosai/MonkeyOCRv2-S)|monkeyocrv2|monkeyocrv2||transformers>=4.45, qwen_vl_utils|✘|vision, embedding, document-understanding|[zenosai/MonkeyOCRv2-S](https://huggingface.co/zenosai/MonkeyOCRv2-S)|
|[zenosai/MonkeyOCRv2-B](https://modelscope.cn/models/zenosai/MonkeyOCRv2-B)|monkeyocrv2|monkeyocrv2||transformers>=4.45, qwen_vl_utils|✘|vision, embedding, document-understanding|[zenosai/MonkeyOCRv2-B](https://huggingface.co/zenosai/MonkeyOCRv2-B)|
```

The bundled fork's own docs table does **not** contain any `monkey` row (checked both
`docs/source` and `docs/source_en`).

> **Trap.** Upstream ms-swift's `MonkeyOCRv2Template` (added for the encoder) uses
> `<|IMAGE|>` and `norm_bbox='none'`; the Parsing checkpoint needs `<|vision_start|><|image_pad|>…`
> from the **bundled** fork. Same `--model_type`/`--template` strings, different template code ⇒
> the Parsing checkpoints must be trained with `parsing/train/ms-swift`, not with upstream ms-swift.

---

## 2. Qwen3-VL

Two independent official recipes.

### 2a. ms-swift (the one Tabularium's `--model_type` world uses)

- Source: <https://github.com/modelscope/ms-swift/blob/main/docs/source_en/BestPractices/Qwen3-VL-Best-Practice.md>
- IDs: `--model_type qwen3_vl` (`qwen3_vl_moe` for 30B-A3B / 235B-A22B), `--template qwen3_vl`.
  Neither flag appears in the example command — ms-swift resolves both from the model id.
- **Status: integration** (ms-swift is the framework Qwen points to for `swift sft`; Qwen's own repo
  ships a different framework, 2b).

Exact Dense-model command (verbatim, `### Dense Models`):

```shell
# 2 * 21GiB
PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' \
IMAGE_MAX_TOKEN_NUM=1024 \
VIDEO_MAX_TOKEN_NUM=128 \
FPS_MAX_FRAMES=16 \
NPROC_PER_NODE=2 \
CUDA_VISIBLE_DEVICES=0,1 \
swift sft \
    --model Qwen/Qwen3-VL-4B-Instruct \
    --dataset 'AI-ModelScope/alpaca-gpt4-data-zh#10000' \
              'AI-ModelScope/LaTeX_OCR:human_handwrite#5000' \
              'swift/VideoChatGPT:Generic#2000' \
    --load_from_cache_file true \
    --split_dataset_ratio 0.01 \
    --tuner_type lora \
    --torch_dtype bfloat16 \
    --num_train_epochs 1 \
    --per_device_train_batch_size 1 \
    --per_device_eval_batch_size 1 \
    --attn_impl flash_attn \
    --padding_free true \
    --learning_rate 1e-4 \
    --lora_rank 8 \
    --lora_alpha 32 \
    --target_modules all-linear \
    --freeze_vit true \
    --freeze_aligner true \
    --packing true \
    --gradient_checkpointing true \
    --vit_gradient_checkpointing false \
    --gradient_accumulation_steps 2 \
    --eval_steps 100 \
    --save_steps 100 \
    --save_total_limit 2 \
    --logging_steps 5 \
    --max_length 4096 \
    --output_dir output \
    --warmup_ratio 0.05 \
    --deepspeed zero2 \
    --dataset_num_proc 4 \
    --dataloader_num_workers 4
```

Post-training eval / export (verbatim):

```shell
swift infer --adapters output/vx-xxx/checkpoint-xxx --stream true --max_new_tokens 2048 --load_data_args true
```

```shell
swift export \
    --model output/vx-xxx/checkpoint-xxx \
    --push_to_hub true \
    --hub_model_id '<your-model-id>' \
    --hub_token '<your-sdk-token>'
```

MoE: `megatron sft` (8×80 GiB), `--lr 1e-5`, `--global_batch_size 4`, `--finetune true`,
full-parameter — different toolchain (`megatron`), not `swift sft`.

Dataset (ms-swift JSONL, from the same page):

```jsonl
{"messages": [{"role": "user", "content": "<image><image>What's the difference between these two images?"}, {"role": "assistant", "content": "The first one is a kitten, the second one is a puppy"}], "images": ["/xxx/x.jpg", "/xxx/x.png"]}
```

> "Qwen3-VL's bbox output uses normalized 1000 relative coordinates. You can use the grounding
> dataset format provided by ms-swift, where the coordinates in "bbox" are absolute coordinates,
> and ms-swift will automatically convert absolute coordinates to normalized 1000 relative
> coordinates." (`"objects": {"ref": [...], "bbox": [[x1,y1,x2,y2], ...]}`)

Table output format: **Qwen HTML format** ("the parsing of documents has reached a higher level,
including not only text but also layout position information and our Qwen HTML format" —
<https://github.com/QwenLM/Qwen3-VL#readme>, cookbook `cookbooks/document_parsing.ipynb`).

### 2b. Qwen's own framework (`qwen-vl-finetune`)

- Source: <https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-finetune/README.md>
- Script: <https://github.com/QwenLM/Qwen3-VL/blob/main/qwen-vl-finetune/scripts/sft_qwen3_4b.sh>
- **Status: vendor.** torchrun + DeepSpeed ZeRO-3, **not** ms-swift. Different dataset schema.

Verbatim from `sft_qwen3_4b.sh` (key args):

```bash
lr=1e-5
batch_size=4
grad_accum_steps=4
...
    --deepspeed ${deepspeed} \
    --model_name_or_path "${llm}" \
    --dataset_use ${datasets} \
    --data_flatten True \
    --tune_mm_vision False \
    --tune_mm_mlp True \
    --tune_mm_llm True \
    --bf16 \
    --output_dir ${output_dir} \
    --num_train_epochs 0.5 \
    --per_device_train_batch_size ${batch_size} \
    --per_device_eval_batch_size $((batch_size*2)) \
    --gradient_accumulation_steps ${grad_accum_steps} \
    --max_pixels 50176 \
    --min_pixels 784 \
    --eval_strategy "no" \
    --save_strategy "steps" \
    --save_steps 1000 \
    --save_total_limit 1 \
    --learning_rate ${lr} \
    --weight_decay 0 \
    --warmup_ratio 0.03 \
    --max_grad_norm 1 \
    --lr_scheduler_type "cosine" \
    --logging_steps 1 \
    --model_max_length 8192 \
    --gradient_checkpointing True \
    --dataloader_num_workers 4 \
    --run_name ${run_name} \
    --report_to wandb"
```

README guidance: *"Training hyperparameters, the suggested learning rate is from 1e-6 to 2e-7"*,
`--lora_r 8 --lora_alpha 16 --lora_dropout 0.0`, `--deepspeed zero3.json`,
`"The Qwen3VL MoE model does not support DeepSpeed with ZeRO-3"`, and
*"Training resolution is critical … `--max_pixels` and `--min_pixels` should be properly set"*.

Dataset (Qwen schema — `image` / `conversations` with `from`/`value`, not `messages`):

```json
{"image": "images/001.jpg",
 "conversations": [{"from": "human", "value": "<image>\nWhat's the main object in this picture?"},
                   {"from": "gpt", "value": "A red apple on a wooden table"}]}
```

### ms-swift table row

```
|[Qwen/Qwen3-VL-8B-Instruct](https://modelscope.cn/models/Qwen/Qwen3-VL-8B-Instruct)|qwen3_vl|qwen3_vl|hermes|transformers>=4.57, qwen_vl_utils>=0.0.14, decord|✔|vision, video|[Qwen/Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)|
```

(`qwen3_vl_moe`/`qwen3_vl` for 30B-A3B and 235B-A22B; `qwen3_vl_emb`, `qwen3_vl_reranker` for the
embedding/reranker variants.)

### Flags that must not travel

`--freeze_aligner true` (MonkeyOCRv2 has `False`), `--packing true` (MonkeyOCRv2 `False`),
`--deepspeed zero2` (MonkeyOCRv2 `zero1`), `--attn_impl flash_attn` (MonkeyOCRv2 spells it
`flash_attention_2`), `IMAGE_MAX_TOKEN_NUM=1024` env (a Qwen-only preprocessor knob),
`--vit_gradient_checkpointing false`, `--max_length 4096` (MonkeyOCRv2 16384).
`--max_pixels 1003520` is **not** in the Qwen recipe.

---

## 3. Unlimited-OCR (baidu/Unlimited-OCR)

**Status: absent.** The official repository documents inference only. Fine-tuning appears exactly
once, as a news line, with no command, no dataset, no toolchain script:

> "- [2026/07/21] 🤝 Thanks to the [ms-swift community](https://github.com/modelscope/ms-swift) for
> their support, our model now supports training with [ms-swift](https://github.com/modelscope/ms-swift)."
>
> — <https://github.com/baidu/Unlimited-OCR#readme> (identical line in
> <https://huggingface.co/baidu/Unlimited-OCR>)

The repo has 12 files, no `train/`, no scheduler, no JSONL schema, no hyperparameters.

**Toolchain: ms-swift only** (as a pointer). There is **no** `examples/models/unlimited_ocr/` in
ms-swift either, so even the framework ships no runnable script for it — only registration:
`swift/template/templates/deepseek.py` (`MLLMTemplateType.unlimited_ocr`, a `DeepseekOCR` subclass
with an R-SWA windowed `data_collator`, `no_repeat_ngram_size` / `ngram_window` generation env args,
and official `re_match`/`process_image_with_refs` post-processing), plus
`swift/model/model_arch.py` (`MLLMModelArch.unlimited_ocr`).

**Vendor inference prompts** (the only place prompt strings appear — these are what training
targets would have to match, but the vendor does not say so):

```
'<image>document parsing.'
'<image>Multi page parsing.'
```

with `generate(..., image_mode="gundam", ngram_window=128)`.

**ms-swift table row:**

```
|[PaddlePaddle/Unlimited-OCR](https://modelscope.cn/models/PaddlePaddle/Unlimited-OCR)|unlimited_ocr|unlimited_ocr||transformers==4.46.3, easydict|✘|vision|[PaddlePaddle/Unlimited-OCR](https://huggingface.co/PaddlePaddle/Unlimited-OCR)|
```

(the Modelscope id is `PaddlePaddle/Unlimited-OCR` while the GitHub/HF org is `baidu`; the HF link in
that row 401s — the live card is `baidu/Unlimited-OCR`).

**Any `swift sft` command for Unlimited-OCR is a reconstruction, not a vendor recipe.**

---

## 4. dots.ocr / dots.mocr (rednote-hilab)

**Status: absent (vendor), registration-only (ms-swift).**

- `rednote-hilab/dots.ocr` now **301-redirects to `rednote-hilab/dots.mocr`** (the repo was renamed);
  both URLs return the same README. <https://github.com/rednote-hilab/dots.mocr>
- The repo (91 files) contains **no** `train/`, no `sft`, no scheduler, no dataset — inference and
  evaluation only. README documents only model loading (`AutoModelForCausalLM.from_pretrained(...,
  trust_remote_code=True)`) and prompt usage.
- No `examples/models/dots*` in ms-swift, no BestPractices page, so ms-swift has no runnable script.

**ms-swift table row — `dots.ocr` only, `dots.mocr` is absent from the table:**

```
|[rednote-hilab/dots.ocr](https://modelscope.cn/models/rednote-hilab/dots.ocr)|dots_ocr|dots_ocr||transformers>=4.51.0|✘|-|[rednote-hilab/dots.ocr](https://huggingface.co/rednote-hilab/dots.ocr)|
```

(`rednote-hilab/dots.llm1.base` / `.inst` are `dots1` — the LLM, unrelated to OCR fine-tuning.)

**Canonical `--model_type dots_ocr --template dots_ocr`** exists only as code:
`swift/template/templates/dots.py::DotsOCRTemplate` — `image_token_id = 151665`,
`placeholder_tokens = ['<|imgpad|>']`, image context `<|img|><|imgpad|><|endofimg|>`, and prompt
skeleton `'<|user|>{{QUERY}}<|endofuser|><|assistant|>'` / `chat_sep ['<|endofassistant|>']`.
That prompt skeleton is the *training* contract — it is not documented anywhere by the vendor.

**No official hyperparameters, dataset format or resume/export command exist for dots.\***
(`dots.mocr` output is structured JSON-ish document parsing with bbox + HTML tables; the vendor
README shows the format but never states it as a training target.)

---

## 5. GLM-OCR (zai-org/GLM-OCR) — LLaMA-Factory

**Status: vendor.** Official `examples/finetune/README.md` (announced 2026.2.12), with two YAMLs and
example data in the repo.

- README: <https://github.com/zai-org/GLM-OCR/blob/main/examples/finetune/README.md>
- Full YAML: <https://github.com/zai-org/GLM-OCR/blob/main/examples/finetune/glm_ocr_full_sft.yaml>
- LoRA YAML: <https://github.com/zai-org/GLM-OCR/blob/main/examples/finetune/glm_ocr_lora_sft.yaml>
- Example data: `examples/finetune/cosyn_chemical.json` + `examples/finetune/cosyn_chemical/`

### Canonical values

`template: glm_ocr` (*"Must be set to `glm_ocr` for GLM-OCR"*) · `stage: sft` · `finetuning_type: full|lora`.
ms-swift equivalent: `--model_type glm_ocr --template glm_ocr`.

### Exact LoRA YAML (verbatim, `glm_ocr_lora_sft.yaml`)

```yaml
### model
model_name_or_path: zai-org/GLM-OCR    # or local path: /path/to/GLM-OCR
trust_remote_code: true

### method
stage: sft
do_train: true
finetuning_type: lora
lora_rank: 8
lora_target: all

### dataset
dataset: cosyn_chemical
template: glm_ocr
cutoff_len: 2048
max_samples: 1000
preprocessing_num_workers: 16
dataloader_num_workers: 4

### output
output_dir: saves/glm-ocr/lora/sft
logging_steps: 10
save_steps: 500
plot_loss: true
overwrite_output_dir: true
save_only_model: false
report_to: none  # choices: [none, wandb, tensorboard, swanlab, mlflow]

### train
per_device_train_batch_size: 4
gradient_accumulation_steps: 4
learning_rate: 1.0e-4
num_train_epochs: 3
lr_scheduler_type: cosine
warmup_ratio: 0.1
bf16: true
ddp_timeout: 180000000
resume_from_checkpoint: null

### eval
# val_size: 0.1
# per_device_eval_batch_size: 1
# eval_strategy: steps
# eval_steps: 500
```

Full example (`glm_ocr_full_sft.yaml`) is the same file with:

```yaml
finetuning_type: full
freeze_vision_tower: true
freeze_multi_modal_projector: true
freeze_language_model: false
...
gradient_accumulation_steps: 2
learning_rate: 1.0e-5
```

### Exact command

```bash
DISABLE_VERSION_CHECK=1 CUDA_VISIBLE_DEVICES=0 \
  llamafactory-cli train path/to/glm_ocr_full_sft.yaml
```

```bash
DISABLE_VERSION_CHECK=1 FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=8 \
  llamafactory-cli train path/to/glm_ocr_full_sft.yaml
```

```bash
DISABLE_VERSION_CHECK=1 FORCE_TORCHRUN=1 NNODES=1 NPROC_PER_NODE=8 \
  llamafactory-cli train path/to/glm_ocr_full_sft.yaml \
  --deepspeed examples/deepspeed/ds_z2_config.json
```

### Dataset — ShareGPT, images relative to LLaMA-Factory's `data/`

```json
[
  {
    "messages": [
      { "role": "user", "content": "<image>Code Generation:" },
      { "role": "assistant", "content": "```rdkit\nC1=CC=CC=C1\n```" }
    ],
    "images": ["cosyn_chemical/example.png"]
  }
]
```

> "Each `<image>` tag in the text corresponds to one entry in the `images` list. The count **must
> match exactly**. Image paths are **relative to** the `data/` directory of the LLaMA-Factory
> repository. `role` can only be `user` or `assistant`."

Registration in `data/dataset_info.json` (`formatting: "sharegpt"`, `columns.messages/images`,
`tags.role_tag/content_tag/user_tag/assistant_tag`).

Training prompts (must stay verbatim for continued training on those tasks):
`Text Recognition:` · `Table Recognition:` · `Formula Recognition:` — *"place the image before the
text prompt, e.g., `<image>Text Recognition:`"*.

### Resume / export

- Resume: `resume_from_checkpoint: null` in the YAML (set to a dir to resume) — documented only as a
  YAML key, no procedure.
- Export / merge adapters: **yes**, in the FAQ:

```bash
llamafactory-cli export \
  --model_name_or_path zai-org/GLM-OCR \
  --adapter_name_or_path saves/glm-ocr/lora/sft \
  --template glm_ocr \
  --export_dir saves/glm-ocr/lora/sft/merged \
  --trust_remote_code true
```

### Source patches the doc requires

```bash
pip install -r requirements/metrics.txt
pip install --upgrade transformers   # LLaMA-Factory pins 5.0.0, which does not support GLM-OCR
```

VRAM: *"LoRA fine-tuning can run on a single GPU with 8 GB VRAM; full fine-tuning requires ~24 GB."*

### Table format

**Unspecified in vendor docs.** The three prompts are named; the README never says whether
`Table Recognition:` returns HTML (it is HTML in practice on the OmniDocBench pipeline), and there
is no OTSL mention anywhere in GLM-OCR docs.

### ms-swift table row

```
|[ZhipuAI/GLM-OCR](https://modelscope.cn/models/ZhipuAI/GLM-OCR)|glm_ocr|glm_ocr|glm4|transformers>=5.0.1dev0|✘|-|[zai-org/GLM-OCR](https://huggingface.co/zai-org/GLM-OCR)|
```

(`swift/template/templates/glm.py` registers `MLLMTemplateType.glm_ocr`; no `examples/models/glm_ocr`.)

---

## 6. DeepSeek-OCR-2

**Status: integration + third-party vendor.** deepseek-ai's own card documents **inference only** —
no training section at all (<https://huggingface.co/deepseek-ai/DeepSeek-OCR-2>). The two recipes
that exist are ms-swift's row/registration and Unsloth's official tutorial + notebook.

### 6a. ms-swift

Table row:

```
|[deepseek-ai/DeepSeek-OCR-2](https://modelscope.cn/models/deepseek-ai/DeepSeek-OCR-2)|deepseek_ocr2|deepseek_ocr2||transformers==4.46.3, easydict|✘|vision|[deepseek-ai/DeepSeek-OCR-2](https://huggingface.co/deepseek-ai/DeepSeek-OCR-2)|
```

ms-swift ships **no `deepseek_ocr2` example**; the nearest is the v1 script
<https://github.com/modelscope/ms-swift/blob/main/examples/models/deepseek_ocr/train.sh> (verbatim):

```bash
# 24GiB
pip install "transformers==4.46.3" easydict "peft==0.15.2"

CUDA_VISIBLE_DEVICES=0 \
swift sft \
    --model deepseek-ai/DeepSeek-OCR \
    --dataset 'AI-ModelScope/LaTeX_OCR:human_handwrite#20000' \
    --load_from_cache_file true \
    --split_dataset_ratio 0.01 \
    --tuner_type lora \
    --torch_dtype bfloat16 \
    --num_train_epochs 1 \
    --per_device_train_batch_size 1 \
    --per_device_eval_batch_size 1 \
    --learning_rate 1e-4 \
    --lora_rank 8 \
    --lora_alpha 32 \
    --target_modules all-linear \
    --freeze_vit true \
    --freeze_aligner true \
    --gradient_accumulation_steps 16 \
    --eval_steps 50 \
    --save_steps 50 \
    --save_total_limit 2 \
    --logging_steps 5 \
    --max_length 4096 \
    --output_dir output \
    --warmup_ratio 0.05 \
    --dataset_num_proc 4 \
    --dataloader_num_workers 4
```

For OCR-2 only the model id and `--model_type deepseek_ocr2` / `--template deepseek_ocr2` differ;
**ms-swift does not publish an OCR-2-specific command**, so this is an adaptation, not a shipped
recipe.

### 6b. Unsloth tutorial (the only end-to-end OCR-2 recipe)

- Guide: <https://unsloth.ai/docs/models/tutorials/deepseek-ocr-2>
- Notebook: <https://github.com/unslothai/notebooks/blob/main/nb/Deepseek_OCR_2_(3B).ipynb>
- Model: `unsloth/DeepSeek-OCR-2` (their transformers-compatible re-upload, *"no accuracy change"*).

LoRA (verbatim):

```python
model = FastVisionModel.get_peft_model(
    model,
    target_modules = [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    r = 16,           # The larger, the higher the accuracy, but might overfit
    lora_alpha = 16,  # Recommended alpha == r at least
    lora_dropout = 0,
    bias = "none",
    random_state = 3407,
    use_rslora = False,
    loftq_config = None,
)
```

Training arguments (verbatim):

```python
data_collator = DeepSeekOCR2DataCollator(
    tokenizer=tokenizer, model=model,
    image_size=768, base_size=1024, crop_mode=True, train_on_responses_only=True,
)
trainer = Trainer(
    model = model,
    data_collator = data_collator,   # Must use!
    train_dataset = converted_dataset,
    args = TrainingArguments(
        per_device_train_batch_size = 2,
        gradient_accumulation_steps = 4,
        warmup_steps = 5,
        max_steps = 60,
        # num_train_epochs = 1, # Set this instead of max_steps for full training runs
        learning_rate = 2e-4,
        logging_steps = 1,
        optim = "adamw_8bit",
        weight_decay = 0.001,
        lr_scheduler_type = "linear",
        seed = 3407,
        fp16 = not is_bf16_supported(),
        bf16 = is_bf16_supported(),
        output_dir = "outputs",
        report_to = "none",
        dataloader_num_workers = 2,
        remove_unused_columns = False,   # "You MUST put the below items for vision finetuning"
    ),
)
```

Dataset (verbatim conversion from the notebook — note the **role tags** and the nested `images`):

```python
instruction = "<image>\nFree OCR. "

def convert_to_conversation(sample):
    conversation = [
        {"role": "<|User|>", "content": instruction, "images": [sample['image']]},
        {"role": "<|Assistant|>", "content": sample["text"]},
    ]
    return {"messages": conversation}
```

i.e. `{"messages": [{"role": "<|User|>", "content": "<image>\nFree OCR. ", "images": [PIL]},
{"role": "<|Assistant|>", "content": "<text>"}]}` — different from ms-swift (`user`/`assistant` +
top-level `images`) and from LLaMA-Factory (ShareGPT).

Vendor inference prompts (the four official tasks):
`<image>\n<|grounding|>Convert the document to markdown.` · `<image>\n<|grounding|>OCR this image.` ·
`<image>\nFree OCR.` · `<image>\nParse the figure.`
Grounding/rec: `<image>\nLocate <|ref|>xxxx<|/ref|> in the image.`
Output style: **Markdown with grounding tags** (not OTSL, not HTML-only).

Save/merge:

```python
model.save_pretrained("deepseek_ocr_lora")            # adapters only
model.save_pretrained_merged("unsloth_finetune", tokenizer)   # 16-bit merged, for vLLM
```

Recommended inference settings (not training): `temperature 0.0`, `max_tokens 8192`,
`ngram_size 30`, `window_size 90`; visual tokens `(0-6)×768×768 + 1×1024×1024`.

### Flags that must not travel

`DeepSeekOCR2DataCollator(image_size=768, base_size=1024, crop_mode=True)` is mandatory in the
Unsloth path (any other collator silently breaks the vision path); `remove_unused_columns=False`
is mandatory for vision SFT; `<|User|>`/`<|Assistant|>` roles; LoRA targets are an explicit 7-module
list with `r=16/alpha=16` (not `all-linear`, not 8/32); `lr 2e-4` (10–100× the document-VLM norms);
`transformers==4.46.3` pin.

---

## 7. MinerU2.5 (opendatalab)

**Status: vendor, but with a private config.** Official lesson 04 of the MinerU training camp,
`04课：MinerU 1.2B 模型微调` in <https://github.com/opendatalab/mineru-tutorials>.

- Doc: `04课：MinerU 1.2B 模型微调/04课：MinerU 1.2B 模型微调 - 文档.md`
  (<https://github.com/opendatalab/mineru-tutorials/blob/main/04%E8%AF%BE%EF%BC%9AMinerU%201.2B%20%E6%A8%A1%E5%9E%8B%E5%BE%AE%E8%B0%83/04%E8%AF%BE%EF%BC%9AMinerU%201.2B%20%E6%A8%A1%E5%9E%8B%E5%BE%AE%E8%B0%83%20-%20%E6%96%87%E6%A1%A3.md>)
- Code bundle: `mineru_sft_code.zip` (attached to the lesson, **not in the repo**), tree:

```
mineru_sft_code/
├── ms_swift_core/     # [Submodule] 官方 ms-swift 源码
├── mineru_ext/        # MinerU 额外的训练逻辑
│   ├── cli            # 重写的训练脚本，包含sft
│   └── templates/     # 自定义 Prompt 模板等等
├── config/            # 配置文件 (数据集路径等)
├── run_mineru.py      # [入口] 自动注入 submodule 路径的启动器
└── requirements.txt
```

### Exact commands (verbatim)

```bash
CUDA_VISIBLE_DEVICES=0 python run_mineru.py sft --config configs/sft/sft_table_rec.yaml
```

```bash
NPROC_PER_NODE=2 CUDA_VISIBLE_DEVICES=0,1 python run_mineru.py sft --config configs/sft/sft_table_rec.yaml
```

> **不要直接使用 `swift sft` 命令，请使用 `python run_mineru.py sft` 进行替代。该脚本会自动加载
> mineru_ext 中的扩展功能。**
> ("Do **not** use the `swift sft` command directly; use `python run_mineru.py sft` instead — that
> script loads the extensions in `mineru_ext`.")

This is the single most important model-specific constraint for MinerU: `swift sft --model_type
qwen2_vl` on a MinerU2.5 checkpoint bypasses the custom prompt templates and the extra training
logic the vendor requires.

### Model / ms-swift type

- Checkpoint: `MinerU2.5-2509-1.2B` (`git clone https://huggingface.co/opendatalab/MinerU2.5-2509-1.2B`).
- Architecture is Qwen2-VL; the ms-swift table lists it as `qwen2_vl` (only the Pro revision is
  listed, the 2509 base is not):

```
|[OpenDataLab/MinerU2.5-Pro-2604-1.2B](https://modelscope.cn/models/OpenDataLab/MinerU2.5-Pro-2604-1.2B)|qwen2_vl|qwen2_vl|hermes|transformers>=4.45, qwen_vl_utils>=0.0.6, decord|✔|vision, video|[opendatalab/MinerU2.5-Pro-2604-1.2B](https://huggingface.co/opendatalab/MinerU2.5-Pro-2604-1.2B)|
```

### Hyperparameters

**Not published.** `configs/sft/sft_table_rec.yaml` lives inside the zip. The only parameter facts
stated in the lesson:

- `model` and `datasets` keys in the YAML must be **absolute paths**;
- *"如果 flash_attn 实在安装不上去的话，也可以跳过，对应的需要将训练的配置文件中的 attn_impl 改成
  sdpa，packing 改成 false"* — i.e. the config contains `attn_impl` and `packing`, and the
  flash-attn path implies `attn_impl: flash_attention_2`, `packing: true`;
- environment: torch 2.6.0 / cu124, vllm 0.8.5, flash-attn 2.7.4.post1, python 3.10,
  `pip install -e ms_swift_core`.

Anything quoting a learning rate / epoch / LoRA rank for MinerU2.5 is **community, not vendor**.

### Dataset

- 4 demo datasets under `dataset/demo` in the zip; the only public preparation script is
  `dataset/pubtabnet/convert_pubtabnet2mineru.py` (PubTabNet → MinerU JSONL,
  `wget https://huggingface.co/datasets/ajimeno/PubTabNet/blob/main/pubtabnet.tar.gz`).
- Training data must use the four official prompts (verbatim, with leading `\n`):

```
"\nTable Recognition:"    "\nFormula Recognition:"
"\nText Recognition:"     "\nLayout Detection:"
```

- Table target format: **not stated in the lesson** (MinerU's published output is HTML tables inside
  Markdown; the JSONL schema of the demo datasets is inside the zip).

### Resume / export

Not documented. Inference after training is documented:

```bash
python mineru_ext/scripts/run_mineru2_5_e2e_local.py \
--model "模型权重文件的路径" \
--img_dir "图片文件夹的路径" \
--output_json "输出的content list的json路径" \
--output_dir "以markdown的形式保存的提取结果"
```

```bash
python mineru_ext/scripts/run_mineru_vllm_singletask.py -i "输入的图片路径" -o "输出的路径" -m "模型权重路径" -p "选择prompt"
```

---

## 8. PaddleOCR-VL (PaddlePaddle) — PaddleFormers + ERNIEKit

**Status: vendor.** There is no standalone `PaddleOCR-VL` repository (404); the weights live at
`PaddlePaddle/PaddleOCR-VL` and fine-tuning is documented in two Paddle repositories.

### 8.1 PaddleFormers — the path Tabularium uses (full **and** LoRA)

Sources:
<https://github.com/PaddlePaddle/PaddleFormers/tree/develop/examples/best_practices/PaddleOCR-VL>
(configs: `paddleocr-vl_full_16k_config.yaml`, `paddleocr-vl_lora_16k_config.yaml`,
`paddleocr-vl_lora_export.yaml`).

```bash
CUDA_VISIBLE_DEVICES=0 \
paddleformers-cli train examples/best_practices/PaddleOCR-VL/paddleocr-vl_full_16k_config.yaml \
                        model_name_or_path=PaddlePaddle/PaddleOCR-VL \
                        train_dataset_path=./ocr_vl_sft-train_Bengali.jsonl \
                        eval_dataset_path=./ocr_vl_sft-test_Bengali.jsonl \
                        pre_alloc_memory=24
```

LoRA: same command with `paddleocr-vl_lora_16k_config.yaml`, lr `5.0e-4`, `pre_alloc_memory=16`.

- Dataset: `train_dataset_type: messages`, the same shape as ms-swift:
  `{"messages":[{"role":"user","content":"<image>OCR:"},{"role":"assistant","content":"…"}],"images":["…"]}`.
  Prompts: `OCR:`, `Table Recognition:` (OTSL), `Formula Recognition:`, `Chart Recognition:`.
- Full config: `fine_tuning: full`, batch 8, grad-accum 8, 2 epochs, `max_seq_len 16384`,
  lr `5.0e-6`, cosine, warmup 0.01, `bf16`, `sharding: stage1`, `pre_alloc_memory: 24`.
- LoRA config: same, lr `5.0e-4`, `lora: true`, `lora_rank: 8`, `pre_alloc_memory: 16`.
- No bboxes in the SFT targets: layout is a separate PaddleX detector (`PP-DocLayout`), trained
  with COCO annotations; the fine-tuning docs never tell you to retrain it.
- Merge: `paddleformers-cli export …/paddleocr-vl_lora_export.yaml model_name_or_path=… output_dir=…`
  → merged weights in `<output_dir>/export`.
- No published config for PaddleOCR-VL-1.6: the 1.5/0.9B configs are reused because the vendor says
  the architecture is unchanged (inferred, not documented).

### 8.2 ERNIEKit — the older path (full SFT only)

Source: <https://github.com/PaddlePaddle/ERNIE/blob/release/v1.5/docs/paddleocr_vl_sft.md>.
Config: `examples/configs/PaddleOCR-VL/sft/run_ocr_vl_sft_16k.yaml`.

```bash
CUDA_VISIBLE_DEVICES=0 \
erniekit train examples/configs/PaddleOCR-VL/sft/run_ocr_vl_sft_16k.yaml \
        model_name_or_path=PaddlePaddle/PaddleOCR-VL \
        train_dataset_path=./ocr_vl_sft-train_Bengali.jsonl
```

- Dataset is `image_info` + `text_info` (`tag: mask|no_mask`, alternating), **not** `messages`.
- `fine_tuning: Full`, `packing: True`, `packing_size: 8`, batch 1, grad-accum 8, 2 epochs,
  lr 5e-6, `max_seq_len 16384`, no freeze settings, `use_flash_attention: True`.
- No LoRA config or documentation for `stage: OCR-VL-SFT`.

Tabularium uses **8.1**: it documents LoRA as well as full SFT and its dataset shape is already the
canonical one. ERNIEKit's `image_info` exporter was removed rather than kept as a second source of
truth.

## 9. TeleOCR and LightOnOCR-3 — no training code

- TeleOCR (<https://github.com/caipeng328/TeleOCR>): inference only. The paper describes four
  training stages (VL alignment, deformation-aware, content-structure decoupled, GRPO) and even
  claims a full implementation release, but the repository has no `train/` and no toolchain.
- LightOnOCR-3 (<https://github.com/lightonai/LightOnOCR>): client, CLI and benchmarks only. The
  vendor blog documents Muon + packing SFT and a GRPO stage with a bbox reward
  `min(IoU/0.75, 1)`, but publishes no code, config or numeric SFT hyperparameters.

Both stay `inference-only` in `train_recipes.py`.

## Cross-model traps (summary)

| Flag / value | Belongs to | Do not copy to |
|---|---|---|
| `--max_pixels 1003520`, `MAX_PIXELS` env | MonkeyOCRv2 (vendor) | Qwen3-VL (`IMAGE_MAX_TOKEN_NUM`), MinerU, GLM-OCR (`cutoff_len`) |
| `--max_length 16384` | MonkeyOCRv2 | Qwen3-VL BP (4096), DeepSeek-OCR ms-swift (4096), GLM-OCR (`cutoff_len 2048`) |
| `--packing False` | MonkeyOCRv2 | Qwen3-VL BP (`true`), MinerU config (`packing: true`) |
| `--attn_impl flash_attention_2` | MonkeyOCRv2 | Qwen3-VL BP/DeepSeek-OCR (`flash_attn`) |
| `--deepspeed zero1` | MonkeyOCRv2 | Qwen3-VL BP (`zero2`), Qwen finetune (`zero3.json`) |
| `--freeze_aligner False` | MonkeyOCRv2 | Qwen3-VL BP + DeepSeek-OCR ms-swift (`true`) |
| `--train_type` | MonkeyOCRv2 | ms-swift docs use `--tuner_type` for the other models |
| `--vit_lr` | only if `--freeze_vit false` (MonkeyOCRv2 README) | never set with `freeze_vit true` |
| `swift sft` | MonkeyOCRv2, Qwen3-VL, GLM-OCR(no), DeepSeek-OCR | **MinerU2.5** (`run_mineru.py sft`) |
| dataset `messages`+`images` | ms-swift models | GLM-OCR (ShareGPT `data/`-relative), Qwen finetune (`image`+`conversations`), Unsloth (`<|User|>` roles) |
| absolute image paths | MonkeyOCRv2 ("absolute ... recommended"), ms-swift | GLM-OCR/LLaMA-Factory -> relative to the dataset directory (`images/<file>`) |
| table format OTSL | MonkeyOCRv2 (`html2otsl.py`) | Qwen3-VL (Qwen HTML), DeepSeek-OCR-2 (grounded Markdown), GLM-OCR (unspecified) |
