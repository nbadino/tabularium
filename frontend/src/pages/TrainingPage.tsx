import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import type { AutoTrainingJob, GpuInfo, TrainConfigBody, TrainingPreflight, TrainingRunItem, TrainingStatus } from '../lib/types'
import { ErrorNotice, Field, Module, Notice, PageHeader } from '../app/ui'
import { runStateLabel } from '../lib/vocab'
import { formatShortDate } from '../lib/dates'
import { useModelNames } from '../app/models/names'
import { PipelineStrip } from '../app/PipelineView'
import { buildPipeline, datasetUsable, usePipelineState } from '../app/pipeline'
import { useProjects, writeActiveProject } from '../app/activeProject'
import TrainingConfigForm, { effectiveExecutor } from './training/TrainingConfigForm'
import TrainingStatusPanel from './training/TrainingStatusPanel'
import TrainingDashboard, { type GpuPoint, type PreparePoint } from './training/TrainingDashboard'
import { BASE_CFG } from './training/presets'
import { useI18n } from '../i18n'

export default function TrainingPage() {
  const { t, locale } = useI18n()
  const nameOf = useModelNames()
  const [params, setParams] = useSearchParams()
  const [view, setViewState] = useState<'monitor' | 'configure'>(() => params.get('view') === 'new' ? 'configure' : 'monitor')
  const setView = (next: 'monitor' | 'configure') => {
    setViewState(next)
    setParams((current) => {
      const updated = new URLSearchParams(current)
      if (next === 'configure') updated.set('view', 'new')
      else updated.delete('view')
      return updated
    }, { replace: true })
  }
  useEffect(() => setViewState(params.get('view') === 'new' ? 'configure' : 'monitor'), [params])
  const [projectId, setProjectId] = useState<number | ''>('')
  const [cfg, setCfg] = useState<TrainConfigBody>(BASE_CFG)
  const [status, setStatus] = useState<TrainingStatus | null>(null)
  const [runs, setRuns] = useState<TrainingRunItem[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [gpuInfo, setGpuInfo] = useState<GpuInfo[]>([])
  /** `null` finché nvidia-smi non ha risposto: «nessuna GPU» è un verdetto, non un default. */
  const [gpuChecked, setGpuChecked] = useState(false)
  const [preflight, setPreflight] = useState<TrainingPreflight | null>(null)
  const [preflightLoading, setPreflightLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [stopArmed, setStopArmed] = useState(false)
  const stopArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [cleanupArmed, setCleanupArmed] = useState(false)
  const cleanupArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [autoJob, setAutoJob] = useState<AutoTrainingJob | null>(null)
  const [autoBusy, setAutoBusy] = useState(false)
  const [autoError, setAutoError] = useState<unknown>(null)
  const [gpuHistory, setGpuHistory] = useState<GpuPoint[]>([])
  const [prepareHistory, setPrepareHistory] = useState<PreparePoint[]>([])

  useEffect(() => {
    apiGet<{ gpus: GpuInfo[] }>('/system/gpu')
      .then((r) => setGpuInfo(r.gpus))
      .catch(() => setGpuInfo([]))
      .finally(() => setGpuChecked(true))
  }, [])

  useEffect(
    () => () => {
      if (stopArmTimer.current) clearTimeout(stopArmTimer.current)
      if (cleanupArmTimer.current) clearTimeout(cleanupArmTimer.current)
    },
    [],
  )

  // Il job automatico vive nel backend: se la pagina viene ricaricata mentre
  // la GPU si prepara, il browser ritrova lo stesso id e riprende il polling.
  useEffect(() => {
    if (projectId === '') return
    const saved = window.localStorage.getItem(`tabularium.auto.${projectId}`)
    if (!saved) return
    let alive = true
    apiGet<AutoTrainingJob>(`/projects/${projectId}/training/auto/${saved}`)
      .then((job) => {
        if (!alive) return
        setAutoJob(job)
        if (['queued', 'renting', 'waiting_ssh', 'provisioning', 'starting', 'running'].includes(job.state)) setView('monitor')
        if (job.run_id) setSelectedRunId(job.run_id)
      })
      .catch(() => window.localStorage.removeItem(`tabularium.auto.${projectId}`))
    return () => {
      alive = false
    }
  }, [projectId])

  useEffect(() => {
    if (!autoJob || projectId === '' || !autoJob.id) return
    const active = ['queued', 'renting', 'waiting_ssh', 'provisioning', 'starting', 'running'].includes(autoJob.state)
    if (!active) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      try {
        let job = await apiGet<AutoTrainingJob>(`/projects/${projectId}/training/auto/${autoJob.id}`)
        if (job.state === 'starting' && !job.run_id) {
          const history = await apiGet<{ items: TrainingRunItem[] }>(`/projects/${projectId}/training/runs`)
          const candidate = history.items.find((item) => item.adapter_id === job.adapter_id && item.started_at && Date.parse(item.started_at) >= Date.parse(job.created_at))
          if (candidate) {
            job = { ...job, run_id: candidate.run_id, phase: 'sync' }
            setRuns(history.items)
            setSelectedRunId(candidate.run_id)
          }
        }
        if (!alive) return
        setAutoJob(job)
        if (job.run_id && (job.run_id !== autoJob.run_id || autoJob.state !== job.state)) {
          setSelectedRunId(job.run_id)
          void loadRuns(projectId)
        }
        if (job.state === 'failed' || job.state === 'cancelled' || job.state === 'finished') return
        timer = setTimeout(() => void tick(), 4000)
      } catch (e) {
        if (alive) setAutoError(e)
      }
    }
    timer = setTimeout(() => void tick(), 1500)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [autoJob?.id, autoJob?.state, projectId]) // eslint-disable-line react-hooks/exhaustive-deps

  const onProject = (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setStatus(null)
    setRuns([])
    setSelectedRunId(null)
    setCleanupArmed(false)
    setAutoJob(null)
    setAutoError(null)
    setGpuHistory([])
    setPrepareHistory([])
  }

  useEffect(() => {
    setGpuHistory([])
    setPrepareHistory([])
  }, [selectedRunId])

  // Telemetria GPU campionata dal polling: il grafico mostra la storia, non
  // solo l'ultimo valore. Per il remoto il backend legge nvidia-smi via SSH.
  useEffect(() => {
    const gpu = (status?.gpu ?? gpuInfo)[0]
    if (!gpu) return
    const point: GpuPoint = {
      i: Date.now(),
      util: gpu.utilization,
      mem: gpu.memory_total ? Math.round((gpu.memory_used / gpu.memory_total) * 100) : 0,
    }
    setGpuHistory((prev) => [...prev.slice(-119), point])
  }, [status?.gpu, gpuInfo])

  useEffect(() => {
    const percent = status?.run?.prepare_job?.progress?.percent
    if (percent == null) return
    setPrepareHistory((prev) => [...prev.slice(-119), { i: Date.now(), percent }])
  }, [status?.run?.prepare_job?.progress?.percent])

  const projects = useProjects(onProject, setError)

  /** Storico delle run: senza, l'ultima era l'unica conoscibile. */
  const loadRuns = async (pid: number) => {
    try {
      const out = await apiGet<{ items: TrainingRunItem[] }>(`/projects/${pid}/training/runs`)
      setRuns(out.items ?? [])
    } catch {
      // Lo storico non deve impedire di avviare o osservare la run corrente.
    }
  }

  useEffect(() => {
    if (projectId === '') return
    void loadRuns(projectId)
  }, [projectId])

  // Dopo una transizione di stato lo storico cambia (finished/failed/stopped).
  useEffect(() => {
    if (projectId === '' || !status?.run?.state) return
    void loadRuns(projectId)
  }, [projectId, status?.run?.state]) // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Polling dello stato. Si ferma da solo quando il run raggiunge uno stato
   * terminale, così una pagina lasciata aperta non interroga il backend
   * all'infinito. Se è selezionata una run vecchia, si legge quella.
   */
  useEffect(() => {
    if (projectId === '') return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    const suffix = selectedRunId ? `?run_id=${encodeURIComponent(selectedRunId)}` : ''

    const tick = async () => {
      try {
        const s = await apiGet<TrainingStatus>(`/projects/${projectId}/training/status${suffix}`)
        if (cancelled) return
        if (!selectedRunId && s.run?.run_id) setSelectedRunId(s.run.run_id)
        if (cancelled) return
        setStatus(s)
        const automaticActive = ['queued', 'renting', 'waiting_ssh', 'provisioning', 'starting', 'running'].includes(autoJob?.state ?? '')
        const terminal =
          !automaticActive && !s.active && !['running', 'queued', 'starting'].includes(s.run?.prepare_job?.state ?? '') &&
          !['running', 'queued'].includes(s.run?.comparison_job?.state ?? '') &&
          (!s.run ||
            s.run.state === 'finished' ||
            s.run.state === 'failed' ||
            s.run.state === 'stopped')
        if (!terminal) timer = setTimeout(() => void tick(), 2500)
      } catch {
        // Rete temporaneamente assente: riprova, senza rumore a schermo.
        if (!cancelled) timer = setTimeout(() => void tick(), 5000)
      }
    }

    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [projectId, selectedRunId, autoJob?.id, autoJob?.state])

  const set = (patch: Partial<TrainConfigBody>) => setCfg((c) => ({ ...c, ...patch }))

  const onResumeRun = (runId: string) => {
    setCfg((c) => ({ ...c, ...status?.run?.config, resume_run_id: runId }))
    setView('configure')
  }

  // Finché nvidia-smi non ha risposto si considera la GPU presente: meglio
  // non spegnere «Locale» per un istante su una macchina che ce l'ha.
  const localGpu = !gpuChecked || gpuInfo.length > 0
  const executor = effectiveExecutor(cfg, localGpu)
  const requestBody = (): TrainConfigBody => ({
    ...cfg,
    executor: executor ?? cfg.executor,
    model_path: cfg.model_path?.trim() ? cfg.model_path.trim() : undefined,
  })

  const startAuto = async (maxDph: number) => {
    if (projectId === '') return
    setAutoBusy(true)
    setAutoError(null)
    try {
      const job = await apiPost<AutoTrainingJob>(`/projects/${projectId}/training/auto`, {
        config: requestBody(),
        credential_ref: 'vault:vast_api_key',
        max_dph: Math.max(0.1, Number(maxDph) || 1),
        min_gpu_ram_gb: 24,
        lang: document.documentElement.lang || 'it',
      })
      setAutoJob(job)
      window.localStorage.setItem(`tabularium.auto.${projectId}`, job.id)
      setView('monitor')
    } catch (e) {
      setAutoError(e)
    } finally {
      setAutoBusy(false)
    }
  }

  const cancelAuto = async () => {
    if (!autoJob || projectId === '') return
    try {
      const job = await apiPost<AutoTrainingJob>(`/projects/${projectId}/training/auto/${autoJob.id}/cancel`, {
        credential_ref: 'vault:vast_api_key',
      })
      setAutoJob(job)
      window.localStorage.removeItem(`tabularium.auto.${projectId}`)
    } catch (e) {
      setAutoError(e)
    }
  }

  // I controlli preliminari si leggono mentre si compila, non al clic: ciò
  // che bloccherà il run è scritto accanto al pulsante, con la causa.
  const { workflow, dataset } = usePipelineState(projectId === '' ? null : projectId, { training: false, runs: false })
  useEffect(() => {
    if (projectId === '' || executor === null) {
      setPreflight(null)
      return
    }
    let alive = true
    setPreflightLoading(true)
    const timer = setTimeout(() => {
      apiPost<TrainingPreflight>(`/projects/${projectId}/training/preflight`, requestBody())
        .then((out) => alive && setPreflight(out))
        .catch(() => alive && setPreflight(null))
        .finally(() => alive && setPreflightLoading(false))
    }, 400)
    return () => {
      alive = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, JSON.stringify(cfg), executor, dataset?.built])

  const start = async () => {
    if (projectId === '') return
    setBusy(true)
    setError(null)
    try {
      const started = await apiPost<TrainingStatus>(`/projects/${projectId}/training/start`, requestBody())
      setStatus(started)
      // Una run nuova non deve restare nascosta dietro alla run vecchia scelta
      // nello storico.
      setSelectedRunId(started.run?.run_id ?? null)
      setView('monitor')
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  /** Fermare un run butta via ore di GPU: serve una seconda pressione. */
  const stop = async () => {
    if (projectId === '') return
    if (!stopArmed) {
      setStopArmed(true)
      if (stopArmTimer.current) clearTimeout(stopArmTimer.current)
      stopArmTimer.current = setTimeout(() => setStopArmed(false), 5000)
      return
    }
    if (stopArmTimer.current) clearTimeout(stopArmTimer.current)
    setStopArmed(false)
    setBusy(true)
    try {
      setStatus(await apiPost<TrainingStatus>(`/projects/${projectId}/training/stop`))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const cleanupRemote = async () => {
    if (projectId === '' || !status?.run?.run_id) return
    if (!cleanupArmed) {
      setCleanupArmed(true)
      if (cleanupArmTimer.current) clearTimeout(cleanupArmTimer.current)
      cleanupArmTimer.current = setTimeout(() => setCleanupArmed(false), 5000)
      return
    }
    if (cleanupArmTimer.current) clearTimeout(cleanupArmTimer.current)
    setCleanupArmed(false)
    setBusy(true)
    try {
      await apiPost(`/projects/${projectId}/training/cleanup`, { run_id: status.run.run_id })
      const suffix = selectedRunId ? `?run_id=${encodeURIComponent(selectedRunId)}` : ''
      setStatus(await apiGet<TrainingStatus>(`/projects/${projectId}/training/status${suffix}`))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const project = projects.find((p) => p.id === projectId) ?? null
  const stages = buildPipeline({ project, workflow, dataset, training: status })

  const state = status?.run?.state ?? '—'
  const metricsData = (status?.metrics ?? []).map((m, i) => ({
    i: m.step ?? i,
    loss: m.eval_loss == null ? m.loss : undefined,
    evalLoss: m.eval_loss,
    lr: m.lr,
  }))
  const gpuList = gpuInfo.length ? gpuInfo : (status?.gpu ?? [])
  const provisioning = Boolean(autoJob && ['queued', 'renting', 'waiting_ssh', 'provisioning', 'starting'].includes(autoJob.state))
  const autoWithoutRun = Boolean(autoJob && !autoJob.run_id && autoJob.state !== 'finished')
  const remoteRun = status?.run?.config?.executor != null && status.run.config.executor !== 'local'

  return (
    <div className="p-3">
      <PageHeader title={t('training.title')} intro={t('training.intro')} aux={
        <div className="w-[min(24rem,100%)]">
          <Field label={t('training.project')}>
            <select className="fld" value={projectId} onChange={(e) => onProject(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">{t('common.chooseProject')}</option>
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </Field>
        </div>
      } />

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {projectId !== '' && <PipelineStrip stages={stages} here="train" />}

      <div role="group" aria-label={t('training.workspace')} className="mb-3 flex flex-wrap gap-2">
        <button type="button" aria-pressed={view === 'monitor'} className={`workspace-tab ${view === 'monitor' ? 'is-active' : ''}`} onClick={() => setView('monitor')}>{t('training.monitor')}</button>
        <button type="button" aria-pressed={view === 'configure'} className={`workspace-tab ${view === 'configure' ? 'is-active' : ''}`} onClick={() => setView('configure')}>{t('training.newTraining')}</button>
      </div>

      {view === 'monitor' && projectId !== '' && <>
        {!provisioning && runs.length > 0 && <div className="mb-3 flex flex-wrap items-end gap-3">
          <div className="min-w-0 flex-1">
            <Field label={t('training.runHistory')}>
              <select className="fld" value={selectedRunId ?? ''} onChange={(e) => { setStatus(null); setSelectedRunId(e.target.value); if (autoWithoutRun) setAutoJob(null) }}>
                {runs.map((item) => <option key={item.run_id} value={item.run_id}>
                  {formatShortDate(item.started_at, locale) || item.run_id} · {nameOf(item.adapter_id ?? '') ?? item.adapter_id} · {item.run_id === status?.run?.run_id && ['running', 'queued', 'starting'].includes(status?.run?.prepare_job?.state ?? '') ? t('training.dashboard.phasePrepare') : runStateLabel(item.state)}
                </option>)}
              </select>
            </Field>
          </div>
          {selectedRunId && !status?.active && !['running', 'queued', 'starting'].includes(status?.run?.prepare_job?.state ?? '') && <button type="button" className="btn mb-1" onClick={() => onResumeRun(selectedRunId)}>{t('training.resumeThisRun')}</button>}
          {status?.active && <button type="button" className="btn btn-danger mb-1" disabled={busy} onClick={() => void stop()}>{stopArmed ? t('training.confirmStop') : t('training.stopRun')}</button>}
        </div>}
        {autoError != null && <ErrorNotice error={autoError} onDismiss={() => setAutoError(null)} />}
        {provisioning && <div className="mb-3 flex items-center justify-between gap-3 border border-[color:var(--color-rule)] p-3">
          <p className="text-[13px]">{t(autoJob?.phase === 'ssh' ? 'training.auto.phaseSsh' : autoJob?.phase === 'env' ? 'training.auto.phaseEnv' : autoJob?.phase === 'sync' ? 'training.auto.phaseSync' : autoJob?.phase === 'train' ? 'training.dashboard.phaseTrain' : 'training.auto.phaseRent')}</p>
          <button type="button" className="btn" onClick={() => void cancelAuto()}>{t('training.auto.cancel')}</button>
        </div>}
        {!autoWithoutRun && status?.run?.state === 'finished' && !['running', 'queued', 'starting', 'finished'].includes(status.run.prepare_job?.state ?? '') && <div className="mb-3">
          <Notice tone={status.run.prepare_job?.state === 'failed' ? 'warn' : 'neutral'}>
            {t('ux.nextPrepareBody')}{' '}
            <a href="#training-checkpoint" className="font-semibold underline underline-offset-2">{t('training.prepareCheckpoint')}</a>
          </Notice>
        </div>}
        <TrainingDashboard
          projectId={projectId}
          status={autoWithoutRun ? null : status}
          autoJob={autoJob}
          metricsData={autoWithoutRun ? [] : metricsData}
          gpuHistory={autoWithoutRun ? [] : gpuHistory}
          prepareHistory={autoWithoutRun ? [] : prepareHistory}
          runs={autoWithoutRun ? [] : runs}
          remote={remoteRun}
        />
        {!status?.run && !provisioning && <Module tab={t('training.monitor')} quiet>
          <p className="mb-3 text-[13px]">{t('training.noRun')}</p>
          <button type="button" className="btn btn-primary" onClick={() => setView('configure')}>{t('training.newTraining')}</button>
        </Module>}
      </>}

      {view === 'configure' && <div className="max-w-5xl">
        <TrainingConfigForm
          hideProject
          projects={projects}
          projectId={projectId}
          cfg={cfg}
          busy={busy}
          isActive={status?.active === true}
          datasetReady={datasetUsable(dataset)}
          stopArmed={stopArmed}
          localGpu={localGpu}
          preflight={preflight}
          preflightLoading={preflightLoading}
          onProjectChange={onProject}
          onConfigChange={set}
          onStart={() => void start()}
          onStop={() => void stop()}
          autoJob={autoJob && ['queued', 'renting', 'waiting_ssh', 'provisioning', 'starting', 'running'].includes(autoJob.state) ? autoJob : null}
          autoBusy={autoBusy}
          autoError={autoError}
          onAutoStart={(maxDph) => void startAuto(maxDph)}
          onAutoCancel={() => void cancelAuto()}
        />
      </div>}
      {view === 'monitor' && !autoWithoutRun && status?.run && status.run.comparison_job?.state !== 'running' && ['finished', 'failed', 'stopped', 'error'].includes(status.run.state) && <div id="training-checkpoint" className="scroll-mt-3">
        <TrainingStatusPanel
          hideRunSelector
          projectId={projectId}
          status={status}
          gpuList={gpuList}
          state={state}
          canCleanup={Boolean(
            status?.run &&
              !status.run.remote_cleaned_at &&
              status.run.state !== 'running' &&
              status.run.state !== 'starting' &&
              status.run.config?.executor &&
              status.run.config.executor !== 'local',
          )}
          cleanupArmed={cleanupArmed}
          onCleanup={() => void cleanupRemote()}
          remote={remoteRun}
          runs={runs}
          selectedRunId={selectedRunId}
          onSelectRun={setSelectedRunId}
          onResumeRun={onResumeRun}
        />
      </div>}
    </div>
  )
}
