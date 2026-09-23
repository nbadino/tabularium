/**
 * Passo 1 dell'hub Modelli: quale modello.
 *
 * Una riga dice tre cose e offre un gesto solo: cosa sa fare il modello,
 * quanto è maturo e se gira su questa macchina; «Scegli» porta al passo
 * della destinazione. Download, avvio e deploy non stanno qui: dipendono da
 * dove lo si esegue, che non è ancora stato deciso.
 */
import { useState } from 'react'
import { apiDelete, apiPost } from '../../lib/api'
import { localRuntimeLabel } from '../../lib/vocab'
import { useI18n } from '../../i18n'
import { Badge, Module, Notice } from '../ui'
import { useInference } from '../inference'
import { isCustom, isSelectable, type ModelItem } from './registry'

const MATURITY_KEY: Record<string, string> = {
  supported: 'cloud.models.maturitySupported',
  experimental: 'cloud.models.maturityExperimental',
  catalog: 'cloud.models.maturityCatalog',
  unavailable: 'cloud.models.maturityUnavailable',
}

const MATURITY_TONE: Record<string, 'ok' | 'progress' | 'neutral' | 'warn'> = {
  supported: 'ok',
  experimental: 'progress',
  catalog: 'neutral',
  unavailable: 'warn',
}

const TASK_ORDER = ['layout', 'text', 'table', 'formula']

interface CustomModelForm {
  display_name: string
  hf_repo: string
  hf_revision: string
  served_model_name: string
  trust_remote_code: boolean
  max_model_len: string
  gpu_memory_utilization: string
  extra_args: string
}

const EMPTY_CUSTOM_FORM: CustomModelForm = {
  display_name: '',
  hf_repo: '',
  hf_revision: '',
  served_model_name: '',
  trust_remote_code: false,
  max_model_len: '',
  gpu_memory_utilization: '',
  extra_args: '',
}

interface ModelPickerProps {
  models: ModelItem[]
  loading: boolean
  selectedId: string | null
  onSelect: (model: ModelItem) => void
  /** Il registro va riletto dopo aver aggiunto o tolto un modello. */
  onChanged: () => Promise<void>
}

export function ModelPicker({ models, loading, selectedId, onSelect, onChanged }: ModelPickerProps) {
  const { t } = useI18n()
  const inf = useInference()
  const [query, setQuery] = useState('')
  const [onlyHere, setOnlyHere] = useState(false)
  const [showAddCustom, setShowAddCustom] = useState(false)
  const [customForm, setCustomForm] = useState<CustomModelForm>(EMPTY_CUSTOM_FORM)
  const [customBusy, setCustomBusy] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const runnableHere = models.filter((m) => m.local?.runnable).length
  const needle = query.trim().toLocaleLowerCase()
  const visible = models.filter((m) => {
    if (onlyHere && !m.local?.runnable) return false
    if (!needle) return true
    return `${m.display_name} ${m.adapter_id} ${m.hf_repo}`.toLocaleLowerCase().includes(needle)
  })

  const taskSummary = (m: ModelItem) =>
    TASK_ORDER.filter((task) => m.tasks.includes(task))
      .map((task) =>
        task === 'table' && m.table_format
          ? t('modelsHub.taskTableFormat', { format: m.table_format.toUpperCase() })
          : t(`modelsHub.task.${task}`),
      )
      .join(' · ')

  const handleAddCustom = async () => {
    setCustomBusy(true)
    setNotice(null)
    try {
      await apiPost('/models/custom', {
        display_name: customForm.display_name.trim() || undefined,
        hf_repo: customForm.hf_repo.trim(),
        hf_revision: customForm.hf_revision.trim() || undefined,
        served_model_name: customForm.served_model_name.trim() || undefined,
        trust_remote_code: customForm.trust_remote_code,
        max_model_len: customForm.max_model_len.trim() ? Number(customForm.max_model_len) : undefined,
        gpu_memory_utilization: customForm.gpu_memory_utilization.trim()
          ? Number(customForm.gpu_memory_utilization)
          : undefined,
        extra_args: customForm.extra_args.trim() || undefined,
      })
      setCustomForm(EMPTY_CUSTOM_FORM)
      setShowAddCustom(false)
      await onChanged()
    } catch (e) {
      setNotice(t('cloud.models.addCustomError', { error: String(e) }))
    } finally {
      setCustomBusy(false)
    }
  }

  const handleRemoveCustom = async (adapterId: string) => {
    setBusyId(adapterId)
    try {
      await apiDelete(`/models/custom/${adapterId}`)
      await onChanged()
    } catch (e) {
      setNotice(t('cloud.models.deleteError', { error: String(e) }))
    } finally {
      setBusyId(null)
    }
  }

  const field = (key: keyof CustomModelForm, label: string, opts: { placeholder?: string; type?: string; wide?: boolean } = {}) => (
    <label className={`flex flex-col gap-1 text-[11px] ${opts.wide ? 'sm:col-span-2' : ''}`}>
      {label}
      <input
        className="fld fld-mono"
        type={opts.type ?? 'text'}
        placeholder={opts.placeholder}
        value={customForm[key] as string}
        onChange={(e) => setCustomForm((f) => ({ ...f, [key]: e.target.value }))}
      />
    </label>
  )

  return (
    <Module
      tab={`1 · ${t('modelsHub.step.model')}`}
      flush
      aux={<span className="text-[11px] text-[color:var(--color-ink-2)]">{t('modelsHub.pickModelAux', { n: String(models.length) })}</span>}
    >
      <div className="flex flex-wrap items-end gap-3 border-b border-[color:var(--color-rule)] p-3">
        <label className="block min-w-[16rem] flex-1">
          <span className="lbl">{t('modelsHub.searchLabel')}</span>
          <input
            className="fld"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('modelsHub.searchPlaceholder')}
          />
        </label>
        {/* Filtro, non ordinamento: chi vuole restare sulla propria macchina
            vede subito quanti modelli può usare davvero. */}
        <div role="group" aria-label={t('modelsHub.filterLabel')} className="flex">
          <button
            type="button"
            aria-pressed={!onlyHere}
            className={`btn btn-sm ${!onlyHere ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`}
            onClick={() => setOnlyHere(false)}
          >
            {t('modelsHub.filterAll', { n: String(models.length) })}
          </button>
          <button
            type="button"
            aria-pressed={onlyHere}
            className={`btn btn-sm -ml-px ${onlyHere ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`}
            onClick={() => setOnlyHere(true)}
          >
            {t('modelsHub.filterHere', { n: String(runnableHere) })}
          </button>
        </div>
      </div>

      {notice && <div className="px-3"><Notice tone="warn">{notice}</Notice></div>}

      <ul className="divide-y divide-[color:var(--color-rule)]">
        {visible.map((m) => {
          const selected = selectedId === m.adapter_id
          const inUse = inf.enabled && inf.adapterId === m.adapter_id && !!inf.provider
          const selectable = isSelectable(m)
          return (
            <li
              key={m.adapter_id}
              className={`flex flex-wrap items-center gap-x-4 gap-y-1 py-2.5 pr-3 ${selected ? 'border-l-[3px] border-l-[color:var(--color-sig)] bg-[color:var(--color-sig-wash)] pl-[9px]' : 'bg-[color:var(--color-sheet)] pl-3'}`}
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[14px] font-bold">{m.display_name}</span>
                  {MATURITY_KEY[m.maturity] && (
                    <Badge tone={MATURITY_TONE[m.maturity] ?? 'neutral'}>{t(MATURITY_KEY[m.maturity])}</Badge>
                  )}
                  {m.adapter_id === 'monkeyocrv2-parsing' && <Badge tone="ok">{t('cloud.models.defaultBadge')}</Badge>}
                  {isCustom(m) && <Badge tone="neutral">{t('cloud.models.customBadge')}</Badge>}
                  {inUse && <Badge tone="sig">{t('cloud.models.inUseNow')}</Badge>}
                </div>
                <div className="mt-0.5 text-[12px] text-[color:var(--color-ink-2)]">
                  {taskSummary(m) || '—'}
                  {m.approx_size_gb != null && (
                    <span className="mono text-[color:var(--color-ink-3)]"> · ~{m.approx_size_gb} GB</span>
                  )}
                </div>
                <details className="mt-0.5 text-[11px] text-[color:var(--color-ink-3)]">
                  <summary className="cursor-pointer">{t('modelsHub.showDetails')}</summary>
                  <div className="mt-1 space-y-0.5">
                    <div className="mono">{m.hf_repo}</div>
                    {m.local?.mlx_repo && <div className="mono">MLX: {m.local.mlx_repo}</div>}
                    {m.license_note && <div>{m.license_note}</div>}
                    {isCustom(m) && (
                      <button
                        type="button"
                        className="btn btn-sm mt-1"
                        disabled={busyId === m.adapter_id}
                        onClick={() => void handleRemoveCustom(m.adapter_id)}
                      >
                        {t('cloud.models.removeCustom')}
                      </button>
                    )}
                  </div>
                </details>
              </div>

              {/* Il verdetto per questa macchina, scritto: non si scopre al
                  click. */}
              <div className="w-[13rem] text-[11px] leading-snug">
                {m.local?.runnable ? (
                  <span className="font-semibold text-[color:var(--color-ok)]">
                    {t('modelsHub.runsHere', { runtime: localRuntimeLabel(m.local.runtime) })}
                  </span>
                ) : selectable ? (
                  <span className="text-[color:var(--color-ink-2)]">{t('modelsHub.remoteOnly')}</span>
                ) : (
                  <span className="text-[color:var(--color-warn)]">{t('modelsHub.notUsableYet')}</span>
                )}
              </div>

              <button
                type="button"
                className={`btn btn-sm w-[7.5rem] justify-center ${selected || !selectedId ? 'btn-primary' : ''}`}
                disabled={!selectable}
                title={!selectable ? t('modelsHub.notUsableYetHint') : undefined}
                onClick={() => onSelect(m)}
              >
                {selected ? t('modelsHub.continue') : t('modelsHub.choose')}
              </button>
            </li>
          )
        })}
        {visible.length === 0 && (
          <li className="p-3 text-[12px] text-[color:var(--color-ink-2)]">
            {loading || models.length === 0 ? t('cloud.models.loading') : t('modelsHub.noMatch')}
          </li>
        )}
      </ul>

      <div className="border-t border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.models.addCustomHint')}</p>
          <button type="button" className="btn btn-sm" aria-expanded={showAddCustom} onClick={() => setShowAddCustom((v) => !v)}>
            {showAddCustom ? t('cloud.models.addCustomCollapse') : t('cloud.models.addCustomOpen')}
          </button>
        </div>
        {showAddCustom && (
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {field('hf_repo', t('cloud.models.fieldHfRepo'), { placeholder: 'org/model-name' })}
            {field('display_name', t('cloud.models.fieldDisplayName'))}
            {field('hf_revision', t('cloud.models.fieldHfRevision'))}
            {field('served_model_name', t('cloud.models.fieldServedName'))}
            {field('max_model_len', t('cloud.models.fieldMaxModelLen'), { type: 'number' })}
            {field('gpu_memory_utilization', t('cloud.models.fieldGpuMemUtil'), { type: 'number' })}
            {field('extra_args', t('cloud.models.fieldExtraArgs'), {
              placeholder: '--dtype bfloat16 --tensor-parallel-size 1',
              wide: true,
            })}
            <label className="flex items-center gap-2 text-[11px] sm:col-span-2">
              <input
                type="checkbox"
                checked={customForm.trust_remote_code}
                onChange={(e) => setCustomForm((f) => ({ ...f, trust_remote_code: e.target.checked }))}
              />
              {t('cloud.models.fieldTrustRemoteCode')}
            </label>
            <div className="sm:col-span-2">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={customBusy || !customForm.hf_repo.trim()}
                onClick={() => void handleAddCustom()}
              >
                {customBusy ? t('cloud.models.downloadStarting') : t('cloud.models.addCustomSubmit')}
              </button>
            </div>
          </div>
        )}
      </div>
    </Module>
  )
}
