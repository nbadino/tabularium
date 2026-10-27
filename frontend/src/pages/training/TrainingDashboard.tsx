import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { AutoTrainingJob, TrainingRunItem, TrainingStatus } from '../../lib/types'
import { Badge, Module } from '../../app/ui'
import { familyLabel, runStateLabel, RUN_STATE_TONE } from '../../lib/vocab'
import { useI18n } from '../../i18n'

export interface GpuPoint {
  i: number
  util: number
  mem: number
}

export interface PreparePoint {
  i: number
  percent: number
}

interface Props {
  projectId?: number | null
  status: TrainingStatus | null
  autoJob: AutoTrainingJob | null
  metricsData: Array<{ i: number; loss?: number; evalLoss?: number; lr?: number }>
  gpuHistory: GpuPoint[]
  prepareHistory: PreparePoint[]
  runs: TrainingRunItem[]
  remote: boolean
}

function seconds(value: string | null | undefined, end?: string | null): number | null {
  if (!value) return null
  const ms = Date.parse(value)
  const until = end ? Date.parse(end) : Date.now()
  return Number.isFinite(ms) && Number.isFinite(until) ? Math.max(0, (until - ms) / 1000) : null
}

function duration(value: number | null): string {
  if (value == null) return '—'
  if (value < 60) return `${Math.round(value)} s`
  const minutes = Math.floor(value / 60)
  if (minutes < 60) return `${minutes} min ${Math.round(value % 60)} s`
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

function money(value: number | null, secondsValue: number | null): string {
  if (value == null || secondsValue == null) return '—'
  return `$${((value * secondsValue) / 3600).toFixed(2)}`
}

function bytes(value: number | null | undefined): string {
  if (!value) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

const AUTO_PHASES = ['rent', 'ssh', 'env', 'sync', 'train', 'prepare', 'done'] as const

function phaseKey(phase: string): string {
  const map: Record<string, string> = {
    rent: 'training.auto.phaseRent',
    ssh: 'training.auto.phaseSsh',
    env: 'training.auto.phaseEnv',
    sync: 'training.auto.phaseSync',
    train: 'training.dashboard.phaseTrain',
    prepare: 'training.dashboard.phasePrepare',
    done: 'training.auto.phaseDone',
  }
  return map[phase] ?? 'training.dashboard.phaseTrain'
}

export default function TrainingDashboard({ projectId, status, autoJob, metricsData, gpuHistory, prepareHistory, runs, remote }: Props) {
  const { t } = useI18n()
  const [, setTick] = useState(0)
  const [showRunLog, setShowRunLog] = useState(false)
  const [prepareLog, setPrepareLog] = useState('')
  useEffect(() => {
    const timer = window.setInterval(() => setTick((n) => n + 1), 5000)
    return () => window.clearInterval(timer)
  }, [])

  const run = status?.run
  useEffect(() => setShowRunLog(false), [run?.run_id])
  const job = autoJob && (!run?.run_id || !autoJob.run_id || autoJob.run_id === run.run_id) ? autoJob : null
  const summary = run?.summary ?? null
  const sourceSteps = runs.find((item) => item.run_id === run?.resumed_from)?.summary?.steps
  const checkpointOnly = Boolean(run?.resumed_from && sourceSteps != null && summary?.steps === sourceSteps)
  const latest = [...metricsData].reverse().find((item) => item.loss != null)
  const latestEval = [...(status?.metrics ?? [])].reverse().find((item) => item.eval_loss != null)?.eval_loss
  const evaluation = run?.evaluation
  const comparisonJob = run?.comparison_job
  const comparison = evaluation?.comparison
  const comparing = comparisonJob?.state === 'running'
  const recoveringComparison = comparing && comparisonJob?.instance_id != null
  const gpu = gpuHistory[gpuHistory.length - 1]
  const gpuName = comparing ? comparisonJob.gpu ?? job?.offer?.gpu_name ?? status?.gpu?.[0]?.name : job?.offer?.gpu_name ?? status?.gpu?.[0]?.name
  const dph = comparisonJob?.hourly_rate ?? job?.offer?.dph_total ?? null
  const terminalJob = job && ['finished', 'failed', 'cancelled'].includes(job.state)
  const elapsed = comparisonJob?.started_at ? seconds(comparisonJob.started_at, comparisonJob.ended_at) : seconds(run?.started_at ?? job?.created_at, run?.ended_at ?? (terminalJob ? job.updated_at : undefined))
  const rentalElapsed = comparisonJob?.hourly_rate ? seconds(comparisonJob.started_at, comparisonJob.ended_at) : job ? seconds(job.created_at, terminalJob ? job.updated_at : undefined) : elapsed
  const rawPhase = job?.state === 'finished' ? 'done' : job?.phase ?? (run?.state === 'finished' ? 'done' : run?.state === 'running' ? 'train' : 'rent')
  const failedPhase = job?.failure_stage === 'training' ? 'train' : job?.failure_stage
  const phase = rawPhase === 'failed' ? failedPhase ?? (summary?.steps && summary.steps === summary.total_steps ? 'prepare' : 'train') : rawPhase === 'training' ? 'train' : rawPhase
  const phaseIndex = AUTO_PHASES.indexOf(phase as (typeof AUTO_PHASES)[number])
  const failed = job?.state === 'failed' || run?.state === 'failed'
  const prepare = run?.prepare_job
  useEffect(() => {
    if (!showRunLog || !prepare || !projectId || !run?.run_id) return
    let cancelled = false
    const update = () => apiGet<{log_tail?: string}>(`/projects/${projectId}/training/prepare-checkpoint?run_id=${encodeURIComponent(run.run_id)}`)
      .then((result) => { if (!cancelled) setPrepareLog(result.log_tail ?? '') })
      .catch(() => { if (!cancelled) setPrepareLog(t('training.noLog')) })
    setPrepareLog('')
    void update()
    const timer = window.setInterval(() => void update(), 5000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [showRunLog, projectId, run?.run_id, Boolean(prepare), t])
  const prepareProgress = prepare?.progress
  const preparePhase = prepare?.phase
  const preparePercent =
    prepareProgress?.percent ??
    (prepare?.state === 'finished' || preparePhase === 'done' ? 100 : 0)
  const prepareSteps = ['merge', 'evaluate', 'download', 'register', 'done'] as const
  const prepareIndex = prepareSteps.indexOf(preparePhase as (typeof prepareSteps)[number])
  const hasMetrics = !checkpointOnly && metricsData.length > 0
  const fetchMatches = [...(status?.log_tail ?? '').matchAll(/Fetching\s+(\d+)\s+files:\s*(\d+)%[^\r\n]*?(\d+)\/(\d+)/g)]
  const fetch = status?.active && !hasMetrics ? fetchMatches[fetchMatches.length - 1] : undefined

  const progressLines = [...(status?.log_tail ?? '').matchAll(/global_step\/max_steps['"]?\s*:\s*['"](\d+)\/(\d+)/g)]
  const recorded = progressLines[progressLines.length - 1]
  const trainLines = [...(status?.log_tail ?? '').matchAll(/Train:\s*\d+%[^\r\n]*?\|\s*(\d+)\/(\d+)/g)]
  const trainProgress = trainLines[trainLines.length - 1]
  const currentStep = summary?.steps != null || recorded || trainProgress ? Math.max(summary?.steps ?? 0, recorded ? Number(recorded[1]) : 0, trainProgress ? Number(trainProgress[1]) : 0) : undefined
  const totalSteps = summary?.total_steps ?? (recorded ? Number(recorded[2]) : trainProgress ? Number(trainProgress[2]) : undefined)
  const trainingComplete = Boolean(totalSteps && currentStep === totalSteps)
  const active = Boolean(status?.active || (job && !terminalJob) || comparing || prepare?.state === 'running')
  const providerText = (job?.provider_message ?? '').toLowerCase()
  const providerActivity = phase === 'ssh' ? /verifying|checksum/.test(providerText) ? 'imageVerify' : /extract/.test(providerText) ? 'imageExtract' : /download|pulling/.test(providerText) ? 'imageDownload' : null : null
  const counts = run?.data_view_manifest?.counts
  const sampleCounts = counts ? Object.values(counts).reduce((sum, item) => ({ train: sum.train + item.train, val: sum.val + item.val }), {train: 0, val: 0}) : null
  const modelLoading = phase === 'sync' && run?.activity?.stage === 'model_loading'
  const stage = modelLoading ? t('training.dashboard.activity.modelLoading') : comparing ? t(`training.comparison.phases.${comparisonJob.phase}`) : prepare?.state === 'running' ? t(`training.dashboard.prepare_${preparePhase}`) : failed ? t('training.dashboard.interrupted') : providerActivity ? t(`training.dashboard.activity.${providerActivity}`) : t(phaseKey(phase))
  const remainingMatches = [...(status?.log_tail ?? '').matchAll(/remaining_time['"]?\s*:\s*['"]([^'"]+)/g)]
  const lastMetric = remainingMatches[remainingMatches.length - 1]
  const nextPhase = AUTO_PHASES[phaseIndex + 1]
  const observationTimes = [job?.updated_at ? Date.parse(job.updated_at) : 0, (status?.metrics?.[status.metrics.length - 1]?.t ?? 0) * 1000, (run?.activity?.updated_at ?? 0) * 1000]
  const lastObserved = Math.max(...observationTimes)

  if (!run && !job) return null

  return (
    <div className="mb-6 space-y-8">
      <Module
        tab={t('training.dashboard.title')}
        aux={
          <span className="flex items-center gap-2">
            <Badge tone={active ? 'neutral' : RUN_STATE_TONE[run?.state ?? job?.state ?? 'queued'] ?? 'neutral'}>
              {active ? runStateLabel('running') : runStateLabel(run?.state ?? job?.state ?? 'queued')}
            </Badge>
            {run?.run_id && (
              <button
                type="button"
                className="btn btn-sm"
                aria-expanded={showRunLog}
                aria-controls="training-run-log"
                onClick={() => setShowRunLog((visible) => !visible)}
              >
                {showRunLog ? t('training.hideLogs') : t('training.showLogs')}
              </button>
            )}
          </span>
        }
      >
        <div role="status" aria-live="polite" className="mb-4 space-y-2">
          <p className="text-sm font-semibold">{stage}</p>
          <p className="text-[12px] text-[color:var(--color-ink-2)]">
            {failed && trainingComplete ? t('training.dashboard.completedButInterrupted')
              : modelLoading ? t('training.dashboard.activity.modelLoadingDetail') : active && !hasMetrics && !comparing && !prepare && AUTO_PHASES.includes(phase as typeof AUTO_PHASES[number]) && phase !== 'done' ? t(`training.dashboard.activity.${phase}`)
              : comparing ? t('training.dashboard.sameGpuComparison')
              : active && nextPhase ? t('training.dashboard.nextStage', { stage: t(phaseKey(nextPhase)) }) : null}
          </p>
          {comparison && !comparing && <p className={`text-[13px] font-semibold ${comparison.verdict === 'regressed' || comparison.verdict === 'mixed' ? 'text-[color:var(--color-warn)]' : comparison.verdict === 'improved' ? 'text-[color:var(--color-ok)]' : ''}`}>
            {t(`training.comparison.verdicts.${comparison.verdict}`)}
            {comparison.deltas.table_teds && ` · TEDS ${(comparison.deltas.table_teds.baseline * 100).toFixed(2)}% → ${(comparison.deltas.table_teds.fine_tuned * 100).toFixed(2)}%`}
          </p>}
          {comparing && comparisonJob.phase === 'environment' && comparisonJob.download && <div>
            <p className="mono text-[11px] break-all">{comparisonJob.download.name} · {bytes(comparisonJob.download.done)} / ~{bytes(comparisonJob.download.total)}</p>
            <progress className="h-1.5 w-full" aria-label={comparisonJob.download.name} value={comparisonJob.download.done} max={Math.max(1, comparisonJob.download.total)} />
          </div>}
          {providerActivity && active && <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('training.dashboard.activity.unknownProgress')}</p>}
          {sampleCounts && <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('training.dashboard.activity.samples', { ...sampleCounts, tablesTrain: counts?.table?.train ?? 0, tablesVal: counts?.table?.val ?? 0 })}</p>}
          {trainingComplete && phase !== 'train' ? <p className="text-[12px]">{t('training.dashboard.trainingFinished', { steps: currentStep ?? 0 })}</p> : currentStep != null && totalSteps != null && totalSteps > 0 && <div>
            <div className="flex justify-between text-[12px] mono"><span>{t('training.dashboard.trainingProgress')}: {currentStep}/{totalSteps}</span><span>{((currentStep / totalSteps) * 100).toFixed(0)}%</span></div>
            <progress className="mt-1 h-1.5 w-full accent-[color:var(--color-accent)]" aria-label={t('training.dashboard.trainingProgress')} value={currentStep} max={totalSteps} />
            {active && phase === 'train' && lastMetric && <p className="text-[11px] text-[color:var(--color-ink-2)]">{t('training.dashboard.remainingTraining', { time: lastMetric[1] })}</p>}
          </div>}
          {active && phase === 'sync' && run?.activity?.stage === 'sync' && <div>
            <p className="mono text-[12px]">{run.activity.files_done}/{run.activity.files_total} file · {bytes(run.activity.bytes_done)} / {bytes(run.activity.bytes_total)}</p>
            <progress className="h-1.5 w-full" aria-label={stage} value={run.activity.bytes_done} max={Math.max(1, run.activity.bytes_total)} />
          </div>}
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[color:var(--color-ink-2)]">
            {active && job?.phase_started_at && <span>{t('training.dashboard.stageDuration', { time: duration(seconds(job.phase_started_at)) })}</span>}
            {active && lastObserved > 0 && <span>{t('training.dashboard.lastUpdate', { time: duration(Math.max(0, (Date.now() - lastObserved) / 1000)) })}</span>}
            {job && <span>{t('training.dashboard.rentalDuration', { time: duration(rentalElapsed) })} · ${dph?.toFixed(3) ?? '—'}/h</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-x-10 gap-y-4">
          {[
            [t('training.dashboard.gpu'), gpuName ?? '—'],
            [t('training.dashboard.cost'), job?.cost_unknown ? '—' : money(dph, rentalElapsed)],
            [t('training.dashboard.loss'), checkpointOnly ? '—' : (latest?.loss ?? summary?.train_loss)?.toFixed(4) ?? '—'],
            [t('training.evalLoss'), (latestEval ?? summary?.eval_loss)?.toFixed(4) ?? '—'],
            [t(summary?.peak_gib != null ? 'training.peakMemory' : 'training.dashboard.memory'), summary?.peak_gib != null ? `${summary.peak_gib.toFixed(1)} GiB` : gpu ? `${gpu.mem}%` : '—'],
          ].filter(([, value]) => value !== '—').map(([label, value]) => (
            <div key={label} className="min-w-24">
              <div className="lbl">{label}</div>
              <div className="mono mt-1 truncate text-[15px] font-semibold" title={String(value)}>{value}</div>
            </div>
          ))}
        </div>

        {checkpointOnly && <p className="mt-2 text-[12px] text-[color:var(--color-ink-2)]">{t('training.dashboard.checkpointOnly')}</p>}
        {(comparisonJob?.instance_id ? comparisonJob.cleanup_confirmed : job?.instance_destroyed) && <p className="mt-2 text-[12px] text-[color:var(--color-ok)]">{t('training.auto.released')}</p>}

        {fetch && <div className="mt-3">
          <p className="text-[12px]">{t('training.dashboard.modelDownload', { done: Number(fetch[3]), total: Number(fetch[4]) })}</p>
          <div role="progressbar" aria-label={t('training.dashboard.modelDownload', { done: Number(fetch[3]), total: Number(fetch[4]) })} aria-valuemin={0} aria-valuemax={Number(fetch[4])} aria-valuenow={Number(fetch[3])} className="mt-2 h-1.5 bg-[color:var(--color-fill)]">
            <div className="h-full bg-[color:var(--color-accent)]" style={{ width: `${Math.max(0, Math.min(100, Number(fetch[2])))}%` }} />
          </div>
        </div>}
        {job && !recoveringComparison && (
          <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2">
            <div className="flex flex-wrap items-center gap-0">
              {AUTO_PHASES.map((item, index) => {
                const done = phaseIndex >= 0 && index < phaseIndex
                const active = index === phaseIndex
                const bad = failed && active
                return (
                  <div key={item} className="flex min-w-0 flex-1 items-center">
                    <div className="flex min-w-0 items-center gap-1.5" title={t(phaseKey(item))}>
                      <span
                        className={`inline-block h-2.5 w-2.5 rounded-full ${
                          bad
                            ? 'bg-[color:var(--color-sig)]'
                            : done
                              ? 'bg-[color:var(--color-ok)]'
                              : active
                                ? 'bg-[color:var(--color-ink)]'
                                : 'bg-[color:var(--color-rule)]'
                        }`}
                      />
                      <span className={`truncate text-[11px] ${active ? 'font-semibold' : 'text-[color:var(--color-ink-2)]'}`}>
                        {t(`training.dashboard.shortPhases.${item}`)}
                      </span>
                    </div>
                    {index < AUTO_PHASES.length - 1 && (
                      <span className={`mx-2 h-px min-w-2 flex-1 ${done ? 'bg-[color:var(--color-ok)]' : 'bg-[color:var(--color-rule)]'}`} />
                    )}
                  </div>
                )
              })}
            </div>
            {(job.log || job.error || job.message) && <details className="mt-2"><summary className="w-fit cursor-pointer text-[12px]">{t('training.auto.details')}</summary><pre className="mono mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-[11px]">{[job.message, job.provider_message, job.error, job.log].filter(Boolean).join('\n')}</pre></details>}
          </div>
        )}

        {prepare && prepare.state !== 'finished' && !recoveringComparison && (
          <div className="mt-2 border-t border-[color:var(--color-rule)] pt-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="lbl">{t('training.dashboard.prepare')}</span>
              {prepareSteps.map((item, index) => (
                <span
                  key={item}
                  className={`text-[11px] ${
                    prepareIndex >= index ? 'font-semibold' : 'text-[color:var(--color-ink-3)]'
                  }`}
                >
                  {t(`training.dashboard.prepare_${item}`)}
                  {index < prepareSteps.length - 1 ? ' →' : ''}
                </span>
              ))}
            </div>
          </div>
        )}

        {prepare && !recoveringComparison && preparePhase === 'download' && prepare.state !== 'finished' && (
          <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2">
            <div className="flex items-center justify-between gap-2">
              <span className="lbl">{prepare?.state === 'failed' ? t('training.dashboard.prepareFailed') : t('training.dashboard.download')}</span>
              <span className="mono text-[11px]">
                {`${preparePercent.toFixed(1)}%`}
                {prepareProgress?.done ? ` · ${bytes(prepareProgress.done)} / ${bytes(prepareProgress.total)}` : ''}
                {prepareProgress?.bytes_per_s ? ` · ${bytes(prepareProgress.bytes_per_s)}/s` : ''}
              </span>
            </div>
            <div className="mt-1 h-2 w-full overflow-hidden bg-[color:var(--color-fill)]">
              <div
                className="h-full bg-[color:var(--color-sig)] transition-all duration-500"
                style={{ width: `${Math.max(0, Math.min(100, preparePercent))}%` }}
              />
            </div>
            {prepareHistory.length > 1 ? (
              <div className="mt-2 h-24">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={prepareHistory} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                    <XAxis dataKey="i" hide />
                    <YAxis domain={[0, 100]} unit="%" width={32} fontSize={10} tickLine={false} />
                    <Tooltip
                      contentStyle={{ background: '#fff', border: '1px solid #111', borderRadius: 0, fontSize: 11 }}
                      formatter={(value: number) => [`${value.toFixed(1)}%`, t('training.dashboard.download')]}
                    />
                    <Area type="monotone" dataKey="percent" stroke="#111" fill="#111" fillOpacity={0.08} strokeWidth={1.3} isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : prepareProgress?.done ? null : (
              <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('training.dashboard.downloadHint')}</p>
            )}
          </div>
        )}

        {showRunLog && (
          <div id="training-run-log" className="mt-3 border-t border-[color:var(--color-rule)] pt-2">
            <pre className="mono max-h-64 overflow-auto bg-[color:var(--color-fill)] p-2 text-[10px] leading-relaxed">
              {(prepare ? prepareLog : status?.log_tail) || t('training.noLog')}
            </pre>
          </div>
        )}
      </Module>

      {(status?.active || hasMetrics || gpuHistory.length > 1) && <div className={`grid gap-8 ${gpuHistory.length > 1 ? 'lg:grid-cols-2' : ''}`}>
        {!checkpointOnly && <Module tab={t('training.dashboard.metrics')} quiet>
          {hasMetrics ? (
            <div>
              <div className="mb-1 flex gap-4 text-[11px]"><span style={{color: '#a3000e'}}>{t('training.loss')}</span><span style={{color: '#326b85'}}>{t('training.evalLoss')}</span></div>
              <div className="h-52"><ResponsiveContainer width="100%" height="100%">
                <AreaChart data={metricsData} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
                  <defs>
                    <linearGradient id="lossFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#a3000e" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#a3000e" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#e6e8eb" strokeDasharray="2 2" />
                  <XAxis dataKey="i" stroke="#736d69" fontSize={11} tickLine={false} />
                  <YAxis stroke="#736d69" fontSize={11} width={44} domain={['auto', 'auto']} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#fff', border: '1px solid #111', borderRadius: 0, fontSize: 12 }}
                    formatter={(value: number, name: string) => [value.toFixed(4), t(name === 'evalLoss' ? 'training.evalLoss' : 'training.loss')]}
                    labelFormatter={(value) => t('training.step', { v: String(value) })}
                  />
                  <Area type="monotone" dataKey="loss" stroke="#a3000e" strokeWidth={1.5} fill="url(#lossFill)" connectNulls isAnimationActive={false} />
                  <Area type="monotone" dataKey="evalLoss" stroke="#326b85" strokeWidth={1.5} strokeDasharray="4 3" fill="none" dot={{r: 3}} connectNulls isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer></div>
            </div>
          ) : (
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('training.dashboard.noMetrics')}</p>
          )}
        </Module>}

        {(gpuHistory.length > 1 || status?.active) && <Module tab={t('training.dashboard.gpuHistory')} quiet>
          {gpuHistory.length > 1 ? (
            <div className="h-52">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={gpuHistory} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
                  <CartesianGrid stroke="#e6e8eb" strokeDasharray="2 2" />
                  <XAxis dataKey="i" hide />
                  <YAxis domain={[0, 100]} unit="%" stroke="#736d69" fontSize={11} width={36} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#fff', border: '1px solid #111', borderRadius: 0, fontSize: 12 }}
                    formatter={(value: number, name: string) => [`${value}%`, name === 'util' ? t('training.dashboard.utilization') : t('training.dashboard.memory')]}
                  />
                  <Area type="monotone" dataKey="util" stroke="#111" fill="#111" fillOpacity={0.08} strokeWidth={1.5} isAnimationActive={false} />
                  <Area type="monotone" dataKey="mem" stroke="#a3000e" fill="#a3000e" fillOpacity={0.05} strokeWidth={1.2} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="text-[12px] text-[color:var(--color-ink-2)]">
              {!status?.active ? t('training.dashboard.noRecordedGpu') : remote ? t('training.dashboard.noGpuHistoryRemote') : t('training.dashboard.noGpuHistory')}
            </p>
          )}
        </Module>}
      </div>}

      {(evaluation || summary?.eval_token_acc != null || comparisonJob) && <Module
        tab={t('training.dashboard.performance')}
      >
        {comparisonJob && <div className="mb-4 space-y-2">
          <p className="text-[13px] font-semibold">{t('training.comparison.title')}</p>
          <p className="text-[12px]">{t(`training.comparison.phases.${comparisonJob.phase}`)}{comparisonJob.target ? ` · ${t(`training.comparison.${comparisonJob.target}`)}` : ''}</p>
          {comparing && <>
            {comparisonJob.family && comparisonJob.sample_state === 'running' && <p className="text-[12px]">{familyLabel(comparisonJob.family)} · {comparisonJob.sample_number}/{comparisonJob.target_total} · {duration(seconds(comparisonJob.sample_started_at))}</p>}
            <p className="mono text-[12px]">{t('training.comparison.progress', { done: comparisonJob.completed ?? 0, total: comparisonJob.total ?? 0 })}</p>
            <div role="progressbar" aria-label={t('training.comparison.title')} aria-valuemin={0} aria-valuemax={comparisonJob.total ?? 16} aria-valuenow={comparisonJob.completed ?? 0} className="h-1.5 bg-[color:var(--color-fill)]"><div className="h-full bg-[color:var(--color-sig)]" style={{ width: `${comparisonJob.total ? 100 * (comparisonJob.completed ?? 0) / comparisonJob.total : 0}%` }} /></div>
            {comparisonJob.transfer && comparisonJob.phase === 'upload' && <p className="mono text-[11px]">{bytes(comparisonJob.transfer.done)} / {bytes(comparisonJob.transfer.total)}</p>}
          </>}
          {(comparisonJob.error || comparisonJob.log_tail) && <details><summary className="w-fit cursor-pointer text-[12px]">{t('training.showLogs')}</summary><pre className="mono mt-2 max-h-64 overflow-auto whitespace-pre-wrap text-[11px]">{[comparisonJob.error, comparisonJob.log_tail].filter(Boolean).join('\n')}</pre></details>}
        </div>}
        {comparison && <div className="space-y-3">
          <table className="w-full text-left text-[12px]">
            <thead><tr><th className="py-2">{t('training.comparison.metric')}</th><th>{t('training.comparison.base')}</th><th>{t('training.comparison.fine_tuned')}</th><th>{t('training.comparison.change')}</th></tr></thead>
            <tbody>{Object.entries(comparison.deltas).map(([key, metric]) => <tr key={key} className="border-t border-[color:var(--color-rule)]">
              <th className="py-2 font-normal">{t(`training.comparison.metrics.${key}`)}</th><td className="mono">{(metric.baseline * 100).toFixed(2)}%</td><td className="mono">{(metric.fine_tuned * 100).toFixed(2)}%</td><td className={`mono ${metric.result === 'improved' ? 'text-[color:var(--color-ok)]' : metric.result === 'regressed' ? 'text-[color:var(--color-warn)]' : ''}`}>{metric.delta > 0 ? '+' : ''}{(metric.delta * 100).toFixed(2)} pp</td>
            </tr>)}</tbody>
          </table>
          {comparison.training_context && comparison.training_context.optimizer_steps != null && <p className="text-[12px]">{t('training.comparison.trainingContext', { pages: comparison.training_context.pages, samples: comparison.training_context.samples, steps: comparison.training_context.optimizer_steps })}</p>}
          {comparison.output_changes && <p className="text-[12px]">{t('training.comparison.outputChanges', { changed: comparison.output_changes.changed, total: comparison.output_changes.total })}</p>}
          <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('training.comparison.scope', { pages: comparison.counts.layout ?? 0, text: comparison.counts.text ?? 0 })}</p>
          {!comparison.counts.table && <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('training.comparison.noTables')}</p>}
        </div>}
        {!comparing && evaluation?.error && <div className="mt-2 text-[12px] text-[color:var(--color-warn)]">
          <p>{t(prepare?.state === 'finished' ? 'training.evaluationRuntimeFailed' : 'training.evaluationFailed')}</p>
          <details className="mt-1"><summary className="cursor-pointer font-semibold">{t('training.showLogs')}</summary><pre className="mono mt-2 overflow-auto whitespace-pre-wrap text-[11px]">{evaluation.error}</pre></details>
        </div>}
        {!comparison && evaluation?.aggregates && (
          <div className="mt-3 grid grid-cols-2 gap-2 border-t border-[color:var(--color-rule)] pt-2 sm:grid-cols-4">
            {[
              [t('evaluate.precision'), evaluation.aggregates.layout?.precision],
              [t('evaluate.recall'), evaluation.aggregates.layout?.recall],
              [t('evaluate.textCer'), evaluation.aggregates.text?.mean_cer],
              [t('evaluate.tablesStruct'), evaluation.aggregates.tables?.structure_ok_pct],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <div className="lbl">{label}</div>
                <div className="mono text-[14px] font-semibold">
                  {value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`}
                </div>
              </div>
            ))}
            <p className="col-span-full text-[11px] text-[color:var(--color-ink-3)]">
              {t('training.dashboard.evalPages', { n: evaluation.pages_evaluated ?? 0 })}
            </p>
          </div>
        )}
        {summary?.eval_token_acc != null && (
          <details className="mt-3"><summary className="w-fit cursor-pointer text-[12px]">{t('training.dashboard.trainingDiagnostics')}</summary><div className="mt-2">
            <div className="flex items-center justify-between text-[11px]">
              <span>{t('training.tokenAccuracy')}</span>
              <span className="mono">{(summary.eval_token_acc * 100).toFixed(1)}%</span>
            </div>
            <div className="mt-1 h-2 w-full overflow-hidden bg-[color:var(--color-fill)]">
              <div className="h-full bg-[color:var(--color-ok)]" style={{ width: `${Math.max(0, Math.min(100, summary.eval_token_acc * 100))}%` }} />
            </div>
            <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('training.dashboard.tokenAccuracyHint')}</p>
          </div></details>
        )}
        {runs.filter((item) => item.summary?.train_loss != null || item.summary?.eval_loss != null).length > 1 && (
          <details className="mt-5">
            <summary className="w-fit cursor-pointer text-[12px] font-medium text-[color:var(--color-ink-2)]">{t('training.dashboard.compareRuns')}</summary>
            <table className="mt-1 w-full text-[11px]">
              <thead>
                <tr className="text-left text-[color:var(--color-ink-3)]">
                  <th className="py-1">{t('training.dashboard.run')}</th>
                  <th className="py-1">{t('training.trainLoss')}</th>
                  <th className="py-1">{t('training.evalLoss')}</th>
                  <th className="py-1">{t('training.tokenAccuracy')}</th>
                </tr>
              </thead>
              <tbody>
                {runs.filter((item) => item.summary?.train_loss != null || item.summary?.eval_loss != null).slice(0, 5).map((item) => (
                  <tr key={item.run_id} className="border-t border-[color:var(--color-rule)]">
                    <td className="mono py-1">{item.run_id}</td>
                    <td className="mono py-1">{item.summary?.train_loss?.toFixed(4) ?? '—'}</td>
                    <td className="mono py-1">{item.summary?.eval_loss?.toFixed(4) ?? '—'}</td>
                    <td className="mono py-1">{item.summary?.eval_token_acc != null ? `${(item.summary.eval_token_acc * 100).toFixed(1)}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </Module>}



    </div>
  )
}
