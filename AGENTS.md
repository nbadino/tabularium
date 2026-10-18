# AGENTS.md — Tabularium

Ambiente locale multipiattaforma per il **riconoscimento documentale e il fine-tuning guidato di MonkeyOCRv2-Parsing**
su **giornali storici "Historic Shipping Index" (1900s)**, focalizzato su **layout multi-colonna complesso
e forma tabulare densa** (registri di movimenti navali, *casualties*, *maritime intelligence*).

Questo documento è la fonte di verità per chiunque (umano o agente IA) lavori su questo repo:
contiene necessità di prodotto, vincoli tecnici verificati sul repo ufficiale, architettura,
convenzioni dati, roadmap progressiva e regole operative.

---

## 1. Missione

Costruire uno strumento unico che accompagni l'utente dall'archivio di immagini al testo
strutturato e, quando serve, al modello affinato. La UX primaria è indipendente
dall'infrastruttura: si sceglie modello e provider una volta, poi riconoscimento, revisione ed
export restano identici in locale, su Vast.ai, Modal o altri endpoint compatibili.

1. **Registra** le pagine dell'archivio (scansioni, PDF multi-pagina) con metadati (data, annata, pagina).
2. **Riconosce** una o molte pagine in sessioni persistenti, esportabili oppure revisionabili
   successivamente anche con l'inferenza disattivata.
3. **Annota manualmente in modo guidato e super-dettagliato** la struttura del documento:
   blocchi semantici + **tabelle complesse con celle unite** + **ordine di lettura** + **trascrizione**.
4. **Esporta** risultati e dataset nell'unico formato che l'addestramento ufficiale accetta
   (JSONL ms-swift, coordinate normalizzate 0–1000, tabelle in OTSL).
5. **Addestra** MonkeyOCRv2-Parsing (LoRA o full-SFT) generando e lanciando gli script
   ufficiali, con monitoraggio live di log, loss e GPU.
6. **Valuta** su pagine mai viste (layout/IoU, ordine di lettura, TEDS per tabelle, CER/WER per testo)
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

Sul corpus Historic Shipping Index END2END a 2 MP ha già corretto testata, titolo e sottotitolo di una
pagina del campione rispetto al percorso a due stadi, ma produce output molto lungo (~9k token) e il comportamento sul
registro OTSL completo deve ancora essere misurato. La UI espone entrambi: nessuno dei due diventa
default sulla base di una sola pagina.

### 2.3.2 Risoluzione delle immagini (`min_pixels` ≠ `max_pixels`)
In `load_image()` ufficiale `min_pixels` **ingrandisce** le immagini piccole e non riduce mai;
`get_layout()` lo passa a `1003520`. Il tetto superiore è la env `MOCR2_MAX_PIXELS`, che il CLI
ufficiale `parse.py` imposta **sempre** dal suo `--max-pixels` (default `1003520`) e propaga a tutte
le chiamate, end2end compresa. Minimo e massimo coincidono: **il layout ufficiale lavora a 1 MP.**

Da non confondere con `max_pixels 1003520` degli iperparametri di *training* (§2.6): è un altro
parametro, di un'altra fase, anche se il valore coincide.

Le scansioni d'archivio però stanno molto oltre la taglia per cui il pipeline è pensato, e il layout
degrada. Misurato su una pagina `index` del campione (2864×3952 = 11,3 MP), a parità di tutto il resto:

| Pixel inviati | Blocchi | Esito |
|---|---|---|
| 11.3 MP (nessun tetto) | 2 | riquadri sovrapposti, inutilizzabili |
| 6.0 MP | 16 | frammentato |
| 4.0 MP | 137 | esploso a livello di cella |
| 2.0 MP | 5 | `Title`, numero, data, corpo — corretto |
| **1.0 MP** | **4** | corretto, meno granulare — **default ufficiale** |

Il default MonkeyOCRv2 è il valore ufficiale: `1003520`, coerente con `MOCR2_MAX_PIXELS`.
Questo limite appartiene alla ricetta MonkeyOCRv2 e non va applicato agli altri VLM, che devono
conservare il proprio preprocessore nativo quando la ricetta non dichiara un tetto. La granularità
in più a 2 MP era una nostra deviazione misurata su una pagina, non il protocollo del checkpoint:
chi la rivuole imposta `TABULARIUM_VLLM_MAX_PIXELS=2000000` (override globale) o il campo
*max pixels* del singolo modello in Impostazioni. `TABULARIUM_VLLM_MAX_PIXELS=0` toglie il tetto; in quel caso resta
`LAYOUT_MAX_PIXELS` (2 MP) come sola rete di sicurezza sul layout, perché a 11 MP collassa.

### 2.3.4 Preprocessore ufficiale delle pagine
`parsing/core_runner.py` non manda mai la pagina grezza al VLM: costruisce un `Preprocessor`
(pesi `preprocessor1.pth`/`preprocessor2.pth`, già dentro il checkpoint) e vi passa ogni pagina
prima di layout e riconoscimento. Il CLI ufficiale lo salta solo con `--skip-preprocess`,
documentato come «this may lead to worse accuracy but faster speed».

In Tabularium è il motore di trasformazione pagina `monkeyocr` (`services/monkey_preprocess.py`),
**non** uno stadio nascosto dentro `inference.py`: il preprocessore cambia la geometria e i bbox
normalizzati 0–1000 valgono sull'immagine che il modello ha ricevuto, quindi applicarlo di nascosto
farebbe atterrare i blocchi su pixel diversi da quelli del canvas. Passando dal master di pagina
resta valido il contratto «prefill, crop ed export osservano gli stessi pixel del canvas», che è
anche ciò che fa il pipeline ufficiale (salva le pagine preprocessate e da lì lavora solo su quelle).

È l'**unica** rettifica neurale: UVDoc e DocScanner-L sono usciti. Erano surrogati scelti prima che
quello del modello fosse raggiungibile, e tenerli in parallelo significava poter preparare le pagine
in un modo diverso da come il modello si aspetta di riceverle.

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

### 2.3.4 Rilevamento della griglia: contratto e dominio d'uso — **DEPRECATO**
> **Deprecato.** L'intero percorso geometrico è in via d'uscita: Tabularium è model-agnostic e i
> modelli non ci danno la geometria del ritaglio (`vlines`/`hlines`/`row_columns`) — su 248 griglie
> salvate una sola la porta. La correzione della struttura passa dal foglio (unisci/separa,
> riga/colonna), non dai confini sul pixel. Restano in servizio, marcati nel codice, solo il
> pulsante «Rileva griglia»/ri-rilevamento al resize dello Studio e la promozione a `Table` del
> prefill OCR. La superficie di editing dei confini sul ritaglio (`TableGridOverlay`) e le sue
> chiavi i18n sono deprecate e non montate. Il testo sotto resta come nota storica tecnica: non
> costruire sopra questo modulo.

`services/table_detect.detect_grid()` propone la struttura di un ritaglio `Table`. Non salva
niente e non è una verità: è una bozza che l'annotatore accetta, sposta o rifiuta.

**Righe dalle linee di base, non dal profilo.** Il passo tipografico e le righe si ricavano dalle
componenti connesse (istogramma dei *fondi* dei glifi), non dall'autocorrelazione del profilo di
inchiostro. Il percorso a proiezione reggeva su una pagina su quattro e si rompeva in tre modi
distinti, tutti riprodotti nei test di regressione (`tests/test_table_detect.py`; nel resto del
documento le pagine d'archivio del campione sono indicate con lettere): armonica del
periodo (pagina `B`, 156 px invece di 39), profilo saturo per interlinea stretta
(pagina `C`), spalmatura da inclinazione (pagina `D`, −1,43°).

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
accanto. Su pagina `B` succedeva sul 10% dei tagli.

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
a più colonne di giornale (pagina `D`, supplemento a cinque colonne) restituisce una griglia
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

1. **Progetti**: crea progetto, importa immagini/PDF dal browser oppure scegli una cartella sorgente sul server, registra pagine,
   metadati data/annata/pagina/tipo di pagina, anteprima.
2. **Studio di annotazione** (cuore del prodotto):
   - **rettifica geometrica revisionabile**: *Allinea e confronta* genera una proposta senza
     toccare annotazioni o master attivo; l'unica rettifica neurale è `monkeyocr`, il
     preprocessore ufficiale del modello (§2.3.4), affiancato dal solo `deskew` rotazionale
     e dalle correzioni manuali prospettiva a quattro angoli e mesh. Originale e proposta restano affiancati
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
     - **dove vivono le bozze**: nel pannello contenuti, non sull'immagine — sul canvas non
       compare nulla di generato finché qualcuno non lo verifica. La **classe** di una bozza si
       cambia dal pannello (`onDraftLabel` → `PATCH /blocks/{id}`): non essendo sul canvas,
       è lì che si ri-etichetta, per esempio quando una data finisce dentro `Page-header` e va
       promossa a `Issue-date`. Le **tabelle** fanno
       eccezione, e la ragione è misurata: la regione è ciò che il layout sbaglia più spesso
       (sulla pagina `index` da 11,3 MP del campione, PaddleOCR-VL propone come `Table` la sola colonna di sinistra e
       lascia *From/For/Latest Report* fuori, come `Text`), e i confini della griglia si
       muovono solo **dentro** il ritaglio, che è il bbox del blocco: senza riquadro
       sull'immagine la correzione è impossibile. Una bozza di tabella sta quindi sul canvas,
       tratteggiata e più chiara di un'annotazione, e si ridimensiona come qualsiasi blocco.
       Al ridimensionamento la griglia **si rifà** sulla regione nuova (`/table/detect`,
       riempimento OCR quando c'è un motore locale): conservarla sarebbe peggio che
       perderla, perché `vlines`/`hlines` sono normalizzate sul ritaglio e su un ritaglio
       diverso indicano un altro punto dell'inchiostro. Se una cella trascritta è già
       `verified` il rifacimento non parte da solo: l'avviso chiede il permesso;
     - **le bozze non si cancellano da sole**: non essendo sul canvas non arrivano mai nel
       payload dell'autosave, quindi `sync_annotations` non può dedurne la cancellazione
       dall'assenza (lo faceva, e la prima modifica al canvas dopo una run di prefill
       distruggeva tutte le bozze con le loro griglie). Si scartano con `DELETE
       /blocks/{id}`, con `replace_drafts`, o dichiarandole in `deleted_ids` nel salvataggio
       bulk — è così che il canvas cancella una tabella: chi cancella lo dice, e un id
       tornato fra gli `items` (annullamento) vince sulla cancellazione;
     - **revisione della pagina**: ogni scrittura che non passa dall'autosave —
       `PUT /blocks/{id}/table`, `PATCH /blocks/{id}` — fa avanzare `annotation_revision` e
       la restituisce nella risposta (`TableSaveOut.annotation_revision`,
       `BlockOut.annotation_revision`). Il client la riallinea con
       `useAnnotationState.syncRevision`: senza, il primo autosave successivo si vedrebbe
       respingere con 409 un conflitto che non esiste;
     - **hint di schema tabella**: il progetto può dichiarare l'intestazione del registro (es.
       «Year e Tons sono unite su una riga sopra») in `settings_json["table_hint"]`
       (`GET/PUT /api/projects/{id}/table-hint`; nell'apposita modale dello Studio). Il testo è
       accodato al prompt di riconoscimento tabella **solo** dai modelli che lo dichiarano
       accettabile (`ModelCapabilities.accepts_prompt_hint`): oggi il solo Qwen3-VL, l'unico
       eseguibile in locale su Apple Silicon con questa proprietà e quindi l'unico verificabile
       senza un endpoint remoto. La famiglia a prompt in linguaggio naturale
       (MonkeyOCRv2/GLM-OCR/TeleOCR) è il candidato naturale, ma resta spenta finché non
       misurata: i motori a prompt di protocollo o pipeline nativa (PaddleOCR-VL, MinerU2.5,
       DeepSeek-OCR-2, Unlimited-OCR) invece lo ignorano per costruzione, e un gate errato
       avrebbe mandato testo fuori distribuzione a un modello che non lo capisce. Il hint è un
       suggerimento, non una verità: resta affiancato dalla riparazione deterministica
       dell'intestazione (`services/table_headers.py::repair_flat_header`), che non dipende
       dall'obbedienza del modello. Sperimentale: da misurare su una pagina `index` prima di
       considerarlo utile.
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
6. **Prova modello** (`/playground`): prova temporanea del modello in uso oppure OCR locale su una pagina; per salvare, correggere ed esportare si passa a Riconosci.
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
- Canvas di annotazione: **Konva.js** (shape editing, transformer) per i blocchi.
- Editor tabellare: **Jspreadsheet CE** (MIT) — `studio/components/JspreadsheetSheet.tsx`.
  Porta le interazioni Excel-like che mancavano (menù contestuale con inserisci/togli riga e
  colonna, selezione a intervallo, copia/incolla multi-cella, fill handle) senza che il modello
  dati diventi suo: `lib/gridSheet.ts` traduce `TableGrid` ↔ foglio nei due sensi, l'OTSL lo
  genera il server dal nostro modello, e i metadati per cella (`source`/`verified`) vivono nei
  `meta` della libreria. Le modifiche **strutturali** non passano dall'API della libreria: la
  struttura nuova la calcola `lib/grid.ts` e il foglio si rimonta — v. M3.
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
                            # requirements-uvdoc.txt (PaddleOCR: motore OCR e
                            # runtime PaddleOCR-VL, non più il dewarp)
  frontend/
    src/
      app/                  # routes, layout, shell (Layout, AuthGate, auth, i18n, ui)
      pages/                # Home, Projects, Annotation, Dataset, Training, Evaluation,
                            # Playground, Login, Setup, Settings, Users
      studio/
        canvas/             # zoom-pan-canvas, tools (Block/Table/Order/Transcribe)
        components/         # ContentPane, JspreadsheetSheet (foglio tabella), LiveStream, ConventionsChecklist, Splitter, PrefillDialog
        state/              # store annotazioni + history (undo/redo)
      lib/                  # api.ts, coords.ts (coordinate 0-1000), otsl.ts (preview), types.ts
    package.json
  conf/                     # template full.sh/lora.sh, conventions.yaml
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

### Viste ufficiali Paddle (`services/paddle_dataset.py`)

Le annotazioni restano nel formato canonico; questo exporter ne deriva tre viste, una per ciascun
modello che il progetto può addestrare — sono modelli distinti e non si sostituiscono a vicenda:

| Vista | File | Modello | Contenuto |
|---|---|---|---|
| layout | `layout_{train,val}.json` + `annotations/` | rilevatore PaddleX (COCO) | bbox + classe del blocco, ordine di lettura |
| VLM | `vlm_{train,val}.jsonl` | PaddleOCR-VL via ERNIEKit | crop del blocco → OTSL (tabelle) o testo |
| riga | `rec_{train,val}.txt` + `rec_dict.txt` + `cells/` | PP-OCR rec | **un campione per cella**: ritaglio → testo |

La vista *riga* è ciò che chiude il ciclo sul motore OCR **locale** (quello che riempie le celle in
`table_detect.fill_cells`): una cella corretta a mano torna indietro come campione. Il ritaglio è lo
stesso che il riconoscitore vede in esercizio — confini della griglia più un quarto di passo di
respiro, altezza nativa (la normalizzazione a 32/48 px è del trainer) — perché un dataset tagliato
diversamente da come si legge insegna un'inquadratura che non esisterà mai. Con `approved_only`
entrano solo le celle `verified`: il testo del prefill non è gold, e il manifest dichiara quante ne
sono state escluse (`cells.unverified_skipped`). Il dizionario dei caratteri è quello osservato nel
corpus, non uno generico.

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

La mappa è **per progetto** e vive in `projects.settings_json["labels"]` (modello additivo: i
default restano sempre disponibili, le custom si aggiungono/rimuovono). Le classi riconoscibili
(teste alla `ALL_PROMPT` §2.3) compaiono nel LAYOUT e nei JSONL di crop; le classi pure-struttura
solo nel LAYOUT.

### 8.2 Classi: origine, classi note per modello, vocabolario nativo

Una classe ha un'**origine**: `public` se appartiene alla tassonomia pubblica del parsing (§2.7,
il modello base la conosce già → transfer gratuito) oppure `custom` (token nuovo, da insegnare).
L'origine è calcolata da `labeling.PUBLIC_LABELS`, non salvata.

«Già nota» però **non è una proprietà assoluta della classe: è della coppia classe + modello.**
Ogni adapter ha un vocabolario nativo (PP-DocLayout `number`, MinerU `doc_title`, …) che traduce
nel canonico. Quelle mappe sono dichiarate una sola volta in `services/model_labels.py` (gli
adapter le importano: una sola fonte) e `known_models_for(name)` dice quali modelli emettono già
una classe. `GET /api/projects/{id}/labels` restituisce per ogni classe `origin` e `known_models`;
`PUT` (admin/editor) sostituisce le **custom** del progetto con validazione rigida (nome unico e
non di default, colore `#RGB`/`#RRGGBB`, tipo ammesso, prompt obbligatorio per testo/tabella/
formula); `POST` crea **una** classe senza riscrivere la lista (il gesto rapido dallo Studio, che
così non sovrascrive il lavoro di un'altra scheda; 409 se il nome esiste già). Il dataset builder
costruisce `_LABEL_INFO` dal progetto (`_project_label_info`), così una classe custom entra davvero
in text_rec invece di essere saltata in silenzio.

**Il bridge nativa → canonica** (`model_labels.resolve_native_label`) traduce qualunque label di
un modello nel vocabolario del progetto, normalizzando l'ortografia (minuscole, `spazio`/`_`/`-`
equivalenti: `Table_document` ≡ `table document`). Ordine di risoluzione, dal più specifico al più
generico: nome canonico esatto → **mappa nativa dell'adapter** (la semantica del modello vince sul
generico) → **alias della classe** (sinonimi dichiarati, anche per le custom). Ogni classe porta
`aliases` in `settings_json`; l'`LabelsEditor` li edita e il prefill li usa. La risoluzione
**fallisce in modo rumoroso**: una label fuori tassonomia non entra mai in silenzio — il prefill
la conta in `summary.unmapped_labels` e il blocco ricade su `Text`, così la UI avvisa e l'utente
può aggiungere l'alias o la classe. Il prefill risolve sulle classi **del progetto** (non sul solo
default), quindi le custom non vengono più coercizzate a `Text`.

**Regola di coerenza per il fine-tuning**: l'addestramento insegna **sempre il vocabolario
canonico**; le mappe native e gli alias servono solo a leggere il modello *base* prima del
fine-tune. Aggiungere un modello = dichiarare la sua mappa in `model_labels.py`; è questo che rende
il sistema model-agnostic.

**Superficie per chi annota**: nello Studio il pulsante **Classi** apre `LabelsEditor`
(`frontend/src/studio/components/LabelsEditor.tsx`) — elenca le classi con badge di origine
(pubblica/custom), segna quelle note al modello attivo (`knownByModel`) e permette di
aggiungere/rimuovere le **custom** del progetto. Il PUT sostituisce solo le custom; i default
(`builtin`) non si toccano. Accanto al selettore di classe nella barra c'è **Nuova classe**
(`NewLabelDialog`): un gesto rapido che crea **una** classe via `POST /labels` e lo conferma con
un avviso, perché la classe vale su tutto il progetto. I campi del form sono condivisi
(`LabelFields`) fra le due superfici. La logica pura è in `frontend/src/lib/labels.ts`.

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

Il layout si misura a due soglie: IoU 0.5 (`layout`) e **IoU 0.75** (`layout_strict`, la
regola del reward di layout di LightOnOCR-3), con precision/recall/**F1** complessivi e per
label. A 0.5 una `Table` che copre solo la colonna di sinistra passa ancora; a 0.75 no. I
modelli **solo end2end** (LightOnOCR-3, Unlimited-OCR, DeepSeek-OCR, dots.mocr) non hanno un
prompt di layout né di crop: si valutano sulla loro generazione nativa, e testo e tabelle
sono il contenuto del blocco predetto abbinato a IoU 0.5 (i blocchi mancati restano fuori
dal CER e li conta il recall).

---

## 12. Roadmap progressiva (milestone)

- **M0 — Scaffolding** ✅ — struttura repo, AGENTS.md, README, requirements/package.json, run scripts,
  scheletro FastAPI + Vite funzionanti (health | ping | info ambiente).
- **M1 — Progetti & pagine** ✅ — CRUD progetto, scan cartella (immagini+PDF, PDF lazy via pypdfium2),
  registro pagine con metadati (issue_date/issue_no/page_no/page_type/status), thumbnail/preview on-demand,
  pagina Progetti + Dettaglio progetto (griglia pagine, edit metadati inline). Test end-to-end coperti.
  - **La lista pagine non tronca in silenzio.** `GET /api/projects/{id}/pages` restituisce `total`
    oltre agli `items`, con `limit` di default e massimo a 10000 (il tetto di una sessione di
    riconoscimento). Il default era 200: un corpus di 361 pagine ne mostrava 200 in ogni
    schermata, e le altre non si potevano vedere né selezionare.
  - **Ogni pagina porta `blocks` e `drafts`** (bozze generate e non confermate). `status` dice
    solo cosa ne ha fatto una persona: una pagina riconosciuta e mai rivista resta `new`, e
    senza `drafts` nessuna lista sapeva dire dove sono le bozze da verificare. Archivio, Studio
    e Riconosci mostrano «N bozze» al posto di «Nuova» e filtrano per «Con bozze».
  - **Le date del server sono UTC senza fuso** (`datetime('now')` di SQLite): si leggono con
    `lib/dates.ts::parseServerDate`, mai con `new Date(...)` diretto, che le prende per ora locale.
- **M2 — Studio annotazione core** ✅ — canvas Konva zoom/pan (zoom rotellina, pan con tool Panoramica),
  strumenti rettangolo/poligono, palette classi, ispezione/label/trascrizione base, spostamento/ridimensionamento
  con Transformer, **ordine di lettura** (indici con spostamento su/giù + riordino automatico), delete, undo/redo
  (Ctrl+Z/Y), autosave (PUT bulk, debounce 700ms), scorciatoie da tastiera (V/R/P/H). API blocchi:
  GET/PUT `/api/pages/{id}/annotations`, PATCH/DELETE `/api/blocks/{id}`, GET `/api/projects/{id}/labels`.
  Il PUT bulk conserva gli ID ricevuti e quindi le tabelle collegate durante l'autosave.
  Nota: modulo rinominato `blocks.py` (collisione con `from __future__ import annotations`).
- **M3 — Tabelle + reading order + trascrizione** ✅ — editor del blocco `Table` su **Jspreadsheet CE**
  (`studio/components/JspreadsheetSheet.tsx`): griglia righe/colonne, **celle unite** (unisci a
  destra/sotto, separa), colonne fantasma, trascrizione cella-cella, resize delle colonne, menù
  contestuale interamente nostro e localizzato, autosave + salvataggio esplicito; **generazione OTSL**
  (`services/otsl.py`, round-trip testato contro l'oracolo ufficiale `otsl_to_html`), crop per blocco
  (`GET /api/blocks/{id}/crop`), tabella per blocco (`GET/PUT /api/blocks/{id}/table` con OTSL in
  risposta), **checklist convenzioni** (`GET/PUT /api/projects/{id}/conventions`), **preview "a fiume"**
  dell'ordine di lettura sul canvas.
  Nota tecnica: l'OTSL ufficiale **non usa tag di chiusura** (`<fcel>text<lcel><nl>…`).
  - **Il foglio non è la fonte di verità.** `lib/gridSheet.ts` (funzioni pure, testate) traduce
    `TableGrid` ↔ Jspreadsheet nei due sensi; `source`/`verified` vivono nei `meta` della libreria,
    `vlines`/`hlines`/`row_columns`/`phantom_cols` passano attraverso intatti. L'OTSL resta generato
    dal server sul nostro modello.
  - **Le modifiche strutturali sono nostre.** `insertColumn`/`insertRow` della libreria chiamano
    `destroyMerge()`, che distrugge *tutti* i merge del foglio, e poi `updateTableReferences` va in
    eccezione su un merge già dissolto (`mergeCells[key][2]` di `undefined`). Inserire o togliere
    righe e colonne passa quindi da `lib/grid.ts` (`insertTrack`/`deleteTrack`, che sanno cosa
    succede a una cella unita che attraversa il punto) e il foglio si rimonta da lì.
  - **Trappole verificate della libreria**: `jspreadsheet()` restituisce un array riempito *dopo* il
    load (l'istanza arriva da `onload`); il menù contestuale si legge dal config dello *spreadsheet*,
    non della worksheet; `getSelection()` restituisce un array `[x1,y1,x2,y2]` mentre i tipi
    dichiarano un oggetto; i merge sono `[colspan, rowspan]`, l'ordine inverso del nostro;
    `setMerge` su celle già unite è un no-op silenzioso; `removeMerge` vuole la chiave
    dell'intervallo (`A2:B2`), non il nome della cella; `alert`/`confirm` nativi sono sostituiti
    dalla nostra `Modal` e da una `Notice`.
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
  - **Dove si addestra è il primo passo del form**, non l'ultimo: «Questa macchina» si spegne con
    la causa quando nvidia-smi non trova GPU, e le istanze Vast.ai accese sull'account si scelgono
    da una lista (host e porta compilati). `trainer.preflight()` distingue l'esecutore: in locale
    controlla repo, Python/conda, GPU e VRAM di *questa* macchina; con `ssh`/`vast`/`runpod` chiede
    host e repo di training remoti e non guarda la GPU locale. Prima li controllava sempre, e da un
    Mac un training remoto non partiva mai. La UI legge il preflight **dal vivo** (debounce 400 ms)
    accanto al pulsante, che resta spento finché qualcosa blocca. Su Vast/RunPod la chiave SSH di
    default è quella che Tabularium registra sull'account; la host key si fissa al lancio (TOFU,
    come il tunnel), e `UserKnownHostsFile` va fra virgolette.
- **M6 — Valutazione & playground** ✅ — `services/inference.py` (client vLLM OpenAI-compatibile:
  layout tollerante, riconoscimento testo/tabelle, ping), `services/evaluate.py` (metriche:
  IoU+label layout, Levenshtein ordine di lettura, CER/WER testo, struttura+CER tabelle; split del
  val riusato dal builder; report JSON in `eval/eval_<ts>/`). API: `POST /api/projects/{id}/evaluate`,
  `POST /api/playground/parse`. Pagine: Valutazione (cards aggregate, per-pagina, overlay GT vs
  predetto, errori pagine) e Playground (layout predetto overlay SVG + blocchi/contenuto, copia md).
  Degrada con warning se il server vLLM non è attivo.
  - **La verità di una valutazione sono le sole pagine approvate** (`approved`/`exported`),
    qualunque cosa dica l'ultima build del dataset: una pagina con bozze mai verificate contava
    l'output del modello come risposta giusta. Senza pagine approvate l'API risponde
    `no_gold_pages`. La valutazione **non richiede un training**: misurare il modello di partenza
    sul proprio corpus è il riferimento che il fine-tuning deve battere.
  - **Quali pagine si misurano.** Finché nessun training del progetto è `finished` nessuna
    pagina è stata vista dal modello, quindi si misurano **tutte** le pagine approvate
    (`report.scope = "all_approved"`): col solo val split un corpus da 11 pagine ne misurava 2.
    Dopo il primo training finito si torna al val split (`scope = "val"`), l'unico onesto.
- **M7 — Pseudo-labeling** ✅ — `services/ocr.py` (import lazy RapidOCR/PaddleOCR, config
  `TABULARIUM_OCR_ENGINE`), `POST /api/projects/{id}/prelabel`: rileva righe di testo su una pagina,
  filtra per confidenza/dimensione + NMS-lite, inserisce blocchi `Text` con `prefill_source` e
  `order_idx`, modalità replace/merge. `BlockOut` ora espone `prefill_source`. UI: pulsante
  La modalità modello espone sia `two_stage` sia l'ufficiale `end2end`; OTSL END2END valido evita
  una seconda chiamata, altrimenti il risultato dichiara il fallback sul crop tabella.
- **Sessioni di riconoscimento: peso e revisione.** `GET …/recognition-runs/{id}` non porta
  l'output grezzo delle pagine (`result: null`) salvo `?result_page=<page_id>`, che lo Studio
  chiede per la sola pagina aperta; l'export continua a usarlo tutto. Con l'output di ogni
  pagina una sessione da 200 pagine pesava 4,7 MB, riletta ogni 1,2 s dallo Studio: ora 60 KB.
  Risultati distingue le pagine «completate» senza blocchi (un modello che risponde e non trova
  niente non è un successo) e filtra per da verificare / senza blocchi / con errori. Lo Studio
  tiene `?project=&page=` nell'URL: un refresh riapre la stessa pagina. I nomi dei modelli in UI
  passano da `app/models/names.ts` (nome di catalogo dall'adapter), mai dal nome servito.
- **M8 — Utilizzo reale & polish** ✅ — frontend `dist` **servito dal backend** (monoprocesso
  `scripts/run.sh`; mount `/assets` + catch-all SPA con deep-link, API prioritarie), **lazy-loading**
  delle pagine (bundle iniziale −63%, 257 kB), `noUnusedLocals`/`noUnusedParameters` riattivati,
  script `build_frontend`/`run` (bash+PowerShell), guida d'uso e setup GPU nel README,
  benchmark d'uso = flusso §README. Nota: accorpare la build di produzione (Vite) prima di
  disinstallare `frontend/node_modules` se si ricompila.
- **M9 — Modello, provider e destinazione** — l'hub Modelli è il solo luogo di configurazione
  modello/provider (Impostazioni non lo duplica: v. `DESIGN.md` § Navigazione), ed è anche
  l'unico posto in cui il corpus si configura. È un **percorso in tre passi — modello → dove →
  configura** (`pages/ModelsHubPage.tsx` + `app/models/`), nell'ordine in cui la decisione si
  prende: ogni passo mostra solo ciò che gli serve e la scelta fatta resta scritta nella sua
  linguetta. Lo stato vive nell'URL (`?modello=…&dove=…&passo=…`), così Indietro torna al passo
  precedente; senza parametri si riapre «Configura» di ciò che è in uso. La compatibilità
  modello ↔ destinazione è **una funzione pura sola** (`app/models/registry.ts` →
  `destinationVerdict`, testata): selettore, destinazioni e pannelli leggono la stessa
  risposta, quindi nessuna opzione cliccabile porta a un rifiuto e un modello senza alcuna
  destinazione non è selezionabile. Il passo 3 locale è una lista di controllo pesi → server →
  verifica (`LocalSetup`); quello remoto è il pannello del provider reso in pagina
  (`CloudControlModal inline onlyProvider=…`), non più una modale. Su Vast.ai il pannello è a
  sua volta una lista di quattro tappe (account → GPU → server → collegamento) **ricavate dallo
  stato vero**, non dai click: dopo un refresh la preparazione in corso si riprende
  (promemoria `tabularium.vast.job` + una lettura del log remoto per istanza), e
  `provision_log` dichiara `adapter_id` perché il log è uno per istanza — senza, il fallimento
  di un modello veniva attribuito a quello appena scelto. Il rail espone Riconosci, Annotazione, Risultati, Progetti, Fine-tuning e Modelli.
  Fine-tuning ha sottosezioni visibili Dataset → Addestramento → Valutazione; Prova modello
  vive accanto alla configurazione in Modelli. Il profilo attivo
  è la fonte di verità atomica (`compute_profiles`, DB v10+); `get_inference_config()` espone
  anche `provider`, `resource_id` e `source_profile_id`, e il client VLLM li usa così che
  «`is_cloud`» non si deduca dall'URL (un tunnel SSH Vast ascolta su `localhost` ma è cloud).
  L'avvio di una
  sessione di riconoscimento sonda l'endpoint (409 `model_endpoint_unreachable` localizzato)
  invece di accodare pagine destinate a fallire; la VRAM locale non viene evocata per run remote;
  lo stop con `disable_inference` arresta la risorsa **identificata** (`resource_id` +
  credenziale del provider), mai la prima che capita nell'account.
- **M10 — Il calcolo locale dipende dall'hardware, e l'app lo dichiara** — un solo posto prende
  la decisione, `services/hardware.py`: piattaforma, memoria (unificata su Apple Silicon, VRAM
  su CUDA), e quali runtime di serving la macchina può ospitare. `vllm` su Linux+CUDA (su
  Windows via WSL2), `mlx-vlm` su Apple Silicon — dove vLLM non gira affatto, essendo
  CUDA-first. Ogni adapter dichiara `local_runtimes` e, quando esiste, `local_mlx_repo` (più
  `local_mlx_repo_choices` per le varianti pubblicate, es. bf16/8-bit, scelte per modello in
  Impostazioni); il
  registro espone per ogni modello `local: {runnable, runtime, reason, mlx_repo, mlx_repo_choices}`, con **codici
  stabili** che la UI traduce (`cuda_required`, `no_nvidia_gpu`, `apple_silicon_required`,
  `no_local_runtime`, `model_unsupported_locally`, `insufficient_memory`). Conseguenza di
  prodotto: su un Mac PaddleOCR-VL ha un benchmark MLX verificato. Qwen3-VL ha avuto prove
  MLX riuscite con il vecchio prompt, ma il suo prefill ora segue il parser nativo `qwenvl html`
  e va ribenchmarkato su Metal (la vecchia richiesta OTSL generava Markdown invalido).
  - **Qwen3-VL su MLX: le finestre delle penalità vanno alzate.** I parametri ufficiali
    Instruct-VL (`temperature 0.7`, `top_p 0.8`, `top_k 20`, `repetition_penalty 1.0`,
    `presence_penalty 1.5`) sono già in `recommended_generation`, ma `mlx-vlm` applica le due
    penalità su una finestra di **20 token** (`DEFAULT_REPETITION_CONTEXT_SIZE`): più corta di
    una riga di registro (~25–40 token), quindi la ripetizione resta invisibile e la
    generazione scorre fino al tetto (misurato: pagina `index` a 16384 token / 508 s,
    `finish_reason=length`). L'adapter dichiara ora il guard MLX
    (`local_mlx_repetition_penalty`/`_context_size`, `local_mlx_presence_context_size`),
    applicato per richiesta in `inference._sampling_for` solo sul serving locale Apple Silicon e
    regolabile in Impostazioni (sezione MLX). Il valore di default è conservativo e va
    confermato dal vivo: le penalità riducono la ripetizione degenerata, ma **non** garantiscono
    l'EOS del checkpoint 4-bit su richieste di pagina intera.
  MinerU2.5 ha anche il percorso nativo `MinerUClient` `mlx-engine` documentato
  dal produttore, ma resta sperimentale finché non passa una prova live sul corpus. Gli altri
  modelli restano remoti, ognuno per una ragione **misurata** servendolo davvero su una pagina:
  **MonkeyOCRv2** perché `mlx-vlm` non ha quell'architettura (verificato sui moduli installati);
  **MinerU2.5** usa il runtime nativo isolato `MinerUClient` MLX 2.0.5, verificato su
  scansioni corpus con layout completo e output tabellare HTML valido; le tabelle dense
  restano da valutare contro annotazioni gold. **DeepSeek-OCR-2** perché la sua ricetta verificata
  si regge sul logits processor n-gram di vLLM, che `mlx-vlm` non ha; **Unlimited-OCR** lo era
  fino a `mlx-vlm` 0.6.3 (via MLX produceva 12288 caratteri di `alpha.alpha.alpha…`), ma da 0.6.4
  `mlx-vlm` porta `unlimited_ocr` con `RingSlidingKVCache` (la R-SWA di upstream) e il modello
  gira in locale — misurato il 2026-10-13 su M5 Pro / 24 GB con `mlx_vlm 0.7.1`: 265,6 tok/s,
  TTFT 1,2 s, 5,8 GB, output grounded-markdown con `<table>` HTML. Essendo il n-gram processor
  ancora assente, la ricetta MLX di questo modello include una penalità di ripetizione con
  finestra 128 (`local_mlx_repetition_penalty`/`local_mlx_repetition_context_size`): senza, la
  generazione entra in loop (misurato: una riga ripetuta 23 volte); **dots.mocr**
  perché la generazione END2END si chiude a 682 caratteri e la run fallisce; **GLM-OCR** perché
  la run «riesce» con **zero** blocchi inseriti — un successo vuoto, peggio di un errore.
  **LightOnOCR-3-1B** è remoto solo perché non è ancora stato provato: niente misura, niente
  colonna MLX. La colonna MLX è quindi una misura, non una promessa: un checkpoint che si carica non è un
  percorso che funziona. Verifica ripetibile: `scripts/e2e_local_inference.py`.
  Il runtime Apple lo prepara Tabularium in `<root>/mlx-runtime`
  (`services/mlx_runtime.py`), come fa con `<root>/vllm-runtime`: il processo dashboard resta
  senza PyTorch. Il percorso MLX non passa dal registro pesi (li scarica `mlx-vlm` dalla cache
  HF) e il nome servito è l'id del checkpoint MLX. La scelta del runtime è **additiva**: dove
  vLLM funziona, il comportamento resta quello di prima. Matrice e misure in
  `docs/LOCAL_INFERENCE_GUIDE.md` §0.
  - **Il percorso ufficiale di PaddleOCR-VL ha un secondo runtime.** La sua pipeline Paddle
    (PP-DocLayout + riconoscimento) vive in `<root>/paddle-runtime`, che si installa da sé
    alla prima messa in servizio del modello. L'installer è platform-aware: su Linux prende
    `paddlepaddle-gpu` dall'indice CUDA, su Apple Silicon la wheel CPU arm64 da PyPI — senza
    quel ramo il percorso ufficiale su un Mac finiva in un vicolo cieco («completa prima
    l'installazione» di qualcosa che nessuno aveva iniziato).
  - **Lo stato di un'installazione si scrive dopo aver creato il venv.** `EnvBuilder(clear=True)`
    cancella il contenuto della cartella: scrivere `.install_state.json` e il log prima li
    faceva sparire, e durante i minuti dell'installazione la UI leggeva `absent` con il log
    vuoto. Vale per tutti e tre i runtime (`local_runtime`, `mlx_runtime`, `paddle_runtime`).
  - **SSH verso Vast.ai: due vie, mai mescolate.** L'istanza espone l'IP della macchina
    (`public_ipaddr` + `ports['22/tcp'][0].HostPort`, la riga «Direct SSH Connect» della
    console) e i forwarder `ssh*.vast.ai` (`ssh_host`/`ssh_port`). `_vast_ssh_endpoint()`
    preferisce la diretta — come `vastai ssh-url --direct` — e ripiega sul proxy solo con la
    coppia completa: incrociare l'host di una via con la porta dell'altra produce un endpoint
    inesistente. Vast.ai però può tenere `public_ipaddr` a `null` anche su un'istanza accesa
    e raggiungibile (verificato sull'istanza 50434880 su rotta v0 di dettaglio e v1 di lista;
    in quel caso nemmeno la CLI ufficiale sa costruire l'URL diretto, e nessuna rotta
    accessibile a chi noleggia pubblica l'indirizzo — le offerte non portano `public_ipaddr`
    e `/machines/` è dell'host). Per questo il pannello Vast ha un **override host/porta**,
    precompilato con la mappatura della 22 che l'API pubblica comunque: all'utente resta da
    incollare l'IP letto dalla console.
  - **Preflight SSH prima di consegnare qualsiasi cosa** (`check_ssh_access`/`ensure_ssh_access`,
    rotta `POST /api/system/cloud/vast/ssh-check`): apre una connessione nuda (`ssh … true`) e
    nomina la causa del fallimento — chiave rifiutata, host key cambiata, host irrisolvibile,
    porta muta. La chiave rifiutata risponde **409** e non più 502 indistinto: prima era
    indistinguibile da un errore di provisioning e diagnosticarla costava un noleggio intero.
- **M11 — Collaborazione live (prima ondata)** — presenza e riallineamento in tempo reale
  sull'annotazione, stile editor collaborativo, senza toccare il modello dati.
  - **Il WebSocket è solo push, non un secondo percorso di scrittura.** Le annotazioni si
    scrivono ancora **solo** dalla REST (`PUT /pages/{id}/annotations`, `PATCH/DELETE /blocks/{id}`,
    `PUT /blocks/{id}/table`): validazioni, audit e revisione ottimistica restano lì. Il canale
    (`services/live.py` → `LiveHub`, `api/live.py` → `WS /api/live/pages/{page_id}`) serve a due
    cose: dire chi è collegato alla pagina e avvisare gli *altri* che è cambiata.
  - **Presenza**: ogni connessione porta `conn_id`, utente e un colore stabile (derivato
    dall'identità). Lo Studio mostra gli avatar dei colleghi e «{nome} sta scrivendo…» mentre
    qualcuno ha modifiche non salvate (`editing`). L'autenticazione passa dal cookie di sessione;
    l'accesso al progetto è verificato prima di entrare nella stanza (pagina inesistente/estranea
    → chiusura 4404, come il 404 delle REST). I viewer si collegano in sola lettura.
  - **Riallineamento**: dopo ogni scrittura REST l'endpoint pubblica `annotations_changed` con la
    nuova revisione e l'autore. Il client ignora l'**eco della propria** modifica (revisione
    combacia) e, se non ha lavoro sporco, adotta la versione del server senza che l'utente ricarichi
    (`adoptRemote` su `useAnnotationState`, che azzera lo storico: fondere due storie è compito di
    un CRDT, non di questo strato). Con modifiche non salvate **non si butta nulla**: compare un
    avviso con chi ha cambiato e un pulsante «Carica la versione remota».
  - **Un solo processo.** L'hub vive in memoria: vale per lo scenario supportato (un processo
    dashboard). Con più worker servirebbe un broker esterno — il confine è `LiveHub.publish`, che
    gli endpoint sincroni (thread di lavoro) chiamano in modo thread-safe.
  - Il proxy di sviluppo (`vite.config.ts`) inoltra i WebSocket (`ws: true`); in produzione il
    canale è same-origin dietro la stessa CSP `connect-src 'self'`.
  - Test: `backend/tests/test_live.py` (hub, broadcast, publish thread-safe, autorizzazione);
    `frontend/src/app/live.test.ts` (apertura canale, presenza, notifica modifiche, riconnessione).
- **M12 — Condivisione stile Drive** — un'unica superficie per l'accesso al progetto.
  - **«Condividi» è una finestra, non un modulo in fondo alla pagina.** `app/ShareDialog.tsx`
    riusa le API già esistenti (`members`, `member-candidates`, `owner`, `activity`): aggiunta
    persone con ricerca per nome/email (il candidato entra come `editor` e il ruolo si cambia
    inline), ruoli Editor/Viewer, rimozione con conferma, trasferimento di proprietà. Due
    linguette: **Persone** (avatar con iniziali e colore stabile, come nello Studio) e
    **Attività**.
  - **Riuso, non un modello di permessi nuovo.** Il backend resta la fonte di verità: chi non è
    proprietario né admin vede la lista in sola lettura (niente campo di aggiunta, ruoli come
    testo) e il server respinge comunque le scritture con 403. `canManage` si calcola da
    `useAuth`/`useCanAdminister` + `project.owner_id`.
  - **Avatar condivisi**: `lib/avatar.ts` (`initialsOf`, `avatarColor`) è la sola fonte di
    iniziali e colore per Studio (`AnnotationPage`) e finestra di condivisione; la tavolozza è
    quella di `services/live.py`, così un utente ha la stessa tinta ovunque.
  - **Il feed attività è uno** (`app/ActivityFeed.tsx`): la stessa resa nella pagina del progetto
    e nella finestra, con un'unica tabella di traduzione delle azioni.
  - Test: `frontend/src/app/ShareDialog.test.tsx` (membri, aggiunta con ruolo, sola lettura).
- **M13 — Onboarding e profilo** — invito via link, password forzata al primo accesso, profilo.
  - **Invito via link** (`POST /api/users` con `invite=true`, `POST /api/users/{id}/invite`):
    l'admin crea l'account senza conoscerne la password; il server genera un token (conservato
    **solo come hash** in `users.invite_token_hash`, con `invite_expires_at`, TTL 72h) e
    restituisce il link una sola volta. L'invitato lo apre su `/invito?token=…`
    (`AcceptInvitePage`), sceglie la password e resta connesso
    (`GET /api/auth/invite/{token}` pubblica lo stato; `POST /api/auth/invite/accept` consuma il
    token, a uso singolo, attiva l'account e invalida le sessioni).
  - **Password da cambiare al primo accesso**: `users.must_change_password` è vero per gli account
    creati dall'admin e dopo un reset (`POST /api/users/{id}/reset-password`). `get_current_user`
    **blocca** ogni rotta tranne `/auth/me`, `/auth/change-password`, `/auth/logout` (403) finché
    l'utente non ne sceglie una propria; `AuthGate` monta `SetPasswordPage` prima dell'app, così
    il blocco è anche UX e non solo server. `set_password(..., must_change=False)` è l'unico punto
    che azzera il flag.
  - **Profilo**: `PATCH /api/auth/me` aggiorna la propria email (il nome utente è immutabile);
    la zona Account delle Impostazioni la edita sul posto. In modalità locale non c'è profilo.
  - **Migrazione 18** (additiva): le tre colonne su `users`; `SCHEMA_VERSION = "18"`.
  - Test: `backend/tests/test_user_onboarding.py` (invito, scadenza, gate, reset, profilo).

- **M14 — Chiarezza dei percorsi** — Progetti e Fine-tuning sono ingressi globali; le
  sottosezioni restano visibili e mantengono attiva la sezione padre. La creazione apre
  subito il progetto e lo rende attivo: `ProjectCreate.archive_dir` è opzionale (`null`
  per import browser), con import di immagini/PDF singoli o cartella. Solo i progetti
  collegati a una cartella sorgente espongono la scansione server. Protocollo, attività
  e pilot restano richiusi per lasciare in vista l’import e il registro pagine.
  `datasetUsable` esclude gli export vuoti; training e valutazione dipendono dai loro
  prerequisiti reali, non dal completamento dell’intero archivio. La prova temporanea
  è nominata Prova modello in tutte le lingue; non mostra errori dell’endpoint quando
  si usa OCR locale e azzera il risultato cambiando pagina.
  Verifica: `docs/UX_WORKFLOW_AUDIT.md`, test progetti/auth/dataset/i18n backend,
  Vitest completo, typecheck e build; browser su istanza isolata con immagine sintetica.

- **M15 — Orientamento e usabilità da riferimenti Mobbin** — il rail Fine-tuning
  apre `/fine-tuning`: una panoramica con prossimo passo ricavato da workflow,
  dataset e training; gli strumenti restano accessibili e gli errori di lettura
  non sono presentati come assenza di dati. Progetti mostra prima elenco e ricerca;
  `?nuovo=1` apre il dialogo di creazione, senza essere ricordato nel rail.
  `PageHeader` non tronca più le descrizioni. Le modali tengono stabile il focus
  durante i render e supportano `data-autofocus` sul campo iniziale.
  Riferimenti, scelte e verifica: `docs/UX_MOBBIN_REVIEW.md` (207 test Vitest,
  typecheck, build, browser it/en/fr). Nessuna nuova dipendenza.

- **M16 — Modelli chiusi via gateway: Gemini 4 Argon** — un modello senza pesi
  pubblici entra come endpoint esterno, non come download. L'adapter `gemini-argon`
  dichiara il protocollo Google `generateContent` (`chat_protocol`), raggiunto dal
  gateway OpenCode (Zen/Go) o da un endpoint Google; `services/opencode_gateway.py`
  legge la chiave dall'archivio di opencode (`~/.local/share/opencode/auth.json`,
  provider `opencode-go`/`opencode`) quando il vault non ne ha una, con override
  `TABULARIUM_OPENCODE_KEY`/`TABULARIUM_OPENCODE_AUTH`. `VllmClient` instrada la
  richiesta al ramo `generateContent` (header `x-opencode-session` obbligatorio),
  lasciando invariati prompt, parsing e percorso a valle. Il percorso verificato è
  **END2END**: un prompt con la tassonomia del progetto e l'OTSL vincolato restituisce
  in una sola generazione bbox 0–1000, label e contenuto — testata (`Title`), numero
  (`Issue-number`), data (`Issue-date`) **e** la tabella in OTSL (misurato live: 4
  elementi su una pagina `index`). È quindi il workflow nativo (`native_mode` →
  `end2end`), quindi **entra nel prefill/Riconosci** (`supports_native`) e nel
  Playground. Il prompt Tabella **vincola i token canonici** (`<fcel>/<ecel>/<lcel>/`
  `<ucel>/<xcel>/<nl>`, niente tag di chiusura): senza, il modello emette
  `<ched>/<splt>/<uched>` che `otsl.otsl_to_grid` non riconosce (misurato live).
  `provider` "opencode" si registra come `custom` nel profilo (il `CHECK` di
  `compute_profiles` non lo ammette: era un 500 all'utente) e un cambio di adapter
  azzera il tetto pixel globale, che è un residuo della ricetta MonkeyOCRv2. La
  disponibilità del gateway è una `generateContent` **senza corpo** (≈0,7 s):
  Argon «pensa» anche per un token (2–20 s), quindi una generazione minima come
  probe faceva lampeggiare `available` e disabilitava l'avvio del riconoscimento.
  Non è addestrabile (`training_types` vuoto) e non si scarica (`hf_repo` vuoto →
  `downloadable`/`download_only` falsi). Verifica: `tests/test_opencode_gateway.py`,
  `tests/test_model_registry.py`, `tests/test_prelabel.py` (END2END con testata+tabella),
  `tests/test_recognition.py` (Playground), `frontend/src/app/models/registry.test.ts`;
  typecheck, Vitest, build.

- **M17 — Benchmark multi-modello sulle pagine gold** — la Valutazione (M6) misura un
  solo modello, quello attivo, contro le pagine approvate. Il benchmark estende la stessa
  misura a **N modelli sulle stesse pagine**, per rispondere a «quale modello — o quale
  checkpoint — è il migliore *sul mio corpus*» e per vedere il delta del fine-tuning.
  - **Gold = le pagine approvate/esportate**, non uno stato nuovo: la verità di una
    valutazione resta l'annotazione umana (§M6). Una pagina «perfetta» è una pagina
    approvata; l'elenco pagine può mostrare un filtro «gold» ma non introduce una seconda
    verità accanto a `approved`/`exported`.
  - **Il riferimento è l'annotazione umana, mai l'output di un altro modello.** Confrontare
    con Argon misura l'accordo con Argon, non la correttezza: al più è uno smoke test, non
    la definizione del benchmark.
  - **Metriche riusate da `services/evaluate.py`** (IoU per label a 0.5 e `layout_strict`
    0.75, Levenshtein dell'ordine di lettura, TEDS, CER/WER), invocate una volta per modello
    sulle stesse pagine. Il benchmark non reimplementa metriche: le compone.
  - **Il confronto passa dal bridge nativa → canonica.** Le label predette si traducono nel
    vocabolario del progetto (`model_labels.resolve_native_label`) come fa il prefill, dentro
    `evaluate._evaluate_page`: senza, un modello che parla i propri nomi (`header`, `table`)
    segnava zero contro la verità canonica. La cache è versionata (`_CACHE_VERSION`) così un
    cambio di logica di misura la invalida. (La mappa nativa di Unlimited-OCR mancava ed è
    stata aggiunta.)
  - **Effimero.** Report in `eval/bench_<ts>/{report.json,report.html}`; nessun blocco o
    griglia scritto nel DB. Un benchmark non può sporcare il corpus né diventare dati di
    training per sbaglio.
  - **Cache per (adapter_id, model, page_id, annotation_revision)** su disco: un refresh non
    ripaga chiamate già fatte. Il report dichiara per modello latenza, token e **costo
    stimato** — i modelli remoti (Gemini 4 Argon via gateway) si pagano, e la spesa deve
    essere leggibile prima di rilanciare.
  - **Orchestrazione dei candidati.** Una lista di endpoint, non il solo profilo attivo. I
    modelli locali il benchmark **li avvia e li spegne da sé**, uno alla volta, su una porta
    dedicata (`_start_local`): **non passa da `serve_manager`**, che cambierebbe il profilo di
    inferenza attivo e romperebbe una run di riconoscimento in corso (es. Argon) — verificato:
    server locale acceso, misurato, spento, profilo intatto. Se l'endpoint locale indicato
    risponde già, si riusa (nessun secondo modello in memoria). Il pid del server avviato
    finisce nello stato (`serve_pid`) e `reconcile_benchmarks()` lo spegne all'avvio dopo un
    riavvio, così non restano orfani.
  - **Esecuzione in background con stato persistito.** Il benchmark non gira nella richiesta
    HTTP: `POST …/benchmark` avvia un thread e risponde subito; `GET …/benchmark/status`
    (stato su disco in `eval/bench_state.json`) dice `running`/`done`/`error` con
    `index`/`total`/`current_adapter`. Un refresh della pagina ritrova lo stato e, se è in
    corso, lo segue; a fine giro il report resta su disco.
  - **Anche gli adapter a pipeline ufficiale si misurano.** Qwen3-VL, PaddleOCR-VL, MinerU2.5,
    GLM-OCR e TeleOCR non hanno un prompt di layout: la loro misura usa la pipeline del
    produttore (`prefill.native_page_items`), la stessa del prefill, così non restano a zero.
  - **Si misura il percorso in esercizio** — prompt e parser dell'adapter, ciò che
    Tabularium userebbe davvero — non la pipeline nativa del produttore. `benchmark_models.py`
    resta lo strumento diagnostico per-fase; il benchmark di prodotto misura l'output che
    l'utente otterrebbe.
  - **Superficie propria, staccata dal Fine-tuning.** Il benchmark è la pagina `/benchmark`
    (voce nel rail), non una sezione della Valutazione: lì si scelgono **modelli** (lista a
    tick, ognuno con badge **Locale/Remoto**) e **pagine** (elenco con checkbox; una pagina è
    selezionabile solo se tutti i blocchi sono confermati). Ogni modello scelto espone il suo
    endpoint e il nome servito, modificabili.
  - **Approvazione in blocco** (`POST /api/projects/{id}/pages/approve`): approva tutte le
    pagine (o quelle scelte) in un colpo. Senza `confirm_drafts` salta quelle con bozze non
    verificate; con il flag le conferma — scelta esplicita, perché rende il modello maestro di
    se stesso.
  - **Risultati man mano e modalità «solo tabelle».** Ogni modello finito viene scritto
    nello stato (`results`), quindi la tabella si popola durante la run; l'avanzamento è per
    modello **e per pagina** (`page_index`/`page_total`). `table_only` misura solo la struttura
    delle tabelle (blocchi `Table` o con contenuto OTSL/HTML), ignorando layout e label: la
    tassonomia (una testata `Title` vs `Page-header`) non abbassa il punteggio della tabella.
  - API: `GET /api/projects/{id}/benchmark/pages` (pagine selezionabili),
    `POST /api/projects/{id}/benchmark` (admin/editor) con `{targets[], page_ids?, limit,
    with_text}`, `GET …/benchmark/latest`; report su disco come per l'eval.
    Verifica: `tests/test_benchmark.py` (metriche riusate, nessuna scrittura DB, cache,
    approvazione in blocco), pagina `/benchmark`; typecheck, Vitest, build.

- **M18 — Accesso di progetto esplicito e consultazione del team** — `ProjectOut.access`
  dichiara ruolo effettivo, fonte e capacità `can_edit`/`can_manage`, calcolate nella stessa
  connessione della lettura/creazione. Il ruolo globale non sostituisce quello di membro.
  L’elenco distingue propri/condivisi, proprietario e persone attive; la condivisione richiede
  persona, ruolo e conferma, con default viewer. `AnnotationEntry` monta l’editor solo per chi
  può scrivere; `ProjectReader` consulta scansioni, blocchi e tabelle senza autosave.
  `TableEntry` risolve il contesto da `GET /api/blocks/{id}` protetto, mai dalla query.
  Il revisore usa ancora editor: assegnazioni e approvazione separata non sono introdotte.
  `PageHeader` porta le spiegazioni sotto Guida, chiusa di default; i menu non hanno attese
  decorative e le pagine non ripetono istruzioni e animazioni globali. Pipeline richiede solo
  gli stati usati dalla pagina. Dettagli e limiti: `docs/UX_TEAM_WORKSPACE.md`.
  Verifica: test auth/projects/live/onboarding/annotations; frontend test, typecheck e build;
  browser su istanza autenticata con dati sintetici, proprietà/condivisione/lettura/URL diretto.

- **M18 — Training di ogni modello con la sua ricetta ufficiale** — Tabularium deve poter
  addestrare **ogni modello a pesi aperti** seguendo le **best practice e la documentazione
  ufficiale** di quel modello, non solo MonkeyOCRv2. I modelli chiusi (Gemini 4 Argon) restano
  fuori: non hanno pesi, quindi non si addestrano — `training_types` vuoto lo dichiara già.
  - **Un registro, non script sparsi.** `services/train_recipes.py` è il gemello di
    `serve_recipes.py`: una ricetta per adapter dichiara `toolchain` (ms-swift, erniekit+paddlex,
    llama-factory, unsloth, community), la **doc ufficiale** di riferimento, i tipi ammessi
    (`lora`/`full`), la **vista dati** che il dataset builder deve produrre e la maturità. Il
    training center legge la ricetta dell'adapter scelto; non conosce MonkeyOCRv2 per nome.
  - **Le viste dati esistono già, vanno unificate.** ms-swift usa i JSONL `messages` multimodali
    (§2.2, `dataset_builder`); Paddle ha le sue tre viste (`paddle_dataset.py`); GLM/LLaMA-Factory
    la conversione ShareGPT (`alternative_training.py`). Ogni ricetta punta alla sua vista; il
    builder non converte mai il target in silenzio.
  - **Disciplina delle ricette (come serving ed eval).** Una ricetta è `verified` **solo dopo una
    run reale** documentata; altrimenti è `experimental`. Nessuna ricetta senza una **doc
    ufficiale** a supporto. La matrice di stato vive in `docs/TRAINING_GUIDE.md`, non in una
    promessa: un toolchain che non ha ancora girato lo dice.
  - **Stato attuale (da verificare, non da assumere).** `ms-swift` copre la maggior parte dei
    modelli OCR — valori dalla sua tabella ufficiale *Supported-models-and-datasets*:
    MonkeyOCRv2 (`monkeyocrv2`, riferimento), Qwen3-VL (`qwen3_vl`), Unlimited-OCR
    (`unlimited_ocr`), DeepSeek-OCR-2 (`deepseek_ocr2`), GLM-OCR (`glm_ocr`), MinerU2.5
    (`qwen2_vl`), dots.ocr (`dots_ocr`). PaddleOCR-VL resta sul suo percorso ufficiale
    `erniekit+paddlex` (non passa dal generatore ms-swift). Da aggiungere: TeleOCR e
    LightOnOCR-3 (non presenti in ms-swift). La maturità è `experimental` finché una run reale
    non la promuove a `verified`; solo MonkeyOCRv2 è `verified`.
  - **Il fine-tuning parte dai dati del benchmark.** Il benchmark (M17) dice *dove* sta il
    divario: qui è la **tassonomia** (le classi custom non vengono emesse: `Issue-date` 0/10,
    `Title` 0/3) e la **struttura tabella** (TEDS basso), non la lettura (CER 0.006). La ricetta
    di training sceglie di conseguenza: LoRA sulla testa per i modelli che leggono già bene.
  - **Provisioning training su GPU.** `scripts/cloud/setup_cloud_train.sh` prepara un host CUDA
    (venv, ms-swift bundlato nel repo ufficiale o wheel pip, clone MonkeyOCRv2) e scrive un
    marcatore `$ROOT/.train_ready`: "preparato" è un fatto su disco, non un'assunzione. Su Vast.ai
    il noleggio lo esegue da sé via `prepare_training` (onstart = curl dello script, nessuna
    porta HTTP), distinto da `prepare_server` (serving); l'executor di training poi sincronizza il
    dataset e lancia lo script. Dettagli e log dei test in `docs/TRAINING_GUIDE.md`.
  - Verifica: `tests/test_train_recipes.py` (ogni adapter addestrabile ha una ricetta con doc
    ufficiale e vista dati; i chiusi no), `GET /api/system/train-recipes`; training wizard che
    mostra toolchain e maturità per il modello scelto.

- **M19 — Studio, assegnazioni e passaggio al revisore** — Migrazione 19 `page_work`:
  annotatore e revisore distinti, assegnazione owner/admin senza concedere accessi, stati
  annotation/pending/changes_requested/approved e versioni ottimistiche. Invio e revisione
  verificano la revisione corrente; l’approvazione assegnata non passa dagli endpoint legacy.
  Una modifica successiva invalida l’approvazione. `PageWorkControl` e filtri personali nel
  sidebar rendono il lavoro visibile; aggiornamenti live sul passaggio di stato.
  Salvataggi canvas/bozze/tabella serializzati, flush fallito impedisce il cambio pagina/progetto,
  scritture dirette con revisione attesa. Selettore viste sempre accessibile, Riconosci visibile,
  comandi secondari sotto Opzioni. Univer serializza solo mutazioni del dato, senza attese
  decorative; i comandi strutturali nativi duplicati sono nascosti.
  Verifica: 76 test backend mirati, 230 frontend, typecheck/build; browser con due account
  distinti, ciclo correzioni/nuovo invio/approvazione, bozze e foglio sintetico 150×8.
  Inventario e limiti: `docs/UX_STUDIO_REVIEW.md`. Nessun ruolo globale revisore o lock pagina.

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
