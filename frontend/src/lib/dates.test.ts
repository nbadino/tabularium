import { describe, expect, it } from 'vitest'
import { parseServerDate } from './dates'

describe('parseServerDate', () => {
  it('legge come UTC una data SQLite senza fuso', () => {
    expect(parseServerDate('2026-09-21 18:08:45')?.toISOString()).toBe('2026-09-21T18:08:45.000Z')
  })
  it('rispetta un fuso esplicito', () => {
    expect(parseServerDate('2026-09-21T18:08:45+02:00')?.toISOString()).toBe('2026-09-21T16:08:45.000Z')
  })
  it('restituisce null per valori vuoti o illeggibili', () => {
    expect(parseServerDate('')).toBeNull()
    expect(parseServerDate('ieri')).toBeNull()
  })
})
