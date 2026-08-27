# TODO — Workflow di ricerca, qualità e piattaforma estendibile

Backlog operativo emerso dalla review del flusso `archivio → annotazione →
dataset → fine-tuning → valutazione`. Non sostituisce la roadmap in
`AGENTS.md`: ne dettaglia le priorità e aggiunge la direzione multi-dominio e
multi-modello. Lloyd's List e MonkeyOCRv2 restano il primo profilo di dominio
e il primo adapter modello, non limiti architetturali.

## P0 — Integrità e riproducibilità

- [x] Rendere autorevoli i confini di qualità e riproducibilità nel backend.
  - `approved`/`exported` non sono più impostabili con la PATCH generica;
    l'approvazione passa da readiness e le pseudo-label non confermate bloccano.
  - Il preflight legge le famiglie dallo snapshot immutabile, richiede una
    validation reale e blocca una GPU già saturata da vLLM.
  - Ogni run usa una copia locale train/val dello snapshot dichiarato; il log
    ha un solo writer.

- [x] Correggere le coordinate della valutazione layout.
  - GT e predizioni sono confrontati entrambi in scala 0–1000; i pixel sorgente
    restano separati per generare i crop di testo/tabella.

- [x] Conservare la geometria corretta delle tabelle.
  - `vlines`/`hlines` sono persistite e validate come monotone, invece di
    essere accettate dall'API e scartate prima del salvataggio.

- [x] Impedire all'autosave bulk di cancellare le griglie tabellari.
  - Il client rimanda gli ID server e il backend aggiorna i blocchi esistenti,
    inserisce solo quelli nuovi ed elimina solo gli ID davvero rimossi.
  - Test di regressione: una griglia resta leggibile dopo un PUT bulk successivo.

- [x] Invalidare i crop se cambiano sorgente, bbox/poligono o trasformazioni.
  - Usare hash/versione annotazione nel nome; non riusare crop obsoleti.
  - Verifica: spostare un blocco e controllare che il crop JSONL cambi.

- [x] Correggere il quoting del training: sostituire `shutil.quote` con
  `shlex.quote` in `services/trainer.py`.
  - Verifica: script valido con `LLOYDS_TRAIN_PYTHON` contenente spazi.

- [x] Rendere l'export atomico, versionato e immutabile.
  - Scrivere in directory temporanea, validare, poi pubblicare snapshot
    `dataset/v0001`, `v0002`, ecc. con manifest, hash e seed.
  - Verifica: un export fallito non modifica lo snapshot precedente.

- [x] Riparare i test backend bloccati dal client FastAPI/Starlette/httpx e
  usare sempre `.venv/bin/python -m pytest` negli script.
  - `httpx2` è fissato nei requirements-dev; con esecuzione non sandboxata la
    suite completa passa (62 test).

## P1 — Protocollo di ricerca e dataset affidabile

- [x] Creare uno **studio di ricerca** sopra il progetto tecnico.
  - Wizard: corpus, intervallo temporale, obiettivo, convenzioni, modello
    e profilo di dominio.
  - Versionare e mostrare il protocollo dello studio.
  - L'unità di generalizzazione vive solo come **unità di split** nella pagina
    Dataset (`split_strategy`): era duplicata nel protocollo, dove però non la
    leggeva nessuno.

- [x] Produrre una mappa del corpus dopo l'import.
  - Statistiche per anno/numero/tipo pagina/scanner, stima tabelle,
    duplicati/quasi-duplicati e campione iniziale bilanciato.

- [x] Supportare split per gruppi: data/numero, annata, scanner, collezione e
  tipo pagina; separare `train`, `validation` e `gold test`.

- [x] Implementare stati annotazione: `bozza → verificata → approvata`.
  - Mostrare checklist per layout, reading order, testo, tabelle e warning.
  - Esportare solo pagine approvate.

- [x] Creare QA e gold set.
  - Il gold set protetto è assegnabile dal registro, escluso dal tuning e
    subordinato a una seconda revisione; `/qa-report` aggrega gli errori ricorrenti.
  - Seconda revisione su campione; 8–12 pagine invisibili al tuning;
    registrazione degli errori ricorrenti.

- [x] Preflight export bloccante.
  - Bbox validi anche dopo normalizzazione; griglie/merge/OTSL round-trip;
    prompt, trascrizioni, crop e classi verificati.
  - Distinguere errori bloccanti da warning.

## P1 — Annotazione guidata e ad alta precisione

- [x] Home operativa: **Stato dello studio / prossimo miglior compito**.
  - Avanzamento per stadio, copertura label, warning, dataset e ultime run.

- [x] Coda di annotazione guidata.
  - Continua lavoro, completa pagine quasi finite, rivedi tabelle complesse,
    copri classi deboli, lavora su campioni bilanciati o incerti.

- [x] Rendere espliciti i passaggi pagina: struttura, contenuto, tabella,
  revisione; consentire il salto dei passaggi non applicabili.

- [x] Trasformare l'editor tabellare in vista di lavoro dedicata.
  - Immagine e griglia sincronizzate; zoom locale; linee trascinabili;
    filetti visibili/dedotti/fantasma; header multi-riga, note, totali e merge.
  - OTSL/HTML restano strumenti avanzati.

- [x] Introdurre tile pyramid per scansioni grandi.
  - Stato ad alta frequenza fuori dal ciclo di rendering React.
  - Verifica: zoom/pan fluidi con immagini grandi e molte regioni.

- [x] Aggiungere browser cartella locale multipiattaforma, mantenendo il
  percorso manuale come fallback. Il picker `webkitdirectory` carica in modo
  sicuro una sessione di import senza esporre o modificare il percorso locale.

## P2 — Esperimenti, valutazione e active learning

- [x] Training Center con preset: prova rapida, LoRA standard, comparativo e
  avanzato; preflight GPU/CUDA/disco/dataset/checkpoint.

- [x] Collegare ogni run a uno snapshot dataset preciso; salvare manifest,
  config, script, versione app/modello e metriche incrementali JSONL.

- [x] Usare SSE nel frontend per log, metriche e GPU al posto del
  polling come percorso normale.

- [x] Completare M6: confronto base-vs-fine-tuned, overlay, IoU/mAP, ordine,
  TEDS, CER/WER e fallimenti peggiori.

- [x] Trasformare i fallimenti in azioni: “aggiungi esempi simili”, “rivedi
  queste tabelle”, “copri questa label”.

- [x] Prelabel assistito e active learning.
  - Bozze da modello/OCR restano non confermate, conservano provenienza e
    confidenza, e la coda privilegia le pagine a bassa confidenza o con errori eval.

- [x] Esporre e tracciare il percorso ufficiale MonkeyOCRv2 END2END.
  - Una chiamata bbox+label+content a 2 MP; OTSL valido viene importato senza
    seconda inferenza, altrimenti il report dichiara il fallback sul crop.
  - Il default resta `two_stage` finché l'A/B sul gold set non misura struttura,
    CER/TEDS, completezza e latenza su più pagine.

- [x] Rendere le bande tabella un'augmentazione sicura e sperimentale.
  - Il crop completo resta incluso; le bande sono create solo da `hlines`
    verificate, non attraversano rowspan e sono conteggiate separatamente.
  - Nessuna prima riga viene assunta automaticamente come intestazione.

- [ ] Eseguire un pilot: 30–50 pagine rappresentative, gold set protetto,
  prima LoRA, analisi errori, poi scala a 150–300 pagine.
  - Il campionatore deterministico 30–50 pagine è disponibile e salvabile nel
    protocollo; Dataset Builder ora offre `pilot_only` per generare uno
    snapshot isolato dal campione salvato (gold escluso). Restano da eseguire
    con il corpus reale la prima LoRA, l’analisi degli errori e la scala a
    150–300 pagine: `scripts/prepare_pilot.py` automatizza la preparazione e il
    preflight, ma dati annotati e una GPU dell’utente restano necessari.

## P2 — Generalizzazione oltre Lloyd's e MonkeyOCRv2

- [x] Definire un **core annotation schema** indipendente dal modello.
  - Pagine, regioni, poligoni/bbox, reading order, trascrizioni, tabelle
    logiche, celle, merge, metadati, provenienza e stato QA.
  - Conservare sempre coordinate sorgente e dati ricchi; non salvare OTSL,
    JSONL ms-swift o prompt come verità primaria.

- [x] Introdurre i **domain profiles** configurabili.
  - `lloyds-list-1900s` iniziale: label, colori, convenzioni, campionamento,
    prompt, regole QA e metriche preferite.
  - Profili futuri: registri, quotidiani moderni, manoscritti, fatture,
    moduli, libri, mappe o documenti tecnici.

- [x] Introdurre i **model adapters**.
  - Contratto comune: capabilities, input, export, training config, inference,
    checkpoint, metriche e vincoli hardware.
  - Primo adapter: MonkeyOCRv2-Parsing/ms-swift/OTSL.
  - Non assumere che tutti i modelli usino prompt, coordinate 0–1000, crop,
    OTSL o LoRA nello stesso modo.

- [x] Separare export dal modello.
  - Ogni snapshot può produrre più export: MonkeyOCRv2 JSONL, COCO/DocLayNet
    layout, ALTO/PAGE XML, HTML/CSV tabelle, JSON interno e formati futuri.
  - Disponibili export `internal`, COCO layout, PAGE XML, ALTO XML e HTML tabelle.
    Verifica: lo stesso ground truth produce più viste senza alterare l'annotazione primaria.

- [x] Catalogo capacità modello/dominio.
  - Dichiarare per ogni adapter: layout, testo, tabelle, formule, poligoni,
    ordine di lettura, multilingua, inferenza locale/remota e training.
  - La UI mostra solo strumenti ed export supportati dalla combinazione scelta.

- [x] Progettare una pipeline a plugin, non un monolite.
  - Interfacce interne per scanner, prelabeler, exporter, trainer, evaluator e
    inference provider.
  - Configurazioni YAML/JSON validate, versionate e portabili.

- [x] Conservare il focus iniziale.
  - Non implementare adapter generici prima che il workflow Lloyd's sia
    validato end-to-end; creare ora solo i confini e le interfacce.

## P3 — Test, documentazione e usabilità

- [x] Test frontend per coordinate, merge/split, OTSL preview, autosave e
  percorso e2e importa → annota → snapshot → script training.
  - Coordinate, merge/split, struttura i18n e shape del dizionario sono
  coperti; `scripts/e2e_smoke.sh` verifica ora rendering Chromium, deep-link
  `/dataset` e health del server. `scripts/e2e_workflow.py` copre ora il
  percorso UI scan → annotazione deterministica → snapshot dataset →
  preflight training, inclusa apertura dell’editor tabella, modifica DOM con
  debounce autosave verificato dal backend e preview OTSL.

- [x] Aggiornare README con protocollo pilota, requisiti GPU, riproducibilità,
  profili di dominio e modello di estensione tramite adapter.

## Ordine consigliato

1. P0: crop, quoting, test backend, export atomico.
2. Protocollo, split a gruppi, QA, preflight e snapshot dataset.
3. Coda guidata, editor tabellare su immagine e tile pyramid.
4. Training riproducibile, SSE e pilot controllato.
5. Valutazione che genera nuova annotazione.
6. Prelabel/active learning.
7. Adapter e profili multi-dominio/modello, usando il core già stabilizzato.
