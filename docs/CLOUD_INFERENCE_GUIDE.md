> Per servire un modello sulla **GPU del tuo PC** invece che su cloud, vedi
> `docs/LOCAL_INFERENCE_GUIDE.md` (parametri vLLM verificati per ciascun
> modello, incluso il registro modelli "a piacere" in stile LM Studio).

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

## 2. Metodo 1: Vast.ai dal wizard di Tabularium (Scelta Consigliata)

Vast.ai è il marketplace GPU più economico (verificato 2026-08: RTX 3090 da 0,07 $/h
con mediana 0,15 $/h; RTX 4090 da 0,13 $/h con mediana 0,36 $/h).

**L'unica cosa che devi fare sul sito del provider è creare un account, ricaricare
il credito e generare una API Key.** Tutto il resto — chiave SSH, ricerca
dell'offerta, noleggio, preparazione del server, tunnel — avviene dentro
Tabularium: nessun terminale, nessuna console web, nessun commit da pubblicare.

### Passo 0: API Key (una sola volta, sul sito)

1. Crea l'account su [vast.ai](https://vast.ai/) e ricarica il credito (bastano $5).
2. Vai su **Account → API Keys** (`https://cloud.vast.ai/manage-keys/`) e crea una chiave.
   Non scegliere alcun template dalla libreria: l'immagine e lo script di avvio
   li impone Tabularium via API.

### Passo 1: Prima configurazione nel wizard

**Impostazioni → Modello e calcolo → Gestisci cloud → Vast.ai**: incolla la API
Key e premi **"Verifica account e prepara la chiave SSH"**. In un passo:

- valida la chiave e mostra il credito residuo (`GET /api/v0/users/current/`,
  campo `credit`);
- genera una coppia ed25519 dedicata in `data/ssh/tabularium_vast_ed25519` — la
  privata non lascia mai la macchina, l'API espone solo fingerprint e pubblica;
- registra la pubblica sull'account (`POST /api/v0/ssh/`), così ogni istanza
  nuova la riceve in automatico. L'operazione è idempotente;
- risolve il commit del runner ufficiale MonkeyOCRv2 da pinnare.

### Passo 2: Noleggia

Filtra per GPU e prezzo massimo, poi **"Noleggia"** (sempre con conferma
esplicita del costo). L'istanza nasce senza hook `onstart`: Tabularium la
prepara dopo, via SSH. Da qui il wizard interroga l'istanza ogni 8 secondi
finché il provider non pubblica host e porta SSH.

### Passo 3: Prepara e connetti

Il pulsante **"Prepara e connetti"** sull'istanza:

1. fissa la host key nel `known_hosts` dedicato (`ssh-keyscan`), perché il
   tunnel usa `StrictHostKeyChecking=yes`;
2. consegna `scripts/cloud/setup_cloud_vllm.sh` **sullo stdin di SSH da questo
   checkout** e lo avvia in background su `/var/log/tabularium_setup.log`. Il
   codice eseguito sulla GPU è quello che hai in locale, non una copia scaricata
   da GitHub;
3. mostra il log remoto nel modale finché vLLM non è in ascolto;
4. apre il tunnel, salva la configurazione di inferenza su
   `http://127.0.0.1:8888/v1` e la testa.

### Cosa fa lo script sull'istanza

`scripts/cloud/setup_cloud_vllm.sh`, in ordine, con il log su
`/var/log/tabularium_setup.log` (è quello che la UI mostra e da cui ricava le
fasi):

1. verifica GPU e compute capability (minimo 8.0: vLLM gira in bfloat16);
2. installa le dipendenze di sistema e clona il runner ufficiale al ref pinnato;
3. **crea un virtualenv dedicato** (`~/tabularium-venv`) e ci installa vLLM e
   PyTorch. Il venv non è un vezzo: le immagini su Ubuntu 24.04 hanno pip
   gestito dalla distro, che rifiuta sia l'auto-aggiornamento (`RECORD file not
   found`) sia gli install di sistema (PEP 668);
4. scarica i pesi del modello e scrive `cloud-manifest.json`;
5. avvia `serve.py` con i flag di serving.

Le versioni installate sono **allineate all'ambiente di serving locale
verificato** (`data/vllm-runtime`): vLLM 0.28.0, transformers 5.16.1, e con esse
`timm`, `einops`, `pillow`, `pydantic`, `huggingface_hub`. La coppia precedente
(vLLM 0.25.1 con transformers 4.51.3) non è più risolvibile da pip, che le
dichiara in conflitto.

### Prerequisiti locali

Servono i binari di OpenSSH (`ssh`, `ssh-keygen`, `ssh-keyscan`):
`scripts/setup_backend.sh` avvisa se mancano. Su Windows usa WSL2 o Git for
Windows.

### Note sull'API del provider

Vast.ai sta migrando per rotte, non in blocco: al 2026-09 la collection delle
istanze risponde solo su **`/api/v1/instances/`** (la v0 restituisce
`410 deprecated_endpoint`), mentre `users/current`, `ssh` e `search/asks`
esistono solo su **v0**. Tabularium usa la v1 per l'elenco e ricade sulla lista
v1 anche per il dettaglio se la rotta v0 dovesse sparire.

### Alternativa headless: hook `onstart`

Il percorso storico resta disponibile nelle API del backend
(`prepare_server: true` su `/api/system/cloud/vast/rent`) per gli usi non
interattivi: in quel caso l'istanza scarica lo script da GitHub e servono due
ref pin-nati (`monkeyocr_ref` e `tabularium_ref`, quest'ultimo un commit
**già pubblicato**). Il wizard non lo usa proprio per non dipendere da un push.

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
   export MONKEYOCR_REF=<commit-o-tag-monkeyocr-verificato>
   export TABULARIUM_REF=<commit-o-tag-tabularium-verificato>
   export TABULARIUM_SERVER_API_KEY=<token-del-server>
   curl -fsSL "https://raw.githubusercontent.com/nbadino/tabularium/${TABULARIUM_REF}/scripts/cloud/setup_cloud_vllm.sh" | bash -s -- --port 8888 --ref "$MONKEYOCR_REF"
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
   export MONKEYOCR_REF=<commit-o-tag-verificato>
   export TABULARIUM_SERVER_API_KEY=<token-del-server>
   bash setup_cloud_vllm.sh --port 8888 --ref "$MONKEYOCR_REF"
   ```
3. In Tabularium, inserisci l'URL pubblico di Vast.ai (es. `http://198.51.100.24:34567/v1`) e la chiave API.

---

## 5. Metodo 4: Modal Serverless (inferenza a chiamata)

Il metodo **pay-per-second**. Tutte le template Modal gestite dalla UI mantengono un container
caldo (`min_containers=1`) per eliminare il cold start e privilegiare la latenza; impostando
`TABULARIUM_MODAL_MIN_CONTAINERS=0` si torna al comportamento a consumo, con cold start alla
prima richiesta.
Il repo include una template pronta (`scripts/cloud/modal_vllm.py`)
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
- **Profilo performance**: per default usa il draft **MonkeyOCRv2-B-Parsing-DFlash** e la ricetta
  ufficiale vLLM **0.25.1 + CUDA 12.9**. DFlash è supportato solo dal checkpoint B-Parsing;
  conserva lo stesso modello target e accelera la decodifica senza cambiare il contenuto richiesto.
  Disattivalo solo con `TABULARIUM_MODAL_DFLASH=0` quando devi usare S-Parsing o un checkpoint
  fine-tuned diverso.
- **Concorrenza**: `TABULARIUM_MODAL_MAX_INPUTS=4` limita le richieste simultanee sul singolo
  container; aumentarlo migliora il throughput ma può peggiorare la latenza della singola pagina.
- **Trasporto Modal**: gli URL `.modal.run` restano in streaming per mostrare i token live. Il client
  continua a drenare la risposta fino a `[DONE]` anche quando il parser ha già trovato la lista completa;
  questo evita la chiusura chunked prematura che produceva `TransferEncodingError` senza mostrare token
  finali spuri. SSE e aggiornamento progressivo restano attivi fra backend locale e UI.
- **Tabelle a bande**: le bande indipendenti vengono richieste in parallelo sugli endpoint cloud
  (massimo quattro) e ricomposte nello stesso ordine; sulla macchina locale restano seriali per
  non aumentare il picco di VRAM.
- **Speculative decoding**: `TABULARIUM_MODAL_DFLASH_TOKENS=16` è il valore ufficiale del draft;
  lasciarlo invariato è il preset consigliato. Ridurlo può aiutare solo in caso di pressione VRAM.
- **GPU**: la template usa **L4** di default (≈ 0,80 $/h attiva; A10 ≈ 1,10 $/h) e **non la T4**:
  vLLM gira in bfloat16 e rifiuta le GPU con compute capability < 8.0
  (`ValueError: Bfloat16 is only supported on GPUs with compute capability of at least 8.0` —
  T4 = 7.5, V100 = 7.0). Le alternative serverless valide sono Ampere o successive.
- **Cold start**: con il profilo production il primo deploy/caricamento richiede alcuni minuti,
  poi il container resta caldo e pronto. Per risparmiare a riposo usa `MIN_CONTAINERS=0` e
  considera il ritardo della riaccensione.
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
