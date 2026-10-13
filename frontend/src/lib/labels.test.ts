import { describe, expect, it } from 'vitest'

import {
  aliasesToText,
  customsOf,
  draftError,
  isRemovable,
  knownByModel,
  parseAliases,
  requiresPrompt,
} from './labels'
import type { LabelDef } from './types'

function label(patch: Partial<LabelDef>): LabelDef {
  return {
    name: 'X',
    color: '#4a5560',
    short: 'X',
    prompt_kind: 'text',
    prompt: 'p',
    origin: 'custom',
    builtin: false,
    aliases: [],
    known_models: [],
    ...patch,
  }
}

describe('labels helpers', () => {
  it('distingue i default (non rimovibili) dalle custom del progetto', () => {
    expect(isRemovable(label({ builtin: true }))).toBe(false)
    expect(isRemovable(label({ builtin: false }))).toBe(true)
    const list = [
      label({ name: 'Title', builtin: true }),
      label({ name: 'Vessel-name', builtin: false }),
    ]
    expect(customsOf(list).map((l) => l.name)).toEqual(['Vessel-name'])
  })

  it('«nota» dipende dal modello attivo', () => {
    const l = label({ known_models: ['paddleocr-vl', 'teleocr'] })
    expect(knownByModel(l, 'paddleocr-vl')).toBe(true)
    expect(knownByModel(l, 'mineru2.5')).toBe(false)
    // Senza modello attivo: nota ad almeno un modello.
    expect(knownByModel(l, null)).toBe(true)
    expect(knownByModel(label({ known_models: [] }), null)).toBe(false)
  })

  it('richiede il prompt solo per testo, tabella e formula', () => {
    expect(requiresPrompt('text')).toBe(true)
    expect(requiresPrompt('table')).toBe(true)
    expect(requiresPrompt('formula')).toBe(true)
    expect(requiresPrompt('picture')).toBe(false)
    expect(requiresPrompt('structure')).toBe(false)
  })

  it('valida la bozza: nome, duplicati, colore, abbreviazione, prompt', () => {
    const ok = { name: 'Vessel-name', color: '#3a5f7d', short: 'VSL', prompt_kind: 'text' as const, prompt: 'text', aliases: [] }
    expect(draftError(ok, [])).toBeNull()
    expect(draftError({ ...ok, name: '1bad' }, [])).toBe('name')
    expect(draftError(ok, ['Vessel-name'])).toBe('duplicate')
    // In modifica, il proprio nome non è un duplicato.
    expect(draftError(ok, ['Vessel-name'], 'Vessel-name')).toBeNull()
    expect(draftError({ ...ok, color: 'rosso' }, [])).toBe('color')
    expect(draftError({ ...ok, short: '' }, [])).toBe('short')
    expect(draftError({ ...ok, prompt: '' }, [])).toBe('prompt')
    // Un tipo strutturale non richiede prompt.
    expect(draftError({ ...ok, prompt_kind: 'structure', prompt: '' }, [])).toBeNull()
  })

  it('legge e scrive i sinonimi nativi', () => {
    expect(parseAliases('table, Table_document , grid')).toEqual(['table', 'Table_document', 'grid'])
    expect(parseAliases(' a, a , , b ')).toEqual(['a', 'b'])
    expect(aliasesToText(['table', 'grid'])).toBe('table, grid')
  })
})
