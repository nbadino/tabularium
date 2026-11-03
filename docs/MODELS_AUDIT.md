# Model and provider audit

Audited on 2026-11-03 against the vendors' primary sources: model cards and raw files on
Hugging Face (`generation_config.json`, `preprocessor_config.json`), the vendors' own
client, parser and serve code on GitHub, the vLLM recipes and vLLM source by tag, PyPI
metadata, and the provider API references. Nothing here is inferred from a summary; a
value that could not be read from a primary source is marked **unverified**.

The official value is the default. A deliberate deviation is allowed only when the code
says why next to it (AGENTS.md R3, R5). Tests pinning the official values below live in
`backend/tests/test_models_audit.py`; when a vendor changes a value, update this file and
that test in the same commit.

## 1. Gaps fixed in this change

| Model / provider | Aspect | Official | Before | Now | Source |
|---|---|---|---|---|---|
| MonkeyOCRv2-Parsing | temperature | `0` on every call (`_chat_completion`) | not sent → checkpoint `generation_config` samples at 0.6 / top-k 20 / top-p 0.95 | `recommended_generation = {"temperature": 0.0}` | [core_runner.py](https://raw.githubusercontent.com/Yuliang-Liu/MonkeyOCRv2/main/parsing/core_runner.py) L185 |
| MonkeyOCRv2-Parsing | block recognition `max_tokens` | 5000 | 3072 | `text_max_tokens = 5000` (clamped to the context) | core_runner.py L779 |
| PaddleOCR-VL-1.6 | direct-path temperature | 0 | not sent → vLLM default 1.0 | `sampling_for` → `temperature 0.0` | PaddleX vllm-server client; vLLM recipe |
| MinerU2.5 | direct-path temperature | 0.0 (`MinerUSamplingParams`) | not sent (greedy anyway via top-k 1) | `temperature 0.0` | [mineru_client.py](https://github.com/opendatalab/mineru-vl-utils) |
| GLM-OCR | direct-path prompts | only `Text Recognition:`, `Table Recognition:`, `Formula Recognition:`; layout comes from the SDK's PP-DocLayout | invented JSON-layout, "Transcribe all text…", OTSL prompts | the three official prompts (same strings as training); no layout prompt | [model card](https://huggingface.co/zai-org/GLM-OCR) |
| GLM-OCR | native labels | formatter keeps the PP-DocLayoutV3 class in `native_label`, prefixes titles with `#`/`##` | read `label` (already collapsed) → every title became Text | reads `native_label`, maps the PP-DocLayoutV3 classes, strips the heading marks | [result_formatter.py](https://raw.githubusercontent.com/zai-org/GLM-OCR/main/glmocr/postprocess/result_formatter.py) |
| dots.mocr | prompt | `prompt_layout_all_en`, indented bullets, trailing newline | reflowed text | byte-for-byte vendor text | [prompts.py](https://raw.githubusercontent.com/rednote-hilab/dots.mocr/master/dots_mocr/utils/prompts.py) |
| DeepSeek-OCR-2 | vLLM version | ≥ 0.16.0 (`deepseek_ocr2.py` first ships there) | 0.12.0 — the architecture does not exist, the model cannot load | 0.16.0 (spec and Modal template) | vLLM tags v0.12.0/v0.15.0 (404) vs v0.16.0; recipe `min_vllm_version` |
| DeepSeek-OCR-2 | `max_tokens` | 8192 | 4096 | 8192, clamped to the served context minus image and prompt (6144 at 8192) | vendor `run_dpsk_ocr2_*.py`; [recipe](https://raw.githubusercontent.com/vllm-project/recipes/main/DeepSeek/DeepSeek-OCR-2.md) |
| Unlimited-OCR | temperature | 0.0 | not sent → 1.0 (no temperature in `generation_config`) | `recommended_generation = {"temperature": 0.0}` | [vLLM unlimited_ocr.py](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/unlimited_ocr.py) |
| Unlimited-OCR | gundam/base mode on vLLM | `mm_processor_kwargs.crop_mode` | only `images_config.image_mode` (SGLang field) → ignored by vLLM | both sent | same file, `mm_kwargs.get("crop_mode", …)` |
| LightOnOCR-3-1B | transformers | `5.16.1` with vLLM 0.30.0 (pixtral import breaks on 5.17) | unpinned | `transformers_version="5.16.1"` | [lightonocr-1b.Dockerfile](https://raw.githubusercontent.com/lightonai/LightOnOCR/main/benchmarks/docker/lightonocr-1b.Dockerfile) |
| Qwen3-VL-8B | `min_pixels` | 512·32·32 (`qwenvl html`) | applied only when overridden | recommended value applies by default | Qwen3-VL cookbook |
| TeleOCR (Vast) | n-gram guard | engine built with `logits_processors=[TeleOCRLogitsProcessor]` | missing in the native gateway | added (`_engine_args`) | [TeleOCR_model.py](https://raw.githubusercontent.com/caipeng328/TeleOCR/main/TeleOCR/vlm_utils/TeleOCR_model.py) L99-102 |
| TeleOCR (local) | n-gram guard | `--logits-processors …VllmV1NoRepeatNGramLogitsProcessor` | missing in the local `serve_command` | added (cloud recipe already had it) | TeleOCR recipe |
| vLLM OpenAI server | `no_repeat_ngram_size` transport | custom args reach logits processors only via `vllm_xargs` | top-level `extra_args` (unknown field, ignored) except MinerU | `vllm_xargs` for every model | [vLLM chat protocol](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/chat_completion/protocol.py) |
| LlamaIndex OCRia-1 | OCR languages | `ParsingLanguages` enum: `en`, `it`, `la`, `ch_sim`… | Tesseract codes (`eng`, `ita`), default `["eng"]` | vendor enum, default `["en"]`; saved legacy codes are translated on read | [API reference](https://developers.llamaindex.ai/reference/resources/parsing/methods/create/index.md) |
| LlamaIndex OCRia-1 | job timeouts | base ≤ 7200 s, per page ≤ 300 s | allowed 86400 / 3600 | 7200 / 300 | same |
| Gemini 4 Argon | model id | `gemini-4-argon` (only id in the OpenCode Go catalog) | `gemini-4-argon-contributor` | `gemini-4-argon` | `GET https://opencode.ai/inference/go/v1/models` (2026-11-03) |
| Gemini 4 Argon | `maxOutputTokens` | covers thinking + answer; keep default or ≥ 32,768 | 3072 (crops) / 20480 (page) | floor 32,768 | Google AI Studio Gemini 4 guide |

## 2. Open items (not changed here)

| Model / provider | Item | Why it is open |
|---|---|---|
| PaddleOCR-VL-1.6 | `PaddleOCRVL()` built without `pipeline_version="v1.6"`; runtime `paddleocr[doc-parser]` unpinned (recipe: ≥ 3.6.0) | needs the Paddle runtime to test; risk of running v1/v1.5 against the 1.6 server on an old runtime |
| PaddleOCR-VL-1.6 | direct-path text `max_tokens` (official 8192) | kept at 3072: the served context is 12288 and the page image shares it |
| dots.mocr | bbox rescale uses the sent size, official divides by the `smart_resize` size (multiples of 28) | up to ~14 px per axis; needs a parser change with a regression page |
| GLM-OCR | MTP speculative tokens: README now 3, vLLM recipe 1 | kept at 1 (recipe); speed only |
| GLM-OCR | table output format | **unverified**: the SDK formatter implies HTML, Tabularium declares markdown |
| MinerU2.5 | direct path drops boxes with inverted coordinates; official swaps them. `mineru-vl-utils` 2.0.8 available (pinned 2.0.5) | low impact; 2.0.5 → 2.0.8 diff **unverified** (source path 404) |
| DeepSeek-OCR-2 | parser accepts one box per ref, vendor accepts a list; vendor scales by /999 | low impact |
| LightOnOCR-3-1B | parser anchored to line start, `section` label maps to Text | low impact |
| LlamaIndex OCRia-1 | OCRia-1 runs only on `cost_effective`/`agentic` without custom prompt; the panel still allows `agentic_plus` and a prompt | needs copy in the setup panel |
| Modal | requests over 150 s answer with 303; the client posts with `allow_redirects=False` | affects only non-streaming adapters (dots.mocr); following redirects needs a credential-safe design |
| Vast.ai | training startup script ~10.7k chars, documented limit 4048; list pagination sends `next_token` (API: `after_token`) | training provisioning, outside this audit's scope |
| Gemini 4 Argon | client timeouts 180–300 s, Google recommends ≥ 15 min | to measure on real pages before raising |
| Xiaomi-OCR-0 | evaluation-only repetition retry not implemented | not part of the inference contract |
| mlx-vlm | installed unpinned | pin with the next measured MLX run |

Everything else matched the official sources: serve flags, prompts, pixel caps and
coordinate scales of MonkeyOCRv2, MinerU2.5 (native path), Unlimited-OCR-2, Qwen3-VL,
Xiaomi-OCR-0 and Surya; GLM-OCR SDK sampling; dots.mocr sampling; TeleOCR sampling and
layout resize; LlamaParse transport; the Vast, RunPod, Modal and mlx-vlm endpoints.

## 3. Detailed findings per model

The tables below are the audit as run on `1344a3e` (before section 1's fixes):
"Tabularium now" means that commit. `MA` = `backend/app/services/model_adapters.py`,
`INF` = `backend/app/services/inference.py`.

### MonkeyOCRv2-Parsing (`zenosai/MonkeyOCRv2-B-Parsing`)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Prompts | `ALL_PROMPT`: LAYOUT / Text / Formula / Table(OTSL) / END2END, exact strings. `Footnote` is commented out, and no Picture key, so `need_infer=False` | Same 5 strings `MA:206-215`. Text prompt sent for every label except Column/Picture (`MA:217-220`), so Footnote is recognized too | none (prompts). Footnote recognition is an undocumented minor deviation | https://raw.githubusercontent.com/Yuliang-Liu/MonkeyOCRv2/main/parsing/core_runner.py (L35-48, L898-899) |
| **Sampling: temperature** | Each `_chat_completion` sends `"temperature": 0 if temperature is None else temperature` (greedy). Only the repeat-retry raises it, to `min(0.2*(n+1),0.8)` with top_p 0.95 | No `sampling_for` and no `recommended_generation` on `MonkeyOCRv2ParsingAdapter` (`MA:158-241`), so no temperature is sent. The checkpoint `generation_config.json` has `do_sample:true, temperature 0.6, top_k 20, top_p 0.95`, so vLLM **samples at T=0.6** on every layout, recognition and END2END call | **gap (high)** | core_runner.py L175-197; https://huggingface.co/zenosai/MonkeyOCRv2-B-Parsing/raw/main/generation_config.json |
| Repeat retry | Recognition and END2END: up to 3 retries (`MOCR2_REC_MAX_RETRIES`), T=0.2/0.4/0.6, top_p 0.95 | END2END only, one retry at T=0.2/top_p 0.95 (`INF:1238-1256`, with comment) | deviation-documented (partial) | core_runner.py L776-806 |
| max_tokens | layout 4096, block recognition 5000, END2END None, single-task table 4096 | layout via `_sampling_for` (no cap set here), text **3072** (`INF:1328`), table 8192 (`INF:139`), END2END 12288 (`MA:170`) | gap (low): text budget is below the official 5000 and can truncate long blocks | core_runner.py L440-442, L921, L1425-1433 |
| Image: layout | `get_layout` min_pixels=1003520; `MOCR2_MAX_PIXELS` = parse.py `--max-pixels` default 1003520 on every call | `LAYOUT_MIN_PIXELS = 1_003_520` (`INF:113`); cap `PARSE_MAX_PIXELS` (`models/monkeyocrv2.py:21,90`) | none | core_runner.py L436-442, L259; https://raw.githubusercontent.com/Yuliang-Liu/MonkeyOCRv2/main/parsing/parse.py L17 |
| Preprocessor (vLLM side) | `min_pixels 3136, max_pixels 11289600, patch 14, merge 2` (Qwen2VL) | Not overridden | none | https://huggingface.co/zenosai/MonkeyOCRv2-B-Parsing/raw/main/preprocessor_config.json |
| Output format / parser | 0-1000 bbox list, OTSL tables, parsed in core_runner | Verified in `docs/spec/10-official-conventions.md` §2.4-2.5 | none (not re-audited) | core_runner.py |
| Serving | `serve.py` (vllm serve + `--trust-remote-code`, DFlash `-d`, `--dflash-num-speculative-tokens` default 16). Defaults: max-model-len 16384, max-num-batched-tokens 16384, max-num-seqs 128, gpu-util 0.5 | Delegates to official `serve.py` (`MA:232-241`, `scripts/serve_model.sh:76-79`). max-model-len 24576, batched 24576, seqs 8, gpu 0.9 | deviation (tunables of the official script; 24576 is not explained beyond `MA:194`) | https://raw.githubusercontent.com/Yuliang-Liu/MonkeyOCRv2/main/parsing/serve.py |
| vLLM version | README: 0.25.1 for DFlash (CUDA ≥12.9), else 0.11.2 | `vllm_version="0.25.1"` (`models/monkeyocrv2.py:33`) | none | https://raw.githubusercontent.com/Yuliang-Liu/MonkeyOCRv2/main/README.md L265-273 |
| Newer version? | Latest repo commit is 2026-09-15 (html2otsl/otsl2html case fix). No new checkpoint | — | none | GitHub API commits |

### MinerU2.5 (`opendatalab/MinerU2.5-Pro-2605-1.2B`)

Default path: the official `MinerUClient.aio_two_step_extract` runs through `scripts/cloud/mineru_native_gateway.py`. The direct OpenAI path (`MA:269-461`) is only used when it is explicitly selected and for diagnostics.

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Checkpoint | 2605 is the newest (HF lists 2509, Pro-2604, Pro-2605) | `MinerU2.5-Pro-2605-1.2B` (`models/mineru2_5.py:47`) | none | https://huggingface.co/api/models?author=opendatalab&search=MinerU2.5 |
| Prompts | `DEFAULT_PROMPTS`: `\nTable Recognition:`, `\nFormula Recognition:`, `\nText Recognition:`, `\nLayout Detection:`, `\nImage Analysis:` (image/chart) | Same 4 (`MA:331-336`). Image Analysis is not exposed in the direct path; the native path inherits it | none | https://raw.githubusercontent.com/opendatalab/mineru-vl-utils/main/mineru_vl_utils/mineru_client.py L68-76 |
| System prompt | `DEFAULT_SYSTEM_PROMPT="You are a helpful assistant."` | None sent, but the checkpoint chat template injects the same default | none | vlm_client/base_client.py L11; https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B/raw/main/chat_template.jinja |
| Sampling (native) | `MinerUSamplingParams`: temperature 0.0, top_p 0.01, top_k 1, rep 1.0, no_repeat_ngram 100. Text/eq/image: presence 1.0 / freq 0.05. Table: presence 1.0 / freq 0.005. Layout: no penalty | Gateway keeps client defaults unless the user overrides (`mineru_native_gateway.py:52-78`); `recommended_generation` is empty | none | mineru_client.py L36-86 |
| Sampling (direct path) | as above, with **temperature 0.0** | `sampling_for` sends top_p 0.01, top_k 1, penalties, `vllm_xargs.no_repeat_ngram_size=100`, `skip_special_tokens=False`, but **no temperature** (`MA:404-423`). The server falls back to generation_config T=0.01 | gap (low; top_k=1 makes it greedy in practice. The comment `MA:403` says the values were "verified on MinerUSamplingParams" but leaves out temperature) | mineru_client.py L36-46; http_client.py L328-348; https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B/raw/main/generation_config.json |
| max tokens | `max_new_tokens=None` | direct table 2048 (`MA:422`) | deviation-documented (`MA:413-421`, 8192 context) | mineru_client.py L46 |
| Layout image | `layout_image_size=(1036,1036)` exact resize (BICUBIC); `min_image_edge=28`; `max_image_edge_ratio=50` | Same defaults (`models/mineru2_5.py:97-102`, `MA:288`) | none | mineru_client.py L280-295, L567-569 |
| Crop preprocessing (direct path) | Crops go through `resize_by_need` (edge-ratio pad to ≤50, upscale to min edge 28) | `native_image_resolution=True` sends crops untouched (`MA:298`), with no edge-ratio or min-edge fix | gap (low, direct path only) | mineru_client.py L278-290 |
| Preprocessor | min_pixels 50176, max_pixels 1605632 | not overridden (`MA:292-293` cites 1605632) | none | https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B/raw/main/preprocessor_config.json |
| Output / parser | `_layout_re`. `_convert_bbox` **swaps** inverted x/y pairs and drops only out-of-range or zero-area boxes. Blocks come out normalized 0-1 | Native: `*1000` (`MA:441`), correct. Direct: `parse_layout` **discards** boxes with x1>x2 or y1>y2 (`MA:366-369`, comment "Come il client ufficiale") | gap (low, direct path only; the comment is wrong) | mineru_client.py L151-160; mineru_vl_utils/structs.py L148-152 |
| Serving | `vllm serve <model> --logits-processors mineru_vl_utils:MinerULogitsProcessor` | Same (`models/mineru2_5.py:60-62`, `MA:456-461`) | none | https://raw.githubusercontent.com/opendatalab/mineru-vl-utils/main/README.md L119-125 |
| Versions | mineru-vl-utils latest **2.0.8** (2026-10-29; vllm `>=0.19.1,<0.31.0`). 2.0.5 declares `<0.29.0` | `mineru-vl-utils[vllm]==2.0.5`, vLLM 0.21.0 (`models/mineru2_5.py:57-59`). Within range | none (newer client available). The comment `mineru2_5.py:53` calls mineru-vl-utils "MinerU 2.0.x"; the MinerU package itself is 4.1.5 | https://pypi.org/pypi/mineru-vl-utils/json |
| max_model_len | config.json `text_config.max_position_embeddings 8192` (top level 32768) | 8192 (`MA:322`) | deviation-documented (`MA:319-321`) | https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B/raw/main/config.json |

### PaddleOCR-VL (`PaddlePaddle/PaddleOCR-VL-1.6`)

Default path: the official `PaddleOCRVL` pipeline (`backend/app/services/paddle_official.py:23-43`). Direct path: `recognize()` / `table_grid()` with adapter prompts.

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Prompts | `OCR:`, `Table Recognition:`, `Formula Recognition:`, `Chart Recognition:`, `Spotting:`, `Seal Recognition:` | First 3 (`MA:1248-1252`) | none (chart/spotting/seal not exposed) | https://raw.githubusercontent.com/vllm-project/recipes/main/models/PaddlePaddle/PaddleOCR-VL-1.6.yaml ; HF card |
| **Sampling (direct path)** | Recipe client: `temperature=0.0`. PaddleX vllm-server client: `"temperature": 0 if temperature is None`, `max_completion_tokens` default `PADDLEOCR_VL_MAX_NEW_TOKENS=8192` | `sampling_for` returns `{}` for text and `{max_tokens:4096}` for table (`MA:1262-1268`); no `recommended_generation`. **No temperature is sent**, and generation_config.json has none, so vLLM uses **T=1.0** | **gap (high for direct path)** | PaddleX `paddlex/inference/models/doc_vlm/predictor.py` L496-516 (develop); constants.py L35; https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/raw/main/generation_config.json |
| max tokens (direct) | 8192 | text 3072 (`INF:1328`), table 4096 (`MA:1267`) | deviation-documented for table (`MA:1263-1266`, 12288 context); text undocumented | constants.py |
| **Pipeline version** | Recipe: "1.6 requires `paddleocr[doc-parser]>=3.6.0` and `pipeline_version="v1.6"`" (default is v1.6 only in ≥3.6; latest paddleocr 3.7.0) | `PaddleOCRVL(...)` built **without** `pipeline_version` (`paddle_official.py:34-40`). Runtime installs **unpinned** `paddleocr[doc-parser]` (`paddle_runtime.py:219`) and treats any importable `PaddleOCRVL` as ready (`paddle_runtime.py:129`), so an older pre-existing runtime silently runs the v1/v1.5 pipeline against the 1.6 server | **gap (medium)** | recipe yaml "Offline Inference" section; https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/paddleocr/_pipelines/paddleocr_vl.py L25-26 |
| Model name | `vl_rec_api_model_name` must match the served name | passes `self.model` = `PaddleOCR-VL-1.6` (`paddle_official.py:37`) | none | recipe yaml |
| Image | preprocessor min_pixels 112896, max_pixels 1003520, patch 14, merge 2. The card uses 1280·28·28 (spotting 2048·28·28). The PaddleX client forwards min/max via `mm_processor_kwargs` | Not set by default (`models/paddleocr_vl.py:111-112`, user-settable); forwarded as pipeline options for vllm-server only (`paddle_official.py:252-260`) | none | https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/raw/main/preprocessor_config.json ; predictor.py L536-559 |
| Serving | `vllm serve PaddlePaddle/PaddleOCR-VL-1.6 --trust-remote-code --max-num-batched-tokens 16384 --no-enable-prefix-caching --mm-processor-cache-gb 0`; min vLLM 0.11.1 | Identical `serve_args` (`models/paddleocr_vl.py:59-64`), vLLM 0.28.0. Local adds `--max-model-len 12288 --gpu-memory-utilization 0.85` (`MA:1270-1281`) | none / deviation-documented (`MA:1200-1207`) | recipe yaml |
| Output | Pipeline JSON (`save_to_json`) with pixel boxes | Converted to 0-1000 (`INF:718-726`) | none | — |
| Training | PaddleFormers, `paddleocr_vl` template; prompts OCR:/Table/Formula | matches (`models/paddleocr_vl.py:67-107`) | not re-verified (out of scope) | — |

### GLM-OCR (`zai-org/GLM-OCR`)

Default path: the official `glmocr` SDK in self-hosted mode (`scripts/cloud/glmocr_native_gateway.py`). Direct path: `recognize()` with adapter prompts.

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| **Prompts (direct path)** | "Prompt Limited": only `Text Recognition:`, `Formula Recognition:`, `Table Recognition:` (plus JSON-schema information extraction). No layout prompt exists | Invented prompts: layout "Return the document layout as a JSON array…", text "Transcribe all text in this image exactly…", table "Extract the table … in OTSL format…" (`MA:987-991`). Training uses the official ones (`models/types.py:134-138`), so inference and training disagree | **gap (high for direct path)** | https://huggingface.co/zai-org/GLM-OCR/raw/main/README.md "Prompt Limited"; glmocr/config.yaml `task_prompt_mapping` |
| Sampling | SDK `page_loader`: max_tokens 8192, temperature 0.0, top_p 0.00001, top_k 1, repetition_penalty 1.1. Recipe: temperature 0.0 | `recommended_generation` identical (`MA:954-960`), sent to the SDK (`INF:568-578`) and to the direct path (`MA:999-1000`) | none | https://raw.githubusercontent.com/zai-org/GLM-OCR/main/glmocr/config.yaml |
| Image | SDK min_pixels 12544, max_pixels 71372800. Preprocessor shortest_edge 12544, longest_edge 9633792, patch 14, merge 2 | max_pixels 71_372_800 (`models/glm_ocr.py:95`), min None (the SDK default applies) | none | config.yaml; https://huggingface.co/zai-org/GLM-OCR/raw/main/preprocessor_config.json |
| **Native output parsing** | `json_result` regions: `label` is mapped to {text, table, formula, image}; the original is kept in `native_label` (doc_title, paragraph_title, …). `bbox_2d` is 0-1000. Titles get `# `/`## ` prefixes. header/footer/number/footnote/aside_text/reference are *abandoned* by default | `parse_native_result` reads only `region["label"]` (`MA:1023`), so doc_title/paragraph_title arrive as "text" and become **Text, never Title**. `GLM_LABELS` keys `title`, `header`, `footer`, `page_number`, `seal`, `figure`, `reference` never occur (`models/glm_ocr.py:16-22`) | **gap (medium)** | https://raw.githubusercontent.com/zai-org/GLM-OCR/main/glmocr/postprocess/result_formatter.py L176-179, L287-375; layout/layout_detector.py L390-410 |
| Table format | SDK formatter special-cases `<table…</table>`, i.e. HTML | `table_format="markdown"` (`MA:966`) | unverified (HTML is strongly implied but not stated in the docs) | result_formatter.py L292-296 |
| **Serving: MTP** | Vendor README (last edited 2026-04-14): `vllm serve zai-org/GLM-OCR --port 8080 --speculative-config '{"method":"mtp","num_speculative_tokens":3}' --served-model-name glm-ocr`. The vLLM recipe (updated 2026-04-17) still says 1 | `num_speculative_tokens: 1` (`models/glm_ocr.py:36`, `MA:1038`, workflow default 1 `models/glm_ocr.py:98`) | gap (low; speed only; official sources conflict, vendor README says 3). The spec cites "README GLM-OCR" but uses the recipe value | https://raw.githubusercontent.com/zai-org/GLM-OCR/main/README.md L130 ; https://raw.githubusercontent.com/vllm-project/recipes/main/models/zai-org/GLM-OCR.yaml |
| Versions | vLLM ≥0.19.0 (docker v0.19.0), transformers ≥5.3.0 | vLLM 0.19.0, transformers 5.3.1 (`models/glm_ocr.py:31-32`) | none | README L116-128 |
| max-model-len / gpu util | "Add according to your own machine" | 16384 / 0.85 local (`MA:1039-1040`) | deviation-documented (`MA:941-944`) | README note |

### dots.mocr (`dots-studio/dots.mocr`, formerly `rednote-hilab/dots.mocr`)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Repo | `rednote-hilab/dots.mocr` returns 307 to `dots-studio/dots.mocr` | `dots-studio/dots.mocr` (`models/dots_ocr.py:19`) | none | https://huggingface.co/api/models/rednote-hilab/dots.mocr |
| **Prompt `prompt_layout_all_en`** | Exact string: `"...Formatting Rules:\n    - Picture: …\n    - Formula: …\n    - Table: …\n    - All Others …\n\n4. Constraints:\n    - The output text …\n    - All layout elements …\n\n5. Final Output: The entire output must be a single JSON object.\n"` (4-space-indented bullets, single newlines, trailing `\n`) | `"...Formatting Rules:\n\n- Picture: …\n\n- Formula: …"` (blank line between bullets, no indent, no trailing `\n`) (`MA:887-903`). Not byte-identical | **gap (medium)**: the model is prompted out of distribution | https://raw.githubusercontent.com/rednote-hilab/dots.mocr/master/dots_mocr/utils/prompts.py ; HF card L640-648 |
| Image prefix | `<|img|><|imgpad|><|endofimg|>` + prompt in the text part | same (`MA:855`) | none | dots_mocr/model/inference.py |
| Sampling | parser: temperature 0.1, top_p 1.0, max_completion_tokens 32768 (inference.py default top_p 0.9 is overridden by the parser) | 0.1 / 1.0 / 32768 (`MA:857-861`) | none | https://raw.githubusercontent.com/rednote-hilab/dots.mocr/master/dots_mocr/parser.py L28-30 |
| Image min/max | MIN_PIXELS 3136, MAX_PIXELS 11289600, IMAGE_FACTOR 28. The client runs `fetch_image` (smart_resize to multiples of 28) before sending | cap 11_289_600 via `_fit_pixels` (`models/dots_ocr.py:77`), **without** 28-multiple snapping | see next row | dots_mocr/utils/consts.py; image_utils.py L84-124 |
| **Bbox post-processing** | `post_process_cells`: model coordinates are in the server-side smart_resize space ("server input width, also has smart_resize in server"), rescaled with `smart_resize(input_h, input_w)` | Divides by `prepared.width/height` (`INF:1285-1291`), not by the smart-resized size. Systematic scale error of up to ~14 px per axis | gap (low-medium) | https://raw.githubusercontent.com/rednote-hilab/dots.mocr/master/dots_mocr/utils/layout_utils.py L147-177 |
| Serving | `vllm serve rednote-hilab/dots.mocr --tensor-parallel-size 1 --gpu-memory-utilization 0.9 --chat-template-content-format string --served-model-name model --trust-remote-code`; vLLM ≥0.11.0 | Same flags (`models/dots_ocr.py:23-27`, `MA:914-923`); vLLM 0.28.0 | none | https://raw.githubusercontent.com/rednote-hilab/dots.mocr/master/README.md L571-580 |
| Request style | non-streaming OpenAI | `stream_response=False` (`MA:856`) | none | inference.py |
| Output | JSON list `{bbox, category, text}`; Table=HTML, Formula=LaTeX | parsed as `label/category`, `content/text` (`INF:1276-1299`) | none | prompts.py |
| Newer version | dots.mocr (2026-07-04) is current; `dots.mocr-svg` is a sibling variant | — | none | HF API |


### deepseek-ocr (deepseek-ai/DeepSeek-OCR-2)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| vLLM version | Recipe `min_vllm_version: "0.16.0"`. `deepseek_ocr2.py` was added to vLLM by PR #33165 (2026-02-02). The raw file returns 404 at tags v0.12.0 and v0.15.0 and 200 at v0.16.0. | `vllm_version="0.12.0"` (`models/deepseek_ocr.py:32`). The docstring says "richiede vLLM >= 0.12.0" (`services/model_adapters.py:1052-1053`). `cloud_manager` installs exactly this pin (`services/cloud_manager.py:1509-1512`). | **gap (blocking)**: vLLM 0.12.0 has no DeepseekOCR2 architecture | https://github.com/vllm-project/recipes/blob/main/models/deepseek-ai/DeepSeek-OCR-2.yaml ; https://raw.githubusercontent.com/vllm-project/vllm/v0.16.0/vllm/model_executor/models/deepseek_ocr2.py |
| Serve flags | `--trust-remote-code --logits_processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor --no-enable-prefix-caching --mm-processor-cache-gb 0` | Same flags (`models/deepseek_ocr.py:33-38`; local `services/model_adapters.py:1170-1181`). Local adds `--max-model-len 8192 --gpu-memory-utilization 0.85`. | none (8192 = recipe `context_length`) | recipe above |
| Prompt | Recipe and README: `<image>\n<\|grounding\|>Convert the document to markdown.`; alternative `<image>\nFree OCR.`. In OpenAI chat the recipe sends an image_url part plus plain text, with no literal `<image>`. | `"<\|grounding\|>Convert the document to markdown."` with the image as an image_url part (`services/model_adapters.py:1097`) | none | https://raw.githubusercontent.com/deepseek-ai/DeepSeek-OCR-2/main/README.md |
| Temperature / skip_special_tokens | `temperature=0.0`, `skip_special_tokens=False` | `services/model_adapters.py:1141-1143`, `1159` | none | recipe; vendor `run_dpsk_ocr2_image.py` |
| N-gram processor args | vLLM recipe: `ngram_size=30, window_size=90, whitelist_token_ids=[128821,128822]`. The vendor's own scripts differ: image 20/90, pdf 20/50, eval 40/90. | 30/90/whitelist (`services/model_adapters.py:1144-1148`, `1160-1164`; defaults `models/deepseek_ocr.py:82-85`) | none (follows the vLLM recipe for the vLLM processor) | recipe; https://raw.githubusercontent.com/deepseek-ai/DeepSeek-OCR-2/main/DeepSeek-OCR2-master/DeepSeek-OCR2-vllm/run_dpsk_ocr2_pdf.py |
| max_tokens | `max_tokens=8192` in every vendor script and in the recipe's offline example, with `max_model_len=8192`. The recipe's online "Free OCR." example uses 2048. | `end2end_max_tokens = 4096` (`services/model_adapters.py:1067`). No comment explains it. | **gap**: dense pages truncated at 4096 (the official end2end budget is 8192, bounded by the 8192 context) | vendor `config.py`/`run_dpsk_ocr2_*.py`; recipe |
| Image preprocessing | `BASE_SIZE=1024, IMAGE_SIZE=768, CROP_MODE=True, MIN_CROPS=2, MAX_CROPS=6`, i.e. "(0-6)×768² + 1×1024²" (vLLM hardcodes the same constants) | Not exposed; no client resize by default (`services/inference.py:239-264`) | none | https://raw.githubusercontent.com/deepseek-ai/DeepSeek-OCR-2/main/DeepSeek-OCR2-master/DeepSeek-OCR2-vllm/config.py |
| Output / bbox scale | `<\|ref\|>label<\|/ref\|><\|det\|>[[x1,y1,x2,y2], …]<\|/det\|>`. The vendor parser `eval()`s a **list of boxes** and scales by **/999**. | The regex accepts exactly one `[[a,b,c,d]]` (`services/model_adapters.py:1117-1123`), so a ref carrying several boxes is dropped. Boxes are checked against 0-1000 (`:1128`). `coordinate_system="unverified"` (`:1072`). | **gap (low)**: multi-box refs are lost. The 0-999 scale is ~0.1 % off and immaterial. | vendor `run_dpsk_ocr2_image.py` (`re_match`, `draw_bounding_boxes`) |
| Newer version | None: DeepSeek-OCR-2 is the latest deepseek-ai OCR repo on HF (HEAD aaa02f3, 2026-02-03) | — | none | https://huggingface.co/api/models?author=deepseek-ai&search=OCR |

### unlimited-ocr (baidu/Unlimited-OCR, v1)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Temperature | `temperature=0.0` (recipe client, vLLM module docstring, SGLang client `temperature: 0`). `generation_config.json` has **no temperature** (only `no_repeat_ngram_size 35`, window 128, `max_new_tokens 32768`), so vLLM falls back to its default of 1.0. | **Not sent.** `UnlimitedOcrAdapter` has neither `sampling_for` nor `recommended_generation` (`services/model_adapters.py:464-658`). The comment at `:575` says "Vedi `recommended_generation` sotto", but no such attribute exists. | **gap (high)**: the model decodes at temperature 1.0 | https://raw.githubusercontent.com/vllm-project/recipes/main/models/baidu/Unlimited-OCR.yaml ; https://huggingface.co/baidu/Unlimited-OCR/raw/main/generation_config.json ; https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/unlimited_ocr.py |
| Prompt / order | `<image>document parsing.`, text part **before** the image_url part | `"<image>document parsing."` (`services/model_adapters.py:604`), `multimodal_content_order="text-image"` (`:526`) | none | recipe |
| skip_special_tokens / n-gram | `skip_special_tokens=False`; `vllm_xargs {ngram_size:35, window_size:128}` (1024 for multi-page) | `services/model_adapters.py:615-621`; defaults `models/unlimited_ocr.py:98-101` | none | recipe |
| max_tokens | 8192 (recipe client and vLLM docstring) | `end2end_max_tokens = 8192` (`services/model_adapters.py:527`) | none | recipe |
| gundam/base selection | vLLM: crop mode comes from the merged mm kwargs `crop_mode` (`mm_kwargs.get("crop_mode", CROP_MODE)`), and multi-image falls back to base. `images_config: {"image_mode": …}` is the **SGLang** request field from the HF README's SGLang section. | Sends `"images_config": {"image_mode": …}` to the **vLLM** server (`services/model_adapters.py:617`). vLLM does not read `images_config`, so the "base" setting (`models/unlimited_ocr.py:102-105`) is a silent no-op and every page runs in gundam. | **gap (medium)**: the user setting has no effect on vLLM | vLLM `unlimited_ocr.py` lines ~185-235; https://huggingface.co/baidu/Unlimited-OCR/raw/main/README.md (SGLang section) |
| Gundam/base geometry | gundam: base 1024 / image 640 / crop; base: 1024 / 1024 / no crop; up to 32 crops in vLLM | Not exposed; no client resize by default | none | HF README; vLLM module |
| Serving runtime / version | Recipe `min_vllm_version: "0.25.0"`; "supported since vLLM v0.25.0" (pip). The `vllm/vllm-openai:unlimited-ocr` image is still listed. | Docker image only (`models/unlimited_ocr.py:44-47`, `services/model_adapters.py:624-639`). The comment says the architecture "non è nella wheel pip stabile". | deviation-documented, but the rationale is stale (the pip wheel ≥0.25.0 now works) | recipe yaml `install.pip.note` |
| Serve flags | `--trust-remote-code --logits_processors …unlimited_ocr:NGramPerReqLogitsProcessor --no-enable-prefix-caching --mm-processor-cache-gb 0`; context 32768 | Same, plus `--max-model-len 32768` (cloud, `models/unlimited_ocr.py:48-54`) or 12288 (local, `services/model_adapters.py:549-559`, documented 8 GB tuning) | deviation-documented (local) | recipe |
| Output / parser | `<\|det\|>type [bbox]<\|/det\|>content` per line (vendor `DET_RE`); tables in HTML; 0-1000 | Regex `services/model_adapters.py:642-657`, `coordinate_system="normalized-0-1000"` | none (the coordinate scale was not stated explicitly by the vendor for v1; 0-1000 is consistent with the v2 `_scale` clamp) | HF README OmniDocBench post-processing |
| MLX path | Vendor publishes no MLX recipe | `repetition_penalty 1.1 / context 128` instead of the n-gram ban (`services/model_adapters.py:561-599`) | deviation-documented | — |
| official_doc link | github.com/baidu/Unlimited-OCR `main` is now **v2**; v1 moved to branch `UnlimitedOCR1.0` | `models/unlimited_ocr.py:63` points to the repo root | gap (cosmetic) | https://huggingface.co/baidu/Unlimited-OCR-2/raw/main/README.md (release list) |

### unlimited-ocr-2 (baidu/Unlimited-OCR-2)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Runtime | SGLang vendor wheel `sglang-0.5.20.dev13+g75b9b06b02`, Python 3.10, `fastokens==0.3.2`. vLLM support is still an open PR. | `models/unlimited_ocr2.py:16-19`, `49-53` | none | https://huggingface.co/baidu/Unlimited-OCR-2/raw/main/README.md |
| Revision | HF HEAD `0480966` (2026-11-02). The diff against pinned `c14071b` is README-only (arXiv link and citation). The wheel's x-linked-etag is identical (`576c71da…`). `generation_config.json` is unchanged. | `_REVISION = c14071b…` (`models/unlimited_ocr2.py:14`) | none | HF API + raw diff |
| Serve flags + env | `--chat-template unlimited-ocr --tokenizer-backend fastokens --tokenizer-worker-num 8 --mem-fraction-static 0.7 --max-running-requests 64 --max-prefill-tokens 2048 --enable-custom-logit-processor --enable-streaming-session --allow-auto-truncate --disable-radix-cache --mm-preprocess-cache-size-mb 0 --cuda-graph-*` and the nine env vars | Identical (`models/unlimited_ocr2.py:56-84`) | none | HF README SGLang section |
| Prompt / order | `"<PAGE><image>" * n + "Multi page parsing."`, images **before** text | `"<PAGE><image>Multi page parsing."` (`services/model_adapters.py:753`), default image-text order | none | HF README |
| Sampling / per-request | `temperature 0`, `skip_special_tokens False`, `images_config {"crop_mode": bool}`, `DeepseekOCRNoRepeatNGramLogitProcessor`, `custom_params {ngram 35, window 128 (1024 multi), page_token_id 128827, eos 1, max_page_tokens 20000}`, `max_tokens = 8000 × pages` | `services/model_adapters.py:695-696`, `758-775` | none | HF README; https://raw.githubusercontent.com/baidu/Unlimited-OCR/main/infer.py |
| Image modes | gundam (crop, tiles 640, base 1024, `dynamic_preprocess` max 16) by default for a single page; base for multi-page | `image_mode` default gundam → `crop_mode` (`models/unlimited_ocr2.py:132-135`) | none | `modeling_unlimitedocr.py` (`_uocr_build_inputs`, `infer`) |
| Output / parser | `<PAGE><BLOCK>label<LEVELn>[x1,y1,x2,y2]<FORK>content`, clamp 0-1000 (`_scale`), `cross_`/`append_`, `<MERGE>`, 17 base labels | Port of `BLOCK_RE`, `_fix_bare_blocks`, `_scale`, `_fix_span_attrs`, labels (`services/model_adapters.py:719-828`, `models/unlimited_ocr2.py:23-41`) | none (title levels and cross-page merges are not rendered: by design, per-page blocks) | https://raw.githubusercontent.com/baidu/Unlimited-OCR/main/utils/process_unlimitedOCR2.py |

### qwen3-vl-8b (Qwen/Qwen3-VL-8B-Instruct)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Sampling | Card VL preset: `top_p 0.8, top_k 20, temperature 0.7, repetition_penalty 1.0, presence_penalty 1.5, out_seq_length 16384`; `generation_config.json` matches (no presence penalty there) | `recommended_generation` (`services/model_adapters.py:1356-1362`); `max_tokens=self._output_budget(16384)` (`services/inference.py:537`) | none | https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/raw/main/README.md ; …/generation_config.json |
| Document-parsing prompt | Cookbook `document_parsing.ipynb`: `"qwenvl html"` (also `"qwenvl markdown"`) | `"qwenvl html"` (`services/model_adapters.py:1406`, `services/inference.py:535`) | none | https://raw.githubusercontent.com/QwenLM/Qwen3-VL/main/cookbooks/document_parsing.ipynb |
| min/max pixels (HTML parsing) | Cookbook: `min_pixels=512*32*32` (524,288), `max_pixels=2048*32*32` (2,097,152) for `qwenvl html`; 4608*32*32 for markdown. Checkpoint processor: `shortest_edge 65536`, `longest_edge 16777216`, patch 16, merge 2. | Recommended `{"min_pixels": 512*32*32, "max_pixels": 2048*32*32}` (`models/qwen3_vl_8b.py:90`). max is applied by default (`services/inference.py:254-257`), but **min_pixels is applied only when the user overrides it** (`services/inference.py:258-261`). | **gap (medium-low)**: pages below 0.52 MP are not upscaled as the cookbook does | cookbook; https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct/raw/main/preprocessor_config.json |
| Output / bbox | QwenVL HTML with `data-bbox="x1 y1 x2 y2"`, relative 0-1000; the cookbook skips `<ol>` and keeps its `<li>` | `parse_native_result` (`services/model_adapters.py:1434-1481`), 0-1000 | none | cookbook `draw_bbox_html` |
| Serve | vLLM ≥ 0.11.0, no trust-remote-code; recipe tips `--limit-mm-per-prompt.video 0`, `--max-model-len` as needed, optional `--mm-processor-cache-gb 0` / `--async-scheduling` | `--dtype bfloat16 --max-model-len 32768 --limit-mm-per-prompt {"image":4,"video":0}`, vLLM 0.28.0 (`models/qwen3_vl_8b.py:29-34`) | none | https://raw.githubusercontent.com/vllm-project/recipes/main/Qwen/Qwen3-VL.md |
| MLX | No vendor MLX recipe | Wider repetition/presence windows (`services/model_adapters.py:1385-1396`) | deviation-documented | — |
| Newer version | No newer Qwen3-VL-8B-Instruct checkpoint (HEAD 0c351dd, 2025-10-15). Qwen3.5 VLMs exist under a different name; not evaluated. | — | none | HF API |

### lightonocr-3 (lightonai/LightOnOCR-3-1B)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| vLLM version + transformers | The vendor serves the 1B (Mistral3/Pixtral arch) from `ocrbench/vllm-openai:v0.30.0-transformers5.16.1`. Its Dockerfile says: "vLLM v0.30.0's pixtral.py imports PixtralRotaryEmbedding, which transformers 5.17 … renamed: pin 5.16.1". Also: "v0.27.1's compiled mode makes the model loop: use v0.30.0 or later". PyPI: vllm 0.30.0 requires `transformers>=5.10.4` (no upper bound); latest transformers is 5.22.1. | `vllm_version="0.30.0"`, **no `transformers_version`** (`models/lightonocr3.py:22`). A fresh env would resolve transformers ≥5.17 (resolver behaviour inferred from PyPI metadata, not executed). | **gap (high)**: the server is likely to fail on import | https://raw.githubusercontent.com/lightonai/LightOnOCR/main/benchmarks/docker/lightonocr-1b.Dockerfile ; https://raw.githubusercontent.com/lightonai/LightOnOCR/main/benchmarks/ocr_bench/config.py |
| Serve flags | Card: `--limit-mm-per-prompt '{"image": 1}' --mm-processor-cache-gb 0 --no-enable-prefix-caching`. Vendor bench adds `--max-model-len 16384`. | Same three flags (`models/lightonocr3.py:23-27`, `services/model_adapters.py:1762-1770`). No `--max-model-len`, so the default 16384 from `max_position_embeddings` applies. | none | https://huggingface.co/lightonai/LightOnOCR-3-1B/raw/main/README.md |
| Prompt | `""` (transcription) or `"grounding"`, text after the image; "Other instructions are out of distribution" | `"grounding"` after the image (`services/model_adapters.py:1737-1742`) | none | card |
| Sampling | `temperature 0.2, top_p 0.9` (card and generation_config; `top_k 0`); vendor client `models.py` profile for 3-1B: top_p 0.9 | `recommended_generation = {"temperature": 0.2, "top_p": 0.9}` (`services/model_adapters.py:1709`) | none | card; generation_config.json; https://raw.githubusercontent.com/lightonai/LightOnOCR/main/lightonocr/models.py |
| max_tokens | Vendor pipeline: 12288 output tokens with a 16384 context (card example: 4096) | `end2end_max_tokens = 12_288` (`services/model_adapters.py:1708`) | none | vendor `ocr_bench/config.py` comment |
| Image resolution | Processor `PixtralImageProcessorFast`, `longest_edge 1540`, patch 14, merge 2. The vendor client downscales to 1540 with LANCZOS and never upscales. | No client resize; the server processor downscales (bicubic, `resample: 3`) | deviation-documented (`services/model_adapters.py:1692-1693`); only the resampling filter differs | https://huggingface.co/lightonai/LightOnOCR-3-1B/raw/main/processor_config.json ; https://raw.githubusercontent.com/lightonai/LightOnOCR/main/lightonocr/render.py |
| Output / parser | `![label](x1,y1,x2,y2)` with an optional `+`, 0-1000. The vendor `MARKER` accepts any label `[A-Za-z_][\w-]*` **anywhere** (not only at line start). The vendor `LABELS` list also includes `section`. | Regex `^!\[([a-z_]+)\+?\]…` with MULTILINE (`services/model_adapters.py:1731-1735`): line-start only, lowercase only. `section` is not in the label map (`models/lightonocr3.py:52-70`), so it becomes Text. | gap (low) | https://raw.githubusercontent.com/lightonai/LightOnOCR/main/lightonocr/grounding.py |
| Revision | HF HEAD `23d5440` (2026-10-09); no newer 1B. 0.8B/4B (Qwen3.5) are excluded on purpose. | `services/model_adapters.py:1696-1698` | none | HF API |


### TeleOCR (`teleocr`)

| Aspect | Official value | Tabularium now (file:line) | Gap | Source URL |
|---|---|---|---|---|
| Pinned stack | vllm==0.11.0, transformers==4.57.1, torch 2.8; `pip install -e .` registers the `TeleOCR_vllm` plugin | vllm 0.11.0, transformers 4.57.1, `git+...TeleOCR.git@main` (models/teleocr.py:45-54) | none (`@main` is not pinned to a commit, which is a reproducibility risk) | https://raw.githubusercontent.com/caipeng328/TeleOCR/main/requirements.txt, /pyproject.toml |
| Engine construction (vllm-async-engine) | `TeleOCR_model.py:93-106`: `AsyncEngineArgs(model, gpu_memory_utilization=0.95, max_model_len=16384, logits_processors=[TeleOCRLogitsProcessor])` | Native gateway: `AsyncEngineArgs(model_path, dtype, gpu_memory_utilization, max_model_len[, max_num_seqs...])` with **no `logits_processors`** (scripts/cloud/teleocr_native_gateway.py:38-50) | **gap**: the per-request `extra_args.no_repeat_ngram_size=100` has no processor to read it, so the official n-gram guard is off on Vast | https://raw.githubusercontent.com/caipeng328/TeleOCR/main/TeleOCR/vlm_utils/TeleOCR_model.py |
| Pipeline | `TeleOCRClient.aio_batch_two_step_extract` (layout, crop, OCR, post-process) | Same, via the sidecar (gateway:130) | none | TeleOCR_client.py |
| Layout modes / prompts | `LAYOUT_PROMPTS = {"Segmentation": "\nMulti-point Layout Segmentation Analysis.", "Detection": "\nAnalyze the image layout."}` | Same text **without the leading `\n`** (model_adapters.py:1543-1567) | gap (low; only on the OpenAI/Modal crop path) | https://raw.githubusercontent.com/caipeng328/TeleOCR/main/TeleOCR/vlm_utils/TeleOCR_client.py (l.39-55) |
| Task prompts | `"\nPlease output the text content from the image."`, `"\nThis is the image of a table. Please output the table in OTSL format."`, `"\nPlease write out the expression of the formula..."` | Same text without the leading `\n` (model_adapters.py:1543-1547) | gap (low) | same |
| System prompt | `DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."` | same (model_adapters.py:1549) | none | vlm_client/base_client.py:7 |
| Message order | image first, then text (default) | image-text (domain/inference.py:280-290) | none | vllm_async_engine_client.py:70-92 |
| Layout image | `resize((1036,1036), BICUBIC)` | same (inference.py:1131-1141) | none | TeleOCR_client.py:171-173 |
| Sampling | temp 0, top_p 0.01, top_k 1, rep 1.0, no_repeat_ngram 100; text/equation presence 1.0 / freq 0.05; table presence 1.0 / freq 0.005; layout all 0 | identical (model_adapters.py:1640-1660) | none | TeleOCR_client.py:16-67 |
| How `no_repeat_ngram_size` reaches vLLM | Engine path: `SamplingParams(extra_args={...})`. OpenAI server path: vLLM reads custom args only from **`vllm_xargs`**; unknown top-level fields are logged as "present in the request but ignored" | OpenAI path sends top-level **`extra_args`** for every adapter except MinerU (domain/inference.py:325-335) | **gap**: on the Modal OpenAI server (modal_teleocr.py, where the processor is registered) the n-gram size is dropped | vLLM v0.11.0 `entrypoints/openai/protocol.py` l.66-97, 646-650, 753; v0.25.1 `chat_completion/protocol.py` l.442 |
| `max_tokens` | `None`, so the full `model_max_length` | 2048 text, 4096 table (model_adapters.py:1661-1663) | deviation-documented ("Keep the crop request budget conservative", model_adapters.py:1661-1662) | vllm_async_engine_client.py:105-106 |
| `skip_special_tokens` | `False` on every request | not sent on the OpenAI path (default True) | unverified impact: layout tags `<box:…><label:…>` are plain text, not special tokens; not checked against the tokenizer | vllm_async_engine_client.py:115-118 |
| Local vLLM serve command | Plugin plus logits processor required | `serve_command` passes `--trust-remote-code ... --max-num-seqs 4` but **no `--logits-processors`** and no plugin install (model_adapters.py:1665-1675). `--max-num-seqs 4` is not upstream | gap (local runtime) | TeleOCR_model.py |
| Modal serve | — | `--logits-processors TeleOCR...:VllmV1NoRepeatNGramLogitsProcessor`, python 3.11 (official is 3.10) (scripts/cloud/modal_teleocr.py:87-98) | none for flags; see the `vllm_xargs` gap | README |
| `MAX_PIXELS` | 8000×8000 = 64,000,000 (PDF rasterisation cap) | 64,000,000 (models/teleocr.py:81) | none | TeleOCR/config.py |
| Output parser | `^<box:([\d\s]+)><label:(\w+)><([^>]+)>$` | same regex (model_adapters.py:1577) | none | TeleOCR_client.py:178-181 |

### Xiaomi-OCR-0 (`xiaomi-ocr-0`)

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| HF repo | Docs now say `SeerRay-Lab/Xiaomi-OCR-0`; the HF API redirects it to `xiaomi-research/Xiaomi-OCR-0` | `xiaomi-research/Xiaomi-OCR-0` (models/xiaomi_ocr.py:137) | none (the redirect resolves) | INSTALL.md l.4; https://huggingface.co/api/models/SeerRay-Lab/Xiaomi-OCR-0 |
| Serve | `vllm serve ... --trust-remote-code --max-model-len 16384` (INSTALL.md l.42-44; the README omits `--trust-remote-code`) | same (xiaomi_ocr.py:144; model_adapters.py:1835-1842) | none | https://raw.githubusercontent.com/xiaomi-research/Xiaomi-OCR-0/main/INSTALL.md |
| vLLM version | Not pinned ("supports Qwen3_5ForConditionalGeneration") | 0.25.1 | deviation-documented (xiaomi_ocr.py:140-142) | INSTALL.md |
| Prompts (region) | text `Extract the text in the image.`; table `Parse the table in the image into OTSL.`; formula `Identify the formula in the image and represent it using LATEX format.` | identical (model_adapters.py:1822-1826) | none | skills/xiaomi-ocr/mcp_ocr_server.py l.38-48 |
| Sampling | temp 0, top_p 0.8, presence 0, repetition 1.0, `chat_template_kwargs.enable_thinking=false` | identical (model_adapters.py:1806-1808, 1832-1833) | none | evaluation_pipeline/README.md l.50-61; mcp_ocr_server.py l.96-97 |
| Region max_tokens | eval 16384; MCP per-task caps 256/1024/2048/512 | `_output_budget(16384)`, so min(16384, 16384-2048) = 14336 | deviation-documented (model_adapters.py docstring; inference.py `_output_budget` explains the vLLM 400) | evaluation_pipeline/README.md |
| Page pre-fit | `fit_image`: thumbnail to 2048 px, LANCZOS | same (inference.py:660-663) | none | evaluation_pipeline/cropping.py |
| Crop | `int(b/1000*w)` with clamp; polygon has 4 points in the PaddleX path, so it is a plain rect | same (inference.py:672-676) | none | cropping.py; layout_detect_paddlex.py l.97-134 |
| Layout | PaddleX PP-DocLayoutV3, threshold 0.25 for eval (MCP default 0.3); LABEL_TASK_MAPPING | 0.25; identical mapping (xiaomi_ocr.py:111-121) | none (documented choice) | layout_detect_paddlex.py l.35-46; eval README l.87-94 |
| Concurrency | 8 | 8 (inference.py:686) | none | mcp_ocr_server.py |
| Repetition retry | The eval pipeline reruns repetitive regions with presence_penalty 1.5 (`--retry-repetitive --retry-presence-penalty 1.5`, `postprocess/repetition_guard.py`); the MCP path does not | not implemented | gap (low; eval-only feature) | eval README l.75-77, 110-113; twostage_img2md.py l.649-660 |
| Use region vs page | The README advises **page** mode for historical/irregular layouts | region mode only | deviation-documented (model_adapters.py:1778-1781) | README l.43; INSTALL.md l.125-127 |
| generation_config / preprocessor_config | none published on HF (404) | — | none | HF file list |
| MLX | not vendor-provided | mlx-vlm with the original weights | deviation-documented (model_adapters.py:1787-1790); mlx-vlm accepts `chat_template_kwargs.enable_thinking` (mlx_vlm/server/schemas.py l.345-348) | https://github.com/Blaizzy/mlx-vlm |

### Surya OCR 2.5 (`surya-ocr-2.5`)

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Docker image | `vllm/vllm-openai:v0.25.1` | same (models/surya_ocr.py:214) | none | surya/settings.py l.105 |
| Serve argv | `--no-enforce-eager --max-num-seqs N --dtype bfloat16 --max-model-len 24576 --max-num-batched-tokens M --gpu-memory-utilization 0.85 --enable-prefix-caching --mm-processor-kwargs {"min_pixels":3136,"max_pixels":6291456}` (+ MTP when the checkpoint has a head) | same minus `--max-num-seqs` / `--max-num-batched-tokens` (surya_ocr.py:220-227) | deviation-documented (surya_ocr.py:217-219) | surya/inference/backends/vllm.py l.182-226 |
| MTP speculative | Auto-on only if config has `num_nextn_predict_layers`/`mtp_num_hidden_layers` > 0 | not set | none: surya-ocr-2.5 config has `mtp_num_hidden_layers: 0`, so it is off upstream too | https://huggingface.co/datalab-to/surya-ocr-2.5/resolve/main/config.json |
| Prompt | `HIGH_ACCURACY_BBOX_PROMPT` = "OCR this image to HTML. Each block is a div with data-label and data-bbox (x0 y0 x1 y1, normalized 0-1000)." | identical (model_adapters.py:1987-1991) | none | surya/inference/prompts.py l.82-85 |
| Sampling | temperature 0.0, top_p 0.1; full page max 12288 | same (model_adapters.py:1945-1946) | none | openai_client.py l.199-201; settings.py l.90 |
| Special tokens | Card: "Your server must return special tokens". Block/coord tokens are `special: false` in tokenizer.json, so default decoding keeps them | sends `skip_special_tokens: False` and strips `<|im_end|>`/`<|endoftext|>` (model_adapters.py:1993-1994, 1982) | none (harmless superset) | HF README l.350; tokenizer.json added_tokens |
| Client image scaling | `scale_to_fit`: ≤3072×2048 px area, dims snapped to 28-px grid, **upscales below 1792×28**, LANCZOS, PNG | no client resize; the server caps at max_pixels 6291456 (inference.py:1194-1199 with `max_pixels=None`) | deviation-documented (model_adapters.py docstring "images are capped by the server"); a tiny-image upscale is missing (low) | surya/inference/util.py l.6-31 |
| Output parsing | `compact.decode` + `merged_cells.convert_merged_cells` / `parse_full_page_html` | reimplemented (model_adapters.py:1996-2034) | unverified equivalence (not diff-tested) | surya/inference/compact.py, parsers.py |
| Full-page fallback | full page, then optional regen (off by default), then block-mode fallback | full page only | gap (low) | settings.py `SURYA_FULLPAGE_REGEN` |
| Local runtime | — | `local_runtimes=("vllm",)` but `serve_command` is None (_StubAdapter, model_adapters.py:256-257) | not exposed (local serve fails by design) | — |

### Gemini 4 Argon (`gemini-argon`, hosted)

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Model id | Google: `gemini-4-argon`. OpenCode Go/Zen catalogs list only `gemini-4-argon` (live `GET https://opencode.ai/inference/go/v1/models`, 2026-11-03) | default `gemini-4-argon-contributor` (model_adapters.py:2085; opencode_gateway.py:22-23) | **gap**: the id is absent from the current public catalog and docs; the code says it was verified live in Oct 2026 | https://aistudio.google.com/docs/models/gemini-4-argon ; https://opencode.ai/docs/go/ ; https://opencode.ai/docs/zen/ |
| Endpoint | OpenCode Go `https://opencode.ai/inference/go/google/v1beta/models/gemini-4-argon` (native Google API) | `.../inference/go/google/v1beta` + `/models/{model}:generateContent` (opencode_gateway.py:22; inference.py:1014) | none | opencode.ai/docs/go |
| Headers | Stable `x-opencode-session`; a non-generic User-Agent | both sent (inference.py:1003-1011) | none | opencode.ai/docs/go |
| API surface | generateContent is still supported; the Interactions API has been the default since June 2026 ("legacy") | generateContent | none (informational) | AI Studio docs (search result) |
| Sampling | "Remove temperature, top_p, top_k… ignored"; catalog flags temperature/top_p/top_k false | sends only if user settings set them (inference.py:990-995) | none | https://aistudio.google.com/docs/latest-model |
| thinking_level | low/medium/high, default medium; `thinking_budget` gives HTTP 400 | not sent (default medium) | deviation-documented (gateway returns 500 on `thinkingLevel`, inference.py:1677-1680) | docs.cloud.google.com …/gemini-4-argon; aistudio latest-model |
| maxOutputTokens | Caps **thinking + output**. Keep the default 65,536 or set ≥32,768; non-streaming direct-HTTP default cap 32,768 | end2end 20,480 (inference.py:147); text crops 3,072 and table bands `TABLE_MAX_TOKENS` (inference.py:1328-1330) passed as `maxOutputTokens` (inference.py:990) | **gap**: with medium thinking, small caps can hit MAX_TOKENS before any answer text | https://aistudio.google.com/docs/latest-model ; Vertex guide |
| Client timeout | "Raise client timeouts to at least 15 minutes" | end2end total 300 s, default 180 s (inference.py:1025, 1183) | gap (medium) | aistudio latest-model |
| media_resolution | Image default (unspecified) = 1120 tokens = `high`; `ultra_high` 2240 per part | not set (default high-equivalent) | not exposed (no gap; ultra_high could help dense registers) | https://aistudio.google.com/docs/media-resolution (in llms-full.txt l.93106-93360) |
| Image part | `inline_data {mime_type,data}` | PNG inline_data (inference.py:996-1003) | none | Gemini REST |
| Structured output | `response_mime_type: application/json` + `response_schema` supported | prompt-only JSON | not exposed | Vertex guide |

### LlamaIndex OCRia-1 (`llamaindex-ocria-1`, hosted LlamaParse v2)

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Upload | `POST /api/v1/beta/files`, multipart `purpose=parse`, Bearer | same (inference.py:318-325) | none | https://developers.llamaindex.ai/llamaparse/parse/getting_started/index.md |
| Start | `POST /api/v2/parse` `{file_id, tier, version, ...}` | same (inference.py:343-350) | none | same; reference/resources/parsing/methods/create |
| Poll | `GET /api/v2/parse/{id}?expand=...`; status under `job.status` (`COMPLETED`) | handles `job` nesting and case (inference.py:378-397) | none | getting_started; retrieving-results |
| expand values | `items`, `metadata`, `usage`, `job_metadata` valid | same (inference.py:383) | none | retrieving-results l.22-26 |
| Which tiers run OCRia-1 | "`cost_effective` or `agentic` with `version: latest`"; `agentic_plus` is a different pipeline | allowed tiers include `agentic_plus` (types.py:224); default agentic/latest (types.py:241-242) | gap (low): picking agentic_plus silently leaves OCRia-1 | https://developers.llamaindex.ai/llamaparse/parse/guides/tiers/index.md l.10-21 |
| custom_prompt | "Jobs with a custom prompt use a general-purpose model… To try OCRia-1, leave the custom prompt empty" | exposed, sent as `agentic_options.custom_prompt` (domain/inference.py:264-267) | gap (medium): a non-empty prompt bypasses OCRia-1 with no warning | tiers l.21 |
| cost_optimizer | agentic/agentic_plus only; routes simple pages to `cost_effective` | gated correctly (types.py:347-352; domain/inference.py:220-221) | none | configuring-parse l.384 |
| **OCR languages** | `processing_options.ocr_parameters.languages`: array of **ParsingLanguages** enum, ISO-639-1-style: `en`, `it`, `fr`, `de`, `ch_sim`, `ch_tra`, `rs_latin`… (≈86 values) | Tesseract 3-letter set (`eng`, `ita`, `chi_sim`…; types.py:182-200); **default `["eng"]`** (types.py:244; domain/inference.py:209). Only 3 of 130 codes (`abq`, `ava`, `che`) are in the enum | **gap (high)**. The code comment (types.py:182) claims Tesseract codes are accepted, which contradicts the current reference | https://developers.llamaindex.ai/reference/resources/parsing/methods/create/index.md (languages enum); …/parse/features/ocr-and-languages l.21-27 |
| Tables as HTML | `output_options.markdown.tables.output_tables_as_markdown: false` gives HTML `<table>` | false (domain/inference.py:229-232) | none | configuring-parse l.59 |
| confidence_score_effort | enum only `"high"`; omit for standard | omitted unless high (domain/inference.py:216-217) | none | create ref l.767 |
| Timeouts | `base_in_seconds` max **7200**; `extra_time_per_page_in_seconds` max **300** | UI limits 30–86400 and 0–3600 (types.py:219-220) | gap (low): values above the vendor max pass validation and fail at the API | create ref l.365-376 |
| allowed_page_failure_ratio | default 0.05 | 0.0 (types.py:264) | gap (cosmetic for 1-page jobs) | create ref l.345 |
| watermark_handling | move_to_end (default)/move_to_start/remove; needs version ≥ 2026-09-28 | same enum (types.py:227) | none | create ref l.315-317 |
| Bounding boxes | items `bbox` = array of `{x,y,w,h[,r]}` in **page points**, frame `page_width/page_height` | normalises by `page_width/page_height` with fallback to image px; handles `r` (model_adapters.py:2237-2270) | none (the class docstring says "pixels", which is wrong wording only) | response-format l.136, 245-263 |
| version | `latest` or a dated release; pinning recommended for production | `latest` default | none (choice) | tiers |


## 4. Providers and endpoints

`_PROFILE_PROVIDERS = {"local","ssh","vast","runpod","modal","custom","llamaindex"}` (inference.py:57). Additional transports: OpenCode gateway (Gemini, `serve_backend="opencode-gateway"`), mlx-vlm (local Apple), SGLang venv (Unlimited-OCR-2), native vendor sidecars on Vast (TeleOCR, GLM-OCR, MinerU).

### vLLM OpenAI-compatible server (used by local/ssh/vast/modal/custom)

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Custom per-request args | `vllm_xargs: dict` becomes `SamplingParams.extra_args`; unknown fields are ignored with a warning (`extra="allow"`) | `extra_args` top-level for all adapters except MinerU (domain/inference.py:325-335) | **gap** (affects TeleOCR and any other adapter emitting `no_repeat_ngram_size`) | vLLM v0.11.0 protocol.py l.66-97, 646, 753; v0.25.1 chat_completion/protocol.py l.442 |
| `repetition_penalty`, `top_k`, `skip_special_tokens`, `chat_template_kwargs`, `mm_processor_kwargs`, `stop_token_ids` | all valid request fields | used as top-level keys | none | v0.25.1 chat_completion/protocol.py l.250-347 |
| `max_tokens` | deprecated in favour of `max_completion_tokens` (still accepted) | `max_tokens` (domain/inference.py:316) | gap (low; future breakage) | v0.25.1 chat_completion/protocol.py l.207 |
| `stream_options.include_usage` | valid | sent when streaming | none | — |

### Modal

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Web server | `@modal.web_server(port, startup_timeout=…)` + `@modal.concurrent(max_inputs)`; `scaledown_window`, `min_containers`, `max_containers` | all used (scripts/cloud/modal_*.py e.g. modal_teleocr.py:74-84) | none | modal.com/docs |
| HTTP request limit | Web functions (incl. `web_server`) cap HTTP requests at **150 s**, then return **303** to a result URL | client posts with `allow_redirects=False` (inference.py:848). A non-streamed response (`DotsOcrAdapter.stream_response=False`, model_adapters.py:863) longer than 150 s gets a 303 and fails as "non JSON" | gap (medium for dots on Modal; streaming adapters send headers early, so not affected) | https://modal.com/docs/guide/webhook-timeouts |
| Auth / deploy | `modal token new`, `modal token info`, `modal deploy` | subprocesses (modal_manager.py:295-330) | none | modal docs |

### Vast.ai

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Base / auth | `https://console.vast.ai/api/v0`, `Authorization: Bearer` | same (cloud_manager.py:511, 1005-1009) | none | vast-cli vastai/api/client.py l.19,136-165 |
| Search offers | `PUT /api/v0/search/asks/` body `{"select_cols":["*"],"q":{...,"type","limit","allocated_storage","order"}}`; CLI defaults verified=true, rented=false | `{"q": query}` (cloud_manager.py:589); verified optional | none (the comment that `select_cols:["*"]` is rejected contradicts the CLI; omitting it works) | vastai/api/offers.py l.80-104 |
| Create | `PUT /api/v0/asks/{id}/`, `env` is a **string** in Docker-flag form, `runtype` enum includes `ssh_direct` | same (cloud_manager.py:687-722) | none | https://docs.vast.ai/api-reference/instances/create-instance ; instances.py l.296-305 |
| Port spec `-p N:N/http` | docs only show `-p 8000:8000`; the CLI `parse_env` accepts only chars `0123456789:tcp/udp` | `-p {port}:{port}/http` (cloud_manager.py:694) | unverified (`/http` suffix undocumented) | vastai/utils.py l.148-184 |
| onstart length | "limited to **4048 characters**; gzip+base64 for longer" | Training onstart embeds base64 of setup_train_env.sh: **10,704 chars** of base64 plus wrapper (auto_training.py:101-113, passed at cloud_manager.py:700) | **gap** (training provisioning on Vast) | docs.vast.ai create-instance "Key Quirks" |
| List instances | `GET /api/v1/instances/` with `limit`, `select_filters`, `order_by`, pagination param **`after_token`** (response `next_token`) | sends `next_token` as the request param (cloud_manager.py:780-783) | gap (low): page 2+ repeats page 1 for accounts with more than 25 instances | vastai/api/instances.py l.40-71 |
| Start/stop/destroy | `PUT /instances/{id}/ {"state":"running"/"stopped"}`, `DELETE /instances/{id}/` (v0) | same (cloud_manager.py:807-813) | none | instances.py l.308-338 |
| SSH keys | `POST /ssh/`, `POST /instances/{id}/ssh/` | same (cloud_manager.py:983-1033) | none | vastai/api/keys.py |

### RunPod

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Base / auth | `https://rest.runpod.io/v1`, Bearer | same (cloud_manager.py:2041) | none | https://rest.runpod.io/v1/openapi.json |
| List / start / stop / delete | `GET /pods`, `POST /pods/{id}/start`, `POST /pods/{id}/stop`, `DELETE /pods/{id}` | same (cloud_manager.py:2052, 2140-2148) | none | openapi paths |
| Pod fields | `desiredStatus`, `costPerHr`, `gpu.displayName/count`, `publicIp`, `portMappings` {"22":port} | same | none | openapi `Pod` schema |
| Create | `imageName, gpuTypeIds[enum], volumeInGb, volumeMountPath, ports ["8888/http","22/tcp"], env{}, interruptible`; `supportPublicIp` needed for SSH via portMappings on some clouds | same fields; no `supportPublicIp`/`cloudType` (cloud_manager.py:2110-2119) | none / not exposed | openapi `PodCreateInput` |

### OpenCode gateway (Gemini)
See model table 4. Endpoint, headers and auth are correct; the model id `gemini-4-argon-contributor` is not in the current catalog (gap).

### LlamaCloud (`llamaindex` provider)
See model table 5. Transport is correct; payload has the language-enum gap.

### mlx-vlm (local Apple Silicon)

| Aspect | Official value | Tabularium now | Gap | Source URL |
|---|---|---|---|---|
| Server | `python -m mlx_vlm.server --model --host --port`; flags `--kv-bits --kv-group-size --max-kv-size --vision-cache-size --kv-quant-scheme` exist | same (mlx_runtime.py:188-215) | none | mlx_vlm/server/cli.py (main) |
| Request fields | `repetition_penalty`, `repetition_context_size`, `presence_penalty`, `presence_context_size`, `top_k`, `enable_thinking` / `chat_template_kwargs.enable_thinking` | used (inference.py:1093-1114) | none | mlx_vlm/server/schemas.py l.345-495 |
| Version | latest 0.7.11 on PyPI; Tabularium verified 0.7.1 | unpinned `PACKAGES=["mlx-vlm"]` (mlx_runtime.py:32) | gap (low; reproducibility) | https://pypi.org/pypi/mlx-vlm/json |

### SGLang runtime, local / ssh / custom
`sglang_runtime.py` builds a uv venv from `recipe.pip_extra` for Unlimited-OCR-2 only (outside this group): **unverified**, not audited against SGLang docs. local/ssh/custom are generic OpenAI endpoints, so the vLLM rows apply.
