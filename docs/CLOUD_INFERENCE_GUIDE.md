# Guida all'Offloading dell'Inferenza su Cloud (Vast.ai, RunPod, Modal)

Questa guida spiega come eseguire **Tabularium interamente sul tuo PC locale** (scansioni, annotazioni, database SQLite, interfaccia web) **delegando tutta l'inferenza pesante (layout, tabelle OTSL, OCR e playground)** a una potente **GPU remota nel Cloud** (es. NVIDIA RTX 4090 / 3090 / A5000 / A100).

---

## 1. Come Funziona l'Architettura Ibrida

```
┌─────────────────────────────────────────────────────────────┐
│                    TUO PC LOCALE (Laptop / Desktop)         │
│  - FastAPI Backend (porta 8787)                             │
│  - React UI nel browser                                     │
│  - Scansioni storiche e Database SQLite (tabularium.db)         │
│  - Zero uso di GPU locale (CPU fredda, ventole spente)      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ (Richiesta HTTP/HTTPS con crop base64)
                               │ (via Tunnel SSH o Proxy HTTPS)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             CLOUD GPU INSTANCE (Vast.ai / RunPod)           │
│  - GPU NVIDIA RTX 4090 (24 GB VRAM) ~ $0.13 - $0.37 / ora   │
│  - Server vLLM OpenAI-compatibile con MonkeyOCRv2-B-Parsing │
│  - Risposta in streaming sub-secondo (>80 token/sec)        │
└─────────────────────────────────────────────────────────────┘
```

I vantaggi:
- **Zero requisiti hardware locali**: puoi annotare e usare il modello da un MacBook Air, un PC portatile senza scheda video dedicata o un mini-PC.
- **Velocità massima**: una RTX 4090 remota genera layout e tabelle istantaneamente.
- **Costo irrisorio**: da ~0,07$ all'ora (RTX 3090). Per 100 pagine di annotazione spendi pochi centesimi, e quando finisci spegni o fermi l'istanza.

---

## 2. Metodo 1: Vast.ai con Tunnel SSH (Scelta Consigliata)

Vast.ai è il marketplace GPU più economico al mondo (verificato 2026-08: RTX 3090 da 0,07 $/h
con mediana 0,15 $/h; RTX 4090 da 0,13 $/h con mediana 0,36 $/h).

### Passo 1: Noleggia un'istanza su Vast.ai
1. Crea un account su [vast.ai](https://vast.ai/) e ricarica $5.
2. Vai su **Search/Create Instance** e imposta i filtri:
   - **GPU**: 1x RTX 4090 oppure 1x RTX 3090 (almeno 24GB VRAM).
   - **Image**: `pytorch/pytorch:2.4.0-cuda12.4-cudnn9-runtime` o qualsiasi template Ubuntu PyTorch standard.
   - **Disk Space**: 40 GB (sufficiente per scaricare il modello ~2.5 GB e l'ambiente).
3. Clicca su **Rent**.

### Passo 2: Avvia il server vLLM sull'istanza Vast.ai
1. Dalla dashboard Vast.ai, clicca sul pulsante **SSH** e copia il comando di connessione (es. `ssh -p 34567 root@198.51.100.24 -L 8080:localhost:8080`).
2. Connettiti dal tuo terminale:
   ```bash
   ssh -p 34567 root@198.51.100.24
   ```
3. Scarica ed esegui il nostro script di setup automatico:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/scripts/cloud/setup_cloud_vllm.sh | bash
   # Oppure se hai clonato il repo:
   bash setup_cloud_vllm.sh --port 8888
   ```
   *Lo script installa vLLM, clona il runner, scarica automaticamente i pesi di MonkeyOCRv2-B-Parsing e avvia il server.*

### Passo 3: Apri il Tunnel SSH sul tuo PC locale
Sul tuo computer locale, esegui il nostro helper dedicato:
```bash
./scripts/cloud/ssh_tunnel.sh -p 34567 root@198.51.100.24
```
Ora la porta remota `8888` è inoltrata in modo sicuro e cifrato al tuo `http://127.0.0.1:8888/v1` locale!

### Passo 4: Collega Tabularium
1. Apri la Home di **Tabularium** nel browser.
2. Nel pannello **"Inferenza Cloud & Locale"**, seleziona il preset **"Vast.ai (Tunnel SSH)"** (oppure URL `http://127.0.0.1:8888/v1`).
3. Clicca su **"Test Connessione"**: vedrai la spia verde con la latenza in millisecondi.
4. Clicca su **"Salva Configurazione"**.
5. Fatto! Da questo momento, ogni operazione di annotazione assistita, rilevamento tabelle, playground ed evaluation userà la GPU Cloud.

---

## 3. Metodo 2: RunPod con Proxy HTTPS (Senza SSH)

RunPod offre proxy HTTPS integrati senza bisogno di aprire tunnel SSH da terminale.

### Passo 1: Noleggia un Pod su RunPod
1. Vai su [runpod.io](https://runpod.io/) -> **Pods**.
2. Scegli **1x RTX 4090** o **1x A5000**.
3. Template: **RunPod PyTorch 2.4 / CUDA 12**.
4. Imposta porta esposta HTTP: `8888`.
5. Clicca **Deploy**.

### Passo 2: Avvia il server vLLM
1. Apri la **Web Terminal** o connettiti via SSH.
2. Esegui lo script:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/cappannonno/tabularium/main/scripts/cloud/setup_cloud_vllm.sh | bash -s -- --port 8888 --api-key "chiave-segreta-tua"
   ```

### Passo 3: Collega Tabularium con l'URL HTTPS di RunPod
RunPod assegna automaticamente un URL pubblico protetto da SSL:
`https://<POD_ID>-8888.proxy.runpod.net/v1`

1. Inserisci l'URL in Tabularium: `https://<POD_ID>-8888.proxy.runpod.net/v1`
2. Inserisci la tua **API Key** segreta.
3. Clicca **"Test Connessione"** e poi **"Salva Configurazione"**.

---

## 4. Metodo 3: Vast.ai con IP Pubblico Diretto + API Key

Se preferisci non usare SSH Tunnel e avere una porta aperta su internet:
1. All'avvio del container Vast.ai, apri una porta diretta (es. `8888`).
2. Avvia vLLM con API Key:
   ```bash
   bash setup_cloud_vllm.sh --port 8888 --api-key "IL_TUO_TOKEN_SEGRETO"
   ```
3. In Tabularium, inserisci l'URL pubblico di Vast.ai (es. `http://198.51.100.24:34567/v1`) e la chiave API.

---

## 5. Metodo 4: Modal Serverless (inferenza a chiamata)

Il metodo **pay-per-second**: la GPU si accende quando arriva una richiesta e si spegne dopo.
Costo a riposo: **0 €**. Il repo include una template pronta (`scripts/cloud/modal_vllm.py`)
che costruisce il container (repo ufficiale + vLLM), scarica i pesi su un volume persistente
ed espone `parsing/serve.py` esattamente come lo script locale `serve_model.sh`.

1. Installa e autentica Modal (una sola volta):
   ```bash
   pip install modal
   modal setup
   ```
2. Fai il deploy della template:
   ```bash
   modal deploy scripts/cloud/modal_vllm.py
   ```
3. Copia l'URL stampato (forma `https://<WORKSPACE>--tabularium-vllm-serve.modal.run`) e
   usa `.../v1` nella card di inferenza di Tabularium. Il preset **Modal (Serverless a chiamata)**
   precompila il formato.

Note operative:
- **GPU**: la template usa **L4** di default (≈ 0,80 $/h attiva; A10 ≈ 1,10 $/h) e **non la T4**:
  vLLM gira in bfloat16 e rifiuta le GPU con compute capability < 8.0
  (`ValueError: Bfloat16 is only supported on GPUs with compute capability of at least 8.0` —
  T4 = 7.5, V100 = 7.0). Le alternative serverless valide sono Ampere o successive.
- **Cold start**: il primo caricamento del modello richiede alcuni minuti. La template tiene il
  container caldo 15 minuti dopo ogni richiesta (`scaledown_window=900`); per lavoro continuativo
  conviene un'istanza a ore (Vast.ai), per chiamate sporadiche il serverless.
- **Crediti**: il piano Starter Modal include **30 $ di crediti ogni mese** (verificato 2026-08).
- **API key** opzionale: `TABULARIUM_VLLM_API_KEY=... modal deploy ...` e imposta la stessa
  chiave nella card di inferenza.

## 6. Metodo 5: RunPod Serverless

Analogo a Modal ma con template Docker gestita da RunPod: serve un container che esponga
l'API OpenAI-compatibile di vLLM. L'endpoint ha formato
`https://api.runpod.ai/v2/<ENDPOINT_ID>/openai/v1` (preset **RunPod Serverless** in Tabularium).
Billing al secondo di worker attivo; **il cold start è fatturato a tariffa piena** —
con poco traffico Modal (crediti gratuiti) è in genere più economico; per orari attivi
lunghi conviene un Pod on-demand.

---

## 7. Verifica Rapida da Terminale (CLI Test)

Puoi testare qualsiasi endpoint remoto con il nostro strumento CLI:

```bash
# Test rapido di disponibilità e latenza:
python3 scripts/cloud/test_cloud_connection.py --url http://127.0.0.1:8888/v1

# Test completo con ritaglio sintetico e misura dei token/secondo:
python3 scripts/cloud/test_cloud_connection.py --url http://127.0.0.1:8888/v1 --sample
```

Output di esempio:
```
>> [Tabularium] Verifica Connessione Cloud / Remote vLLM
>> Target URL: http://127.0.0.1:8888/v1
============================================================
[1/2] Test disponibilità endpoint (/models)...
  ✓ Connessione riuscita! Latenza ping: 24.3 ms
  ✓ Modelli disponibili sul server: MonkeyOCRv2-B-Parsing

[2/2] Esecuzione inferenza di test con ritaglio sintetico...
  ✓ Inferenza completata in 410.5 ms!
  ✓ Token generati: 48 (~117.0 token/s)
>> [SUCCESS] Il server Cloud è pronto e compatibile con Tabularium!
```

---

## 8. Ottimizzazione Costi: Come Risparmiare

- **Ferma l'istanza quando non annoti**: sia su Vast.ai che su RunPod puoi mettere in pausa l'istanza. In pausa paghi solo lo storage (~0.05$/giorno). La **Gestione Cloud da UI** (pulsante 🎛️ nella card di inferenza) avvia/ferma tunnel e istanze Vast.ai senza terminale.
- **Batch di annotazione**: prepara prima le pagine scansionate (scan locale), poi avvia la GPU cloud, annota tutte le pagine in un blocco di 1 o 2 ore (costo totale ~$0.50), ed infine arresta la GPU.
- **Prezzi verificati (agosto 2026)** — vast.ai/pricing, runpod.io/pricing, modal.com/pricing:

| GPU | Vast.ai on-demand | Serverless |
|---|---|---|
| RTX 3090 (24 GB) | da 0,07 $/h · mediana 0,15 $/h | — |
| RTX 4090 (24 GB) | da 0,13 $/h · mediana 0,36 $/h | — |
| L4 (24 GB) | — | ≈ 0,80 $/h attiva (Modal), 0 $ a riposo |
| A10 (24 GB) | — | ≈ 1,10 $/h attiva (Modal), 0 $ a riposo |
| T4 (16 GB) | — | ❌ **non utilizzabile**: vLLM rifiuta bfloat16 su compute capability < 8.0 |

  Su Vast.ai le istanze **spot/interruptible** costano ulteriormente il 40–70% in meno,
  adatte al fine-tuning con checkpoint frequenti (il training center di Tabularium salva
  checkpoint ricaricabili). Attenzione: i default ufficiali di fine-tuning (batch 4,
  16384 token) chiedono ~26 GiB di VRAM e non entrano in una 24 GB — usa il preset a
  batch ridotto con gradiente accumulato (v. §2.6.1 di AGENTS.md).
- **Fine-tuning**: non serve una GPU serverless; 2–3 ore di RTX 3090/4090 su Vast.ai
  (da ~0,07–0,13 $/h) bastano per una run LoRA su questo corpus.
- **Strategia consigliata**: Vast.ai a ore per annotazione/prefill/training;
  Modal serverless per playground e valutazioni sporadiche.
