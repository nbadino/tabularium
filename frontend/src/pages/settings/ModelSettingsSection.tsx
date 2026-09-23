/** Parametri per modello: defaults di ricetta, override persistenti. */
import { useEffect, useMemo, useState } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import { useI18n } from '../../i18n'
import { Badge, ErrorNotice, Module, Notice } from '../../app/ui'
import type { SectionProps } from './SettingsPage'

type SettingValue = number | string | boolean | null
type Values = Record<string, Record<string, SettingValue>>
interface ModelSettingsItem {
  adapter_id: string
  display_name: string
  recommended: Values
  overrides: Values
  effective: Values
  restart_required: boolean
}

const SERVING_FIELDS = [
  { key: 'gpu_memory_utilization', min: 0.2, max: 0.98, step: 0.01 },
  { key: 'max_model_len', min: 512, max: 131072, step: 512 },
  { key: 'max_num_seqs', min: 1, max: 128, step: 1 },
  { key: 'max_num_batched_tokens', min: 512, max: 131072, step: 512 },
] as const
const GENERATION_FIELDS = [
  { key: 'temperature', min: 0, max: 2, step: 0.05 },
  { key: 'top_p', min: 0.01, max: 1, step: 0.01 },
  { key: 'top_k', min: 1, max: 1000, step: 1 },
  { key: 'max_tokens', min: 1, max: 131072, step: 128 },
  { key: 'repetition_penalty', min: 0.5, max: 2, step: 0.05 },
  { key: 'presence_penalty', min: -2, max: 2, step: 0.05 },
  { key: 'frequency_penalty', min: -2, max: 2, step: 0.005 },
  { key: 'no_repeat_ngram_size', min: 0, max: 512, step: 1 },
] as const
const PADDLE_WORKFLOW_FIELDS = [
  'use_layout_detection', 'layout_nms', 'use_doc_orientation_classify', 'use_doc_unwarping',
  'use_chart_recognition', 'use_seal_recognition', 'use_ocr_for_image_block',
  'format_block_content', 'merge_layout_blocks', 'use_queues',
] as const
const PADDLE_LAYOUT_NUMBERS = [
  { key: 'layout_threshold', min: 0, max: 1, step: 0.01 },
  { key: 'layout_unclip_ratio', min: 0.01, max: 10, step: 0.05 },
] as const

export default function ModelSettingsSection({ isAdmin }: SectionProps) {
  const { t } = useI18n()
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
      setSelectedId((current) => current || res.items[0]?.adapter_id || '')
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])
  const selected = useMemo(() => items.find((item) => item.adapter_id === selectedId) ?? null, [items, selectedId])
  useEffect(() => {
    if (selected) setDraft(JSON.parse(JSON.stringify(selected.effective)))
  }, [selected])

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
        Object.entries(values).filter(([key, value]) => value !== null && value !== defaults[key]),
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
        <span className="lbl">{label}</span>
        <input
          type="number" min={min} max={max} step={step}
          value={typeof value === 'boolean' ? '' : value ?? ''}
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
          <select className="fld mt-1 w-full" value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setSaved(false) }} disabled={loading}>
            {items.map((item) => <option key={item.adapter_id} value={item.adapter_id}>{item.display_name}</option>)}
          </select>
        </label>
        {loading && <p className="text-[12px]">{t('common.loading')}</p>}
        {selected && (
          <>
            {selected.restart_required && <Notice tone="warn">{t('settings.modelRestartHint')}</Notice>}
            <div className="grid gap-4 lg:grid-cols-3">
              <section>
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelRemoteRuntime')}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
                  {SERVING_FIELDS.map((field) => numberField('serving', field.key, t(`settings.modelField.${field.key}`), field.min, field.max, field.step))}
                </div>
              </section>
              <section>
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelGeneration')}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
                  {GENERATION_FIELDS.map((field) => numberField('generation', field.key, t(`settings.modelField.${field.key}`), field.min, field.max, field.step, true))}
                </div>
              </section>
              <section>
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelImage')}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
                  {numberField('image', 'max_pixels', t('settings.modelField.max_pixels'), 0, 64000000, 1000, true)}
                  {selected.adapter_id === 'paddleocr-vl' && numberField('image', 'min_pixels', t('settings.modelField.min_pixels'), 1, 64000000, 1000, true)}
                </div>
                <p className="mt-2 text-[10px] text-[color:var(--color-ink-3)]">{t('settings.modelPixelsHint')}</p>
              </section>
            </div>
            {['paddleocr-vl', 'qwen3-vl-8b'].includes(selected.adapter_id) && (
              <section className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-1 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelMlxRuntime')}</h3>
                <p className="mb-3 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t('settings.modelMlxIntro')}</p>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {([
                    ['kv_bits', 2, 8, 0.5], ['kv_group_size', 16, 256, 16],
                    ['max_kv_size', 512, 131072, 512], ['vision_cache_size', 0, 1024, 1],
                  ] as const).map(([key, min, max, step]) => numberField('mlx', key, t(`settings.mlxField.${key}`), min, max, step, true))}
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
              </section>
            )}
            {selected.adapter_id === 'teleocr' && (
              <section className="mt-4 max-w-xl border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelWorkflow')}</h3>
                <label className="block">
                  <span className="lbl">{t('settings.teleocrLayoutMode')}</span>
                  <select
                    className="fld mt-1 w-full"
                    value={String(draft.workflow?.layout_mode ?? 'Detection')}
                    disabled={!isAdmin || loading || saving}
                    onChange={(event) => {
                      setDraft((current) => ({
                        ...current,
                        workflow: { ...(current.workflow ?? {}), layout_mode: event.target.value },
                      }))
                      setSaved(false)
                    }}
                  >
                    <option value="Detection">Detection · {t('settings.teleocrDetectionHint')}</option>
                    <option value="Segmentation">Segmentation · {t('settings.teleocrSegmentationHint')}</option>
                  </select>
                </label>
                <p className="mt-1 text-[10px] text-[color:var(--color-ink-3)]">{t('settings.teleocrLayoutOfficial')}</p>
              </section>
            )}
            {selected.adapter_id === 'glm-ocr' && (
              <section className="mt-4 max-w-xl border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelWorkflow')}</h3>
                <p className="mb-3 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t('settings.glmWorkflowIntro')}</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {numberField('workflow', 'speculative_tokens', t('settings.glmSpeculativeTokens'), 1, 16, 1)}
                </div>
              </section>
            )}
            {selected.adapter_id === 'deepseek-ocr' && (
              <section className="mt-4 max-w-xl border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-2 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelWorkflow')}</h3>
                <p className="mb-3 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t('settings.deepseekWorkflowIntro')}</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {numberField('workflow', 'ngram_size', t('settings.deepseekNgramSize'), 1, 256, 1)}
                  {numberField('workflow', 'window_size', t('settings.deepseekWindowSize'), 1, 1024, 1)}
                </div>
              </section>
            )}
            {selected.adapter_id === 'paddleocr-vl' && (
              <section className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
                <h3 className="mb-1 text-[12px] font-bold uppercase tracking-wide">{t('settings.modelWorkflow')}</h3>
                <p className="mb-3 max-w-[75ch] text-[11px] text-[color:var(--color-ink-2)]">{t('settings.paddleWorkflowIntro')}</p>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {PADDLE_WORKFLOW_FIELDS.map((key) => (
                    <label key={key} className="block border-t border-[color:var(--color-rule)] pt-2">
                      <span className="lbl">{t(`settings.paddleWorkflow.${key}`)}</span>
                      <select
                        className="fld mt-1 w-full"
                        value={draft.workflow?.[key] == null ? 'auto' : String(draft.workflow[key])}
                        disabled={!isAdmin || loading || saving}
                        onChange={(event) => {
                          const raw = event.target.value
                          setDraft((current) => ({
                            ...current,
                            workflow: { ...(current.workflow ?? {}), [key]: raw === 'auto' ? null : raw === 'true' },
                          }))
                          setSaved(false)
                        }}
                      >
                        <option value="auto">{t('settings.modelAuto')}</option>
                        <option value="true">{t('settings.paddleEnabled')}</option>
                        <option value="false">{t('settings.paddleDisabled')}</option>
                      </select>
                    </label>
                  ))}
                  {PADDLE_LAYOUT_NUMBERS.map((field) => numberField(
                    'workflow', field.key, field.key,
                    field.min, field.max, field.step, true,
                  ))}
                  <label className="block border-t border-[color:var(--color-rule)] pt-2">
                    <span className="lbl">layout_merge_bboxes_mode</span>
                    <select
                      className="fld mt-1 w-full"
                      value={String(draft.workflow?.layout_merge_bboxes_mode ?? '')}
                      disabled={!isAdmin || loading || saving}
                      onChange={(event) => {
                        const value = event.target.value || null
                        setDraft((current) => ({
                          ...current,
                          workflow: { ...(current.workflow ?? {}), layout_merge_bboxes_mode: value },
                        }))
                        setSaved(false)
                      }}
                    >
                      <option value="">{t('settings.modelAuto')}</option>
                      <option value="large">large</option>
                      <option value="small">small</option>
                      <option value="union">union</option>
                    </select>
                  </label>
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
