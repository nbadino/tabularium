/**
 * Ambiente: le versioni e i percorsi che servono quando qualcosa non torna.
 *
 * Non c'è niente da cambiare qui — è la targhetta della macchina, e si legge
 * come tale: etichetta a sinistra, valore in voce macchina a destra.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api'
import type { HealthResponse, SystemInfo } from '../../lib/types'
import { Badge, ErrorNotice, Module } from '../../app/ui'
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

  const rows: Array<[string, string]> = [
    [t('settings.envApp'), health?.version ?? info?.version ?? '—'],
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
