# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Pubblico potenziale open source: ricercatori e archivisti digitali che lavorano su giornali
storici con layout complessi (multi-colonna, tabelle dense) e vogliono affinare un modello di
document parsing sul proprio corpus. Uso reale corrente: studio self-hosted, multi-utente —
primo avvio con setup dell'amministratore, login con sessioni (cookie HttpOnly), ruoli globali
(admin/editor/viewer), accesso ai progetti per proprietario o membro, impostazioni istanza e
gestione utenti (creazione, attivazione, reset password, eliminazione). La registrazione è
chiusa di default e l'admin la apre dalle Impostazioni; `TABULARIUM_AUTH=off` conserva la
modalità locale a utente singolo per chi non vuole login.

## Product Purpose

Tabularium accompagna l'utente dall'archivio di scansioni al modello affinato: registra le
pagine dell'archivio (immagini/PDF) con metadati, guida l'annotazione super-dettagliata
(blocchi semantici, tabelle con celle unite, ordine di lettura, trascrizione con convenzioni),
esporta il dataset nel formato ufficiale ms-swift (JSONL, coordinate 0–1000, tabelle OTSL),
genera e lancia il training di MonkeyOCRv2-Parsing (LoRA/SFT) con monitoraggio live, e valuta
su pagine mai viste (TEDS, CER/WER, IoU layout, ordine di lettura) per iterare.

Successo = il proprietario di un archivio riesce, da solo, a produrre un modello affinato che
parsa correttamente pagine mai viste del suo corpus — senza toccare a mano JSONL, OTSL o script
di training.

## Positioning

Unico strumento end-to-end guidato per il fine-tuning di MonkeyOCRv2-Parsing su corpus storici:
l'annotazione è pensata per layout giornalistico d'epoca (tabelle con celle unite, colonne
"fantasma", ordine di lettura multi-colonna), l'export rispetta rigorosamente le convenzioni
ufficiali del modello (JSONL ms-swift, coordinate 0–1000, OTSL verificato contro `otsl_to_html`),
e il training usa gli script/iperparametri canonici del repo ufficiale — nessuna reinventazione
del pipeline.

## Operating Context

- Web app locale multipiattaforma (Windows/Linux/macOS): un solo processo backend FastAPI che
  serve la UI e orchestra tutto; annotazione ed export funzionano ovunque.
- Il training richiede GPU NVIDIA con CUDA (env dedicato `monkeyocrv2-train`), tipicamente su
  Linux; il repo ufficiale `Yuliang-Liu/MonkeyOCRv2` resta un checkout esterno configurabile,
  mai modificato.
- Sessioni di annotazione lunghe su scansioni ad alta risoluzione: zoom/pan fluido, scorri-
  mute da tastiera, autosave, undo/redo, checklist convenzioni sempre visibile.
- Dati reali dell'utente: nessuna distruzione automatica; ogni operazione distruttiva richiede
  conferma esplicita.
- Lingue del progetto: documentazione e commenti in italiano, identificatori e log in inglese.

## Capabilities and Constraints

- M0–M8 completati e in uso quotidiano: scaffolding, progetti e pagine, studio di annotazione,
  tabelle e reading order, dataset builder, training center, valutazione e playground,
  pseudo-labeling, build servita dal backend. Sopra di esso il layer self-hosted (setup iniziale,
  login, ruoli, impostazioni istanza, gestione utenti). Roadmap e dettagli in `AGENTS.md` §12.
- Convenzioni dati non negoziabili (verificate su `core_runner.py` ufficiale): JSONL ms-swift
  con path assoluti, coordinate normalizzate 0–1000 solo in export, tabelle in OTSL, prompt
  ufficiali §2.3 di AGENTS.md.
- Tassonomia estendibile per progetto: 11 label pubbliche del parsing + classi custom giornale
  (`Issue-header, Column, Headline, Byline, Advertisement, Note`).
- Backend senza PyTorch: training/inferenza girano in processi/env separati (subprocess, vLLM,
  SSE per i log); il dashboard non dipende mai dal training in esecuzione.
- Storage: SQLite + filesystem; percorsi mai hard-coded (env var `TABULARIUM_ROOT`).
- In decisamente deciso: destinazione del rilascio pubblico (licenza, repo pubblico, momento).

## Brand Commitments

- Nome: **Tabularium**, legato al corpus di riferimento "Historic Shipping Index" (1900s).
- Il prodotto è candidato a rilascio pubblico/open source: naming, UX e documentazione devono
  reggere l'esposizione a un pubblico esterno.

## Evidence on Hand

- Corpus corrente: progetto "Tabularium Smoke" con 3 pagine registrate in `data/tabularium.db`;
  thumbnails in `data/thumbs/`.
- Fonte di verità tecnica: `AGENTS.md` (contesto ufficiale MonkeyOCRv2, convenzioni dati,
  iperparametri, roadmap, regole operative).
- Nessun corpus esterno verificato, nessuna metrica pubblicata, nessun testimonial: i claim
  futuri sulle metriche vanno prodotti dalla fase di valutazione (M6), mai inventati.

## Product Principles

1. **Fedeltà all'ufficiale**: ogni convenzione dati/prompt/formato segue il repo ufficiale
   MonkeyOCRv2; in dubbio, verificare il codice sorgente, mai approssimare.
2. **Guidato, non magico**: ogni correzione manuale dell'utente è dato di training; il tool
   mostra e insegna le convenzioni invece di nasconderle.
3. **I dati dell'utente sono sacri**: nessuna distruzione automatica, conferme esplicite,
   log/replay sempre disponibili.
4. **Funziona ovunque si annota**: la preparazione dati è multipiattaforma; solo il training
   richiede la GPU dedicata.
5. **Iterazione misurata**: ogni ciclo annotazione→training→valutazione produce metriche
   (TEDS, CER/WER, IoU) che guidano il ciclo successivo.
