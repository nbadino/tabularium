# Guida al Serving Locale (GPU sul tuo PC)

Questa guida documenta come servire un modello OCR/VLM **sulla GPU locale**, con i
parametri vLLM verificati sulla documentazione ufficiale di ciascun modello
(agosto 2026) — l'equivalente locale di `docs/CLOUD_INFERENCE_GUIDE.md`, che
copre invece l'offloading su GPU remota.

`VllmClient` (`backend/app/services/inference.py`) non distingue locale da
remoto: parla sempre a un endpoint OpenAI-compatibile via vLLM, capendo da
solo se punta a `127.0.0.1`/`localhost` (`is_cloud`). L'unica parte
specifica del locale è **come lanciare il server** — il `serve_command()` di
ogni adapter in `backend/app/services/model_adapters.py` — e questo è
l'oggetto della guida.

**Nessun modello è bloccato per dimensione.** Come in LM Studio: puoi provare
qualunque modello sulla tua GPU, il registro mostra solo un avviso ⚠ se il
checkpoint rischia di non entrare nella VRAM libera rilevata (v. §6). Le note
di fattibilità qui sotto (es. "margine stretto su 8 GB") sono informative, non
limiti imposti dal codice.

**Niente comandi da terminale, niente variabili d'ambiente da impostare.**
Tutto il flusso è: Registro Modelli → Scarica → Avvia. Alla primissima
richiesta di avviare un modello generico (`vllm serve ...`), Tabularium crea
da sé un ambiente Python dedicato (`TABULARIUM_ROOT/vllm-runtime`) e ci
installa vLLM — un paio di minuti la prima volta, istantaneo dopo. Per
MonkeyOCRv2-Parsing clona anche da sé il checkout ufficiale del repo
(`TABULARIUM_ROOT/vendor/MonkeyOCRv2`), che gli serve per `serve.py`. Le
variabili d'ambiente citate qui sotto (`TABULARIUM_TRAIN_REPO`,
`TABULARIUM_TRAIN_PYTHON`, `TABULARIUM_SERVE_PYTHON`) restano solo un
**override opzionale** per chi ha già un ambiente/checkout proprio — non sono
mai un prerequisito.

GPU di riferimento usata per tarare i parametri: **RTX 4060 Laptop, 8 GB
VRAM** (Ada Lovelace, compute capability 8.9 — soddisfa il requisito bf16 di
vLLM, che richiede compute capability ≥ 8.0).

---

## 1. Come funziona il registro modelli in locale

1. **Download** — dalla card "Registro modelli" (o `POST /api/models/{id}/download`)
   scarica i pesi da Hugging Face in `TABULARIUM_MODELS_DIR/<adapter_id>/`
   (default `data/models/<adapter_id>/`).
2. **Serve** — `POST /api/models/{id}/serve/start` chiama
   `adapter.serve_command(model_path, port)` e lancia il processo
   (`backend/app/services/serve_manager.py`): un solo modello alla volta, lo
   start ferma sempre quello precedente (una GPU consumer non ne regge due).
3. **Collegamento automatico** — lo start imposta anche l'endpoint di
   inferenza attivo (`http://127.0.0.1:<porta>/v1`) con l'adapter giusto:
   nessun passaggio manuale in Impostazioni.

Non devi installare `vllm` tu: se non è già sul `PATH` del processo backend
(e non hai impostato `TABULARIUM_SERVE_PYTHON`), lo start lo installa da sé
nell'ambiente condiviso descritto sopra. MonkeyOCRv2 fa eccezione solo per il
*codice* di serving: delega a uno script dedicato che gira nello stesso
ambiente, ma legge il proprio checkout del repo ufficiale (§2, anch'esso
clonato in automatico).

---

## 2. MonkeyOCRv2-B-Parsing (produzione attuale)

Serve command: `scripts/serve_model.sh` (non `vllm serve` diretto). Requisiti
verificati sul repo ufficiale `Yuliang-Liu/MonkeyOCRv2`:

- Python 3.11, vLLM installato secondo la guida ufficiale (0.25.1 per il draft
  DFlash, oppure 0.11 legacy), più `parsing/requirements.txt` (`timm`,
  `gradio`, `pypdfium2`). **Nessuna compilazione manuale di flash-attn è
  richiesta per il serving** — solo per l'ambiente di training/vision encoder,
  che è un env separato.
- Il repo ufficiale (checkout con la cartella `parsing/` e lo script
  `serve.py`) è distinto dai pesi: i pesi li scarica il registro modelli di
  Tabularium, il codice di serving no — **viene clonato in automatico** al
  primo avvio (`TABULARIUM_ROOT/vendor/MonkeyOCRv2`). Se hai già un tuo
  checkout, `TABULARIUM_TRAIN_REPO` lo sostituisce; stesso discorso per
  `TABULARIUM_TRAIN_PYTHON`/`TABULARIUM_SERVE_PYTHON` sull'ambiente Python.
- Nessun Dockerfile ufficiale: il clone automatico del checkout resta l'unico
  percorso, ma non richiede alcuna azione manuale.
- **`gcc-13`, `NVCC_PREPEND_FLAGS=-allow-unsupported-compiler`, `MAX_JOBS=2`
  nello script sono workaround empirici** di questa installazione (compilatore/
  CUDA JIT), non requisiti documentati dal repo ufficiale — su una macchina
  diversa potrebbero non servire, o servirne di diversi.
- Flag di `serve.py` (`--gpu-memory-utilization 0.9 --max-model-len 24576
  --max-num-batched-tokens 24576 --max-num-seqs 8`): i default ufficiali sono
  più bassi (0.5 / 16384 / 16384 / 128) — quelli in uso sono **tarati
  empiricamente** su questa GPU da 8 GB, non "corretti" rispetto all'upstream.
- VRAM minima: non dichiarata ufficialmente; verificata empiricamente su 8 GB.

---

## 3. MinerU2.5 (`opendatalab/MinerU2.5-Pro-2605-1.2B`)

```
vllm serve <model_path> --port <porta> \
  --logits-processors mineru_vl_utils:MinerULogitsProcessor \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.75 \
  --max-model-len 16384 \
  --max-num-seqs 4 \
  --max-num-batched-tokens 8192 \
  --served-model-name mineru2.5
```

- `--logits-processors` è **obbligatorio** (repo ufficiale
  `opendatalab/mineru-vl-utils`): senza, il modello va in loop di ripetizione.
  Installare `mineru-vl-utils` **senza** l'extra `[vllm]` (dichiara vLLM
  `<0.22.0`; qui si usa 0.21.0 nel template cloud, compatibile).
- `--dtype`/`--gpu-memory-utilization`/`--max-model-len`/`--max-num-seqs`/
  `--max-num-batched-tokens`: **non raccomandati da nessuna guida ufficiale**
  (nessuna VRAM minima dichiarata, solo benchmark su A100) — valori scelti per
  8 GB, da validare empiricamente. Il `config.json` del checkpoint dichiara
  `max_position_embeddings=32768` a livello top-level ma `8192` nella
  sotto-struct `text_config`: discrepanza non spiegata da OpenDataLab, 16384 è
  una scelta intermedia prudente.
- Checkpoint ~2.5 GB: margine comodo su 8 GB anche coi valori sopra.

---

## 4. dots.mocr (`dots-studio/dots.mocr`)

```
vllm serve <model_path> --port <porta> \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.80 \
  --max-model-len 16384 \
  --chat-template-content-format string \
  --trust-remote-code \
  --served-model-name dots-mocr
```

- Architettura nativa in **vLLM ≥ 0.11.0** (PR ufficiale #24645): non serve
  più il pin storico `vllm==0.9.1`.
- `--chat-template-content-format string` è **obbligatorio** secondo il
  README ufficiale (controlla solo la serializzazione testo per il chat
  template, non interferisce con l'invio di immagini in stile OpenAI usato da
  `VllmClient`).
- **Checkpoint reale ~6.1 GB** (due shard safetensors) — una stima precedente
  di 3.4 GB era quella del solo componente LLM (1.7B), non del checkpoint
  intero. Su 8 GB il margine è stretto: `--gpu-memory-utilization 0.80` e
  `--max-model-len 16384` sono una scelta prudente non vendor-verificata (il
  README non raccomanda valori per hardware specifico).

---

## 5. PaddleOCR-VL-1.6 (`PaddlePaddle/PaddleOCR-VL-1.6`)

```
vllm serve <model_path> --port <porta> \
  --trust-remote-code \
  --max-num-batched-tokens 16384 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --served-model-name PaddleOCR-VL-1.6
```

- Comando base verificato sulla recipe ufficiale vLLM
  (`docs.vllm.ai/projects/recipes/.../PaddleOCR-VL.html`).
- **`--max-model-len` è indispensabile**: il `config.json` dichiara
  `max_position_embeddings=131072`, e senza un tetto esplicito vLLM lo usa
  come default, precallocando una KV cache enorme (issue vLLM #31372,
  "Using max model len 131072") — sproporzionato per OCR su crop.
- Checkpoint piccolo (~1.8 GB); i maintainer confermano solo una RTX 3060
  12GB come configurazione minima testata — su 8 GB non c'è conferma
  ufficiale, ma il margine aritmetico è ampio.

---

## 6. Unlimited-OCR (`baidu/Unlimited-OCR`) — via Docker

**Unica eccezione all'automazione**: l'architettura non è nella wheel pip
stabile di vLLM, quindi non basta l'ambiente vLLM che Tabularium prepara da
sé per gli altri modelli. Serve **Docker + `nvidia-container-toolkit`
installati sulla macchina** (questi sì, a carico tuo: sono dipendenze di
sistema, non pacchetti Python — Tabularium lancia `docker run` da sé, ma non
può installare Docker al posto tuo). Una volta presenti, "Avvia" funziona
come per ogni altro modello: nessun comando da digitare.

Il serve locale usa l'immagine Docker dedicata
`vllm/vllm-openai:unlimited-ocr`:

```
docker run --rm --gpus all --network host --ipc host \
  -v <model_path>:/model \
  vllm/vllm-openai:unlimited-ocr \
  /model \
  --trust-remote-code \
  --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching --mm-processor-cache-gb 0 \
  --port <porta> --gpu-memory-utilization 0.85 \
  --served-model-name Unlimited-OCR
```

- Il logits processor è **obbligatorio**: senza, i documenti lunghi vanno in
  loop sui token `<|det|>`.
- Il montaggio `-v <model_path>:/model` punta ai pesi già scaricati dal
  registro modelli invece di farli riscaricare dentro il container.
- La card ufficiale dichiara "una singola GPU ≥ 8GB è sufficiente per
  l'inferenza BF16": margine dichiarato ma stretto su 8 GB.
- Fermare il server (`serve/stop`) invia SIGTERM al processo `docker run`,
  che Docker inoltra al container — non istantaneo come un processo nativo,
  ma normale.

---

## 7. GLM-OCR (`zai-org/GLM-OCR`)

```
vllm serve <model_path> --port <porta> \
  --speculative-config '{"method": "mtp", "num_speculative_tokens": 3}' \
  --max-num-batched-tokens 32768 \
  --max-model-len 16384 \
  --gpu-memory-utilization 0.85 \
  --served-model-name glm-ocr
```

- Richiede **vLLM ≥ 0.19.0**. Architettura nativa (Transformers ≥ 5.0.0):
  `--trust-remote-code` non è nel comando ufficiale — se il server rifiuta
  l'architettura su una wheel meno recente, è il primo flag da aggiungere.
- `--max-num-batched-tokens 32768` evita l'errore noto in community "exceeds
  pre-allocated encoder cache size" su immagini ad alta risoluzione.
- Checkpoint reale ~2.7 GB (misurato dal file safetensors su Hugging Face, il
  README non dichiara una dimensione): comodo su 8 GB.
- **Nessuna integrazione OCR** (prompt/parsing) ancora implementata in
  Tabularium: questo lo rende deployabile, non ancora usabile dal prefill
  strutturato.

---

## 8. DeepSeek-OCR-2 (`deepseek-ai/DeepSeek-OCR-2`)

```
vllm serve <model_path> --port <porta> \
  --trust-remote-code \
  --logits-processors vllm.model_executor.models.deepseek_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching --mm-processor-cache-gb 0 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85 \
  --served-model-name deepseek-ocr-2
```

- Richiede **vLLM ≥ 0.12.0**. L'architettura ha una storia di breaking change
  fra versioni vLLM (issue vllm-project/vllm#33252): verificare con
  `vllm serve --help` prima di un deploy reale.
- Licenza corretta: **Apache-2.0** (non MIT come indicato in una stima
  precedente).
- La "Gundam mode" (compressione ottica/crop tiling) è hardcoded
  nell'integrazione vLLM: nessun flag la controlla.
- Card ufficiale: "una singola GPU ≥ 8GB è tipicamente sufficiente per
  l'inferenza BF16" — margine stretto ma dichiarato.
- Nessuna integrazione OCR ancora implementata (come GLM-OCR).

---

## 9. Qwen3-VL-8B-Instruct (`Qwen/Qwen3-VL-8B-Instruct`)

```
vllm serve <model_path> --port <porta> \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.90 \
  --limit-mm-per-prompt '{"image":4,"video":0}' \
  --served-model-name qwen3-vl-8b
```

- Architettura nativa in **vLLM ≥ 0.11.0**: nessun `--trust-remote-code`.
- **Checkpoint reale ~16.3 GB in bf16** (il "9B" della card è il conteggio
  parametri, non la dimensione su disco: 2 byte/parametro in bf16). Su 8 GB
  il warning di dimensione si attiva sempre con questo checkpoint; esiste una
  variante ufficiale FP8 (`Qwen/Qwen3-VL-8B-Instruct-FP8`, ~9.9 GB) e alcune
  AWQ 4-bit community (~7 GB) — nessuna lascia margine reale per KV cache +
  vision encoder su 8 GB, ma nulla è bloccato: resta provabile.
- VLM generalista, nessuna integrazione OCR ancora implementata.

---

## 10. Modelli "a piacere" (repo Hugging Face libero)

Dalla card "Registro modelli" → "Aggiungi modello personalizzato" (o
`POST /api/models/custom`) puoi registrare qualunque repo Hugging Face:

```json
{
  "display_name": "Il mio modello",
  "hf_repo": "org/nome-modello",
  "hf_revision": "main",
  "served_model_name": "il-mio-modello",
  "trust_remote_code": true,
  "max_model_len": 8192,
  "gpu_memory_utilization": 0.85,
  "extra_args": "--dtype bfloat16 --tensor-parallel-size 1"
}
```

Il sistema costruisce un `vllm serve <repo> ...` generico con i flag che
fornisci; `extra_args` passa flag vLLM aggiuntivi non coperti dal form. Nessun
protocollo OCR (prompt/parsing) è verificato per questi modelli: sono
solo scaricabili e servibili, non integrati nel prefill assistito.

`DELETE /api/models/custom/{id}` rimuove del tutto la definizione (e i pesi
se scaricati); `DELETE /api/models/{id}` cancella solo i pesi, lasciando la
definizione riusabile per un nuovo download.

---

## 11. Come funziona l'avviso di dimensione (VRAM)

Ogni voce del registro modelli espone `vram_warning` (stringa o `null`):
confronta la dimensione del checkpoint (reale su disco se già scaricato,
altrimenti la stima dichiarata) con la VRAM libera rilevata via `nvidia-smi`,
con un margine del 35% per KV cache/attivazioni. **È un avviso, non un
blocco** — a differenza del preflight del training (`services/vram.py`, che
può bloccare un preset non riproducibile), qui l'utente decide sempre se
provare comunque. Se il server va in out-of-memory, i primi due parametri da
ridurre sono `--max-model-len` e `--gpu-memory-utilization`.

---

## 12. Tabella riassuntiva

| Modello | Locale | Cloud (Modal) | Checkpoint | Note 8 GB |
|---|---|---|---|---|
| MonkeyOCRv2-B-Parsing | script dedicato | `modal_vllm.py` | ~1.5 GB | tarato, in produzione |
| MinerU2.5 | `vllm serve` | `modal_mineru.py` | ~2.5 GB | comodo |
| dots.mocr | `vllm serve` | `modal_dots_ocr.py` | ~6.1 GB | margine stretto |
| PaddleOCR-VL-1.6 | `vllm serve` | `modal_paddleocr_vl.py` | ~1.8 GB | comodo |
| Unlimited-OCR | Docker | `modal_unlimited_ocr.py` | ~6 GB | margine stretto |
| GLM-OCR | `vllm serve` | `modal_glm_ocr.py` | ~2.7 GB | comodo |
| DeepSeek-OCR-2 | `vllm serve` | `modal_deepseek_ocr.py` | ~6 GB | margine stretto |
| Qwen3-VL-8B | `vllm serve` | `modal_qwen3_vl.py` | ~16.3 GB | oltre 8 GB, warning atteso |
| Modello custom | `vllm serve` generico | — | variabile | warning secondo dimensione reale |

Fonti dettagliate (model card, README, recipe vLLM, issue) sono citate nei
docstring dei rispettivi adapter in `backend/app/services/model_adapters.py`.
