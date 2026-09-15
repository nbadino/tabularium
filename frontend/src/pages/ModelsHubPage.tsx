import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Badge, ErrorNotice, Modal, Module } from '../app/ui'
import { syncInferenceFromBackend, useInference } from '../app/inference'
import { ModelsModal } from '../app/ModelsModal'
import { CloudControlModal } from '../app/CloudControlModal'
import { useI18n } from '../i18n'

export default function ModelsHubPage() {
  const { t } = useI18n()
  const inference = useInference()
  const [modelsOpen, setModelsOpen] = useState(false)
  const [providersOpen, setProvidersOpen] = useState(false)
  /** Scheda provider da aprire quando l'apertura nasce da «Deploya qui». */
  const [focusProvider, setFocusProvider] = useState<'local' | 'vast' | 'runpod' | 'modal' | 'manual' | null>(null)
  const [focusAdapterId, setFocusAdapterId] = useState<string | null>(null)
  const [focusModelLabel, setFocusModelLabel] = useState<string | null>(null)
  const [destinationChoiceOpen, setDestinationChoiceOpen] = useState(false)
  const [selectedModel, setSelectedModel] = useState<{ id: string; label: string } | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    void syncInferenceFromBackend().catch(setError)
  }, [])

  const destination = inference.provider ?? 'local'

  /** La libreria seleziona soltanto il modello. La destinazione viene scelta
   *  nel passaggio successivo, senza ereditare silenziosamente il provider
   *  della configurazione precedente. */
  const handleDeploy = (adapterId: string, displayName: string) => {
    setSelectedModel({ id: adapterId, label: displayName })
    setModelsOpen(false)
    setDestinationChoiceOpen(true)
  }

  const handleChooseExecution = () => {
    if (selectedModel) {
      setDestinationChoiceOpen(true)
      return
    }
    // Una configurazione precedente contiene già un modello valido: lo
    // rendiamo esplicito nella stessa schermata modello → destinazione,
    // invece di dedurre silenziosamente il provider dall'URL.
    if (inference.model && inference.adapterId) {
      setSelectedModel({ id: inference.adapterId, label: inference.model })
      setDestinationChoiceOpen(true)
      return
    }
    setModelsOpen(true)
  }

  const chooseDestination = (provider: 'local' | 'vast' | 'runpod' | 'modal' | 'manual') => {
    if (!selectedModel) return
    setFocusAdapterId(selectedModel.id)
    setFocusModelLabel(selectedModel.label)
    setDestinationChoiceOpen(false)
    if (provider === 'local') {
      setFocusProvider('local')
      setModelsOpen(true)
      return
    }
    setFocusProvider(provider)
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
          tab={t('modelsHub.activeSetup')}
          aux={<Badge tone={inference.enabled && inference.available ? 'ok' : 'warn'}>
            {inference.enabled && inference.available
              ? t('recognition.modelReady')
              : inference.model && !inference.available
                ? t('recognition.modelUnavailable')
                : inference.model
                ? t('modelsHub.modelConfigured')
                : t('modelsHub.chooseModelFirst')}
          </Badge>}
        >
          <p className="mb-2 text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.activeSetupHint')}</p>
          <div className="text-[18px] font-bold">{selectedModel?.label || inference.model || t('modelsHub.chooseModelFirst')}</div>
          <div className="mono mt-1 truncate text-[11px] text-[color:var(--color-ink-3)]">
            {selectedModel
              ? `${selectedModel.id} · ${t('modelsHub.chooseExecution')}`
              : inference.model
                ? `${inference.adapterId} · ${t(inference.enabled && inference.available ? 'modelsHub.chooseExecution' : 'modelsHub.lastDestination')}: ${t(`recognition.provider.${destination}`)}`
                : t('modelsHub.chooseExecution')}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className="btn btn-primary" onClick={() => { setSelectedModel(null); setModelsOpen(true) }}>{selectedModel?.label ?? t('modelsHub.chooseModelFirst')}</button>
            <button
              type="button"
              className="btn"
              onClick={handleChooseExecution}
              disabled={!selectedModel && !(inference.model && inference.adapterId)}
            >
              {t('modelsHub.chooseExecution')}
            </button>
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

      <ModelsModal
        open={modelsOpen}
        onClose={() => setModelsOpen(false)}
        activeProvider={focusProvider === 'local' ? 'local' : null}
        selectedAdapterId={focusProvider === 'local' ? focusAdapterId : null}
        onDeploy={handleDeploy}
        onChangeDestination={() => setDestinationChoiceOpen(true)}
      />
      {selectedModel && destinationChoiceOpen && (
        <Modal title={t('modelsHub.chooseDestinationTitle')} onClose={() => setDestinationChoiceOpen(false)}>
          <div className="space-y-4 p-4 text-[13px]">
            <div className="border border-[color:var(--color-rule-strong)] bg-[color:var(--color-panel)] p-3">
              <span className="lbl">{t('modelsHub.selectedModel')}</span>
              <div className="mt-1 text-[18px] font-bold">{selectedModel.label}</div>
              <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.chooseDestinationHint')}</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {(['local', 'vast', 'runpod', 'modal', 'manual'] as const).map((provider) => (
                <button key={provider} type="button" className="border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] p-3 text-left hover:border-[color:var(--color-sig)]" onClick={() => chooseDestination(provider)}>
                  <span className="block font-bold">{t(`recognition.provider.${provider}`)}</span>
                  <span className="mt-1 block text-[11px] text-[color:var(--color-ink-2)]">{t(`modelsHub.destination.${provider}`)}</span>
                </button>
              ))}
            </div>
          </div>
        </Modal>
      )}
      <CloudControlModal
        open={providersOpen}
        onClose={() => { setProvidersOpen(false); setFocusProvider(null); setFocusAdapterId(null); setFocusModelLabel(null) }}
        focusProvider={focusProvider === 'local' ? null : focusProvider}
        focusAdapterId={focusAdapterId}
        focusModelLabel={focusModelLabel}
      />
    </div>
  )
}
