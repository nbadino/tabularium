import { describe, expect, it } from 'vitest'
import { emptyGrid } from './grid'
import { gridToUniver, univerToGrid, UNIVER_SHEET_ID } from './univerGrid'
import type { TableGrid } from './types'

/** 3x4 con geometria e metadati, come esce dal prefill. */
const g4 = (): TableGrid => {
  const base = emptyGrid(3, 4)
  return {
    ...base,
    header_rows: 1,
    phantom_cols: [2],
    vlines: [0, 0.25, 0.5, 0.75, 1],
    hlines: [0, 1 / 3, 2 / 3, 1],
    row_columns: [[0.24, 0.49, 0.74]],
    row_columns_proven: [[true, false, true]],
    cells: base.cells.map((c) => ({
      ...c,
      text: `${c.r}${c.c}`,
      source: 'ocr' as const,
      verified: false,
    })),
  }
}

const cellAt = (grid: TableGrid, r: number, c: number) => grid.cells.find((x) => x.r === r && x.c === c)
const sheetOf = (data: ReturnType<typeof gridToUniver>) => data.sheets![UNIVER_SHEET_ID]!

describe('gridToUniver', () => {
  it('porta testo e celle unite, e non materializza le celle vuote', () => {
    const grid = g4()
    const data = gridToUniver(grid)
    const sheet = sheetOf(data)

    expect(sheet.rowCount).toBe(3)
    expect(sheet.columnCount).toBe(4)
    expect(sheet.cellData![0]![1]!.v).toBe('01')
    // Le celle piene ci sono tutte; quelle vuote no.
    const presenti = Object.values(sheet.cellData!).reduce((n, row) => n + Object.keys(row).length, 0)
    expect(presenti).toBe(12)
  })

  it('una cella unita diventa un merge, una sola volta', () => {
    const grid = g4()
    const cells = grid.cells.map((c) =>
      c.r === 0 && c.c === 0 ? { ...c, rowspan: 2, colspan: 2 } : c,
    )
    const sheet = sheetOf(gridToUniver({ ...grid, cells: cells.filter((c) => !(c.r === 0 && c.c === 1)) }))
    expect(sheet.mergeData).toHaveLength(1)
    expect(sheet.mergeData![0]).toEqual({ startRow: 0, startColumn: 0, endRow: 1, endColumn: 1 })
  })

  it('le righe di intestazione si vedono: bloccate e in grassetto', () => {
    const data = gridToUniver(g4())
    const sheet = sheetOf(data)
    expect(sheet.freeze).toEqual({ xSplit: 0, ySplit: 1, startRow: 1, startColumn: 0 })
    expect(sheet.cellData![0]![0]!.s).toBe('tabHeader')
    expect(data.styles!.tabHeader).toEqual({ bl: 1, bg: { rgb: '#f1f0ec' } })
    // Fuori dall'intestazione nessuno stile.
    expect(sheet.cellData![1]![0]!.s).toBeUndefined()
  })
})

describe('univerToGrid', () => {
  it('il giro completo conserva testo, merge e geometria', () => {
    const grid = g4()
    const back = univerToGrid(gridToUniver(grid), grid)
    expect(back.rows).toBe(grid.rows)
    expect(back.cols).toBe(grid.cols)
    expect(back.cells.map((c) => c.text)).toEqual(grid.cells.map((c) => c.text))
    expect(back.vlines).toEqual(grid.vlines)
    expect(back.hlines).toEqual(grid.hlines)
    expect(back.row_columns).toEqual(grid.row_columns)
    expect(back.phantom_cols).toEqual([2])
    expect(back.header_rows).toBe(1)
  })

  it('il testo invariato conserva la sua storia', () => {
    const grid = g4()
    const back = univerToGrid(gridToUniver(grid), grid)
    expect(cellAt(back, 1, 1)?.source).toBe('ocr')
    expect(cellAt(back, 1, 1)?.verified).toBe(false)
  })

  it('il testo riscritto è una correzione umana da verificare', () => {
    const grid = g4()
    const data = gridToUniver(grid)
    data.sheets![UNIVER_SHEET_ID]!.cellData![1]![1]!.v = 'corretto'
    const back = univerToGrid(data, grid)
    expect(cellAt(back, 1, 1)?.text).toBe('corretto')
    expect(cellAt(back, 1, 1)?.source).toBe('manual')
    expect(cellAt(back, 1, 1)?.verified).toBe(false)
    // Il resto resta com'era.
    expect(cellAt(back, 1, 2)?.source).toBe('ocr')
  })

  it('una cella verificata che cambia torna da verificare', () => {
    const grid = g4()
    const verified = {
      ...grid,
      cells: grid.cells.map((c) => (c.r === 2 && c.c === 2 ? { ...c, verified: true } : c)),
    }
    const data = gridToUniver(verified)
    data.sheets![UNIVER_SHEET_ID]!.cellData![2]![2]!.v = 'riscritto'
    expect(cellAt(univerToGrid(data, verified), 2, 2)?.verified).toBe(false)
    // Non toccata, resta verificata.
    expect(cellAt(univerToGrid(gridToUniver(verified), verified), 2, 2)?.verified).toBe(true)
  })

  it('le dimensioni nuove ritagliano la geometria invece di inventarla', () => {
    const grid = g4()
    const data = gridToUniver(grid)
    data.sheets![UNIVER_SHEET_ID]!.rowCount = 2
    data.sheets![UNIVER_SHEET_ID]!.columnCount = 3
    const back = univerToGrid(data, grid)
    expect(back.rows).toBe(2)
    expect(back.cols).toBe(3)
    expect(back.vlines).toHaveLength(4)
    expect(back.hlines).toHaveLength(3)
    expect(back.cells.every((c) => c.r < 2 && c.c < 3)).toBe(true)
    // La colonna fantasma 2 esiste ancora (le colonne sono 3)…
    expect(back.phantom_cols).toEqual([2])

    // …ma sparisce quando la colonna non c'è più.
    const narrower = gridToUniver(grid)
    narrower.sheets![UNIVER_SHEET_ID]!.columnCount = 2
    expect(univerToGrid(narrower, grid).phantom_cols).toEqual([])
  })
})
