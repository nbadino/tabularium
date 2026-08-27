import { useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '../lib/api'
import type { GpuInfo, TrainConfigBody, TrainingStatus } from '../lib/types'
import { ErrorNotice } from '../app/ui'
import { PipelineStrip } from '../app/Pipeline'
import { buildPipeline, usePipelineState } from '../app/pipeline'
import { useProjects, writeActiveProject } from '../app/activeProject'
import TrainingConfigForm from './training/TrainingConfigForm'
import TrainingStatusPanel from './training/TrainingStatusPanel'
import { BASE_CFG } from './training/presets'
import { useI18n } from '../i18n'

export default function TrainingPage() {
  const { t } = useI18n()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [cfg, setCfg] = useState<TrainConfigBody>(BASE_CFG)
  const [status, setStatus] = useState<TrainingStatus | null>(null)
  const [gpuInfo, setGpuInfo] = useState<GpuInfo[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [stopArmed, setStopArmed] = useState(false)
  const stopArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    apiGet<{ gpus: GpuInfo[] }>('/system/gpu')
      .then((r) => setGpuInfo(r.gpus))
      .catch(() => setGpuInfo([]))
  }, [])

  useEffect(
    () => () => {
      if (stopArmTimer.current) clearTimeout(stopArmTimer.current)
    },
    [],
  )

  const onProject = (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setStatus(null)
  }

  const projects = useProjects(onProject, setError)

  /**
   * Polling dello stato. Si ferma da solo quando il run raggiunge uno stato
   * terminale, così una pagina lasciata aperta non interroga il backend
   * all'infinito.
   */
  useEffect(() => {
    if (projectId === '') return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null

    const tick = async () => {
      try {
        const s = await apiGet<TrainingStatus>(`/projects/${projectId}/training/status`)
        if (cancelled) return
        setStatus(s)
        if (s.gpu.length) setGpuInfo(s.gpu)
        const terminal =
          !s.active &&
          (s.run?.state === 'finished' || s.run?.state === 'failed' || s.run?.state === 'stopped')
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
  }, [projectId])

  const set = (patch: Partial<TrainConfigBody>) => setCfg((c) => ({ ...c, ...patch }))

  const start = async () => {
    if (projectId === '') return
    setBusy(true)
    setError(null)
    try {
      const body: TrainConfigBody = {
        ...cfg,
        model_path: cfg.model_path?.trim() ? cfg.model_path.trim() : undefined,
      }
      setStatus(await apiPost<TrainingStatus>(`/projects/${projectId}/training/start`, body))
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

  const project = projects.find((p) => p.id === projectId) ?? null
  const { workflow, dataset } = usePipelineState(projectId === '' ? null : projectId)
  const stages = buildPipeline({ project, workflow, dataset, training: status })

  const state = status?.run?.state ?? '—'
  const metricsData = (status?.metrics ?? []).map((m, i) => ({
    i: m.step ?? i,
    loss: m.loss,
    lr: m.lr,
  }))
  const gpuList = gpuInfo.length ? gpuInfo : (status?.gpu ?? [])

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('training.title')}
        </h1>
        <p className="mt-1 max-w-[80ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('training.intro')}
        </p>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {projectId !== '' && <PipelineStrip stages={stages} here="train" />}

      <div className="grid gap-3 lg:grid-cols-2">
        <TrainingConfigForm
          projects={projects}
          projectId={projectId}
          cfg={cfg}
          busy={busy}
          isActive={status?.active === true}
          stopArmed={stopArmed}
          onProjectChange={onProject}
          onConfigChange={set}
          onStart={() => void start()}
          onStop={() => void stop()}
        />
        <TrainingStatusPanel
          status={status}
          gpuList={gpuList}
          metricsData={metricsData}
          state={state}
        />
      </div>
    </div>
  )
}
