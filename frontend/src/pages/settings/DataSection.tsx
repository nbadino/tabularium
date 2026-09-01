/**
 * Dati e backup: lo stato del database e le copie da cui si torna indietro.
 *
 * Il ripristino chiede conferma in una `Modal` del prodotto, non in un
 * `window.confirm` del browser: è l'unica azione di questa pagina che può far
 * sparire del lavoro, e il testo deve poterlo dire per intero.
 */
import { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../../lib/api'
import { Badge, Modal, Module, Notice, WarnNotice } from '../../app/ui'
import { describeError } from '../../lib/errors'
import { useI18n } from '../../i18n'
import type { SectionProps } from './SettingsPage'

interface BackupItem {
  name: string
  size: number
  modified_at: string
}

interface BackupState {
  integrity: { ok: boolean; journal_mode: string; messages: string[] }
  items: BackupItem[]
}

export default function DataSection({ isAdmin }: SectionProps) {
  const { t } = useI18n()
  const [state, setState] = useState<BackupState | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'sig'; text: string } | null>(null)
  const [confirming, setConfirming] = useState<BackupItem | null>(null)

  const load = () =>
    apiGet<BackupState>('/system/backup')
      .then(setState)
      .catch(() => setState(null))

  useEffect(() => {
    void load()
  }, [])

  const fail = (e: unknown) => setNotice({ tone: 'sig', text: describeError(e).messaggio })

  const create = async () => {
    setBusy('create')
    setNotice(null)
    try {
      const created = await apiPost<{ name: string }>('/system/backup')
      setNotice({ tone: 'ok', text: t('settings.backupCreated', { name: created.name }) })
      await load()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  const restore = async (item: BackupItem) => {
    setConfirming(null)
    setBusy(item.name)
    setNotice(null)
    try {
      await apiPost(`/system/backup/${encodeURIComponent(item.name)}/restore?confirm=true`)
      setNotice({ tone: 'ok', text: t('settings.backupRestored', { name: item.name }) })
      await load()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  const integrity = state?.integrity
  const integrityBadge = !integrity ? (
    <Badge tone="neutral">{t('settings.backupIntegrityUnknown')}</Badge>
  ) : integrity.ok ? (
    <Badge tone="ok">
      {t('settings.backupIntegrityOk', { mode: integrity.journal_mode.toUpperCase() })}
    </Badge>
  ) : (
    <Badge tone="warn">{t('settings.backupIntegrityFail')}</Badge>
  )

  return (
    <div className="space-y-3">
      {!isAdmin && (
        <WarnNotice title={t('settings.tabData')}>{t('settings.backupAdminOnly')}</WarnNotice>
      )}

      <Module tab={t('settings.backup')} aux={integrityBadge}>
        <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
          {t('settings.backupHint')}
        </p>

        {integrity && !integrity.ok && integrity.messages.length > 0 && (
          <pre className="mono mt-2 max-h-32 overflow-auto border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] whitespace-pre-wrap">
            {integrity.messages.join('\n')}
          </pre>
        )}

        {notice && <Notice tone={notice.tone}>{notice.text}</Notice>}

        {isAdmin && (
          <button
            type="button"
            className="btn btn-primary mt-3"
            disabled={busy != null}
            onClick={() => void create()}
          >
            {busy === 'create' ? t('settings.backupCreating') : t('settings.backupCreate')}
          </button>
        )}
      </Module>

      {isAdmin && (
        <Module tab={t('settings.backupList')} quiet flush>
          <div className="divide-y divide-[color:var(--color-rule)]">
            {(state?.items ?? []).map((item) => (
              <div key={item.name} className="flex flex-wrap items-center gap-2 px-3 py-2 text-[12px]">
                <span className="mono truncate">{item.name}</span>
                <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
                  {Math.round(item.size / 1024)} KB
                </span>
                <span className="ml-auto flex items-center gap-2">
                  <a className="btn btn-sm" href={`/api/system/backup/${encodeURIComponent(item.name)}`}>
                    {t('settings.backupDownload')}
                  </a>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy != null}
                    onClick={() => setConfirming(item)}
                  >
                    {busy === item.name ? t('settings.backupRestoring') : t('settings.backupRestore')}
                  </button>
                </span>
              </div>
            ))}
            {(state?.items ?? []).length === 0 && (
              <p className="px-3 py-2 text-[12px] text-[color:var(--color-ink-3)]">
                {t('settings.backupEmpty')}
              </p>
            )}
          </div>
        </Module>
      )}

      {confirming && (
        <Modal
          title={t('settings.backupRestoreTitle')}
          onClose={() => setConfirming(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setConfirming(null)}>
                {t('common.close')}
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void restore(confirming)}
              >
                {t('settings.backupRestore')}
              </button>
            </>
          }
        >
          <p className="max-w-[62ch] p-3 text-[13px]">
            {t('settings.backupRestoreBody', { name: confirming.name })}
          </p>
        </Modal>
      )}
    </div>
  )
}
