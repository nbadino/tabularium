import { useEffect, useState } from 'react'
import type {
  GpuInfo,
  PrepareStatus,
  TrainingRunItem,
  TrainingStatus,
} from '../../lib/types'
import { runStateLabel, RUN_STATE_TONE } from '../../lib/vocab'
import { formatShortDate } from '../../lib/dates'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../../lib/api'
import { Badge, Module, Notice, WarnNotice } from '../../app/ui'
import { useI18n } from '../../i18n'

interface TrainingStatusPanelProps {
  hideRunSelector?: boolean
  projectId: number | ''
  status: TrainingStatus | null
  gpuList: GpuInfo[]
  state: string
  canCleanup: boolean
  cleanupArmed: boolean
  onCleanup: () => void
  /** Il training scelto gira su una GPU remota: la GPU locale non conta. */
  remote?: boolean
  runs: TrainingRunItem[]
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  onResumeRun: (runId: string) => void
}

/** Cosa significa davvero lo stato in cui è finito il run. */
function stateNote(t: (k: string) => string, state: string): string {
  const key: Record<string, string> = {
    running: 'training.stateNoteRunning',
    finished: 'training.stateNoteFinished',
    stopped: 'training.stateNoteStopped',
    failed: 'training.stateNoteFailed',
    starting: 'training.stateNoteStarting',
  }
  return key[state] ? t(key[state]) : ''
}

function formatBytes(value: number | null | undefined): string {
  if (!value) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let amount = value
  let unit = 0
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024
    unit += 1
  }
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`
}

function phaseLabel(t: (k: string) => string, phase: string | undefined): string {
  const key: Record<string, string> = {
    merge: 'training.phaseMerge',
    download: 'training.phaseDownload',
    register: 'training.phaseRegister',
    done: 'training.phaseDone',
  }
  return phase && key[phase] ? t(key[phase]) : (phase ?? '')
}

export default function TrainingStatusPanel({
  hideRunSelector = false,
  projectId,
  status,
  gpuList,
  state,
  canCleanup,
  cleanupArmed,
  onCleanup,
  remote = false,
  runs,
  selectedRunId,
  onSelectRun,
  onResumeRun,
}: TrainingStatusPanelProps) {
  const { t, locale } = useI18n()
  const note = stateNote(t, state)
  const runId = status?.run?.run_id ?? ''
  const qualityRegressed = status?.run?.evaluation?.comparison?.verdict === 'regressed'
  const checkpoint = status?.run?.checkpoint ?? null
  const [prepare, setPrepare] = useState<PrepareStatus | null>(null)
  const [prepareError, setPrepareError] = useState<string | null>(null)
  const [prepareBusy, setPrepareBusy] = useState(false)
  const [prepareTick, setPrepareTick] = useState(0)
  const [showPrepareLog, setShowPrepareLog] = useState(false)

  // Il piano di merge si legge prima del clic: dove gira, cosa manca, quanto
  // pesa. Il pulsante non deve mai portare a un errore di shell.
  useEffect(() => {
    if (projectId === '' || !runId || state !== 'finished') {
      setPrepare(null)
      setPrepareError(null)
      return
    }
    let alive = true
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      try {
        const out = await apiGet<PrepareStatus>(
          `/projects/${projectId}/training/prepare-checkpoint?run_id=${encodeURIComponent(runId)}`,
        )
        if (!alive) return
        setPrepare(out)
        setPrepareError(null)
        if (out.job?.state === 'running') timer = setTimeout(() => void tick(), 2000)
      } catch (e) {
        if (alive) setPrepareError(String(e))
      }
    }
    void tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [projectId, runId, state, prepareTick])

  const startPrepare = async () => {
    if (projectId === '' || !runId) return
    setPrepareBusy(true)
    setPrepareError(null)
    try {
      const out = await apiPost<PrepareStatus>(`/projects/${projectId}/training/prepare-checkpoint`, {
        run_id: runId,
      })
      setPrepare(out)
      setPrepareTick((value) => value + 1)
    } catch (e) {
      setPrepareError(String(e))
    } finally {
      setPrepareBusy(false)
    }
  }

  const latestRun = runs[0]?.run_id
  const selected = runs.find((item) => item.run_id === selectedRunId)
  const job = prepare?.job
  const plan = prepare?.plan
  const model = prepare?.model
  const jobRunning = job?.state === 'running'
  const jobFinished = job?.state === 'finished' || Boolean(model)

  return (
    <div className="space-y-3">
      <Module
        tab={t(hideRunSelector ? 'training.postRun' : 'training.stateTab')}
        aux={
          !hideRunSelector && status?.run?.run_id && (
            <span className="mono text-[11px]">{status.run.run_id}</span>
          )
        }
      >
        {!hideRunSelector && runs.length > 0 && (
          <div className="mb-3 border-b border-[color:var(--color-rule)] pb-2">
            <span className="lbl">{t('training.runHistory')}</span>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <select
                aria-label={t('training.runHistory')}
                value={selectedRunId ?? ''}
                onChange={(e) => onSelectRun(e.target.value)}
                className="fld min-w-0 flex-1"
              >
                {runs.map((item) => (
                  <option key={item.run_id} value={item.run_id}>
                    {formatShortDate(item.started_at, locale) || item.run_id} ·{' '}
                    {item.adapter_id ?? item.run_id} · {runStateLabel(item.state)}
                  </option>
                ))}
              </select>
              {selectedRunId && selectedRunId !== latestRun && (
                <button type="button" className="btn btn-sm" onClick={() => onResumeRun(selectedRunId)}>
                  {t('training.resumeThisRun')}
                </button>
              )}
            </div>
            {selected && selected.adapter_id && (
              <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">
                {t('training.runHistoryHint', {
                  adapter: selected.adapter_id,
                  state: runStateLabel(selected.state),
                })}
              </p>
            )}
          </div>
        )}

        {!hideRunSelector && <>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={RUN_STATE_TONE[state] ?? 'neutral'}>{runStateLabel(state)}</Badge>
          {status?.run?.exit_code != null && state === 'failed' && (
            <span className="mono text-[11px] text-[color:var(--color-ink-2)]">
              exit {status.run.exit_code}
            </span>
          )}
        </div>
        {note && (
          <p className="mt-1.5 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
            {note}
          </p>
        )}

        </>}

        {state === 'finished' && (
          <div className="space-y-4">
            <details>
              <summary className="w-fit cursor-pointer text-[12px] text-[color:var(--color-ink-2)]">{t('training.weightsLocal')}</summary>
              {checkpoint ? (
                <>
                  <p className="mono mt-1 break-all text-[11px]" title={checkpoint.path}>
                    {checkpoint.path}
                  </p>
                  <p className="text-[11px] text-[color:var(--color-ink-3)]">
                    {t('training.weightsSize', { size: formatBytes(checkpoint.bytes) })}
                    {checkpoint.merged_path
                      ? ` · ${t('training.mergedSize', { size: formatBytes(checkpoint.merged_bytes) })}`
                      : ''}
                  </p>
                </>
              ) : (
                <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('training.noCheckpoint')}</p>
              )}
            </details>

            <div>
              {jobRunning ? (
                <Notice tone="neutral">
                  <span>{t('training.preparingCheckpoint')}</span>{' '}
                  <span className="text-[color:var(--color-ink-2)]">{phaseLabel(t, job?.phase)}</span>
                </Notice>
              ) : jobFinished && (model || job?.model_name) ? (
                <>
                  <Notice tone={qualityRegressed ? 'warn' : 'ok'}>
                    {t(qualityRegressed ? 'training.modelSavedForReview' : 'training.modelRegistered', { name: model?.display_name ?? job?.model_name ?? '' })}
                  </Notice>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Link to={`/modelli?modello=${encodeURIComponent(model?.id ?? job?.model_id ?? '')}&passo=dove`} className={`btn ${qualityRegressed ? '' : 'btn-primary'} no-underline`}>{t(qualityRegressed ? 'training.reviewModel' : 'training.serveModel')}</Link>
                  </div>
                  {!status?.run?.comparison_job && !status?.run?.evaluation?.comparison && <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('training.serveThenEvaluate')}</p>}
                </>
              ) : plan && !plan.ready ? (
                <WarnNotice title={t('training.prepareBlocked')}>
                  <ul className="list-disc space-y-1 pl-4">
                    {plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
                  </ul>
                </WarnNotice>
              ) : (
                <>
                  <p className="text-[12px] text-[color:var(--color-ink-2)]">
                    {plan?.mode === 'remote'
                      ? t('training.prepareRemote')
                      : plan?.mode === 'local'
                        ? t('training.prepareLocal')
                        : t('training.prepareRegister')}
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => void startPrepare()}
                      disabled={prepareBusy || !plan?.ready}
                    >
                      {prepareBusy
                        ? t('training.preparingCheckpoint')
                        : plan?.needs_merge
                          ? t('training.prepareCheckpoint')
                          : t('training.registerCheckpoint')}
                    </button>
                    {prepare?.model && <Badge tone="ok">{prepare.model.display_name}</Badge>}
                  </div>
                  {plan?.local_toolchain && !plan.local_toolchain.available && plan.mode === 'local' && (
                    <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{plan.local_toolchain.hint}</p>
                  )}
                </>
              )}
              {prepareError && <Notice tone="warn">{prepareError}</Notice>}
              {job?.state === 'failed' && (
                <WarnNotice title={t('training.prepareFailed')}>
                  <p className="text-[12px]">{t('training.prepareFailed')}</p>
                </WarnNotice>
              )}
              {(jobRunning || job?.state === 'failed') && prepare?.log_tail && (
                <div className="mt-2">
                  <button
                    type="button"
                    className="btn btn-sm"
                    aria-expanded={showPrepareLog}
                    aria-controls="training-prepare-log"
                    onClick={() => setShowPrepareLog((visible) => !visible)}
                  >
                    {showPrepareLog ? t('training.hideLogs') : t('training.prepareLog')}
                  </button>
                  {showPrepareLog && <pre id="training-prepare-log" className="mono mt-2 max-h-48 overflow-auto bg-[color:var(--color-fill)] p-2 text-[10px] leading-relaxed">
                    {job?.error ? `${job.error}\n\n` : ''}{prepare.log_tail}
                  </pre>}
                </div>
              )}
            </div>

          </div>
        )}

        {state === '—' && (
          <p className="mt-1.5 text-[12px] text-[color:var(--color-ink-2)]">
            {t('training.noRun')}
          </p>
        )}
        {canCleanup && !jobRunning && (
          <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2">
            <button type="button" className="btn btn-sm" onClick={onCleanup}>
              {cleanupArmed ? t('training.confirmCleanupRemote') : t('training.cleanupRemote')}
            </button>
            <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">
              {t('training.cleanupRemoteNote')}
            </p>
          </div>
        )}
      </Module>

      {/* Senza GPU locale non è un allarme: il training ha la sua via remota
          al passo 1, e il pannello lo dice invece di mandare l'utente altrove. */}
      {remote ? null : gpuList.length === 0 ? (
        <WarnNotice title={t('training.noGpuTitle')}>
          <p>
            {t('training.noGpuBody', { cmd: 'nvidia-smi' })}
          </p>
          <p className="mt-1.5">
            <b className="font-semibold">{t('errors.whatToDo')}</b>
            {t('training.noGpuAction')}
          </p>
        </WarnNotice>
      ) : (
        <Module tab={t('training.gpuTab')} quiet flush>
          <table className="w-full border-collapse text-[12px]">
            <thead className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)]">
              <tr>
                <th className="px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                  {t('training.dev')}
                </th>
                <th className="px-2 py-1 text-right text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                  {t('training.mem')}
                </th>
                <th className="px-2 py-1 text-right text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                  {t('training.usage')}
                </th>
                <th className="px-2 py-1 text-right text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                  {t('training.temp')}
                </th>
              </tr>
            </thead>
            <tbody className="ruled">
              {gpuList.map((g) => (
                <tr key={g.index}>
                  <td className="px-2 py-1">
                    <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
                      {g.index}
                    </span>{' '}
                    {g.name}
                  </td>
                  <td className="mono px-2 py-1 text-right">
                    {g.memory_used} / {g.memory_total} MB
                  </td>
                  <td className="mono px-2 py-1 text-right">{g.utilization}%</td>
                  <td className="mono px-2 py-1 text-right">{g.temp}°C</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Module>
      )}

    </div>
  )
}
