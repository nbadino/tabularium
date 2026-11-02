import { describe, expect, it } from 'vitest'
import { legacyTarget } from './legacyRoutes'

describe('legacyTarget', () => {
  it('sends the old sections into the project they were about', () => {
    expect(legacyTarget('/archivio', '', 4)).toBe('/progetti/4')
    expect(legacyTarget('/riconosci', '?pages=1,2', 4)).toBe('/progetti/4/riconoscimenti?pages=1%2C2')
    expect(legacyTarget('/annotazione', '?project=2&page=9', 4)).toBe('/progetti/2/studio?page=9')
    expect(legacyTarget('/annotazione', '?page=9', 4)).toBe('/progetti/4/studio?page=9')
    expect(legacyTarget('/risultati', '?project=2&run=24', 4)).toBe('/progetti/2/riconoscimenti/24')
    expect(legacyTarget('/risultati', '', 4)).toBe('/progetti/4/riconoscimenti')
  })

  it('maps the fine-tuning steps and keeps their own parameters', () => {
    expect(legacyTarget('/fine-tuning', '', 3)).toBe('/progetti/3/fine-tuning')
    expect(legacyTarget('/dataset', '?project=5', 3)).toBe('/progetti/5/fine-tuning/dati')
    expect(legacyTarget('/training', '?view=new', 3)).toBe('/progetti/3/fine-tuning/addestra?view=new')
    expect(legacyTarget('/valutazione', '', 3)).toBe('/progetti/3/fine-tuning/valuta')
    expect(legacyTarget('/benchmark', '', 3)).toBe('/progetti/3/fine-tuning/confronta')
  })

  it('falls back to the project list when no project is known', () => {
    expect(legacyTarget('/annotazione', '', null)).toBe('/progetti')
    expect(legacyTarget('/training', '', null)).toBe('/progetti')
  })
})
