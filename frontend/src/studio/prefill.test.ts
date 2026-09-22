import { describe, expect, it } from 'vitest'
import {
  defaultPrefillMode,
  prefillNeedsConfirm,
  prefillSeverity,
  replacementPlan,
  summarizeForPrefill,
  summarizePage,
  type PrefillBlockLike,
} from './prefill'

function block(partial: Partial<PrefillBlockLike>): PrefillBlockLike {
  return { prefill: null, confirmed: false, label: 'Text', ...partial }
}

describe('summarizeForPrefill', () => {
  it('conta bozze, blocchi e tabelle', () => {
    const summary = summarizeForPrefill([
      block({ prefill: 'rapidocr:0.9' }),
      block({ prefill: 'rapidocr:0.8' }),
      block({ prefill: 'model:x', confirmed: true }),
      block({}),
      block({ label: 'Table' }),
    ])
    expect(summary).toEqual({ blocks: 5, drafts: 2, tables: 1 })
  })

  it('una bozza confermata non è più una bozza', () => {
    const summary = summarizeForPrefill([block({ prefill: 'rapidocr:0.9', confirmed: true })])
    expect(summary.drafts).toBe(0)
  })
})

describe('defaultPrefillMode', () => {
  it('pagina vuota: merge', () => {
    expect(defaultPrefillMode({ blocks: 0, drafts: 0, tables: 0 })).toBe('merge')
  })

  it('con bozze non confermate: sostituisci le bozze', () => {
    expect(defaultPrefillMode({ blocks: 3, drafts: 2, tables: 0 })).toBe('replace_drafts')
  })

  it('con solo lavoro umano: aggiungi, non cancellare', () => {
    expect(defaultPrefillMode({ blocks: 4, drafts: 0, tables: 1 })).toBe('merge')
  })
})

describe('replacementPlan', () => {
  const summary = { blocks: 5, drafts: 2, tables: 1 }

  it('merge non cancella nulla', () => {
    expect(replacementPlan('merge', summary)).toEqual({ blocks: 0, drafts: 0, tables: 0 })
  })

  it('replace_drafts cancella solo le bozze, mai le tabelle umane', () => {
    expect(replacementPlan('replace_drafts', summary)).toEqual({
      blocks: 2,
      drafts: 2,
      tables: 0,
    })
  })

  it('replace_all cancella tutto', () => {
    expect(replacementPlan('replace_all', summary)).toEqual(summary)
  })
})

describe('prefillNeedsConfirm', () => {
  it('pagina vuota: nessuna conferma', () => {
    expect(
      prefillNeedsConfirm('replace_all', { blocks: 0, drafts: 0, tables: 0 }),
    ).toBe(false)
  })

  it('merge su pagina piena: nessuna conferma necessaria', () => {
    expect(prefillNeedsConfirm('merge', { blocks: 5, drafts: 0, tables: 1 })).toBe(false)
  })

  it('qualsiasi modalità sostitutiva su pagina piena: conferma', () => {
    expect(prefillNeedsConfirm('replace_drafts', { blocks: 5, drafts: 2, tables: 1 })).toBe(true)
    expect(prefillNeedsConfirm('replace_all', { blocks: 5, drafts: 2, tables: 1 })).toBe(true)
  })
})

describe('prefillSeverity', () => {
  it('replace_all su sole bozze resta una cancellazione leggera', () => {
    expect(prefillSeverity('replace_all', { blocks: 2, drafts: 2, tables: 0 })).toBe('drafts')
  })

  it('replace_all con lavoro umano è grave', () => {
    expect(prefillSeverity('replace_all', { blocks: 5, drafts: 2, tables: 1 })).toBe('human')
  })

  it('le altre modalità non sono mai gravi', () => {
    expect(prefillSeverity('merge', { blocks: 5, drafts: 2, tables: 1 })).toBe('none')
    expect(prefillSeverity('replace_drafts', { blocks: 5, drafts: 2, tables: 1 })).toBe('none')
  })
})

describe('summarizePage', () => {
  it('conta anche le bozze che vivono nel pannello, non solo quelle sul canvas', () => {
    // Il canvas porta il lavoro umano e le bozze di tabella; le altre bozze
    // stanno nel pannello contenuti. Il dialog di conferma deve vederle tutte,
    // altrimenti sottostima ciò che la modalità scelta rimuove.
    const onCanvas = [
      { label: 'Title', prefill: null, confirmed: true },
      { label: 'Table', prefill: 'model:official', confirmed: false },
    ]
    const inPane = [
      { label: 'Text', confirmed: false },
      { label: 'Text', confirmed: false },
      { label: 'Issue-date', confirmed: false },
    ]
    const summary = summarizePage(onCanvas, inPane)
    expect(summary.blocks).toBe(5)
    expect(summary.drafts).toBe(4)
    expect(summary.tables).toBe(1)
  })

  it('senza bozze nel pannello coincide con il solo canvas', () => {
    const blocks = [{ label: 'Title', prefill: null, confirmed: true }]
    expect(summarizePage(blocks, [])).toEqual(summarizeForPrefill(blocks))
  })
})
