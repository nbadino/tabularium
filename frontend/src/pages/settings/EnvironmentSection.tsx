/**
 * Ambiente: le versioni e i percorsi che servono quando qualcosa non torna.
 *
 * Non c'è niente da cambiare qui — è la targhetta della macchina, e si legge
 * come tale: etichetta a sinistra, valore in voce macchina a destra.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../../lib/api'
import type { HealthResponse, SystemInfo } from '../../lib/types'
import { Module } from '../../app/ui'
import { useI18n } from '../../i18n'

export default function EnvironmentSection() {
  const { t } = useI18n()
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [health, setHealth] = useState<HealthResponse | null>(null)

  useEffect(() => {
    apiGet<SystemInfo>('/system/info').then(setInfo).catch(() => {})
    apiGet<HealthResponse>('/health').then(setHealth).catch(() => {})
  }, [])

  const rows: Array<[string, string]> = [
    [t('settings.envBackend'), health?.status ?? '—'],
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
    <Module tab={t('settings.env')} quiet aux={<span>{t('settings.envLocal')}</span>}>
      <dl className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex min-w-0 gap-2">
            <dt className="lbl !mb-0 shrink-0 pt-px">{label}</dt>
            <dd className="mono min-w-0 flex-1 truncate text-[12px]" title={value}>
              {value}
            </dd>
          </div>
        ))}
      </dl>
    </Module>
  )
}
