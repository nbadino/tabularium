# Tabularium — registro test locali e benchmark

Registro operativo dei test eseguiti per verificare il workflow reale:

`Registro modelli → download → serving locale → endpoint OpenAI-compatible → inferenza`.

Il benchmark è uno strumento interno di verifica. Non è una funzione che l’utente
deve configurare o usare per annotare dati, e nessun output del benchmark viene
salvato come annotazione.

## Regole del test

- Un solo VLM alla volta sulla GPU locale.
- Stessa immagine, stesso task e stesso `max_pixels` quando si confrontano modelli.
- Si registra sempre adapter, checkpoint, runtime, porta, latenza e risultato.
- Download e serving sono verificati attraverso le stesse API che usa la UI;
  i comandi shell servono soltanto per raccogliere evidenza tecnica.
- Un test non eseguito non viene marcato come riuscito.
- Le misure su una GPU già occupata non sono confrontabili e vanno ripetute.

## Hardware e runtime — 2026-09-01

| Voce | Evidenza |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU |
| VRAM | 8188 MiB |
| Compute capability | 8.9 |
| Driver | 580.173.02 |
| PyTorch | 2.13.0+cu130 |
| CUDA visibile a PyTorch | sì, 1 dispositivo |
| vLLM | 0.28.0 |
| Transformers | 5.16.1 |
| Hugging Face Hub | 1.29.0 |
| Docker | 27.1.1 |

La prima interrogazione `nvidia-smi` dentro il sandbox non vedeva il driver;
la verifica diretta successiva ha confermato GPU e CUDA funzionanti.

## Stato dei checkpoint locali

Verificato il 2026-09-01 con `du -sh data/models/*`:

| Adapter | Checkpoint locale | Dimensione osservata | Download | Serving/inferenza |
|---|---:|---:|---|---|
| `monkeyocrv2-parsing` | sì | 2.0 GB | presente | layout superato |
| `mineru2.5` | sì | 2.2 GB | presente | text superato; layout da riallineare |
| `paddleocr-vl` | sì | 1.9 GB | presente | text superato |
| `deepseek-ocr` | sì | 6.4 GB | presente | text superato; layout non delimitato |
| `unlimited-ocr` | sì | 6.4 GB | presente | end2end superato |
| `dots-ocr` | temporaneo | 5.7 GiB in `/tmp` | download riuscito | OOM anche con contesto 4096/offload/eager |
| `glm-ocr` | temporaneo | 2.5 GB in `/tmp` | download riuscito | engine non completa warmup anche a contesto 4096 |
| `qwen3-vl-8b` | parziale | 204 MiB | resume UX fermato a 212.8 MB; stima 16.3 GB | non verificato |

È presente anche il draft `monkeyocrv2-parsing-draft` (161 MB), ma è marcato
`.unusable` dopo il tentativo precedente: sulla 4060 il serving deve ripiegare
sul modello senza DFlash.

## Test UX e prodotto

### Frontend — superato

Eseguito il 2026-09-01:

```text
npm test -- --run
Test Files 13 passed (13)
Tests      79 passed (79)
```

Build produzione superata:

```text
npm run build
vite build: success
```

Il detector UI sui componenti modificati non ha rilevato problemi:

```text
node .../impeccable/scripts/detect.mjs --json \
  frontend/src/app/ModelsModal.tsx frontend/src/pages/EvaluationPage.tsx
[]
```

La UI consente di scaricare e avviare un modello dal Registro. La porta locale
è selezionabile perché una porta occupata non deve costringere l’utente a
modificare codice.

### Backend e benchmark — compilazione superata

```text
PYTHONPATH=backend data/vllm-runtime/bin/python -m py_compile \
  backend/app/services/inference.py backend/app/services/evaluate.py \
  backend/app/api/evaluate.py scripts/benchmark_models.py
backend-and-benchmark-checks-passed
```

Il Python di sistema non contiene le dipendenze dev (`httpx2`), ma la suite è
stata eseguita con `backend/.venv/bin/pytest`:

```text
backend/.venv/bin/pytest -q backend/tests/test_model_registry.py
28 passed in 1.15s
```

Questo include il test del preflight: con 2 GiB liberi il download GLM viene
rifiutato prima di creare la directory o avviare un sottoprocesso.

La suite backend completa è stata poi eseguita con accesso ai socket locali
necessari ai test di lifecycle del serving:

```text
backend/.venv/bin/pytest -q backend/tests
292 passed, 3 skipped, 1 deselected in 68.42s
```
Il test escluso è `test_provider_refresh_persists_rate_and_live_state`,
legato a una rotta mock Vast.ai (`/api/v1/instances/`) e non al registry,
OAuth, serving locale o benchmark.

## Test di serving/inferenza

### Unlimited-OCR — deploy e inferenza end-to-end superati

Il 2026-09-01 era attivo un container Docker con:

```text
vllm/vllm-openai:unlimited-ocr
--served-model-name Unlimited-OCR
--port 8888
--max-model-len 12288
--gpu-memory-utilization 0.85
--cpu-offload-gb 4
```

Health check osservato:

```json
{"object":"list","data":[{"id":"Unlimited-OCR"}]}
```

VRAM osservata durante il serving: circa 5646 MiB usati.

Il primo lancio del benchmark aveva un bug nel runner (immagine non passata a
`end2end`), corretto subito dopo. La seconda prova ha poi evidenziato un
secondo bug: il retry generico per liste JSON veniva applicato anche al formato
grounded-markdown di Unlimited-OCR. Il client ora riconosce il protocollo nativo
e non ritenta inutilmente.

| Task | Esito | Wall | TTFT | Output |
|---|---:|---:|---:|---|
| end2end | superato, 4/4 blocchi validi | 29.375 s | 1.929 s | `data/benchmarks/bench_20260901T173836Z/outputs/unlimited-ocr/end2end-001.json` |

Il container è stato fermato dopo la prova e la GPU è tornata libera.

### Spazio disco — blocker operativo rilevato

Dopo i checkpoint già presenti, la partizione del progetto ha raggiunto circa
il 100% di utilizzo, con 1.3 GB liberi al controllo del 2026-09-01. Il download
di GLM-OCR (file principale atteso ~2.65 GB) è stato arrestato da Hugging Face
con `No space left on device`; sono rimasti soltanto 6.7 MB di metadati nella
cartella `data/models/glm-ocr`. Qwen3-VL è stimato a 16.3 GB e non può essere
scaricato in queste condizioni. Nessun file esistente è stato cancellato.

### MonkeyOCRv2 — avvio e layout superati

Il tentativo precedente sulla porta 8888 era stato impedito dal container
Unlimited-OCR già attivo; non era un fallimento del modello. Aggiornamento
2026-09-01: prova completata su GPU libera con il runtime
dedicato. Il server è diventato pronto dopo il caricamento e il warmup; il
processo ha usato `--gpu-memory-utilization 0.9`, `--max-model-len 24576` e
ha esposto il modello `MonkeyOCRv2`. Il risultato è stato poi salvato e il
server è stato fermato correttamente.

| Task | Immagine | Esito | Wall | TTFT | Output |
|---|---|---:|---:|---:|---|
| layout | `test/1502-a-BANCO-SAN-GIORGIO-originale.jpg` (741×1024) | 5/5 elementi validi | 2.537 s | 1.183 s | 147 token, 165.11 token/s |

Artefatti completi:

- report: `data/benchmarks/bench_20260901T144611Z/report.json`
- raw output: `data/benchmarks/bench_20260901T144611Z/outputs/monkeyocrv2-parsing/layout-001.json`

Nota: questa è una misura di serving/layout e non una metrica di qualità
gold. Per qualità servono le pagine annotate e il validation split.

Il 2026-09-02 il checkout ufficiale è stato ripristinato automaticamente in
`data/vendor/MonkeyOCRv2` e il workflow completo `scripts/serve_model.sh` →
`parsing/serve.py` è stato ripetuto con path assoluti, come fa la UX. Il server
ha esposto `MonkeyOCRv2`; il benchmark omogeneo `text` ha prodotto 916
caratteri validi in 3.628 s (TTFT 0.758 s, 164.80 token/s).

Output: `data/benchmarks/bench_20260902T064827Z/outputs/monkeyocrv2-parsing/text-001.json`.

### MinerU2.5 — deploy superato, protocollo da riallineare

La verifica dalla UX aveva già dimostrato che MinerU si avvia e funziona nel
prefill. La ripetizione del test sul percorso `serve_manager` ha confermato:

- caricamento del checkpoint completato;
- endpoint `/v1/models` pronto;
- GPU utilizzata durante il serving senza OOM;
- inferenza testuale non vuota sulla stessa immagine campione.

| Task | Esito | Wall | Output |
|---|---:|---:|---|
| layout | HTTP OK, ma 0 blocchi / 1 token | 1.217 s | `data/benchmarks/bench_20260901T145521Z/outputs/mineru2.5/layout-001.json` |
| text | superato, 1116 caratteri | 3.352 s | `data/benchmarks/bench_20260901T150055Z/outputs/mineru2.5/text-001.json` |

Il risultato layout non invalida il deploy: è un’anomalia di compatibilità fra
il runner/parser e il protocollo layout MinerU, perché lo stesso modello è già
usabile dalla UX. Va quindi corretto il benchmark per riprodurre esattamente il
percorso UX prima di confrontare i modelli. Il test iniziale fatto mentre il
server non era ancora pronto (`data/benchmarks/bench_20260901T145212Z/report.json`)
resta registrato come errore di readiness, non come errore del modello.

### PaddleOCR-VL — deploy e inferenza superati

Il 2026-09-01 il checkpoint locale è stato caricato dal `serve_manager` e il
server vLLM ha raggiunto `Application startup complete`. Il primo 404 era
causato dal nome sbagliato inviato dal runner (`paddleocr-vl` invece di
`PaddleOCR-VL-1.6`), quindi non era un problema del modello. Ripetuto con il
nome esposto dal server, il test ha prodotto testo non vuoto.

| Task | Esito | Wall | Output |
|---|---:|---:|---|
| text | superato, 10783 caratteri | 12.045 s | `data/benchmarks/bench_20260901T151055Z/outputs/paddleocr-vl/text-001.json` |

Il primo tentativo resta comunque conservato nel report
`data/benchmarks/bench_20260901T150511Z/report.json` come diagnostica di
configurazione del target. Il runner salva anche un artefatto `*-error.json`
per ogni nuova iterazione fallita.

### DeepSeek-OCR-2 — deploy e inferenza superati con offload

Il 2026-09-01 il checkpoint locale è stato servito dal percorso UX con
`--cpu-offload-gb 4`, necessario sulla RTX 4060 Laptop da 8 GB. Il server ha
completato il warmup senza OOM e l’inferenza testuale ha prodotto output non
vuoto.

| Task | Esito | Wall | TTFT | Output |
|---|---:|---:|---:|---|
| text | superato, 380 caratteri | 17.760 s | 2.350 s | `data/benchmarks/bench_20260901T172157Z/outputs/deepseek-ocr/text-001.json` |

La misura è di serving/inferenza, non ancora di qualità gold; il parser layout
specifico DeepSeek resta da validare.

Un tentativo layout successivo ha mostrato che il prompt layout DeepSeek può
entrare in una generazione non delimitata: il server continua a produrre token
senza chiudere lo stream e il test è stato interrotto manualmente dopo il
timeout operativo. Non è una misura valida e non viene usata nel confronto; il
log del server resta in `data/models/deepseek-ocr/.serve.log`.

## Confronto preliminare dei run validi

Questa tabella confronta solo run con output conforme al task indicato. Non è
una classifica di accuratezza: usa task diversi dove il protocollo del modello
non è ancora omogeneo e non contiene metriche gold.

| Adapter | Task | Output valido | Wall | TTFT |
|---|---|---:|---:|---:|
| MonkeyOCRv2-Parsing | text | 916 caratteri | 3.628 s | 0.758 s |
| MinerU2.5 | text | 1116 caratteri | 3.337 s | 0.632 s |
| PaddleOCR-VL | text | 10783 caratteri | 12.031 s | 0.566 s |
| DeepSeek-OCR-2 | text | 380 caratteri | 17.774 s | 2.400 s |
| Unlimited-OCR | end2end | 4 blocchi | 29.375 s | 1.929 s |

### GLM-OCR — download temporaneo riuscito, serving non sostenibile su 8 GB

Il checkpoint completo è stato scaricato temporaneamente in
`/tmp/tabularium-glm-ocr` (2.5 GB). La ricetta con speculative MTP fallisce
perché il checkpoint non contiene i pesi MTP attesi da vLLM 0.28.0. Il fallback
senza MTP ha poi caricato il modello base, ma la configurazione 16k/32k è andata
in OOM; anche il profilo consumer 8k/8k non ha completato la profilazione
FlashInfer. Non viene quindi dichiarato deploy riuscito.

Un terzo tentativo con `gpu-memory-utilization=0.90` e contesto 8192 ha
confermato il limite: il modello carica i pesi ma vLLM non riesce a
inizializzare una cache KV sufficiente. GLM resta quindi non verificato su
questa GPU, anche senza speculative decoding.

Il 2026-09-02 è stato provato anche il profilo minimo ragionevole senza MTP,
`max-model-len=4096`, `max-num-batched-tokens=8192` e quota GPU 0.90. I pesi
sono stati caricati (2.21 GiB), ma l’engine è terminato durante il warmup con
`Engine core initialization failed`; non è stata possibile alcuna inferenza.

### Dots OCR — download temporaneo riuscito, OOM in profilazione

Il checkpoint completo è stato scaricato temporaneamente in
`/tmp/tabularium-dots-ocr` (~5.7 GiB di pesi). Con offload CPU 4 GiB e contesto
8k i pesi sono stati caricati, ma vLLM ha terminato con `No available memory for
the cache blocks` durante l’inizializzazione dell’encoder. Il modello resta
non verificato sulla RTX 4060 Laptop da 8 GB.

Un secondo tentativo con `gpu-memory-utilization=0.90` ha ridotto il margine
solo fino a `-0.24 GiB` di cache disponibile e ha prodotto lo stesso errore;
non è quindi sufficiente aumentare la quota GPU senza una quantizzazione o una
GPU più capiente.

Il 2026-09-02 il contesto è stato ridotto a 4096 con quota GPU 0.90. Il modello
ha fallito esplicitamente con `torch.OutOfMemoryError` durante il forward
vision (`7.45 GiB` già occupati su `7.62 GiB` visibili); anche dots.ocr non è
quindi deployabile su questa scheda con la build vLLM corrente.

È stata provata anche la combinazione più favorevole osservata (`--enforce-eager`
e offload CPU 3 e poi 4 GiB). Il vision encoder ha comunque richiesto altri
338 MiB con soli 270 MiB liberi: l’errore è rimasto identico in entrambe le
prove. Non viene forzata una riduzione artificiale della risoluzione, perché
altererebbe il caso d’uso delle scansioni dense.

È stato aggiunto un preflight al Registro: prima di avviare un download
confronta spazio libero, dimensione stimata e un margine di 512 MiB. Con lo
stato attuale il download persistente di GLM viene rifiutato subito con un
messaggio esplicito, invece di saturare la partizione e fallire a metà.

## Runner benchmark

Il runner interno è [scripts/benchmark_models.py](scripts/benchmark_models.py).
Ogni esecuzione crea automaticamente `data/benchmarks/bench_<UTC>/`, con un
`report.json` e `outputs/<adapter_id>/` contenente il risultato grezzo completo
di ogni iterazione. Questi file sono gli artefatti da conservare per confronti
futuri; il report non sostituisce le annotazioni gold.
Accetta più endpoint già avviati e registra, per ogni run:

- esito e errore;
- latenza totale e aggregati mediani;
- TTFT, token/s e token usage restituiti dal server;
- numero di elementi layout/end2end o caratteri riconosciuti;
- validità OTSL per il task tabella.

Il campo `output_file` nel report collega ogni misura al raw output esatto che
l’ha prodotta.

Il parametro `--timeout` è ora propagato a tutti i task (`layout`, `text`,
`table` ed `end2end`), non soltanto a END2END: una generazione non delimitata
non può più bloccare indefinitamente il benchmark.

### Collegamento Hugging Face da UX

Per rispettare il vincolo «l'utente non tocca codice», il Registro modelli ora
espone il flusso OAuth Device Code ufficiale di Hugging Face. L'amministratore
preme **Collega Hugging Face**, autorizza l'app nel browser con il codice breve
e la UI fa polling dello stato; il token resta nella cache della libreria HF e
non viene esposto al frontend o ai log. Il download Qwen potrà essere ripreso
dalla UI dopo l'autorizzazione.

`ok` richiede inoltre un output conforme: una risposta HTTP con zero elementi
layout/end2end, testo vuoto o OTSL non valido viene registrata come fallimento
di protocollo, pur conservando il raw output per la diagnosi.

### Regressione runtime fresco — MinerU2.5 superata

Il 2026-09-02 è stato ripetuto il serving da checkpoint locale con il runtime
gestito dalla UX (`PATH` del venv, `CC=gcc-13`, `CXX=g++-13`,
`NVCC_CCBIN=/usr/bin/g++-13`) e una cache FlashInfer non già pronta. Il server
ha completato warmup e ha esposto `/v1/models`; il benchmark `text` ha prodotto
1116 caratteri validi in 3.337 s (TTFT 0.632 s, 198.24 token/s).

Output: `data/benchmarks/bench_20260902T062359Z/outputs/mineru2.5/text-001.json`.
Questo test ha anche isolato il fallback compiler appena aggiunto al
`serve_manager`: senza gli override GCC il primo avvio falliva nella
compilazione FlashInfer, mentre la configurazione UX completa passa.

### Benchmark omogeneo — PaddleOCR-VL superato

Ripetuto il 2026-09-02 sullo stesso campione e con il task `text`, usando la
ricetta vLLM del registry e il runtime UX completo. Il server ha completato il
warmup e il nome esposto verificato è `PaddleOCR-VL-1.6`. L’output è valido:
10783 caratteri in 12.031 s (TTFT 0.566 s, 275.46 token/s; generazione
terminata per il limite di 3072 token).

Output: `data/benchmarks/bench_20260902T062607Z/outputs/paddleocr-vl/text-001.json`.

### Benchmark omogeneo — DeepSeek-OCR-2 superato

Ripetuto il 2026-09-02 sullo stesso campione e con il task `text`, usando la
ricetta vLLM del registry, 4 GiB di offload CPU e il runtime UX completo. Il
server ha esposto correttamente `deepseek-ocr-2` e l’output è valido: 380
caratteri in 17.774 s (TTFT 2.400 s, 15.08 token/s).

Output: `data/benchmarks/bench_20260902T063034Z/outputs/deepseek-ocr/text-001.json`.

Esempio di confronto su due server già avviati:

```bash
PYTHONPATH=backend data/vllm-runtime/bin/python scripts/benchmark_models.py \
  --image test/1502-a-BANCO-SAN-GIORGIO-originale.jpg \
  --target monkeyocrv2-parsing,http://127.0.0.1:8888/v1,MonkeyOCRv2 \
  --target mineru2.5,http://127.0.0.1:8889/v1,mineru2.5 \
  --task layout --repeat 2 --output data/benchmarks/run.json
```

Il terzo campo del target è opzionale: con `adapter_id,url` il runner usa
automaticamente `capabilities.served_model_name`, cioè lo stesso nome con cui
il Registro avvia il server. Questo evita mismatch fra id interno e nome vLLM.

Per un adapter senza protocollo layout verificato usare `--task end2end`.
Un confronto di qualità richiede inoltre il validation split gold del progetto;
non è corretto confrontare soltanto il numero di blocchi o la velocità.

## Coda di verifica

### MinerU2.5: cap 1 MP del client violava il protocollo immagine — risolto (2026-09-02)

Sintomo (istanza Vast RTX 5060 Ti, MinerU2.5 via tunnel): prefill two-stage
produceva 155+ blocchi `Text` di ~32 px con contenuti da un solo carattere
(`-`, `、`, `3`) su LSI_17186_015 — il layout del modello era un flood di
micro-blocchi `page_number`, categoria sconosciuta ricaduta su `Text`.

Causa (riprodotta live, non inferita): `_chat` applicava a **tutte** le
immagini il tetto globale `VLLM_MAX_PIXELS=1.003.520` (pensato per
MonkeyOCRv2/MOCR2). L'immagine di layout di MinerU2.5 è fissata a 1036x1036 =
1.073.296 pixel, quindi veniva ridimensionata a 1001x1001 prima dell'invio.
A 1001x1001 il modello allucina la griglia di micro-blocchi (riprodotto con
richiesta manuale); a 1036x1036 esatti produce il layout pulito (5 blocchi:
2 Title, 2 Text, 1 Table a piena pagina — identico all'output del client
ufficiale `mineru-vl-utils` 1.2.1 eseguito sull'istanza stessa).

Correzione: `layout()` non passa più la dimensione ufficiale per il cap;
per i crop di testo/tabella gli adapter con `native_image_resolution`
(MinerU2.5) sono esonerati dal tetto client — i crop partono a risoluzione
nativa e il cap vero lo applica il preprocessore di vLLM dal
`preprocessor_config` del checkpoint (max_pixels=1605632), come fa il client
ufficiale. Verifica post-fix con il client Tabularium: 5 blocchi puliti,
testo del crop titolo = `SHIPPING INDEX`.

Nota: il difetto non era legato al cloud — colpiva qualsiasi serving di
MinerU2.5, anche locale.

Restano aperti solo test che richiedono un cambio di risorse o di hardware:

1. `qwen3-vl-8b`: almeno 16.3 GB liberi per il download persistente, oltre al
   margine operativo. Il 2026-09-02, con 26 GB liberi, il download via registry
   UX è stato avviato correttamente; HF anonimo ha trasferito 145.7 MB, poi si è
   fermato a `Fetching 16 files: 12/16`. Un resume nello stesso processo UX ha
   raggiunto 212.8 MB prima di restare senza ulteriore progresso osservabile.
   È stato cancellato lasciando 212.8 MB parziali riutilizzabili e circa 25 GB
   liberi. Nessun server o processo è rimasto attivo. Non è quindi una prova di
   serving né un fallimento della GPU; resta aperto il problema del download HF
   anonimo lento/bloccato.
2. `dots-ocr` e `glm-ocr`: GPU più capiente oppure checkpoint quantizzato
   ufficialmente supportato dal relativo progetto.
3. Benchmark di qualità: validation split gold comune e task omogeneo per
   confrontare davvero i modelli, oltre alle misure di latenza già raccolte.

## Risultato corrente

Il percorso UI di build e il runtime CUDA sono verificati. Il catalogo contiene
gli adapter e diversi checkpoint locali, ma il deploy/inferenza di tutti i
modelli e il benchmark comparativo completo sono ancora **incompleti**.

### Nota sui dataset annotati

I file `data/projects/5`, `6` e `9` presenti nel workspace sono dati di test,
non un gold set validato dall’utente. Le prove generate in
`data/benchmarks/gold_20260902T063434Z/` e
`data/benchmarks/gold_20260902T063552Z/` sono quindi soltanto esplorative e
non entrano in alcuna classifica o conclusione di qualità. Il runner
`scripts/benchmark_gold.py` resta pronto per un futuro validation set realmente
revisionato, ma non certifica il dataset che riceve.
