---
target: frontend pages (M1 + studio)
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 2
p1_count: 3
timestamp: 2026-08-26T17-23-25Z
slug: frontend-src-pages
---
# Critique — frontend/src/pages (Lloyds Lab, superficie M1 + studio M2)

Method: dual-agent (A: design review · B: detector CLI evidence). Browser non disponibile; evidenza B da detector con validazione fixture. Detector: 0 findings su 20 file (regex-engine scope; regole DOM/computed-style non valutate).

## Design Health Score: 23/40 (Acceptable)

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Autosave + polling training + GPU readout buoni; scansione senza progresso, report perso alla navigazione |
| 2 | Match System / Real World | 3 | Vocabolario dominio giusto; eccezioni raw `String(e)` ed enum inglesi nella UI italiana |
| 3 | User Control and Freedom | 2 | Backspace→delete committato da autosave 700ms; no server undo, no beforeunload, no conferma Stop |
| 4 | Consistency and Standards | 3 | Pattern uniformi; primary color spezzato (sky/emerald), stato pagina in due idiomi |
| 5 | Error Prevention | 1 | Delete-block senza attrito, path free-text, rebuild sovrascrive in silenzio; solo window.confirm |
| 6 | Recognition Rather Than Recall | 3 | Shortcut stampate, hint poligono, checklist; codici `short` palette senza hotkey reali |
| 7 | Flexibility and Efficiency | 2 | Undo/hotkey/autosave presenti; no bulk-edit metadati, no next/prev pagina, reorder click-by-click |
| 8 | Aesthetic and Minimalist Design | 2 | ~16 campi training senza disclosure; rail studio 5 pannelli; Dashboard spreca celle su sysinfo |
| 9 | Error Recovery | 1 | Ogni catch = `String(e)` in box rosso; no rimedio, no retry autosave, errori scansione troncati |
| 10 | Help and Documentation | 2 | Checklist convenzioni "guidata"; zero onboarding verso "crea progetto" |

## Design Specificity Verdict

Template admin dark-Tailwind intercambiabile: `index.css` è una riga, ogni superficie è slate-950/900/800 + sky-600, icone nav glifi Unicode stock. Carattere dominio solo nel copy (shipping, casualties, OTSL). Nulla evoca il giornale storico. Dashboard = dump sysinfo + roadmap milestone interna; footer sidebar "Milestone M0" (Layout.tsx:54). Detector: genuinamente pulito in scope regex, ma il deficit è di assenza (carattere, gerarchia, protezione) — non misurabile dal detector.

## Overall Impression

Meccanica giusta (autosave debounced, undo client, checklist, anteprime JSONL); sicurezza e lingua sbagliano dove il prodotto promette protezione. Opportunità singola più grande: mettere il giornale al centro della home.

## What's Working

1. Modello dati studio affidabile (autosave 700ms con riconciliazione id, undo/redo con keyboard binding — AnnotationPage.tsx:263-294).
2. "Guidato, non magico" eseguito: hint poligono, frecce reading order, checklist, anteprime JSONL ms-swift (DatasetPage.tsx:189-205).
3. Lingua form consistente e empty state che indicano l'azione (ProjectsPage.tsx:111-115).

## Priority Issues

1. **[P0] Cancellazione annotazione a un tasto dall'irrecuperabile** (StudioCanvas.tsx:192-208 + autosave AnnotationPage.tsx:272-276). Fix: Delete+selezione, toast undo con grace window, `beforeunload` su dirty. → `/impeccable harden`
2. **[P0] Cancellazione progetto protetta solo da window.confirm** (ProjectDetailPage.tsx:154-163). Fix: modale con typed-confirm + riepilogo contenuti. → `/impeccable harden`
3. **[P1] Nessun onboarding first-run** (DashboardPage.tsx:41-66, footer Layout.tsx:54). Fix: next-step card stateful; footer → versione app. → `/impeccable onboard`
4. **[P1] Errori = eccezioni raw senza recovery** (onnipresente). Fix: error-mapping `{messaggio, suggerimento}`, retry autosave. → `/impeccable clarify`
5. **[P1] Studio inaccessibile da tastiera** (LayersPanel.tsx:71-95 hover-only, canvas senza DOM, focus solo-bordo, modale tabelle senza Escape/trap). Fix: focus-within, focus ring token, LayersPanel keyboard-operable. → `/impeccable audit`
6. **[P2] Training: 16 hyperparametri senza disclosure + azione primaria spezzata** (TrainingPage.tsx:237-365). Fix: Preset + "Avanzate", colore primario unico, conferma Stop, stati terminali distinti. → `/impeccable distill`

## Persona Red Flags

- **Alex (power annotator):** ~1000 edit discreti per 200 pagine (no bulk/copy-previous); no next/prev pagina; codici `short` palette promettono hotkey assenti; Backspace-cancella mentre si digita.
- **Jordan (archivista primo utilizzo):** path assoluto a mano senza picker/validazione; landing su sysinfo per maintainer; 2/7 nav placeholder; checklist senza spiegazioni; nessun segnale "stai andando bene" prima di Dataset.
- **Sam (tastiera/screen reader):** blocchi canvas senza DOM, azioni layer hover-only, stato pagina colore-only (AnnotationPage.tsx:382-386), testo 10px funzionale, focus = tinta bordo. Non può annotare.

## Minor Observations

Enum inglesi raw nelle select; alt = rel_path; report scansione in stato React perso; `output_dir: undefined as unknown as string` (TrainingPage.tsx:35); grafico loss senza assi, LR non plottato; "Salva" manuale ridondante; no indicatore zoom; no route 404; thumbnail h-64 letterboxed su broadsheet.

## Questions to Consider

- Perché il giornale non è la home? Un muro di pagine tinte per stato risolverebbe identità + onboarding insieme.
- Se le convenzioni sono la pedagogia, perché checkbox passive invece di validazione live?
- A cosa serve lo stato pagina se nulla ci reagisce? "Prossima non annotata" dovrebbe essere il default dello studio.
