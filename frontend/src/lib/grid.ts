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
