import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import { ApiError } from '../lib/api'
import type { ComputeProfile, PageItem, RecognitionRun } from '../lib/types'
import { Badge, ErrorNotice, Field, Module, Notice, Progress } from '../app/ui'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { syncInferenceFromBackend, useInference } from '../app/inference'
import { useAuth } from '../app/auth'
import { IconPlayground } from '../app/icons'
import { useI18n } from '../i18n'

function runLabel(run: RecognitionRun, t: (key: string) => string): string {
  const key = {
    queued: 'recognition.statusQueued',
    running: 'recognition.statusRunning',
    finished: 'recognition.statusFinished',
    finished_with_errors: 'recognition.statusFinishedWithErrors',
    failed: 'recognition.statusFailed',
    cancelled: 'recognition.statusCancelled',
  }[run.state]
  return t(key)
}

function runTone(run: RecognitionRun): 'neutral' | 'progress' | 'ok' | 'warn' | 'sig' {
  if (run.state === 'finished') return 'ok'
  if (run.state === 'finished_with_errors') return 'warn'
  if (run.state === 'failed' || run.state === 'cancelled') return 'sig'
  return 'progress'
}

interface ModelRuntimeInfo {
  adapter_id: string
  vram_warning: string | null
  local_serve_blocker?: string | null
}

function RunSummary({ run, onCancel }: { run: RecognitionRun; onCancel: () => void }) {
  const { t } = useI18n()
  const active = run.state === 'queued' || run.state === 'running'
  const pct = run.total_pages ? (run.completed_pages / run.total_pages) * 100 : 0
  return (
    <Module tab={active ? t('recognition.current') : t('recognition.recent')} aux={<Badge tone={runTone(run)}>{runLabel(run, t)}</Badge>}>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <strong className="text-[16px]">{run.model_name || run.engine}</strong>
        <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
          {run.provider} · #{run.id}
        </span>
      </div>
      <div className="mt-3">
        <Progress value={pct} label={t('recognition.progress', { done: run.completed_pages, total: run.total_pages })} />
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-[color:var(--color-ink-2)]">
          <span>{t('recognition.progress', { done: run.completed_pages, total: run.total_pages })}</span>
          <span>{t('recognition.completed', { ok: run.succeeded_pages })}</span>
          {run.failed_pages > 0 && <span>{t('recognition.failed', { n: run.failed_pages })}</span>}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {run.items?.[0] && (
          <Link
            to={`/annotazione?project=${run.project_id}&page=${run.items[0].page_id}&run=${run.id}`}
            className="btn btn-primary no-underline"
          >
            {t('recognition.openStudio')}
          </Link>
        )}
        <Link to={`/risultati?project=${run.project_id}&run=${run.id}`} className="btn btn-primary no-underline">
          {t('recognition.openResults')}
        </Link>
        {active && (
          <button type="button" className="btn btn-danger" onClick={onCancel}>
            {t('recognition.cancel')}
          </button>
        )}
      </div>
    </Module>
  )
}

export default function RecognizePage() {
  const { t } = useI18n()
  const inference = useInference()
  const auth = useAuth()
  const canManageInference = !auth.enabled || auth.user?.role === 'admin'
  const [projectId, setProjectId] = useState<number | ''>('')
  const [pages, setPages] = useState<PageItem[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [runs, setRuns] = useState<RecognitionRun[]>([])
  const [profiles, setProfiles] = useState<ComputeProfile[]>([])
  const [modelRuntime, setModelRuntime] = useState<ModelRuntimeInfo[]>([])
  const [current, setCurrent] = useState<RecognitionRun | null>(null)
  const [engine, setEngine] = useState<'model' | 'ocr'>('model')
  const [disableAfter, setDisableAfter] = useState(true)
  const [busy, setBusy] = useState(false)
  const [profileBusy, setProfileBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [backendRestartRequired, setBackendRestartRequired] = useState(false)

  useEffect(() => {
    void Promise.all([
      apiGet<ComputeProfile[]>('/system/compute-profiles').then(setProfiles),
      apiGet<{ items: ModelRuntimeInfo[] }>('/models').then((out) => setModelRuntime(out.items)),
    ]).catch(() => {})
  }, [])

  const activateProfile = async (profileId: number) => {
    const profile = profiles.find((item) => item.id === profileId)
    if (!profile || profile.active || !canManageInference) return
    setProfileBusy(true)
    setError(null)
    try {
      const active = await apiPost<ComputeProfile>(`/system/compute-profiles/${profile.id}/activate`)
      setProfiles((before) => before.map((item) => ({ ...item, active: item.id === active.id })))
      await syncInferenceFromBackend()
    } catch (e) {
      setError(e)
    } finally {
      setProfileBusy(false)
    }
  }

  const loadRuns = async (pid: number) => {
    try {
      const out = await apiGet<{ items: RecognitionRun[] }>(`/recognition-runs?project_id=${pid}`)
      setBackendRestartRequired(false)
      setRuns(out.items)
      const active = out.items.find((run) => run.state === 'queued' || run.state === 'running')
      if (active) setCurrent(await apiGet<RecognitionRun>(`/projects/${pid}/recognition-runs/${active.id}`))
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // Il frontend built può essere già aggiornato mentre un processo
        // FastAPI avviato prima della build conserva ancora le vecchie route.
        // L'archivio resta utilizzabile; serve solo riavviare quel processo.
        setBackendRestartRequired(true)
        setRuns([])
        return
      }
      throw e
    }
  }

  const onProject = async (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setPages([])
    setRuns([])
    setCurrent(null)
    setSelected(new Set())
    if (pid === '') return
    try {
      // Le pagine sono il contenuto primario: un problema nel solo storico
      // delle run non deve far apparire vuoto un archivio che non lo è.
      const pageOut = await apiGet<{ items: PageItem[] }>(`/projects/${pid}/pages`)
      setPages(pageOut.items)
      await loadRuns(pid)
    } catch (e) {
      setError(e)
    }
  }

  const projects = useProjects((pid) => void onProject(pid), setError)

  useEffect(() => {
    if (!current || (current.state !== 'queued' && current.state !== 'running')) return
    let stopped = false
    const tick = async () => {
      try {
        const next = await apiGet<RecognitionRun>(`/projects/${current.project_id}/recognition-runs/${current.id}`)
        if (stopped) return
        setCurrent(next)
        if (next.state !== 'queued' && next.state !== 'running') {
          await loadRuns(next.project_id)
        }
      } catch (e) {
        if (!stopped) setError(e)
      }
    }
    const id = setInterval(() => void tick(), 2000)
    return () => {
      stopped = true
      clearInterval(id)
    }
  }, [current?.id, current?.state])

  const visiblePages = useMemo(() => pages, [pages])
  const runtimeWarning = modelRuntime.find((item) => item.adapter_id === inference.adapterId)
  // «Pronto» = raggiungibile davvero (ping del backend), non solo attivato:
  // un endpoint configurato ma giù non deve sembrare operativo.
  const modelReady = engine === 'ocr' || (inference.enabled && inference.available)
  const toggle = (id: number) => setSelected((before) => {
    const next = new Set(before)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const start = async () => {
    if (projectId === '' || selected.size === 0) return
    setBusy(true)
    setError(null)
    try {
      const run = await apiPost<RecognitionRun>(`/projects/${projectId}/recognition-runs`, {
        page_ids: [...selected],
        engine,
        mode: 'replace_drafts',
        model_mode: 'native',
        stop_policy: canManageInference && disableAfter && engine === 'model' ? 'disable_inference' : 'none',
      })
      setCurrent(run)
      setRuns((before) => [run, ...before.filter((item) => item.id !== run.id)])
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!current) return
    try {
      setCurrent(await apiPost<RecognitionRun>(`/projects/${current.project_id}/recognition-runs/${current.id}/cancel`))
    } catch (e) {
      setError(e)
    }
  }

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">{t('recognition.title')}</h1>
        <p className="mt-1 max-w-[72ch] text-[13px] text-[color:var(--color-ink-2)]">{t('recognition.intro')}</p>
      </div>

      {error != null && <div className="mb-3"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}
      {backendRestartRequired && (
        <div className="mb-3">
          <Notice tone="warn">
            <span>{t('recognition.backendRestartRequired')}</span>{' '}
            <button type="button" className="font-semibold underline underline-offset-2" onClick={() => window.location.reload()}>
              {t('recognition.reloadAfterRestart')}
            </button>
          </Notice>
        </div>
      )}
      {current && <div className="mb-3"><RunSummary run={current} onCancel={() => void cancel()} /></div>}

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Module tab={t('recognition.pages')} flush aux={<span>{t('recognition.selected', { n: selected.size })}</span>}>
          <div className="flex flex-wrap items-end gap-3 border-b border-[color:var(--color-rule)] p-3">
            <div className="min-w-56 flex-1">
              <Field label={t('recognition.project')}>
                <select value={projectId} onChange={(e) => void onProject(e.target.value === '' ? '' : Number(e.target.value))} className="fld">
                  <option value="">{t('common.chooseProject')}</option>
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
              </Field>
            </div>
            <button type="button" className="btn" disabled={pages.length === 0} onClick={() => setSelected(new Set(visiblePages.map((page) => page.id)))}>{t('recognition.selectAll')}</button>
            <button type="button" className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>{t('recognition.clearSelection')}</button>
          </div>
          {pages.length === 0 ? (
            <p className="p-4 text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.emptyPages')}</p>
          ) : (
            <ul className="grid grid-cols-2 border-l border-t border-[color:var(--color-rule)] sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7">
              {visiblePages.map((page) => {
                const checked = selected.has(page.id)
                return (
                  <li key={page.id} className="border-b border-r border-[color:var(--color-rule)]">
                    <label className={`block cursor-pointer ${checked ? 'bg-[color:var(--color-sig-wash)] outline outline-2 -outline-offset-2 outline-[color:var(--color-sig)]' : 'hover:bg-[color:var(--color-fill)]'}`}>
                      <img src={`/api/pages/${page.id}/thumbnail`} alt="" loading="lazy" className="aspect-[3/4] w-full bg-white object-cover object-top" />
                      <span className="flex items-center gap-2 border-t border-[color:var(--color-rule)] p-1.5">
                        <input type="checkbox" checked={checked} onChange={() => toggle(page.id)} />
                        <span className="mono min-w-0 truncate text-[11px]" title={page.rel_path}>{page.rel_path}</span>
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
          )}
        </Module>

        <div className="space-y-3">
          <Module tab={t('recognition.newRun')}>
            <Field label={t('recognition.engine')}>
              <select value={engine} onChange={(e) => setEngine(e.target.value as 'model' | 'ocr')} className="fld">
                <option value="model" disabled={!inference.enabled}>{t('recognition.servedModel')}</option>
                <option value="ocr">{t('recognition.localOcr')}</option>
              </select>
            </Field>
            {engine === 'model' && profiles.length > 0 && (
              <div className="mt-3">
                <Field label={t('recognition.computeProfile')}>
                  <select
                    value={profiles.find((profile) => profile.active)?.id ?? ''}
                    onChange={(e) => void activateProfile(Number(e.target.value))}
                    disabled={!canManageInference || profileBusy}
                    className="fld"
                  >
                    {profiles
                      .filter((profile) => profile.purpose !== 'training')
                      .map((profile) => (
                        <option key={profile.id} value={profile.id}>
                          {profile.name} · {profile.served_model_name} · {t(`recognition.provider.${profile.provider}`)}
                        </option>
                      ))}
                  </select>
                </Field>
                <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">
                  {profileBusy ? t('recognition.activatingProfile') : t('recognition.computeProfileHint')}
                </p>
              </div>
            )}
            {engine === 'model' && !inference.isCloud && runtimeWarning?.vram_warning && (
              <Notice tone="warn">{runtimeWarning.vram_warning}</Notice>
            )}
            {engine === 'model' && !inference.isCloud && runtimeWarning?.local_serve_blocker && (
              <Notice tone="sig">{runtimeWarning.local_serve_blocker}</Notice>
            )}
            {engine === 'model' && inference.enabled && !inference.available && (
              <Notice tone="warn">{t('recognition.unreachableNotice', { url: inference.url })}</Notice>
            )}
            <div className="mt-3 border-y border-[color:var(--color-rule)] py-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[13px] font-semibold">{engine === 'model' ? inference.model || '—' : t('recognition.localOcr')}</div>
                  <div className="mono truncate text-[11px] text-[color:var(--color-ink-3)]">
                    {engine === 'model'
                      ? t(inference.isCloud ? 'recognition.locationCloud' : 'recognition.locationLocal')
                      : t('recognition.locationCpu')}
                  </div>
                </div>
                <Badge tone={modelReady ? 'ok' : 'warn'}>
                  {engine === 'ocr'
                    ? t('recognition.modelReady')
                    : !inference.enabled
                      ? t('recognition.modelOff')
                      : modelReady
                        ? t('recognition.modelReady')
                        : t('recognition.modelUnreachable')}
                </Badge>
              </div>
              <Link to="/modelli" className="mt-2 inline-block text-[11px] font-semibold no-underline">{t('recognition.changeModel')}</Link>
            </div>
            {engine === 'model' && canManageInference && (
              <label className="mt-3 flex items-start gap-2 text-[12px]">
                <input type="checkbox" checked={disableAfter} onChange={(e) => setDisableAfter(e.target.checked)} />
                <span><b className="font-semibold">{t('recognition.stopAfter')}</b><span className="mt-0.5 block text-[11px] text-[color:var(--color-ink-3)]">{t('recognition.stopAfterHint')}</span></span>
              </label>
            )}
            <button type="button" onClick={() => void start()} disabled={backendRestartRequired || busy || projectId === '' || selected.size === 0 || !modelReady} className="btn btn-primary mt-4 w-full">
              <IconPlayground size={13} />{busy ? t('recognition.starting') : t('recognition.start')}
            </button>
          </Module>

          <Module tab={t('recognition.recent')} quiet flush>
            {runs.length === 0 ? <p className="p-3 text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.noRuns')}</p> : (
              <ul className="divide-y divide-[color:var(--color-rule)]">
                {runs.slice(0, 8).map((run) => (
                  <li key={run.id}>
                    <Link to={`/risultati?project=${run.project_id}&run=${run.id}`} className="flex items-center gap-2 p-2 text-[12px] no-underline hover:bg-[color:var(--color-fill)]">
                      <span className="mono text-[11px]">#{run.id}</span>
                      <span className="min-w-0 flex-1 truncate">{run.model_name || run.engine}</span>
                      <Badge tone={runTone(run)}>{runLabel(run, t)}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Module>
        </div>
      </div>
    </div>
  )
}
