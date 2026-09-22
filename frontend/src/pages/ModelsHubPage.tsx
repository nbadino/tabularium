/**
 * L'hub Modelli: il luogo unico in cui si sceglie il modello, si decide dove
 * eseguirlo e si aprono gli strumenti che lo migliorano.
 *
 * La libreria non è più una modale: è la pagina. Prima il catalogo stava
 * dietro «Scegli il modello» e l'hub restava due riquadri su fondo bianco;
 * ora il catalogo è il corpo e la destinazione è dichiarata in testa, come
 * vuole il design: ogni riga offre l'azione che vale nella destinazione
 * attiva (servire in locale, deployare sul provider remoto).
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Badge, ErrorNotice, Module, Notice } from '../app/ui'
import { apiGet } from '../lib/api'
import type { SystemInfo } from '../lib/types'
import { syncInferenceFromBackend, useInference } from '../app/inference'
import { ModelsCatalog } from '../app/ModelsCatalog'
import { CloudControlModal } from '../app/CloudControlModal'
import { localRuntimeLabel } from '../lib/vocab'
import { useI18n } from '../i18n'

type Provider = 'local' | 'vast' | 'runpod' | 'modal' | 'manual'

const PROVIDERS: readonly Provider[] = ['local', 'vast', 'runpod', 'modal', 'manual']

export default function ModelsHubPage() {
  const { t } = useI18n()
  const inference = useInference()
  const [providersOpen, setProvidersOpen] = useState(false)
  /** Scheda provider da aprire quando l'apertura nasce da «Deploya qui». */
  const [focusProvider, setFocusProvider] = useState<Provider | null>(null)
  const [focusAdapterId, setFocusAdapterId] = useState<string | null>(null)
  const [focusModelLabel, setFocusModelLabel] = useState<string | null>(null)
  const [destinationChoiceOpen, setDestinationChoiceOpen] = useState(false)
  const [selectedModel, setSelectedModel] = useState<{ id: string; label: string } | null>(null)
  const [error, setError] = useState<unknown>(null)
  /** Capacità della macchina: senza CUDA locale il serving in locale non parte. */
  const [caps, setCaps] = useState<SystemInfo['capabilities'] | null>(null)

  useEffect(() => {
    void syncInferenceFromBackend().catch(setError)
    apiGet<SystemInfo>('/system/info')
      .then((info) => setCaps(info.capabilities ?? null))
      .catch(() => {})
  }, [])

  const usableLocalRuntimes = caps?.local_compute?.usable_runtimes ?? []
  const localAvailable = usableLocalRuntimes.length > 0
  // La ragione mostrata è quella del primo runtime che la macchina *non*
  // può ospitare: è la causa più vicina a «perché non posso usare il locale».
  const localReason =
    caps?.local_compute?.runtimes?.['vllm']?.reason ??
    caps?.local_compute?.runtimes?.['mlx-vlm']?.reason ??
    'no_local_runtime'
  const savedProvider = inference.provider ?? null
  const savedModelLabel = selectedModel?.label || inference.model || null
  // La destinazione della libreria: quella scelta adesso, altrimenti il
  // profilo salvato. Se non c'è né l'una né l'altro il catalogo resta
  // neutro — nessun provider ereditato in silenzio. Se la macchina non può
  // servire nulla in locale, «locale» non è una destinazione praticabile e
  // il catalogo torna neutro invece di offrire un serving che non partirà.
  const wantedProvider = focusProvider ?? savedProvider
  const catalogProvider = !localAvailable && wantedProvider === 'local' ? null : wantedProvider
  const catalogAdapterId = focusProvider ? focusAdapterId : inference.adapterId ?? null
  const destinationIsLocal = wantedProvider === 'local' || wantedProvider === null

  const destinationLabel = savedProvider
    ? t(`recognition.provider.${savedProvider}`)
    : t('recognition.locationLocal')

  const handleDeploy = (adapterId: string, displayName: string) => {
    setSelectedModel({ id: adapterId, label: displayName })
    setDestinationChoiceOpen(true)
  }

  const chooseDestination = (provider: Provider) => {
    setFocusAdapterId(selectedModel?.id ?? inference.adapterId ?? null)
    setFocusModelLabel(selectedModel?.label ?? inference.model ?? null)
    setDestinationChoiceOpen(false)
    if (provider === 'local') {
      setFocusProvider('local')
      return
    }
    setFocusProvider(provider)
    setProvidersOpen(true)
  }

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">{t('nav.models')}</h1>
        <p className="mt-1 max-w-[72ch] text-[13px] text-[color:var(--color-ink-2)]">{t('recognition.modelsIntro')}</p>
      </div>

      {error != null && <div className="mb-3"><ErrorNotice error={error} /></div>}

      {!localAvailable && destinationIsLocal && (
        <div className="mb-3">
          <Notice tone="warn">
            <b className="font-semibold">{t('localCompute.reason.no_local_runtime')}</b>{' '}
            {t(`localCompute.reason.${localReason}`)}
          </Notice>
        </div>
      )}
      {localAvailable && destinationIsLocal && !caps?.local_compute?.cuda?.available && (
        <div className="mb-3">
          <Notice>{t('localCompute.mlxHint')}</Notice>
        </div>
      )}

      {/* La destinazione è dichiarata in testa, prima della libreria: è ciò
          che decide l'azione di ogni riga. */}
      <Module
        tab={t('modelsHub.activeSetup')}
        aux={
          <Badge tone={inference.enabled && inference.available ? 'ok' : 'warn'}>
            {inference.enabled && inference.available
              ? t('recognition.modelReady')
              : inference.model && !inference.available
                ? t('recognition.modelUnavailable')
                : inference.model
                  ? t('modelsHub.modelConfigured')
                  : t('modelsHub.chooseModelFirst')}
          </Badge>
        }
      >
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <div className="min-w-0">
            <span className="lbl">{t('modelsHub.runningModel')}</span>
            <div className="truncate text-[18px] font-bold">
              {savedModelLabel ?? t('modelsHub.chooseModelFirst')}
            </div>
            <div className="mono truncate text-[11px] text-[color:var(--color-ink-3)]">
              {(selectedModel?.id ?? inference.adapterId) || t('modelsHub.chooseModelFirst')}
            </div>
          </div>
          <div className="min-w-0">
            <span className="lbl">{t('cloud.models.destinationLabel')}</span>
            <div className="text-[15px] font-semibold">{destinationLabel}</div>
            <div className="text-[11px] text-[color:var(--color-ink-2)]">
              {t(`modelsHub.destination.${savedProvider ?? 'local'}`)}
            </div>
          </div>
          <div className="ml-auto flex flex-wrap gap-2">
            <button
              type="button"
              className="btn"
              onClick={() => setDestinationChoiceOpen(true)}
            >
              {t('cloud.models.changeDestination')}
            </button>
          </div>
        </div>
      </Module>

      {destinationChoiceOpen && (
        <div className="mt-3">
          <Module tab={t('modelsHub.chooseDestinationTitle')}>
            <p className="text-[12px] text-[color:var(--color-ink-2)]">
              {selectedModel
                ? t('modelsHub.chooseDestinationHint')
                : t('modelsHub.chooseExecution')}
            </p>
            {/* Cosa può fare questa macchina, prima che l'utente scelga: senza
                questo, «Locale» sembra sempre possibile e il rifiuto arriva
                dopo il click. */}
            {caps?.local_compute && (
              <p className="mt-2 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1.5 text-[11px] text-[color:var(--color-ink-2)]">
                <span className="lbl !mb-0 mr-2">{t('localCompute.title')}</span>
                {t('localCompute.machine', {
                  os: caps.local_compute.platform,
                  arch: caps.local_compute.arch,
                })}
                {' · '}
                {caps.local_compute.memory_gb
                  ? t('localCompute.memory', { gb: String(caps.local_compute.memory_gb) })
                  : t('localCompute.memoryUnknown')}
                {' · '}
                {caps.local_compute.usable_runtimes.length > 0
                  ? caps.local_compute.usable_runtimes
                      .map((r) => localRuntimeLabel(r))
                      .join(', ')
                  : t('localCompute.reason.no_local_runtime')}
              </p>
            )}
            <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {PROVIDERS.map((provider) => {
                const blocked = provider === 'local' && !localAvailable
                return (
                  <button
                    key={provider}
                    type="button"
                    disabled={blocked}
                    className="border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] p-3 text-left hover:border-[color:var(--color-sig)] disabled:cursor-not-allowed disabled:border-[color:var(--color-rule)] disabled:bg-[color:var(--color-fill)] disabled:hover:border-[color:var(--color-rule)]"
                    onClick={() => chooseDestination(provider)}
                  >
                    <span className={`block font-bold ${blocked ? 'text-[color:var(--color-ink-3)]' : ''}`}>
                      {t(`recognition.provider.${provider}`)}
                    </span>
                    <span className="mt-1 block text-[11px] text-[color:var(--color-ink-2)]">
                      {provider === 'local'
                        ? localAvailable
                          ? t('localCompute.runnable', {
                              runtime: localRuntimeLabel(caps?.local_compute?.usable_runtimes?.[0]),
                            })
                          : t(`localCompute.reason.${localReason}`)
                        : t(`modelsHub.destination.${provider}`)}
                    </span>
                  </button>
                )
              })}
            </div>
          </Module>
        </div>
      )}

      <div className="mt-3">
        <ModelsCatalog
          activeProvider={catalogProvider}
          selectedAdapterId={catalogAdapterId}
          onDeploy={handleDeploy}
        />
      </div>

      <div className="mt-3">
        <Module tab={t('modelsHub.improveModel')} quiet>
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.toolsIntro')}</p>
          <ul className="ruled mt-3 border-t border-[color:var(--color-rule)]">
            {(
              [
                { to: '/dataset', label: t('nav.dataset'), hint: t('modelsHub.toolDatasetHint') },
                { to: '/training', label: t('nav.training'), hint: t('modelsHub.toolTrainingHint') },
                { to: '/valutazione', label: t('nav.evaluation'), hint: t('modelsHub.toolEvaluationHint') },
                { to: '/playground', label: t('nav.playground'), hint: t('modelsHub.toolPlaygroundHint') },
              ] as const
            ).map((tool) => (
              <li key={tool.to} className="border-b border-[color:var(--color-rule)]">
                <Link
                  to={tool.to}
                  className="block px-1 py-1.5 no-underline hover:bg-[color:var(--color-fill)]"
                >
                  <span className="text-[13px] font-semibold text-[color:var(--color-ink)]">{tool.label}</span>
                  <span className="mt-0.5 block text-[11px] text-[color:var(--color-ink-2)]">{tool.hint}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Module>
      </div>

      <CloudControlModal
        open={providersOpen}
        onClose={() => {
          setProvidersOpen(false)
          setFocusProvider(null)
          setFocusAdapterId(null)
          setFocusModelLabel(null)
        }}
        focusProvider={focusProvider === 'local' ? null : focusProvider}
        focusAdapterId={focusAdapterId}
        focusModelLabel={focusModelLabel}
      />
    </div>
  )
}
