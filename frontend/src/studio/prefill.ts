/**
 * Decisioni del prefill, senza React.
 *
 * Il prefill è l'unica operazione dello studio che può cancellare lavoro
 * esistente (il backend esegue un DELETE e le griglie seguono via
 * ON DELETE CASCADE). La scelta di cosa sostituire — e la conferma che
 * l'utente deve dare — non deve stare dispersa nel JSX: qui è logica pura,
 * testabile, e la UI la esegue alla lettera.
 */

/** Cosa succede ai blocchi già presenti sulla pagina. Rispecchia il backend. */
export type PrefillMode = 'merge' | 'replace_drafts' | 'replace_all'

/** Modalità legacy accettata dal backend per compatibilità: sostituisce tutto. */
export const LEGACY_REPLACE_ALL = 'replace_all' as const

export interface PrefillBlockLike {
  /** Provenienza prefill (`rapidocr:0.91`, `model:...`) o null se manuale. */
  prefill?: string | null
  confirmed?: boolean
  label?: string
}

/** Conteggio di ciò che il prefill incontrerebbe sulla pagina corrente. */
export interface PrefillPageSummary {
  /** Tutti i blocchi presenti. */
  blocks: number
  /** Bozze del prefill non ancora confermate: l'unico lavoro che si può
   *  rifare senza perdita. */
  drafts: number
  /** Blocchi Table: sul backend portano una griglia che viene distrutta
   *  insieme al blocco se la modalità è sostitutiva totale. */
  tables: number
}

export function summarizeForPrefill(blocks: PrefillBlockLike[]): PrefillPageSummary {
  return {
    blocks: blocks.length,
    drafts: blocks.filter((b) => b.prefill != null && !b.confirmed).length,
    tables: blocks.filter((b) => b.label === 'Table').length,
  }
}

/**
 * La modalità proposta all'utente quando apre il dialogo:
 * - con bozze non confermate il senso del prefill è rifarle (`replace_drafts`);
 * - con solo lavoro umano la scelta sicura è aggiungere (`merge`): l'utente
 *   ha disegnato quei blocchi, cancellarli non è mai il default;
 * - pagina vuota: tutte le modalità sono equivalenti, si parte da merge.
 */
export function defaultPrefillMode(summary: PrefillPageSummary): PrefillMode {
  if (summary.blocks === 0) return 'merge'
  return summary.drafts > 0 ? 'replace_drafts' : 'merge'
}

/** Quanto lavoro la modalità eliminerebbe, per la frase di conferma. */
export function replacementPlan(
  mode: PrefillMode,
  summary: PrefillPageSummary,
): { blocks: number; drafts: number; tables: number } {
  switch (mode) {
    case 'merge':
      return { blocks: 0, drafts: 0, tables: 0 }
    case 'replace_drafts':
      // Le tabelle del lavoro umano non sono bozze: sopravvivono.
      return { blocks: summary.drafts, drafts: summary.drafts, tables: 0 }
    case 'replace_all':
      return summary
  }
}

/**
 * Serve una conferma esplicita? Sì appena c'è qualcosa che la modalità scelta
 * cancellerebbe: una pagina vuota non chiede il permesso di riempirsi.
 */
export function prefillNeedsConfirm(
  mode: PrefillMode,
  summary: PrefillPageSummary,
): boolean {
  const plan = replacementPlan(mode, summary)
  return plan.blocks > 0
}

/** Gravità della cancellazione: determina il tono della conferma. */
export type PrefillSeverity = 'none' | 'drafts' | 'human'

export function prefillSeverity(
  mode: PrefillMode,
  summary: PrefillPageSummary,
): PrefillSeverity {
  if (mode !== 'replace_all') return 'none'
  // replace_all tocca anche il lavoro umano quando esistono blocchi
  // manuali o confermati oltre alle bozze.
  const human = summary.blocks - summary.drafts
  return human > 0 ? 'human' : 'drafts'
}
