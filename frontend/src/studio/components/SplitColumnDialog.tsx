/**
 * «Separa per separatore»: si sceglie il separatore e si vede **prima** cosa
 * succede alle righe.
 *
 * L'anteprima non è decorazione. La divisione riscrive il testo di una colonna
 * intera e non entra nella storia di Univer (il documento si ricostruisce), e
 * su un registro di cinquanta righe «Doris .. (Br)» diviso sullo spazio dà due
 * cose diverse da «Doris .. (Br) stmr.»: finché non lo si vede, ogni scelta di
 * separatore è una scommessa.
 */
import { useMemo, useState } from 'react'
import type { TableGrid } from '../../lib/types'
import { splitColumn, splitHeaderAcrossRows, splitStartRow } from '../../lib/grid'
import { Modal } from '../../app/ui'
import { useI18n } from '../../i18n'

interface SplitColumnDialogProps {
  grid: TableGrid
  /** Colonna da dividere (0-based). */
  column: number
  /** Riceve il modello nuovo; `null` quando la divisione non è possibile. */
  onApply: (next: TableGrid | null, parts: number, mode?: 'columns' | 'headerRows') => void
  onClose: () => void
}

/** Il separatore di partenza è lo spazio: sui registri è quello che ricorre. */
const PRESETS: { key: string; value: string }[] = [
  { key: 'splitSpace', value: ' ' },
  { key: 'splitComma', value: ',' },
  { key: 'splitSemicolon', value: ';' },
  { key: 'splitDash', value: '-' },
]

const PREVIEW_ROWS = 6

export default function SplitColumnDialog({ grid, column, onApply, onClose }: SplitColumnDialogProps) {
  const { t } = useI18n()
  const [separator, setSeparator] = useState(' ')
  const [custom, setCustom] = useState('')
  const [parts, setParts] = useState(2)
  const [fromRight, setFromRight] = useState(false)
  const [splitHeader, setSplitHeader] = useState(true)
  // Parte dai dati, non dalla testata. Le testate unite sono la ragione più
  // frequente per cui la separazione veniva rifiutata prima ancora di vedere
  // l'anteprima (per esempio «Year blt» sopra due colonne).
  const [startRow, setStartRow] = useState(splitStartRow(grid, column) + 1)
  const [endRow, setEndRow] = useState(grid.rows)

  const effective = custom !== '' ? custom : separator
  const result = useMemo(() => splitColumn(grid, column, { separator: effective, maxParts: parts, fromRight, startRow: startRow - 1, endRow: endRow - 1, splitHeader }), [
    grid,
    column,
    effective,
    parts, fromRight, splitHeader, startRow, endRow,
  ])
  const verticalHeader = useMemo(
    () => splitHeaderAcrossRows(grid, column, { separator: effective, fromRight }),
    [grid, column, effective, fromRight],
  )

  /** Quante righe hanno davvero qualcosa da dividere: se sono zero, non c'è
   *  niente da fare e il pulsante non deve illudere. */
  const affected = useMemo(() => {
    if (!result) return 0
    let n = 0
    for (let r = startRow - 1; r < endRow; r++) {
      const before = grid.cells.find((c) => c.r === r && c.c === column)?.text ?? ''
      const after = result.cells.find((c) => c.r === r && c.c === column)?.text ?? ''
      if (after !== before) n += 1
    }
    return n
  }, [grid, column, result, startRow, endRow])

  const preview = useMemo(() => {
    const rows: { before: string; after: string[] }[] = []
    for (let r = startRow - 1; r < endRow && rows.length < PREVIEW_ROWS; r++) {
      const before = grid.cells.find((c) => c.r === r && c.c === column)?.text ?? ''
      if (!before) continue
      const after: string[] = []
      for (let k = 0; k < parts; k++) {
        after.push(result?.cells.find((c) => c.r === r && c.c === column + k)?.text ?? '')
      }
      rows.push({ before, after })
    }
    return rows
  }, [grid, column, parts, result, startRow, endRow])

  const verticalPreview = useMemo(() => {
    if (!verticalHeader) return []
    return verticalHeader.cells
      .filter((cell) => cell.c === column && cell.r < startRow - 1 && (cell.text ?? '').trim() !== '')
      .sort((a, b) => a.r - b.r)
      .map((cell) => cell.text)
  }, [column, startRow, verticalHeader])

  const verticalMode = affected === 0 && verticalHeader !== null
  const chosenResult = verticalMode ? verticalHeader : result

  return (
    <Modal
      title={t('table.splitTitle')}
      wide
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose} className="btn">
            {t('common.cancel')}
          </button>
          <button
            type="button"
            disabled={!chosenResult || (affected === 0 && !verticalHeader)}
            onClick={() => onApply(chosenResult, verticalMode ? verticalPreview.length : parts, verticalMode ? 'headerRows' : 'columns')}
            className="btn btn-primary"
          >
            {t('table.splitRun')}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3 p-3">
        <p className="text-[12px] text-[color:var(--color-ink-2)]">
          {t('table.splitColumnN', { n: column + 1 })}
        </p>

        <div className="flex flex-wrap items-end gap-3">
          <div>
            <span className="lbl">{t('table.splitSeparator')}</span>
            <div className="flex flex-wrap items-center gap-1">
              {PRESETS.map((preset) => (
                <button
                  key={preset.key}
                  type="button"
                  aria-pressed={custom === '' && separator === preset.value}
                  onClick={() => {
                    setCustom('')
                    setSeparator(preset.value)
                  }}
                  className={`btn btn-sm ${
                    custom === '' && separator === preset.value
                      ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white'
                      : ''
                  }`}
                >
                  {t(`table.${preset.key}`)}
                </button>
              ))}
              <input
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                placeholder={t('table.splitOther')}
                aria-label={t('table.splitOther')}
                className="fld fld-mono w-20"
              />
            </div>
          </div>
          <div>
            <label className="lbl" htmlFor="split-parts">
              {t('table.splitParts')}
            </label>
            <input
              id="split-parts"
              type="number"
              min={2}
              max={8}
              value={parts}
              onChange={(e) => setParts(Math.max(2, Math.min(8, Number(e.target.value) || 2)))}
              className="fld fld-mono w-16"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={fromRight} onChange={(event) => setFromRight(event.target.checked)} />{t('table.splitFromRight')}</label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={splitHeader} onChange={(event) => setSplitHeader(event.target.checked)} />{t('table.splitHeader')}</label>
          <label className="text-xs">{t('table.splitFirstRow')}<input aria-label={t('table.splitFirstRow')} type="number" min={1} max={grid.rows} value={startRow} onChange={(event) => setStartRow(Math.max(1, Math.min(grid.rows, Number(event.target.value) || 1)))} className="fld fld-mono ml-2 w-20" /></label>
          <label className="text-xs">{t('table.splitLastRow')}<input aria-label={t('table.splitLastRow')} type="number" min={startRow} max={grid.rows} value={endRow} onChange={(event) => setEndRow(Math.max(1, Math.min(grid.rows, Number(event.target.value) || grid.rows)))} className="fld fld-mono ml-2 w-20" /></label>
        </div>
        <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('table.splitRangeHint')}</p>
        {!result && !verticalHeader ? (
          <p className="border border-[color:var(--color-warn)] px-2 py-1 text-[12px] text-[color:var(--color-warn)]">
            {t('table.splitMerged')}
          </p>
        ) : verticalMode ? (
          <>
            <span className="lbl">{t('table.splitPreview')}</span>
            <div className="flex flex-col gap-px border border-[color:var(--color-rule)] bg-[color:var(--color-rule)]">
              {verticalPreview.map((text, i) => (
                <span key={`${text}-${i}`} className="bg-[color:var(--color-sheet)] px-2 py-1 text-[12px]">
                  {text}
                </span>
              ))}
            </div>
            <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('table.splitHeaderRowsHint')}</p>
          </>
        ) : affected === 0 ? (
          <p className="border border-[color:var(--color-warn)] px-2 py-1 text-[12px] text-[color:var(--color-warn)]">
            {t('table.splitEmpty')}
          </p>
        ) : (
          <>
            <span className="lbl">{t('table.splitPreview')}</span>
            <table className="w-full border-collapse text-[12px]">
              <tbody>
                {preview.map((row) => (
                  <tr key={row.before + row.after.join('|')}>
                    <td className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1">
                      {row.before}
                    </td>
                    <td className="w-6 px-1 text-center text-[color:var(--color-ink-3)]">→</td>
                    {row.after.map((text, k) => (
                      <td
                        key={k}
                        className="border border-[color:var(--color-rule)] px-2 py-1"
                        style={{ background: k === 0 ? 'var(--color-sig-wash)' : 'var(--color-sheet)' }}
                      >
                        {text || <span className="text-[color:var(--color-ink-3)]">—</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="text-[11px] text-[color:var(--color-ink-2)]">
              {t('table.splitAffected', { n: affected })}
            </p>
          </>
        )}
      </div>
    </Modal>
  )
}
