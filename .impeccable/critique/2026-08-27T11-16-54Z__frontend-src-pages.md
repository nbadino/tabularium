---
target: frontend annotazione e tabelle
total_score: 18
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 4
timestamp: 2026-08-27T11-16-54Z
slug: frontend-src-pages
---
# Critique — annotazione e tabelle

**Modalità:** Operate  
**Provenienza:** dual-agent assessment; A ha esaminato UX e sorgente senza vedere B, B ha eseguito detector e browser senza vedere A.  
**Target:** `/home/cappannonno/lloyds-lab/frontend/src/pages` e studio di annotazione.

## Verdetto

Il prodotto usa concetti molto specifici e corretti — OTSL, colonne fantasma, crop originale, celle unite, prefill e ordine di lettura — ma il cuore dell'interazione è ancora un editor CRUD che costringe l'utente a confrontare mentalmente fotografia, griglia e slider. La shell scura e multicolore dello Studio è inoltre incoerente con il sistema chiaro, squadrato e a segnale rosso unico dichiarato in `DESIGN.md`. Il problema non è principalmente estetico: operazioni irreversibili e stati non verificati possono contaminare il ground truth.

## Nielsen

| # | Euristica | Score |
|---|---|---:|
| 1 | Visibilità stato sistema | 2/4 |
| 2 | Corrispondenza col mondo reale | 3/4 |
| 3 | Controllo e libertà | 1/4 |
| 4 | Coerenza e standard | 1/4 |
| 5 | Prevenzione errori | 1/4 |
| 6 | Riconoscimento invece di memoria | 2/4 |
| 7 | Flessibilità ed efficienza | 2/4 |
| 8 | Design estetico e minimale | 2/4 |
| 9 | Recupero dagli errori | 2/4 |
| 10 | Aiuto e documentazione | 2/4 |
| **Totale** | **Poor** | **18/40** |

## Carico cognitivo

Sei fallimenti su otto: alto. Toolbar e pannelli presentano troppe decisioni contemporanee; geometria, contenuto e verifica non sono sequenziati; i confini deboli sono conteggiati ma non localizzati; gli slider richiedono di ricordare quale linea della fotografia si sta correggendo.

## Evidenza browser e detector

Il detector meccanico ha restituito zero finding, ma non modella transizioni di stato o operazioni distruttive. In Chromium, a 1440×1000, una tabella reale 56×12 mostrava circa 8×8 celle; a 900×700 circa 6×7, con doppio scroll e senza coordinate sticky. Il server temporaneo e i file di prova sono stati rimossi; nessun overlay Impeccable è stato iniettato.

## Problemi prioritari

1. **P0 — Stato approvato esportabile aggirando readiness.** Il select chiamava la PATCH generica anche per `approved/exported`, bypassando `/approve`; il backend scriveva lo stato senza invarianti. Correzione backend applicata durante l'audit.
2. **P1 — Merge celle distruttivo.** Conserva solo il testo della cella in alto a sinistra e scarta gli altri contenuti, senza diff, conferma o undo.
3. **P1 — Chiusura editor distruttiva.** Escape, backdrop o Cancel scartano anche una lunga sessione 56×12 senza dirty guard.
4. **P1 — Incertezza non localizzata.** Il supporto del detector diventa solo “N confini deboli”; non indica linee o celle e non registra accept/reject/verificato.
5. **P1 — Geometria scollegata dalla prova.** Gli slider non sono sovrapposti alla fotografia e potevano produrre linee incrociate; il backend scartava vlines/hlines al salvataggio. Persistenza e monotonicità backend corrette durante l'audit.
6. **P2 — Densità senza contesto.** Mancano indici sticky, coordinate cella corrente e sincronizzazione con la riga fotografica; a larghezza laptop il canvas resta troppo stretto.
7. **P2 — Percorso tastiera incompleto.** Merge/range selection e geometria restano mouse-only.

## Persona red flags

- L'annotatore esperto non può annullare merge/rilevamento né saltare alla prossima cella incerta.
- Il validatore metodico non ha diff, audit trail o prova che ogni warning sia stato risolto.
- L'utente da tastiera/screen reader non può correggere range e bounding box con precisione.

## Osservazioni minori

- Le colonne fantasma sono mostrate ma non risultano facilmente marcabili/smarcabili.
- L'ingresso all'editor tabella è duplicato tra Inspector e pulsante separato.
- L'OTSL tecnico non è accompagnato da un riepilogo umano di celle, merge e anomalie.
- Versione del detector/modello e configurazione del prefill non sono parte visibile dell'audit trail.

## Questions to Consider

- La priorità UI deve essere la sicurezza delle modifiche o la manipolazione diretta dei confini sulla foto?
- Una tabella deve poter essere approvata dall'utente o solo derivare da invarianti backend più eventuale override motivato?
- Vogliamo mantenere l'editor a foglio di calcolo o trasformarlo in una coda di anomalie localizzate sulla fotografia?
