# Design

<!-- impeccable:design-schema 1 -->

Sistema visivo di Tabularium, registrato dal codice costruito (non dalle intenzioni).
Direzione: **alta densità giapponese**, sfidante vincente del registro *bolder*, seed `f6d9f98e`.
Modo: **Operate**. Sorgente della verità: `frontend/src/index.css` e `frontend/src/app/`.

## Tesi

Un mosaico di moduli rigati, ciascuno con la sua linguetta, impacchettati bordo a bordo.
Rifiuta la dashboard admin scura a card fluttuanti che questa categoria spedisce di default:
qui la densità è onesta, non gonfiata di spazio bianco, perché l'utente reale fa ~1000 edit
discreti su 200 pagine e ha bisogno di vedere molto insieme.

**Non negoziabile:** ogni zona porta il proprio nome scritto. Nessun modulo senza linguetta,
nessun pulsante affidato alla sola icona, nessuna azione che appare solo al passaggio del mouse.

## Superficie

Chiaro, per una ragione materiale: il contenuto è inchiostro su carta (scansioni di giornali
in scala di grigi) e si legge su fondo carta. Non c'è tema scuro — è una scelta, non un'omissione.

| Ruolo | Token | Valore |
|---|---|---|
| Foglio | `--color-sheet` | `#ffffff` |
| Riempimento (testate, log) | `--color-fill` | `#f4f5f7` |
| Filetto | `--color-rule` | `#c9ccd1` |
| Filetto forte (divisioni maggiori, modali) | `--color-rule-strong` | `#111111` |
| Inchiostro | `--color-ink` | `#111111` |
| Inchiostro secondario | `--color-ink-2` | `#4c5259` |
| Inchiostro terziario | `--color-ink-3` | `#6b7178` |
| Tavolo luminoso (fondo di una scansione) | `--color-table` | `#dfe2e6` |

Niente ombre. Niente gradienti. Niente angoli arrotondati. Filetti da 1px: la pagina è un
foglio stampato, non una superficie con oggetti sopra.

## Il segnale ha un ruolo solo

`--color-sig` `#e60012` significa **«qui è vivo»** e nient'altro: linguetta del modulo attivo,
linguetta di navigazione corrente, azione primaria, selezione corrente, testo selezionato,
cursore di testo, anello di messa a fuoco.

Due intensità della stessa famiglia, mai un secondo rosso:
- `--color-sig-plate` `#d0000f` — piastre piene con testo bianco (contrasto AA).
- `--color-sig-text` `#c4000e` — rosso su bianco a corpo piccolo (contrasto AA).
- `--color-sig-wash` `#fdecec` — fondo della riga o della cella viva.

Gli **allarmi** non competono col segnale: usano la piastra piena rossa più il segno
`IconAlert` (triangolo), quindi si distinguono per forma prima che per colore.
Canali separati e distinti: avviso `--color-warn` `#8a5a00` su `--color-warn-wash`,
conferma `--color-ok` `#0a6c3d` su `--color-ok-wash`.

**Regola di accessibilità:** nessuno stato è comunicato dal solo colore. Ogni stato è una
parola in un `.badge`; il colore rinforza, non informa (`app/ui.tsx` → `Badge`, `lib/vocab.ts`
→ `STATUS_TONE`).

## Tipografia

Un solo gotico compatto a corpi piccoli e interlinea stretta, più una voce macchina.
Entrambi **self-hosted** in `public/fonts/` — l'app è locale-first e non deve dipendere dalla rete.

- **Archivio (UI)**: `Archivo` 400–700 variabile. `--font-ui`.
- **Macchina**: `Chivo Mono` 400–600, con `font-feature-settings: 'zero' 1`. `--font-mono`.
  Solo per ciò che la macchina produce o misura: percorsi, coordinate, JSONL, OTSL, log,
  identificativi di run, dimensioni in pixel. Mai come costume «tecnico».

Cifre tabulari globali (`font-variant-numeric: tabular-nums`): è una superficie di misura,
le colonne di numeri si incolonnano.

| Ruolo | Corpo | Peso |
|---|---|---|
| Titolo del corpus (home) | 34px, `tracking -0.035em` | 700 |
| Titolo di sezione | 26px, `tracking -0.03em` | 700 |
| Titolo dentro un modulo | 19px | 700 |
| Corpo | 13px | 400 |
| Denso / tabelle | 12px | 400 |
| Etichetta (`.lbl`), linguetta, micro | 11px maiuscoletto, `tracking 0.04–0.06em` | 600 |

Nessun testo funzionale sotto gli 11px, e 11px solo per etichette; il testo che porta
informazione sta a 12–13px.

## Il modulo

Il contenitore universale: `.mod` + `.mod-head` + `.mod-tab` (+ `.mod-head-aux`) + `.mod-body`.
Un form da 16 campi, una tabella di metriche, la rassegna dell'archivio e un pannello dello
studio sono lo stesso oggetto a densità diverse.

- `.mod-tab` — piastra rossa: la regione è viva o primaria.
- `.mod-tab-quiet` — piastra nera: la regione è strutturale.
- `Collapsible` — stesso modulo, corpo a soffietto. È la forma della *disclosure*: i 12
  iperparametri avanzati del training vivono qui, non nel form principale.

Componenti in `frontend/src/app/ui.tsx`: `Module`, `Collapsible`, `ErrorNotice`, `WarnNotice`,
`Notice`, `Badge`, `Progress`, `Modal`, `Field`.

`Notice` è l'esito di un'azione detto in una riga dentro una piastra — un solo canale di ritorno
per zona, invece di cinque avvisi resi ognuno a modo suo. Per ciò che si rompe resta `ErrorNotice`,
che dice anche cosa fare adesso; le eccezioni grezze non arrivano mai allo schermo (`lib/errors.ts`).

`Progress` è un filetto che si riempie, mai da solo: accanto c'è sempre la misura scritta
(`123 MB di ~1,8 GB`), come per i `Badge`. Senza un totale attendibile la barra è indeterminata —
dice «sta procedendo», che è l'unica cosa vera in quel caso — e non si inventa mai un «passo N di M»
quando i passi dipendono da cosa manca davvero. Vale la stessa disciplina degli stati: un processo
vivo non è un server pronto, e finché l'endpoint tace la UI dice «caricamento», non «in servizio».

## Navigazione

Rail multi-riga persistente in alto, non una sidebar da sette link.
Riga 1: identità, lingua, stato del backend. Riga 2: le sezioni come linguette bordo a bordo
(`.navtab`), l'attiva su fondo nero con la piastra rossa da 3px sotto.
Le quattro destinazioni primarie sono **Riconosci, Risultati, Archivio, Modelli**. Dataset,
training, valutazione e configurazione dei provider restano strumenti contestuali dentro queste
aree: non sono percorsi globali concorrenti. Il rail scorre orizzontalmente al proprio interno.
La colonna sinistra resta libera per il **contesto** (pagine, progetti), mai per i link globali.

Modello e luogo di esecuzione sono sempre visibili nello stesso indicatore globale. Cambiare da
locale a un provider remoto non cambia il flusso operativo: selezione pagine, avanzamento,
risultati, revisione ed export conservano componenti, stati e vocabolario.

Nell'hub Modelli la destinazione decide il gesto: la libreria dichiara in testa «dove gira»
(profilo attivo) e ogni riga offre come azione primaria il deploy su quel provider — non il
download — quando la destinazione è remota e il modello è deployabile. «Deploya su Vast.ai» apre
la scheda del provider con il modello già scelto: la selezione non si rifà da capo. Le azioni
locali restano in riga, secondarie, perché la destinazione può cambiare; il modello in uso porta
il badge «In uso ora».

Nello studio il rail destro è **una zona sola**: il contenuto della pagina. L'output del modello
arriva in diretta in cima, i blocchi si correggono riga per riga sotto, e l'ordine di lettura si
governa dalle righe stesse — non da un secondo elenco che ripete gli stessi blocchi. Le regole di
trascrizione sono materiale di consultazione e stanno dietro il loro pulsante, non in un pannello
che occupa il rail per sempre.

Quella lista è anche l'equivalente DOM del canvas: i blocchi disegnati su Konva non esistono nel
DOM, quindi devono vivere qui come righe vere, navigabili da tastiera (frecce, Alt+frecce per
riordinare, Canc) e leggibili da uno screen reader.

E dentro una pagina, quando gli argomenti sono più d'uno: Impostazioni ha quattro zone
(Account / Istanza / Dati e backup / Ambiente) rese con le stesse linguette, la scelta persistita
nell'URL (`?s=`). Modello e provider non sono duplicati qui: appartengono all'hub Modelli.
La densità regge un form fitto, non quattro argomenti in fila.

## Il percorso

Il percorso primario è **seleziona pagine → riconosci → controlla i risultati → correggi o
esporta**. Le sessioni bulk appartengono al backend, sono persistite pagina per pagina e
continuano anche se il browser cambia schermata. Terminata la generazione, l'inferenza può
essere disattivata: risultati e correzioni non dipendono più dalla GPU.

Il vecchio percorso obbligato in sei fasi — Progetto, Scansione, Annotazione, Dataset, Training,
Valutazione — non compare più nell'Archivio: imponeva il fine-tuning anche a chi voleva soltanto
riconoscere ed esportare. L'Archivio è ora una rassegna operativa con tre uscite esplicite:
aprire una pagina, gestire le sorgenti o riconoscere una selezione. Dataset, training e
valutazione restano strumenti del Modello e delle singole attività, non tappe globali.

Nota di composizione: la fase corrente non usa un bordo sinistro colorato — è il tell del
template. Il rilievo viene dalla piastra e dal fondo, cioè dalla grammatica del mondo.

## Icone

Disegnate, mai glifi Unicode: `frontend/src/app/icons.tsx`.
Una sola famiglia di tratto — griglia 16, `stroke-width 1.5`, `stroke-linecap="square"`,
`stroke-linejoin="miter"` — così i segni appartengono allo stesso sistema dei filetti da 1px.
Sono sempre `aria-hidden`: il nome accessibile sta sul controllo.

## Movimento

Minimo e funzionale. Due momenti soltanto, entrambi con la stessa curva: `.swap` — il cambio di contenuto in posto,
140ms, `cubic-bezier(0.16, 1, 0.3, 1)`, con un `clip-path` che scopre da sinistra (il gesto
della piastra che viene stampata). Sotto `prefers-reduced-motion` diventa uno scambio istantaneo
e ogni transizione dell'app si azzera.

Il secondo è lo streaming: `.stream-in` (220ms) fa entrare ogni pezzo di output che il modello
produce senza far saltare quello che stai leggendo, e `.caret` è l'unico segnale che lampeggia —
solo mentre lo stream è aperto, perché dice «la riga non è finita». Sotto `prefers-reduced-motion`
il testo compare e basta, e il cursore resta fermo e visibile.

## Superfici del browser

Tematizzate dalla palette, non lasciate ai default: `::selection`, `caret-color`, `accent-color`,
`scrollbar-color` e le scrollbar WebKit, l'offset delle sottolineature.
Anello di messa a fuoco unico: `outline: 2px solid var(--color-sig)` + `box-shadow: 0 0 0 1px`
del foglio, così resta visibile sia sul bianco sia dentro una piastra rossa piena.

## Lingua

L'interfaccia parla italiano (con `en`/`fr` via `src/i18n`); il backend parla inglese.
Nessun enum grezzo raggiunge lo schermo: passa da `lib/vocab.ts`.
Gli errori non sono mai eccezioni grezze: `lib/errors.ts` li traduce in
`{titolo, messaggio, suggerimento}` — cosa è successo e **cosa fare adesso** — con il testo
tecnico richiuso dietro «dettaglio tecnico».

## Rischio dichiarato

Densità senza gerarchia diventa rumore. Ciò che tiene la densità leggibile è la linguetta
(ogni regione si nomina) e la disciplina del rosso (un ruolo solo). Un modulo senza linguetta,
o un secondo rosso con un altro significato, rompe il sistema più di qualsiasi errore di spaziatura.
