/** Helper sulle griglie di tabelle: matrice fisica, merge, split, resize. */
import type { TableCell, TableGrid } from './types'

/** Normalizza griglie provenienti da versioni precedenti del backend.
 * `phantom_cols` è stata aggiunta dopo il primo formato persistito e può
 * quindi mancare nelle risposte già salvate. */
export function normalizeTableGrid(value: TableGrid): TableGrid {
  return {
    ...value,
    rows: Number(value.rows) || 0,
    cols: Number(value.cols) || 0,
    cells: Array.isArray(value.cells) ? value.cells : [],
    phantom_cols: Array.isArray(value.phantom_cols) ? value.phantom_cols : [],
    header_rows: Math.max(0, Math.min(20, Number(value.header_rows) || 0)),
  }
}

/** Mappa ogni posizione fisica (r,c) alla cella logica che la copre. */
export function ownerMap(grid: TableGrid): (TableCell | undefined)[][] {
  const map: (TableCell | undefined)[][] = Array.from({ length: grid.rows }, () =>
    Array(grid.cols).fill(undefined),
  )
  for (const cell of grid.cells) {
    for (let rr = cell.r; rr < cell.r + cell.rowspan; rr++) {
      for (let cc = cell.c; cc < cell.c + cell.colspan; cc++) {
        if (rr < grid.rows && cc < grid.cols) map[rr][cc] = cell
      }
    }
  }
  return map
}

function isSingular(cell: TableCell | undefined): cell is TableCell {
  return !!cell && cell.rowspan === 1 && cell.colspan === 1
}

/** Unisce l'area rettangolare selezionata in una singola cella.
 *  Richiede che ogni posizione dell'area sia coperta da una sola cella 1x1.
 */
export function mergeRange(
  grid: TableGrid,
  r1: number,
  c1: number,
  r2: number,
  c2: number,
): TableGrid | null {
  const minR = Math.min(r1, r2)
  const maxR = Math.max(r1, r2)
  const minC = Math.min(c1, c2)
  const maxC = Math.max(c1, c2)
  const map = ownerMap(grid)

  for (let rr = minR; rr <= maxR; rr++) {
    for (let cc = minC; cc <= maxC; cc++) {
      const owner = map[rr]?.[cc]
      if (!isSingular(owner) || owner.r !== rr || owner.c !== cc) return null
    }
  }

  const anchor = map[minR][minC] ?? { r: minR, c: minC, rowspan: 1, colspan: 1, text: '' }
  const cells = grid.cells.filter(
    (cell) =>
      !(
        cell.r >= minR &&
        cell.r <= maxR &&
        cell.c >= minC &&
        cell.c <= maxC &&
        cell.rowspan === 1 &&
        cell.colspan === 1
      ),
  )
  cells.push({
    r: minR,
    c: minC,
    rowspan: maxR - minR + 1,
    colspan: maxC - minC + 1,
    text: anchor.text,
  })
  return { ...grid, cells }
}

/** Separa una cella unita alle singole posizioni. Ritorna null se non unita. */
export function splitCell(grid: TableGrid, r: number, c: number): TableGrid | null {
  const owner = ownerMap(grid)[r]?.[c]
  if (!owner || (owner.rowspan === 1 && owner.colspan === 1)) return null
  const cells = grid.cells.filter((cell) => cell !== owner)
  for (let rr = owner.r; rr < owner.r + owner.rowspan; rr++) {
    for (let cc = owner.c; cc < owner.c + owner.colspan; cc++) {
      cells.push({
        r: rr,
        c: cc,
        rowspan: 1,
        colspan: 1,
        text: rr === owner.r && cc === owner.c ? owner.text : '',
      })
    }
  }
  return { ...grid, cells }
}

/** Ridimensiona la griglia (truncando span e scartando celle fuori area). */
export function resizeGrid(grid: TableGrid, rows: number, cols: number): TableGrid {
  const cells = grid.cells
    .filter((c) => c.r < rows && c.c < cols)
    .map((c) => ({
      ...c,
      rowspan: Math.min(c.rowspan, rows - c.r),
      colspan: Math.min(c.colspan, cols - c.c),
    }))
  return {
    rows,
    cols,
    cells,
    phantom_cols: grid.phantom_cols.filter((i) => i < cols),
    vlines: Array.from({ length: cols + 1 }, (_, i) => grid.vlines?.[i] ?? i / cols),
    hlines: Array.from({ length: rows + 1 }, (_, i) => grid.hlines?.[i] ?? i / rows),
  }
}

/** Ridimensiona riempiendo: le posizioni nuove — o rimaste scoperte —
 *  diventano celle 1x1 vuote. `resizeGrid` tronca ma non crea: nel foglio
 *  di calcolo una riga o colonna aggiunta deve essere scrivibile subito,
 *  non apparire come fila di buchi non selezionabili. */
export function growGrid(grid: TableGrid, rows: number, cols: number): TableGrid {
  const resized = resizeGrid(grid, rows, cols)
  const map = ownerMap(resized)
  const cells: TableCell[] = [...resized.cells]
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (!map[r]?.[c]) cells.push({ r, c, rowspan: 1, colspan: 1, text: '' })
    }
  }
  return { ...resized, cells }
}

/** Inserisce una traccia vuota (riga o colonna) alla posizione `at`, spostando
 *  le successive e allargando le celle unite che attraversano il punto.
 *  `at` può essere uguale al numero di tracce: aggiunge in coda. */
export function insertTrack(grid: TableGrid, axis: 'row' | 'col', at: number): TableGrid {
  const isRow = axis === 'row'
  const count = isRow ? grid.rows : grid.cols
  const pos = Math.max(0, Math.min(at, count))
  const start = (c: TableCell) => (isRow ? c.r : c.c)
  const span = (c: TableCell) => (isRow ? c.rowspan : c.colspan)
  const key = isRow ? 'r' : 'c'
  const spanKey = isRow ? 'rowspan' : 'colspan'

  const cells = grid.cells.map((cell) => {
    const s = start(cell)
    if (s >= pos) return { ...cell, [key]: s + 1 } as TableCell
    if (s + span(cell) > pos) return { ...cell, [spanKey]: span(cell) + 1 } as TableCell
    return cell
  })

  const lines = [...((isRow ? grid.hlines : grid.vlines) ?? [])]
  if (lines.length === count + 1) {
    // Il nuovo confine coincide con quello esistente: due tracce sovrapposte
    // finché l'utente non la riempie — coerente, non inventa geometrie.
    lines.splice(pos, 0, lines[pos] ?? lines[lines.length - 1])
  }

  return {
    ...grid,
    rows: isRow ? grid.rows + 1 : grid.rows,
    cols: isRow ? grid.cols : grid.cols + 1,
    cells,
    phantom_cols: isRow
      ? grid.phantom_cols
      : grid.phantom_cols.map((i) => (i >= pos ? i + 1 : i)),
    vlines: isRow ? grid.vlines : lines,
    hlines: isRow ? lines : grid.hlines,
  }
}

/** Elimina la traccia `at` (0-based). Il testo delle celle che stanno lì viene
 *  PERSO: la UI chiede conferma. Le celle unite che attraversano la traccia
 *  si restringono; le successive slittano. null se la traccia non esiste o è
 *  l'ultima rimasta. */
export function deleteTrack(grid: TableGrid, axis: 'row' | 'col', at: number): TableGrid | null {
  const isRow = axis === 'row'
  const count = isRow ? grid.rows : grid.cols
  if (at < 0 || at >= count || count < 2) return null
  const start = (c: TableCell) => (isRow ? c.r : c.c)
  const span = (c: TableCell) => (isRow ? c.rowspan : c.colspan)
  const key = isRow ? 'r' : 'c'
  const spanKey = isRow ? 'rowspan' : 'colspan'

  const cells: TableCell[] = []
  for (const cell of grid.cells) {
    const s = start(cell)
    const e = s + span(cell) - 1
    if (e < at || s > at) {
      // Intera prima o intera dopo: solo eventuale shift.
      cells.push(s > at ? ({ ...cell, [key]: s - 1 } as TableCell) : cell)
      continue
    }
    // Attraversa la traccia eliminata: si restringe (o sposta l'ancora).
    if (span(cell) > 1) {
      const nextSpan = span(cell) - 1
      if (nextSpan === 0) continue // era larga solo la traccia eliminata
      cells.push({
        ...cell,
        [key]: s > at ? s - 1 : s,
        [spanKey]: nextSpan,
      } as TableCell)
    }
    // start === at && span === 1: la cella eliminata, il suo testo va perso.
  }

  const lines = [...((isRow ? grid.hlines : grid.vlines) ?? [])]
  if (lines.length === count + 1) lines.splice(at + 1, 1)

  return {
    ...grid,
    rows: isRow ? grid.rows - 1 : grid.rows,
    cols: isRow ? grid.cols : grid.cols - 1,
    cells,
    phantom_cols: isRow
      ? grid.phantom_cols
      : grid.phantom_cols.filter((i) => i !== at).map((i) => (i > at ? i - 1 : i)),
    vlines: isRow ? grid.vlines : lines,
    hlines: isRow ? lines : grid.hlines,
  }
}

/** Celle ordinate per (r, c), utili per render e confronti. */
export function sortedCells(grid: TableGrid): TableCell[] {
  return [...grid.cells].sort((a, b) => a.r - b.r || a.c - b.c)
}

/** Griglia vuota (tutte celle 1x1 senza testo). */
export function emptyGrid(rows: number, cols: number): TableGrid {
  const cells: TableCell[] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      cells.push({ r, c, rowspan: 1, colspan: 1, text: '' })
    }
  }
  return {
    rows,
    cols,
    cells,
    phantom_cols: [],
    vlines: Array.from({ length: cols + 1 }, (_, i) => i / cols),
    hlines: Array.from({ length: rows + 1 }, (_, i) => i / rows),
  }
}

/** Asse su cui agire: confini verticali (colonne) od orizzontali (righe). */
export type Axis = 'v' | 'h'

/**
 * Rifiuta il confine interno `index`, fondendo le due tracce che separa.
 *
 * È la controparte di «questo confine il rilevatore se l'è inventato»: la
 * proposta si corregge togliendo una linea, non ridisegnando la griglia.
 *
 * Il testo delle due celle fuse si concatena, perché è quello che è successo
 * fisicamente sulla pagina: due colonne che il rilevatore aveva separato erano
 * una sola. Ritorna `null` quando la fusione sarebbe **ambigua** — due celle
 * unite con estensione diversa sull'altro asse — invece di scegliere al posto
 * dell'utente: lì va prima separata la cella unita.
 */
export function dropBoundary(grid: TableGrid, axis: Axis, index: number): TableGrid | null {
  const count = axis === 'v' ? grid.cols : grid.rows
  if (index < 1 || index > count - 1 || count < 2) return null

  const start = (c: TableCell) => (axis === 'v' ? c.c : c.r)
  const span = (c: TableCell) => (axis === 'v' ? c.colspan : c.rowspan)
  const cross = (c: TableCell) => (axis === 'v' ? c.r : c.c)
  const crossSpan = (c: TableCell) => (axis === 'v' ? c.rowspan : c.colspan)
  // Le due tracce fuse collassano entrambe su `index - 1`.
  const remap = (j: number) => (j < index ? j : j - 1)

  const moved = grid.cells.map((cell) => {
    const from = remap(start(cell))
    const to = remap(start(cell) + span(cell) - 1)
    const next = { ...cell, [axis === 'v' ? 'c' : 'r']: from } as TableCell
    return { ...next, [axis === 'v' ? 'colspan' : 'rowspan']: to - from + 1 } as TableCell
  })

  // Sola collisione possibile: due celle distinte finite entrambe su index-1.
  const out: TableCell[] = []
  const pending = new Map<string, TableCell>()
  for (const cell of moved) {
    if (start(cell) !== index - 1) {
      out.push(cell)
      continue
    }
    const key = `${cross(cell)}:${crossSpan(cell)}`
    const twin = pending.get(key)
    if (!twin) {
      pending.set(key, cell)
      continue
    }
    const text = [twin.text, cell.text].map((s) => s.trim()).filter(Boolean).join(' ')
    pending.set(key, {
      ...twin,
      text,
      [axis === 'v' ? 'colspan' : 'rowspan']: Math.max(span(twin), span(cell)),
    } as TableCell)
  }
  const anchored = [...pending.values()]
  // Due celle sull'altro asse con estensione diversa non si sanno fondere.
  const seen = new Set<number>()
  for (const cell of anchored) {
    if (seen.has(cross(cell))) return null
    for (let k = cross(cell); k < cross(cell) + crossSpan(cell); k++) seen.add(k)
  }

  const lines = axis === 'v' ? grid.vlines : grid.hlines
  const kept = (lines ?? []).filter((_, i) => i !== index)
  return {
    ...grid,
    rows: axis === 'h' ? grid.rows - 1 : grid.rows,
    cols: axis === 'v' ? grid.cols - 1 : grid.cols,
    cells: [...out, ...anchored],
    phantom_cols:
      axis === 'v'
        ? grid.phantom_cols.filter((i) => i !== index - 1).map((i) => (i >= index ? i - 1 : i))
        : grid.phantom_cols,
    vlines: axis === 'v' ? kept : grid.vlines,
    hlines: axis === 'h' ? kept : grid.hlines,
  }
}

/**
 * Inserisce un confine alla posizione normalizzata `at`, spezzando la traccia
 * che lo contiene. È il gesto «qui il rilevatore una colonna non l'ha vista».
 *
 * Il testo resta nella traccia di sinistra (o in alto): spostarlo a metà
 * sarebbe un'ipotesi, e l'annotatore lo sposta dove va. Ritorna `null` se `at`
 * cade fuori dal contenuto o su un confine già esistente.
 */
export function insertBoundary(grid: TableGrid, axis: Axis, at: number): TableGrid | null {
  const lines = [...((axis === 'v' ? grid.vlines : grid.hlines) ?? [])]
  if (lines.length < 2) return null
  if (at <= lines[0] || at >= lines[lines.length - 1]) return null
  const index = lines.findIndex((v) => v > at)
  if (index < 1) return null
  // Un confine a ridosso di un altro non separa niente e crea una traccia vuota.
  const tooClose = Math.abs(at - lines[index]) < 1e-4 || Math.abs(at - lines[index - 1]) < 1e-4
  if (tooClose) return null

  const track = index - 1 // traccia spezzata
  const start = (c: TableCell) => (axis === 'v' ? c.c : c.r)
  const span = (c: TableCell) => (axis === 'v' ? c.colspan : c.rowspan)
  const key = axis === 'v' ? 'c' : 'r'
  const spanKey = axis === 'v' ? 'colspan' : 'rowspan'

  const cells: TableCell[] = []
  for (const cell of grid.cells) {
    const from = start(cell)
    const to = from + span(cell) - 1
    if (to < track) {
      cells.push(cell)
    } else if (from > track) {
      cells.push({ ...cell, [key]: from + 1 } as TableCell)
    } else if (span(cell) > 1) {
      // La cella attraversa la traccia spezzata: si allarga di uno.
      cells.push({ ...cell, [spanKey]: span(cell) + 1 } as TableCell)
    } else {
      cells.push(cell)
      cells.push({ ...cell, [key]: from + 1, text: '' } as TableCell)
    }
  }

  lines.splice(index, 0, at)
  return {
    ...grid,
    rows: axis === 'h' ? grid.rows + 1 : grid.rows,
    cols: axis === 'v' ? grid.cols + 1 : grid.cols,
    cells,
    phantom_cols:
      axis === 'v' ? grid.phantom_cols.map((i) => (i > track ? i + 1 : i)) : grid.phantom_cols,
    vlines: axis === 'v' ? lines : grid.vlines,
    hlines: axis === 'h' ? lines : grid.hlines,
  }
}
