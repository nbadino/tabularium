/**
 * Impostazioni dell'istanza (self-hosted).
 *
 * Solo l'amministratore le modifica (PUT), ma la lettura (GET) è aperta a chi
 * è autenticato: la schermata mostra i campi editabili solo per l'admin.
 */
import { FormEvent, useEffect, useState } from 'react'
import { apiGet, apiPost, apiPut } from '../lib/api'
import type { ComputeProfile, InstanceSettings, User } from '../lib/types'
import { Badge, ErrorNotice, Field, Module } from '../app/ui'
import { useAuth } from '../app/auth'
import { LOCALES, LOCALE_LABELS, useI18n } from '../i18n'
import { useInference, saveInferenceToBackend, testInferenceConnection } from '../app/inference'
import {
  guessModalTemplate,
  MODAL_TEMPLATE_TARGET,
  MODAL_TEMPLATES,
  PREFILL_MODAL_TEMPLATES,
} from '../app/CloudControlModal'

interface ModalTemplateStatus {
  app_state: string | null
  endpoint: string | null
}

export default function SettingsPage() {
  const { locale, setLocale, t } = useI18n()
  const { user } = useAuth()
  const inf = useInference()
  const [settings, setSettings] = useState<InstanceSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const [backup, setBackup] = useState<{ integrity: { ok: boolean; journal_mode: string; messages: string[] }; items: Array<{ name: string; size: number; modified_at: string }> } | null>(null)
  const [backupBusy, setBackupBusy] = useState(false)
  const [backupNotice, setBackupNotice] = useState<string | null>(null)
  const [profiles, setProfiles] = useState<ComputeProfile[]>([])
  const [profileNotice, setProfileNotice] = useState<string | null>(null)

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

  useEffect(() => {
    apiGet<typeof backup>('/system/backup').then(setBackup).catch(() => setBackup(null))
    apiGet<ComputeProfile[]>('/system/compute-profiles').then(setProfiles).catch(() => setProfiles([]))
  }, [])

  const activateProfile = async (id: number) => {
    setProfileNotice(null)
    try {
      const active = await apiPost<ComputeProfile>(`/system/compute-profiles/${id}/activate`)
      setProfiles((current) => current.map((profile) => ({ ...profile, active: profile.id === active.id })))
      setProfileNotice(`Profilo attivo: ${active.name}`)
    } catch (e) { setProfileNotice(String(e)) }
  }

  const createBackup = async () => {
    setBackupBusy(true)
    setBackupNotice(null)
    try {
      const created = await apiPost<{ name: string }>('/system/backup')
      setBackupNotice(`Backup creato: ${created.name}`)
      setBackup(await apiGet<typeof backup>('/system/backup'))
    } catch (e) { setBackupNotice(String(e)) } finally { setBackupBusy(false) }
  }

  const restoreBackup = async (name: string) => {
    if (!window.confirm(`Ripristinare ${name}? Verrà creato prima un backup dello stato attuale.`)) return
    setBackupBusy(true)
    try {
      await apiPost(`/system/backup/${encodeURIComponent(name)}/restore?confirm=true`)
      setBackupNotice(`Ripristino completato: ${name}`)
      setBackup(await apiGet<typeof backup>('/system/backup'))
    } catch (e) { setBackupNotice(String(e)) } finally { setBackupBusy(false) }
  }

  // --- Modello per il prefill: quali template Modal risultano deployate --------
  const [modalStates, setModalStates] = useState<Record<string, ModalTemplateStatus>>({})
  const [modelBusy, setModelBusy] = useState<string | null>(null)
  const [modelNotice, setModelNotice] = useState<string | null>(null)
  const activeTemplate = guessModalTemplate(inf.url)

  useEffect(() => {
    let stop = false
    void Promise.all(
      PREFILL_MODAL_TEMPLATES.map((id) =>
        apiGet<ModalTemplateStatus>(`/system/cloud/modal?template=${id}`)
          .then((res) => [id, res] as const)
          .catch(() => [id, { app_state: null, endpoint: null }] as const),
      ),
    ).then((pairs) => {
      if (!stop) setModalStates(Object.fromEntries(pairs))
    })
    return () => {
      stop = true
    }
  }, [])

  const useModelForPrefill = async (id: string) => {
    const status = modalStates[id]
    const target = MODAL_TEMPLATE_TARGET[id]
    if (!status?.endpoint || !target) return
    setModelBusy(id)
    setModelNotice(null)
    try {
      const url = status.endpoint.replace(/\/$/, '') + '/v1'
      await saveInferenceToBackend({ enabled: true, url, model: target.model, adapterId: target.adapterId })
      await testInferenceConnection({ url, model: target.model })
      setModelNotice(t('settings.prefillModelSet', { name: MODAL_TEMPLATES.find((m) => m.id === id)?.label ?? id }))
    } catch (e) {
      setModelNotice(String(e))
    } finally {
      setModelBusy(null)
    }
  }

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

      {user && <div className="mb-3">
        <Module tab={t('settings.account')}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('settings.accountUser')}>
              <div className="fld bg-[color:var(--color-fill)]" aria-label={t('settings.accountUser')}>{user.username}</div>
            </Field>
            <Field label={t('settings.accountRole')}>
              <div className="fld bg-[color:var(--color-fill)]">{t(`users.role${user.role.charAt(0).toUpperCase()}${user.role.slice(1)}`)}</div>
            </Field>
            <Field label={t('settings.accountEmail')}>
              <div className="fld min-h-[2.25rem] bg-[color:var(--color-fill)]">{user.email || '—'}</div>
            </Field>
            <Field label={t('settings.language')} hint={t('settings.languageHint')}>
              <select className="fld" value={locale} onChange={(e) => setLocale(e.target.value as typeof locale)}>
                {LOCALES.map((item) => <option key={item} value={item}>{LOCALE_LABELS[item]}</option>)}
              </select>
            </Field>
          </div>
        </Module>
      </div>}

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

      <div className="mt-3">
        <Module tab="Profili compute">
          <p className="text-[12px] text-[color:var(--color-ink-2)]">Endpoint, modello e adapter vengono cambiati insieme e diventano attivi solo dopo un health check riuscito.</p>
          {profileNotice && <p role="status" className="mt-2 text-[12px]">{profileNotice}</p>}
          <div className="mt-3 divide-y divide-[color:var(--color-rule)] border-y border-[color:var(--color-rule)]">
            {profiles.map((profile) => <div key={profile.id} className="flex flex-wrap items-center gap-2 py-2 text-[12px]">
              <span className="font-semibold">{profile.name}</span><span className="badge">{profile.provider}</span><span className="mono text-[11px] text-[color:var(--color-ink-2)]">{profile.served_model_name} · {profile.model_adapter_id}</span>
              {profile.active ? <span className="ml-auto text-[color:var(--color-ok)]">Attivo</span> : isAdmin ? <button type="button" className="btn btn-sm ml-auto" onClick={() => void activateProfile(profile.id)}>Attiva e verifica</button> : null}
            </div>)}
            {!profiles.length && <p className="py-2 text-[12px] text-[color:var(--color-ink-2)]">Nessun profilo configurato.</p>}
          </div>
        </Module>
      </div>

      <div className="mt-3">
        <Module tab="Dati e backup">
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className={backup?.integrity.ok ? 'text-[color:var(--color-ok)]' : 'text-[color:var(--color-sig-text)]'}>
              {backup?.integrity.ok ? `Database integro · ${backup.integrity.journal_mode.toUpperCase()}` : 'Integrità da verificare'}
            </span>
            {isAdmin && <button type="button" className="btn btn-sm ml-auto" disabled={backupBusy} onClick={() => void createBackup()}>Crea backup</button>}
          </div>
          {backupNotice && <p role="status" className="mt-2 text-[12px]">{backupNotice}</p>}
          {isAdmin && backup?.items.length ? <div className="mt-3 divide-y divide-[color:var(--color-rule)] border-y border-[color:var(--color-rule)]">{backup.items.slice(0, 5).map((item) => <div key={item.name} className="flex flex-wrap items-center gap-2 py-2 text-[11px]"><span className="mono">{item.name}</span><span className="text-[color:var(--color-ink-3)]">{Math.round(item.size / 1024)} KB</span><a className="ml-auto" href={`/api/system/backup/${encodeURIComponent(item.name)}`}>Scarica</a><button type="button" className="btn btn-sm" disabled={backupBusy} onClick={() => void restoreBackup(item.name)}>Ripristina</button></div>)}</div> : null}
        </Module>
      </div>

      {isAdmin && <div className="mt-3">
        <Module tab={t('settings.prefillModel')}>
          <p className="max-w-[70ch] text-[13px] text-[color:var(--color-ink-2)]">
            {t('settings.prefillModelIntro')}
          </p>

          {modelNotice && (
            <p role="status" className="mt-2 text-[12px] text-[color:var(--color-ink)]">
              {modelNotice}
            </p>
          )}

          <div className="mt-3 divide-y divide-[color:var(--color-rule)] border border-[color:var(--color-rule)]">
            {PREFILL_MODAL_TEMPLATES.map((id) => {
              const meta = MODAL_TEMPLATES.find((m) => m.id === id)
              const status = modalStates[id]
              const deployed = status?.app_state === 'deployed' && !!status.endpoint
              const isActive = activeTemplate === id
              return (
                <div key={id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold">{meta?.label ?? id}</span>
                      {isActive && <Badge tone="ok">{t('settings.prefillModelActiveBadge')}</Badge>}
                      {status && (
                        <Badge tone={deployed ? 'ok' : 'neutral'}>
                          {deployed
                            ? t('settings.prefillModelDeployed')
                            : t('settings.prefillModelNotDeployed')}
                        </Badge>
                      )}
                    </div>
                    {!deployed && status && (
                      <p className="mt-0.5 text-[11px] text-[color:var(--color-ink-3)]">
                        {t('settings.prefillModelDeployHint')}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => void useModelForPrefill(id)}
                    disabled={!deployed || isActive || modelBusy === id}
                    className="btn btn-sm btn-primary"
                  >
                    {modelBusy === id ? t('common.loading') : t('settings.prefillModelUse')}
                  </button>
                </div>
              )
            })}
          </div>
        </Module>
      </div>}
    </div>
  )
}
