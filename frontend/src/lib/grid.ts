/** Helper sulle griglie di tabelle: matrice fisica, merge, split, resize. */
import type { TableCell, TableGrid } from './types'

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
