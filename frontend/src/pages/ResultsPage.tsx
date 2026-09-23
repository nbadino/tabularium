import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { apiDownload, apiGet, apiPost } from '../lib/api'
import type { RecognitionRun } from '../lib/types'
import { Badge, ErrorNotice, Field, Module, Notice, Progress } from '../app/ui'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { syncInferenceFromBackend, toggleInferenceEnabled, useInference } from '../app/inference'
import { useI18n } from '../i18n'
import { useCanAdminister } from '../app/auth'
import { pageLabel } from '../lib/pageLabel'
import { runTitle, useModelNames } from '../app/models/names'
import { formatShortDate } from '../lib/dates'

const isActive = (run: RecognitionRun | null) => run?.state === 'queued' || run?.state === 'running'

const stateKey = (state: string) => {
  const keys: Record<string, string> = {
    queued: 'recognition.statusQueued',
    running: 'recognition.statusRunning',
    finished: 'recognition.statusFinished',
    finished_with_errors: 'recognition.statusFinishedWithErrors',
    failed: 'recognition.statusFailed',
    cancelled: 'recognition.statusCancelled',
  }
  return keys[state] ?? state
}

function providerLabel(provider: string, engine: RecognitionRun['engine'], t: (key: string) => string): string {
  const effectiveProvider = engine === 'ocr' ? 'local' : provider
  return ['local', 'ssh', 'vast', 'runpod', 'modal', 'custom'].includes(effectiveProvider)
    ? t(`recognition.provider.${effectiveProvider}`)
    : effectiveProvider
}

/** Una pagina «completata» senza blocchi non è un successo: il modello ha
 *  risposto e non ha trovato niente. Mostrarla in verde la nascondeva. */
const isEmptyResult = (item: NonNullable<RecognitionRun['items']>[number]) => item.state === 'finished' && item.blocks === 0

type PageFilter = 'all' | 'drafts' | 'empty' | 'failed'

export default function ResultsPage() {
  const { t, locale } = useI18n()
  const nameOf = useModelNames()
  const [pageFilter, setPageFilter] = useState<PageFilter>('all')
  const [params, setParams] = useSearchParams()
  const inference = useInference()
  const canManageInference = useCanAdminister()
  const [projectId, setProjectId] = useState<number | ''>(() => Number(params.get('project')) || '')
  const [runs, setRuns] = useState<RecognitionRun[]>([])
  const [run, setRun] = useState<RecognitionRun | null>(null)
  const [exportFormat, setExportFormat] = useState<'json' | 'text' | 'csv'>('text')
  const [error, setError] = useState<unknown>(null)

  const openRun = async (pid: number, runId: number) => {
    const next = await apiGet<RecognitionRun>(`/projects/${pid}/recognition-runs/${runId}`)
    setRun(next)
    setPageFilter('all')
    setParams({ project: String(pid), run: String(runId) }, { replace: true })
  }

  const onProject = async (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setRuns([])
    setRun(null)
    if (pid === '') return
    try {
      const out = await apiGet<{ items: RecognitionRun[] }>(`/recognition-runs?project_id=${pid}`)
      setRuns(out.items)
      const requested = Number(params.get('run'))
      const first = out.items.find((item) => item.id === requested) ?? out.items[0]
      if (first) await openRun(pid, first.id)
    } catch (e) {
      setError(e)
    }
  }

  const projects = useProjects((fallback) => void onProject(projectId === '' ? fallback : projectId), setError)

  useEffect(() => {
    if (!run || !isActive(run)) return
    let stopped = false
    const tick = async () => {
      try {
        const next = await apiGet<RecognitionRun>(`/projects/${run.project_id}/recognition-runs/${run.id}`)
        if (!stopped) {
          setRun(next)
          if (!isActive(next)) await syncInferenceFromBackend()
        }
      } catch (e) {
        if (!stopped) setError(e)
      }
    }
    const id = setInterval(() => void tick(), 2000)
    return () => { stopped = true; clearInterval(id) }
  }, [run?.id, run?.state])

  const download = async (scope: 'raw' | 'reviewed') => {
    if (!run) return
    try {
      const blob = await apiDownload(`/projects/${run.project_id}/recognition-runs/${run.id}/export?scope=${scope}&format=${exportFormat}`)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      const extension = exportFormat === 'text' ? 'txt' : exportFormat
      anchor.download = `tabularium-run-${run.id}-${scope}.${extension}`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e)
    }
  }

  const disable = async () => {
    try {
      await toggleInferenceEnabled(false)
    } catch (e) {
      setError(e)
    }
  }

  const retryFailed = async () => {
    if (!run) return
    try {
      const next = await apiPost<RecognitionRun>(`/projects/${run.project_id}/recognition-runs/${run.id}/retry`)
      setRuns((before) => [next, ...before.filter((item) => item.id !== next.id)])
      setRun(next)
      setParams({ project: String(next.project_id), run: String(next.id) }, { replace: true })
    } catch (e) {
      setError(e)
    }
  }

  const pct = run?.total_pages ? (run.completed_pages / run.total_pages) * 100 : 0
  const nextReview = run?.items?.find((item) => item.drafts > 0) ?? run?.items?.[0]
  const items = run?.items ?? []
  const counts: Record<PageFilter, number> = {
    all: items.length,
    drafts: items.filter((item) => item.drafts > 0).length,
    empty: items.filter(isEmptyResult).length,
    failed: items.filter((item) => item.state === 'failed').length,
  }
  const shownItems = items.filter((item) =>
    pageFilter === 'drafts'
      ? item.drafts > 0
      : pageFilter === 'empty'
        ? isEmptyResult(item)
        : pageFilter === 'failed'
          ? item.state === 'failed'
          : true,
  )

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">{t('recognition.resultsTitle')}</h1>
        <p className="mt-1 max-w-[72ch] text-[13px] text-[color:var(--color-ink-2)]">{t('recognition.resultsIntro')}</p>
      </div>
      {error != null && <div className="mb-3"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}

      <div className="grid gap-3 lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className="space-y-3">
          <Module tab={t('recognition.project')}>
            <Field label={t('recognition.projectPick')}>
              <select value={projectId} onChange={(e) => void onProject(e.target.value === '' ? '' : Number(e.target.value))} className="fld">
                <option value="">{t('common.chooseProject')}</option>
                {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </Field>
          </Module>
          <Module tab={t('recognition.recent')} quiet flush>
            {runs.length === 0 ? <p className="p-3 text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.noRuns')}</p> : (
              <ul className="divide-y divide-[color:var(--color-rule)]">
                {runs.map((item) => (
                  <li key={item.id}>
                    <button type="button" onClick={() => projectId !== '' && void openRun(projectId, item.id)} className={`w-full p-2 text-left ${run?.id === item.id ? 'bg-[color:var(--color-sig-wash)] outline outline-2 -outline-offset-2 outline-[color:var(--color-sig)]' : 'hover:bg-[color:var(--color-fill)]'}`}>
                      <span className="flex items-center gap-2"><b className="mono text-[11px]">#{item.id}</b><span className="min-w-0 flex-1 truncate text-[12px] font-semibold">{runTitle(item, nameOf)}</span></span>
                      <span className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-[color:var(--color-ink-3)]">
                        <span>{t('recognition.runPagesCount', { done: item.completed_pages, total: item.total_pages })}</span>
                        <span>· {providerLabel(item.provider, item.engine, t)}</span>
                        <span className="mono">· {formatShortDate(item.created_at, locale)}</span>
                        {item.state !== 'finished' && <Badge tone={isActive(item) ? 'progress' : 'warn'}>{t(stateKey(item.state))}</Badge>}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Module>
        </div>

        {!run ? (
          <Module tab={t('recognition.resultsTitle')} quiet><p className="text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.pickRun')}</p></Module>
        ) : (
          <div className="space-y-3">
            <Module tab={`${t('recognition.resultsTitle')} · #${run.id}`} aux={<Badge tone={isActive(run) ? 'progress' : run.failed_pages ? 'warn' : 'ok'}>{t(stateKey(run.state))}</Badge>}>
              <div className="mb-2 flex flex-wrap items-baseline gap-x-3">
                <span className="text-[18px] font-bold">{runTitle(run, nameOf)}</span>
                <span className="mono text-[11px] text-[color:var(--color-ink-3)]">{formatShortDate(run.created_at, locale)}</span>
              </div>
              <Progress value={pct} label={t('recognition.progress', { done: run.completed_pages, total: run.total_pages })} />
              <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px]">
                <span>{t('recognition.progress', { done: run.completed_pages, total: run.total_pages })}</span>
                <span className="mono text-[11px] text-[color:var(--color-ink-3)]">{providerLabel(run.provider, run.engine, t)} · {run.adapter_id}</span>
                <span className="ml-auto flex flex-wrap gap-2">
                  {nextReview && (
                    <Link to={`/annotazione?project=${run.project_id}&page=${nextReview.page_id}&run=${run.id}`} className="btn btn-primary no-underline">
                      {isActive(run) ? t('recognition.openStudio') : t('recognition.startReview')}
                    </Link>
                  )}
                  <select value={exportFormat} onChange={(e) => setExportFormat(e.target.value as 'json' | 'text' | 'csv')} className="fld !w-auto" aria-label={t('recognition.exportFormat')}>
                    <option value="text">{t('recognition.formatText')}</option>
                    <option value="csv">{t('recognition.formatCsv')}</option>
                    <option value="json">{t('recognition.formatJson')}</option>
                  </select>
                  <button type="button" className="btn" disabled={isActive(run)} onClick={() => void download('raw')}>{t('recognition.rawExport')}</button>
                  <button type="button" className="btn" disabled={isActive(run)} onClick={() => void download('reviewed')}>{t('recognition.reviewedExport')}</button>
                </span>
              </div>
              {!isActive(run) && !inference.enabled && <Notice tone="ok">{t('recognition.computeOff')}</Notice>}
              {!isActive(run) && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {(run.failed_pages > 0 || run.state === 'cancelled') && (
                    <button type="button" className="btn btn-primary" onClick={() => void retryFailed()}>{t('recognition.retryFailed')}</button>
                  )}
                  {/* Spegnere il modello ha senso solo se questa elaborazione
                      l'ha usato: dopo un OCR locale non c'è niente da fermare. */}
                  {run.engine === 'model' && inference.enabled && canManageInference && <button type="button" className="btn" onClick={() => void disable()}>{t('recognition.disableNow')}</button>}
                </div>
              )}
            </Module>

            <Module
              tab={t('recognition.runPages')}
              quiet
              flush
              aux={
                <div role="group" aria-label={t('recognition.filterLabel')} className="flex flex-wrap gap-1">
                  {(['all', 'drafts', 'empty', 'failed'] as const)
                    .filter((key) => key === 'all' || counts[key] > 0)
                    .map((key) => (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={pageFilter === key}
                        onClick={() => setPageFilter(key)}
                        className={`btn btn-sm ${pageFilter === key ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`}
                      >
                        {t(`recognition.filter.${key}`, { n: counts[key] })}
                      </button>
                    ))}
                </div>
              }
            >
              {counts.empty > 0 && pageFilter !== 'failed' && (
                <p className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-warn-wash)] px-3 py-2 text-[12px] text-[color:var(--color-warn)]">
                  {t('recognition.emptyHint', { n: counts.empty })}
                </p>
              )}
              {shownItems.length === 0 && <p className="p-3 text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.noneInFilter')}</p>}
              <ul className="divide-y divide-[color:var(--color-rule)]">
                {shownItems.map((item) => (
                  <li key={item.id} className="grid gap-2 p-2 sm:grid-cols-[56px_minmax(0,1fr)_auto] sm:items-center">
                    <img src={`/api/pages/${item.page_id}/thumbnail`} alt="" className="h-16 w-12 border border-[color:var(--color-rule)] bg-white object-cover object-top" />
                    <div className="min-w-0">
                      <div className="mono truncate text-[12px] font-semibold" title={pageLabel(item)}>{pageLabel(item)}</div>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {isEmptyResult(item) ? (
                          <Badge tone="warn">{t('recognition.emptyResult')}</Badge>
                        ) : (
                          <>
                            <Badge tone={item.state === 'finished' ? 'ok' : item.state === 'failed' ? 'sig' : 'progress'}>{t(stateKey(item.state))}</Badge>
                            <Badge>{t('recognition.blocks', { n: item.blocks })}</Badge>
                          </>
                        )}
                        {item.drafts > 0 && <Badge tone="warn">{t('recognition.drafts', { n: item.drafts })}</Badge>}
                      </div>
                      {item.error && <p className="mt-1 text-[11px] text-[color:var(--color-sig-text)]">{item.error}</p>}
                    </div>
                    <Link to={`/annotazione?project=${run.project_id}&page=${item.page_id}&run=${run.id}`} className="btn no-underline">{t('recognition.review')}</Link>
                  </li>
                ))}
              </ul>
            </Module>
          </div>
        )}
      </div>
    </div>
  )
}
