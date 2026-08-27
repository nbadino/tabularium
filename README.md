# Lloyds Lab

Dashboard web locale (multipiattaforma) per il **fine-tuning guidato di
[MonkeyOCRv2-Parsing](https://github.com/Yuliang-Liu/MonkeyOCRv2)** su giornali storici
**Lloyd's List** (1900s) con layout multi-colonna e tabelle dense (movimenti navali, casualties).

## Cosa fa

1. Registra le pagine dell'archivio (immagini/PDF) e le organizza per progetto.
2. Ti guida nell'annotazione **super-dettagliata**: blocchi semantici, **tabelle con celle
   unite**, **ordine di lettura**, trascrizione con convenzioni.
3. Esporta il dataset nel formato ufficiale ms-swift (JSONL, coordinate 0–1000, tabelle OTSL).
4. Genera e lancia il training (LoRA/SFT) con monitoraggio live (log, loss, GPU).
5. Valuta su pagine mai viste (TEDS, CER/WER, IoU layout, ordine di lettura) e itera.
6. L'interfaccia è **multilingua**: italiano, inglese e francese (switch nel rail,
   persistito per utente); anche i messaggi del backend (errori, warning, coda di
   lavoro) seguono la lingua scelta.

## Stato

- **M0 — Scaffolding** ✅ backend FastAPI + frontend Vite/React/TS/Tailwind + script setup/run (bash & PowerShell)
- **M1 — Progetti & pagine** ✅ CRUD progetto, scansione archivio (immagini + PDF), registro pagine con
  metadati (data/annata/pagina/tipo/stato), anteprime servite dal backend, pagina Progetti + Dettaglio progetto
  nel frontend
- **M2 — Studio di annotazione** ✅ canvas zoom/pan (Konva), rettangoli/poligoni, palette classi,
  ordine di lettura, undo/redo, autosave
- **M3 — Tabelle + convenzioni** ✅ editor tabellare con celle unite, OTSL verificato, crop per
  blocco, checklist convenzioni, preview "a fiume" dell'ordine di lettura
- **M4 — Dataset builder** ✅ 3 famiglie JSONL ms-swift (layout 0–1000, ritagli testo, tabelle
  OTSL), split per pagina, report con warning
- **M5 — Training center** ✅ generatore script ms-swift (LoRA/full), launch+stop, log e grafico
  loss live, telemetria GPU, resume dai checkpoint
- **M6 — Valutazione & playground** ✅ metriche layout/ordine/CER-WER/tabelle con overlay
  GT-vs-predetto, playground con analisi pagina via vLLM
- **M7 — Pseudo-labeling** ✅ prefill OCR (RapidOCR/PaddleOCR) con soglia di confidenza,
  badge "OCR", correzione guidata
- **M8 — Packaging & polish** ✅ frontend servito dal backend (**monoprocesso** `./scripts/run.sh`),
  **lazy-loading** pagine (bundle iniziale 257 kB vs 1 MB), strict TS riattivati, deep-link SPA,
  script build/run Windows+Linux, guida d'uso completa
- **M9 — Localizzazione** ✅ interfaccia **italiano/inglese/francese**: dizionario i18n tipato
  (`frontend/src/i18n/`), switch di lingua nel rail, plurali per lingua, e backend che risponde
  nella lingua della richiesta (`Accept-Language`) per errori, warning dataset/valutazione,
  coda di annotazione e preflight training.

La roadmap completa è in `AGENTS.md` (§12); la fonte di verità tecnica è `AGENTS.md`.
Le priorità operative emerse dalla review del workflow di ricerca sono in
[`TODO.md`](TODO.md).

## Struttura

```
backend/   FastAPI (Python ≥3.11)
frontend/  React + Vite + TypeScript (Tailwind)
conf/      template script training, schema labeling, convenzioni
scripts/   setup e avvio (bash + PowerShell)
```

## Quickstart

### Installazione (una volta sola)

```bash
# 1. backend (Python ≥3.11)
cd lloyds-lab
./scripts/setup_backend.sh

# 2. frontend (Node ≥20)
./scripts/setup_frontend.sh

# 3. (opzionale) OCR per il prefill — batasta una riga
#    backend/.venv/bin/pip install rapidocr-onnxruntime

# 4. (opzionale) dewarp neurale UVDoc — richiede Python 3.12 e PaddlePaddle
#    installare prima il wheel CPU/CUDA dalla guida ufficiale PaddlePaddle,
#    poi: backend/.venv/bin/pip install -r backend/requirements-uvdoc.txt
```

Il dewarp usa UVDoc solo quando il runtime PaddleOCR è disponibile; in caso
contrario `medium` e `high` applicano esclusivamente il deskew sicuro. Il
device si può impostare con `LLOYDS_UVDOC_DEVICE=cpu` oppure `gpu:0`. Ogni
risultato viene validato contro crop e variazioni anomale dei bordi prima di
essere salvato.

Per il livello `high` è disponibile anche DocScanner-L, il modello PyTorch
ufficiale per la rettifica non uniforme:

```bash
./scripts/setup_docscanner.sh
source .venv-uvdoc/bin/activate
uv pip install -r backend/requirements-docscanner.txt
```

Scaricare quindi `seg.pth` e `DocScanner-L.pth` dalla cartella indicata nel
README del repository ufficiale e copiarli in
`vendor/DocScanner/model_pretrained/`. Infine impostare:

```bash
export LLOYDS_DOCSCANNER_ROOT="$PWD/vendor/DocScanner"
# abilita DocScanner-L per il confronto sperimentale
export LLOYDS_DOCSCANNER_ENABLE=1
./scripts/run.sh
```

DocScanner-L è opt-in perché sulle scansioni d’archivio può introdurre onde:
con l’opzione disattivata `high` usa UVDoc, mentre con l’opzione attivata
esegue DocScanner-L e ricade automaticamente su UVDoc se runtime o pesi non
sono disponibili. La risposta dell’endpoint indica il motore effettivamente
utilizzato.

### Avvio (produzione — monoprocesso)

```bash
./scripts/run.sh          # apre http://localhost:8787  (Windows: .\scripts\run.ps1)
```

Su Linux/KDE, per avviare automaticamente backend e frontend al login e aprire la
dashboard quando pronta, esegui una volta:

```bash
./scripts/install_autostart.sh
```

Per disattivare l'avvio automatico:

```bash
./scripts/install_autostart.sh --remove
```

Un solo processo: FastAPI serve API **e** frontend buildato. Su impacchetto GPU/training,
vedi la sezione sotto e `AGENTS.md` §10.

### Avvio (sviluppo — hot reload)

```bash
./scripts/run_backend.sh   # terminale 1 → :8787
./scripts/run_frontend.sh  # terminale 2 → :5173 (proxy /api)
```

### Verifica

```bash
./scripts/test_backend.sh       # usa sempre backend/.venv/bin/python -m pytest
cd frontend && npm run test && npm run typecheck
# smoke browser (Chromium) e workflow sintetico completo
cd frontend && npm run test:e2e && npm run test:e2e:workflow
```

## Flusso d'uso raccomandato

1. **Progetti** → crea progetto sulla cartella dell'archivio → **Scansiona archivio**; in
   alternativa usa **Scegli cartella** per importare una directory dal browser (percorso manuale
   sempre disponibile);
   compila i metadati (data, annata, n., tipo) nel dettaglio progetto.
2. **Annotazione** → apri una pagina → se la pagina è **storta** premi **⇱ Deskew** (prima di
   annotare) → (opzionale) **✦ Prefill** per le bozze →
   correggi/disegna i blocchi con la **palette classi**, assegna **ordine di lettura**
   (frecce attivabili in toolbar), trascrivi i blocchi di testo (checklist convenzioni a
   destra); per i blocchi `Table` usa **▦ Editor tabella** (celle unite → OTSL), dove
   **Rileva griglia** propone righe e colonne dalla geometria della pagina.
3. **Dataset** → scegli l'unità di split (pagina, numero/data, annata o sorgente),
   l'adapter modello e se includere solo pagine approvate → **Costruisci dataset**:
   controlla warning, preflight e snapshot versionato dei JSONL.
4. **Training** → il preflight verifica dataset, repo/env e GPU; wizard (LoRA consigliato
   su VRAM ≤ 16GB, batch 2 se 8GB) → avvia e monitora via SSE loss/GPU; puoi stoppare e la
   run riprende dai checkpoint, mantenendo il riferimento allo snapshot dataset.
5. **Valutazione** → con il modello affinato servito da vLLM, confronta GT vs predetto
   (layout, ordine, CER/WER, tabelle) e scegli su quale fallimento tornare ad annotare.
6. **Playground** → prova il modello su qualsiasi pagina dell'archivio.

### Rilevamento della griglia (tabelle senza filetti)

I registri Lloyd's non hanno righe di riquadro: le colonne stanno insieme per composizione
tipografica e i campi sono legati da puntini di guida. I modelli di struttura tabellare
addestrati su tabelle moderne riquadrate qui rendono male. **Rileva griglia** nell'editor tabella
combina tre segnali e propone una bozza da verificare:

- il **passo tipografico** (autocorrelazione del profilo di inchiostro) dà le righe — le righe
  bianche fra i gruppi di voci restano gap più larghi e non diventano righe fantasma;
- i **gutter bianchi** danno i confini di colonna dimostrabili;
- gli **allineamenti ricorrenti** dei bordi di parola danno i confini restanti: una colonna
  allineata a destra produce un picco di bordi destri anche senza spazio bianco.

I **puntini di guida vengono soppressi** prima dell'analisi: sono inchiostro, quindi saldano
parole di colonne diverse e cancellano proprio i confini che servono.

Ogni confine esce con il proprio **supporto** (su quante righe è attestato) e quelli deboli sono
segnalati: il rilevatore propone e dichiara quanto è sicuro, non inventa struttura. I confini che
la geometria non può provare — due colonne adiacenti senza gutter né allineamento costante — si
aggiungono a mano trascinando i filetti.

Sulla pagina di riferimento `LSI_17186_015` il rilevatore trova il registro atteso, ma non è ancora
generalizzabile: su `_014` include anche la testata e su `_039`/`_11652` sbaglia il passo di riga.
Per questo i confini geometrici non vengono usati come verità automatica né per creare chunk.

### Riempimento delle celle

Il selettore accanto a **Rileva griglia** sceglie come riempirle:

| | Cosa fa | Serve la GPU |
|---|---|---|
| **solo struttura** | griglia vuota, trascrivi tu | no |
| **+ OCR per cella** | RapidOCR cella per cella | no |
| **+ MonkeyOCRv2** | struttura **e** contenuto dal modello | sì |

L'OCR per cella non fonde più le colonne — il riquadro arriva dalla griglia — ma sulle celle di
uno o due caratteri (`Flg`, `Reg`) i riconoscitori generici sbagliano spesso.

MonkeyOCRv2 può leggere meglio le celle molto corte, ma sulle tabelle storiche grandi usiamo due
strategie da confrontare, non una ricetta già dichiarata vincente:

- **END2END a 2 MP** legge in una generazione bbox, classi e contenuti dell'intera pagina. Ha già
  migliorato testata e titoli su `_014`, ma è lento e il registro OTSL completo va ancora misurato.
- **Crop tabella a bande** resta un fallback sperimentale. Le bande seguono soltanto `hlines`
  verificate dall'utente, si sovrappongono per poche righe e non attraversano rowspan. Il crop
  completo viene comunque mantenuto nel dataset.

La prima riga non è considerata automaticamente un'intestazione: spesso è già la prima nave. La
ripetizione su una banda è consentita solo dichiarando esplicitamente `header_rows`.

### Generi di pagina

`page_type` si deduce dal nome file e decide la ricetta di annotazione:

| `page_type` | Prefisso | Struttura | Ricetta |
|---|---|---|---|
| `index` | `LSI` | una tabella a piena pagina | `Title`, `Issue-number`, `Issue-date`, `Page-header`, `Table` + OTSL |
| `voyage-supplement` | `LSIVS` | colonne di schede-nave | `Column`, `Section-header`, `List-item` — niente OTSL |

Il **prefill** offre tre scelte, selezionabili accanto al pulsante nell'annotatore:

- `ocr` (RapidOCR/PaddleOCR) rileva **righe di testo** e le etichetta tutte `Text`. Non ha alcuna
  nozione di tabella: su una pagina indice produce centinaia di blocchi con le colonne fuse.
- `model / due stadi` (MonkeyOCRv2 via vLLM) esegue layout e poi riconoscimento per blocco;
- `model / END2END` usa il prompt ufficiale per bbox + label + contenuto in una sola generazione.
  Se una `Table` contiene OTSL valido lo salva direttamente; altrimenti dichiara e usa il fallback
  di riconoscimento sul crop.

Il motore `model` richiede `./scripts/serve_model.sh` attivo, e migliora a ogni ciclo di
fine-tuning: dal secondo giro il prefill usa il tuo modello affinato. Il dato di training sono le
**correzioni**, non l'output grezzo — reimmettere il predetto senza correggerlo insegna al modello
i suoi stessi errori.

Le annotazioni restano indipendenti dal modello: `monkeyocrv2-parsing` è il primo adapter,
mentre i profili di dominio in `conf/profiles/` permettono di estendere il workflow a nuovi
corpora senza perdere coordinate, tabelle, provenienza o stato QA.

Per uno studio riproducibile compila il **Protocollo studio** nel dettaglio progetto e assegna
8–12 pagine come **gold test** dal registro. Le pagine gold restano protette e vengono escluse
automaticamente da train/validation. Gli adapter e i plugin disponibili sono consultabili via
`/api/system/model-adapters` e `/api/system/plugins`; l’export canonico è in `exports/annotations.json`,
mentre `exports/layout.coco.json` serve per tool layout esterni.
Sono disponibili anche `annotations.page.xml`, `annotations.alto.xml` e `tables.html` per
interoperare con tool editoriali e archivi digitali.

Per il primo esperimento usa il pannello **Pilot iniziale** nel dettaglio progetto, salva il
campione bilanciato nel protocollo e attiva **Solo campione pilot salvato** in Dataset. Il report
indica esplicitamente `pilot_only` e lo snapshot rimane riproducibile tramite seed e manifest.
In alternativa, con il backend attivo, `python3 scripts/prepare_pilot.py <project_id>` esegue
la stessa preparazione e il preflight LoRA; non avvia il training automaticamente.

## Setup training (macchina GPU)

```bash
git clone https://github.com/Yuliang-Liu/MonkeyOCRv2 /percorso/MonkeyOCRv2
conda create -n monkeyocrv2-train python=3.11 -y && conda activate monkeyocrv2-train
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
pip install transformers==4.57.1 accelerate==1.11.0 qwen_vl_utils==0.0.14
pip install -e /percorso/MonkeyOCRv2/parsing/train/ms-swift

export LLOYDS_TRAIN_REPO=/percorso/MonkeyOCRv2
export LLOYDS_TRAIN_ENV=monkeyocrv2-train
./scripts/run.sh
```

Il modello affinato si serve con il parser ufficiale: `cd parsing && python serve.py -m <checkpoint> -p 8888`,
poi nella pagina Valutazione/Playground punta a `http://127.0.0.1:8888/v1`.

**Server di inferenza a portata di script**: `./scripts/serve_model.sh [modello] [porta]` avvia vLLM
sull'env `MonkeyOCRv2Parsing` con tutti gli accorgimenti già risolti (PATH env, `ninja`, `gcc-13`,
`NVCC_PREPEND_FLAGS`). L'endpoint di default del dashboard è già `http://127.0.0.1:8888/v1` (vedi `.env`).

## Nota hardware

L'annotazione e l'export funzionano ovunque (Windows/Linux/macOS). Il **training** richiede una
GPU NVIDIA con CUDA (environment dedicato `monkeyocrv2-train`; su macOS/CPU resta disponibile
solo la parte di preparazione dati).
