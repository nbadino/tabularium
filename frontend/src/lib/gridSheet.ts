/**
 * Il ponte fra il nostro modello di tabella e Jspreadsheet CE.
 *
 * Funzioni pure, testabili senza browser: è qui che si concentra il rischio
 * dell'integrazione, non nel componente React.
 *
 * Tre cose che la libreria non sa e che restano nostre:
 *
 * 1. **L'ordine del merge.** Jspreadsheet usa `[colspan, rowspan]`, il nostro
 *    modello `rowspan`/`colspan`. Invertirli non produce un errore: produce
 *    un merge trasposto, cioè una tabella sbagliata in silenzio. Il test lo
 *    copre esplicitamente.
 * 2. **I metadati per cella.** La libreria possiede testo e struttura, non
 *    `source`/`verified`: si conservano per (r, c) della cella di origine e si
 *    riapplicano al ritorno. Perderli significherebbe cancellare la
 *    distinzione fra testo del prefill e testo confermato — che è il dato di
 *    training.
 * 3. **La geometria.** `vlines`/`hlines`, i confini piegati e le colonne
 *    fantasma non hanno un posto nel foglio: passano attraverso, intatti.
 */
import type { TableCell, TableGrid } from './types'

export interface SheetData {
  /** Matrice di valori: una stringa per cella, riga per riga. */
  data: string[][]
  /** Merge in notazione A1 → `[colspan, rowspan]` (ordine della libreria). */
  mergeCells: Record<string, [number, number]>
}

export function columnName(index: number): string {
  let n = Math.max(0, index)
  let name = ''
  do {
    name = String.fromCharCode(65 + (n % 26)) + name
    n = Math.floor(n / 26) - 1
  } while (n >= 0)
  return name
}

/** `A1:C2` → coordinate zero-based. */
export function parseRange(range: string): { r: number; c: number; r2: number; c2: number } | null {
  const [from, to] = range.split(':')
  const parse = (ref: string) => {
    const match = /^([A-Za-z]+)(\d+)$/.exec(ref.trim())
    if (!match) return null
    const c = match[1]
      .toUpperCase()
      .split('')
      .reduce((acc, ch) => acc * 26 + (ch.charCodeAt(0) - 64), 0) - 1
    return { r: Number(match[2]) - 1, c }
  }
  const a = parse(from ?? '')
  const b = parse(to ?? from ?? '')
  if (!a || !b) return null
  return { r: a.r, c: a.c, r2: b.r, c2: b.c }
}

export function gridToSheet(grid: TableGrid): SheetData {
  const data = Array.from({ length: grid.rows }, () => Array.from({ length: grid.cols }, () => ''))
  const mergeCells: Record<string, [number, number]> = {}
  for (const cell of grid.cells) {
    if (cell.r < grid.rows && cell.c < grid.cols) data[cell.r][cell.c] = cell.text ?? ''
    const rs = cell.rowspan ?? 1
    const cs = cell.colspan ?? 1
    if (rs > 1 || cs > 1) {
      const from = `${columnName(cell.c)}${cell.r + 1}`
      const to = `${columnName(cell.c + cs - 1)}${cell.r + rs}`
      // [colspan, rowspan]: l'ordine della libreria, non il nostro.
      mergeCells[`${from}:${to}`] = [cs, rs]
    }
  }
  return { data, mergeCells }
}

/**
 * Il ritorno: dalla libreria al nostro modello.
 *
 * `previous` è la griglia da cui si è partiti: serve a riportare sui merge
 * riapplicati i metadati che la libreria non conosce (chi ha scritto il testo
 * e se è stato verificato). Una cella il cui testo è cambiato torna
 * `source: 'manual'` e non verificata — è una correzione umana da confermare.
 */
export function sheetToGrid(
  data: string[][],
  merges: Record<string, [number, number]>,
  previous: TableGrid,
): TableGrid {
  const rows = Math.max(1, data.length)
  const cols = Math.max(1, data[0]?.length ?? 1)
  const covered: boolean[][] = Array.from({ length: rows }, () => Array.from({ length: cols }, () => false))
  const spans = new Map<string, { rs: number; cs: number }>()

  for (const [range, size] of Object.entries(merges ?? {})) {
    const parsed = parseRange(range)
    if (!parsed) continue
    const cs = size?.[0] ?? parsed.c2 - parsed.c + 1
    const rs = size?.[1] ?? parsed.r2 - parsed.r + 1
    if (cs < 1 || rs < 1) continue
    spans.set(`${parsed.r}:${parsed.c}`, { rs, cs })
    for (let r = parsed.r; r <= parsed.r + rs - 1; r++) {
      for (let c = parsed.c; c <= parsed.c + cs - 1; c++) {
        if (r < rows && c < cols) covered[r][c] = !(r === parsed.r && c === parsed.c)
      }
    }
  }

  const before = new Map<string, TableCell>()
  for (const cell of previous.cells) before.set(`${cell.r}:${cell.c}`, cell)

  const cells: TableCell[] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (covered[r][c]) continue
      const span = spans.get(`${r}:${c}`)
      const text = String(data[r]?.[c] ?? '')
      const old = before.get(`${r}:${c}`)
      const unchanged = old ? (old.text ?? '') === text : false
      cells.push({
        r,
        c,
        rowspan: span?.rs ?? 1,
        colspan: span?.cs ?? 1,
        text,
        // Il testo invariato conserva la sua storia; quello riscritto è una
        // correzione umana da verificare.
        source: unchanged ? old?.source : 'manual',
        verified: unchanged ? old?.verified ?? false : false,
      })
    }
  }

  return {
    ...previous,
    rows,
    cols,
    cells,
    // La geometria non vive nel foglio: passa attraverso, intatta.
    phantom_cols: (previous.phantom_cols ?? []).filter((c) => c < cols),
    vlines: (previous.vlines ?? []).filter((_, i) => i <= cols),
    hlines: (previous.hlines ?? []).filter((_, i) => i <= rows),
  }
}

/** Vero quando due griglie differiscono in qualcosa che va salvato. */
export function gridChanged(a: TableGrid, b: TableGrid): boolean {
  if (a.rows !== b.rows || a.cols !== b.cols) return true
  if (a.cells.length !== b.cells.length) return true
  const key = (cell: TableCell) => `${cell.r}:${cell.c}:${cell.rowspan}:${cell.colspan}:${cell.text}`
  const left = a.cells.map(key).sort()
  const right = b.cells.map(key).sort()
  return left.some((value, index) => value !== right[index])
}
