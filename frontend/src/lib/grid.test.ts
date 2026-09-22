import { describe, expect, it } from 'vitest'
import {
  deleteTrack,
  dropBoundary,
  emptyGrid,
  insertBoundary,
  fillDown,
  insertTrack,
  joinColumns,
  mergeKeepsEveryText,
  mergeRange,
  normalizeColumn,
  splitColumn,
  transformColumnCase,
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

describe('inserimento di una traccia', () => {
  /** 3x4 con geometria esplicita, come uscirebbe dal rilevatore. */
  const g4 = (): TableGrid => {
    const base = emptyGrid(3, 4)
    return {
      ...base,
      vlines: [0, 0.25, 0.5, 0.75, 1],
      hlines: [0, 1 / 3, 2 / 3, 1],
      cells: base.cells.map((c) => ({ ...c, text: `${c.r}${c.c}` })),
    }
  }

  /** Il contratto che il backend verifica: confini crescenti in (0, 1). */
  const increasing = (values: number[]) =>
    values.every((v, i) => i === 0 || v > values[i - 1])

  const at = (out: TableGrid, r: number, c: number) => out.cells.find((x) => x.r === r && x.c === c)

  it('alloca al nuovo confine una larghezza positiva, non una traccia sovrapposta', () => {
    const out = insertTrack(g4(), 'col', 2)
    expect(out.cols).toBe(5)
    expect(out.vlines).toHaveLength(6)
    expect(increasing(out.vlines!)).toBe(true)
    // La traccia nuova prende metà della vicina: la colonna 2 diventa 0.5–0.625.
    expect(out.vlines!.slice(2, 4)).toEqual([0.5, 0.625])
    expect(at(out, 0, 2)?.text).toBe('')
    expect(at(out, 0, 3)?.text).toBe('02')
  })

  it('funziona in testa, dove non c è spazio fuori dal ritaglio', () => {
    const out = insertTrack(g4(), 'col', 0)
    expect(out.cols).toBe(5)
    expect(out.vlines![0]).toBe(0)
    expect(increasing(out.vlines!)).toBe(true)
    expect(at(out, 0, 0)?.text).toBe('')
    expect(at(out, 0, 1)?.text).toBe('00')
  })

  it('funziona in coda', () => {
    const out = insertTrack(g4(), 'col', 4)
    expect(out.cols).toBe(5)
    expect(out.vlines![out.vlines!.length - 1]).toBe(1)
    expect(increasing(out.vlines!)).toBe(true)
    expect(out.cells.filter((x) => x.c === 4)).toHaveLength(3)
  })

  it('vale identico sulle righe', () => {
    const out = insertTrack(g4(), 'row', 1)
    expect(out.rows).toBe(4)
    expect(out.hlines).toHaveLength(5)
    expect(increasing(out.hlines!)).toBe(true)
    expect(out.cells.filter((x) => x.r === 1)).toHaveLength(4)
  })

  it('non lascia buchi: ogni posizione ha la sua cella', () => {
    for (const axis of ['row', 'col'] as const) {
      for (const index of [0, 1, 3, 4]) {
        const out = insertTrack(g4(), axis, index)
        expect(out.cells).toHaveLength(out.rows * out.cols)
      }
    }
  })

  it('tiene allineati i confini piegati alla nuova cardinalità', () => {
    const base: TableGrid = {
      ...g4(),
      row_columns: Array.from({ length: 3 }, () => [0.25, 0.5, 0.75]),
      row_columns_proven: Array.from({ length: 3 }, () => [true, true, true]),
    }

    // Colonna: ogni riga guadagna un confine interno, non provato.
    const byCol = insertTrack(base, 'col', 1)
    expect(byCol.row_columns).toHaveLength(3)
    for (const row of byCol.row_columns!) {
      expect(row).toHaveLength(byCol.cols - 1)
      expect(increasing(row)).toBe(true)
    }
    expect(byCol.row_columns_proven![0]).toEqual([true, false, true, true])

    // Riga: una riga in più, stesso numero di confini interni.
    const byRow = insertTrack(base, 'row', 1)
    expect(byRow.row_columns).toHaveLength(4)
    for (const row of byRow.row_columns!) expect(row).toHaveLength(byRow.cols - 1)
    expect(byRow.row_columns_proven![1]).toEqual([false, false, false])
  })
})

describe('i confini piegati seguono ogni cambio di struttura', () => {
  /** Le stesse regole che il backend applica al PUT /blocks/{id}/table. */
  const valid = (grid: TableGrid) => {
    const increasing = (values: number[]) => values.every((v, i) => i === 0 || v > values[i - 1])
    if (grid.vlines?.length) {
      expect(grid.vlines).toHaveLength(grid.cols + 1)
      expect(increasing(grid.vlines)).toBe(true)
    }
    if (grid.hlines?.length) {
      expect(grid.hlines).toHaveLength(grid.rows + 1)
      expect(increasing(grid.hlines)).toBe(true)
    }
    if (grid.row_columns?.length) {
      expect(grid.row_columns).toHaveLength(grid.rows)
      expect(grid.row_columns_proven).toHaveLength(grid.rows)
      for (const [i, row] of grid.row_columns.entries()) {
        expect(row).toHaveLength(grid.cols - 1)
        expect(increasing(row)).toBe(true)
        expect(grid.row_columns_proven![i]).toHaveLength(row.length)
      }
    }
  }

  /** 3x4 con confini piegati, come li persiste un rilevamento. */
  const withColumns = (): TableGrid => ({
    ...emptyGrid(3, 4),
    vlines: [0, 0.25, 0.5, 0.75, 1],
    hlines: [0, 1 / 3, 2 / 3, 1],
    row_columns: Array.from({ length: 3 }, (_, r) => [0.24 + r * 0.01, 0.49 + r * 0.01, 0.74 + r * 0.01]),
    row_columns_proven: Array.from({ length: 3 }, () => [true, true, true]),
  })

  it('inserire e togliere una traccia', () => {
    for (const axis of ['row', 'col'] as const) {
      for (const at of [0, 2, 4]) {
        const inserted = insertTrack(withColumns(), axis, at)
        valid(inserted)
        valid(deleteTrack(inserted, axis, 1)!)
      }
    }
  })

  it('aggiungere e rifiutare un confine', () => {
    const byColumn = insertBoundary(withColumns(), 'v', 0.4)!
    valid(byColumn)
    valid(dropBoundary(byColumn, 'v', 2)!)

    const byRow = insertBoundary(withColumns(), 'h', 0.5)!
    valid(byRow)
    valid(dropBoundary(byRow, 'h', 1)!)
  })

  it('senza confini piegati non ne inventa', () => {
    const plain: TableGrid = { ...emptyGrid(3, 4), vlines: [0, 0.25, 0.5, 0.75, 1] }
    const out = insertTrack(plain, 'col', 1)
    expect(out.row_columns ?? []).toHaveLength(0)
    expect(out.vlines).toHaveLength(out.cols + 1)
  })
})

describe('separare una colonna sul separatore', () => {
  /** 4 righe x 3 colonne con testo di registro nella colonna 1. */
  const register = (): TableGrid => {
    const base = emptyGrid(4, 3)
    const texts = [
      ['', 'Doris .. (Br)', '1'],
      ['', 'Aagtekerk .. (Ne)', '2'],
      ['', 'Nettuno', '3'],
      ['', '', '4'],
    ]
    return {
      ...base,
      header_rows: 1,
      vlines: [0, 0.2, 0.6, 1],
      hlines: [0, 0.25, 0.5, 0.75, 1],
      cells: base.cells.map((c) => ({
        ...c,
        text: texts[c.r][c.c],
        source: 'ocr' as const,
        verified: false,
      })),
    }
  }

  const at = (grid: TableGrid, r: number, c: number) =>
    grid.cells.find((x) => x.r === r && x.c === c)

  it('divide in due colonne sullo spazio e lascia il resto al suo posto', () => {
    const out = splitColumn(register(), 1, { separator: ' ' })!
    expect(out.cols).toBe(4)
    expect(at(out, 1, 1)?.text).toBe('Aagtekerk')
    expect(at(out, 1, 2)?.text).toBe('.. (Ne)')
    // La colonna di destra slitta e il suo testo non si perde.
    expect(at(out, 1, 3)?.text).toBe('2')
    // Righe senza separatore: intatte.
    expect(at(out, 2, 1)?.text).toBe('Nettuno')
    expect(at(out, 2, 2)?.text).toBe('')
  })

  it('le sequenze di spazi sono un separatore solo', () => {
    const grid = register()
    grid.cells = grid.cells.map((c) =>
      c.r === 1 && c.c === 1 ? { ...c, text: 'Aagtekerk   ..   (Ne)' } : c,
    )
    const out = splitColumn(grid, 1, { separator: ' ' })!
    expect(at(out, 1, 1)?.text).toBe('Aagtekerk')
    expect(at(out, 1, 2)?.text).toBe('.. (Ne)')
  })

  it('con tre colonne i pezzi in più restano uniti nell ultima', () => {
    const out = splitColumn(register(), 1, { separator: ' ', maxParts: 3 })!
    expect(out.cols).toBe(5)
    // «Doris .. (Br)» su tre colonne: Doris | .. | (Br)
    expect(at(out, 0, 1)?.text).toBe('Doris')
    expect(at(out, 0, 2)?.text).toBe('..')
    expect(at(out, 0, 3)?.text).toBe('(Br)')

    const many: TableGrid = {
      ...register(),
      cells: register().cells.map((c) =>
        c.r === 1 && c.c === 1 ? { ...c, text: 'uno due tre quattro' } : c,
      ),
    }
    const capped = splitColumn(many, 1, { separator: ' ', maxParts: 2 })!
    expect(capped.cols).toBe(4)
    expect(at(capped, 1, 1)?.text).toBe('uno')
    expect(at(capped, 1, 2)?.text).toBe('due tre quattro')
  })

  it('una virgola divide allo stesso modo', () => {
    const grid = register()
    grid.cells = grid.cells.map((c) =>
      c.r === 1 && c.c === 1 ? { ...c, text: 'Aagtekerk, 1924, Ne' } : c,
    )
    const out = splitColumn(grid, 1, { separator: ',' })!
    expect(at(out, 1, 1)?.text).toBe('Aagtekerk')
    expect(at(out, 1, 2)?.text).toBe('1924, Ne')
  })

  it('il testo diviso va riguardato: non resta verificato', () => {
    const grid = register()
    grid.cells = grid.cells.map((c) => (c.r === 1 && c.c === 1 ? { ...c, verified: true } : c))
    const out = splitColumn(grid, 1, { separator: ' ' })!
    expect(at(out, 1, 1)?.verified).toBe(false)
    expect(at(out, 1, 1)?.source).toBe('manual')
    // La riga che non si è divisa conserva la sua storia.
    expect(at(out, 2, 1)?.source).toBe('ocr')
  })

  it('geometria e confini piegati seguono le colonne nuove', () => {
    const out = splitColumn(register(), 1, { separator: ' ' })!
    expect(out.vlines).toHaveLength(out.cols + 1)
    expect((out.vlines ?? []).every((v, i, all) => i === 0 || v > all[i - 1])).toBe(true)
    expect(out.phantom_cols).toEqual([])
  })

  it('rifiuta di dividere una colonna dentro una cella unita', () => {
    const merged = mergeRange(register(), 0, 1, 0, 2)!
    expect(splitColumn(merged, 1, { separator: ' ' })).toBeNull()
    // Un separatore vuoto non è un separatore.
    expect(splitColumn(register(), 1, { separator: '' })).toBeNull()
    expect(splitColumn(register(), 9, { separator: ' ' })).toBeNull()
  })
})

describe('le altre operazioni di colonna', () => {
  /** 4 righe x 3 colonne: la 0 e la 1 si possono unire, la 2 no. */
  const g = (): TableGrid => {
    const base = emptyGrid(4, 3)
    const texts = [
      ['Doris', '.. (Br)', '1'],
      ['Aagtekerk', '.. (Ne)', '2'],
      ['', '', '3'],
      ['  Ysabel   1924 ', '\t(Br) ', '4'],
    ]
    return {
      ...base,
      vlines: [0, 0.3, 0.6, 1],
      hlines: [0, 0.25, 0.5, 0.75, 1],
      cells: base.cells.map((c) => ({
        ...c,
        text: texts[c.r][c.c],
        source: 'ocr' as const,
        verified: false,
      })),
    }
  }
  const at = (grid: TableGrid, r: number, c: number) =>
    grid.cells.find((x) => x.r === r && x.c === c)

  it('unire due colonne le fonde in una, con il separatore scelto', () => {
    const out = joinColumns(g(), 0, { separator: ' ' })!
    expect(out.cols).toBe(2)
    expect(at(out, 0, 0)?.text).toBe('Doris .. (Br)')
    expect(at(out, 1, 0)?.text).toBe('Aagtekerk .. (Ne)')
    // La colonna di destra slitta e non si perde.
    expect(at(out, 0, 1)?.text).toBe('1')
    // Le righe vuote restano vuote: non si inventa un separatore.
    expect(at(out, 2, 0)?.text).toBe('')
    expect(out.vlines).toHaveLength(out.cols + 1)
  })

  it('unire non lascia verificato ciò che ha riscritto', () => {
    const out = joinColumns(g(), 0, { separator: ' ' })!
    expect(at(out, 0, 0)?.verified).toBe(false)
    expect(at(out, 0, 0)?.source).toBe('manual')
  })

  it('rifiuta di unire quando la fusione sarebbe ambigua', () => {
    // Una cella unita in verticale sulla colonna 0 e celle singole sulla 1.
    const merged = mergeRange(g(), 0, 0, 1, 0)!
    expect(joinColumns(merged, 0, { separator: ' ' })).toBeNull()
    expect(joinColumns(g(), 2, { separator: ' ' })).toBeNull()
  })

  it('normalizza spazi doppi, tabulazioni e spazi ai bordi', () => {
    const out = normalizeColumn(g(), 0)!
    expect(at(out, 3, 0)?.text).toBe('Ysabel 1924')
    // La colonna accanto non si tocca.
    expect(at(out, 3, 1)?.text).toBe('\t(Br) ')
    expect(at(out, 0, 0)?.text).toBe('Doris')
  })

  it('normalizzare non mangia i ritorni a capo: sono righe della pagina', () => {
    const grid = g()
    grid.cells = grid.cells.map((c) =>
      c.r === 0 && c.c === 0 ? { ...c, text: '  Doris  \n  .. (Br) ' } : c,
    )
    const out = normalizeColumn(grid, 0)!
    expect(at(out, 0, 0)?.text).toBe('Doris\n.. (Br)')
  })

  it('maiuscole, minuscole e iniziale maiuscola', () => {
    expect(at(transformColumnCase(g(), 0, 'upper')!, 0, 0)?.text).toBe('DORIS')
    expect(at(transformColumnCase(g(), 0, 'lower')!, 0, 0)?.text).toBe('doris')
    expect(at(transformColumnCase(g(), 1, 'upper')!, 0, 1)?.text).toBe('.. (BR)')
    const grid = g()
    grid.cells = grid.cells.map((c) =>
      c.r === 0 && c.c === 0 ? { ...c, text: 'aagtekerk van nederland' } : c,
    )
    expect(at(transformColumnCase(grid, 0, 'title')!, 0, 0)?.text).toBe('Aagtekerk Van Nederland')
  })

  it('propaga verso il basso solo le righe indicate', () => {
    const grid = g()
    const out = fillDown(grid, 0, 0, 2)!
    expect(at(out, 1, 0)?.text).toBe('Doris')
    expect(at(out, 2, 0)?.text).toBe('Doris')
    // Fuori dall'intervallo resta com'era (la riga 3 è quella sporca).
    expect(at(out, 3, 0)?.text).toBe('  Ysabel   1924 ')
    // La colonna accanto non si tocca.
    expect(at(out, 1, 1)?.text).toBe('.. (Ne)')
    expect(fillDown(grid, 0, 3, 1)).toBeNull()
  })

  it('propagare non entra nelle posizioni coperte da una cella unita', () => {
    const merged = mergeRange(g(), 1, 0, 2, 0)!
    const out = fillDown(merged, 0, 0, 3)!
    // La riga 1 è dentro una cella unita verticale: non la si riscrive.
    expect(at(out, 1, 0)?.text).toBe('Aagtekerk')
    expect(out.cells.find((c) => c.r === 1 && c.c === 0)?.rowspan).toBe(2)
  })
})

describe('unire celle senza perdere testo', () => {
  const g = (): TableGrid => {
    const base = emptyGrid(3, 3)
    const texts = [
      ['Nave', '', ''],
      ['Doris', '.. (Br)', '1'],
      ['Aagtekerk', '', '2'],
    ]
    return { ...base, cells: base.cells.map((c) => ({ ...c, text: texts[c.r][c.c] })) }
  }

  it('passa quando l unico testo è quello che sopravvive', () => {
    expect(mergeKeepsEveryText(g(), 0, 0, 0, 1)).toBe(true)
    expect(mergeKeepsEveryText(g(), 2, 1, 2, 2)).toBe(false)
  })

  it('rifiuta quando il testo sta in un altra cella dell intervallo', () => {
    // Riga 1: due celle con testo.
    expect(mergeKeepsEveryText(g(), 1, 1, 1, 2)).toBe(false)
    // Riga 2: il testo è nella cella accanto all'ancora, che è vuota.
    expect(mergeKeepsEveryText(g(), 2, 1, 2, 2)).toBe(false)
    // In verticale: l'ancora è piena, ma sotto c'è un altro valore.
    expect(mergeKeepsEveryText(g(), 1, 0, 2, 0)).toBe(false)
    // E in verticale quando sotto non c'è niente, unire è sicuro.
    expect(mergeKeepsEveryText(g(), 1, 1, 2, 1)).toBe(true)
  })

  it('un intervallo di sole celle vuote passa', () => {
    expect(mergeKeepsEveryText(g(), 0, 1, 0, 2)).toBe(true)
  })
})
