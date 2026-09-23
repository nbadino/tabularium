import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import { ApiError } from '../lib/api'
import type { PageItem, PrefillEngines, RecognitionRun, SystemInfo } from '../lib/types'
import { Badge, ErrorNotice, Field, Modal, Module, Notice, Progress } from '../app/ui'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useInference } from '../app/inference'
import { useCanAdminister } from '../app/auth'
import { IconArchive, IconPlayground } from '../app/icons'
import { useI18n } from '../i18n'
import { pageLabel, pageShortLabel } from '../lib/pageLabel'
import { runTitle, useModelNames } from '../app/models/names'

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

function runProvider(run: RecognitionRun): string {
  return run.engine === 'ocr' ? 'local' : run.provider
}


const INITIAL_PAGE_LIMIT = 100

interface ModelRuntimeInfo {
  adapter_id: string
  vram_warning: string | null
  local_serve_blocker?: string | null
}

function RunSummary({ run, onCancel }: { run: RecognitionRun; onCancel: () => void }) {
  const { t } = useI18n()
  const nameOf = useModelNames()
  const active = run.state === 'queued' || run.state === 'running'
  const pct = run.total_pages ? (run.completed_pages / run.total_pages) * 100 : 0
  const provider = runProvider(run)
  const providerKey = ['local', 'ssh', 'vast', 'runpod', 'modal', 'custom'].includes(provider)
    ? `recognition.provider.${provider}`
    : null
  return (
    <Module tab={active ? t('recognition.current') : t('recognition.recent')} aux={<Badge tone={runTone(run)}>{runLabel(run, t)}</Badge>}>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <strong className="text-[16px]">{runTitle(run, nameOf)}</strong>
        <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
          {providerKey ? t(providerKey) : provider} · #{run.id}
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
  const nameOf = useModelNames()
  const inference = useInference()
  const canManageInference = useCanAdminister()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [pages, setPages] = useState<PageItem[]>([])
  const [pageQuery, setPageQuery] = useState('')
  const [pageLimit, setPageLimit] = useState(INITIAL_PAGE_LIMIT)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [runs, setRuns] = useState<RecognitionRun[]>([])
  const [modelRuntime, setModelRuntime] = useState<ModelRuntimeInfo[]>([])
  const [current, setCurrent] = useState<RecognitionRun | null>(null)
  const [engine, setEngine] = useState<'model' | 'ocr'>('model')
  const [disableAfter, setDisableAfter] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [backendRestartRequired, setBackendRestartRequired] = useState(false)
  const [engines, setEngines] = useState<PrefillEngines | null>(null)
  const [caps, setCaps] = useState<SystemInfo['capabilities'] | null>(null)
  const [engineTouched, setEngineTouched] = useState(false)

  useEffect(() => {
    void Promise.all([
      apiGet<{ items: ModelRuntimeInfo[] }>('/models').then((out) => setModelRuntime(out.items)),
      apiGet<PrefillEngines>('/system/prefill-engines').then(setEngines),
      apiGet<SystemInfo>('/system/info').then((info) => setCaps(info.capabilities ?? null)),
    ]).catch(() => {})
  }, [])

  // Il motore di default lo decide il backend, non questa pagina: su
  // un'installazione senza GPU «modello» è una voce disabilitata, e trovarla
  // già selezionata manda l'utente dritto in un vicolo cieco. La scelta
  // esplicita dell'utente vince sempre su quella suggerita.
  useEffect(() => {
    if (engineTouched || !engines?.recommended) return
    setEngine(engines.recommended)
  }, [engines?.recommended, engineTouched])

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
    setPageQuery('')
    setPageLimit(INITIAL_PAGE_LIMIT)
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
          // Bozze e blocchi delle pagine sono cambiati: le miniature e la
          // domanda «sostituire le bozze?» devono vedere i numeri nuovi.
          const refreshed = await apiGet<{ items: PageItem[] }>(`/projects/${next.project_id}/pages`)
          if (!stopped) setPages(refreshed.items)
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

  const visiblePages = useMemo(() => {
    const query = pageQuery.trim().toLocaleLowerCase()
    if (!query) return pages
    return pages.filter((page) => pageLabel(page).toLocaleLowerCase().includes(query))
  }, [pages, pageQuery])
  const renderedPages = visiblePages.slice(0, pageLimit)
  const unrecognized = useMemo(() => pages.filter((page) => (page.blocks ?? 0) === 0), [pages])
  const runtimeWarning = modelRuntime.find((item) => item.adapter_id === inference.adapterId)
  // «Pronto» = raggiungibile davvero (ping del backend), non solo attivato:
  // un endpoint configurato ma giù non deve sembrare operativo.
  // «Pronto» dell'OCR non è un'assunzione: se il motore non è installato nel
  // backend l'elaborazione muore con un messaggio di pip. Finché il backend
  // non risponde restiamo sul comportamento precedente, per non gridare al lupo.
  const ocrReady = engines ? engines.ocr.available : true
  const modelReady = engine === 'ocr' ? ocrReady : inference.enabled && inference.available
  // Su una macchina senza CUDA locale «l'endpoint non risponde» è il sintomo,
  // non la causa: mostrarli entrambi manda a controllare un endpoint che non
  // potrà mai rispondere. Vince la causa, che dice anche come uscirne.
  const noLocalGpu = engine === 'model' && !inference.isCloud && caps?.local_cuda === false
  // La macchina può servire *qualcosa* in locale? Su Apple Silicon sì, via
  // MLX — ma non quel modello, se non ha un checkpoint MLX. Distinguere le due
  // cose evita di dire «questa macchina non può» quando può, solo con un
  // altro modello.
  const localRuntimeAvailable = (caps?.local_compute?.usable_runtimes?.length ?? 0) > 0
  // Shift+clic seleziona l'intervallo dall'ultima pagina toccata, come in
  // qualsiasi elenco di file: su 361 pagine, una alla volta non è un gesto.
  const lastPicked = useRef<number | null>(null)
  const shiftHeld = useRef(false)
  const toggle = (id: number) => {
    const anchor = lastPicked.current
    const range = shiftHeld.current && anchor != null && anchor !== id
    lastPicked.current = id
    setSelected((before) => {
      const next = new Set(before)
      if (range) {
        const ids = visiblePages.map((page) => page.id)
        const from = ids.indexOf(anchor)
        const to = ids.indexOf(id)
        if (from >= 0 && to >= 0) {
          const on = !before.has(id)
          for (const pageId of ids.slice(Math.min(from, to), Math.max(from, to) + 1)) {
            if (on) next.add(pageId)
            else next.delete(pageId)
          }
          return next
        }
      }
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Le pagine scelte che hanno già bozze: una sostituzione le cancella, e con
  // loro le correzioni fatte senza spuntare «Verificato» (restano bozze).
  // Si chiede prima, invece di sostituire in silenzio.
  const [askReplace, setAskReplace] = useState<{ pages: number; drafts: number } | null>(null)
  const onStartClick = () => {
    if (projectId === '' || selected.size === 0) return
    const withDrafts = pages.filter((page) => selected.has(page.id) && (page.drafts ?? 0) > 0)
    if (withDrafts.length > 0) {
      setAskReplace({ pages: withDrafts.length, drafts: withDrafts.reduce((sum, page) => sum + (page.drafts ?? 0), 0) })
      return
    }
    void start('replace_drafts')
  }

  const start = async (mode: 'replace_drafts' | 'merge') => {
    setAskReplace(null)
    if (projectId === '' || selected.size === 0) return
    setBusy(true)
    setError(null)
    try {
      const run = await apiPost<RecognitionRun>(`/projects/${projectId}/recognition-runs`, {
        page_ids: [...selected],
        engine,
        mode,
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
        <Module tab={t('recognition.pages')} flush aux={<span>{t('recognition.selected', { n: selected.size })} <span className="text-[color:var(--color-ink-3)]">· {t('recognition.rangeHint')}</span></span>}>
          <div className="flex flex-wrap items-end gap-3 border-b border-[color:var(--color-rule)] p-3">
            <div className="min-w-56 flex-1">
              <Field label={t('recognition.project')}>
                <select value={projectId} onChange={(e) => void onProject(e.target.value === '' ? '' : Number(e.target.value))} className="fld">
                  <option value="">{t('common.chooseProject')}</option>
                  {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                </select>
              </Field>
            </div>
            <div className="min-w-56 flex-1">
              <Field label={t('recognition.filterPages')}>
                <input
                  value={pageQuery}
                  onChange={(e) => setPageQuery(e.target.value)}
                  className="fld"
                  placeholder={t('recognition.filterPagesHint')}
                />
              </Field>
            </div>
            <button type="button" className="btn" disabled={visiblePages.length === 0} onClick={() => setSelected((before) => new Set([...before, ...visiblePages.map((page) => page.id)]))}>{t('recognition.selectAll')}</button>
            {/* La scelta più frequente su un corpus già in parte riconosciuto:
                le pagine che non hanno ancora nessun blocco. */}
            {unrecognized.length > 0 && unrecognized.length < pages.length && (
              <button type="button" className="btn" onClick={() => setSelected(new Set(unrecognized.map((page) => page.id)))}>
                {t('recognition.selectUnrecognized', { n: unrecognized.length })}
              </button>
            )}
            <button type="button" className="btn" disabled={selected.size === 0} onClick={() => setSelected(new Set())}>{t('recognition.clearSelection')}</button>
          </div>
          {pages.length === 0 ? (
            <div className="flex flex-col items-start gap-2 p-6">
              <IconArchive size={22} />
              <p className="text-[13px] font-semibold">
                {projects.length === 0
                  ? t('recognition.emptyNoArchive')
                  : projectId === ''
                    ? t('recognition.emptyChooseProject')
                    : t('recognition.emptyPages')}
              </p>
              <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">
                {projects.length === 0
                  ? t('recognition.emptyNoArchiveBody')
                  : projectId === ''
                    ? t('recognition.emptyChooseProjectBody')
                    : t('recognition.emptyPagesBody')}
              </p>
              {projects.length === 0 ? (
                <Link to="/progetti" className="btn btn-primary no-underline">
                  {t('recognition.emptyCreateArchive')}
                </Link>
              ) : projectId !== '' ? (
                <Link to={`/progetti/${projectId}`} className="btn no-underline">
                  {t('recognition.emptyOpenProject')}
                </Link>
              ) : null}
            </div>
          ) : visiblePages.length === 0 ? (
            <div className="p-6 text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.noFilteredPages')}</div>
          ) : (
            <>
              <ul className="grid grid-cols-2 border-l border-t border-[color:var(--color-rule)] sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7">
                {renderedPages.map((page) => {
                  const checked = selected.has(page.id)
                  return (
                    <li key={page.id} className="border-b border-r border-[color:var(--color-rule)]">
                      <label className={`block cursor-pointer ${checked ? 'bg-[color:var(--color-sig-wash)] outline outline-2 -outline-offset-2 outline-[color:var(--color-sig)]' : 'hover:bg-[color:var(--color-fill)]'}`}>
                        <img src={`/api/pages/${page.id}/thumbnail`} alt="" loading="lazy" className="aspect-[3/4] w-full bg-white object-cover object-top" />
                        <span className="flex items-center gap-2 border-t border-[color:var(--color-rule)] p-1.5">
                          <input
                            type="checkbox"
                            checked={checked}
                            onClick={(e) => {
                              shiftHeld.current = e.shiftKey
                            }}
                            onChange={() => toggle(page.id)}
                          />
                          <span className="mono min-w-0 flex-1 truncate text-[11px]" title={pageLabel(page)}>{pageShortLabel(page)}</span>
                          {(page.drafts ?? 0) > 0 && <Badge tone="warn">{t('home.draftsBadge', { n: page.drafts ?? 0 })}</Badge>}
                        </span>
                      </label>
                    </li>
                  )
                })}
              </ul>
              {renderedPages.length < visiblePages.length && (
                <div className="flex items-center justify-center border-x border-b border-[color:var(--color-rule)] p-2">
                  <button type="button" className="btn btn-sm" onClick={() => setPageLimit((before) => before + INITIAL_PAGE_LIMIT)}>
                    {t('recognition.showMorePages', { n: visiblePages.length - renderedPages.length })}
                  </button>
                </div>
              )}
            </>
          )}
        </Module>

        <div className="space-y-3">
          <Module tab={t('recognition.newRun')}>
            <Field label={t('recognition.engine')}>
              <select
                value={engine}
                onChange={(e) => {
                  setEngineTouched(true)
                  setEngine(e.target.value as 'model' | 'ocr')
                }}
                className="fld"
              >
                <option value="model" disabled={!inference.enabled || !inference.available}>{t('recognition.servedModel')}</option>
                <option value="ocr">{t('recognition.localOcr')}</option>
              </select>
            </Field>
            <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">
              {t(engine === 'model' ? 'recognition.servedModelHint' : 'recognition.localOcrHint')}
            </p>
            {engine === 'model' && !inference.isCloud && runtimeWarning?.vram_warning && (
              <Notice tone="warn">{runtimeWarning.vram_warning}</Notice>
            )}
            {engine === 'model' && !inference.isCloud && runtimeWarning?.local_serve_blocker && (
              <Notice tone="sig">{runtimeWarning.local_serve_blocker}</Notice>
            )}
            {engine === 'model' && inference.enabled && !inference.available && !noLocalGpu && (
              <Notice tone="warn">{t('recognition.unreachableNotice', { url: inference.url })}</Notice>
            )}
            {engine === 'ocr' && engines && !engines.ocr.available && (
              <Notice tone="sig">{t('recognition.ocrMissingNotice')}</Notice>
            )}
            {noLocalGpu && (
              <Notice tone="warn">
                {localRuntimeAvailable ? (
                  <>
                    <b className="font-semibold">{t('recognition.modelNotLocalHere')}</b>{' '}
                    {t('recognition.modelNotLocalHereBody')}
                  </>
                ) : (
                  <>
                    <b className="font-semibold">{t('recognition.noLocalGpu')}</b>{' '}
                    {caps?.cuda_note === 'WSL2'
                      ? t('recognition.noLocalGpuWslBody')
                      : t('recognition.noLocalGpuBody')}
                  </>
                )}
              </Notice>
            )}
            <div className="mt-3 border-y border-[color:var(--color-rule)] py-2">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[13px] font-semibold">{engine === 'model' ? inference.model || '—' : t('recognition.localOcr')}</div>
                  <div className="mono truncate text-[11px] text-[color:var(--color-ink-3)]">
                    {engine === 'model'
                      ? `${t(inference.isCloud ? 'recognition.locationCloud' : 'recognition.locationLocal')} · ${inference.provider ? t(`recognition.provider.${inference.provider}`) : ''}`
                      : t('recognition.locationCpu')}
                  </div>
                </div>
                <Badge tone={modelReady ? 'ok' : 'warn'}>
                  {engine === 'ocr'
                    ? ocrReady
                      ? t('recognition.modelReady')
                      : t('recognition.ocrMissing')
                    : !inference.enabled
                      ? t('recognition.modelOff')
                      : modelReady
                        ? t('recognition.modelReady')
                        : t('recognition.modelUnreachable')}
                </Badge>
              </div>
              <Link to="/modelli" className="mt-2 inline-block text-[11px] font-semibold no-underline">
                {inference.enabled ? t('recognition.changeModel') : t('recognition.chooseModel')}
              </Link>
            </div>
            {engine === 'model' && canManageInference && (
              <label className="mt-3 flex items-start gap-2 text-[12px]">
                <input type="checkbox" checked={disableAfter} onChange={(e) => setDisableAfter(e.target.checked)} />
                <span><b className="font-semibold">{t('recognition.stopAfter')}</b><span className="mt-0.5 block text-[11px] text-[color:var(--color-ink-3)]">{t('recognition.stopAfterHint')}</span></span>
              </label>
            )}
            <button type="button" onClick={onStartClick} disabled={backendRestartRequired || busy || projectId === '' || selected.size === 0 || !modelReady} className="btn btn-primary mt-4 w-full">
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
                      <span className="min-w-0 flex-1 truncate">{runTitle(run, nameOf)}</span>
                      <Badge tone={runTone(run)}>{runLabel(run, t)}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Module>
        </div>
      </div>
      {askReplace && (
        <Modal
          title={t('recognition.replace.title')}
          onClose={() => setAskReplace(null)}
          footer={
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="btn" onClick={() => setAskReplace(null)}>{t('common.cancel')}</button>
              <button type="button" className="btn" onClick={() => void start('merge')}>{t('recognition.replace.merge')}</button>
              <button type="button" className="btn btn-primary" onClick={() => void start('replace_drafts')}>{t('recognition.replace.replace')}</button>
            </div>
          }
        >
          <div className="space-y-2 p-3 text-[13px]">
            <p>{t('recognition.replace.body', { pages: askReplace.pages, drafts: askReplace.drafts })}</p>
            <p className="text-[12px] text-[color:var(--color-warn)]">{t('recognition.replace.warning')}</p>
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.replace.mergeHint')}</p>
          </div>
        </Modal>
      )}
    </div>
  )
}
