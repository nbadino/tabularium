/**
 * L'hub Modelli: il luogo unico in cui si sceglie il modello, si decide dove
 * eseguirlo e lo si mette in servizio.
 *
 * Il percorso è in tre passi, nell'ordine in cui la decisione si prende
 * davvero: **modello → dove → configura**. Prima la libreria offriva su ogni
 * riga l'azione della destinazione salvata (scarica, avvia, deploya) e la
 * destinazione si cambiava da un modulo a parte: chi arrivava per la prima
 * volta vedeva porta, account Hugging Face e sette badge per riga prima di
 * aver scelto qualsiasi cosa. Ora ogni passo mostra solo ciò che serve a
 * quel passo, e la scelta fatta resta scritta nella linguetta del passo.
 *
 * Lo stato del percorso vive nell'URL (`?modello=…&dove=…&passo=…`): il
 * pulsante Indietro del browser torna al passo precedente e un link
 * condiviso riapre la stessa configurazione.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { Badge, ErrorNotice, Module, Notice } from '../app/ui'
import { apiGet } from '../lib/api'
import type { SystemInfo } from '../lib/types'
import { syncInferenceFromBackend, useInference } from '../app/inference'
import { CloudControlModal } from '../app/CloudControlModal'
import { ModelPicker } from '../app/models/ModelPicker'
import { DestinationPicker } from '../app/models/DestinationPicker'
import { LocalSetup } from '../app/models/LocalSetup'
import {
  destinationVerdict,
  fetchModelRegistry,
  isDestination,
  readStoredModelRegistry,
  resolveStep,
  type Destination,
  type ModelItem,
  type StepId,
} from '../app/models/registry'
import { useI18n } from '../i18n'

/** Parole dell'URL, in italiano come le rotte (`/modelli`). */
const STEP_PARAM: Record<StepId, string> = { model: 'modello', destination: 'dove', configure: 'configura' }
const PARAM_STEP: Record<string, StepId> = { modello: 'model', dove: 'destination', configura: 'configure' }

export default function ModelsHubPage() {
  const { t } = useI18n()
  const inference = useInference()
  const [params, setParams] = useSearchParams()
  const [models, setModels] = useState<ModelItem[]>(readStoredModelRegistry)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [caps, setCaps] = useState<SystemInfo['capabilities'] | null>(null)
  const [remoteDone, setRemoteDone] = useState(false)
  const stepsRef = useRef<HTMLElement>(null)

  const reload = useCallback(async (force = true) => {
    try {
      setModels(await fetchModelRegistry(force))
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void syncInferenceFromBackend().catch(setError)
    // The adapter registry changes with an app/backend update (for example a
    // newly supported model). A recent browser cache must not hide it; force
    // one authoritative refresh whenever the Models hub is opened.
    void reload(true)
    apiGet<SystemInfo>('/system/info')
      .then((info) => setCaps(info.capabilities ?? null))
      .catch(() => {})
  }, [reload])

  // La configurazione salvata è il punto di partenza quando l'URL non dice
  // niente: si riapre il passo «Configura» di ciò che è in uso, così lo
  // stato del server o dell'istanza è la prima cosa che si vede.
  const configured = Boolean(inference.provider && inference.adapterId)
  const urlModel = params.get('modello')
  const urlDest = params.get('dove')
  const urlStep = PARAM_STEP[params.get('passo') ?? ''] ?? null
  const pristine = !urlModel && !urlDest && !urlStep
  const modelId = urlModel ?? (pristine && configured ? inference.adapterId : null)
  const destRaw = urlDest ?? (pristine && configured ? inference.provider : null)
  const dest: Destination | null = isDestination(destRaw) ? destRaw : null
  const model = useMemo(() => models.find((m) => m.adapter_id === modelId) ?? null, [models, modelId])
  const step = resolveStep(urlStep ?? (pristine && configured ? 'configure' : null), model, dest)

  const go = (next: { model?: string | null; dest?: Destination | null; step: StepId }) => {
    const p = new URLSearchParams()
    const m = next.model !== undefined ? next.model : modelId
    const d = next.dest !== undefined ? next.dest : dest
    if (m) p.set('modello', m)
    if (d) p.set('dove', d)
    p.set('passo', STEP_PARAM[next.step])
    setRemoteDone(false)
    setParams(p)
    // Il passo nuovo comincia sotto la linguetta: se la si è lasciata fuori
    // schermo scorrendo il catalogo, la si riporta in vista.
    const top = stepsRef.current?.getBoundingClientRect().top
    if (top != null && top < 0) stepsRef.current?.scrollIntoView({ block: 'start' })
  }

  const selectModel = (m: ModelItem) => {
    // Cambiare modello invalida la destinazione solo se il nuovo modello non
    // la accetta: chi ha scelto «Modal» e prova un altro modello resta lì.
    const keep = dest && destinationVerdict(m, dest).ok ? dest : null
    go({ model: m.adapter_id, dest: keep, step: 'destination' })
  }

  const inUse = inference.enabled && configured
  const inUseReady = inUse && inference.available
  const inUseLabel = models.find((m) => m.adapter_id === inference.adapterId)?.display_name ?? inference.model

  const stepTabs: { id: StepId; n: number; label: string; value: string | null; enabled: boolean }[] = [
    { id: 'model', n: 1, label: t('modelsHub.step.model'), value: model?.display_name ?? null, enabled: true },
    {
      id: 'destination',
      n: 2,
      label: t('modelsHub.step.destination'),
      value: dest && model ? t(`recognition.provider.${dest}`) : null,
      enabled: !!model,
    },
    {
      id: 'configure',
      n: 3,
      label: t('modelsHub.step.configure'),
      // Il terzo passo non ha una scelta da ricordare: ha uno stato.
      value:
        model && dest
          ? inUseReady && model.adapter_id === inference.adapterId && dest === inference.provider
            ? t('modelsHub.stepReady')
            : t('modelsHub.stepConfiguring')
          : null,
      enabled: !!model && !!dest,
    },
  ]

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">{t('nav.models')}</h1>
        <p className="mt-1 max-w-[80ch] text-[13px] text-[color:var(--color-ink-2)]">{t('modelsHub.intro')}</p>
      </div>

      {error != null && <div className="mb-3"><ErrorNotice error={error} /></div>}

      {/* Cosa usa Tabularium adesso: la risposta a «cosa sto usando?» non
          deve dipendere dal passo aperto. */}
      <Module
        tab={t('modelsHub.inUseTitle')}
        quiet
        aux={
          <Badge tone={inUseReady ? 'ok' : inUse ? 'warn' : 'neutral'}>
            {inUseReady
              ? t('recognition.modelReady')
              : inUse
                ? t('recognition.modelUnavailable')
                : t('modelsHub.nothingInUse')}
          </Badge>
        }
      >
        {inUse ? (
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <div className="min-w-0">
              <span className="lbl">{t('modelsHub.runningModel')}</span>
              <div className="truncate text-[16px] font-bold">{inUseLabel}</div>
            </div>
            <div>
              <span className="lbl">{t('cloud.models.destinationLabel')}</span>
              <div className="text-[14px] font-semibold">{t(`recognition.provider.${inference.provider}`)}</div>
            </div>
            <div className="min-w-0">
              <span className="lbl">{t('modelsHub.endpoint')}</span>
              <div className="mono truncate text-[12px]">{inference.url || '—'}</div>
            </div>
            <div className="ml-auto flex flex-wrap gap-2">
              {!(step === 'configure' && modelId === inference.adapterId && dest === inference.provider) && (
                <button
                  type="button"
                  className="btn"
                  onClick={() =>
                    go({
                      model: inference.adapterId,
                      dest: isDestination(inference.provider) ? inference.provider : null,
                      step: 'configure',
                    })
                  }
                >
                  {t('modelsHub.manageInUse')}
                </button>
              )}
              {inUseReady && (
                <Link to="/" className="btn btn-primary no-underline">
                  {t('modelsHub.goRecognize')}
                </Link>
              )}
            </div>
          </div>
        ) : (
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.nothingInUseHint')}</p>
        )}
      </Module>

      {/* I tre passi: la linguetta porta la scelta fatta, così tornare
          indietro non costa rileggere la pagina per ricordarla. */}
      <nav ref={stepsRef} aria-label={t('modelsHub.stepsLabel')} className="mt-3 scroll-mt-3">
        <ol className="grid border border-[color:var(--color-rule)] sm:grid-cols-3">
          {stepTabs.map((s) => {
            const active = s.id === step
            return (
              <li key={s.id} className="border-b border-[color:var(--color-rule)] last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
                <button
                  type="button"
                  disabled={!s.enabled}
                  aria-current={active ? 'step' : undefined}
                  onClick={() => go({ step: s.id })}
                  className={`relative flex w-full items-center gap-2.5 px-3 py-2 text-left disabled:cursor-not-allowed ${
                    active ? 'bg-[color:var(--color-ink)] text-white' : 'bg-[color:var(--color-sheet)] hover:bg-[color:var(--color-fill)] disabled:hover:bg-[color:var(--color-sheet)]'
                  }`}
                >
                  <span
                    className={`flex h-6 w-6 shrink-0 items-center justify-center text-[12px] font-bold ${
                      active
                        ? 'bg-[color:var(--color-sig-plate)] text-white'
                        : s.value
                          ? 'bg-[color:var(--color-ink)] text-white'
                          : 'border border-[color:var(--color-rule)] text-[color:var(--color-ink-3)]'
                    }`}
                    aria-hidden
                  >
                    {s.n}
                  </span>
                  <span className="min-w-0">
                    <span className={`block text-[11px] font-semibold uppercase tracking-[0.04em] ${active ? 'text-white/80' : s.enabled ? 'text-[color:var(--color-ink-2)]' : 'text-[color:var(--color-ink-3)]'}`}>
                      {s.label}
                    </span>
                    <span className={`block truncate text-[13px] font-semibold ${active ? 'text-white' : s.enabled ? '' : 'text-[color:var(--color-ink-3)]'}`}>
                      {s.value ?? (active ? t('modelsHub.stepNow') : '—')}
                    </span>
                  </span>
                  {active && <span className="absolute inset-x-0 bottom-0 h-[3px] bg-[color:var(--color-sig)]" aria-hidden />}
                </button>
              </li>
            )
          })}
        </ol>
      </nav>

      <div className="mt-3">
        {step === 'model' && (
          <ModelPicker
            models={models}
            loading={loading}
            selectedId={model?.adapter_id ?? null}
            onSelect={selectModel}
            onChanged={() => reload(true)}
          />
        )}

        {step === 'destination' && model && (
          <DestinationPicker
            model={model}
            selected={dest}
            caps={caps}
            onSelect={(d) => go({ dest: d, step: 'configure' })}
          />
        )}

        {step === 'configure' && model && dest === 'local' && (
          <LocalSetup model={model} onChanged={() => reload(true)} />
        )}

        {step === 'configure' && model && dest && dest !== 'local' && (
          <div className="space-y-3">
            <p className="max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('modelsHub.remoteIntro', {
                model: model.display_name,
                provider: t(`recognition.provider.${dest}`),
              })}
            </p>
            {remoteDone && (
              <Notice tone="ok">
                {t('modelsHub.remoteDone', { model: model.display_name, provider: t(`recognition.provider.${dest}`) })}{' '}
                <Link to="/">{t('modelsHub.goRecognize')}</Link>
              </Notice>
            )}
            <CloudControlModal
              key={`${dest}:${model.adapter_id}`}
              open
              inline
              onlyProvider={dest}
              focusProvider={dest}
              focusAdapterId={model.adapter_id}
              focusModelLabel={model.display_name}
              onClose={() => {
                // In linea «chiudere» vuol dire «configurazione salvata»: si
                // resta sul passo e lo si dice.
                void syncInferenceFromBackend().catch(() => {})
                setRemoteDone(true)
              }}
            />
          </div>
        )}

        {step !== 'model' && !model && !loading && (
          <Notice tone="warn">{t('modelsHub.modelMissing')}</Notice>
        )}
      </div>

      <div className="mt-3">
        <Module tab={t('modelsHub.improveModel')} quiet>
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.toolsIntro')}</p>
          <ul className="mt-3 grid border-t border-[color:var(--color-rule)] sm:grid-cols-2 xl:grid-cols-4">
            {(
              [
                { to: '/dataset', label: t('nav.dataset'), hint: t('modelsHub.toolDatasetHint') },
                { to: '/training', label: t('nav.training'), hint: t('modelsHub.toolTrainingHint') },
                { to: '/valutazione', label: t('nav.evaluation'), hint: t('modelsHub.toolEvaluationHint') },
                { to: '/playground', label: t('nav.playground'), hint: t('modelsHub.toolPlaygroundHint') },
              ] as const
            ).map((tool) => (
              <li key={tool.to} className="border-b border-[color:var(--color-rule)]">
                <Link to={tool.to} className="block px-1 py-1.5 no-underline hover:bg-[color:var(--color-fill)]">
                  <span className="text-[13px] font-semibold text-[color:var(--color-ink)]">{tool.label}</span>
                  <span className="mt-0.5 block text-[11px] text-[color:var(--color-ink-2)]">{tool.hint}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Module>
      </div>
    </div>
  )
}
