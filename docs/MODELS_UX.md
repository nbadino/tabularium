# Model parameters — UX spec

Status: implemented (variant B) for the Models hub (`/modelli`), November 2026. Scope: the parameter
panel shown in step 3 ("Configure") of the hub — today `ModelSettingsSection` inside
the "Model parameters" collapsible — and how it relates to `ModelPicker`,
`DestinationPicker`, the provider checklists and the compute profiles. The official
values the panel starts from are audited in [`MODELS_AUDIT.md`](MODELS_AUDIT.md).

## 1. What is wrong today

The panel is powerful and complete: every knob that reaches vLLM, mlx-vlm or a vendor
pipeline can be set per model, validated by the backend and reset. What it lacks is
structure.

1. **It ignores the hub.** The panel has its own model dropdown and opens on the model
   *in use*, not on the model chosen in steps 1–2. Configuring Unlimited-OCR while
   MonkeyOCRv2 is in use shows MonkeyOCRv2's parameters.
2. **Grouped by implementation, not by intent.** Three equal columns — "Remote serving
   (vLLM)", "Generation", "Image" — then "MLX" and "Official workflow". The groups name
   the software layer a value goes to, not the question the user is answering.
3. **Everything at the same level.** Image resolution (changes the result on every page)
   sits next to `max_num_batched_tokens` (a scheduler budget almost nobody should touch).
4. **"Automatic" hides the truth.** Most generation fields show "Automatic — let the
   adapter manage this parameter", but the adapter *does* send a value, per task
   (GLM-OCR: temperature 0, top-k 1, repetition 1.1; MinerU2.5: presence penalty 1.0 on
   text only). The user cannot see what is actually sent.
5. **Changes are invisible.** Nothing marks which values differ from the official
   recipe. Reset exists only for the whole model.
6. **Fields for the wrong destination.** vLLM serving fields show while configuring the
   local MLX path and vice versa; the MLX section shows on Vast.ai.
7. **Hard-coded model ids in the view** (`mineru2.5`, `paddleocr-vl`, `qwen3-vl-8b` decide
   which image fields appear) — a R4 violation: the backend already knows which image
   fields each model honours (`SettingsSpec.image_fields`) but does not send them.
8. **Copy is vendor vocabulary.** `top_p`, `max_num_seqs`, `kv_group_size` as labels,
   with hints like "Recommended: 1003520".

## 2. Inventory

Every parameter the panel can set today, with the intent it serves. "Where" says which
destination the value reaches; the panel must hide a value that cannot reach the
current destination (it stays reachable by switching destination).

| Key | Today's label (it) | Intent | Where | Level |
|---|---|---|---|---|
| `image.max_pixels` | Pixel massimi immagine | Quality/speed | all VLMs that declare it | **Essential** |
| `image.min_pixels` | Pixel minimi | Quality | models that declare it | Advanced |
| `generation.max_tokens` | Token massimi | Quality (truncation) | all | **Essential** |
| `generation.temperature` | Temperature | Advanced sampling | all | Advanced |
| `generation.top_p` | Top-p | Advanced sampling | all | Advanced |
| `generation.top_k` | Top-k | Advanced sampling | all | Advanced |
| `generation.repetition_penalty` | Penalità ripetizione | Anti-loop | all | Advanced |
| `generation.presence_penalty` | Presence penalty | Anti-loop | all | Advanced |
| `generation.frequency_penalty` | Frequency penalty | Anti-loop | all | Advanced |
| `generation.no_repeat_ngram_size` | No-repeat n-gram | Anti-loop | models with an n-gram processor | Advanced |
| `workflow.*` | per model | Quality/speed (vendor pipeline) | per model | per field: `essential` flag in the spec |
| `serving.dtype` | Precisione | Server | vLLM (local NVIDIA, Vast, RunPod, Modal) | Server |
| `serving.gpu_memory_utilization` | Memoria GPU vLLM | Server | vLLM | Server |
| `serving.max_model_len` | Contesto massimo | Server | vLLM | Server |
| `serving.max_num_seqs` | Sequenze parallele | Server/speed | vLLM | Server |
| `serving.max_num_batched_tokens` | Token per batch | Server/speed | vLLM | Server |
| `mlx.repo` | Variante del checkpoint | Which model | local Apple Silicon | Model choice (already asked in step 2) |
| `mlx.kv_bits`, `kv_group_size`, `kv_quant_scheme`, `max_kv_size` | Cache KV … | Server/memory | local Apple Silicon | Server |
| `mlx.vision_cache_size` | Cache visiva | Server/speed | local Apple Silicon | Server |
| `mlx.repetition_penalty`, `repetition_context_size`, `presence_context_size` | Penalità … | Anti-loop (MLX path) | local Apple Silicon | Advanced |
| `mlx.log_level` | Livello log | Diagnostics | local Apple Silicon | Server |

Outside the panel, and staying where they are (they are part of *choosing* or
*provisioning*, not of tuning the model): model and custom checkpoint (step 1), destination
and MLX variant (step 2), Vast.ai GPU filter / max price / disk / CUDA / network, Modal
keep-warm, RunPod and SSH endpoints, LlamaCloud key (step 3 checklists), compute
profiles (Settings → Instance).

## 3. Intents and levels

The four questions a user brings to step 3, in order of how often they are asked:

1. **Which model** — answered in step 1. The panel header repeats it ("Parameters ·
   MonkeyOCRv2-Parsing · on Vast.ai") and never offers a second model picker.
2. **Where it runs** — answered in step 2. It decides which server group exists at all.
3. **Quality and speed** — the *essentials*: the 2–4 values that change the result or
   the time per page. Always visible.
4. **Advanced / expert** — everything else, in collapsible groups named for what they
   do, not for the software they configure.

Progressive disclosure, three levels:

- **L0 — summary line.** Collapsed state of the panel in step 3: "Official parameters"
  or "2 values differ from the official recipe", with *Customise*.
- **L1 — essentials.** `image.max_pixels`, `generation.max_tokens`, and the workflow
  fields the model's spec marks `essential` (MonkeyOCRv2 DFlash, Unlimited-OCR image
  mode, TeleOCR layout mode, LlamaCloud tier and languages).
- **L2 — groups**, each collapsed, each with its own "n differ" badge:
  - *How it reads the image* — `min_pixels`, non-essential image workflow fields.
  - *How it writes the text* — sampling and anti-loop values, with the official value
    per task shown (see §4).
  - *Model pipeline* — the remaining vendor workflow fields.
  - *Server on {destination}* — the `serving.*` group for vLLM destinations, the `mlx.*`
    group for local Apple Silicon. Only the group for the current destination renders;
    a one-line note says the other exists.

## 4. Defaults and differences

- The default of every field is the **official value** from the model's spec / adapter
  (audited in `MODELS_AUDIT.md`). Where the vendor documents no value the field says
  "Model default" and names who decides (the checkpoint's `generation_config.json`, the
  vLLM version) — never "Automatic".
- The backend sends, per model, the sampling the adapter applies **per task**
  (`task_sampling`: layout, text, table, formula, end-to-end). Generation fields show it:
  "Official: 0 (text) · 0.1 (table)". A user override applies to every task, and the
  copy says so.
- A field whose value differs from its default shows the official value next to it and
  a *Reset* control; the field row is marked (wash + "differs" word, never colour
  alone). Group headers and the L0 line count differences. *Reset all* stays.
- The backend sends `image_fields` so the view never names a model.

## 5. Copy

Plain language, the vendor key in monospace as secondary text for experts. Examples:

| Key | New label (it) | Help (it) |
|---|---|---|
| `max_pixels` | Dettaglio dell'immagine | Quanti pixel vede il modello. Più dettaglio, più lento; oltre il valore ufficiale il layout può peggiorare. |
| `max_tokens` | Lunghezza massima della risposta | Oltre questo limite la pagina viene troncata. |
| `temperature` | Casualità | 0 = sempre la stessa risposta. Per l'OCR il valore ufficiale è quasi sempre basso. |
| `repetition_penalty` | Freno alle ripetizioni | Scoraggia il modello dal ripetere la stessa riga all'infinito. |
| `gpu_memory_utilization` | Quota di memoria GPU | Quanta memoria della scheda il server può occupare. |
| `max_model_len` | Contesto massimo | Pagina + risposta devono starci. Abbassalo se la GPU va in out-of-memory. |
| `max_num_seqs` | Richieste in parallelo | Più alto = più pagine insieme, più memoria. |

All copy goes through `it.ts` / `en.ts` / `fr.ts` (R11).

## 6. Layout alternatives

Three were rendered for the user before implementation:

- **A — Accordion levels.** One column: summary bar, essentials always open, groups as
  collapsible rows with their difference badge.
- **B — Tabs by intent.** Essentials / Image / Text / Pipeline / Server tabs with
  difference counts, and a strip listing every changed value with its reset.
- **C — Comparison ledger.** A ruled table "Parameter · Official · In use", grouped by
  intent, filtered by *essentials / all / only different*.

Chosen: **B — tabs by intent** (user's choice, 2026-11-03). As built:

- The panel follows the hub: the model and destination chosen in steps 1–2
  (`adapterId`, `destination`, `localRuntime` props); its own model picker remains only
  when it is rendered without a hub choice.
- Tabs: *Essentials* · *Image* · *Text* · *Model pipeline* · *Server · {destination}*. A
  tab with no field for this model and destination is not shown. Each tab shows how
  many of its values differ from the official ones.
- A strip under the tabs lists every difference, wherever it lives, with its reset.
- Each row: plain label and help, the vendor key in monospace, the control, "official:
  …" (per task for sampling, e.g. "1 (text, tables)"), and *Reset* when it differs.
- *Save* sends only the values that differ; *Reset everything to official* clears the
  model's overrides.
- The MLX presence-penalty window (`mlx.presence_context_size`), validated by the
  backend but never shown before, is now reachable in the Text tab.

## 7. Non-goals

- No new parameters and none removed: every value settable today stays settable.
- No change to the backend validation rules, the storage format of overrides
  (`model_settings:<id>`) or the provisioning checklists.
