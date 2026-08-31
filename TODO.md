# TODO — Workflow di ricerca, qualità e piattaforma estendibile

Backlog operativo emerso dalla review del flusso `archivio → annotazione →
dataset → fine-tuning → valutazione`. Non sostituisce la roadmap in
`AGENTS.md`: ne dettaglia le priorità e aggiunge la direzione multi-dominio e
multi-modello. Historic Shipping Index e MonkeyOCRv2 restano il primo profilo di dominio
e il primo adapter modello, non limiti architetturali.

## Controllo review tecnica — aggiornato 2026-08-31

Checklist di controllo derivata dalla review allegata. Viene aggiornata
progressivamente: `[x]` significa chiuso e verificato, `[ ]` significa ancora
aperto oppure solo parzialmente coperto.

### P0 — sicurezza e integrità collaborativa

- [x] Autosave concorrente: revisione pagina, `expected_revision`, risposta
  `409` con stato remoto e riconciliazione esplicita nell’annotatore.
- [x] Condivisione progetto: membri, ruoli editor/viewer, rimozione,
  trasferimento owner, elenco candidati e audit delle operazioni principali.
- [ ] Segreti cloud/API: rimosso il salvataggio delle chiavi nel browser;
  il vault server-side cifrato è implementato, ma va ancora installato e
  verificato nel virtualenv/CI (`cryptography`, rifiutata dal sandbox di rete).
- [x] SSRF di inferenza/playground: endpoint riservati, URL validati,
  destinazioni private bloccate e redirect HTTP non seguiti.

### P1 — operatività, compute e persistenza

- [x] Profili compute atomici: adapter, endpoint, modello servito, runtime,
  hardware, health check e attivazione senza perdere il profilo precedente.
- [x] Job locali persistiti in SQLite, con PID/process group, log, stop e
  riconciliazione dei processi dopo il riavvio del backend.
- [x] Backup SQLite online, integrity check, retention, download e restore
  amministrativo con conferma.
- [x] Audit append-only per autenticazione amministrativa e operazioni
  sensibili su progetti, annotazioni, utenti, compute e training.
- [x] Cookie configurabile `Secure`, rate limiting persistente per auth, CORS
  ristretto, security headers/CSP e verifica SSH tramite known_hosts.
- [x] Vincolo FK reale su `projects.owner_id` e migrazione completa delle
  invarianti di ownership: migrazione SQLite v12 con `ON DELETE RESTRICT`,
  più regressione su foreign key, cancellazione e disattivazione dell’owner.
- [ ] Persistenza/recovery uniforme per tutti gli orchestratori cloud e
  training remoto; oggi il percorso persistito è quello dei job locali.
- [ ] Executor remoto (locale/SSH/Vast/RunPod), manifest, checksum, upload,
  checkpoint, resume, stop, cleanup, costi e riferimenti credenziali.
- [ ] Recipe Vast/RunPod riproducibili: versioni pinnate, branch/commit
  dichiarati, hardware verificato, manifest e test del contratto API.

### P1/P2 — qualità di prodotto e verifica

- [x] UI di collaborazione: accesso al progetto con aggiunta/rimozione,
  modifica inline del ruolo, trasferimento owner e attività recente leggibile.
- [ ] UI completa per impostazioni, accessi/attività, capability dichiarate,
  studio responsive e componenti/modal coerenti.
- [ ] Test auth/annotazione non bloccanti e matrice CI sulle versioni Python
  supportate; la suite mirata passa, ma la suite completa va rieseguita nel
  nuovo ambiente di test.
- [ ] Pilot reale da 30–50 pagine con LoRA, gold set protetto, analisi errori
  e successiva scala a 150–300 pagine.

### Evidenze dell’ultima tranche

- `ProjectDetailPage` carica `/activity`, aggiorna la cronologia dopo le
  modifiche di accesso e mantiene i nomi lunghi leggibili senza mostrare
  payload tecnici; `npm run build` passa e il detector UI non segnala rilievi.
- `SettingsPage` è ora realmente universale: account, ruolo, email e lingua
  sono disponibili anche a editor/viewer; le sezioni amministrative restano
  condizionate dal ruolo.
- `ModelsModal` usa la primitiva `Modal` condivisa (focus trap e ripristino del
  focus), senza shadow/radii divergenti o glyph Unicode; build e detector UI
  restano puliti.
- `AnnotationPage` espone di nuovo poligono, panoramica, flow preview, ordine
  di lettura e checklist delle convenzioni; i controlli esistenti ricevono i
  tool e le azioni dello store reale. Build TypeScript verificata.
- Test frontend: `10` file e `66` test passati (`npm run test -- --run`).
- `.github/workflows/ci.yml` verifica il backend su Python 3.11, 3.12 e 3.13;
  Python 3.14 resta esplicitamente fuori finché lo stack TestClient non è
  compatibile e verificato.
- La promessa README sul merge LoRA è stata resa onesta: il prodotto conserva
  adapter, recipe e manifest verificati; resta aperta l’integrazione del merge
- La UI del Training Center espone ora executor locale/SSH/Vast/RunPod e i
  parametri SSH senza salvare segreti; il trainer persiste e recupera il
  `remote_job_id` anche per Vast/RunPod. Il lifecycle remoto completo resta
  aperto finché non sono verificati upload, checkpoint/resume, cleanup e costi
  end-to-end.
  ufficiale verso un checkpoint servibile, con test end-to-end.
- La disattivazione di un utente proprietario ora restituisce `409` finché i
  suoi progetti non vengono trasferiti; aggiunta regressione in
  `tests/test_auth.py`.
- Il tunnel SSH cloud persiste ora PID, process group e parametri in `jobs`;
  dopo il riavvio il backend riconcilia il processo e lo stop verifica che il
  PID sia ancora un processo `ssh`. Gli orchestratori cloud restano comunque
  aperti per lifecycle remoto, costi e teardown.
- Vast/RunPod registrano ora le risorse create in `jobs` con provider, owner,
  stato, tariffa opzionale e stima cumulativa del costo; start/stop/delete
  aggiornano il ciclo di vita e i test contrattuali coprono costo e chiusura.

- `tests/test_compute_profiles.py`, `tests/test_db_migrations.py`,
  `tests/test_backup.py` e `tests/test_training.py` sono i test di controllo;
  insieme a `tests/test_rate_limit.py`, ultimo esito: `26 passed` dopo la
  migrazione v12 e la correzione della persistenza del rate limit.
- Il vault è stato provato con il Python di sistema (`vault probe ok`): il
  plaintext non compare nel ciphertext; la verifica nel virtualenv resta
  sospesa finché non è disponibile `cryptography`.
- La suite vault passa con il Python di sistema: `2 passed`; il percorso API
  registra anche sostituzione/rimozione del credential senza salvarne il valore;
  sono disponibili `POST/DELETE /api/system/secrets` riservati all’admin e gli
  endpoint Vast/RunPod/Modal accettano riferimenti vault.
- `scripts/cloud/setup_cloud_vllm.sh` ora richiede un ref MonkeyOCRv2
  esplicito, pinna le dipendenze principali, fallisce sui prerequisiti GPU/disco,
  rimuove il flag incompatibile e genera `cloud-manifest.json`; `bash -n` passa.
- L’onstart Vast quota modello e API key senza interpolazione shell non sicura;
  la ricerca ora usa `PUT /api/v0/search/asks/` e il provisioning RunPod usa
  `POST /pods` con schema persistente documentato; contract test mockati: `8/8`.
  Resta da validare la forma Vast contro l’API reale e completare lifecycle,
  teardown e costi.
- Il training locale usa ora `TrainingRecipe` + `LocalProcessExecutor` e
  scrive `recipe.json` con hash SHA-256 di script e dataset; test di controllo:
  `33 passed, 2 skipped`. È disponibile anche `SshExecutor` con sync
  `rsync --checksum` e host-key verification, con PID remoto persistito,
  recovery e stop via process group; la riconciliazione non marca più come
  falliti i job SSH al riavvio. Checkpoint e artefatti hanno ora manifest
  SHA-256, download SSH e verifica prima della chiusura della run. Restano
  aperti validazione API reale e lifecycle costi per Vast/RunPod;
  i relativi executor sono già provider espliciti sopra SSH per istanze/pod
  già pronti.
- La checklist non sostituisce il backlog sottostante: le voci già presenti
  più in basso conservano il dettaglio storico e tecnico delle implementazioni.

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
  - Verifica: script valido con `TABULARIUM_TRAIN_PYTHON` contenente spazi.

- [x] Rendere l'export atomico, versionato e immutabile.
  - Scrivere in directory temporanea, validare, poi pubblicare snapshot
    `dataset/v0001`, `v0002`, ecc. con manifest, hash e seed.
  - Verifica: un export fallito non modifica lo snapshot precedente.

- [ ] Riparare la verifica HTTP backend e usare sempre
  `.venv/bin/python -m pytest` negli script.
  - `httpx2` è fissato nei requirements-dev e i test unitari mirati passano;
    la suite HTTP completa resta bloccata nel virtualenv Python 3.14 anche con
    un’app FastAPI minimale: una route sincrona si ferma nell’adapter thread
    di AnyIO. Serve una matrice CI su Python supportati e versioni compatibili,
    non una workaround nel runtime.

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

- [~] Trasformare l'editor tabellare in vista di lavoro dedicata.
  - **Fatto**: immagine e griglia sovrapposte e sincronizzate, zoom locale,
    linee trascinabili con la tastiera come alternativa, supporto per confine
    leggibile dalla forma (continuo = attestato, tratteggiato = da guardare),
    e accettazione confine per confine — «Aggiungi colonna/riga» e «Rifiuta
    confine», con `dropBoundary`/`insertBoundary` che rifiutano i casi ambigui
    invece di indovinare (`lib/grid.ts`, `studio/components/TableGridOverlay.tsx`).
    La voce era segnata come completata ma il codice aveva ancora cursori
    numerici accanto all'immagine: su un ritaglio da 2600 px un cursore largo
    80 px sposta la linea di ~32 px per pixel, quindi la correzione fine non
    era possibile.
  - **Da fare**: colonne fantasma raggiungibili dall'overlay, `header_rows`
    esposto nel modale, note e totali come ruoli di riga dichiarabili.
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

- [x] Rendere il rilevatore di griglia affidabile su tutto il campione.
  - Righe dalle linee di base dei glifi (componenti connesse) invece che
    dall'autocorrelazione del profilo, soglia di Otsu, inclinazione stimata e
    compensata senza ruotare l'immagine, avviso `skewed` esposto e tradotto.
  - Da 1 pagina su 4 a 4 su 4 sul campione reale; le tre rotture sono fissate
    da test di regressione che falliscono sulla versione precedente.
  - Resta noto e documentato (AGENTS.md §2.3.4): su una pagina a più colonne di
    giornale il rilevatore non sa di essere fuori dominio, e le due ipotesi per
    accorgersene sono state misurate e respinte.

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

- [x] Assegnare i valori alle celle invece di tagliare a x fisso.
  - Le colonne derivano di 25–77 px scendendo (0,6–2,0 passi) mentre una cifra
    è larga ~20 px: nessuna retta descrive la tabella. `snap_boundaries()`
    porta ogni confine nel varco di bianco della sua riga; dove un varco esiste
    (86–96% dei tagli) nessun valore viene spezzato, dove non esiste il taglio
    è marcato non provato e la cella entra fra le `uncertain`.
  - Da 5,7% a 0,5% di valori spezzati sul campione reale.
  - L'overlay disegna la spezzata reale sotto la retta modificabile.
  - **Da fare**: persistere i confini piegati (oggi il grid salvato porta solo
    le rette, quelli piegati vivono nella diagnostica della bozza).

- [x] Far sapere al preflight se la configurazione entra nella GPU.
  - Il controllo guardava la VRAM libera in assoluto e passava sempre su una
    scheda scarica; poi `swift sft` andava in OOM dopo aver scaricato i pesi.
  - `services/vram.py` stima i termini reali: su 8 GB il preset ufficiale
    (batch 4, 16384) chiede 26 GB, e il termine dominante sono i **logit**
    (vocabolario da 151936 su hidden 1024), non i pesi.
  - Tutti e tre i preset spediti non entravano in 8 GB: aggiunto `gpu8`
    (batch 1, 8192, grad_accum 4 → ~5,4 GB, stesso batch effettivo).

- [ ] Eseguire un pilot: 30–50 pagine rappresentative, gold set protetto,
  prima LoRA, analisi errori, poi scala a 150–300 pagine.
  - Il campionatore deterministico 30–50 pagine è disponibile e salvabile nel
    protocollo; Dataset Builder ora offre `pilot_only` per generare uno
    snapshot isolato dal campione salvato (gold escluso). Restano da eseguire
    con il corpus reale la prima LoRA, l’analisi degli errori e la scala a
    150–300 pagine: `scripts/prepare_pilot.py` automatizza la preparazione e il
    preflight, ma dati annotati e una GPU dell’utente restano necessari.

## P2 — Generalizzazione oltre Historic Shipping Index e MonkeyOCRv2

- [x] Definire un **core annotation schema** indipendente dal modello.
  - Pagine, regioni, poligoni/bbox, reading order, trascrizioni, tabelle
    logiche, celle, merge, metadati, provenienza e stato QA.
  - Conservare sempre coordinate sorgente e dati ricchi; non salvare OTSL,
    JSONL ms-swift o prompt come verità primaria.

- [x] Introdurre i **domain profiles** configurabili.
  - `tabularium-list-1900s` iniziale: label, colori, convenzioni, campionamento,
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
  - Non implementare adapter generici prima che il workflow Historic Shipping Index sia
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
