/**
 * Passo 2 dell'hub Modelli: dove eseguire il modello scelto.
 *
 * Ogni destinazione dichiara, prima del click, tre cose: se accoglie *questo*
 * modello (e se no perché), cosa comporta e cosa servirà per configurarla.
 * Il verdetto è lo stesso che applicheranno i pannelli del passo successivo
 * (`destinationVerdict`): nessuna opzione cliccabile porta a un rifiuto.
 */
import { localRuntimeLabel } from '../../lib/vocab'
import type { SystemInfo } from '../../lib/types'
import { useI18n } from '../../i18n'
import { Badge, Module } from '../ui'
import { IconWarn } from '../icons'
import { DESTINATIONS, destinationVerdict, recommendedDestination, type Destination, type ModelItem } from './registry'

interface DestinationPickerProps {
  model: ModelItem
  selected: Destination | null
  caps: SystemInfo['capabilities'] | null
  onSelect: (dest: Destination) => void
}

export function DestinationPicker({ model, selected, caps, onSelect }: DestinationPickerProps) {
  const { t } = useI18n()
  const recommended = recommendedDestination(model)
  const machine = caps?.local_compute

  return (
    <Module tab={`2 · ${t('modelsHub.step.destination')}`} aux={<span className="text-[11px] font-semibold">{model.display_name}</span>}>
      <p className="max-w-[72ch] text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.chooseDestinationHint')}</p>

      {/* Cosa può fare questa macchina, prima che l'utente scelga: senza
          questo, «Locale» sembra sempre possibile e il rifiuto arriva dopo il
          click. */}
      {machine && (
        <p className="mt-2 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-2 py-1.5 text-[11px] text-[color:var(--color-ink-2)]">
          <span className="lbl !mb-0 mr-2 inline">{t('localCompute.title')}</span>
          {t('localCompute.machine', { os: machine.platform, arch: machine.arch })}
          {' · '}
          {machine.memory_gb
            ? t('localCompute.memory', { gb: String(machine.memory_gb) })
            : t('localCompute.memoryUnknown')}
          {' · '}
          {machine.usable_runtimes.length > 0
            ? machine.usable_runtimes.map((r) => localRuntimeLabel(r)).join(', ')
            : t('localCompute.reason.no_local_runtime')}
        </p>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {DESTINATIONS.map((dest) => {
          const verdict = destinationVerdict(model, dest)
          const isSelected = selected === dest
          const isRecommended = recommended === dest
          return (
            <button
              key={dest}
              type="button"
              disabled={!verdict.ok}
              aria-pressed={isSelected}
              onClick={() => onSelect(dest)}
              className={`group flex flex-col border p-3 text-left disabled:cursor-not-allowed disabled:bg-[color:var(--color-fill)] ${
                isSelected
                  ? 'border-[color:var(--color-sig)] bg-[color:var(--color-sig-wash)] outline outline-1 outline-[color:var(--color-sig)]'
                  : 'border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] hover:border-[color:var(--color-ink)] disabled:hover:border-[color:var(--color-rule)]'
              }`}
            >
              <span className="flex flex-wrap items-center gap-2">
                <span className={`text-[14px] font-bold ${verdict.ok ? '' : 'text-[color:var(--color-ink-3)]'}`}>
                  {t(`recognition.provider.${dest}`)}
                </span>
                {isRecommended && <Badge tone="ok">{t('modelsHub.recommended')}</Badge>}
                {verdict.publish && <Badge tone="neutral">{t('modelsHub.publishBadge')}</Badge>}
              </span>

              {verdict.ok ? (
                <>
                  <span className="mt-1 block text-[12px] text-[color:var(--color-ink)]">
                    {dest === 'local'
                      ? t('modelsHub.dest.localBody', { runtime: localRuntimeLabel(model.local.runtime) })
                      : t(`modelsHub.dest.${dest}Body`)}
                  </span>
                  <span className="mt-2 block border-t border-[color:var(--color-rule)] pt-1.5 text-[11px] text-[color:var(--color-ink-2)]">
                    <span className="lbl !mb-0 inline">{t('modelsHub.youWillNeed')}</span>{' '}
                    {dest === 'local' && model.local.runtime === 'mlx-vlm'
                      ? t('modelsHub.dest.localNeedsMlx', { repo: model.local.mlx_repo ?? model.hf_repo })
                      : dest === 'local'
                        ? t('modelsHub.dest.localNeedsVllm', {
                            size: model.approx_size_gb != null ? `~${model.approx_size_gb} GB` : '—',
                          })
                        : t(`modelsHub.dest.${dest}Needs`)}
                  </span>
                  {dest === 'local' && model.vram_warning && (
                    <span className="mt-1.5 flex items-start gap-1 text-[11px] text-[color:var(--color-warn)]">
                      <IconWarn size={12} />
                      <span>{model.vram_warning}</span>
                    </span>
                  )}
                </>
              ) : (
                <span className="mt-1 block text-[11px] text-[color:var(--color-ink-2)]">
                  {verdict.reasonKey ? t(verdict.reasonKey) : null}
                </span>
              )}
            </button>
          )
        })}
      </div>
    </Module>
  )
}
