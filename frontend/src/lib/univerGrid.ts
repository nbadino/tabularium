/**
 * Il ponte fra il nostro modello di tabella e Univer.
 *
 * Funzioni pure, provabili senza browser: qui sta il rischio
 * dell'integrazione, non nel componente React. È la stessa divisione di
 * responsabilità che `gridSheet.ts` fa per Jspreadsheet CE.
 *
 * Tre cose restano **nostre**, e Univer non le vede mai:
 *
 * 1. **La geometria** (`vlines`/`hlines`, i confini piegati): è misurata sul
 *    ritaglio della scansione, non sul foglio di calcolo.
 * 2. **I metadati per cella** (`source`, `verified`): Univer possiede testo,
 *    stile e merge — non chi ha scritto il testo né se un umano l'ha
 *    confermato. È il dato di training, quindi non si delega.
 * 3. **Le colonne fantasma** e le righe di intestazione dichiarate: sono
 *    proprietà del documento, non della griglia di calcolo. Le righe di
 *    intestazione però *si vedono*: diventano bloccate e in grassetto.
 */
import type { ICellData, IWorkbookData, IWorksheetData } from '@univerjs/core'
import type { LocaleType } from '@univerjs/core'
import type { TableCell, TableGrid } from './types'

/** `CellValueType.STRING` e `BooleanNumber.TRUE/FALSE` di Univer.
 *
 *  Importare l'enum qui trascinerebbe il runtime di Univer dentro un modulo
 *  che deve restare puro (e provabile in Node): i valori sono quelli
 *  dichiarati dall'enum, non numeri scelti da noi. */
const CELL_STRING = 1
const TRUE = 1
const FALSE = 0

/** Id dell'unico foglio: il foglio di calcolo serve *una* tabella. */
export const UNIVER_SHEET_ID = 'tab'
/** Id dello stile delle righe di intestazione. */
const HEADER_STYLE_ID = 'tabHeader'

/** Nel documento serializzato i fogli sono parziali: Univer ammette uno
 *  snapshot che non dichiara tutto, e il resto lo mette la sua normalizzazione. */
function worksheetOf(data: IWorkbookData): Partial<IWorksheetData> | undefined {
  return data.sheets?.[UNIVER_SHEET_ID] ?? Object.values(data.sheets ?? {})[0]
}

/** Testo di una cella, in forma di stringa: Univer tiene numeri e booleani. */
function textAt(sheet: Partial<IWorksheetData> | undefined, r: number, c: number): string {
  const value = sheet?.cellData?.[r]?.[c]?.v
  return value == null ? '' : String(value)
}

/**
 * Il nostro modello → il documento di Univer.
 *
 * Le celle vuote non si scrivono: in Univer una cella assente è una cella
 * vuota, e materializzarla servirebbe solo a dipingere bordi che non esistono.
 * Fanno eccezione le righe di intestazione, che portano lo stile e quindi
 * devono esistere anche vuote.
 */
export function gridToUniver(grid: TableGrid): IWorkbookData {
  const rows = Math.max(1, grid.rows)
  const cols = Math.max(1, grid.cols)
  const header = Math.min(Math.max(0, grid.header_rows ?? 0), rows)
  const cellData: IWorksheetData['cellData'] = {}
  const mergeData: IWorksheetData['mergeData'] = []

  for (const cell of grid.cells) {
    if (cell.r >= rows || cell.c >= cols) continue
    const rs = Math.max(1, cell.rowspan ?? 1)
    const cs = Math.max(1, cell.colspan ?? 1)
    if (rs > 1 || cs > 1) {
      mergeData.push({
        startRow: cell.r,
        startColumn: cell.c,
        endRow: cell.r + rs - 1,
        endColumn: cell.c + cs - 1,
      })
    }
    const text = cell.text ?? ''
    const isHeader = cell.r < header
    if (!text && !isHeader) continue
    const entry: ICellData = {}
    if (text) {
      entry.v = text
      entry.t = CELL_STRING
    }
    if (isHeader) entry.s = HEADER_STYLE_ID
    cellData[cell.r] = { ...(cellData[cell.r] ?? {}), [cell.c]: entry }
  }

  return {
    id: 'tabularium-table',
    name: 'Table',
    appVersion: '0.25.1',
    locale: 'enUS' as LocaleType,
    styles: { [HEADER_STYLE_ID]: { bl: TRUE, bg: { rgb: '#f1f0ec' } } },
    sheetOrder: [UNIVER_SHEET_ID],
    sheets: {
      [UNIVER_SHEET_ID]: {
        id: UNIVER_SHEET_ID,
        name: 'Table',
        tabColor: '',
        hidden: FALSE,
        freeze: { xSplit: 0, ySplit: header, startRow: header, startColumn: 0 },
        rowCount: rows,
        columnCount: cols,
        zoomRatio: 1,
        scrollTop: 0,
        scrollLeft: 0,
        defaultColumnWidth: 96,
        defaultRowHeight: 24,
        mergeData,
        cellData,
        rowData: {},
        columnData: {},
        rowHeader: { width: 46 },
        columnHeader: { height: 22 },
        showGridlines: TRUE,
      },
    },
  }
}

/**
 * Il ritorno: dal documento di Univer al nostro modello.
 *
 * `previous` è la griglia da cui si è partiti: serve a riportare sui merge
 * riapplicati i metadati che Univer non conosce, e a conservare la geometria.
 * Una cella il cui testo è cambiato torna `source: 'manual'` e non verificata —
 * è una correzione umana da confermare.
 */
export function univerToGrid(data: IWorkbookData, previous: TableGrid): TableGrid {
  const sheet = worksheetOf(data)
  const rows = Math.max(1, sheet?.rowCount ?? previous.rows)
  const cols = Math.max(1, sheet?.columnCount ?? previous.cols)

  const spans = new Map<string, { rs: number; cs: number }>()
  const covered = new Set<string>()
  for (const range of sheet?.mergeData ?? []) {
    const rs = range.endRow - range.startRow + 1
    const cs = range.endColumn - range.startColumn + 1
    if (rs < 1 || cs < 1) continue
    spans.set(`${range.startRow}:${range.startColumn}`, { rs, cs })
    for (let r = range.startRow; r <= range.endRow; r++) {
      for (let c = range.startColumn; c <= range.endColumn; c++) {
        if (r !== range.startRow || c !== range.startColumn) covered.add(`${r}:${c}`)
      }
    }
  }

  const before = new Map(previous.cells.map((cell) => [`${cell.r}:${cell.c}`, cell]))
  const cells: TableCell[] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (covered.has(`${r}:${c}`)) continue
      const text = textAt(sheet, r, c)
      const old = before.get(`${r}:${c}`)
      const span = spans.get(`${r}:${c}`)
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
    // La geometria non vive nel foglio: passa attraverso, ritagliata alle
    // dimensioni nuove.
    phantom_cols: previous.phantom_cols.filter((c) => c < cols),
    vlines: (previous.vlines ?? []).filter((_, i) => i <= cols),
    hlines: (previous.hlines ?? []).filter((_, i) => i <= rows),
  }
}

/** Impronta del modello: serve a non riscrivere sul server ciò che è già lì. */
export function univerGridSignature(grid: TableGrid): string {
  return JSON.stringify([
    grid.rows,
    grid.cols,
    grid.phantom_cols,
    grid.header_rows ?? 0,
    grid.cells.map((cell) => [
      cell.r,
      cell.c,
      cell.rowspan,
      cell.colspan,
      cell.text,
      cell.source ?? '',
      cell.verified ?? false,
    ]),
  ])
}
