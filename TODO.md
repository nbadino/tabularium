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
- [x] Segreti cloud/API: rimosso il salvataggio delle chiavi nel browser;
  il vault server-side cifrato è implementato, `cryptography` è una dipendenza
  obbligatoria verificata dagli installer e dalla suite del virtualenv Python
  3.12; la CI installa lo stesso `requirements.txt`.
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
  training remoto; locale, Modal e gli identificativi Vast/RunPod sono
  persistiti e riconciliati, ma manca ancora la verifica end-to-end uniforme
  dei lifecycle remoti.
- [ ] Executor remoto (locale/SSH/Vast/RunPod), manifest, checksum, upload,
  checkpoint, resume, stop, cleanup, costi e riferimenti credenziali.
  Contratti e percorsi principali sono implementati e testati; resta da
  eseguire il ciclo completo contro provider reali.
- [ ] Recipe Vast/RunPod riproducibili: versioni pinnate, branch/commit
  dichiarati, hardware verificato, manifest e test del contratto API.
  Il contratto locale è coperto; manca la prova operativa completa sui
  provider.

### P1/P2 — qualità di prodotto e verifica

- [x] UI di collaborazione: accesso al progetto con aggiunta/rimozione,
  modifica inline del ruolo, trasferimento owner e attività recente leggibile.
- [ ] UI completa per impostazioni, accessi/attività, capability dichiarate,
  studio responsive e componenti/modal coerenti.
- [x] Test auth/annotazione non bloccanti e matrice CI sulle versioni Python
  supportate; la suite backend completa passa nel virtualenv Python 3.12
  allineato (`240 passed`) e la CI copre Python 3.11–3.13.
- [ ] Pilot reale da 30–50 pagine con LoRA, gold set protetto, analisi errori
  e successiva scala a 150–300 pagine.

### Evidenze dell’ultima tranche

- La suite backend completa è ora eseguibile anche nel virtualenv Python 3.14
  tramite il client ASGI confinato ai test: `225 passed, 3 skipped`.
  Gli skip sono espliciti per `cryptography` assente nel virtualenv e scansioni
  reali opzionali; nel virtualenv Python 3.12 con `cryptography` installata
  passa `240 passed`; frontend verificato con `79 passed` e build completata.
- I confini verticali piegati (`row_columns`) e i flag
  (`row_columns_proven`) fanno ora parte del contratto `TableGrid`, vengono
  salvati nel JSON della tabella e sono riutilizzati dall’overlay dopo il
  caricamento; l’API valida cardinalità, monotonia e normalizzazione;
  regressione struttura rilevatore: `4 passed`.
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
- Lo studio ha ora una modalità persistita per utente: `Tutto`, `Solo canvas`
  e `Solo trascrizione`; la prima conserva gli splitter, le altre collassano
  i pannelli non necessari per viewport stretti. Build, test frontend e
  detector UI passano; resta da validare il comportamento su dispositivi reali.
- Test frontend: `13` file e `79` test passati (`npm run test -- --run`).
- Il contratto training remoto è ora verificato anche al confine API: `TrainConfig`
  conserva repository e interprete remoti; recipe Vast/RunPod usa dataset,
  cache e checkpoint relativi alla directory sincronizzata. Test mirati
  trainer/executor: `24 passed`; build frontend e detector UI passano.
- `.github/workflows/ci.yml` verifica il backend su Python 3.11, 3.12 e 3.13;
  Python 3.14 resta esplicitamente fuori finché lo stack TestClient non è
  compatibile e verificato.
- La promessa README sul merge LoRA è stata resa onesta: il prodotto conserva
  adapter, recipe e manifest verificati; resta aperta l’integrazione del merge
  ufficiale verso un checkpoint servibile, con test end-to-end.
- La UI del Training Center espone ora executor locale/SSH/Vast/RunPod e i
  parametri SSH senza salvare segreti; lo schema API conserva anche repository
  e Python remoti. Lo script usa percorsi relativi alla recipe sincronizzata,
  quindi dataset, cache e checkpoint non puntano più al filesystem locale.
  Il trainer persiste e recupera il `remote_job_id` anche per Vast/RunPod. Il
  lifecycle remoto completo resta aperto finché non sono verificati upload,
  checkpoint/resume, cleanup e costi end-to-end.
- Il resume è ora esplicito tramite `resume_run_id`: valida che la run
  appartenga al progetto, copia solo i checkpoint locali verificati nella
  nuova recipe e li sincronizza anche verso l’executor remoto.
- Il provisioning Vast richiede ora un `monkeyocr_ref` esplicito (commit SHA o
  tag verificato); il ref viene passato all’onstart e la chiave server non è
  più interpolata nello script, ma solo nella variabile d’ambiente dell’istanza.
  Contract test: ref obbligatorio, quoting e assenza del plaintext nell’onstart;
  `bash -n` dello script passa.
- La UI blocca ora il noleggio automatico Vast finché il ref pin-nato non è
  compilato, evitando un’azione che il backend rifiuterebbe.
- Verificato anche l’ordine del parsing nello script: `--ref` viene elaborato
  prima del controllo obbligatorio. Il contract test Vast passa (`1 passed`).
- La recipe cloud controlla ora la compute capability minima per BF16 e salva
  nel `cloud-manifest.json` recipe ID, Python, Transformers, dtype, limiti
  vLLM, GPU memory utilization e capability hardware.
- Anche lo script `vast_onstart.sh` e il comando manuale RunPod richiedono
  esplicitamente il ref pin-nato; non esiste più un esempio operativo che
  avvii il setup con il branch implicito.
- I task Modal persistono ora PID, process group, template, log, stato ed
  owner, exit code in `jobs`; dopo un riavvio `status()` recupera log e task ancora
  vivi invece di perdere lo stato singleton; lo stop dell’app interrompe
  anche un deploy recuperato. Regressioni Modal: `2 passed`.
- Anche il serving locale dei modelli persiste ora `serve` in `jobs` con PID,
  process group, log, owner e comando; `status`/`stop` recuperano un server
  ancora vivo dopo il riavvio del backend. Regressione lifecycle + recovery:
  `2 passed` (suite dedicata verificata fino al limite noto del TestClient).
- Il lifespan esegue anche il reconcile dei job `serve`: PID non più presenti
  vengono marcati `failed` invece di restare bloccati in `running`; la suite
  non HTTP del serving ora copre lifecycle, recovery e PID morto (`10 passed`).
- Verificato il grafo frontend: Handsontable era dichiarato ma non usato e
  viene rimosso da `package.json` e `package-lock.json`; React Query resta
  perché è usato da `main.tsx`. Build e test frontend: `66 passed`.
- La documentazione ora dichiara il supporto backend realmente coperto dalla
  CI (Python 3.11–3.13); Python 3.14 resta esplicitamente pendente per il
  blocco noto nell’adapter TestClient/AnyIO.
- Il training remoto espone ora un cleanup esplicito e autenticato: rifiuta
  run attive, valida il `run_id` dentro la directory del progetto, rimuove
  solo la directory remota già composta dal provider e lascia intatti i
  checkpoint locali; l’azione UI richiede una seconda pressione. Contract test
  SSH e training: `25 passed`.
- La documentazione chiarisce i confini multipiattaforma: dashboard, OCR CPU,
  annotazione ed export funzionano su Linux/macOS/Windows; serving e training
  CUDA locale richiedono Linux o WSL2, mentre SSH/Vast/RunPod sono disponibili
  da tutti i client. Gli installer frontend usano ora `npm ci` e il lockfile
  è verificato con `npm ci --dry-run --offline`.
- `/api/system/info` dichiara ora le capability della macchina (`dashboard`,
  `cpu_ocr`, `local_cuda`, `remote_gpu`) e l’Ambiente le mostra in UI; su
  Windows espone esplicitamente il percorso CUDA via WSL2.
- Il refresh delle istanze Vast/RunPod registra ora anche risorse già create
  fuori da Tabularium, aggiorna stato/tariffa e calcola la stima persistita;
  regressione refresh Vast: `2 passed` insieme al contract test di provisioning.
- La disattivazione di un utente proprietario ora restituisce `409` finché i
  suoi progetti non vengono trasferiti; aggiunta regressione in
  `tests/test_auth.py`.
- Il tunnel SSH cloud persiste ora PID, process group e parametri in `jobs`;
  dopo il riavvio il backend riconcilia il processo e lo stop verifica che il
  PID sia ancora un processo `ssh`. Gli orchestratori cloud restano comunque
  aperti per lifecycle remoto, costi e teardown.
- Vast/RunPod registrano ora le risorse create in `jobs` con provider, owner,
  stato, tariffa opzionale e stima cumulativa del costo; start/stop/delete
  aggiornano il ciclo di vita. La UI mostra la stima corrente e offre
  `Distruggi` con conferma esplicita per entrambe le piattaforme; test cloud
  mirati: `4 passed`.

- `tests/test_compute_profiles.py`, `tests/test_db_migrations.py`,
  `tests/test_backup.py` e `tests/test_training.py` sono i test di controllo;
  insieme a `tests/test_rate_limit.py`, ultimo esito: `26 passed` dopo la
  migrazione v12 e la correzione della persistenza del rate limit.
- Il lifecycle del backend esegue ora `PRAGMA integrity_check` dopo
  `init_db()` e interrompe l’avvio con un errore diagnostico se il database
  non è integro; il controllo resta esposto anche nella pagina backup.
  Regressione lifecycle: `test_startup_stops_on_corrupt_database`.
- Corretta una regressione del test health: non assume più la vecchia baseline
  DB `6`, ma verifica la versione corrente dichiarata da `SCHEMA_VERSION`.
- Aggiunti test senza runtime Paddle per il parser ufficiale: varianti JSON
  annidate, fallback markdown a pagina intera e output vuoto.
- La suite HTTP ora usa una compatibilità ASGI solo su Python 3.14: mantiene
  cookie e stream SSE e aggira il deadlock del portal `TestClient`; il runtime
  dell’app non viene modificato.
- I setup backend shell/PowerShell accettano esplicitamente Python 3.11–3.13
  e rifiutano versioni diverse con un messaggio diagnostico; questo evita di
  creare ambienti apparentemente supportati ma incompatibili con la matrice CI.
- Gli installer verificano anche l’import di `cryptography` dopo l’installazione:
  nessun ambiente viene dichiarato pronto mentre il vault cifrato è assente.
- L’helper SSH CLI non usa più `eval` sul comando incollato e applica
  `StrictHostKeyChecking=yes` con un `known_hosts` dedicato, coerente con il
  tunnel gestito dal backend; le recipe cloud documentate richiedono ora
  esplicitamente un ref verificato e passano i secret tramite environment.
- L’hook Vast richiede e scarica il setup Tabularium al suo `TABULARIUM_REF`,
  separato dal `MONKEYOCR_REF` del runner, prima di avviarlo; un onstart copiato
  da solo non può più puntare a uno script locale inesistente.
- Il protocollo pilota non consente più sovrapposizioni gold/pilot: proteggere
  una pagina la rimuove dal campione salvato e l’API restituisce il conteggio
  delle esclusioni; regressione `test_gold_pages_cannot_enter_saved_pilot`.
- Il campionatore pilot applica ora anche lato backend il contratto 30–50
  pagine (`target` fuori intervallo risponde `422`), non solo nello script CLI;
  regressione `test_pilot_sample_requires_review_sized_target`.
- Lo smoke E2E attende ora fino a 30 secondi la readiness del backend quando
  deve prima compilare il frontend, evitando falsi fallimenti su macchine
  lente e su Windows/WSL.
- Le preferenze Modal hanno abbandonato il prefisso storico `lloyds.*` con una
  migrazione one-shot verso `tabularium.*`; il test verifica che la migrazione
  non conservi chiavi obsolete e non coinvolga segreti.
- Il tipo frontend `InferenceConfig` non dichiara più `api_key`: l’API espone
  solo `has_api_key`, impedendo che un secret possa rientrare accidentalmente
  nel contratto client durante refactoring futuri.
- Il registro modelli espone ora una maturità esplicita (`supportato`,
  `sperimentale`, `catalogo`, `non disponibile`) derivata dal contratto
  dell’adapter: scaricabile non significa più implicitamente pronto per
  inferenza o training.
- La configurazione inferenza non lascia più un riferimento `vault:inference`
  nel profilo legacy quando la credenziale viene rimossa; il test cloud usa una
  chiave Fernet effimera e non contiene segreti persistenti.
- La recipe Vast generata dal backend richiede ora ref verificati sia per
  MonkeyOCRv2 sia per Tabularium, scarica lo script dal repository corretto
  alla revisione dichiarata e non usa più `main` implicito; regressione del
  provisioning: `10 passed`.
- La procedura RunPod nella guida e nel pannello usa anch’essa due ref
  espliciti (`MONKEYOCR_REF` e `TABULARIUM_REF`) e la credenziale server via
  env, senza URL su `main` né `--api-key` esposto nel comando copiabile.
- Il frontend gestisce anche il caso di una pagina aperta durante una nuova
  build: se un chunk lazy hashato non è più presente, tenta un solo reload
  automatico per riallineare `index.html` e gli asset. Il marker viene
  cancellato solo dopo il mount riuscito dentro Suspense, evitando loop se il
  server continua a servire asset incoerenti; test frontend `79 passed` e
  build ripetuta dopo la modifica.
- `scripts/run.sh` e `scripts/run.ps1` ricostruiscono ora automaticamente il
  frontend quando i sorgenti sono più recenti di `dist/index.html`; è inoltre
  disponibile `TABULARIUM_BUILD_FRONTEND=1` per forzare la build e prevenire
  mismatch tra manifest HTML e chunk hashati al riavvio.
- Gli installer backend shell e PowerShell validano ora Python 3.11–3.13
  prima di creare o usare il virtualenv, evitando setup apparentemente riusciti
  su versioni non supportate; README aggiornato.
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

- [x] Riparare la verifica HTTP backend e usare sempre
  `.venv/bin/python -m pytest` negli script.
  - `httpx2` è fissato nei requirements-dev; la suite completa passa anche nel
    virtualenv Python 3.14 usando una compatibilità ASGI confinata a
    `tests/conftest.py`, mentre il runtime di produzione resta invariato.
  - Gli script `test_backend.sh` e `test_backend.ps1` invocano il Python del
    virtualenv; la matrice CI continua a verificare Python 3.11–3.13.

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
  - I confini piegati e i flag `row_columns_proven` sono ora persistiti nel
    grid salvato; le vecchie griglie restano compatibili perché i campi sono
    opzionali. Regressione: forma, cardinalità e normalizzazione verificate.

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
