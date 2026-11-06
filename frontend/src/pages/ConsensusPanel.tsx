/**
 * Model comparison on the Recognition page: the saved models (compute profiles),
 * saving the model chosen in Models as one more, and a comparison run over the
 * selected pages. The run never writes annotations; the Studio shows its result.
 * Once it has ended, one model's reading can become the drafts of the run's pages
 * (*Use as the base*), so the comparison doubles as the recognition.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiDelete, apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { useConfirm } from '../app/confirm'
import { Badge, ErrorNotice, Module } from '../app/ui'
import { projectPath } from '../app/projectPaths'
import type { AdoptResult } from '../studio/consensus'

export interface SavedModel {
  id: number
  name: string
  model_adapter_id: string
  served_model_name: string
  has_credential: boolean
}

export interface ConsensusRun {
  id: number
  project_id: number
  state: 'queued' | 'running' | 'finished' | 'finished_with_errors' | 'failed' | 'cancelled'
  models: { key: string; profile_id: number; name: string }[]
  total: number
  succeeded: number
  failed: number
  pending: number
  error: string | null
}

/** One page of a run (`GET …/consensus-runs/{id}/pages`). */
export interface ConsensusRunPage {
  page_id: number
  name: string
  states: Record<string, 'queued' | 'running' | 'finished' | 'failed' | 'cancelled'>
  errors: Record<string, string>
  /** Null until two models have read the page. */
  agreement: number | null
  open: { digits: number; text: number; missing: number } | null
}

const ACTIVE = new Set(['queued', 'running'])

const STATE_MARK: Record<string, string> = { queued: '·', running: '…', finished: '✓', failed: '✕', cancelled: '–' }

export default function ConsensusPanel({
  projectId,
  pageIds,
  canManage,
  currentModel,
  onProfilesChange,
}: {
  projectId: number | ''
  pageIds: number[]
  /** Saving or deleting a model touches keys: administrators only. */
  canManage: boolean
  /** Display name of the model chosen in Models, proposed as the saved name. */
  currentModel: string
  /** The saved models after a load, a save or a delete (the Recognition engines). */
  onProfilesChange?: (items: SavedModel[]) => void
}) {
  const { t, tn } = useI18n()
  const confirm = useConfirm()
  const [profiles, setProfiles] = useState<SavedModel[]>([])
  const [chosen, setChosen] = useState<Set<number>>(new Set())
  const [run, setRun] = useState<ConsensusRun | null>(null)
  const [runPages, setRunPages] = useState<ConsensusRunPage[]>([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [base, setBase] = useState('')
  const [adopted, setAdopted] = useState<{ written: number; human: number; missing: number } | null>(null)
  const [error, setError] = useState<unknown>(null)

  const loadProfiles = useCallback(async () => {
    const out = await apiGet<{ items: SavedModel[] }>('/consensus/profiles')
    setProfiles(out.items)
    onProfilesChange?.(out.items)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { void loadProfiles().catch(setError) }, [loadProfiles])
  useEffect(() => {
    if (projectId === '') return
    void apiGet<{ items: ConsensusRun[] }>(`/projects/${projectId}/consensus-runs`)
      .then((out) => setRun(out.items[0] ?? null))
      .catch(setError)
  }, [projectId])

  // Follow an active run until it ends.
  useEffect(() => {
    if (!run || !ACTIVE.has(run.state)) return
    const timer = window.setInterval(() => {
      void apiGet<ConsensusRun>(`/projects/${run.project_id}/consensus-runs/${run.id}`).then(setRun).catch(setError)
    }, 2000)
    return () => window.clearInterval(timer)
  }, [run])

  // Page by page: which model has read it, and once two have, how far they agree
  // and what is left to look at. Refreshed with the run while it is going.
  const runKey = run ? `${run.id}:${run.state}:${run.succeeded}:${run.failed}` : ''
  useEffect(() => {
    if (!run) { setRunPages([]); return }
    let stale = false
    void apiGet<{ items: ConsensusRunPage[] }>(`/projects/${run.project_id}/consensus-runs/${run.id}/pages`)
      .then((out) => { if (!stale) setRunPages(out.items ?? []) })
      .catch(() => { if (!stale) setRunPages([]) })
    return () => { stale = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey])

  const save = async () => {
    setBusy(true)
    try {
      const saved = await apiPost<SavedModel>('/consensus/profiles', { name: (name || currentModel).trim() })
      setName('')
      await loadProfiles()
      setChosen((prev) => new Set(prev).add(saved.id))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (profile: SavedModel) => {
    if (!await confirm({ title: t('consensus.deleteTitle'), message: t('consensus.deleteConfirm', { name: profile.name }), acceptLabel: t('consensus.delete') })) return
    try {
      await apiDelete(`/consensus/profiles/${profile.id}`)
      setChosen((prev) => { const next = new Set(prev); next.delete(profile.id); return next })
      await loadProfiles()
    } catch (e) {
      setError(e)
    }
  }

  const start = async () => {
    if (projectId === '') return
    setBusy(true)
    try {
      setRun(await apiPost<ConsensusRun>(`/projects/${projectId}/consensus-runs`, {
        page_ids: pageIds,
        profile_ids: [...chosen],
      }))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const retry = async () => {
    if (!run) return
    try {
      setRun(await apiPost<ConsensusRun>(`/projects/${run.project_id}/consensus-runs/${run.id}/retry`))
    } catch (e) {
      setError(e)
    }
  }

  const cancel = async () => {
    if (!run) return
    try {
      setRun(await apiPost<ConsensusRun>(`/projects/${run.project_id}/consensus-runs/${run.id}/cancel`))
    } catch (e) {
      setError(e)
    }
  }

  const adopt = async () => {
    if (!run) return
    const model = run.models.find((m) => m.key === (base || run.models[0]?.key))
    if (!model) return
    if (!await confirm({ title: t('consensus.adoptTitle'), message: t('consensus.adoptRunConfirm', { name: model.name }), acceptLabel: t('consensus.adoptAccept') })) return
    setBusy(true)
    try {
      const out = await apiPost<AdoptResult>(`/projects/${run.project_id}/consensus-runs/${run.id}/adopt`, { model_key: model.key })
      setAdopted({
        written: out.written.length,
        human: out.skipped.filter((item) => item.reason === 'human_work').length,
        missing: out.skipped.filter((item) => item.reason === 'no_result').length,
      })
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  // Where «Check in the Studio» goes: the first compared page with cells still open,
  // else the first compared page, else the first page of the run.
  const compared = runPages.filter((page) => page.agreement != null)
  const openCount = (page: ConsensusRunPage) => page.open ? page.open.digits + page.open.text + page.open.missing : 0
  const target = compared.find((page) => openCount(page) > 0) ?? compared[0] ?? null
  const targetId = target?.page_id ?? pageIds[0]

  const canStart = chosen.size >= 2 && pageIds.length > 0 && projectId !== '' && !busy && !(run && ACTIVE.has(run.state))
  const tone = run?.state === 'finished' ? 'ok' : run && (run.state === 'failed' || run.state === 'finished_with_errors') ? 'warn' : 'neutral'

  return (
    <Module tab={t('consensus.panelTitle')}>
      <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('consensus.panelHint')}</p>
      {error != null && <div className="mt-2"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}

      <div className="mt-3 lbl">{t('consensus.profiles')}</div>
      {profiles.length === 0 ? (
        <p className="text-[12px] text-[color:var(--color-ink-3)]">{t('consensus.noProfiles')}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {profiles.map((profile) => (
            <li key={profile.id} className="flex items-center gap-2 text-[12px]">
              <label className="flex min-w-0 flex-1 items-center gap-2">
                <input
                  type="checkbox"
                  checked={chosen.has(profile.id)}
                  onChange={(e) => setChosen((prev) => {
                    const next = new Set(prev)
                    if (e.target.checked) next.add(profile.id)
                    else next.delete(profile.id)
                    return next
                  })}
                />
                <span className="min-w-0 truncate font-semibold">{profile.name}</span>
                <span className="mono truncate text-[11px] text-[color:var(--color-ink-3)]">{profile.served_model_name}</span>
              </label>
              {canManage && (
                <button type="button" className="text-[11px] underline" onClick={() => void remove(profile)}>{t('consensus.delete')}</button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canManage && (
        // Saving is done once per model: kept folded, out of the way of comparing.
        <details className="mt-3 border-t border-[color:var(--color-rule)] pt-2" data-testid="save-model">
          <summary className="cursor-pointer text-[11px] font-semibold text-[color:var(--color-ink-2)]">{t('consensus.saveSection')}</summary>
          <label className="mt-2 block">
            <span className="lbl">{t('consensus.saveName')}</span>
            <input className="fld" value={name} placeholder={currentModel} onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="button" className="btn mt-2 w-full" disabled={busy || !(name || currentModel).trim()} onClick={() => void save()}>{t('consensus.saveCurrent')}</button>
          <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.saveHint')}</p>
        </details>
      )}

      <button type="button" className="btn btn-primary mt-3 w-full" disabled={!canStart} onClick={() => void start()}>
        {t('consensus.start', { n: pageIds.length })}
      </button>
      {!canStart && !(run && ACTIVE.has(run.state)) && <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.pickTwo')}</p>}

      {run && (
        <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2 text-[12px]" data-testid="consensus-run">
          <div className="flex items-center gap-2">
            <span className="mono text-[11px]">#{run.id}</span>
            <span className="min-w-0 flex-1 truncate">{run.models.map((m) => m.name).join(' · ')}</span>
            <Badge tone={tone}>{t(`consensus.state.${run.state}`)}</Badge>
          </div>
          <div className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">
            {t('consensus.progress', { done: run.succeeded + run.failed, total: run.total })}
            {run.failed > 0 && <span className="ml-2 text-[color:var(--color-sig-text)]">{t('consensus.failedCount', { n: run.failed })}</span>}
          </div>
          {runPages.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-[color:var(--color-ink-2)]" data-testid="model-progress">
              {run.models.map((m) => {
                const done = runPages.filter((page) => page.states[m.key] === 'finished').length
                const failed = runPages.filter((page) => page.states[m.key] === 'failed').length
                return (
                  <span key={m.key}>
                    {t('consensus.modelProgress', { name: m.name, done, total: runPages.length })}
                    {failed > 0 && <span className="ml-1 text-[color:var(--color-sig-text)]">{tn('consensus.modelFailedPages', failed)}</span>}
                  </span>
                )
              })}
            </div>
          )}
          {run.error && <p className="text-[11px] text-[color:var(--color-sig-text)]">{run.error}</p>}
          <div className="mt-1 flex gap-3">
            {ACTIVE.has(run.state) && <button type="button" className="text-[11px] underline" onClick={() => void cancel()}>{t('consensus.cancel')}</button>}
            {!ACTIVE.has(run.state) && run.failed > 0 && <button type="button" className="text-[11px] font-semibold underline" onClick={() => void retry()}>{t('consensus.retry')}</button>}
          </div>
          {/* Always in view: the list's file names are links too, but they do not read as buttons. */}
          {targetId != null && (
            <>
              <Link to={projectPath(run.project_id, 'studio', { consensus: 1, page: targetId })} className="btn btn-primary mt-2 block w-full text-center" data-testid="check-in-studio">
                {t('consensus.checkInStudio')}
              </Link>
              {target && (
                <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">
                  {t(openCount(target) > 0 ? 'consensus.checkInStudioHint' : 'consensus.checkInStudioDone', { name: target.name.split('/').pop() ?? target.name })}
                </p>
              )}
            </>
          )}
          {runPages.length > 0 && (
            <ul className="mt-2 max-h-[260px] space-y-1 overflow-auto" data-testid="run-pages" aria-label={t('consensus.runPages')}>
              {runPages.map((page) => {
                const left = page.open ? page.open.digits + page.open.text + page.open.missing : 0
                return (
                  <li key={page.page_id} className="flex flex-wrap items-baseline gap-x-2 border-b border-[color:var(--color-rule)] pb-1 text-[11px]">
                    {/* The file on a line of its own: the narrow column truncated it to «L…». */}
                    <Link to={projectPath(run.project_id, 'studio', { consensus: 1, page: page.page_id })} className="mono block w-full truncate font-semibold" title={page.name}>
                      {page.name.split('/').pop()}
                    </Link>
                    <span className="mono text-[color:var(--color-ink-3)]" title={run.models.map((m) => `${m.name}: ${t(`consensus.state.${page.states[m.key] ?? 'queued'}`)}`).join(' · ')}>
                      {run.models.map((m) => STATE_MARK[page.states[m.key] ?? 'queued']).join(' ')}
                    </span>
                    {page.agreement == null ? (
                      <span className="text-[color:var(--color-ink-3)]">{Object.values(page.states).some((state) => state === 'failed') ? t('consensus.pageFailed') : t('consensus.pageWaiting')}</span>
                    ) : (
                      <>
                        <span className="font-semibold">{t('consensus.agreement', { pct: Math.round(page.agreement * 100) })}</span>
                        <span className={left > 0 ? 'text-[color:var(--color-sig-text)]' : 'text-[color:var(--color-ok)]'}>
                          {left > 0
                            ? t('consensus.pageLeft', { digits: page.open!.digits, text: page.open!.text, missing: page.open!.missing })
                            : t('consensus.allSettled')}
                        </span>
                      </>
                    )}
                    <Link to={projectPath(run.project_id, 'studio', { consensus: 1, page: page.page_id })} className="ml-auto font-semibold">{t('consensus.openPage')}</Link>
                    {Object.entries(page.errors).map(([key, message]) => (
                      <span key={key} className="w-full text-[color:var(--color-sig-text)]">{`${run.models.find((m) => m.key === key)?.name ?? key}: ${message}`}</span>
                    ))}
                  </li>
                )
              })}
            </ul>
          )}
          {/* Usable while the run goes on: only the pages this model has already read are written. */}
          {run.succeeded > 0 && (
            <div className="mt-2 border-t border-[color:var(--color-rule)] pt-2" data-testid="adopt-run">
              <label className="block">
                <span className="lbl">{t('consensus.adoptRunLabel')}</span>
                <select className="fld" value={base || run.models[0]?.key} onChange={(e) => { setBase(e.target.value); setAdopted(null) }}>
                  {run.models.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
                </select>
              </label>
              <button type="button" className="btn mt-2 w-full" disabled={busy} onClick={() => void adopt()}>{t('consensus.adoptRun')}</button>
              <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.adoptRunHint')}</p>
              {ACTIVE.has(run.state) && <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">{t('consensus.adoptWhileRunning')}</p>}
              {adopted && (
                <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]" data-testid="adopt-result">
                  {tn('consensus.adoptWritten', adopted.written)}
                  {adopted.human > 0 && ` · ${tn('consensus.adoptSkippedHuman', adopted.human)}`}
                  {adopted.missing > 0 && ` · ${tn('consensus.adoptSkippedMissing', adopted.missing)}`}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </Module>
  )
}
