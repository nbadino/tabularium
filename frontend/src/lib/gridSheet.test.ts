import { describe, expect, it } from 'vitest'
import { columnName, gridChanged, gridToSheet, parseRange, sheetToGrid } from './gridSheet'
import type { TableGrid } from './types'

function grid(partial: Partial<TableGrid> = {}): TableGrid {
  return {
    rows: 3,
    cols: 3,
    cells: [],
    phantom_cols: [],
    header_rows: 0,
    ...partial,
  }
}

describe('gridToSheet', () => {
  it('scrive i merge nell’ordine della libreria: [colspan, rowspan]', () => {
    // La trappola: il nostro modello è rowspan/colspan. Se l'ordine non viene
    // invertito, un merge 2 righe × 1 colonna diventa 1 × 2 senza errori.
    const source = grid({
      cells: [
        { r: 0, c: 0, rowspan: 2, colspan: 1, text: 'verticale' },
        { r: 0, c: 1, rowspan: 1, colspan: 2, text: 'orizzontale' },
      ],
    })
    const sheet = gridToSheet(source)
    expect(sheet.mergeCells['A1:A2']).toEqual([1, 2])
    expect(sheet.mergeCells['B1:C1']).toEqual([2, 1])
  })

  it('mette il testo nella cella di origine e lascia vuote le coperte', () => {
    const sheet = gridToSheet(grid({ cells: [{ r: 1, c: 1, rowspan: 1, colspan: 2, text: 'x' }] }))
    expect(sheet.data[1][1]).toBe('x')
    expect(sheet.data[1][2]).toBe('')
    expect(sheet.data.length).toBe(3)
    expect(sheet.data[0].length).toBe(3)
  })
})

describe('sheetToGrid', () => {
  it('riporta i merge nel nostro ordine, anche in 2D', () => {
    const back = sheetToGrid(
      [
        ['titolo', '', ''],
        ['', '', ''],
        ['', '', ''],
      ],
      { 'A1:B2': [2, 2], 'C1:C3': [1, 3] },
      grid(),
    )
    const titolo = back.cells.find((c) => c.r === 0 && c.c === 0)!
    expect([titolo.rowspan, titolo.colspan]).toEqual([2, 2])
    const colonna = back.cells.find((c) => c.r === 0 && c.c === 2)!
    expect([colonna.rowspan, colonna.colspan]).toEqual([3, 1])
    // Le celle coperte non sono celle del nostro modello.
    expect(back.cells.some((c) => c.r === 1 && c.c === 0)).toBe(false)
  })

  it('conserva source e verified dove il testo non è cambiato', () => {
    const previous = grid({
      cells: [{ r: 0, c: 0, rowspan: 1, colspan: 1, text: 'ABADESA', source: 'ocr', verified: true }],
    })
    const same = sheetToGrid([['ABADESA', '', ''], ['', '', ''], ['', '', '']], {}, previous)
    const kept = same.cells.find((c) => c.r === 0 && c.c === 0)!
    expect(kept.source).toBe('ocr')
    expect(kept.verified).toBe(true)
  })

  it('un testo riscritto è una correzione umana da verificare', () => {
    const previous = grid({
      cells: [{ r: 0, c: 0, rowspan: 1, colspan: 1, text: 'ABADESA', source: 'ocr', verified: true }],
    })
    const edited = sheetToGrid([['ABADESA (a)', '', ''], ['', '', ''], ['', '', '']], {}, previous)
    const cell = edited.cells.find((c) => c.r === 0 && c.c === 0)!
    expect(cell.source).toBe('manual')
    expect(cell.verified).toBe(false)
  })

  it('la geometria non vive nel foglio e passa attraverso', () => {
    const previous = grid({
      phantom_cols: [2],
      vlines: [0, 0.3, 0.6, 1],
      hlines: [0, 0.5, 1],
      row_columns: [[0, 0.3, 0.6, 1]],
      row_columns_proven: [[true, true, true, true]],
    })
    const back = sheetToGrid([['a', 'b', 'c'], ['d', 'e', 'f'], ['g', 'h', 'i']], {}, previous)
    expect(back.phantom_cols).toEqual([2])
    expect(back.vlines).toEqual(previous.vlines)
    expect(back.hlines).toEqual(previous.hlines)
    expect(back.row_columns).toEqual(previous.row_columns)
    expect(back.row_columns_proven).toEqual(previous.row_columns_proven)
  })

  it('una colonna fantasma che non esiste più viene scartata', () => {
    const previous = grid({ phantom_cols: [1, 5] })
    const back = sheetToGrid([['a', 'b'], ['c', 'd']], {}, previous)
    expect(back.phantom_cols).toEqual([1])
  })
})

describe('utilità', () => {
  it('columnName oltre la Z', () => {
    expect(columnName(0)).toBe('A')
    expect(columnName(25)).toBe('Z')
    expect(columnName(26)).toBe('AA')
  })

  it('parseRange legge un intervallo e rifiuta il resto', () => {
    expect(parseRange('A1:C2')).toEqual({ r: 0, c: 0, r2: 1, c2: 2 })
    expect(parseRange('B3')).toEqual({ r: 2, c: 1, r2: 2, c2: 1 })
    expect(parseRange('non-un-range')).toBeNull()
  })

  it('gridChanged distingue una modifica da un giro a vuoto', () => {
    const before = grid({ cells: [{ r: 0, c: 0, rowspan: 1, colspan: 1, text: 'a' }] })
    const same = grid({ cells: [{ r: 0, c: 0, rowspan: 1, colspan: 1, text: 'a' }] })
    const edited = grid({ cells: [{ r: 0, c: 0, rowspan: 1, colspan: 1, text: 'b' }] })
    expect(gridChanged(before, same)).toBe(false)
    expect(gridChanged(before, edited)).toBe(true)
  })
})
