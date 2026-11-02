# Semplificazione dell'interfaccia — specifica

Stato: **bozza da discutere**, 2 novembre 2026. Nessuna modifica al codice è ancora stata fatta.

## Perché

Le revisioni del 17–23 ottobre ([UX_WORKFLOW_AUDIT](UX_WORKFLOW_AUDIT.md),
[UX_USE_CASE_VERIFICATION](UX_USE_CASE_VERIFICATION.md), [UX_TEAM_WORKSPACE](UX_TEAM_WORKSPACE.md))
hanno ripulito le singole pagine: meno cornici, sezioni apribili, caricamento a blocchi. La
struttura non è cambiata, ed è lì che nasce la complessità.

Un'installazione nuova (0.5.0 su un server senza GPU, 2 novembre) mostra il problema:

- l'utente atterra su **Riconosci** con l'avviso *"L'endpoint http://127.0.0.1:8888/v1 non
  risponde"*; ogni pagina ripete in rosso *"MonkeyOCRv2-Parsing · Locale · Non disponibile"*,
  mentre l'OCR su CPU è pronto e funziona;
- per riconoscere la prima pagina servono circa **10 clic** su quattro schermate: Riconosci →
  Nuovo progetto → nome → crea → Importa → file → Riconosci → scegli progetto → seleziona
  tutte → cambia motore → avvia → apri risultati;
- un progetto vuoto mostra Protocollo studio *versionato*, Perimetro corpus, *Profilo dominio /
  adapter modello*, Pilot iniziale *30–50 pagine bilanciate* e *Approva tutte* con 0 pagine;
- il progetto si sceglie in **11 selettori diversi**, uno per pagina.

L'interfaccia è organizzata attorno alle fasi interne del sistema (riconoscimento, annotazione,
dataset, training, provider). L'utente ragiona attorno a **un archivio e le sue pagine**.

## Obiettivo

> Chi installa Tabularium riconosce la prima pagina in meno di un minuto, senza leggere niente,
> e trova ogni funzione avanzata quando gli serve, non prima.

Misure di successo, verificate nel browser su un'installazione nuova senza GPU:

| Misura | Oggi | Obiettivo |
|---|---:|---:|
| Clic da "progetto creato" al primo risultato OCR | ~8 | ≤ 3 |
| Voci della navigazione principale | 5 + 3 sotto-menu | 2 |
| Selettori di progetto | 11 | 1 (nell'intestazione del progetto) |
| Errori o avvisi rossi al primo avvio, senza GPU | 2 per pagina | 0 |
| Controlli visibili in un progetto senza pagine | ~25 | ≤ 4 |
| Sinonimi per lo stesso concetto (vedi glossario) | 4 gruppi | 0 |

## Principi

1. **Il progetto è il contenitore di tutto.** Riconoscere, correggere, esportare e fare
   fine-tuning sono azioni *su un progetto*, non sezioni dell'app. Il progetto vive nell'URL
   (`/progetti/:id/...`), non in un selettore per pagina.
2. **Il default è ciò che funziona.** Senza un modello raggiungibile il motore è l'OCR locale,
   e non si mostra nessun errore. Un modello non configurato non è un guasto.
3. **Una sola azione principale per schermata**, scelta in base allo stato: senza pagine è
   *Importa*, con pagine non riconosciute è *Riconosci*, con bozze è *Rivedi*, con tutto
   verificato è *Esporta*.
4. **Mostrare le funzioni avanzate solo quando servono.** Se una funzione serve a una minoranza,
   o solo dopo un certo punto del lavoro, sta dietro un ingresso con un nome chiaro
   ("Avanzate", "Parametri"), mai in vista nel flusso base.
5. **L'interfaccia si spiega da sola.** Niente "▸ Guida" chiuso in ogni pagina: stati vuoti che
   dicono cosa fare, etichette in linguaggio comune, termini tecnici solo nei tooltip e nei
   dettagli. La documentazione resta raggiungibile da un link "Aiuto" nel menu utente.
6. **Un concetto, un nome, un posto.** Ogni azione ha un posto principale; le scorciatoie (palette
   comandi, tastiera) sono in più, non sostituiscono il posto principale.
7. **Il lavoro di squadra compare quando c'è una squadra.** Assegnazioni, revisione, presenza e
   accessi restano tutti, ma si mostrano quando il progetto ha più di un membro.

Cosa **non** cambia: semantica dei dati, regola "generato non è verificato", conferme sulle
operazioni distruttive, formati di export, ricette di training, sistema visivo di
[DESIGN.md](../DESIGN.md). Questa è una revisione della struttura e dei testi, non un
ridisegno visivo.

## Glossario

Un termine per concetto, nelle tre lingue. I termini a destra spariscono dall'interfaccia.

| Concetto | Termine (IT · EN · FR) | Da eliminare |
|---|---|---|
| Insieme di pagine di un lavoro | **Progetto** · Project · Projet | archivio, corpus, archivio attivo |
| Lancio del modello su più pagine | **Riconoscimento** · Recognition · Reconnaissance | elaborazione, sessione, run, coda |
| Lancio del modello su una pagina nello Studio | **Riconosci pagina** | prefill, prefill della pagina, inferenza nativa |
| Risultato del modello non ancora controllato | **Bozza** · Draft · Brouillon | — (già usato, va reso coerente) |
| Pagina controllata da una persona | **Verificata** · Verified · Vérifiée | approvata, confermata (vedi decisione D4) |
| Editor della pagina | **Studio** · Studio · Studio | annotazione, studio di annotazione |
| Riquadro sulla pagina | **Zona** · Region · Zone | blocco, block |
| Adattamento del modello alle proprie pagine | **Fine-tuning** · Fine-tuning · Fine-tuning | addestramento, training, refine |
| Una sessione di fine-tuning | **Addestramento** · Training run · Entraînement | run |
| Dove gira il modello | **Esecuzione** (questo computer, GPU a noleggio, servizio esterno) | destinazione, provider, endpoint |

Le classi delle zone (Title, Page-header, Byline…) vanno tradotte nell'interfaccia; il nome
tecnico resta nel tooltip e nell'export.

## Nuova struttura

### Navigazione

```
Intestazione:  Tabularium   [Progetti]  [Modelli]          Cerca ⌘K   Utente ▾
                                                                       ├ Impostazioni
                                                                       ├ Utenti (admin)
                                                                       ├ Aiuto
                                                                       ├ Lingua: IT EN FR
                                                                       └ Esci

Progetto:      ← Progetti / Nome progetto ▾      [Pagine] [Riconoscimenti] [Fine-tuning]   ⋯
                                                                              ⋯ = Condividi, Avanzate, Elimina
```

- **Progetti** è la pagina iniziale (`/`). Con un solo progetto, `/` apre direttamente quel
  progetto.
- **Modelli** resta in alto perché è un'impostazione dell'istanza, condivisa dai progetti.
- Il nome del progetto nell'intestazione è l'unico selettore di progetto (menu a tendina).
- Le schede del progetto sono tre. **Fine-tuning** è un percorso principale (decisione D2) e
  resta sempre visibile; senza pagine verificate mostra cosa manca invece di un modulo vuoto.
- La pillola di stato del modello in intestazione sparisce dalla navigazione globale e diventa
  una riga nella scheda Riconoscimenti, dove serve.

### Corrispondenza vecchie → nuove rotte

Tutte le vecchie rotte restano come redirect, così link e segnalibri continuano a funzionare.

| Oggi | Dopo |
|---|---|
| `/` Riconosci | `/progetti/:id/riconoscimenti` |
| `/risultati` | `/progetti/:id/riconoscimenti/:runId` |
| `/archivio` Archivio (`HomePage`) | `/progetti/:id` scheda Pagine |
| `/progetti` | `/` (lista progetti) |
| `/progetti/:id` (`ProjectDetailPage`) | `/progetti/:id` scheda Pagine |
| `/annotazione?project=&page=` | `/progetti/:id/studio/:pageId` |
| `/tabella/:blockId` | `/progetti/:id/studio/:pageId/tabella/:blockId` |
| `/fine-tuning`, `/dataset`, `/training`, `/valutazione` | `/progetti/:id/fine-tuning` (un percorso a passi) |
| `/benchmark` | `/progetti/:id/fine-tuning/confronta` |
| `/playground` Prova modello | rimossa: *Prova su una pagina* nella pagina Modelli |
| `/modelli` | `/modelli` |
| `/impostazioni`, `/utenti` | invariate |

## Schermata per schermata

Per ogni schermata: cosa resta in vista, cosa va dietro un ingresso, cosa sparisce.

### Primo avvio, accesso

| Resta | Cambia |
|---|---|
| Setup con nome utente, password, conferma | Niente flash della pagina di login mentre si carica lo stato |
| Login con OIDC opzionale | Se non esiste ancora un admin, il login porta al setup invece di rispondere "credenziali errate" |
| | Email: testo *"Facoltativa, usata per recuperi e notifiche"* → *"Facoltativa"*. Non esiste invio di email; non promettere recuperi |
| | Sotto il pulsante: *"Conserva questa password: per reimpostarla serve l'accesso al server"*, insieme a un comando `tabularium reset-password` (backend, fuori da questa spec) |
| | Una scheda rimasta sul setup dopo che l'admin è stato creato altrove si aggiorna e passa al login |

### Progetti (`/`)

- **Nessun progetto**: una sola area centrale, *"Crea il tuo primo progetto"*, con nome e zona
  di trascinamento file **nella stessa schermata**. Creare il progetto e caricare le pagine è un
  solo passo, non due.
- **Con progetti**: lista con nome, numero di pagine, avanzamento (verificate / totali), data
  dell'ultima modifica. Un pulsante principale *Nuovo progetto*.
- I filtri *Tutti / Di mia proprietà / Condivisi con me* compaiono solo se l'utente ha almeno un
  progetto condiviso.
- "Da una cartella sul server" resta come seconda opzione del modulo di creazione, solo per gli
  admin.
- Sparisce: il pulsante "Persone e accessi" su ogni riga (si raggiunge dal progetto).

### Progetto → Pagine (scheda iniziale)

Unisce `HomePage` (parete di miniature) e `ProjectDetailPage` (registro). Oggi sono due
schermate che si sovrappongono.

- **In vista**: miniature delle pagine con stato (nuova, bozza, verificata), filtro per stato,
  ricerca (oltre 20 pagine), una **barra dell'azione principale** che segue il principio 3:
  - 0 pagine → zona di trascinamento *Aggiungi immagini o PDF*;
  - pagine non riconosciute → *Riconosci N pagine* (con l'OCR locale o il modello configurato,
    in un clic, senza passare dalla scheda Riconoscimenti);
  - bozze presenti → *Rivedi N bozze* (apre lo Studio sulla prima);
  - tutto verificato → *Esporta*.
- **Vista tabella** (interruttore miniature ↔ righe): la tabella del registro con metadati
  modificabili (data, numero, tipo) e modifica di più pagine insieme. È la vista di chi cura i
  metadati, non quella iniziale.
- **Menu ⋯ del progetto**: Condividi · Avanzate · Elimina progetto.
- **Avanzate** (pagina dedicata del progetto, `/progetti/:id/avanzate`): Protocollo studio,
  Pilot iniziale, pagine di test protette (gold), classi delle zone, regole di trascrizione,
  schema tabella, attività recente, cartella sorgente sul server e nuova scansione.
- Sparisce dalla vista base: Protocollo studio, Pilot, Attività recente, il percorso assoluto
  della cartella, "Conferma anche le bozze", "Approva tutte" (resta nella vista tabella, con
  conferma).

### Progetto → Riconoscimenti

Unisce `RecognizePage` e `ResultsPage`.

- **In alto, una riga**: *Motore: OCR locale · Pronto* oppure *Modello: MinerU2.5 su Modal ·
  Pronto*, con il link *Cambia*. Se un modello è configurato ma non risponde: *Il modello non
  risponde. Puoi usare l'OCR locale o controllare in Modelli*. Mai l'URL dell'endpoint.
- **Nuovo riconoscimento**: quali pagine (*Non ancora riconosciute (N)*, preselezionato; *Tutte*;
  *Scegli…* apre la griglia di selezione), motore, *Avvia*. La griglia di 100 miniature non è
  più in vista per default.
- **Elenco dei riconoscimenti**: data, motore, pagine fatte / totali, stato. Aprirne uno mostra
  avanzamento, pagine con problemi, *Rivedi nello Studio*, *Esporta* (formato + originale o
  corretto), *Riprova le pagine non riuscite*.
- "Disattiva il modello al termine" diventa un'opzione dentro *Avvia* (solo admin, solo per un
  modello a noleggio), e sparisce il pulsante doppio "Disattiva modello" dai risultati.
- Il riquadro di conferma "Sostituisci le bozze" resta: protegge il lavoro.

### Studio

La pagina più usata e la più carica (`AnnotationPage.tsx`, 1.601 righe). Obiettivo: l'utente vede
la pagina, le zone e il testo; tutto il resto si apre quando serve.

- **Intestazione**: ← Pagine · nome pagina · *Pagina 3 di 40* · un'unica azione di chiusura
  **Verifica e vai alla successiva** (Invio). Oggi chiudere una pagina significa scegliere tra
  "Approva", "Pagina successiva", "Invia al revisore" e il menu di stato.
- **Barra strumenti**: Seleziona · Rettangolo · Poligono · Sposta (con le lettere V R P H),
  Annulla, *Riconosci pagina*. Il selettore della classe compare solo mentre si disegna o con una
  zona selezionata. "Nuova classe", "Flusso di lettura", "Ripeti", "Salva", "Riparti da zero"
  restano nel menu ⋯ (Salva è automatico: il menu mostra solo lo stato).
- **Barra laterale pagine**: chiusa di default su schermi sotto 1440 px; le frecce e la
  scorciatoia J/K bastano per scorrere. Il selettore di progetto sparisce (il progetto è
  nell'URL).
- **Riquadro contenuto**: zone in ordine di lettura, testo modificabile, *Verificata* per zona.
  Il menu "Opzioni" (Rinumera, Regole, Classi, Schema tabella) passa in ⋯; Classi, Regole e Schema
  tabella sono le stesse pagine di *Avanzate* del progetto.
- **Selettore "Vista"** (Tutto / Solo pagina / Solo testo): diventa due pulsanti di
  ridimensionamento sul divisore, come in un editor di codice.
- **Lavoro di squadra** (Assegna, Invia al revisore, Richiedi correzioni, coda "Da revisionare"):
  compare solo se il progetto ha più di un membro (principio 7).
- **Riconosci pagina** (oggi "Prefill della pagina"): motore preselezionato come nella scheda
  Riconoscimenti; il modo è *Aggiungi alle zone esistenti* per default, *Sostituisci le bozze* e
  *Sostituisci tutto* restano con la conferma attuale.
- **Tabella**: resta l'editor attuale (oggetto di [UX_STUDIO_REVIEW](UX_STUDIO_REVIEW.md)); in
  questa fase si allinea solo il percorso e il pulsante indietro con etichetta.

### Progetto → Fine-tuning

Percorso principale (decisione D2), ma presentato come **un solo percorso a tre passi**, non
quattro pagine con selettori propri.

```
1 Prepara i dati  →  2 Addestra  →  3 Valuta
```

- In cima, una sola card **Prossimo passo** con lo stato e un pulsante (oggi esiste già in
  `FineTuningPage`; diventa l'intestazione dell'intero percorso).
- **Prepara i dati**: *Prepara il dataset* con i default (80 % addestramento, split per pagina,
  solo pagine verificate). In vista: quante pagine e zone entrano, un avviso se sono troppo
  poche. Dietro *Parametri*: quota, unità di split, seed, adattatore, solo campione pilot,
  formati per altri modelli, anteprima dei file generati.
- **Addestra**: scelta della GPU in linguaggio comune (*Questo computer* se ha una GPU adatta,
  *GPU a noleggio (Vast.ai)* con budget orario, *Un server mio (SSH)*), modello di partenza,
  *Avvia*. Tipo (LoRA / completo), epoche e learning rate dietro *Parametri*, i 15 parametri
  avanzati dentro quello. Durante l'addestramento: grafico, tempo stimato, costo, *Ferma*; il
  log si apre su richiesta (come oggi).
- **Valuta**: tre numeri in linguaggio comune in cima (*Testo: 2,1 % di caratteri errati*,
  *Zone trovate: 94 %*, *Tabelle corrette: 81 %*), con il confronto col modello di partenza.
  Precision, recall, IoU, TEDS, WER e la tabella per classe stanno sotto *Dettagli*.
- **Confronta modelli** (oggi Benchmark): una sezione dentro Valuta. Gli URL e i nomi serviti
  si scelgono dai modelli configurati, non si digitano.
- Cosa sparisce: i selettori di progetto su Dataset, Training, Evaluation, Benchmark; la
  scheda "Panoramica" (diventa la card Prossimo passo); il doppio pulsante "Ferma il run".

### Modelli

- **In uso ora**: nome del modello, dove gira, stato, *Cambia*, *Prova su una pagina* (sostituisce
  la pagina Prova modello).
- **Scegliere un modello** (passo 1): restano ricerca e schede; il filtro *Girano su questo
  computer* è attivo per default quando ce n'è almeno uno. "Aggiungi un modello da Hugging Face"
  diventa un link in fondo alla lista, non un modulo sempre aperto.
- **Dove farlo girare** (passo 2): prima le opzioni possibili su questa macchina, poi le altre.
  Un'opzione impossibile (oggi *Locale* selezionato ma "Questa macchina non può servire modelli
  in locale") non è mai preselezionata; va in fondo, grigia, con la causa in una riga.
  Descrizioni senza gergo:
  - *Questo computer* — "Usa la GPU di questa macchina."
  - *Modal* — "Paghi solo i secondi in cui il modello lavora. Serve un account Modal."
  - *Vast.ai* — "Noleggi una GPU a ore. Serve un account Vast.ai con credito."
  - *RunPod* — "Noleggi una GPU su RunPod. Serve un account RunPod."
  - *Un server che hai già* — "Indica l'indirizzo di un server compatibile."
  Nei dettagli (tooltip): vLLM, SGLang, tunnel SSH, proxy HTTPS.
- **Configura** (passo 3): invariato nella logica; parametri del modello oggi in *Impostazioni →
  Modelli* (preset, override vLLM/MLX) si spostano qui sotto *Parametri del modello*.
- L'indicatore 1-2-3 resta solo durante la configurazione, non quando un modello è già in uso.

### Impostazioni

- **Account**: nome, ruolo, email (una volta sola, modificabile: oggi compare due volte),
  *Cambia password*. La lingua esce da qui: resta nel menu utente.
- **Istanza** (admin): nome, ruolo di default, registrazione, accesso istituzionale (OIDC).
- **Dati e backup** (admin): invariato.
- **Ambiente**: invariato, in fondo.
- La scheda **Modelli** sparisce (vedi sopra). Il link "Gestisci utenti" resta; Utenti è anche
  nel menu utente.

### Intestazione e palette comandi

- Logo e titolo della scheda: **Tabularium** ovunque. Oggi il logo dice "TabulariumLab" e il
  titolo passa da "Fine-tuning MonkeyOCRv2" (`index.html`) a "Riconoscimento documenti".
- Palette comandi (⌘K): resta, con progetti, pagine e azioni del progetto corrente. Escono
  lingua ed Esci (sono nel menu utente); entra Confronta modelli, oggi assente.

## Cosa si rimuove dal codice

| Elemento | Motivo |
|---|---|
| `HomePage.tsx` (`/archivio`) | assorbita dalla scheda Pagine |
| `PlaygroundPage.tsx` | sostituita da *Prova su una pagina* in Modelli |
| `PageHeader` con `<details>` "Guida" | sostituito da stati vuoti e testi in linea (principio 5) |
| 11 selettori di progetto e la logica "progetto con più pagine" di `activeProject.ts` | il progetto è nell'URL |
| `studio/components/ClassPalette.tsx` | non usato |
| chiavi i18n `home.next*`, `home.env*`, `settings.tabCompute` | non usate |
| pillola modello nell'intestazione globale | spostata nella scheda Riconoscimenti |
| scheda Impostazioni → Modelli | spostata in Modelli |

Nessuna rotta backend viene rimossa.

## Fasi

Ogni fase è una PR indipendente, verificata nel browser su un'installazione nuova e su un
progetto con dati reali, con screenshot nella descrizione.

1. **Default e testi** (piccola, nessun cambio di struttura)
   - motore di default = OCR locale senza modello raggiungibile, nessun avviso rosso al primo avvio;
   - nome "Tabularium" ovunque, titolo della pagina;
   - glossario applicato alle stringhe in `i18n/*.ts`, classi tradotte;
   - primo avvio: flash del login, testo email, login senza admin → setup;
   - rimozione del codice morto (tabella sopra).
2. **Progetto al centro**: nuove rotte con redirect, intestazione del progetto con il suo
   selettore, schede Pagine e Riconoscimenti, rimozione dei selettori per pagina e di `HomePage`.
3. **Pagine e Avanzate**: barra dell'azione principale, creazione + caricamento in un passo,
   vista tabella, pagina Avanzate del progetto.
4. **Studio**: azione unica di chiusura, barra strumenti ridotta, lavoro di squadra condizionale,
   barra laterale chiudibile.
5. **Fine-tuning a passi**: percorso unico, parametri dietro *Parametri*, valutazione in
   linguaggio comune, Confronta modelli dentro Valuta.
6. **Modelli e Impostazioni**: ordine delle opzioni, testi, Prova su una pagina, spostamento dei
   parametri dei modelli, pulizia di Impostazioni.

Le fasi 1 e 6 non dipendono dalle altre. La 2 è il prerequisito di 3, 4 e 5.

## Decisioni aperte

| # | Domanda | Proposta in questa bozza |
|---|---|---|
| D1 | Utente principale: ricercatore singolo o team? | Singolo; il lavoro di squadra compare con più membri |
| D2 | Peso del fine-tuning | **Principale** (indicato il 2 novembre): scheda sempre visibile nel progetto |
| D3 | Protocollo, pilot, gold set, attività, benchmark: tenere, nascondere, togliere? | Nascondere in *Avanzate* del progetto; benchmark dentro Valuta |
| D4 | "Verificata" e "approvata" sono due stati diversi (zona verificata, pagina approvata)? | Unificare in *Verificata* per l'utente singolo; *Approvata* solo nel flusso di revisione di squadra |
| D5 | Con un solo progetto, `/` apre direttamente il progetto? | Sì |
| D6 | Lingua di default dell'interfaccia | Quella del browser, inglese se non disponibile (oggi italiano) |
| D7 | La vista tabella del registro resta nella scheda Pagine o va in Avanzate? | Resta, come interruttore |
