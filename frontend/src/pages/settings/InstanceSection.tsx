/**
 * La configurazione dell'istanza: come si chiama e chi può entrarci.
 *
 * La lettura è aperta a chiunque è autenticato — sapere se la registrazione
 * è aperta non è un segreto — ma la scrittura è dell'amministratore. Chi non
 * lo è vede i campi spenti *e* la riga che spiega perché: un form disabilitato
 * senza motivo scritto è un vicolo cieco.
 */
import { FormEvent, useEffect, useState } from 'react'
import { NavLink } from 'react-router'
import { apiGet, apiPut } from '../../lib/api'
import type { InstanceSettings } from '../../lib/types'
import { ErrorNotice, Field, Module, Notice, WarnNotice } from '../../app/ui'
import { useI18n } from '../../i18n'
import type { SectionProps } from './SettingsPage'

export default function InstanceSection({ isAdmin }: SectionProps) {
  const { t } = useI18n()
  const [settings, setSettings] = useState<InstanceSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    apiGet<InstanceSettings>('/settings').then(setSettings).catch(setError)
  }, [])

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!settings) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      setSettings(await apiPut<InstanceSettings>('/settings', settings))
      setSaved(true)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}

      {!isAdmin && (
        <WarnNotice title={t('settings.tabInstance')}>{t('settings.readOnly')}</WarnNotice>
      )}

      <Module tab={t('settings.instance')}>
        {settings == null ? (
          <p className="text-[13px] text-[color:var(--color-ink-2)]">{t('common.loading')}</p>
        ) : (
          <form onSubmit={save}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('settings.instanceName')} hint={t('settings.instanceNameHint')}>
                <input
                  className="fld"
                  value={settings.instance_name}
                  disabled={!isAdmin}
                  onChange={(e) => setSettings({ ...settings, instance_name: e.target.value })}
                />
              </Field>
              <Field label={t('settings.defaultRole')} hint={t('settings.defaultRoleHint')}>
                <select
                  className="fld"
                  value={settings.default_new_user_role}
                  disabled={!isAdmin}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      default_new_user_role: e.target.value as 'editor' | 'viewer',
                    })
                  }
                >
                  <option value="editor">{t('users.roleEditor')}</option>
                  <option value="viewer">{t('users.roleViewer')}</option>
                </select>
              </Field>
            </div>

            <label className="mt-3 flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[color:var(--color-sig)]"
                checked={settings.allow_registration}
                disabled={!isAdmin}
                onChange={(e) => setSettings({ ...settings, allow_registration: e.target.checked })}
              />
              {t('settings.allowRegistration')}
            </label>
            <p className="mt-1 max-w-[70ch] text-[11px] text-[color:var(--color-ink-3)]">
              {t('settings.allowRegistrationHint')}
            </p>

            {saved && <Notice tone="ok">{t('settings.saved')}</Notice>}

            {isAdmin && (
              <button type="submit" disabled={busy} className="btn btn-primary mt-3">
                {busy ? t('settings.saving') : t('settings.save')}
              </button>
            )}
          </form>
        )}
      </Module>

      {isAdmin && (
        <Module tab={t('settings.accounts')} quiet>
          <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
            {t('settings.accountsHint')}
          </p>
          <NavLink to="/utenti" className="btn mt-3">
            {t('settings.manageUsers')}
          </NavLink>
        </Module>
      )}
    </div>
  )
}
