import { useState } from 'react'
import { apiDownloadFile } from '../lib/api'
import type { RecognitionRun } from '../lib/types'
import { useI18n } from '../i18n'
import { UNASSIGNED, useCollections } from './collections'
import { Module } from './ui'

/** The formats, most useful first. The key is the API `format`. */
export const EXPORT_FORMATS = [
  { key: 'pdf', ext: 'pdf' },
  { key: 'markdown', ext: 'md' },
  { key: 'text', ext: 'txt' },
  { key: 'csv', ext: 'csv' },
  { key: 'json', ext: 'json' },
] as const

export type ExportFormat = (typeof EXPORT_FORMATS)[number]['key']
type Scope = 'reviewed' | 'raw'

const FORMAT_KEY = 'tabularium.export.format'

function rememberedFormat(): ExportFormat {
  try {
    const saved = localStorage.getItem(FORMAT_KEY)
    const known = EXPORT_FORMATS.find((format) => format.key === saved)
    if (known) return known.key
  } catch {
    // Storage may be unavailable (private window): the default still works.
  }
  return 'pdf'
}

function rememberFormat(format: ExportFormat) {
  try {
    localStorage.setItem(FORMAT_KEY, format)
  } catch {
    // Not remembering the choice is harmless.
  }
}

function formatSize(bytes: number, locale: string): string {
  const mb = bytes / (1024 * 1024)
  if (mb >= 1) return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(mb)} MB`
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(Math.max(1, bytes / 1024))} KB`
}

/**
 * Export of one recognition run: pick a format, pick which text, download.
 * One button, named after the file it produces.
 */
export function RunExport({
  run,
  busyRun,
  primary,
  onError,
}: {
  run: RecognitionRun
  /** The run is still going: its text is not final yet. */
  busyRun: boolean
  /** Export is the main action of the screen (nothing left to check). */
  primary: boolean
  onError: (error: unknown) => void
}) {
  const { t, locale } = useI18n()
  const { overview } = useCollections(run.project_id)
  const [format, setFormat] = useState<ExportFormat>(rememberedFormat)
  const [scope, setScope] = useState<Scope>('reviewed')
  const [collection, setCollection] = useState('all')
  // null when idle; the bytes received so far while downloading.
  const [received, setReceived] = useState<number | null>(null)
  const ext = EXPORT_FORMATS.find((item) => item.key === format)?.ext ?? format
  const collections = overview?.collections ?? []

  const pick = (next: ExportFormat) => {
    setFormat(next)
    rememberFormat(next)
  }

  const download = async () => {
    setReceived(0)
    try {
      const query = new URLSearchParams({ scope, format })
      if (collection !== 'all') query.set('collection', collection)
      const { blob, filename } = await apiDownloadFile(
        `/projects/${run.project_id}/recognition-runs/${run.id}/export?${query}`,
        setReceived,
      )
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename ?? `tabularium-run-${run.id}.${ext}`
      anchor.click()
      // Revoked later: some browsers start reading the blob after click() returns.
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (error) {
      onError(error)
    } finally {
      setReceived(null)
    }
  }

  const busy = received !== null
  return (
    <Module tab={t('recognition.export.title')}>
      <div className="grid gap-x-8 gap-y-5 lg:grid-cols-[minmax(0,3fr)_minmax(16rem,2fr)]">
        <fieldset className="min-w-0" disabled={busy}>
          <legend className="lbl">{t('recognition.export.format')}</legend>
          <div className="space-y-2">
            {EXPORT_FORMATS.map((item) => (
              <label key={item.key} className="flex items-start gap-2 text-[13px]">
                <input type="radio" name={`export-format-${run.id}`} className="mt-[3px]" checked={format === item.key} onChange={() => pick(item.key)} />
                <span className="min-w-0">
                  <b className="font-semibold">{t(`recognition.export.${item.key}`)}</b>
                  <span className="mono ml-1.5 text-[11px] text-[color:var(--color-ink-3)]">.{item.ext}</span>
                  <span className="block text-[12px] text-[color:var(--color-ink-2)]">{t(`recognition.export.${item.key}Hint`)}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="min-w-0 space-y-5">
          <fieldset disabled={busy}>
            <legend className="lbl">{t('recognition.export.version')}</legend>
            <div className="space-y-2">
              {(['reviewed', 'raw'] as const).map((value) => (
                <label key={value} className="flex items-start gap-2 text-[13px]">
                  <input type="radio" name={`export-scope-${run.id}`} className="mt-[3px]" checked={scope === value} onChange={() => setScope(value)} />
                  <span className="min-w-0">
                    <b className="font-semibold">{t(`recognition.export.${value}`)}</b>
                    <span className="block text-[12px] text-[color:var(--color-ink-2)]">{t(`recognition.export.${value}Hint`)}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          {collections.length > 0 && (
            <label className="block">
              <span className="lbl">{t('recognition.export.pages')}</span>
              <select className="fld" value={collection} disabled={busy} onChange={(e) => setCollection(e.target.value)}>
                <option value="all">{t('scope.allCollections')}</option>
                {collections.map((c) => <option key={c.id} value={String(c.id)}>{c.name}</option>)}
                <option value={UNASSIGNED}>{t('collections.none')}</option>
              </select>
            </label>
          )}

          <div>
            <button type="button" className={`btn ${primary ? 'btn-primary' : ''}`} disabled={busyRun || busy} aria-busy={busy} onClick={() => void download()}>
              {busy
                ? (received ? t('recognition.export.preparingSize', { size: formatSize(received, locale) }) : t('recognition.export.preparing'))
                : t('recognition.export.download', { ext })}
            </button>
            <p className="mt-1.5 text-[12px] text-[color:var(--color-ink-2)]" aria-live="polite">
              {busyRun ? t('recognition.export.waitRun') : ''}
            </p>
          </div>
        </div>
      </div>
    </Module>
  )
}
