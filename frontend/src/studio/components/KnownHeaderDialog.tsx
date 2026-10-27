import { useState } from 'react'
import { apiDelete, apiPost } from '../../lib/api'
import type { HeaderTemplate, KnownTableHeader } from '../../lib/types'
import { Modal } from '../../app/ui'
import { useConfirm } from '../../app/confirm'
import { useI18n } from '../../i18n'

function HeaderPreview({ header }: { header: HeaderTemplate }) {
  return (
    <div className="overflow-auto border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] p-2">
      <div
        className="grid min-w-fit gap-px bg-[color:var(--color-rule-strong)]"
        style={{ gridTemplateColumns: `repeat(${header.cols}, minmax(52px, 1fr))` }}
      >
        {header.cells.map((cell) => (
          <div
            key={`${cell.r}:${cell.c}`}
            className="flex min-h-8 items-center bg-[color:var(--color-panel)] px-1.5 py-1 text-[11px] font-semibold"
            style={{ gridColumn: `${cell.c + 1} / span ${cell.colspan}`, gridRow: `${cell.r + 1} / span ${cell.rowspan}` }}
          >
            {cell.text}
          </div>
        ))}
      </div>
    </div>
  )
}

export default function KnownHeaderDialog({
  projectId,
  mode,
  template,
  headers,
  onHeadersChange,
  onApply,
  onClose,
}: {
  projectId: number
  mode: 'save' | 'apply'
  template?: HeaderTemplate
  headers: KnownTableHeader[]
  onHeadersChange: (items: KnownTableHeader[]) => void
  onApply?: (header: KnownTableHeader) => string | null
  onClose: () => void
}) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const [name, setName] = useState('')
  const [selectedId, setSelectedId] = useState(headers[0]?.id ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const selected = headers.find((header) => header.id === selectedId) ?? null

  const save = async () => {
    if (!template || !name.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const item = await apiPost<KnownTableHeader>(`/projects/${projectId}/table-headers`, {
        name: name.trim(),
        rows: template.rows,
        cols: template.cols,
        cells: template.cells,
      })
      onHeadersChange([...headers.filter((header) => header.id !== item.id && header.name.toLocaleLowerCase() !== item.name.toLocaleLowerCase()), item])
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const remove = async (header: KnownTableHeader) => {
    const ok = await confirm({
      title: t('knownHeader.deleteTitle'),
      message: t('knownHeader.deleteBody', { name: header.name }),
      acceptLabel: t('common.delete'),
    })
    if (!ok) return
    try {
      await apiDelete(`/projects/${projectId}/table-headers/${header.id}`)
      const next = headers.filter((item) => item.id !== header.id)
      onHeadersChange(next)
      setSelectedId(next[0]?.id ?? '')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const apply = () => {
    if (!selected || !onApply) return
    const message = onApply(selected)
    if (message) setError(message)
    else onClose()
  }

  return (
    <Modal
      title={mode === 'save' ? t('knownHeader.saveTitle') : t('knownHeader.applyTitle')}
      onClose={onClose}
      footer={
        mode === 'save' ? (
          <div className="flex items-center justify-end gap-2">
            <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
            <button type="button" className="btn btn-primary" disabled={busy || !name.trim()} onClick={() => void save()}>
              {busy ? t('common.saving') : t('common.save')}
            </button>
          </div>
        ) : (
          <div className="flex items-center justify-end gap-2">
            <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
            <button type="button" className="btn btn-primary" disabled={!selected} onClick={apply}>
              {t('knownHeader.replace')}
            </button>
          </div>
        )
      }
    >
      <div className="flex w-full flex-col gap-3 p-4">
        {error && <p className="border border-[color:var(--color-danger)] bg-[color:var(--color-fill)] p-2 text-[12px] text-[color:var(--color-danger)]">{error}</p>}
        {mode === 'save' ? (
          <>
            <label className="flex flex-col gap-1 text-[12px]">
              <span className="lbl !mb-0">{t('knownHeader.name')}</span>
              <input
                data-autofocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') void save() }}
                placeholder={t('knownHeader.namePlaceholder')}
                className="fld"
              />
            </label>
            <p className="text-[11px] text-[color:var(--color-ink-3)]">
              {t('knownHeader.selectionSize', { rows: template?.rows ?? 0, cols: template?.cols ?? 0 })}
            </p>
            {template && <HeaderPreview header={template} />}
          </>
        ) : headers.length === 0 ? (
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('knownHeader.empty')}</p>
        ) : (
          <>
            <div className="flex items-end gap-2">
              <label className="flex min-w-0 flex-1 flex-col gap-1 text-[12px]">
                <span className="lbl !mb-0">{t('knownHeader.choose')}</span>
                <select data-autofocus value={selectedId} onChange={(event) => setSelectedId(event.target.value)} className="fld">
                  {headers.map((header) => (
                    <option key={header.id} value={header.id}>{header.name} · {header.rows}×{header.cols}</option>
                  ))}
                </select>
              </label>
              {selected && (
                <button type="button" className="btn btn-sm btn-danger" onClick={() => void remove(selected)}>
                  {t('common.delete')}
                </button>
              )}
            </div>
            {selected && <HeaderPreview header={selected} />}
            <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('knownHeader.replaceHint')}</p>
          </>
        )}
      </div>
    </Modal>
  )
}
