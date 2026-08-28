/**
 * Impostazioni dell'istanza (self-hosted).
 *
 * Solo l'amministratore le modifica (PUT), ma la lettura (GET) è aperta a chi
 * è autenticato: la schermata mostra i campi editabili solo per l'admin.
 */
import { FormEvent, useEffect, useState } from 'react'
import { apiGet, apiPut } from '../lib/api'
import type { InstanceSettings, User } from '../lib/types'
import { ErrorNotice, Field, Module } from '../app/ui'
import { useAuth } from '../app/auth'
import { useI18n } from '../i18n'

export default function SettingsPage() {
  const { t } = useI18n()
  const { user } = useAuth()
  const [settings, setSettings] = useState<InstanceSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  const load = () =>
    apiGet<InstanceSettings>('/settings')
      .then((s) => {
        setSettings(s)
        setSaved(false)
      })
      .catch(setError)

  useEffect(() => {
    void load()
  }, [])

  const isAdmin = (user as User | null)?.role === 'admin'

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!settings) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const updated = await apiPut<InstanceSettings>('/settings', settings)
      setSettings(updated)
      setSaved(true)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('settings.title')}
        </h1>
        <p className="mt-1 max-w-[78ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('settings.intro')}
        </p>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {settings == null ? (
        <p className="text-[13px] text-[color:var(--color-ink-2)]">{t('common.loading')}</p>
      ) : (
        <Module tab={t('settings.general')}>
          <form onSubmit={save}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('settings.instanceName')} hint={t('settings.instanceNameHint')}>
                <input
                  value={settings.instance_name}
                  onChange={(e) =>
                    setSettings({ ...settings, instance_name: e.target.value })
                  }
                  disabled={!isAdmin}
                  className="fld"
                />
              </Field>
              <Field
                label={t('settings.defaultRole')}
                hint={t('settings.defaultRoleHint')}
              >
                <select
                  value={settings.default_new_user_role}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      default_new_user_role: e.target.value as 'editor' | 'viewer',
                    })
                  }
                  disabled={!isAdmin}
                  className="fld"
                >
                  <option value="editor">{t('users.roleEditor')}</option>
                  <option value="viewer">{t('users.roleViewer')}</option>
                </select>
              </Field>
            </div>

            <label className="mt-3 flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                checked={settings.allow_registration}
                onChange={(e) =>
                  setSettings({ ...settings, allow_registration: e.target.checked })
                }
                disabled={!isAdmin}
                className="h-4 w-4 accent-[color:var(--color-sig)]"
              />
              {t('settings.allowRegistration')}
            </label>
            <p className="mt-1 max-w-[70ch] text-[11px] text-[color:var(--color-ink-3)]">
              {t('settings.allowRegistrationHint')}
            </p>

            {saved && (
              <p role="status" className="mt-3 text-[12px] text-[color:var(--color-ok)]">
                {t('settings.saved')}
              </p>
            )}

            {isAdmin && (
              <button type="submit" disabled={busy} className="btn btn-primary mt-3">
                {busy ? t('common.loading') : t('settings.save')}
              </button>
            )}
          </form>
        </Module>
      )}
    </div>
  )
}
