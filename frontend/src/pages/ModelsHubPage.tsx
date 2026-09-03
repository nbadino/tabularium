import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Badge, ErrorNotice, Module } from '../app/ui'
import { syncInferenceFromBackend, useInference } from '../app/inference'
import { useAuth } from '../app/auth'
import { ModelsModal } from '../app/ModelsModal'
import { CloudControlModal } from '../app/CloudControlModal'
import { apiGet, apiPost } from '../lib/api'
import type { ComputeProfile } from '../lib/types'
import { useI18n } from '../i18n'

export default function ModelsHubPage() {
  const { t } = useI18n()
  const inference = useInference()
  const auth = useAuth()
  const canManage = !auth.enabled || auth.user?.role === 'admin'
  const [profiles, setProfiles] = useState<ComputeProfile[]>([])
  const [modelsOpen, setModelsOpen] = useState(false)
  const [providersOpen, setProvidersOpen] = useState(false)
  /** Scheda provider da aprire quando l'apertura nasce da «Deploya qui». */
  const [focusProvider, setFocusProvider] = useState<'vast' | 'runpod' | 'modal' | 'manual' | null>(null)
  const [focusAdapterId, setFocusAdapterId] = useState<string | null>(null)
  const [focusModelLabel, setFocusModelLabel] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    void Promise.all([
      syncInferenceFromBackend(),
      apiGet<ComputeProfile[]>('/system/compute-profiles').then(setProfiles),
    ]).catch(setError)
  }, [])

  const activeProfile = profiles.find((profile) => profile.active) ?? null
  const destination = activeProfile?.provider ?? inference.provider ?? 'local'

  const activate = async (profile: ComputeProfile) => {
    if (!canManage || profile.active) return
    setBusy(profile.id)
    setError(null)
    try {
      const active = await apiPost<ComputeProfile>(`/system/compute-profiles/${profile.id}/activate`)
      setProfiles((before) => before.map((item) => ({ ...item, active: item.id === active.id })))
      await syncInferenceFromBackend()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  /** «Deploya su <provider>» dalla libreria: apre il pannello del provider
   *  con il modello già scelto, così la selezione non si rifà da capo. */
  const handleDeploy = (adapterId: string, displayName: string) => {
    setFocusAdapterId(adapterId)
    setFocusModelLabel(displayName)
    setFocusProvider(
      destination === 'vast' || destination === 'runpod' || destination === 'modal'
        ? destination
        : 'manual',
    )
    setModelsOpen(false)
    setProvidersOpen(true)
  }

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">{t('nav.models')}</h1>
        <p className="mt-1 max-w-[72ch] text-[13px] text-[color:var(--color-ink-2)]">{t('recognition.modelsIntro')}</p>
      </div>

      {error != null && <div className="mb-3"><ErrorNotice error={error} /></div>}

      <div className="mb-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(280px,0.45fr)]">
        <Module
          tab={t('recognition.activeModel')}
          aux={<Badge tone={inference.enabled && inference.available ? 'ok' : 'warn'}>{inference.enabled ? t('recognition.modelReady') : t('recognition.modelOff')}</Badge>}
        >
          <div className="text-[18px] font-bold">{inference.model || '—'}</div>
          <div className="mono mt-1 truncate text-[11px] text-[color:var(--color-ink-3)]">
            {inference.adapterId} · {t(`recognition.provider.${destination}`)}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn btn-primary" onClick={() => setModelsOpen(true)}>{t('modelsHub.modelLibrary')}</button>
            <button type="button" className="btn" onClick={() => setProvidersOpen(true)}>{t('modelsHub.executionProviders')}</button>
          </div>
        </Module>

        <Module tab={t('modelsHub.improveModel')} quiet>
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('recognition.trainingBody')}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link to="/training" className="btn btn-primary no-underline">{t('nav.training')}</Link>
            <Link to="/valutazione" className="btn no-underline">{t('nav.evaluation')}</Link>
          </div>
        </Module>
      </div>

      <Module tab={t('modelsHub.executionProfiles')} quiet flush>
        {profiles.length === 0 ? (
          <div className="p-4 text-[12px] text-[color:var(--color-ink-3)]">{t('modelsHub.noExecutionProfiles')}</div>
        ) : (
          <div className="divide-y divide-[color:var(--color-rule)]">
            {profiles.map((profile) => (
              <div key={profile.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <b className="text-[13px]">{profile.name}</b>
                    <Badge>{t(`recognition.provider.${profile.provider}`)}</Badge>
                  </div>
                  <div className="mono mt-1 truncate text-[11px] text-[color:var(--color-ink-3)]">
                    {profile.served_model_name} · {profile.endpoint}
                  </div>
                </div>
                {profile.active ? (
                  <Badge tone="ok">{t('modelsHub.profileActive')}</Badge>
                ) : (
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={!canManage || busy != null}
                    onClick={() => void activate(profile)}
                  >
                    {busy === profile.id ? t('common.loading') : t('modelsHub.activateProfile')}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Module>

      <ModelsModal
        open={modelsOpen}
        onClose={() => setModelsOpen(false)}
        activeProvider={destination}
        onDeploy={handleDeploy}
        onChangeDestination={() => { setModelsOpen(false); setProvidersOpen(true) }}
      />
      <CloudControlModal
        open={providersOpen}
        onClose={() => { setProvidersOpen(false); setFocusProvider(null); setFocusAdapterId(null); setFocusModelLabel(null) }}
        focusProvider={focusProvider}
        focusAdapterId={focusAdapterId}
        focusModelLabel={focusModelLabel}
      />
    </div>
  )
}
