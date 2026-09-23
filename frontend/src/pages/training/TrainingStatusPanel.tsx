import {
  CartesianGrid,
  Label,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useState } from 'react'
import type { GpuInfo, TrainingStatus } from '../../lib/types'
import { runStateLabel, RUN_STATE_TONE } from '../../lib/vocab'
import { Link } from 'react-router'
import { apiPost } from '../../lib/api'
import { Badge, Module, Notice, WarnNotice } from '../../app/ui'
import { useI18n } from '../../i18n'

interface TrainingStatusPanelProps {
  projectId: number | ''
  status: TrainingStatus | null
  gpuList: GpuInfo[]
  metricsData: Array<{ i: number; loss?: number; lr?: number }>
  state: string
  canCleanup: boolean
  cleanupArmed: boolean
  onCleanup: () => void
  /** Il training scelto gira su una GPU remota: la GPU locale non conta. */
  remote?: boolean
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

export default function TrainingStatusPanel({
  projectId,
  status,
  gpuList,
  metricsData,
  state,
  canCleanup,
  cleanupArmed,
  onCleanup,
  remote = false,
}: TrainingStatusPanelProps) {
  const { t, tn } = useI18n()
  const hasLr = metricsData.some((m) => m.lr != null)
  const note = stateNote(t, state)
  const [registeringModel, setRegisteringModel] = useState(false)
  const [registeredModel, setRegisteredModel] = useState<string | null>(null)
  const [registerError, setRegisterError] = useState<string | null>(null)

  const registerModel = async () => {
    if (projectId === '' || !status?.run?.run_id) return
    setRegisteringModel(true)
    setRegisterError(null)
    try {
      const model = await apiPost<{ display_name?: string }>(`/projects/${projectId}/training/register-model`, {
        run_id: status.run.run_id,
      })
      setRegisteredModel(model.display_name ?? t('training.registeredModel'))
    } catch (e) {
      setRegisterError(String(e))
    } finally {
      setRegisteringModel(false)
    }
  }

  return (
    <div className="space-y-3">
      <Module
        tab={t('training.stateTab')}
        aux={
          status?.run?.run_id && (
            <span className="mono text-[11px]">{status.run.run_id}</span>
          )
        }
      >
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
        {state === 'finished' && (
          <div className="mt-3 border-t border-[color:var(--color-rule)] pt-2">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('training.checkpointReady')}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button type="button" className="btn btn-primary" onClick={() => void registerModel()} disabled={registeringModel || registeredModel !== null}>
                {registeringModel ? t('training.registeringModel') : t('training.useCheckpoint')}
              </button>
              {registeredModel && <Link to="/modelli" className="btn no-underline">{t('training.chooseProvider')}</Link>}
            </div>
            {registeredModel && <Notice tone="ok">{t('training.modelRegistered', { name: registeredModel })}</Notice>}
            {registerError && <Notice tone="warn">{registerError}</Notice>}
          </div>
        )}
        {state === '—' && (
          <p className="mt-1.5 text-[12px] text-[color:var(--color-ink-2)]">
            {t('training.noRun')}
          </p>
        )}
        {canCleanup && (
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

      {metricsData.length > 0 && (
        <Module
          tab={t('training.trend')}
          quiet
          aux={<span>{tn('training.pointsRecorded', metricsData.length)}</span>}
        >
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={metricsData} margin={{ top: 6, right: 46, bottom: 22, left: 6 }}>
                <CartesianGrid stroke="#e6e8eb" strokeDasharray="2 2" />
                <XAxis
                  dataKey="i"
                  stroke="#6b7178"
                  fontSize={11}
                  tickLine={false}
                  axisLine={{ stroke: '#c9ccd1' }}
                >
                  <Label
                    value={t('training.logSteps')}
                    position="insideBottom"
                    offset={-14}
                    style={{ fill: '#6b7178', fontSize: 11 }}
                  />
                </XAxis>
                <YAxis
                  yAxisId="loss"
                  stroke="#6b7178"
                  fontSize={11}
                  width={46}
                  tickLine={false}
                  axisLine={{ stroke: '#c9ccd1' }}
                  domain={['auto', 'auto']}
                >
                  <Label
                    value={t('training.loss')}
                    angle={-90}
                    position="insideLeft"
                    style={{ fill: '#6b7178', fontSize: 11, textAnchor: 'middle' }}
                  />
                </YAxis>
                {hasLr && (
                  <YAxis
                    yAxisId="lr"
                    orientation="right"
                    stroke="#6b7178"
                    fontSize={11}
                    width={44}
                    tickLine={false}
                    axisLine={{ stroke: '#c9ccd1' }}
                    domain={['auto', 'auto']}
                    tickFormatter={(v: number) => v.toExponential(0)}
                  >
                    <Label
                      value={t('training.lrLabel')}
                      angle={90}
                      position="insideRight"
                      style={{ fill: '#6b7178', fontSize: 11, textAnchor: 'middle' }}
                    />
                  </YAxis>
                )}
                <Tooltip
                  contentStyle={{
                    background: '#ffffff',
                    border: '1px solid #111111',
                    borderRadius: 0,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: '#4c5259' }}
                  labelFormatter={(v) => t('training.step', { v: String(v) })}
                  formatter={(v: number, n: string) => [
                    n === 'lr' ? v.toExponential(2) : v.toFixed(4),
                    n === 'lr' ? t('training.lrLabel') : t('training.loss'),
                  ]}
                />
                <Line
                  yAxisId="loss"
                  type="monotone"
                  dataKey="loss"
                  stroke="#e60012"
                  dot={false}
                  strokeWidth={1.5}
                  isAnimationActive={false}
                />
                {hasLr && (
                  <Line
                    yAxisId="lr"
                    type="monotone"
                    dataKey="lr"
                    stroke="#111111"
                    strokeDasharray="3 2"
                    dot={false}
                    strokeWidth={1}
                    isAnimationActive={false}
                  />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">
            <span className="inline-block h-px w-4 align-middle bg-[color:var(--color-sig)]" /> {t('training.loss')}
            {hasLr && (
              <>
                {'   '}
                <span className="ml-3 inline-block h-px w-4 border-t border-dashed border-[color:var(--color-ink)] align-middle" />{' '}
                {t('training.lrLabel')}
              </>
            )}
          </p>
        </Module>
      )}

      <Module tab={t('training.logTab')} quiet flush>
        <pre className="mono max-h-80 overflow-auto bg-[color:var(--color-fill)] p-2 text-[11px] leading-relaxed">
          {status?.log_tail || t('training.noLog')}
        </pre>
      </Module>
    </div>
  )
}
