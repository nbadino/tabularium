/**
 * Ambiente: le versioni e i percorsi che servono quando qualcosa non torna.
 *
 * Non c'è niente da cambiare qui — è la targhetta della macchina, e si legge
 * come tale: etichetta a sinistra, valore in voce macchina a destra.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api'
import type { HealthResponse, SystemInfo, UpdateStatus } from '../../lib/types'
import { Badge, ErrorNotice, Module, Notice } from '../../app/ui'
import { useAuth } from '../../app/auth'
import { useI18n } from '../../i18n'

export default function EnvironmentSection() {
  const { t } = useI18n()
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    setError(null)
    void Promise.all([
      apiGet<SystemInfo>('/system/info'),
      apiGet<HealthResponse>('/health'),
    ]).then(([nextInfo, nextHealth]) => {
      setInfo(nextInfo)
      setHealth(nextHealth)
    }).catch(setError).finally(() => setLoading(false))
  }

  useEffect(load, [])

  // Only administrators install releases, and only they may ask the server.
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [update, setUpdate] = useState<UpdateStatus | null>(null)
  const loadUpdate = (refresh = false) => {
    if (!isAdmin) return
    apiGet<UpdateStatus>(`/system/update${refresh ? '?refresh=true' : ''}`).then(setUpdate).catch(() => {})
  }
  useEffect(() => loadUpdate(), [isAdmin])

  const latestLabel = !update
    ? null
    : !update.enabled
      ? t('settings.envUpdateCheckOff')
      : update.latest == null
        ? t('settings.envUpdateUnknown')
        : update.update_available
          ? update.latest
          : `${update.latest} · ${t('settings.envUpToDate')}`

  const rows: Array<[string, string]> = [
    [t('settings.envApp'), health?.version ?? info?.version ?? '—'],
    ...(latestLabel ? [[t('settings.envLatest'), latestLabel] as [string, string]] : []),
    [t('settings.envSchema'), info?.schema_version ?? '—'],
    [t('settings.envData'), info?.data_dir ?? '—'],
    [t('settings.envPython'), info?.python ?? '—'],
    [t('settings.envPlatform'), info?.platform ?? '—'],
    [t('settings.envDashboard'), info?.capabilities?.dashboard === undefined ? '—' : info.capabilities.dashboard ? t('settings.yes') : t('settings.no')],
    [t('settings.envCpuOcr'), info?.capabilities?.cpu_ocr === undefined ? '—' : info.capabilities.cpu_ocr ? t('settings.yes') : t('settings.no')],
    [t('settings.envLocalCuda'), info?.capabilities?.local_cuda === undefined ? '—' : info.capabilities.local_cuda ? t('settings.yes') : (info.capabilities.cuda_note ?? t('settings.no'))],
    [t('settings.envRemoteGpu'), info?.capabilities?.remote_gpu === undefined ? '—' : info.capabilities.remote_gpu ? t('settings.yes') : t('settings.no')],
  ]

  return (
    <div className="space-y-3">
      {error != null && (
        <div>
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
          <button type="button" className="btn mt-2" onClick={load}>{t('common.retry')}</button>
        </div>
      )}
      <Module
        tab={t('settings.env')}
        quiet
        aux={<Badge tone={health?.status === 'ok' ? 'ok' : loading ? 'progress' : 'warn'}>{loading ? t('common.loading') : health?.status === 'ok' ? t('settings.envOnline') : t('settings.envUnavailable')}</Badge>}
      >
        <p className="mb-3 max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">{t('settings.envHint')}</p>
        {update?.update_available && update.latest && (
          <div className="mb-3">
            <Notice tone="sig">
              <strong>{t('settings.envUpdateTitle', { version: update.latest })}</strong>{' '}
              {t('settings.envUpdateBody', { current: update.current })}{' '}
              {update.url && <><a href={update.url} target="_blank" rel="noreferrer">{t('settings.envUpdateNotes')}</a>{' · '}</>}
              <a href={update.guide_url} target="_blank" rel="noreferrer">{t('settings.envUpdateGuide')}</a>
            </Notice>
          </div>
        )}
        {isAdmin && update?.enabled && (
          <button type="button" className="btn mb-3" onClick={() => loadUpdate(true)}>{t('settings.envUpdateRecheck')}</button>
        )}
        <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
          {rows.map(([label, value]) => (
            <div key={label} className="min-w-0 border-t border-[color:var(--color-rule)] pt-2">
              <dt className="lbl">{label}</dt>
              <dd className="mono min-w-0 truncate text-[12px]" title={value}>{value}</dd>
            </div>
          ))}
        </dl>
      </Module>
    </div>
  )
}
