# Changelog

Le modifiche rilevanti di Tabularium sono raccolte qui. Il formato segue
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e le versioni usano
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Studio: assegnazione per pagina a un annotatore e un revisore distinti, code personali,
  invio alla revisione, richiesta di correzioni e approvazione con identità e versioni
  controllate dal server. Le pagine assegnate non aggirano il percorso con l’approvazione
  precedente. Vedi `docs/UX_STUDIO_REVIEW.md` per verifiche e limiti.
- Registro delle ricette di training per adapter (M18, `services/train_recipes.py`): ogni modello a
  pesi aperti dichiara toolchain, doc ufficiale, tipi ammessi (`lora`/`full`), vista dati e
  maturità (`verified`/`experimental`/`planned`). `GET /api/system/train-recipes`. Il generatore
  di script dispatcha dalla ricetta e fallisce esplicito su ciò che non sa eseguire. Cablati via
  ms-swift (valori dalla tabella ufficiale): MonkeyOCRv2, Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2,
  GLM-OCR, MinerU2.5, dots.ocr. I modelli chiusi (Argon) restano fuori: non hanno pesi.
- Provisioning del training su GPU: `scripts/cloud/setup_cloud_train.sh` prepara l'ambiente ms-swift
  (venv + repo + `swift`) con marcatore `.train_ready`; su Vast.ai il noleggio lo esegue da sé
  (`prepare_training`, onstart dedicato, distinto da `prepare_server`). Guida e log dei test in
  `docs/TRAINING_GUIDE.md`.
- Benchmark multi-modello (M17): pagina dedicata `/benchmark` (staccata dal Fine-tuning) dove
  si scelgono i modelli con dei tick (badge Locale/Remoto) e le pagine da usare come
  riferimento, con le metriche della Valutazione (IoU 0.5/0.75, ordine di lettura, struttura
  tabelle, CER/WER). Report su disco (`eval/bench_<ts>/`), nessuna scrittura nel DB, cache per
  (adapter, model, page, revision). Approvazione in blocco delle pagine
  (`POST /api/projects/{id}/pages/approve`). API `GET …/benchmark/pages`,
  `POST …/benchmark`, `GET …/benchmark/latest`. I modelli locali il benchmark li avvia e li
  spegne da sé (uno alla volta, porta dedicata, profilo attivo intatto; pid tracciato e orfani
  spenti al riavvio); gli adapter a pipeline ufficiale (Qwen3-VL, PaddleOCR-VL, MinerU2.5, GLM,
  TeleOCR) si misurano con la pipeline del produttore. L'esecuzione è in background con stato
  persistito (`GET …/benchmark/status`): un refresh della pagina ritrova il lavoro in corso.
  Le label predette passano dal bridge nativa→canonica (`model_labels`) come nel prefill, con
  cache versionata; la mappa nativa di Unlimited-OCR è stata aggiunta.
  I risultati compaiono man mano che ogni modello finisce, con avanzamento per pagina;
  la modalità «solo tabelle» misura la struttura delle tabelle ignorando layout e label.
- Gemini 4 Argon via gateway OpenCode: adapter `gemini-argon` (protocollo Google
  `generateContent`), modello senza pesi pubblici servito come endpoint esterno. Percorso
  nativo **END2END**: in una generazione restituisce testata (`Title`), numero
  (`Issue-number`), data (`Issue-date`) e la tabella in OTSL, quindi entra nel
  prefill/Riconosci e nel Playground; la chiave si legge dall'archivio di opencode quando
  il vault non ne ha una. Il provider esterno si registra come `custom`
  nel profilo di calcolo (il `CHECK` non ammetteva `opencode`: era un 500 in salvataggio).
  Dopo il salvataggio l'hub riconosce di nuovo la destinazione «Endpoint» (provider
  `custom` → `manual`) e lo Studio nomina il modello nella scelta del motore di prefill.
  La disponibilità del gateway è una `generateContent` senza corpo (~0,7 s) invece di una
  generazione minima: Argon «pensa» anche per un token (2–20 s) e il probe faceva
  lampeggiare lo stato, disabilitando l'avvio del riconoscimento.
- Quickstart più chiaro per macOS, Linux e Windows.
- Versione unica per backend e frontend, aggiornata da `scripts/version.py`.
- Unlimited-OCR ha un percorso locale su Apple Silicon: da `mlx-vlm` 0.6.4 esiste il
  modello `unlimited_ocr` con la R-SWA di upstream. Due varianti selezionabili per
  modello (`bf16`, `8bit`) e un guard anti-loop (`repetition_penalty` 1.1, finestra
  128), perché il processore n-gram ufficiale non è in `mlx-vlm`.
- Server MLX dedicato per Unlimited-OCR: il server `mlx_vlm.server` non inietta
  l'immagine per questa architettura, quindi Tabularium espone la stessa interfaccia
  OpenAI ma sull'API `mlx_vlm.generate` che funziona.

### Changed
- Studio: viste sempre raggiungibili, riconoscimento visibile e comandi secondari in Opzioni;
  cambio pagina/progetto subordinato al salvataggio di canvas e bozze. Il foglio serializza
  soltanto modifiche dei dati, senza attese decorative o comandi strutturali duplicati.
- README riorganizzato con istruzioni di primo avvio e comandi di release.
- Hub Modelli: scegliendo «Questo Mac» per un modello con più varianti MLX si chiede
  quale quantizzazione usare; la scelta è editabile anche nel passo Pesi e in
  Impostazioni → Modelli.

### Fixed
- Varianti MLX e memoria: il verdetto «gira su questo Mac» guardava solo la variante
  di default, quindi su un Mac da 8 GB Unlimited-OCR risultava «memoria insufficiente»
  per il bf16 e «Questo Mac» restava spento, anche se l'8-bit ci sta — e la scelta
  della variante, che viene dopo, non si raggiungeva mai. Ora ogni variante dichiara
  il proprio peso (`local_mlx_variant_weight_factors`); senza una scelta salvata si
  serve la più grande che entra (bf16 sui Mac grandi, 8-bit su quelli da 8 GB), e le
  varianti che non entrano sono spente nella scelta.
- Unlimited-OCR su Mac da 8 GB: la cache dei buffer di MLX tratteneva memoria già
  liberata, la memoria libera scendeva al 2% e il GPU andava in timeout alla prima
  pagina (`kIOGPUCommandBufferCallbackErrorTimeout`). Fino a 12 GB il server dedicato
  parte con la cache spenta (`--cache-limit-gb 0`) e la svuota dopo ogni pagina;
  misurato su M1 / 8 GB: pagina a tabella in 42 s, una da 30k caratteri in 130 s,
  memoria libera minima 7–9%.
- Riconoscimento con un modello locale: se il server muore a metà pagina (su un Mac da
  8 GB il GPU può andare in timeout anche con la cache spenta), la sessione lo
  riavvia sulla stessa porta e riprova la pagina una volta, invece di far fallire
  quella e tutte le successive. Il server di Unlimited-OCR esce da solo al primo
  guasto Metal, perché dopo un timeout il GPU rifiuta ogni comando finché il
  processo vive. Provato e scartato: passi di prefill più piccoli non abbassano il
  picco (4,90 GB contro 4,89).
- Pagine approvate e bozze del modello: un riconoscimento rilanciato su una pagina già
  approvata vi aggiunge bozze non confermate, e valutazione ed export le prendevano
  per verità — il modello veniva misurato (e addestrato) sul proprio output. Ora su
  una pagina approvata contano solo i blocchi confermati; sulle altre pagine nulla
  cambia (il report conta le bozze come prefill non verificato).

## [0.1.0]

### Added
- Flusso di riconoscimento, annotazione, esportazione dataset, training e valutazione.
