/**
 * Modello e calcolo: una zona per una domanda sola — «quale modello risponde
 * adesso, e come lo cambio».
 *
 * Prima la stessa configurazione si scriveva da tre posti diversi, impilati
 * nella stessa pagina: la card inferenza (endpoint a mano), i profili compute
 * (endpoint + modello + adapter con health check) e i modelli Modal deployati
 * («usa per il prefill»). Tre gesti diversi per lo stesso effetto, e nessuno
 * che dichiarasse quale fosse in vigore.
 *
 * Qui c'è **uno stato dichiarato** — «in uso ora», con la sua provenienza —
 * e sotto le **sorgenti** che possono prenderne il posto, tutte attivate con
 * lo stesso gesto e lo stesso significato. La forma manuale resta, ma dietro
 * un soffietto: è il caso raro, non il pannello che accoglie.
 */
import { useEffect, useState } from 'react'
import {
  INFERENCE_PRESETS,
  saveInferenceToBackend,
  syncInferenceFromBackend,
  testInferenceConnection,
  useInference,
} from '../../app/inference'
import {
  guessModalTemplate,
  MODAL_TEMPLATES,
  MODAL_TEMPLATE_TARGET,
  PREFILL_MODAL_TEMPLATES,
  CloudControlModal,
} from '../../app/CloudControlModal'
import { ModelsModal } from '../../app/ModelsModal'
import { Badge, Collapsible, Field, Module, Notice } from '../../app/ui'
import { describeError } from '../../lib/errors'
import { apiGet, apiPost } from '../../lib/api'
import type { ComputeProfile } from '../../lib/types'
import { useI18n } from '../../i18n'
import type { SectionProps } from './SettingsPage'

interface ModalTemplateStatus {
  app_state: string | null
  endpoint: string | null
}

/** Un avviso è una frase, non un'eccezione: qui passa tutto ciò che si mostra. */
type Feedback = { tone: 'ok' | 'sig'; text: string } | null

export default function ComputeSection({ isAdmin }: SectionProps) {
  const { t } = useI18n()
  const inf = useInference()
  const [profiles, setProfiles] = useState<ComputeProfile[]>([])
  const [modalStates, setModalStates] = useState<Record<string, ModalTemplateStatus>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<Feedback>(null)
  const [cloudOpen, setCloudOpen] = useState(false)
  const [modelsOpen, setModelsOpen] = useState(false)

  useEffect(() => {
    void syncInferenceFromBackend()
    apiGet<ComputeProfile[]>('/system/compute-profiles').then(setProfiles).catch(() => setProfiles([]))
  }, [])

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

  const fail = (e: unknown) => setFeedback({ tone: 'sig', text: describeError(e).messaggio })

  /* -------------------------------------------------- da dove viene l'endpoint */

  const activeProfile = profiles.find((p) => p.active && p.endpoint === inf.url)
  const modalTemplate = inf.url.includes('.modal.run') ? guessModalTemplate(inf.url) : null
  const origin = !inf.url
    ? null
    : activeProfile
      ? t('settings.computeOriginProfile', { name: activeProfile.name })
      : modalTemplate
        ? t('settings.computeOriginModal', {
            name: MODAL_TEMPLATES.find((m) => m.id === modalTemplate)?.label ?? modalTemplate,
          })
        : t('settings.computeOriginManual')

  /* ------------------------------------------------------------------- azioni */

  const runTest = async () => {
    setBusy('test')
    setFeedback(null)
    try {
      const res = await testInferenceConnection({ url: inf.url, model: inf.model })
      setFeedback(
        res.ok
          ? { tone: 'ok', text: t('settings.computeTestOk', { ms: String(res.latency_ms ?? '?') }) }
          : { tone: 'sig', text: t('settings.computeTestFail', { error: String(res.error) }) },
      )
      await syncInferenceFromBackend()
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  const toggleEnabled = async () => {
    const next = !inf.enabled
    setBusy('enabled')
    setFeedback(null)
    try {
      await saveInferenceToBackend({ enabled: next })
      setFeedback({
        tone: 'ok',
        text: next ? t('settings.computeEnabledOn') : t('settings.computeEnabledOff'),
      })
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  const activateProfile = async (profile: ComputeProfile) => {
    setBusy(`profile-${profile.id}`)
    setFeedback(null)
    try {
      const active = await apiPost<ComputeProfile>(`/system/compute-profiles/${profile.id}/activate`)
      setProfiles((current) => current.map((p) => ({ ...p, active: p.id === active.id })))
      await syncInferenceFromBackend()
      setFeedback({ tone: 'ok', text: t('settings.computeActivated', { name: active.name }) })
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  const activateModal = async (id: string) => {
    const status = modalStates[id]
    const target = MODAL_TEMPLATE_TARGET[id]
    if (!status?.endpoint || !target) return
    const label = MODAL_TEMPLATES.find((m) => m.id === id)?.label ?? id
    setBusy(`modal-${id}`)
    setFeedback(null)
    try {
      const url = `${status.endpoint.replace(/\/$/, '')}/v1`
      await saveInferenceToBackend({
        enabled: true,
        url,
        model: target.model,
        adapterId: target.adapterId,
      })
      const res = await testInferenceConnection({ url, model: target.model })
      setFeedback(
        res.ok
          ? { tone: 'ok', text: t('settings.computeActivated', { name: label }) }
          : { tone: 'sig', text: t('settings.computeTestFail', { error: String(res.error) }) },
      )
    } catch (e) {
      fail(e)
    } finally {
      setBusy(null)
    }
  }

  /* --------------------------------------------------------------------- vista */

  const online = inf.enabled && inf.available
  const stateBadge = !inf.enabled ? (
    <Badge tone="neutral">{t('settings.computeDisabled')}</Badge>
  ) : online ? (
    <Badge tone="ok">
      {inf.isCloud ? t('settings.computeOnlineCloud') : t('settings.computeOnline')}
      {inf.latencyMs != null && ` · ${t('settings.computeLatency', { ms: String(inf.latencyMs) })}`}
    </Badge>
  ) : (
    <Badge tone="warn">{t('settings.computeOffline')}</Badge>
  )

  return (
    <div className="space-y-3">
      <Module tab={t('settings.computeCurrent')} aux={stateBadge}>
        <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
          {t('settings.computeCurrentHint')}
        </p>

        {inf.url ? (
          <dl className="mt-3 grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
            {[
              [t('settings.computeEndpoint'), inf.url],
              [t('settings.computeModel'), inf.model],
              [t('settings.computeAdapter'), inf.adapterId],
              [t('settings.computeOrigin'), origin ?? '—'],
            ].map(([label, value]) => (
              <div key={label} className="flex min-w-0 gap-2">
                <dt className="lbl !mb-0 shrink-0 pt-px">{label}</dt>
                <dd className="mono min-w-0 flex-1 truncate text-[12px]" title={value}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="mt-3 text-[12px] text-[color:var(--color-ink-3)]">
            {t('settings.computeNoEndpoint')}
          </p>
        )}

        {feedback && <Notice tone={feedback.tone}>{feedback.text}</Notice>}

        <div className="mt-3 flex flex-wrap gap-2">
          {isAdmin && (
            <>
              <button
                type="button"
                className="btn"
                disabled={!inf.url || busy === 'test'}
                onClick={() => void runTest()}
              >
                {busy === 'test' ? t('settings.computeTesting') : t('settings.computeTest')}
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy === 'enabled'}
                onClick={() => void toggleEnabled()}
              >
                {inf.enabled ? t('settings.computeDisable') : t('settings.computeEnable')}
              </button>
            </>
          )}
          <button type="button" className="btn" onClick={() => setCloudOpen(true)}>
            {t('settings.computeManage')}
          </button>
          <button type="button" className="btn" onClick={() => setModelsOpen(true)}>
            {t('settings.computeModels')}
          </button>
        </div>
      </Module>

      <Module tab={t('settings.computeSources')} quiet>
        <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
          {t('settings.computeSourcesHint')}
        </p>

        <h3 className="lbl mt-3">{t('settings.computeProfiles')}</h3>
        <div className="divide-y divide-[color:var(--color-rule)] border-y border-[color:var(--color-rule)]">
          {profiles.map((profile) => {
            const active = profile.active && profile.endpoint === inf.url
            return (
              <div key={profile.id} className="flex flex-wrap items-center gap-2 py-2 text-[12px]">
                <span className="font-semibold">{profile.name}</span>
                <Badge tone="neutral">{profile.provider}</Badge>
                <span className="mono truncate text-[11px] text-[color:var(--color-ink-3)]">
                  {profile.served_model_name} · {profile.model_adapter_id}
                </span>
                <span className="ml-auto">
                  {active ? (
                    <Badge tone="ok">{t('settings.computeActive')}</Badge>
                  ) : isAdmin ? (
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={busy === `profile-${profile.id}`}
                      onClick={() => void activateProfile(profile)}
                    >
                      {busy === `profile-${profile.id}`
                        ? t('settings.computeActivating')
                        : t('settings.computeActivate')}
                    </button>
                  ) : null}
                </span>
              </div>
            )
          })}
          {profiles.length === 0 && (
            <p className="py-2 text-[12px] text-[color:var(--color-ink-3)]">
              {t('settings.computeProfilesEmpty')}
            </p>
          )}
        </div>

        <h3 className="lbl mt-4">{t('settings.computeModal')}</h3>
        <div className="divide-y divide-[color:var(--color-rule)] border-y border-[color:var(--color-rule)]">
          {PREFILL_MODAL_TEMPLATES.map((id) => {
            const label = MODAL_TEMPLATES.find((m) => m.id === id)?.label ?? id
            const status = modalStates[id]
            const deployed = status?.app_state === 'deployed' && !!status.endpoint
            const active = modalTemplate === id
            return (
              <div key={id} className="flex flex-wrap items-center gap-2 py-2 text-[12px]">
                <span className="font-semibold">{label}</span>
                {status && (
                  <Badge tone={deployed ? 'ok' : 'neutral'}>
                    {deployed ? t('settings.computeDeployed') : t('settings.computeNotDeployed')}
                  </Badge>
                )}
                {!deployed && status && (
                  <span className="text-[11px] text-[color:var(--color-ink-3)]">
                    {t('settings.computeDeployHint')}
                  </span>
                )}
                <span className="ml-auto">
                  {active ? (
                    <Badge tone="ok">{t('settings.computeActive')}</Badge>
                  ) : isAdmin ? (
                    <button
                      type="button"
                      className="btn btn-sm"
                      disabled={!deployed || busy === `modal-${id}`}
                      onClick={() => void activateModal(id)}
                    >
                      {busy === `modal-${id}`
                        ? t('settings.computeActivating')
                        : t('settings.computeActivate')}
                    </button>
                  ) : null}
                </span>
              </div>
            )
          })}
        </div>
      </Module>

      {isAdmin && (
        <Collapsible tab={t('settings.computeManual')} quiet>
          <ManualEndpoint onDone={setFeedback} />
        </Collapsible>
      )}

      <CloudControlModal open={cloudOpen} onClose={() => setCloudOpen(false)} />
      <ModelsModal open={modelsOpen} onClose={() => setModelsOpen(false)} />
    </div>
  )
}

/** L'endpoint scritto a mano: il caso raro, con la sua verifica accanto. */
function ManualEndpoint({ onDone }: { onDone: (f: Feedback) => void }) {
  const { t } = useI18n()
  const inf = useInference()
  const [url, setUrl] = useState(inf.url)
  const [model, setModel] = useState(inf.model)
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [local, setLocal] = useState<Feedback>(null)

  useEffect(() => {
    setUrl(inf.url)
    setModel(inf.model)
  }, [inf.url, inf.model])

  const say = (f: Feedback) => {
    setLocal(f)
    onDone(f)
  }

  const runTest = async () => {
    setBusy('test')
    try {
      const res = await testInferenceConnection({ url, model, apiKey })
      say(
        res.ok
          ? { tone: 'ok', text: t('settings.computeTestOk', { ms: String(res.latency_ms ?? '?') }) }
          : { tone: 'sig', text: t('settings.computeTestFail', { error: String(res.error) }) },
      )
    } catch (e) {
      say({ tone: 'sig', text: describeError(e).messaggio })
    } finally {
      setBusy(null)
    }
  }

  const runSave = async () => {
    setBusy('save')
    try {
      await saveInferenceToBackend({ url, model: model || 'MonkeyOCRv2', apiKey })
      say({ tone: 'ok', text: t('settings.computeSavedManual') })
    } catch (e) {
      say({ tone: 'sig', text: describeError(e).messaggio })
    } finally {
      setBusy(null)
    }
  }

  return (
    <>
      <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
        {t('settings.computeManualHint')}
      </p>

      <div className="mt-3">
        <span className="lbl">{t('settings.computePresets')}</span>
        <div className="flex flex-wrap gap-1.5">
          {INFERENCE_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              className="btn btn-sm"
              title={t(preset.hintKey)}
              onClick={() => {
                setUrl(preset.url)
                setModel(preset.model)
                say({
                  tone: 'ok',
                  text: t('settings.computePresetApplied', { name: t(preset.labelKey) }),
                })
              }}
            >
              {t(preset.labelKey)}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Field label={t('settings.computeEndpoint')} hint={t('settings.computeEndpointHint')}>
            <input
              className="fld fld-mono"
              value={url}
              placeholder="http://127.0.0.1:8888/v1"
              onChange={(e) => setUrl(e.target.value)}
            />
          </Field>
        </div>
        <Field label={t('settings.computeModel')} hint={t('settings.computeModelHint')}>
          <input
            className="fld fld-mono"
            value={model}
            placeholder="MonkeyOCRv2"
            onChange={(e) => setModel(e.target.value)}
          />
        </Field>
        <Field label={t('settings.computeApiKey')} hint={t('settings.computeApiKeyHint')}>
          <div className="flex gap-1.5">
            <input
              className="fld fld-mono flex-1"
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              placeholder={t('settings.computeApiKeyPlaceholder')}
              onChange={(e) => setApiKey(e.target.value)}
            />
            <button type="button" className="btn btn-sm" onClick={() => setShowKey((v) => !v)}>
              {showKey ? t('settings.computeHide') : t('settings.computeShow')}
            </button>
          </div>
        </Field>
      </div>

      {local && <Notice tone={local.tone}>{local.text}</Notice>}

      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" className="btn" disabled={!url || busy != null} onClick={() => void runTest()}>
          {busy === 'test' ? t('settings.computeTesting') : t('settings.computeTest')}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={!url || busy != null}
          onClick={() => void runSave()}
        >
          {busy === 'save' ? t('settings.saving') : t('settings.computeSaveManual')}
        </button>
      </div>
    </>
  )
}
