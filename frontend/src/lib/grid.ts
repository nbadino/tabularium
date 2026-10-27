/** Helper sulle griglie di tabelle: matrice fisica, merge, split, resize. */
import type { HeaderTemplate, TableCell, TableGrid } from './types'
import { toFractionGlyphs } from './fractions'

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

/** Estrae una selezione come intestazione autonoma, mantenendo merge e testi
 *  relativi al suo angolo. Rifiuta i merge che attraversano il bordo: una
 *  selezione parziale non descrive una struttura riutilizzabile. */
export function extractHeaderTemplate(
  grid: TableGrid,
  selection: { startRow: number; startColumn: number; endRow: number; endColumn: number },
): HeaderTemplate | null {
  const minR = Math.max(0, Math.min(selection.startRow, selection.endRow))
  const maxR = Math.min(grid.rows - 1, Math.max(selection.startRow, selection.endRow))
  const minC = Math.max(0, Math.min(selection.startColumn, selection.endColumn))
  const maxC = Math.min(grid.cols - 1, Math.max(selection.startColumn, selection.endColumn))
  if (maxR < minR || maxC < minC) return null

  const map = ownerMap(grid)
  const logical = new Map<string, TableCell>()
  for (let r = minR; r <= maxR; r++) {
    for (let c = minC; c <= maxC; c++) {
      const owner = map[r]?.[c]
      if (!owner) continue
      const inside = owner.r >= minR && owner.c >= minC
        && owner.r + owner.rowspan - 1 <= maxR
        && owner.c + owner.colspan - 1 <= maxC
      if (!inside) return null
      logical.set(`${owner.r}:${owner.c}`, owner)
    }
  }
  // Materializza anche le posizioni vuote: l'intestazione applicata deve
  // sostituire l'intero rettangolo, non lasciare buchi con celle precedenti.
  for (let r = minR; r <= maxR; r++) {
    for (let c = minC; c <= maxC; c++) {
      if (!logical.has(`${r}:${c}`) && !map[r]?.[c]) logical.set(`${r}:${c}`, { r, c, rowspan: 1, colspan: 1, text: '' })
    }
  }
  return {
    rows: maxR - minR + 1,
    cols: maxC - minC + 1,
    cells: [...logical.values()]
      .sort((a, b) => a.r - b.r || a.c - b.c)
      .map((cell) => ({
        r: cell.r - minR,
        c: cell.c - minC,
        rowspan: cell.rowspan,
        colspan: cell.colspan,
        text: cell.text ?? '',
      })),
  }
}

/** Sostituisce un rettangolo con un'intestazione nota. Il punto di partenza è
 *  l'angolo selezionato; i dati sotto restano intatti. */
export function applyHeaderTemplate(
  grid: TableGrid,
  header: HeaderTemplate,
  selection: { startRow: number; startColumn: number; endRow: number; endColumn: number },
): TableGrid | null {
  const startRow = Math.max(0, Math.min(selection.startRow, selection.endRow))
  const startColumn = Math.max(0, Math.min(selection.startColumn, selection.endColumn))
  const selectedEndRow = Math.max(selection.startRow, selection.endRow)
  if (startRow < 0 || startColumn < 0 || header.rows < 1 || header.cols < 1) return null

  // Il rettangolo selezionato è ciò che viene sostituito. Se l'header noto è
  // più alto o più largo, la differenza diventa una vera inserzione/rimozione:
  // le righe dati sotto scorrono, non vengono sovrascritte.
  let adjusted: TableGrid = grid
  const rowDelta = header.rows - (selectedEndRow - startRow + 1)
  if (rowDelta > 0) {
    for (let i = 0; i < rowDelta; i++) adjusted = insertTrack(adjusted, 'row', selectedEndRow + 1 + i)
  } else if (rowDelta < 0) {
    for (let i = 0; i < -rowDelta; i++) {
      const next = deleteTrack(adjusted, 'row', startRow + header.rows)
      if (!next) return null
      adjusted = next
    }
  }
  const endRow = startRow + header.rows - 1
  const endColumn = startColumn + header.cols - 1
  const grown = growGrid(adjusted, Math.max(adjusted.rows, endRow + 1), Math.max(adjusted.cols, endColumn + 1))
  const map = ownerMap(grown)
  const toRemove = new Set<TableCell>()
  for (let r = startRow; r <= endRow; r++) {
    for (let c = startColumn; c <= endColumn; c++) {
      const owner = map[r]?.[c]
      if (!owner) continue
      const fullyInside = owner.r >= startRow && owner.c >= startColumn
        && owner.r + owner.rowspan - 1 <= endRow
        && owner.c + owner.colspan - 1 <= endColumn
      if (!fullyInside) return null
      toRemove.add(owner)
    }
  }
  const cells: TableCell[] = grown.cells.filter((cell) => !toRemove.has(cell))
  for (const cell of header.cells) {
    if (cell.r < 0 || cell.c < 0 || cell.r + cell.rowspan > header.rows || cell.c + cell.colspan > header.cols) return null
    cells.push({
      r: startRow + cell.r,
      c: startColumn + cell.c,
      rowspan: cell.rowspan,
      colspan: cell.colspan,
      text: cell.text,
      source: 'manual',
      verified: true,
      verification_source: null,
    })
  }
  return {
    ...grown,
    cells,
    header_rows: startRow === 0 ? Math.max(grown.header_rows ?? 0, header.rows) : grown.header_rows,
  }
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

  // L'ancora geometrica è in alto a sinistra, ma per una testata verticale il
  // testo può stare nella seconda riga (per esempio `Fig` sotto una cella
  // vuota). In quel caso conservarlo è sicuro: l'intervallo contiene un solo
  // valore. Prendiamo la prima cella non vuota in ordine di lettura.
  const textCell = (() => {
    for (let rr = minR; rr <= maxR; rr++) {
      for (let cc = minC; cc <= maxC; cc++) {
        const cell = map[rr]?.[cc]
        if (cell && (cell.text ?? '').trim() !== '') return cell
      }
    }
    return map[minR][minC] ?? { r: minR, c: minC, rowspan: 1, colspan: 1, text: '' }
  })()
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
    text: textCell.text,
    source: textCell.source,
    verified: textCell.verified,
    verification_source: textCell.verification_source,
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

/** Confini uniformi su `count` tracce: la geometria di ripiego quando il grid
 *  non ne porta una utilizzabile. */
function uniformLines(count: number): number[] {
  return Array.from({ length: count + 1 }, (_, i) => i / count)
}

/** I confini interni di una riga/colonna, senza i due bordi del contenuto. */
function innerBoundaries(lines: number[] | undefined, count: number): number[] {
  if (count < 1) return []
  const full = lines && lines.length === count + 1 ? lines : uniformLines(count)
  return full.slice(1, count)
}

/** I confini piegati nella forma che il contratto richiede. */
type RowColumns = { row_columns: number[][]; row_columns_proven: boolean[][] }
type RowColumnsPatch = Partial<RowColumns>
interface RowColumnsShape {
  columns: number[][]
  proven: boolean[][]
}

/** I confini piegati di una griglia, o `null` se non ci sono o non tornano. */
function rowColumnsShape(grid: TableGrid): RowColumnsShape | null {
  const columns = grid.row_columns ?? []
  const expected = Math.max(0, grid.cols - 1)
  if (!columns.length || columns.length !== grid.rows) return null
  if (!columns.every((row) => row.length === expected)) return null
  // La provenienza può mancare: senza, ogni confine è non provato.
  const given = grid.row_columns_proven ?? []
  const proven =
    given.length === columns.length && given.every((row, i) => row.length === columns[i].length)
      ? given
      : columns.map((row) => row.map(() => false))
  return { columns: columns.map((row) => [...row]), proven: proven.map((row) => [...row]) }
}

/**
 * Applica ai confini piegati la stessa modifica fatta alle rette.
 *
 * Servono a disegnare la spezzata reale e a riempire le celle: quando la
 * struttura cambia devono cambiare con lei. Saltare il passaggio lascia la
 * cardinalità vecchia e il backend respinge il salvataggio («cardinalità dei
 * confini interni non valida») — un secondo 400 dietro quello dei `vlines`.
 * `{}` quando la griglia non ne porta: niente da aggiornare.
 */
function withRowColumns(
  grid: TableGrid,
  edit: (shape: RowColumnsShape) => RowColumns,
): RowColumnsPatch {
  const shape = rowColumnsShape(grid)
  return shape ? edit(shape) : {}
}

/** Inserisce in ogni riga il confine interno `index`, marcato non provato.
 *  `value` riceve i due confini che la riga ha davvero attorno a quel punto. */
function insertRowColumn(
  shape: RowColumnsShape,
  index: number,
  value: (lo: number, hi: number) => number,
): RowColumns {
  const columns = shape.columns.map((row) => {
    const lo = index === 0 ? 0 : row[index - 1]
    const hi = index === row.length ? 1 : row[index]
    const out = [...row]
    out.splice(index, 0, value(lo, hi))
    return out
  })
  const proven = shape.proven.map((row) => {
    const out = [...row]
    out.splice(index, 0, false)
    return out
  })
  return { row_columns: columns, row_columns_proven: proven }
}

/** Toglie da ogni riga il confine interno `index`. */
function removeRowColumn(shape: RowColumnsShape, index: number): RowColumns {
  return {
    row_columns: shape.columns.map((row) => row.filter((_, i) => i !== index)),
    row_columns_proven: shape.proven.map((row) => row.filter((_, i) => i !== index)),
  }
}

/** Aggiunge una riga di confini alla posizione `at`, non provati. */
function insertRowColumnRow(shape: RowColumnsShape, at: number, row: number[]): RowColumns {
  const columns = [...shape.columns]
  const proven = [...shape.proven]
  columns.splice(at, 0, [...row])
  proven.splice(at, 0, row.map(() => false))
  return { row_columns: columns, row_columns_proven: proven }
}

/** Toglie la riga di confini `at`. */
function removeRowColumnRow(shape: RowColumnsShape, at: number): RowColumns {
  return {
    row_columns: shape.columns.filter((_, i) => i !== at),
    row_columns_proven: shape.proven.filter((_, i) => i !== at),
  }
}

/** Inserisce una traccia vuota (riga o colonna) alla posizione `at`, spostando
 *  le successive e allargando le celle unite che attraversano il punto.
 *  `at` può essere uguale al numero di tracce: aggiunge in coda. */
export function insertTrack(grid: TableGrid, axis: 'row' | 'col', at: number): TableGrid {
  const isRow = axis === 'row'
  const count = isRow ? grid.rows : grid.cols
  if (count < 1) return grid
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

  const source = (isRow ? grid.hlines : grid.vlines) ?? []
  const lines = source.length === count + 1 ? [...source] : uniformLines(count)

  // Il confine nuovo si ricava dalla traccia vicina **a valle** (a monte solo
  // quando si aggiunge in coda): la traccia nuova prende metà della larghezza
  // di quella che le sta accanto. Ripetere un confine esistente darebbe invece
  // una traccia di larghezza zero — due confini sovrapposti che il contratto
  // dei confini rifiuta, esattamente come `insertBoundary` rifiuta un confine
  // a ridosso di un altro.
  const boundary = Math.min(pos + 1, count)
  lines.splice(boundary, 0, (lines[boundary - 1] + lines[boundary]) / 2)

  const rows = isRow ? grid.rows + 1 : grid.rows
  const cols = isRow ? grid.cols : grid.cols + 1
  const next: TableGrid = {
    ...grid,
    rows,
    cols,
    cells,
    phantom_cols: isRow
      ? grid.phantom_cols
      : grid.phantom_cols.map((i) => (i >= pos ? i + 1 : i)),
    vlines: isRow ? grid.vlines : lines,
    hlines: isRow ? lines : grid.hlines,
    ...withRowColumns(grid, (shape) =>
      isRow
        ? insertRowColumnRow(shape, pos, innerBoundaries(grid.vlines, grid.cols))
        : insertRowColumn(shape, boundary - 1, (lo, hi) => (lo + hi) / 2),
    ),
  }

  // La traccia nuova non ha inchiostro: dove nessuna cella la copre — nessuna
  // unione l'ha attraversata allargandosi — nasce una cella vuota, altrimenti
  // il modello resta con un buco che l'OTSL non sa rappresentare.
  const map = ownerMap(next)
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (!map[r][c]) cells.push({ r, c, rowspan: 1, colspan: 1, text: '' })
    }
  }
  return next
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
    // La colonna eliminata porta con sé il suo confine piegato (le rette lo
    // tolgono in `lines`); la riga eliminata porta con sé la sua riga.
    ...withRowColumns(grid, (shape) =>
      isRow ? removeRowColumnRow(shape, at) : removeRowColumn(shape, at),
    ),
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
    // Le due tracce fuse diventano una: il confine piegato che le separava non
    // esiste più. Le righe collassano su `index - 1`, quindi si toglie la riga
    // di confini `index`.
    ...withRowColumns(grid, (shape) =>
      axis === 'v' ? removeRowColumn(shape, index - 1) : removeRowColumnRow(shape, index),
    ),
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
  // Dove cade il confine dentro la traccia, in proporzione: serve a proporre ai
  // confini piegati lo stesso punto, non un centro che l'utente non ha scelto.
  const ratio = (at - lines[index - 1]) / (lines[index] - lines[index - 1])
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
    // La traccia nuova nasce dalla `track` spezzata: eredita i confini piegati
    // di quella, non provati, così la spezzata resta leggibile.
    ...withRowColumns(grid, (shape) =>
      axis === 'v'
        ? insertRowColumn(shape, track, (lo, hi) => lo + ratio * (hi - lo))
        : insertRowColumnRow(shape, index, shape.columns[track]),
    ),
  }
}

export interface SplitColumnOptions {
  /** Separatore letterale. Lo spazio divide le sequenze di spazi (nel registro
   *  una cella ha spesso due spazi dove l'occhio ne vede uno). */
  separator: string
  /** In quante colonne al massimo dividere. 2 = una colonna nuova. */
  maxParts?: number
  fromRight?: boolean
  startRow?: number
  endRow?: number
  /** Separa anche la testata della colonna quando il suo testo usa lo stesso
   *  separatore e almeno una riga di dati viene effettivamente divisa. */
  splitHeader?: boolean
}

const MAX_SPLIT_PARTS = 8

/** Prima riga che «Separa per separatore» può trattare, 0-based.
 *
 * Le righe di testata dichiarate non sono dati. Se il modello non le ha
 * dichiarate, si saltano comunque le celle unite che occupano l'inizio della
 * colonna: sono intestazioni a più colonne, non valori da dividere. Fermarsi
 * alla prima riga non unita evita di saltare per errore una cella unita che
 * compare più in basso, nel corpo della tabella.
 */
export function splitStartRow(grid: TableGrid, at: number): number {
  if (at < 0 || at >= grid.cols || grid.rows < 1) return 0
  const map = ownerMap(grid)
  let row = Math.min(grid.rows - 1, Math.max(0, grid.header_rows ?? 0))
  while (row < grid.rows) {
    const owner = map[row]?.[at]
    if (!owner || (owner.rowspan <= 1 && owner.colspan <= 1)) break
    row += 1
  }
  return Math.min(row, grid.rows - 1)
}

/** I pezzi di un testo, al massimo `maxParts`.
 *
 *  Lo spazio divide le *sequenze* di spazi: nel registro una cella ha spesso
 *  due spazi dove l'occhio ne vede uno, e «Doris  .. (Br)» deve dare due pezzi,
 *  non quattro. Un separatore letterale invece non si ricompone mai: il resto
 *  si tiene **com'era**, spazi compresi — rimontarlo con `join` mangerebbe lo
 *  spazio dopo la virgola di «Aagtekerk, 1924, Ne». */
function splitParts(text: string, separator: string, maxParts: number, fromRight = false): string[] {
  const trimmed = text.trim()
  if (!trimmed) return ['']
  if (separator === ' ') {
    const parts = trimmed.split(/\s+/).filter((part) => part !== '')
    if (fromRight && parts.length > maxParts) return [parts.slice(0, parts.length - maxParts + 1).join(' '), ...parts.slice(parts.length - maxParts + 1)]
    return parts.length > maxParts
      ? [...parts.slice(0, maxParts - 1), parts.slice(maxParts - 1).join(' ')]
      : parts
  }
  const parts: string[] = []
  let rest = trimmed
  for (let k = 1; k < maxParts; k++) {
    const cut = fromRight ? rest.lastIndexOf(separator) : rest.indexOf(separator)
    if (cut < 0) break
    if (fromRight) {
      parts.unshift(rest.slice(cut + separator.length))
      rest = rest.slice(0, cut)
    } else {
      parts.push(rest.slice(0, cut))
      rest = rest.slice(cut + separator.length)
    }
  }
  if (fromRight) parts.unshift(rest)
  else parts.push(rest)
  return parts.map((part) => part.trim()).filter((part) => part !== '')
}

/** La testata che può seguire la separazione dei dati. Si accetta solo una
 *  cella che occupa una sola colonna: una testata già unita su più colonne è
 *  un raggruppamento voluto, non testo da spezzare. Lo `rowspan` verticale è
 *  invece normale nelle testate su due righe e viene conservato. */
function splitHeaderFor(
  grid: TableGrid,
  at: number,
  separator: string,
  maxParts: number,
  fromRight: boolean,
): { anchor: TableCell; parts: string[] } | null {
  const firstDataRow = splitStartRow(grid, at)
  if (firstDataRow < 1) return null
  const map = ownerMap(grid)
  for (let r = 0; r < firstDataRow; r++) {
    const anchor = map[r]?.[at]
    if (!anchor || anchor.c !== at || anchor.colspan !== 1) continue
    const parts = splitParts(anchor.text ?? '', separator, maxParts, fromRight)
    if (parts.length > 1) return { anchor, parts }
  }
  return null
}

/** Riscrive la testata sulle colonne appena inserite. Le celle vuote createsi
 *  con l'inserimento vengono sostituite da un'unica testata con lo stesso
 *  `rowspan`, così l'allineamento verticale resta quello delle colonne vicine. */
function applyHeaderSplit(
  grid: TableGrid,
  at: number,
  header: { anchor: TableCell; parts: string[] },
): TableGrid {
  const { anchor, parts } = header
  const headerEnd = anchor.r + anchor.rowspan
  const cells = grid.cells
    .filter((cell) => !(cell.c > at && cell.c < at + parts.length && cell.r >= anchor.r && cell.r < headerEnd))
    .map((cell) =>
      cell.r === anchor.r && cell.c === at
        ? { ...cell, text: parts[0], source: 'manual' as const, verified: false, verification_source: null }
        : cell,
    )
  for (let k = 1; k < parts.length; k++) {
    cells.push({
      ...anchor,
      c: at + k,
      text: parts[k],
      source: 'manual',
      verified: false,
      verification_source: null,
    })
  }
  return { ...grid, cells }
}

/** Cella di testata che ancora una colonna e può essere distribuita sulle
 *  righe che occupa. È il caso di «Year blt» su due righe di testata. */
export function splitHeaderAcrossRows(
  grid: TableGrid,
  at: number,
  options: { separator: string; fromRight?: boolean },
): TableGrid | null {
  if (at < 0 || at >= grid.cols || !options.separator) return null
  const firstDataRow = splitStartRow(grid, at)
  if (firstDataRow < 1) return null
  const map = ownerMap(grid)
  let anchor: TableCell | null = null
  for (let r = 0; r < firstDataRow; r++) {
    const candidate = map[r]?.[at]
    if (candidate?.c === at && candidate.colspan === 1 && candidate.rowspan > 1) {
      anchor = candidate
      break
    }
  }
  if (!anchor) return null
  const parts = splitParts(anchor.text ?? '', options.separator, Math.min(MAX_SPLIT_PARTS, anchor.rowspan), options.fromRight)
  if (parts.length < 2) return null

  const cells = grid.cells.filter((cell) => !(cell.r === anchor!.r && cell.c === anchor!.c))
  for (let row = 0; row < anchor.rowspan; row++) {
    cells.push({
      ...anchor,
      r: anchor.r + row,
      rowspan: 1,
      text: parts[row] ?? '',
      source: 'manual',
      verified: false,
      verification_source: null,
    })
  }
  return { ...grid, cells }
}

/**
 * Divide una colonna in più colonne sul separatore scelto.
 *
 * Nei registri la stessa cella tiene cose diverse — nome della nave, bandiera,
 * stazza, tipo — separate da uno spazio o da una virgola: separarle è il primo
 * lavoro di chi annota, e farlo a mano su cinquanta righe è dove nascono gli
 * errori di allineamento.
 *
 * Le colonne nuove si ricavano **dalla larghezza di quella divisa** (una
 * inserita alla volta subito dopo di lei, così ognuna prende metà della
 * vicina): la geometria resta dentro il ritaglio, e i `vlines` e i confini
 * piegati seguono, non si inventano.
 *
 * Il testo diviso è una **trasformazione**, non una correzione umana: le celle
 * che cambiano tornano non verificate, perché vanno guardate. Le righe che il
 * separatore non ce l'hanno restano intatte, storia compresa.
 *
 * Ritorna `null` quando la colonna è coperta da una cella unita: lì il testo è
 * uno solo e non si sa in quale riga finirebbe. Si separa prima quella.
 */
export function splitColumn(
  grid: TableGrid,
  at: number,
  options: SplitColumnOptions,
): TableGrid | null {
  if (at < 0 || at >= grid.cols) return null
  const separator = options.separator
  if (!separator) return null
  const maxParts = Math.max(2, Math.min(MAX_SPLIT_PARTS, options.maxParts ?? 2))

  const map = ownerMap(grid)
  const rowParts = new Map<number, string[]>()
  const header = options.splitHeader
    ? splitHeaderFor(grid, at, separator, maxParts, Boolean(options.fromRight))
    : null
  let dataWillSplit = false
  const start = options.startRow ?? 0
  const end = options.endRow ?? grid.rows - 1
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end >= grid.rows || start > end) return null
  for (let r = start; r <= end; r++) {
    const owner = map[r]?.[at]
    if (owner && (owner.rowspan > 1 || owner.colspan > 1)) return null
    // Oltre il tetto il resto resta unito nell'ultima colonna: spezzare una
    // descrizione in otto colonne non è quello che l'utente ha chiesto.
    const parts = splitParts(owner?.text ?? '', separator, maxParts, options.fromRight)
    if (parts.length > 1) dataWillSplit = true
    rowParts.set(r, parts)
  }

  // Una inserzione alla volta **nella stessa posizione**: la seconda spinge a
  // destra la prima, e le colonne nuove restano tutte figlie di quella divisa.
  let next = grid
  for (let k = 1; k < maxParts; k++) next = insertTrack(next, 'col', at + 1)

  const cells = next.cells.map((cell) => {
    if (cell.r >= grid.rows || cell.c < at || cell.c >= at + maxParts) return cell
    const parts = rowParts.get(cell.r) ?? []
    if (parts.length < 2) return cell
    const index = cell.c - at
    if (index === 0) {
      return { ...cell, text: parts[0], source: 'manual' as const, verified: false, verification_source: null }
    }
    const text = parts[index] ?? ''
    // Vuota resta vuota: non si marca come correzione ciò che non ha testo.
    if (!text) return cell
    return { ...cell, text, source: 'manual' as const, verified: false, verification_source: null }
  })

  const result = { ...next, cells }
  return header && dataWillSplit ? applyHeaderSplit(result, at, header) : result
}

/** Un testo riscritto è una correzione umana da verificare; se non è cambiato
 *  nulla, la cella conserva la sua storia. */
function withText(cell: TableCell, text: string): TableCell {
  if ((cell.text ?? '') === text) return cell
  return { ...cell, text, source: 'manual' as const, verified: false }
}

/**
 * Vero quando unire quell'intervallo non perde niente.
 *
 * `mergeRange` può conservare il testo della prima cella non vuota, ma non può
 * tenere più valori diversi in una cella sola. Su un registro questo sarebbe
 * quasi sempre una perdita — e per giunta la cella resterebbe `verified` —
 * quindi la fusione si rifiuta invece di sceglierlo per l'utente. Passa il caso
 * normale delle intestazioni: zero o un solo valore nell'intervallo, anche
 * quando quel valore sta sotto l'angolo vuoto in alto a sinistra.
 */
export function mergeKeepsEveryText(
  grid: TableGrid,
  r1: number,
  c1: number,
  r2: number,
  c2: number,
): boolean {
  const minR = Math.min(r1, r2)
  const maxR = Math.max(r1, r2)
  const minC = Math.min(c1, c2)
  const maxC = Math.max(c1, c2)
  let keptText: string | null = null
  for (const cell of grid.cells) {
    if (cell.r < minR || cell.r > maxR || cell.c < minC || cell.c > maxC) continue
    const text = (cell.text ?? '').trim()
    if (!text) continue
    if (keptText != null) return false
    keptText = text
  }
  return true
}

/** Le celle 1x1 di una colonna, con la loro riga. Salta le posizioni coperte
 *  da una cella unita: lì il testo è di un'altra cella e non si tocca. */
function columnCells(grid: TableGrid, at: number): { row: number; cell: TableCell }[] {
  const map = ownerMap(grid)
  const out: { row: number; cell: TableCell }[] = []
  for (let r = 0; r < grid.rows; r++) {
    const owner = map[r]?.[at]
    if (!owner || owner.rowspan > 1 || owner.colspan > 1) continue
    out.push({ row: r, cell: owner })
  }
  return out
}

/**
 * Unisce due o più colonne adiacenti in una sola, con il separatore scelto.
 *
 * È l'inverso della separazione, e serve quando il rilevatore ha spezzato
 * quello che sulla pagina era un campo solo. La struttura la porta
 * `dropBoundary`, che sa cosa succede alle celle unite e **rifiuta** i casi
 * ambigui invece di sceglierli: qui si rifà solo il testo, prendendolo dalle
 * colonne originali, perché `dropBoundary` le unisce con uno spazio e qui il
 * separatore lo decide l'utente.
 */
export function joinColumns(
  grid: TableGrid,
  at: number,
  options: { separator: string; count?: number },
): TableGrid | null {
  const count = Math.max(2, Math.min(4, options.count ?? 2))
  if (at < 0 || at + count > grid.cols) return null
  const separator = options.separator

  // Il testo unito si legge dalle colonne di partenza, non dal risultato.
  const joined = new Map<number, string>()
  for (let r = 0; r < grid.rows; r++) {
    const parts: string[] = []
    for (let k = 0; k < count; k++) {
      const cell = grid.cells.find((c) => c.r === r && c.c === at + k)
      const text = (cell?.text ?? '').trim()
      if (text) parts.push(text)
    }
    joined.set(r, parts.join(separator))
  }

  let next = grid
  for (let k = 1; k < count; k++) {
    const dropped = dropBoundary(next, 'v', at + 1)
    if (!dropped) return null
    next = dropped
  }
  // Il confronto è con la colonna **di partenza**, non con il risultato di
  // `dropBoundary`: quello ha già concatenato i testi, e confrontandosi con lui
  // la cella risulterebbe «non cambiata» e resterebbe verificata mentre il suo
  // testo è una fusione.
  const cells = next.cells.map((cell) => {
    if (cell.c !== at || cell.r >= grid.rows) return cell
    const joinedText = joined.get(cell.r) ?? ''
    const original = grid.cells.find((c) => c.r === cell.r && c.c === at)?.text ?? ''
    if (joinedText === original) return cell
    return { ...cell, text: joinedText, source: 'manual' as const, verified: false }
  })
  return { ...next, cells }
}

/**
 * Normalizza gli spazi orizzontali di una colonna: sequenze di spazi e
 * tabulazioni diventano uno spazio, e gli spazi in testa e in coda spariscono.
 *
 * I **ritorni a capo restano**: su questi registri rappresentano le righe
 * della pagina, sono dato e non rumore. Si tolgono solo gli spazi che stanno
 * intorno a un a capo, che sono impaginazione.
 */
export function normalizeColumn(grid: TableGrid, at: number): TableGrid | null {
  if (at < 0 || at >= grid.cols) return null
  const cells = grid.cells.map((cell) => {
    if (cell.c !== at || cell.rowspan > 1 || cell.colspan > 1) return cell
    const normalized = (cell.text ?? '')
      .replace(/[^\S\n]+/g, ' ')
      .replace(/ *\n */g, '\n')
      .trim()
    return withText(cell, normalized)
  })
  return { ...grid, cells }
}

/** Frazioni digitate → tipografiche su una colonna (v. `lib/fractions.ts`). */
export function fractionsColumn(grid: TableGrid, at: number): TableGrid | null {
  if (at < 0 || at >= grid.cols) return null
  const cells = grid.cells.map((cell) =>
    cell.c !== at || cell.rowspan > 1 || cell.colspan > 1 ? cell : withText(cell, toFractionGlyphs(cell.text ?? '')),
  )
  return { ...grid, cells }
}

export type CaseMode = 'upper' | 'lower' | 'title'

/** Maiuscole, minuscole o iniziale maiuscola su una colonna. */
export function transformColumnCase(grid: TableGrid, at: number, mode: CaseMode): TableGrid | null {
  if (at < 0 || at >= grid.cols) return null
  const cells = grid.cells.map((cell) => {
    if (cell.c !== at || cell.rowspan > 1 || cell.colspan > 1) return cell
    const text = cell.text ?? ''
    const next =
      mode === 'upper'
        ? text.toUpperCase()
        : mode === 'lower'
          ? text.toLowerCase()
          : text.replace(/\p{L}[\p{L}\p{N}’'-]*/gu, (word) => word[0].toUpperCase() + word.slice(1).toLowerCase())
    return withText(cell, next)
  })
  return { ...grid, cells }
}

/**
 * Propaga verso il basso il valore di una cella, dalla riga `from` alla riga
 * `to` compresa.
 *
 * Serve per le intestazioni vuote e per i valori che sulla pagina valgono per
 * tutto il blocco sotto di sé. Non tocca le posizioni coperte da una cella
 * unita: lì non c'è una cella da riempire.
 */
export function fillDown(
  grid: TableGrid,
  at: number,
  from: number,
  to: number,
): TableGrid | null {
  if (at < 0 || at >= grid.cols) return null
  // Un intervallo rovesciato è una chiamata sbagliata, non «niente da fare»:
  // si rifiuta, come le altre operazioni, invece di restituire una griglia
  // identica che nasconde l'errore di chi ha chiamato.
  if (from < 0 || from >= grid.rows || to < from) return null
  const first = from
  const last = Math.min(to, grid.rows - 1)
  const source = grid.cells.find((c) => c.r === first && c.c === at)
  if (!source || source.rowspan > 1 || source.colspan > 1) return null
  const text = source.text ?? ''
  const targets = new Set(columnCells(grid, at).filter((c) => c.row > first && c.row <= last).map((c) => c.row))
  const cells = grid.cells.map((cell) =>
    targets.has(cell.r) && cell.c === at ? withText(cell, text) : cell,
  )
  return { ...grid, cells }
}

/**
 * Segna come verificate (o no) le celle con testo dentro l'intervallo.
 *
 * È la conferma dell'annotatore che la cella corrisponde alla scansione: il
 * testo non cambia. Una cella unita conta se la sua origine sta
 * nell'intervallo; le celle vuote non hanno niente da confermare.
 */
export function setVerified(
  grid: TableGrid,
  range: { startRow: number; startColumn: number; endRow: number; endColumn: number },
  verified: boolean,
): TableGrid {
  const cells = grid.cells.map((cell) =>
    cell.r >= range.startRow && cell.r <= range.endRow &&
    cell.c >= range.startColumn && cell.c <= range.endColumn &&
    (cell.text ?? '').trim() !== ''
      ? { ...cell, verified }
      : cell,
  )
  return { ...grid, cells }
}
