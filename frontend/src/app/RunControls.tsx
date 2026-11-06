/**
 * Pause, resume and stop for a recognition run, and the header indicator that
 * finds the runs in progress from any page.
 *
 * A run started from the project page opens its results, which had no way to
 * stop it: with a paid per-page provider that meant hundreds of pages read
 * against the user's will. The controls live here once and every view that
 * shows a running session uses them.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import type { RecognitionRun } from '../lib/types'
import { useI18n } from '../i18n'
import { useConfirm } from './confirm'
import { projectPath } from './projectPaths'
import { Menu } from './ui'

export const runIsActive = (run: RecognitionRun | null | undefined) =>
  run?.state === 'queued' || run?.state === 'running'

type Action = 'pause' | 'resume' | 'cancel'

export function RecognitionRunControls({
  run,
  onChange,
  onError,
  size = '',
}: {
  run: RecognitionRun
  onChange: (run: RecognitionRun) => void
  onError?: (error: unknown) => void
  size?: '' | 'btn-sm'
}) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const [busy, setBusy] = useState<Action | null>(null)
  if (!runIsActive(run)) return null

  const act = async (action: Action) => {
    if (action === 'cancel') {
      const left = run.total_pages - run.completed_pages
      const ok = await confirm({
        title: t('recognition.stopTitle'),
        message: t('recognition.stopMessage', { n: left }),
        acceptLabel: t('recognition.stop'),
      })
      if (!ok) return
    }
    setBusy(action)
    try {
      onChange(await apiPost<RecognitionRun>(`/projects/${run.project_id}/recognition-runs/${run.id}/${action}`))
    } catch (e) {
      onError?.(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <span className="inline-flex flex-wrap gap-2">
      {run.paused ? (
        <button type="button" className={`btn btn-primary ${size}`} disabled={busy != null} onClick={() => void act('resume')}>
          {t('recognition.resume')}
        </button>
      ) : (
        <button type="button" className={`btn ${size}`} disabled={busy != null} onClick={() => void act('pause')} title={t('recognition.pauseHint')}>
          {t('recognition.pause')}
        </button>
      )}
      <button type="button" className={`btn btn-danger ${size}`} disabled={busy != null} onClick={() => void act('cancel')}>
        {busy === 'cancel' ? t('recognition.stopping') : t('recognition.stop')}
      </button>
    </span>
  )
}

const POLL_MS = 4000

/** The runs in progress across the projects this user can see, polled from the header. */
export function ActiveRunsIndicator() {
  const { t } = useI18n()
  const [runs, setRuns] = useState<RecognitionRun[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const out = await apiGet<{ items: RecognitionRun[] }>('/recognition-runs')
      setRuns(out.items.filter(runIsActive))
    } catch {
      // The header must not fail because one poll did: keep the last state.
    }
  }, [])

  useEffect(() => {
    void refresh()
    const id = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  if (runs.length === 0) return null
  const update = (next: RecognitionRun) =>
    setRuns((before) => before.map((r) => (r.id === next.id ? next : r)).filter(runIsActive))
  const anyRunning = runs.some((r) => !r.paused)
  const label = runs.length === 1
    ? t(runs[0].paused ? 'recognition.indicatorPaused' : 'recognition.indicatorOne', {
        done: runs[0].completed_pages,
        total: runs[0].total_pages,
      })
    : t('recognition.indicatorMany', { n: runs.length })

  return (
    <Menu
      label={
        <span className="inline-flex items-center gap-1.5" aria-live="polite">
          <span
            aria-hidden="true"
            className={`inline-block h-2 w-2 rounded-full ${anyRunning ? 'animate-pulse bg-[color:var(--color-sig-plate)]' : 'bg-[color:var(--color-ink-3)]'}`}
          />
          {label}
        </span>
      }
    >
      {runs.map((run) => (
        <div key={run.id} className="flex flex-col gap-1.5 px-2 py-1.5">
          <div className="flex items-baseline gap-2">
            <Link to={projectPath(run.project_id, `riconoscimenti/${run.id}`)} className="font-semibold">
              {t('recognition.indicatorRun', { id: run.id })}
            </Link>
            <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
              {t('recognition.progress', { done: run.completed_pages, total: run.total_pages })}
              {run.paused ? ` · ${t('recognition.statusPaused')}` : ''}
            </span>
          </div>
          <RecognitionRunControls run={run} onChange={update} onError={(e) => setError(String((e as Error)?.message ?? e))} size="btn-sm" />
        </div>
      ))}
      {error && <div className="px-2 pb-1 text-[11px] text-[color:var(--color-sig-plate)]">{error}</div>}
    </Menu>
  )
}
