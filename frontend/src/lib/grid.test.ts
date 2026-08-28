import { describe, expect, it } from 'vitest'
import {
  dropBoundary,
  emptyGrid,
  insertBoundary,
  mergeRange,
  resizeGrid,
  splitCell,
} from './grid'
import type { TableGrid } from './types'

describe('table grid operations', () => {
  it('merges and splits a physical range without losing anchor text', () => {
    const base = emptyGrid(2, 2)
    base.cells[0].text = 'header'
    const merged = mergeRange(base, 0, 0, 0, 1)
    expect(merged?.cells).toHaveLength(3)
    expect(merged?.cells.find((c) => c.colspan === 2)?.text).toBe('header')
    const split = merged && splitCell(merged, 0, 1)
    expect(split?.cells).toHaveLength(4)
    expect(split?.cells.find((c) => c.r === 0 && c.c === 0)?.text).toBe('header')
  })

  it('keeps adjustable ruling positions when resizing', () => {
    const grid = emptyGrid(2, 2)
    grid.vlines![1] = 0.42
    const resized = resizeGrid(grid, 3, 2)
    expect(resized.vlines?.[1]).toBe(0.42)
    expect(resized.hlines).toHaveLength(4)
  })
})

describe('confini: rifiuto e inserimento', () => {
  /** Griglia 2x3 con testo riconoscibile in ogni cella. */
  const g3 = (): TableGrid => {
    const base = emptyGrid(2, 3)
    return {
      ...base,
      vlines: [0, 0.3, 0.6, 1],
      hlines: [0, 0.5, 1],
      cells: base.cells.map((c) => ({ ...c, text: `${c.r}${c.c}` })),
    }
  }

  it('rifiutare un confine fonde le due colonne e unisce il testo', () => {
    const out = dropBoundary(g3(), 'v', 1)!
    expect(out.cols).toBe(2)
    expect(out.vlines).toEqual([0, 0.6, 1])
    const at = (r: number, c: number) => out.cells.find((x) => x.r === r && x.c === c)
    // Le colonne 0 e 1 erano una sola sulla pagina: il testo si concatena.
    expect(at(0, 0)?.text).toBe('00 01')
    expect(at(1, 0)?.text).toBe('10 11')
    // La terza colonna scorre a sinistra e conserva il proprio testo.
    expect(at(0, 1)?.text).toBe('02')
  })

  it('una cella unita che attraversa il confine perde una colonna, non il testo', () => {
    const merged = mergeRange(g3(), 0, 0, 0, 1)!
    const out = dropBoundary(merged, 'v', 1)!
    const spanning = out.cells.find((c) => c.r === 0 && c.c === 0)!
    expect(spanning.colspan).toBe(1)
    expect(spanning.text).toBe('00')
  })

  it('rifiuta la fusione ambigua invece di sceglierla al posto dell utente', () => {
    // Una cella unita in verticale sulla colonna 0 e celle singole sulla 1:
    // fondere le due colonne non ha un esito unico.
    const merged = mergeRange(g3(), 0, 0, 1, 0)!
    expect(dropBoundary(merged, 'v', 1)).toBeNull()
  })

  it('non tocca i bordi esterni: sono il contorno del contenuto, non confini', () => {
    expect(dropBoundary(g3(), 'v', 0)).toBeNull()
    expect(dropBoundary(g3(), 'v', 3)).toBeNull()
  })

  it('inserire un confine spezza la colonna e lascia il testo a sinistra', () => {
    const out = insertBoundary(g3(), 'v', 0.45)!
    expect(out.cols).toBe(4)
    expect(out.vlines).toEqual([0, 0.3, 0.45, 0.6, 1])
    const at = (r: number, c: number) => out.cells.find((x) => x.r === r && x.c === c)
    expect(at(0, 1)?.text).toBe('01')
    expect(at(0, 2)?.text).toBe('')
    expect(at(0, 3)?.text).toBe('02')
  })

  it('inserire e poi rifiutare lo stesso confine riporta al numero di colonne di partenza', () => {
    const start = g3()
    const out = dropBoundary(insertBoundary(start, 'v', 0.45)!, 'v', 2)!
    expect(out.cols).toBe(start.cols)
    expect(out.vlines).toEqual(start.vlines)
  })

  it('un confine fuori dal contenuto o sopra un altro non si inserisce', () => {
    expect(insertBoundary(g3(), 'v', 0)).toBeNull()
    expect(insertBoundary(g3(), 'v', 1)).toBeNull()
    expect(insertBoundary(g3(), 'v', 0.3)).toBeNull()
  })

  it('funziona identico sulle righe', () => {
    const out = dropBoundary(g3(), 'h', 1)!
    expect(out.rows).toBe(1)
    expect(out.hlines).toEqual([0, 1])
    expect(out.cells.find((c) => c.r === 0 && c.c === 0)?.text).toBe('00 10')
  })
})
