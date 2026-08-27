# Guida all'Offloading dell'Inferenza su Cloud (Vast.ai, RunPod, Modal)

Questa guida spiega come eseguire **Lloyds Lab interamente sul tuo PC locale** (scansioni, annotazioni, database SQLite, interfaccia web) **delegando tutta l'inferenza pesante (layout, tabelle OTSL, OCR e playground)** a una potente **GPU remota nel Cloud** (es. NVIDIA RTX 4090 / 3090 / A5000 / A100).

---

## 1. Come Funziona l'Architettura Ibrida

```
┌─────────────────────────────────────────────────────────────┐
│                    TUO PC LOCALE (Laptop / Desktop)         │
│  - FastAPI Backend (porta 8787)                             │
│  - React UI nel browser                                     │
│  - Scansioni storiche e Database SQLite (lloyds.db)         │
│  - Zero uso di GPU locale (CPU fredda, ventole spente)      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               │ (Richiesta HTTP/HTTPS con crop base64)
                               │ (via Tunnel SSH o Proxy HTTPS)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             CLOUD GPU INSTANCE (Vast.ai / RunPod)           │
│  - GPU NVIDIA RTX 4090 (24 GB VRAM) ~ $0.25 - $0.35 / ora   │
│  - Server vLLM OpenAI-compatibile con MonkeyOCRv2-B-Parsing │
│  - Risposta in streaming sub-secondo (>80 token/sec)        │
└─────────────────────────────────────────────────────────────┘
```

I vantaggi:
- **Zero requisiti hardware locali**: puoi annotare e usare il modello da un MacBook Air, un PC portatile senza scheda video dedicata o un mini-PC.
- **Velocità massima**: una RTX 4090 remota genera layout e tabelle istantaneamente.
- **Costo irrisorio**: ~0.25$ all'ora. Per 100 pagine di annotazione spendi pochi centesimi, e quando finisci spegni o fermi l'istanza.

---

## 2. Metodo 1: Vast.ai con Tunnel SSH (Scelta Consigliata)

Vast.ai è il marketplace GPU più economico al mondo ($0.20–$0.35/h per RTX 3090/4090).

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
   curl -fsSL https://raw.githubusercontent.com/cappannonno/lloyds-lab/main/scripts/cloud/setup_cloud_vllm.sh | bash
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

### Passo 4: Collega Lloyds Lab
1. Apri la Home di **Lloyds Lab** nel browser.
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
   curl -fsSL https://raw.githubusercontent.com/cappannonno/lloyds-lab/main/scripts/cloud/setup_cloud_vllm.sh | bash -s -- --port 8888 --api-key "chiave-segreta-tua"
   ```

### Passo 3: Collega Lloyds Lab con l'URL HTTPS di RunPod
RunPod assegna automaticamente un URL pubblico protetto da SSL:
`https://<POD_ID>-8888.proxy.runpod.net/v1`

1. Inserisci l'URL in Lloyds Lab: `https://<POD_ID>-8888.proxy.runpod.net/v1`
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
3. In Lloyds Lab, inserisci l'URL pubblico di Vast.ai (es. `http://198.51.100.24:34567/v1`) e la chiave API.

---

## 5. Verifica Rapida da Terminale (CLI Test)

Puoi testare qualsiasi endpoint remoto con il nostro strumento CLI:

```bash
# Test rapido di disponibilità e latenza:
python3 scripts/cloud/test_cloud_connection.py --url http://127.0.0.1:8888/v1

# Test completo con ritaglio sintetico e misura dei token/secondo:
python3 scripts/cloud/test_cloud_connection.py --url http://127.0.0.1:8888/v1 --sample
```

Output di esempio:
```
>> [Lloyds Lab] Verifica Connessione Cloud / Remote vLLM
>> Target URL: http://127.0.0.1:8888/v1
============================================================
[1/2] Test disponibilità endpoint (/models)...
  ✓ Connessione riuscita! Latenza ping: 24.3 ms
  ✓ Modelli disponibili sul server: MonkeyOCRv2-B-Parsing

[2/2] Esecuzione inferenza di test con ritaglio sintetico...
  ✓ Inferenza completata in 410.5 ms!
  ✓ Token generati: 48 (~117.0 token/s)
>> [SUCCESS] Il server Cloud è pronto e compatibile con Lloyds Lab!
```

---

## 6. Ottimizzazione Costi: Come Risparmiare

- **Ferma l'istanza quando non annoti**: sia su Vast.ai che su RunPod puoi mettere in pausa l'istanza. In pausa paghi solo lo storage (~0.05$/giorno).
- **Batch di annotazione**: prepara prima le pagine scansionate (scan locale), poi avvia la GPU cloud, annota tutte le pagine in un blocco di 1 o 2 ore (costo totale ~$0.50), ed infine arresta la GPU.
- **GPU consigliate**:
  - **NVIDIA RTX 4090** (24GB VRAM): massima velocità, ~$0.30/h.
  - **NVIDIA RTX 3090** (24GB VRAM): eccellente rapporto qualità/prezzo, ~$0.20/h.
  - **NVIDIA A5000 / A6000**: ideali per sessioni prolungate nei datacenter.
