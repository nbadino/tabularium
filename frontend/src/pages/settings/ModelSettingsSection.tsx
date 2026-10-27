/** Parametri per modello: defaults di ricetta, override persistenti. */
import { useEffect, useMemo, useState } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import { useI18n } from '../../i18n'
import { Badge, ErrorNotice, Module, Notice, SkeletonLines } from '../../app/ui'
import { useInference } from '../../app/inference'
import type { SectionProps } from './SettingsPage'
import WorkflowFields, { type SettingValue, type WorkflowFieldSpec } from './WorkflowFields'

type Values = Record<string, Record<string, SettingValue>>
interface ModelSettingsItem {
  adapter_id: string
  display_name: string
  recommended: Values
  overrides: Values
  effective: Values
  restart_required: boolean
  /** Vero quando il modello espone il pannello MLX (letto dal payload, non da una lista). */
  mlx_available?: boolean
  /** Campi di sampling che il vendor rifiuta: il pannello li nasconde. */
  generation_unsupported?: string[]
  /** Il vendor non espone `dtype` (wrapper ufficiale MonkeyOCRv2). */
  serving_dtype?: boolean
  /** Il modello ha un logits processor n-gram: solo allora `no_repeat_ngram_size` ha senso. */
  no_repeat_ngram?: boolean
  /** Schema dei campi workflow: il pannello si genera da qui. */
  workflow_fields?: WorkflowFieldSpec[]
  workflow_intro?: string
  sections?: string[]
  /** Varianti MLX selezionabili della stessa conversione (es. bf16, 8-bit). */
  mlx_repo_choices?: { variant: string; repo: string }[]
}


const SERVING_FIELDS = [
  { key: 'gpu_memory_utilization', min: 0.2, max: 0.98, step: 0.01 },
  { key: 'max_model_len', min: 512, max: 131072, step: 512 },
  { key: 'max_num_seqs', min: 1, max: 128, step: 1 },
  { key: 'max_num_batched_tokens', min: 512, max: 131072, step: 512 },
] as const
const GENERATION_FIELDS = [
  { key: 'temperature', min: 0, max: 2, step: 0.05 },
  { key: 'top_p', min: 0.000001, max: 1, step: 0.00001 },
  { key: 'top_k', min: 1, max: 1000, step: 1 },
  { key: 'max_tokens', min: 1, max: 131072, step: 128 },
  { key: 'repetition_penalty', min: 0.5, max: 2, step: 0.05 },
  { key: 'presence_penalty', min: -2, max: 2, step: 0.05 },
  { key: 'frequency_penalty', min: -2, max: 2, step: 0.005 },
  { key: 'no_repeat_ngram_size', min: 0, max: 512, step: 1 },
] as const
export default function ModelSettingsSection({ isAdmin }: SectionProps) {
  const { t } = useI18n()
  const inference = useInference()
  const [items, setItems] = useState<ModelSettingsItem[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [draft, setDraft] = useState<Values>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiGet<{ items: ModelSettingsItem[] }>('/system/model-settings')
      setItems(res.items)
      const nextId = selectedId || res.items.find((item) => item.adapter_id === inference.adapterId)?.adapter_id || res.items[0]?.adapter_id || ''
      setSelectedId(nextId)
      const next = res.items.find((item) => item.adapter_id === nextId)
      setDraft(next ? JSON.parse(JSON.stringify(next.effective)) : {})
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])
  const selected = useMemo(() => items.find((item) => item.adapter_id === selectedId) ?? null, [items, selectedId])

  const update = (section: string, key: string, raw: string) => {
    const value = raw === '' ? null : Number(raw)
    setDraft((current) => ({
      ...current,
      [section]: { ...(current[section] ?? {}), [key]: value },
    }))
    setSaved(false)
  }

  const payload = (): Values => {
    const result: Values = {}
    for (const section of ['serving', 'generation', 'image', 'workflow', 'mlx']) {
      const values = draft[section] ?? {}
      const defaults = selected?.recommended[section] ?? {}
      const changed = Object.fromEntries(
        Object.entries(values).filter(([key, value]) => value !== null && JSON.stringify(value) !== JSON.stringify(defaults[key])),
      ) as Record<string, SettingValue>
      if (Object.keys(changed).length) result[section] = changed
    }
    return result
  }

  const save = async (reset = false) => {
    if (!selected || !isAdmin) return
    setSaving(true)
    setError(null)
    try {
      const next = await apiPut<ModelSettingsItem>(`/system/model-settings/${selected.adapter_id}`, reset ? {} : payload())
      setItems((current) => current.map((item) => item.adapter_id === next.adapter_id ? next : item))
      setDraft(JSON.parse(JSON.stringify(next.effective)))
      setSaved(!reset)
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }

  const numberField = (section: string, key: string, label: string, min: number, max: number, step: number, automatic = false) => {
    const value = draft[section]?.[key]
    const recommended = selected?.recommended[section]?.[key]
    return (
      <label key={`${section}.${key}`} className="block border-t border-[color:var(--color-rule)] pt-2">
        <span className="lbl" title={`${section}.${key}`}>{label}</span>
        <input
          type="number" min={min} max={max} step={step}
          value={typeof value === 'boolean' || Array.isArray(value) || (typeof value === 'object' && value !== null) ? '' : value ?? ''}
          placeholder={automatic ? t('settings.modelAuto') : recommended == null ? t('settings.modelAuto') : String(recommended)}
          disabled={!isAdmin || loading || saving}
          onChange={(event) => update(section, key, event.target.value)}
          className="fld mt-1 w-full"
        />
        <span className="mt-1 block text-[10px] text-[color:var(--color-ink-3)]">
          {recommended == null ? t('settings.modelUsesAdapter') : t('settings.modelRecommended', { value: String(recommended) })}
        </span>
      </label>
    )
  }

  return (
    <div className="space-y-3">
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
      {saved && <Notice tone="ok">{t('settings.modelSaved')}</Notice>}
      {!isAdmin && <Notice tone="warn">{t('settings.modelAdminOnly')}</Notice>}
      <Module tab={t('settings.modelTuning')} quiet aux={<Badge tone={selected?.restart_required ? 'warn' : 'ok'}>{selected?.restart_required ? t('settings.modelRestart') : t('settings.modelRecommendedBadge')}</Badge>}>
        <p className="mb-3 max-w-[75ch] text-[12px] text-[color:var(--color-ink-2)]">{t('settings.modelTuningIntro')}</p>
        <label className="mb-3 block max-w-xl">
          <span className="lbl">{t('settings.modelChoose')}</span>
          <select className="fld mt-1 w-full" value={selectedId} onChange={(event) => {
            const nextId = event.target.value
            setSelectedId(nextId)
            const next = items.find((item) => item.adapter_id === nextId)
            setDraft(next ? JSON.parse(JSON.stringify(next.effective)) : {})
            setSaved(false)
          }} disabled={loading}>
            {items.map((item) => <option key={item.adapter_id} value={item.adapter_id}>{item.display_name}</option>)}
          </select>
        </label>
        {loading && <SkeletonLines lines={5} label={t('common.loading')} />}
        {selected && (
          <>
            {selected.restart_required && <Notice tone="warn">{t('settings.modelRestartHint')}</Notice>}
            <div className="grid gap-4 lg:grid-cols-3">
              <section>
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelRemoteRuntime')}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
                  {selected.serving_dtype !== false && (
                    <label className="block border-t border-[color:var(--color-rule)] pt-2">
                      <span className="lbl">{t('settings.modelField.dtype')}</span>
                      <select className="fld mt-1 w-full" value={String(draft.serving?.dtype ?? '')} disabled={!isAdmin || loading || saving}
                        onChange={(event) => { setDraft((current) => ({ ...current, serving: { ...(current.serving ?? {}), dtype: event.target.value || null } })); setSaved(false) }}>
                        <option value="">{t('settings.modelAuto')}</option>
                        {(['half', 'float16', 'bfloat16', 'float', 'float32'] as const).map((value) => <option key={value} value={value}>{value}</option>)}
                      </select>
                      <span className="mt-1 block text-[10px] text-[color:var(--color-ink-3)]">{t('settings.modelDtypeHint')}</span>
                    </label>
                  )}
                  {SERVING_FIELDS.map((field) => numberField('serving', field.key, t(`settings.modelField.${field.key}`), field.min, field.max, field.step))}
                </div>
              </section>
              <section>
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelGeneration')}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
                  {GENERATION_FIELDS.filter((field) => {
                    if (field.key === 'no_repeat_ngram_size') return selected.no_repeat_ngram === true
                    if (selected.generation_unsupported?.includes(field.key)) return false
                    return true
                  }).map((field) => numberField('generation', field.key, t(`settings.modelField.${field.key}`), field.min, field.max, field.step, true))}
                </div>
              </section>
              <section>
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelImage')}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
                  {selected.adapter_id !== 'mineru2.5' && numberField('image', 'max_pixels', t('settings.modelField.max_pixels'), 0, 128000000, 1000, true)}
                  {['paddleocr-vl', 'qwen3-vl-8b'].includes(selected.adapter_id) && numberField('image', 'min_pixels', t('settings.modelField.min_pixels'), 1, 64000000, 1000, true)}
                </div>
                <p className="mt-2 text-[10px] text-[color:var(--color-ink-3)]">{t('settings.modelPixelsHint')}</p>
              </section>
            </div>
            {selected.mlx_available && (
              <section className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-1 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelMlxRuntime')}</h3>
                <p className="mb-3 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t('settings.modelMlxIntro')}</p>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {(selected.mlx_repo_choices?.length ?? 0) > 0 && (
                    <label className="block border-t border-[color:var(--color-rule)] pt-2">
                      <span className="lbl">{t('settings.mlxField.repo')}</span>
                      <select className="fld mt-1 w-full" value={String(draft.mlx?.repo ?? '')} disabled={!isAdmin || loading || saving}
                        onChange={(event) => { setDraft((current) => ({ ...current, mlx: { ...(current.mlx ?? {}), repo: event.target.value || null } })); setSaved(false) }}>
                        <option value="">{t('settings.modelAuto')}</option>
                        {selected.mlx_repo_choices?.map((choice) => <option key={choice.repo} value={choice.repo}>{choice.variant} · {choice.repo}</option>)}
                      </select>
                      <span className="mt-1 block text-[10px] text-[color:var(--color-ink-3)]">{t('settings.mlxRepoHint')}</span>
                    </label>
                  )}
                  {([
                    ['kv_bits', 2, 8, 0.5], ['kv_group_size', 16, 256, 16],
                    ['max_kv_size', 512, 131072, 512], ['vision_cache_size', 0, 1024, 1],
                  ] as const).map(([key, min, max, step]) => numberField('mlx', key, t(`settings.mlxField.${key}`), min, max, step, true))}
                  {selected.recommended?.mlx?.repetition_penalty != null && (
                    <>
                      {numberField('mlx', 'repetition_penalty', t('settings.mlxField.repetition_penalty'), 0.5, 2, 0.05, true)}
                      {numberField('mlx', 'repetition_context_size', t('settings.mlxField.repetition_context_size'), 1, 2048, 1, true)}
                    </>
                  )}
                  {(['kv_quant_scheme', 'log_level'] as const).map((key) => (
                    <label key={key} className="block border-t border-[color:var(--color-rule)] pt-2">
                      <span className="lbl">{t(`settings.mlxField.${key}`)}</span>
                      <select className="fld mt-1 w-full" value={String(draft.mlx?.[key] ?? '')} disabled={!isAdmin || loading || saving}
                        onChange={(event) => { setDraft((current) => ({ ...current, mlx: { ...(current.mlx ?? {}), [key]: event.target.value || null } })); setSaved(false) }}>
                        <option value="">{t('settings.modelAuto')}</option>
                        {(key === 'kv_quant_scheme' ? ['uniform', 'turboquant'] : ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']).map((value) => <option key={value} value={value}>{value}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
                {selected.recommended?.mlx?.repetition_penalty != null && (
                  <p className="mt-2 text-[10px] text-[color:var(--color-ink-3)]">{t('settings.mlxRepetitionHint')}</p>
                )}
              </section>
            )}
            {(selected.workflow_fields?.length ?? 0) > 0 && (
              <section className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelWorkflow')}</h3>
                {selected.workflow_intro && (
                  <p className="mb-3 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t(selected.workflow_intro)}</p>
                )}
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  <WorkflowFields
                    fields={selected.workflow_fields ?? []}
                    values={draft.workflow}
                    disabled={!isAdmin || loading || saving}
                    onChange={(key, value) => {
                      setDraft((current) => ({ ...current, workflow: { ...(current.workflow ?? {}), [key]: value } }))
                      setSaved(false)
                    }}
                  />
                </div>
              </section>
            )}
            <div className="mt-4 flex flex-wrap gap-2 border-t border-[color:var(--color-rule)] pt-3">
              <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={!isAdmin || saving || loading}>{saving ? t('common.saving') : t('settings.modelSave')}</button>
              <button type="button" className="btn" onClick={() => void save(true)} disabled={!isAdmin || saving || loading}>{t('settings.modelReset')}</button>
            </div>
          </>
        )}
      </Module>
    </div>
  )
}
