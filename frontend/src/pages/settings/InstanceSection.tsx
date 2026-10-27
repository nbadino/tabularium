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
import { ErrorNotice, Field, Module, Notice, SkeletonLines, WarnNotice } from '../../app/ui'
import { useI18n } from '../../i18n'
import { useAuth } from '../../app/auth'
import type { SectionProps } from './SettingsPage'

/** An older payload (or a test double) may predate the OIDC fields. */
function withOidcDefaults(value: InstanceSettings): InstanceSettings {
  return {
    ...value,
    oidc_enabled: value.oidc_enabled ?? false,
    oidc_issuer: value.oidc_issuer ?? '',
    oidc_client_id: value.oidc_client_id ?? '',
    oidc_scopes: value.oidc_scopes ?? 'openid profile email',
    oidc_allowed_domains: value.oidc_allowed_domains ?? [],
    oidc_button_label: value.oidc_button_label ?? 'SSO',
    oidc_client_secret_set: value.oidc_client_secret_set ?? false,
  }
}

export default function InstanceSection({ isAdmin }: SectionProps) {
  // Senza account (modalità locale) registrazione, ruolo predefinito e
  // gestione utenti non riguardano nessuno: si mostra solo il nome.
  const accounts = useAuth().enabled
  const { t } = useI18n()
  const [settings, setSettings] = useState<InstanceSettings | null>(null)
  const [baseline, setBaseline] = useState<InstanceSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // The secret is write-only and the domains are edited as text: neither is part of
  // the settings object the API returns.
  const [secret, setSecret] = useState('')
  const [clearSecret, setClearSecret] = useState(false)
  const [domains, setDomains] = useState('')

  useEffect(() => {
    apiGet<InstanceSettings>('/settings')
      .then((raw) => {
        const value = withOidcDefaults(raw)
        setSettings(value)
        setBaseline(value)
        setDomains(value.oidc_allowed_domains.join(', '))
      })
      .catch(setError)
  }, [])

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!settings) return
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      const payload: Record<string, unknown> = {
        instance_name: settings.instance_name.trim(),
        allow_registration: settings.allow_registration,
        default_new_user_role: settings.default_new_user_role,
        oidc_enabled: settings.oidc_enabled,
        oidc_issuer: settings.oidc_issuer.trim(),
        oidc_client_id: settings.oidc_client_id.trim(),
        oidc_scopes: settings.oidc_scopes,
        oidc_button_label: settings.oidc_button_label,
        oidc_allowed_domains: domains.split(',').map((item) => item.trim()).filter(Boolean),
      }
      if (secret.trim()) payload.oidc_client_secret = secret.trim()
      if (clearSecret) payload.oidc_client_secret_clear = true
      const next = await apiPut<InstanceSettings>('/settings', payload)
      setSettings(next)
      setBaseline(next)
      setDomains(next.oidc_allowed_domains.join(', '))
      setSecret('')
      setClearSecret(false)
      setSaved(true)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const changed = settings != null && baseline != null
    && (
      JSON.stringify(settings) !== JSON.stringify(baseline)
      || domains !== baseline.oidc_allowed_domains.join(', ')
      || secret.trim() !== ''
      || clearSecret
    )
  const oidcReady = !settings?.oidc_enabled
    || Boolean(settings.oidc_issuer.trim() && settings.oidc_client_id.trim())
  const valid = Boolean(settings?.instance_name.trim()) && oidcReady

  return (
    <div className="space-y-3">
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}

      {!isAdmin && (
        <WarnNotice title={t('settings.tabInstance')}>{t('settings.readOnly')}</WarnNotice>
      )}

      <Module tab={t('settings.instance')}>
        {settings == null ? (
          <SkeletonLines lines={4} label={t('common.loading')} />
        ) : (
          <form onSubmit={save}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('settings.instanceName')} hint={t('settings.instanceNameHint')}>
                <input
                  className="fld"
                  value={settings.instance_name}
                  maxLength={100}
                  required
                  aria-invalid={!valid}
                  disabled={!isAdmin}
                  onChange={(e) => {
                    setSaved(false)
                    setSettings({ ...settings, instance_name: e.target.value })
                  }}
                />
                {!valid && <p className="mt-1 text-[11px] text-[color:var(--color-sig-text)]">{t('settings.instanceNameRequired')}</p>}
              </Field>
              {accounts && <Field label={t('settings.defaultRole')} hint={t('settings.defaultRoleHint')}>
                <select
                  className="fld"
                  value={settings.default_new_user_role}
                  disabled={!isAdmin}
                  onChange={(e) => {
                    setSaved(false)
                    setSettings({
                      ...settings,
                      default_new_user_role: e.target.value as 'editor' | 'viewer',
                    })
                  }}
                >
                  <option value="editor">{t('users.roleEditor')}</option>
                  <option value="viewer">{t('users.roleViewer')}</option>
                </select>
              </Field>}
            </div>

            {accounts ? <>
            <label className="mt-3 flex items-center gap-2 text-[13px]">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[color:var(--color-sig)]"
                checked={settings.allow_registration}
                disabled={!isAdmin}
                onChange={(e) => {
                  setSaved(false)
                  setSettings({ ...settings, allow_registration: e.target.checked })
                }}
              />
              {t('settings.allowRegistration')}
            </label>
            <p className="mt-1 max-w-[70ch] text-[11px] text-[color:var(--color-ink-3)]">
              {t('settings.allowRegistrationHint')}
            </p>
            </> : (
              <p className="mt-3 max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">{t('settings.localNoAccounts')}</p>
            )}

            {accounts && (
              <fieldset className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
                <legend className="text-[12px] font-bold uppercase tracking-wide">{t('settings.oidcTitle')}</legend>
                <p className="mb-3 mt-1 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t('settings.oidcIntro')}</p>
                <label className="flex items-center gap-2 text-[13px]">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-[color:var(--color-sig)]"
                    checked={settings.oidc_enabled}
                    disabled={!isAdmin}
                    onChange={(e) => {
                      setSaved(false)
                      setSettings({ ...settings, oidc_enabled: e.target.checked })
                    }}
                  />
                  {t('settings.oidcEnabled')}
                </label>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <Field label={t('settings.oidcIssuer')} hint={t('settings.oidcIssuerHint')}>
                    <input
                      className="fld"
                      value={settings.oidc_issuer}
                      placeholder="https://idp.universita.it"
                      disabled={!isAdmin}
                      onChange={(e) => {
                        setSaved(false)
                        setSettings({ ...settings, oidc_issuer: e.target.value })
                      }}
                    />
                  </Field>
                  <Field label={t('settings.oidcClientId')}>
                    <input
                      className="fld"
                      value={settings.oidc_client_id}
                      disabled={!isAdmin}
                      onChange={(e) => {
                        setSaved(false)
                        setSettings({ ...settings, oidc_client_id: e.target.value })
                      }}
                    />
                  </Field>
                  <Field label={t('settings.oidcClientSecret')} hint={t('settings.oidcClientSecretHint')}>
                    <input
                      className="fld"
                      type="password"
                      autoComplete="new-password"
                      value={secret}
                      placeholder={settings.oidc_client_secret_set ? '••••••••' : ''}
                      disabled={!isAdmin}
                      onChange={(e) => {
                        setSaved(false)
                        setSecret(e.target.value)
                      }}
                    />
                    {settings.oidc_client_secret_set && (
                      <label className="mt-1 flex items-center gap-2 text-[12px]">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-[color:var(--color-sig)]"
                          checked={clearSecret}
                          disabled={!isAdmin}
                          onChange={(e) => {
                            setSaved(false)
                            setClearSecret(e.target.checked)
                          }}
                        />
                        {t('settings.oidcSecretClear')}
                      </label>
                    )}
                  </Field>
                  <Field label={t('settings.oidcScopes')} hint={t('settings.oidcScopesHint')}>
                    <input
                      className="fld"
                      value={settings.oidc_scopes}
                      disabled={!isAdmin}
                      onChange={(e) => {
                        setSaved(false)
                        setSettings({ ...settings, oidc_scopes: e.target.value })
                      }}
                    />
                  </Field>
                  <Field label={t('settings.oidcDomains')} hint={t('settings.oidcDomainsHint')}>
                    <input
                      className="fld"
                      value={domains}
                      placeholder="uni.it, ateneo.it"
                      disabled={!isAdmin}
                      onChange={(e) => {
                        setSaved(false)
                        setDomains(e.target.value)
                      }}
                    />
                  </Field>
                  <Field label={t('settings.oidcButtonLabel')} hint={t('settings.oidcButtonLabelHint')}>
                    <input
                      className="fld"
                      value={settings.oidc_button_label}
                      maxLength={40}
                      disabled={!isAdmin}
                      onChange={(e) => {
                        setSaved(false)
                        setSettings({ ...settings, oidc_button_label: e.target.value })
                      }}
                    />
                  </Field>
                </div>
                {!oidcReady && (
                  <p className="mt-2 text-[11px] text-[color:var(--color-sig-text)]">{t('settings.oidcIncomplete')}</p>
                )}
              </fieldset>
            )}

            {saved && <Notice tone="ok">{t('settings.saved')}</Notice>}

            {isAdmin && (
              <button type="submit" disabled={busy || !changed || !valid} className="btn btn-primary mt-3">
                {busy ? t('settings.saving') : t('settings.save')}
              </button>
            )}
          </form>
        )}
      </Module>

      {isAdmin && accounts && (
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
