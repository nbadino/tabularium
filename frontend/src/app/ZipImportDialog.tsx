import { useEffect, useRef, useState } from 'react'
import { Modal } from './ui'
import { useI18n, tn } from '../i18n'
import { importZip, inspectZip, uploadZip, ZIP_SKIP_REASONS } from '../lib/zipImport'
import type { ZipJob, ZipLayout, ZipPlan, ZipReport } from '../lib/zipImport'
import { describeError } from '../lib/errors'

type Step =
  | { kind: 'uploading'; sent: number; size: number }
  | { kind: 'inspecting' }
  | { kind: 'choosing'; plan: ZipPlan }
  | { kind: 'importing'; job: ZipJob | null }
  | { kind: 'done'; report: ZipReport }
  | { kind: 'failed'; message: string }

const mb = (bytes: number) => (bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)

function Bar({ value, label }: { value: number; label: string }) {
  return (
    <div className="mt-2 h-1.5 overflow-hidden border border-[color:var(--color-rule-strong)]" role="progressbar" aria-label={label} aria-valuenow={Math.round(value * 100)} aria-valuemin={0} aria-valuemax={100}>
      <div className="h-full bg-[color:var(--color-sig)] transition-[width] duration-300" style={{ width: `${Math.max(3, value * 100)}%` }} />
    </div>
  )
}

/**
 * One ZIP archive from upload to summary. Folders become collections only when
 * the user says so; a single folder or loose files are imported without asking.
 * `collectionId` is where a flat import lands (`null`: no collection).
 */
export default function ZipImportDialog({
  projectId,
  file,
  collectionId,
  onClose,
  onImported,
}: {
  projectId: number
  file: File
  collectionId: number | null
  onClose: () => void
  onImported: () => void
}) {
  const { t } = useI18n()
  const [step, setStep] = useState<Step>({ kind: 'uploading', sent: 0, size: file.size })
  const [layout, setLayout] = useState<ZipLayout>('collections')
  const uploadId = useRef<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  const fail = (e: unknown) => setStep({ kind: 'failed', message: describeError(e).messaggio })

  const runImport = async (id: string, chosen: ZipLayout) => {
    setStep({ kind: 'importing', job: null })
    try {
      const report = await importZip(projectId, id, chosen, chosen === 'flat' ? collectionId : null, (job) =>
        setStep({ kind: 'importing', job }),
      )
      setStep({ kind: 'done', report })
      onImported()
    } catch (e) {
      fail(e)
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    abort.current = controller
    void (async () => {
      try {
        const id = await uploadZip(projectId, file, (sent, size) => setStep({ kind: 'uploading', sent, size }), controller.signal)
        uploadId.current = id
        setStep({ kind: 'inspecting' })
        const plan = await inspectZip(projectId, id)
        if (plan.needs_choice) setStep({ kind: 'choosing', plan })
        else await runImport(id, 'flat')
      } catch (e) {
        if (!controller.signal.aborted) fail(e)
      }
    })()
    return () => controller.abort()
    // The dialog handles one file for its whole life.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const busy = step.kind === 'uploading' || step.kind === 'inspecting' || step.kind === 'importing'
  const close = () => {
    if (step.kind === 'uploading') abort.current?.abort()
    onClose()
  }

  const footer = step.kind === 'choosing' ? (
    <>
      <button type="button" className="btn" onClick={close}>{t('common.cancel')}</button>
      <button type="button" className="btn btn-primary" onClick={() => uploadId.current && void runImport(uploadId.current, layout)}>
        {t('zip.import')}
      </button>
    </>
  ) : (
    <button type="button" className={busy ? 'btn' : 'btn btn-primary'} onClick={close}>
      {busy && step.kind === 'uploading' ? t('common.cancel') : t('common.close')}
    </button>
  )

  return (
    <Modal title={t('zip.title', { name: file.name })} onClose={close} closeOnBackdrop={!busy} footer={footer}>
      <div className="p-3">
      {step.kind === 'uploading' && (
        <div>
          <p className="text-[13px]">{t('zip.uploading', { sent: mb(step.sent), size: mb(step.size) })}</p>
          <Bar value={step.size ? step.sent / step.size : 0} label={t('zip.uploadingLabel')} />
        </div>
      )}
      {step.kind === 'inspecting' && <p className="text-[13px]">{t('zip.inspecting')}</p>}
      {step.kind === 'importing' && (
        <div>
          <p className="text-[13px]">
            {step.job && step.job.total > 0
              ? t(step.job.phase === 'extracting' ? 'zip.extracting' : 'zip.registering', { current: step.job.current, total: step.job.total })
              : t('zip.starting')}
          </p>
          <Bar value={step.job && step.job.total ? step.job.current / step.job.total : 0} label={t('zip.importingLabel')} />
        </div>
      )}
      {step.kind === 'choosing' && (
        <fieldset className="space-y-3">
          <legend className="text-[13px]">{tn('zip.foldersFound', step.plan.folders.length)}</legend>
          <ul className="max-h-40 overflow-auto border border-[color:var(--color-rule)] text-[12px]">
            {step.plan.folders.map((folder) => (
              <li key={folder.name} className="flex justify-between gap-3 border-b border-[color:var(--color-rule)] px-2 py-1 last:border-b-0">
                <span className="truncate">{folder.name}</span>
                <span className="mono text-[color:var(--color-ink-2)]">{tn('zip.files', folder.files)}</span>
              </li>
            ))}
          </ul>
          <label className="flex items-start gap-2 text-[13px]">
            <input type="radio" name="zip-layout" checked={layout === 'collections'} onChange={() => setLayout('collections')} />
            <span>
              <b className="font-semibold">{t('zip.layoutCollections')}</b>
              <span className="block text-[12px] text-[color:var(--color-ink-2)]">
                {t('zip.layoutCollectionsHint')}
                {step.plan.loose_files > 0 && <> {tn('zip.looseFiles', step.plan.loose_files)}</>}
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 text-[13px]">
            <input type="radio" name="zip-layout" checked={layout === 'flat'} onChange={() => setLayout('flat')} />
            <span>
              <b className="font-semibold">{t('zip.layoutFlat')}</b>
              <span className="block text-[12px] text-[color:var(--color-ink-2)]">{t('zip.layoutFlatHint')}</span>
            </span>
          </label>
          {step.plan.skipped_total > 0 && (
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{tn('zip.willSkip', step.plan.skipped_total)}</p>
          )}
        </fieldset>
      )}
      {step.kind === 'done' && <ZipSummary report={step.report} />}
      {step.kind === 'failed' && <p role="alert" className="text-[13px] text-[color:var(--color-sig-text)]">{step.message}</p>}
      </div>
    </Modal>
  )
}

export function ZipSummary({ report }: { report: ZipReport }) {
  const { t } = useI18n()
  return (
    <div className="space-y-3 text-[13px]">
      <p className="text-[15px] font-semibold">{tn('zip.imported', report.registered)}</p>
      {(report.collections.length > 0 || report.unassigned_pages > 0) && (
        <ul className="border border-[color:var(--color-rule)] text-[12px]">
          {report.collections.map((c) => (
            <li key={c.id} className="flex justify-between gap-3 border-b border-[color:var(--color-rule)] px-2 py-1 last:border-b-0">
              <span className="truncate">{c.name} <span className="text-[color:var(--color-ink-2)]">· {t(c.created ? 'zip.created' : 'zip.existing')}</span></span>
              <span className="mono">{tn('zip.pages', c.pages)}</span>
            </li>
          ))}
          {report.unassigned_pages > 0 && (
            <li className="flex justify-between gap-3 px-2 py-1">
              <span>{t('collections.none')}</span>
              <span className="mono">{tn('zip.pages', report.unassigned_pages)}</span>
            </li>
          )}
        </ul>
      )}
      {report.duplicates > 0 && <p>{tn('zip.duplicates', report.duplicates)}</p>}
      {report.skipped_total > 0 && (
        <div>
          <p className="font-semibold">{tn('zip.skipped', report.skipped_total)}</p>
          <ul className="mt-1 list-inside list-disc text-[12px]">
            {ZIP_SKIP_REASONS.filter((r) => report.skipped_counts[r]).map((r) => (
              <li key={r}>{t(`zip.reason.${r}`)}: <span className="mono">{report.skipped_counts[r]}</span></li>
            ))}
          </ul>
          <details className="mt-1 text-[12px]">
            <summary className="cursor-pointer">{t('zip.skippedNames')}</summary>
            <ul className="mono mt-1 max-h-32 overflow-auto text-[11px]">
              {report.skipped.map((s, i) => <li key={i} className="break-all">{s.name} — {t(`zip.reason.${s.reason}`)}</li>)}
            </ul>
          </details>
        </div>
      )}
      {report.errors.length > 0 && (
        <div>
          <p className="font-semibold">{tn('zip.errors', report.errors.length)}</p>
          <ul className="mono mt-1 max-h-32 overflow-auto text-[11px]">
            {report.errors.map((e, i) => <li key={i} className="break-all">{e}</li>)}
          </ul>
        </div>
      )}
      {report.ignored > 0 && <p className="text-[12px] text-[color:var(--color-ink-2)]">{tn('zip.ignored', report.ignored)}</p>}
    </div>
  )
}
