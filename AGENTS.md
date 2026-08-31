# AGENTS.md — Tabularium

Dashboard locale multipiattaforma per il **fine-tuning guidato di MonkeyOCRv2-Parsing**
su **giornali storici "Historic Shipping Index" (1900s)**, focalizzato su **layout multi-colonna complesso
e forma tabulare densa** (registri di movimenti navali, *casualties*, *maritime intelligence*).

Questo documento è la fonte di verità per chiunque (umano o agente IA) lavori su questo repo:
contiene necessità di prodotto, vincoli tecnici verificati sul repo ufficiale, architettura,
convenzioni dati, roadmap progressiva e regole operative.

---

## 1. Missione

Costruire uno strumento unico che accompagni l'utente dall'archivio di foto al modello affinato:

1. **Registra** le pagine dell'archivio (scansioni, PDF multi-pagina) con metadati (data, annata, pagina).
2. **Annota manualmente in modo guidato e super-dettagliato** la struttura del documento:
   blocchi semantici + **tabelle complesse con celle unite** + **ordine di lettura** + **trascrizione**.
3. **Esporta** il dataset nell'unico formato che l'addestramento ufficiale accetta
   (JSONL ms-swift, coordinate normalizzate 0–1000, tabelle in OTSL).
4. **Addestra** MonkeyOCRv2-Parsing (LoRA o full-SFT) generando e lanciando gli script
   ufficiali, con monitoraggio live di log, loss e GPU.
5. **Valuta** su pagine mai viste (layout/IoU, ordine di lettura, TEDS per tabelle, CER/WER per testo)
   e **itera**.

Vincolo trasversale: **multipiattaforma** (Windows/Linux/macOS) per la UI e la preparazione dati;
il training richiede GPU NVIDIA CUDA (su Linux; eventualmente WSL2 su Windows).

---

## 2. Contesto tecnico ufficiale (fatti verificati — da NON mettere in discussione senza proof)

### 2.1 Modello e stack di training
- Modello: `zenosai/MonkeyOCRv2-B-Parsing` (0.7B: ViT 113M congelato + LLM 0.6B) o `-S` (0.6B: ViT-S 28M).
- Stack di training: versione **monkeyocrv2-compatibile di ms-swift** bundlata in
  `parsing/train/ms-swift` del repo ufficiale `Yuliang-Liu/MonkeyOCRv2`.
- Comando canonico: `swift sft --model zenosai/MonkeyOCRv2-B-Parsing --model_type monkeyocrv2 --template monkeyocrv2 ...`
- Il ViT resta **congelato** di default (`--freeze_vit True`); sbloccarlo solo se lo shift
  visivo dei fogli d'epoca lo richiede (`--vit_lr` ridotto).

### 2.2 Formato dataset (assoluto, non negoziabile)
Una riga JSONL = un campione, formato `messages` multimodale ms-swift:

```json
{"messages": [{"role": "user", "content": "<image><PROMPT>"}, {"role": "assistant", "content": "<CONTENT>"}], "images": ["/absolute/path/to/image.jpg"]}
```

- Numero di `<image>` in `content` = numero di elementi in `images`. Percorsi **assoluti**.

### 2.3 Prompt ufficiali (autoritativi: da `parsing/core_runner.py` → `ALL_PROMPT`)
| Task | PROMPT | CONTENT |
|---|---|---|
| Layout | `Please output the categories and coordinates of the document elements in reading order.` | Lista di `{'bbox': [x1,y1,x2,y2], 'label': '...'}` nell'ordine di lettura |
| Testo | `Please output the text content from the image.` | Testo semplice, no Markdown |
| Formula | `Please write out the expression of the formula in the image using LaTeX format.` | LaTeX in `$...$` |
| Tabella | `Please extract the table from the image and represent it in OTSL format.` | OTSL (v. §2.5) |
| END2END | `List the document elements in reading order, including their categories, coordinates, and the content of each element.` | record con bbox+label+content |

### 2.3.1 Due percorsi ufficiali: due stadi ed END2END
Il runner predefinito usa `get_layout()` con `ALL_PROMPT["LAYOUT"]` e ottiene bbox + label; il
contenuto arriva da una seconda chiamata per blocco. Il repository espone però anche il prompt
ufficiale `END2END`, che in una sola generazione restituisce bbox + label + contenuto dell'intera
pagina. Non va quindi descritto il percorso a due stadi come l'unico percorso supportato.

Conseguenza pratica: **se il layout a due stadi etichetta `Text` un registro,
non vuol dire che il modello non sappia leggere le tabelle.** Basta ritagliare quel blocco e
interrogarlo con il prompt Tabella per ottenere OTSL corretto — colonne separate, celle di uno o due
caratteri incluse. Le due cose vanno valutate separatamente.

Sul corpus Historic Shipping Index END2END a 2 MP ha già corretto testata, titolo e sottotitolo della pagina `_014`
rispetto al percorso a due stadi, ma produce output molto lungo (~9k token) e il comportamento sul
registro OTSL completo deve ancora essere misurato. La UI espone entrambi: nessuno dei due diventa
default sulla base di una sola pagina.

### 2.3.2 Risoluzione delle immagini (`min_pixels` ≠ `max_pixels`)
In `load_image()` ufficiale `min_pixels` **ingrandisce** le immagini piccole e non riduce mai;
`get_layout()` lo passa a `1003520`. Un tetto superiore esiste solo se si imposta la env
`MOCR2_MAX_PIXELS`: **di default il codice ufficiale non riduce nulla.**

Da non confondere con `max_pixels 1003520` degli iperparametri di *training* (§2.6): è un altro
parametro, di un'altra fase. Usare quel valore come tetto in inferenza non è ciò che fa il codice
ufficiale.

Le scansioni d'archivio però stanno molto oltre la taglia per cui il pipeline è pensato, e il layout
degrada. Misurato su `LSI_17186_015` (2864×3952 = 11.3 MP), a parità di tutto il resto:

| Pixel inviati | Blocchi | Esito |
|---|---|---|
| 11.3 MP (nessun tetto) | 2 | riquadri sovrapposti, inutilizzabili |
| 6.0 MP | 16 | frammentato |
| 4.0 MP | 137 | esploso a livello di cella |
| **2.0 MP** | **5** | `Title`, numero, data, corpo — corretto |
| 1.0 MP | 4 | corretto, meno granulare |

Perciò `services/inference.py` impone un tetto di 2 MP **alla sola chiamata di layout**
(`LAYOUT_MAX_PIXELS`, override con `TABULARIUM_VLLM_MAX_PIXELS`). Sui ritagli di testo e tabella non si
riduce: sono già piccoli e ridurli cancellerebbe i caratteri.

### 2.3.3 Tabelle grandi: intero come verità, bande come esperimento
I chunk ciechi non sono una strategia sicura: possono spezzare righe, rowspan e contesto di
colonna. Il dataset conserva sempre il crop completo della tabella. Come augmentazione opzionale
può aggiungere bande di righe logiche sovrapposte, ma solo quando l'annotazione contiene `hlines`
verificate; i boundary che attraversano un rowspan sono vietati. Non si usano filetti visibili.
Il rilevatore geometrico è stabile su tutte e quattro le pagine del campione da quando ricava le
righe dalle linee di base dei glifi (v. §2.3.4), ma resta una **proposta da correggere**, non una
verità: le `hlines` valgono come verificate solo dopo il passaggio dell'annotatore.

In inferenza il percorso a bande resta un fallback sperimentale del riconoscimento per crop. La
prima riga non viene più trattata implicitamente come intestazione (su molti registri è già una
nave); un'eventuale intestazione ripetuta deve essere dichiarata esplicitamente con `header_rows`.
END2END a 2 MP deve essere confrontato con questo fallback su pagine gold senza sovrascriverle.

> Nota: il README di fine-tuning ufficiale suggerisce di annotare le tabelle in **HTML** e poi
> convertire con `html2otsl.py`; a inferenza il modello viene interrogato **direttamente in OTSL**.
> Quindi i dati di training devono contenere OTSL. Il builder esporterà OTSL generato dal grid
> di annotazione (+ opzione HTML intermedio per fedeltà al flusso ufficiale).

### 2.3.4 Rilevamento della griglia: contratto e dominio d'uso
`services/table_detect.detect_grid()` propone la struttura di un ritaglio `Table`. Non salva
niente e non è una verità: è una bozza che l'annotatore accetta, sposta o rifiuta.

**Righe dalle linee di base, non dal profilo.** Il passo tipografico e le righe si ricavano dalle
componenti connesse (istogramma dei *fondi* dei glifi), non dall'autocorrelazione del profilo di
inchiostro. Il percorso a proiezione reggeva su una pagina su quattro e si rompeva in tre modi
distinti, tutti riprodotti nei test di regressione (`tests/test_table_detect.py`): armonica del
periodo (`LSI_8447_014`, 156 px invece di 39), profilo saturo per interlinea stretta
(`LSI_1974_039`), spalmatura da inclinazione (`LSIVS_11652_006`, −1,43°).

| Campo | Significato |
|---|---|
| `vlines` / `hlines` | Confini normalizzati 0–1 **sul ritaglio**, crescenti, `cols+1` e `rows+1`. |
| `column_support` | Su quante righe è attestato ciascun confine verticale. I due estremi valgono per definizione il numero di righe: non sono attestati, sono i bordi del contenuto. |
| `warnings` | Codici stabili, mai prosa: il backend parla inglese, la UI traduce (§13.11). Oggi solo `skewed`. |
| `diagnostics` | Misure che rendono verificabile l'esito: `pitch_px`, `glyph_height_px`, `glyphs`, `otsu`, `shear`, `skew_deg`, `row_bands`, `leader_dots_suppressed`, `gutters`, `content_x`. |

**Binarizzazione con Otsu**, non a soglia fissa: sul corpus la soglia cade fra 142 e 152, quindi
128 perdeva sistematicamente i tratti sottili. Convenzione `gray <= t`, come `cv2`.

**L'inclinazione si compensa, non si ruota.** Lo scorrimento `tan θ` corregge le coordinate dei
glifi e trasla i profili di riga; l'immagine non viene mai ruotata, così i confini restituiti
restano nel sistema di riferimento del ritaglio. Ma un `hline` è orizzontale per contratto: oltre
~0,2° non può più seguire la riga di testo, e il rilevatore emette `skewed` perché la pagina va
raddrizzata con *⇱ Deskew* prima di annotare. L'avviso è un limite dichiarato, non un errore.

**I confini piegano.** Una colonna di questi registri non sta ferma: composta a mano e ripresa
da una pagina curva, deriva orizzontalmente scendendo. Misurato sul campione, ogni confine
interno si sposta fra 25 e 77 px dall'alto al basso, cioè fra 0,6 e 2,0 passi tipografici,
mentre una cifra è larga una ventina di pixel — quindi **nessuna retta può descrivere la
tabella**: un taglio dritto alla mediana finisce dentro un numero e ne manda metà nella cella
accanto. Su `LSI_8447_014` succedeva sul 10% dei tagli.

`snap_boundaries()` porta perciò ogni confine, riga per riga, nel varco di bianco migliore
entro 1,5 passi, pesando la larghezza del varco contro la distanza dalla retta. Dove un varco
esiste — l'86–96% dei tagli sul campione — **nessuno dei tagli spezza un valore**; dove non
esiste, la retta resta e il taglio è marcato non provato. Complessivamente da 5,7% a 0,5% di
valori spezzati.

Due conseguenze di contratto:
- `diagnostics.row_columns` porta i confini piegati (0–1, per riga) e `row_columns_proven` dice
  quali sono attestati da un varco; `row_columns_unproven` li conta. Servono a **disegnarli** e
  a riempire le celle, e non sono ancora persistiti: il grid salvato porta solo le rette.
- `fill_cells(..., snap=True)` usa i confini piegati e marca `uncertain` le celle il cui
  confine non è provato. Vanno passati anche `shear`, altrimenti i varchi si cercano nel
  sistema di riferimento sbagliato.

Non c'è un avviso «la colonna deriva»: deriva su tutte e quattro le pagine del campione (dal 7%
al 41% di tagli non provati), quindi scatterebbe sempre. La deriva non è una condizione della
scansione ma una proprietà per cella, e come tale viene contata e marcata.

**Dominio d'uso.** Vale su un ritaglio che è **una sola tabella allineata a spazi**. Su una pagina
a più colonne di giornale (`LSIVS_11652_006`, supplemento a cinque colonne) restituisce una griglia
priva di senso, e **non sa accorgersene**: entrambe le ipotesi verificabili sono state misurate e
respinte — la larghezza dei gutter è invertita rispetto all'attesa (0,62 passi lì contro 1,07 del
gutter *interno* più largo di una tabella singola) e le linee di base non si disallineano, perché
le cinque colonne sono composte sulla stessa griglia tipografica. Un avviso che scatta sulle pagine
sbagliate insegna a ignorare gli avvisi, quindi non ce n'è uno. Il caso è risolto dal flusso: su una
pagina simile l'utente disegna cinque blocchi `Column`, non un blocco `Table`.

**Dipendenze.** Solo `numpy` e `Pillow`, come tutto il backend: le componenti connesse sono
implementate nel modulo (run + union-find) e verificate contro `cv2` nei test, senza che `cv2`
diventi una dipendenza dichiarata.

### 2.4 Coordinate (CRITICO — verificato in `core_runner.py::_map_bbox_to_image`)
- Il modello emette i bbox **normalizzati in scala 0–1000 per asse** (x e y indipendentemente).
- A inferenza i valori sono riportati ai pixel con `x / 1000 * width`, `y / 1000 * height`.
- **Quindi**: le annotazioni si memorizzano in pixel della sorgente, e l'export converte in 0–1000:
  `round(px / width * 1000)`, `round(py / height * 1000)`.
- Coordinate integer; `x1<x2`, `y1<y2` sempre (l'ordine dei punti è top-left/bottom-right).

### 2.5 OTSL (sintassi estratta da `core_runner.py::otsl_to_html`)
- Token per cella: `<fcel>testo</fcel>` (cella piena), `<ecel></ecel>` (cella vuota ma *valida*),
  `<lcel></lcel>` (estende di 1 il `colspan` della cella valida a sinistra),
  `<ucel></ucel>` (estende di 1 il `rowspan` della cella valida sopra),
  `<xcel></xcel>` (cella invalida).
- Separatore di riga: `<nl>`.
- Encoding di un grid (= matrice fisica): visita **row-major**; la prima cella di una regione
  unita è `fcel`/`ecel`, le celle coperte a destra sono `lcel`, quelle sotto sono `ucel`.
- Le celle fisiche non coperte e parte di un merge 2D (sia lato che sopra) richiedono attenzione
  → modulo `otsl.py` dedicato con test contro `otsl_to_html` ufficiale.

### 2.6 Iperparametri ufficiali (default canonicali — da template `parsing/train/scripts/*.sh`)
```
max_length 16384 | max_pixels 1003520 | num_train_epochs 1 | learning_rate 1e-5
lr_scheduler cosine | warmup_ratio 0.05 | deepspeed zero1 | torch_dtype bfloat16
gradient_checkpointing True | train_type lora (rank 8, alpha 32, target all-linear) | freeze_vit True
per_device_train_batch_size 4 | gradient_accumulation_steps 1 | save_steps 500 | save_total_limit 2
```
- `eval_steps` ufficiale è enorme (= non si valuta in training); noi aggiungeremo un val split.
- Lo script di resume (ricerca ultimo `checkpoint-N` in `output_dir`) va preservato.

### 2.6.1 Quanta VRAM chiede davvero (e perché il preset ufficiale non basta)
Gli iperparametri di §2.6 sono quelli canonici del repo e restano il riferimento, ma sono
scritti per la scheda su cui è stato addestrato il modello, non per una qualunque. Il conto,
implementato e testato in `services/vram.py`:

| Termine | Formula | batch 1, 16384 token |
|---|---|---|
| **Logit** | `B × S × vocab × 2` | **4,6 GiB** |
| Pesi (bf16, dal disco) | — | 2,0 GiB |
| Attivazioni salvate (checkpointing) | `L × B × S × H × 2` | 0,9 GiB |
| Ricalcolo di uno strato | `B × S × (H + 3·I) × 2` | 0,3 GiB |
| LoRA + Adam (rank 8) | fp32 sugli adattatori | ~0,1 GiB |

Il termine dominante è il **vocabolario**, non i pesi: `MonkeyOCRv2-B-Parsing` ha
`vocab_size` 151936 su `hidden_size` 1024, quindi la matrice dei logit è 148 volte più larga
dello stato nascosto. «È un modello da 0,7B, ci sta comodo» è quindi un'intuizione sbagliata:
ci stanno comodi i pesi, non la testa di uscita a lunghezza piena.

Conseguenza su una GPU da 8 GB (RTX 4060 Laptop, il caso di riferimento):

| Configurazione | Stima | Esito |
|---|---|---|
| Ufficiale: batch 4, 16384 | 26,2 GiB | non entra |
| batch 1, 16384 | 8,3 GiB | non entra |
| **batch 1, 8192, grad_accum 4** | **5,4 GiB** | **entra** (preset `gpu8`) |
| batch 1, 4096 | 3,9 GiB | entra |
| Full-SFT, batch 1, 4096 | 15,5 GiB | non entra |

Full-SFT non è una strada su 8 GB e non lo diventa accorciando la sequenza: sono gli stati
dell'ottimizzatore su tutti i pesi a non entrare. LoRA resta comunque la scelta giusta ai
volumi di dati previsti.

`trainer.preflight()` calcola la stima e **blocca** la run quando non entra, indicando la
`max_length` più grande che entra davvero (bisezione sulla stessa stima, così il consiglio non
può contraddirla). La stima è deliberatamente un **limite inferiore**: ignora frammentazione
dell'allocatore, buffer DeepSpeed, il picco fp32 della cross-entropy e il contesto CUDA. Serve
a dire «non ci sta» con sicurezza, non «ci sta» con sicurezza — perciò c'è un margine del 25%
prima di dichiarare che va bene.

Nota su `--deepspeed zero1`: su una sola GPU non partiziona niente e aggiunge dipendenza e
buffer. Resta nello script per fedeltà al repo ufficiale, ma non è ciò che rende eseguibile
una run da 8 GB.

### 2.7 Label pubbliche del parsing
`Caption, Footnote, List-item, Page-footer, Page-header, Section-header, Text, Title, Formula, Table, Picture`
→ estese con classi custom giornale (v. §8): `Issue-number, Issue-date, Column, Headline, Byline, Advertisement, Note`.
Le classi custom sono token: il modello le impara durante il fine-tuning. Ogni classe non-testo
richiede un prompt di riconoscimento definito nella config di progetto.

**Preferire sempre la label pubblica quando descrive la stessa cosa fisica**: il modello base la
conosce già, quindi è transfer gratuito, mentre una classe custom parte da zero e sottrae esempi
alle altre. `Title`, `Page-header`, `Section-header` e `List-item` sono entrate in tassonomia per
questo motivo.

---

## 3. Requisiti funzionali (vista utente)

1. **Progetti**: crea progetto, scegli cartella archivio scansioni, registra pagine (immagini/PDF),
   metadati data/annata/pagina/tipo di pagina, anteprima.
2. **Studio di annotazione** (cuore del prodotto):
   - **rettifica geometrica revisionabile**: *Allinea e confronta* genera una proposta senza
     toccare annotazioni o master attivo; motori distinti `deskew`, `uvdoc`, `docscanner`,
     prospettiva manuale a quattro angoli e mesh manuale. Originale e proposta restano affiancati
     fino ad *Accetta come master*; solo l'accettazione, previa conferma, elimina eventuali blocchi
     le cui coordinate diventerebbero invalide. Il master è PNG lossless e diventa l'unica sorgente
     per canvas, Prefill, crop ed export; JPEG solo per preview/tile. Metadati e API dichiarano
     motore richiesto/reale, fallback, warning ed errore diagnostico. Il Prefill non riesegue mai
     il deskew sull'originale quando esiste un master accettato;
   - zoom/pan fluido su scansioni ad alta risoluzione (server serve tile/anteprime downsampled).
     Convenzioni gesto (allineate ai visualizzatori di documenti): rotella/due dita = pan,
     Ctrl/Cmd+rotella (e pinch) = zoom centrato sul puntatore, Space/middle-drag = pan
     temporaneo, Ctrl+0 = adatta, Ctrl± = zoom. I delta wheel sono normalizzati a pixel
     (`wheelDeltaPixels`) e il fattore zoom è esponenziale con clamp (`wheelZoomFactor`,
     testati in `canvasGeometry.test.ts`);
   - le tre colonne dello studio (pagine | canvas | contenuti) sono ridimensionabili tramite
     `Splitter` (drag, frecce ±16px, doppio click = reset); le larghezze sono persistite in
     `localStorage['tabularium.studio.split']`;
   - strumenti blocco: rettangolo e poligono; palette classi con colori; edit/resize/delete;
   - **editor tabellare**: righe/colonne, **celle unite (merge)**, colonne "fantasma" dove i
     filetti sono sbiaditi, trascrizione cella-cella via tastiera, anteprima HTML live;
   - **reading order**: numerazione dei blocchi di pagina + ordine interno alle tabelle;
     preview "a fiume" (frecce) che segue l'ordine per validazione visiva;
   - **trascrizione** dei blocchi testo con checklist convenzioni attiva;
   - **prefill via pseudo-labeling**, motori/modalità distinti e non intercambiabili:
     - `ocr` (RapidOCR/PaddleOCR): rileva **righe di testo** e le etichetta `Text`. Non ha alcuna
       nozione di tabella, ma con `table_promote=True` (default) il più grande cluster di righe
       consecutive la cui geometria dimostra una griglia viene **promosso a blocco `Table`** con
       griglia e celle precompilate cella per cella (le colonne fuse non servono); le righe fuori
       dal cluster (testata, numero del fascicolo) restano `Text`. Le celle con testo nascono
       `source=ocr`, `verified=false`. Utile sulle pagine di prosa e, con la promozione, anche sui
       registri; disattivabile per richiesta.
     - `model/two_stage` (MonkeyOCRv2 via vLLM): layout, poi riconoscimento per blocco;
     - `model/end2end`: una generazione ufficiale bbox+label+content. Se il contenuto `Table` è OTSL
       valido viene salvato direttamente; altrimenti ricade esplicitamente sul crop tabella.
     In tutti i casi il dato di training sono le **correzioni** dell'utente, non l'output grezzo:
     ogni cella/blocco precompilato nasce non verificato, e il report d'export distingue le due
     cose (`table_cells.verified` / `prefill_unverified`). Reimmettere il predetto senza correggerlo
     insegna al modello i suoi stessi errori;
     - **modalità di sostituzione esplicite**: il prefill non cancella mai senza che l'utente
       scelga. `merge` aggiunge; `replace_drafts` rimuove solo le bozze non confermate
       (`prefill_source` non nullo **e** `confirmed=0`), mai il lavoro umano; `replace_all`
       cancella tutto, griglie comprese, e richiede conferma nella UI (`replace` resta come
       alias storico di `replace_all`). La risposta dichiara `replaced_blocks`/`replaced_grids`;
       su pagina piena il pulsante Prefill apre un dialog di conferma (`PrefillDialog`);
   - autosave, stato avanzamento per pagina, scorciatoie da tastiera;
   - **autosave e navigazione**: il cambio pagina/progetto attende `flush()` dello store
     (`useAnnotationState` espone `dirty`/`getDirty`/`flush`); lo smontaggio con lavoro
     sporco salva in emergenza. Il debounce da solo protegge solo la chiusura del browser.
3. **Dataset builder**: genera le 3 famiglie JSONL (§7), split **per pagina** e mai per ritaglio,
   normalizzazione 0–1000, mappa classi, stat e validazioni (box fuori pagina, classi senza prompt, ecc.).
4. **Training center**: wizard che genera `full.sh`/`lora.sh` dalle template ufficiali, check
   sistema (GPU/VRAM/CUDA), lancio, **log streaming (SSE) + grafico loss + uso GPU**, stop,
   resume dai checkpoint.
5. **Valutazione & iterazione**: layout GT vs predetto overlay, metriche §11, analisi fallimenti.
6. **Playground**: prova del modello affinato su nuove pagine (via vLLM).
7. **Istanza self-hosted** (auth/utenti/impostazioni):
   - **primo avvio**: schermata di setup che crea l'amministratore dell'istanza
     (un solo account, poi login normali); registrazione **chiusa di default**
     (`TABULARIUM_REGISTRATION_OPEN=0`), apribile dall'admin nelle Impostazioni;
   - **login/logout**: sessioni via cookie HttpOnly + SameSite=Strict
     (`tab_session`) con fallback `Authorization: Bearer`; reset password di un
     utente invalida tutte le sue sessioni aperte;
   - **ruoli globali**: `admin` (governa tutto, anche utenti/impostazioni),
     `editor` (crea e lavora sui progetti), `viewer` (legge). Il backend è la
     fonte di verità: la UI disabilita solo i comandi che finirebbero in un 400;
   - **accesso ai progetti**: `projects.owner_id` + `project_members` per
     condividere un progetto con altri utenti (editor/viewer). Regole sensibili
     protette lato server: niente auto-declassamento, niente eliminazione
     dell'ultimo admin, 404 per i progetti altrui (esistenza nascosta);
   - **Impostazioni istanza** (solo admin): nome, registrazione aperta/chiusa,
     ruolo predefinito per i nuovi account;
   - **gestione utenti** (solo admin): creazione, cambio ruolo/attivazione,
     reset password, eliminazione.

---

## 4. Stack & architettura

Studio scelto: **web app locale** = backend Python **FastAPI** + frontend **React/Vite/TS**.
Il backend è l'unico processo che l'utente avvia; serve la UI e orchestra tutto. Nessuna dipendenza
PyTorch nel processo dashboard (il training gira in env separati).

### Backend
- Python ≥3.11, FastAPI, Uvicorn, Pydantic v2.
- Storage: **SQLite** (modello §6) per progetti/annotazioni; filesystem per immagini e dataset.
- Pillow + numpy per elaborazione immagini (thumbnails, tiles, crop, deskew placeholder).
- `subprocess` per orchestrazione training/valutazione; `nvidia-smi`/`psutil` per telemetria GPU.
- Client HTTP (requests/httpx) verso server **vLLM** (inferenza/prefill/eval).
- Librerie opzionali (import lazy): `rapidocr-onnxruntime` o `paddleocr` per il prefill.
- **Autenticazione** (solo stdlib, nessuna dipendenza): PBKDF2-HMAC-SHA256 per le
  password, token di sessione conservati come hash SHA-256 in SQLite, cookie
  HttpOnly + SameSite=Strict. Modalità locale `TABULARIUM_AUTH=off` per i test.
  Permessi per ruolo globale e per progetto in `services/auth.py` +
  `api/deps.py::require_resource`; routing protetto a livello router.

### Frontend
- React 19 + Vite + TypeScript + Tailwind CSS 4 + React Router 7 + Recharts (grafici).
  Stato globale via **piccoli store modulari** (`useSyncExternalStore`, senza
  Zustand): `app/i18n`, `app/auth` — il pattern `useSyncExternalStore` è quello
  canonico del progetto.
- Canvas di annotazione: **Konva.js** (shape editing, transformer) per i blocchi;
  overlay/grid tabellare in SVG o canvas custom (componente `TableCellsEditor`).
- Nessun framework "tutto pronto": UI costruita su componenti nostri.
- **Auth lato UI**: `AuthGate` decide cosa mostrare all'avvio (setup admin,
  login o app), `api.ts` reagisce ai 401 azzerando l'utente, `Layout` espone
  menu utente (nome + ruolo + logout, Impostazioni/Utenti solo per l'admin).

### Componenti riusati (non fork)
- `ms-swift` + script `full.sh` / `lora.sh` ufficiali (template; l'app le genera parametrizzate).
- `html2otsl.py` ufficiale (converter HTML→OTSL, richiamabile).
- Convenzioni dati ufficiali §2 (JSONL, OTSL, coordinate).

---

## 5. Struttura repository (obiettivo finale)

```
tabularium/
  AGENTS.md
  README.md
  backend/
    app/
      main.py               # entry FastAPI, mount static frontend, health
      config.py             # settings (paths host, env names, repo checkout path, HF cache)
      db.py                 # schema SQLite + DAO
      schemas.py            # Pydantic models
      api/
        deps.py             # require_resource(write=...) path-based, permessi progetto
        auth.py             # setup, login, logout, me, register, status
        users.py            # CRUD utenti (admin) + reset password
        settings.py         # impostazioni istanza (PUT admin)
        projects.py
        pages.py
        blocks.py           # annotazioni (blocchi) + tassonomia label
        prelabel.py
        datasets.py         # export JSONL famiglie + split + stats
        training.py         # wizard scripts, run, SSE log, stop, resume
        evaluate.py
        playground.py
      services/
        security.py         # hash password (PBKDF2), token di sessione (SHA-256)
        auth.py             # login/sessioni + require_role/require_project_access
        images.py           # thumbnails/tiles/EXIF/deskew
        pages.py            # anteprime/thumbnail on-demand per pagina
        scan.py             # scansione archivio (immagini + PDF lazy)
        labeling.py         # tassonomia label Historic Shipping Index + prompt
        blocks.py           # CRUD blocchi + reading order
        tables.py           # CRUD grid tabelle (cells/merges) + HTML preview
        otsl.py             # grid → OTSL (+ test contro otsl_to_html)
        dataset_builder.py  # annotazioni → 3 JSONL (regole §7)
        trainer.py          # subprocess, log tail, telemetria, resume
        inference.py        # client vLLM OpenAI-compatibile
      tests/
    requirements.txt        # core; varianti opzionali: requirements-dev.txt,
                            # requirements-uvdoc.txt, requirements-docscanner.txt
  frontend/
    src/
      app/                  # routes, layout, shell (Layout, AuthGate, auth, i18n, ui)
      pages/                # Home, Projects, Annotation, Dataset, Training, Evaluation,
                            # Playground, Login, Setup, Settings, Users
      studio/
        canvas/             # zoom-pan-canvas, tools (Block/Table/Order/Transcribe)
        components/         # LayersPanel, Inspector, ClassPalette, TableCellsEditor, FlowPreview, ConventionsChecklist
        state/              # store annotazioni + history (undo/redo)
      lib/                  # api.ts, coords.ts (coordinate 0-1000), otsl.ts (preview), types.ts
    package.json
  conf/                     # template full.sh/lora.sh, labeling_schema.yaml, conventions.yaml
  scripts/
    setup_backend.sh / .ps1 # env + dipendenze
    setup_frontend.sh / .ps1
    run.sh / run.ps1        # avvio locale (backend+frontend build/preview)
    train_on_gpu.sh         # helper per lanciare training in env GPU dedicato
    cloud/                  # offloading inferenza su GPU remota: setup_cloud_vllm.sh,
                            # ssh_tunnel.sh, vast_onstart.sh, test_cloud_connection.py,
                            # modal_vllm.py (deployment Modal serverless, vLLM >= 0.25,
                            # GPU Ampere+ per il bf16 — la T4 non è servibile)
  data/                     # (gitignored) progetti locali
```

---

## 6. Modello dati (SQLite)

- `projects(id, name, root_dir, owner_id, created_at, settings_json)` — settings: classi+colori+prompt map,
  convenzioni trascrizione, modello base, max_pixels/max_length, split ratio, py/conda env names;
  `owner_id` = chi ha creato il progetto (vedi permessi sotto).
- `pages(id, project_id, rel_path, abs_path, width, height, issue_date, issue_no, page_no,
  page_type, status, meta_json)`.
- `blocks(id, page_id, label, bbox_px_json [x1,y1,x2,y2 pixel], content, order_idx, kind
  ['text','table','formula','picture','custom'], prefill_source, confirmed, updated_at)`.
- `tables(id, block_id, grid_json)` — `grid_json`: `{rows: n, cols: m, cells:[{r,c,rowspan,colspan,
  text, source, verified}], vlines:[...], hlines:[...]}` (griglia fisica; gli span sono i merge;
  `source` = chi ha scritto il testo della cella, `verified` = conferma umana — solo le celle con
  testo sono interessate).
- Coordinate **memorizzate in pixel sorgente**; normalizzazione a 0–1000 **solo in export** (§7).
- `annotation_state(id, page_id, user, ...)` per avanzamento multi-pagina.
- **Auth (SCHEMA_VERSION 6)**:
  - `users(id, username UNIQUE, password_hash, email, role ['admin','editor','viewer'],
    active, created_at, last_login_at)`;
  - `sessions(id, user_id, token_hash, expires_at)` — token casuale a 32 byte,
    conservato **solo hashato**; cookie `tab_session` (HttpOnly, SameSite=Strict);
  - `project_members(project_id, user_id, role ['editor','viewer'])` — accesso
    condiviso ai progetti; `projects.owner_id` è l'altro lato (solo l'owner elimina);
  - `settings(id, instance_name, allow_registration, default_new_user_role)` — singola riga;
  - migrazioni: le versioni fino alla **baseline 6** restano additive per
    presenza di colonna (`db.py::_ensure_columns`); da lì in poi ogni cambiamento
    di schema è una migrazione **versionata e ordinata** in `db.py::_MIGRATIONS`
    (versione → DDL), applicata una sola volta alle sole basi più indietro,
    con versione registrata in `meta` e errore visibile all'avvio se la catena
    è incompleta. Test di upgrade in `tests/test_db_migrations.py`. I test
    auth girano su
    `TABULARIUM_AUTH=off` per la suite storica e `on` (fixture) per `test_auth.py`.

---

## 7. Dataset export — 3 famiglie JSONL

Export atomico per progetto; split train/val **per pagina** (default 90/10, seed fisso).

1. **`layout_train.jsonl` / `_val`** — immagine pagina intera + prompt LAYOUT. CONTENT =
   lista dict in ordine di lettura: `[{'bbox': [x1,y1,x2,y2], 'label': '...'}, ...]` con coord 0–1000.
2. **`text_rec.jsonl`** — un campione per blocco di testo/headline/etc.: crop del blocco (PNG/JPEG
   salvato in `data/<project>/crops/`) + prompt testo + trascrizione.
3. **`table_otsl.jsonl`** — crop completo della tabella + prompt OTSL + OTSL generato dal grid.
   In modalità sperimentale si aggiungono crop per bande di righe logiche, mai al posto del crop
   completo e solo con `hlines` verificate; report e conteggi distinguono `full` e `bands`.
4. (opzionale) `formula.jsonl`, `end2end.jsonl`.

Regole:
- I crop vanno generati una volta e cache-ati (stesso percorso assoluto nei JSONL).
- Path immagini **assoluti**; il builder stampa un report di sanità (count per famiglia/classi,
  box fuori pagina, blocchi senza trascrizione, classi senza prompt).
- Celle vuote `ecel` valide incluse; blocco tabella senza cella testuale non esportato.
- Il bulk autosave aggiorna i blocchi esistenti preservandone gli ID: eliminare e reinserire farebbe
  scattare `ON DELETE CASCADE` e perderebbe le griglie tabellari associate.

---

## 8. Tassonomia Historic Shipping Index & mappa prompt (default di progetto)

| Label (interna) | Esportata | Prompt riconoscimento (classe non-layout) |
|---|---|---|
| Title | `Title` | testo |
| Page-header | `Page-header` | testo |
| Issue-number | `Issue-number` | testo |
| Issue-date | `Issue-date` | testo |
| Column | `Column` | — (solo layout; helper) |
| Headline | `Headline` | testo |
| Byline | `Byline` | testo |
| Text | `Text` | testo |
| Section-header | `Section-header` | testo |
| List-item | `List-item` | testo |
| Advertisement | `Advertisement` | testo |
| Note/Adder | `Note` | testo |
| Table | `Table` | OTSL |
| Formula | `Formula` | LaTeX |
| Picture | `Picture` | — |

**Perché numero e data sono classi separate** (e perché non esiste una classe `Date`): la data del
fascicolo è il campo più importante del corpus — ogni movimento nave è relativo a essa — e il corpus
attraversa almeno tre formati di testata fra il 1940 e il 1973 (masthead grande con numero a sinistra
e data al centro; riga unica «HISTORIC SHIPPING INDEX, Mon., May 20, 1940.»; Voyage Supplement con
data a sinistra e indice a pollice + numero di pagina a destra). Etichettarla direttamente evita una
regex di estrazione per ciascun formato. Una classe `Date` generica sarebbe invece incoerente: le
date dentro le celle sono centinaia per pagina e nessuna label di layout può raggiungerle.

**Regola di confine**: una classe di layout non spezza mai una riga tipografica. Dove data e testata
sono composte sulla stessa riga (formato 1940) la data resta dentro `Title` e si recupera dalla
trascrizione; disegnare un riquadro a metà riga su una virgola è il confine che i modelli di layout
sbagliano di più.

### 8.1 Generi di pagina (`pages.page_type`)

Il corpus non è omogeneo e i due generi vogliono ricette diverse. Il tipo si deduce dal prefisso del
nome file (`backend/app/services/page_meta.py`) e va valorizzato **prima** di annotare.

| `page_type` | Prefisso | Struttura | Ricetta |
|---|---|---|---|
| `index` | `LSI` | una tabella a piena pagina, senza filetti | `Title`, `Issue-number`, `Issue-date`, `Page-header`, `Table` + OTSL |
| `voyage-supplement` | `LSIVS` | colonne parallele di schede-nave | `Page-header`, `Issue-date`, `Column`, `Section-header`, `List-item` — **niente OTSL** |

`issue_date` non si deduce dal nome file e **non va mai presa dall'EXIF**: quella è la data di
digitalizzazione. Finisce in `meta_json.scan_date`.

La mappa è in `conf/labeling_schema.yaml` e modificabile per progetto. Le classi riconoscibili
(teste alla `ALL_PROMPT` §2.3) compaiono nel LAYOUT e nei JSONL di crop; le classi pure-struttura
solo nel LAYOUT.

---

## 9. Convenzioni di trascrizione (config per progetto)

Checklist sempre visibile nell'annotatore (esempio default Historic Shipping Index):
- Espandere i *soft hyphen* di fine riga riassemblando le parole spezzate.
- Conservare grafia/sigle originali (`inst.`, `ult.`, `barq.`, `psgr. stmr.`, maiuscoletto).
- Nomi di navi in corsivo → segnare `*nave*` (configurabile) o testo piano.
- Segni di colonna/colonnini sbiaditi: la colonna fantasma si annota, non si trascrive.
- Trattini/linee di riga nelle tabelle: rappresentati dalla griglia, mai nel testo.
Le violazioni generano warning in export. Centrali per la coerenza del dataset.

---

## 10. Training center

- Il wizard genera (dalle template in `conf/`) `full.sh`/`lora.sh` con env vars, resume
  (logica `get_latest_checkpoint` ufficiale) e i flag §2.6, puntando ai JSONL esportati e al
  modello base scelto, con `--split_dataset_ratio` ≥ 0.05 per val loss.
- Esecuzione in env dedicato (conda `monkeyocrv2-train` o venv) sulla macchina GPU (o remoto via
  SSH, milestone futura). La dashboard: lancia, **streama log via SSE**, parse dei log per loss
  e learning rate (JSONL dei run), telemetria GPU (nvidia-smi sampling), stop, resume.
- Percorsi chiave: `output_dir = checkpoints/monkeyocrv2_lora` (o `_full_sft`),
  `MODELSCOPE_CACHE = checkpoints/cache`, log in `lora/train_*`/`full_sft/train_*`.
- A fine run: merge dei LoRA Adapter (ms-swift) in un checkpoint servibile da vLLM (`serve.py`).

---

## 11. Valutazione

Su val split (pagine mai viste), serviti da vLLM con il checkpoint affinato:
- **Layout**: mAP-like per label, IoU media per blocco, accuratezza label;
- **Ordine di lettura**: Levenshtein normalizzato tra sequenze di label GT e predette (+
  percentuale di ordini perfetti);
- **Tabelle**: **TEDS** (Tree Edit Distance Similarity) GT-vs-predetto;
- **Testo**: **CER/WER** per blocco.
Output: report JSON + HTML, con overlay pagine GT vs predetto (risposta: `--draw-layout`) e
tabella dei fallimenti peggiori per guidare la nuova iterazione di annotazione.

---

## 12. Roadmap progressiva (milestone)

- **M0 — Scaffolding** ✅ — struttura repo, AGENTS.md, README, requirements/package.json, run scripts,
  scheletro FastAPI + Vite funzionanti (health | ping | info ambiente).
- **M1 — Progetti & pagine** ✅ — CRUD progetto, scan cartella (immagini+PDF, PDF lazy via pypdfium2),
  registro pagine con metadati (issue_date/issue_no/page_no/page_type/status), thumbnail/preview on-demand,
  pagina Progetti + Dettaglio progetto (griglia pagine, edit metadati inline). Test end-to-end coperti.
- **M2 — Studio annotazione core** ✅ — canvas Konva zoom/pan (zoom rotellina, pan con tool Panoramica),
  strumenti rettangolo/poligono, palette classi, ispezione/label/trascrizione base, spostamento/ridimensionamento
  con Transformer, **ordine di lettura** (indici con spostamento su/giù + riordino automatico), delete, undo/redo
  (Ctrl+Z/Y), autosave (PUT bulk, debounce 700ms), scorciatoie da tastiera (V/R/P/H). API blocchi:
  GET/PUT `/api/pages/{id}/annotations`, PATCH/DELETE `/api/blocks/{id}`, GET `/api/projects/{id}/labels`.
  Il PUT bulk conserva gli ID ricevuti e quindi le tabelle collegate durante l'autosave.
  Nota: modulo rinominato `blocks.py` (collisione con `from __future__ import annotations`).
- **M3 — Tabelle + reading order + trascrizione** ✅ — `TableCellsEditor` (griglia righe/colonne,
  **celle unite** con modalità Unisci/Seleziona/Separa, colonne fantasma, trascrizione cella-cella
  con Tab, resize griglia), **generazione OTSL** (`services/otsl.py`, round-trip testato contro
  l'oracolo ufficiale `otsl_to_html`), crop per blocco (`GET /api/blocks/{id}/crop`), tabella per
  blocco (`GET/PUT /api/blocks/{id}/table` con OTSL in risposta), **checklist convenzioni**
  (`GET/PUT /api/projects/{id}/conventions`), **preview "a fiume"** dell'ordine di lettura sul canvas.
  Nota tecnica: l'OTSL ufficiale **non usa tag di chiusura** (`<fcel>text<lcel><nl>…`).
- **M4 — Dataset builder** ✅ — `services/dataset_builder.py`: genera le 3 famiglie JSONL
  (layout pagina intera con coordinate **0–1000** e ordine di lettura, text_rec con ritagli+trascrizione,
  table con OTSL, formula opzionale), **split per pagina** deterministico (ratio+seed), crop su disco
  con path assoluti, crop tabella completo + bande logiche sperimentali solo con `hlines` verificate,
  report con warning di sanità persistito in `dataset/report.json`.
  API: `POST/GET /api/projects/{id}/datasets[/build]`. Pagina Dataset con slider split, seed,
  statistiche, warning, file e anteprime righe JSONL.
- **M5 — Training center** ✅ — `services/trainer.py`: generazione script `bash` dalla template
  ufficiale (`swift sft` con flag §2.6 + `--val_dataset` + resume dai checkpoint), merge train/val
  dalle famiglie (`prepare_training_files`), lancio in subprocess con log su file, monitoraggio
  metriche (loss/lr parsate dal log), telemetria GPU (nvidia-smi), stop, stato persistito in
  `runs/<run_id>/{run.json,train.sh,train.log}`. API: `POST /api/projects/{id}/training/{start,stop}`,
  `GET …/status`, `GET …/stream` (SSE), `GET /api/system/gpu`. Pagina Training: wizard config,
  grafico loss (Recharts), GPU cards, log live con polling.
- **M6 — Valutazione & playground** ✅ — `services/inference.py` (client vLLM OpenAI-compatibile:
  layout tollerante, riconoscimento testo/tabelle, ping), `services/evaluate.py` (metriche:
  IoU+label layout, Levenshtein ordine di lettura, CER/WER testo, struttura+CER tabelle; split del
  val riusato dal builder; report JSON in `eval/eval_<ts>/`). API: `POST /api/projects/{id}/evaluate`,
  `POST /api/playground/parse`. Pagine: Valutazione (cards aggregate, per-pagina, overlay GT vs
  predetto, errori pagine) e Playground (layout predetto overlay SVG + blocchi/contenuto, copia md).
  Degrada con warning se il server vLLM non è attivo.
- **M7 — Pseudo-labeling** ✅ — `services/ocr.py` (import lazy RapidOCR/PaddleOCR, config
  `TABULARIUM_OCR_ENGINE`), `POST /api/projects/{id}/prelabel`: rileva righe di testo su una pagina,
  filtra per confidenza/dimensione + NMS-lite, inserisce blocchi `Text` con `prefill_source` e
  `order_idx`, modalità replace/merge. `BlockOut` ora espone `prefill_source`. UI: pulsante
  La modalità modello espone sia `two_stage` sia l'ufficiale `end2end`; OTSL END2END valido evita
  una seconda chiamata, altrimenti il risultato dichiara il fallback sul crop tabella.
- **M8 — Utilizzo reale & polish** ✅ — frontend `dist` **servito dal backend** (monoprocesso
  `scripts/run.sh`; mount `/assets` + catch-all SPA con deep-link, API prioritarie), **lazy-loading**
  delle pagine (bundle iniziale −63%, 257 kB), `noUnusedLocals`/`noUnusedParameters` riattivati,
  script `build_frontend`/`run` (bash+PowerShell), guida d'uso e setup GPU nel README,
  benchmark d'uso = flusso §README. Nota: accorpare la build di produzione (Vite) prima di
  disinstallare `frontend/node_modules` se si ricompila.

Ogni milestone termina con la sezione "Verifica": cosa lanciare per testare (vedi §13).

---

## 13. Regole operative per l'agente

1. **Scrivere codice nella lingua del progetto**: commenti/README in italiano, identificatori e
   log in inglese. Documentazione in italiano.
2. **Non alterare mai** il repo ufficiale `Yuliang-Liu/MonkeyOCRv2` (referenziarlo come checkout
   esterno configurabile in `config.py`); copiare *template* copiandole in `conf/`, mai includerle.
3. **Verifiche contro l'ufficiale**: coordinate 0–1000, prompt §2.3, OTSL §2.5, JSONL §2.2.
   In caso di dubbio, ricontrollare `core_runner.py`, `parsing/train/README.md`, `scripts/*.sh`.
4. Implementare **in ordine di milestone**; ogni MR deve aggiornare `AGENTS.md`/README se cambiano
   convenzioni o struttura.
5. **Test**: backend con pytest (particolare cura a `otsl.py`, `dataset_builder.py`,
   normalizzazione coordinate); frontend con un build `tsc --noEmit` + `vite build` senza errori
   e test Vitest (unitari + componenti con jsdom/testing-library per i flussi a rischio:
   autosave/cambio pagina, conferma prefill). La CI (`.github/workflows/ci.yml`) esegue test
   backend, typecheck + test + build frontend e verifica che la build non generi artefatti
   versionati.
6. **Esecuzione**: backend `uvicorn app.main:app --port 8787`; frontend dev `vite` con proxy verso
   il backend; MAI far dipendere l'app dal training in esecuzione.
7. **Percorsi**: mai hard-coded; tutto da `config.py` (env var `TABULARIUM_ROOT`).
8. **Data safety**: i progetti creati dall'utente sono dati reali; nessuna distruzione automatica
   di dati; ogni operazione distruttiva richiede conferma esplicita nella UI e nel codice.
9. I merge/coordinate delle tabelle devono essere validati: fare riferimento all'algoritmo
   `otsl_to_html` ufficiale come oracle nei test.
10. Log e metriche dei run di training in `data/<project>/runs/<run_id>/` (JSONL + .log + plot data),
    così la UI è always-replayable anche a training terminato.
11. **Localizzazione (obbligatoria da ora)**: l'interfaccia è multilingue **it/en/fr**. Nessuna
    stringa UI hardcoded: ogni testo passa dal dizionario i18n
    (`frontend/src/i18n/it.ts` = sorgente di verità della struttura; `en.ts`/`fr.ts` identici,
    tipati come `Dict`). Segnaposto `{var}` identici nelle tre lingue; plurali come `{one, other}`
    (il francese usa il singolare per 0 e 1). Lo switch di lingua è nel rail (persistito in
    `localStorage['tabularium.locale']`). Il backend localizza i messaggi che finiscono in UI
    (dettagli HTTPException, warnings dataset/eval, errori readiness, reason della coda,
    preflight training) tramite `Accept-Language` e `backend/app/services/i18n.py`
    (default italiano; `localize_detail` riconosce i dettagli esistenti dal catalogo inverso).
    Il client API manda sempre `Accept-Language: <locale>`.
12. **Auth come difesa, non come ornamento**: ogni nuova rotta entra protetta dal
    `require_resource` del suo path (o da un controllo esplicito quando l'id del
    progetto è nel body). Regole che non si devono mai regalare lato client: niente
    auto-declassamento/auto-disattivazione, niente eliminazione dell'ultimo admin,
    niente reset password del proprio account, 404 (non 403) per i progetti altrui.
    Password e token solo hashati in DB; sessioni con scadenza; `TABULARIUM_AUTH`
    resta `off` **solo** per i test.
    stringa UI hardcoded: ogni testo passa dal dizionario i18n
    (`frontend/src/i18n/it.ts` = sorgente di verità della struttura; `en.ts`/`fr.ts` identici,
    tipati come `Dict`). Segnaposto `{var}` identici nelle tre lingue; plurali come `{one, other}`
    (il francese usa il singolare per 0 e 1). Lo switch di lingua è nel rail (persistito in
    `localStorage['tabularium.locale']`). Il backend localizza i messaggi che finiscono in UI
    (dettagli HTTPException, warnings dataset/eval, errori readiness, reason della coda,
    preflight training) tramite `Accept-Language` e `backend/app/services/i18n.py`
    (default italiano; `localize_detail` riconosce i dettagli esistenti dal catalogo inverso).
    Il client API manda sempre `Accept-Language: <locale>`.

---

## 14. Riferimenti

- Repo modello: https://github.com/Yuliang-Liu/MonkeyOCRv2
- Fine-tune Parsing: https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/parsing/train (README + scripts)
- Fine-tune Und: https://github.com/Yuliang-Liu/MonkeyOCRv2/tree/main/understanding/train
- Parsing pipeline (coordinate/prompt/OTSL): `parsing/core_runner.py`, `parsing/parse.py`
- Modelli: https://huggingface.co/zenosai/MonkeyOCRv2-B-Parsing · `-S-Parsing`
- Paper: https://arxiv.org/abs/2607.11562
