/**
 * Model parameters, grouped by what the user wants to change (docs/MODELS_UX.md).
 *
 * Every value starts from the model's official recipe (`recommended`, audited in
 * docs/MODELS_AUDIT.md); only the values that differ are saved. The panel is tabs by
 * intent — essentials, image, text, model pipeline, server — each tab saying how many of
 * its values differ from the official ones, and a strip that lists every difference
 * wherever it lives, with its reset. Nothing settable before is missing: the tabs
 * regroup the same fields.
 *
 * Which fields exist comes from the payload (`image_fields`, `sections`,
 * `generation_unsupported`, `workflow_fields`…), never from the model's id.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import { useI18n } from '../../i18n'
import { Badge, ErrorNotice, Module, Notice, SkeletonLines } from '../../app/ui'
import { useInference } from '../../app/inference'
import { WorkflowControl, workflowLabel, workflowValueText, type SettingValue, type WorkflowFieldSpec } from './WorkflowFields'

type Values = Record<string, Record<string, SettingValue>>
type Section = 'serving' | 'generation' | 'image' | 'workflow' | 'mlx'
type TabId = 'essentials' | 'image' | 'text' | 'pipeline' | 'server'

export interface ModelSettingsItem {
  adapter_id: string
  display_name: string
  recommended: Values
  overrides: Values
  effective: Values
  restart_required: boolean
  mlx_available?: boolean
  generation_unsupported?: string[]
  serving_dtype?: boolean
  no_repeat_ngram?: boolean
  workflow_fields?: WorkflowFieldSpec[]
  workflow_intro?: string
  sections?: string[]
  image_fields?: string[]
  /** What the adapter sends per task before any override (backend `_task_sampling`). */
  task_sampling?: Record<string, Record<string, SettingValue>>
  mlx_repo_choices?: { variant: string; repo: string }[]
}

interface Props {
  isAdmin: boolean
  /** The model chosen in the hub. Without it the panel offers its own model choice. */
  adapterId?: string | null
  /** Where the model runs: decides which server group exists. */
  destination?: string | null
  /** Local runtime on this machine (`mlx-vlm` or `vllm`), for the local destination. */
  localRuntime?: string | null
}

const SECTIONS: Section[] = ['serving', 'generation', 'image', 'workflow', 'mlx']
const TASKS = ['layout', 'text', 'table', 'formula', 'end2end'] as const

// The native knobs, in the schema shape the vendor fields already use, so one renderer
// draws them all. Bounds mirror `services/model_settings.py`.
const num = (key: string, minimum: number, maximum: number, step: number, kind: 'int' | 'float' = 'float'): WorkflowFieldSpec =>
  ({ key, kind, minimum, maximum, step, nullable: true })
const GENERATION: WorkflowFieldSpec[] = [
  num('temperature', 0, 2, 0.05),
  num('top_p', 0.000001, 1, 0.00001),
  num('top_k', 1, 1000, 1, 'int'),
  num('repetition_penalty', 0.5, 2, 0.05),
  num('presence_penalty', -2, 2, 0.05),
  num('frequency_penalty', -2, 2, 0.005),
  num('no_repeat_ngram_size', 0, 512, 1, 'int'),
]
const MAX_TOKENS = num('max_tokens', 1, 131072, 128, 'int')
const IMAGE: Record<string, WorkflowFieldSpec> = {
  max_pixels: num('max_pixels', 0, 128000000, 1000, 'int'),
  min_pixels: num('min_pixels', 1, 64000000, 1000, 'int'),
}
const SERVING: WorkflowFieldSpec[] = [
  { key: 'dtype', kind: 'enum', values: ['bfloat16', 'float16', 'half', 'float', 'float32'], nullable: true },
  num('gpu_memory_utilization', 0.2, 0.98, 0.01),
  num('max_model_len', 512, 131072, 512, 'int'),
  num('max_num_seqs', 1, 128, 1, 'int'),
  num('max_num_batched_tokens', 512, 131072, 512, 'int'),
]
const MLX_SERVER: WorkflowFieldSpec[] = [
  num('kv_bits', 2, 8, 0.5),
  num('kv_group_size', 16, 256, 16, 'int'),
  { key: 'kv_quant_scheme', kind: 'enum', values: ['uniform', 'turboquant'], nullable: true },
  num('max_kv_size', 512, 131072, 512, 'int'),
  num('vision_cache_size', 0, 1024, 1, 'int'),
  { key: 'log_level', kind: 'enum', values: ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], nullable: true },
]
const MLX_GUARD: WorkflowFieldSpec[] = [
  num('repetition_penalty', 0.5, 2, 0.05),
  num('repetition_context_size', 1, 2048, 1, 'int'),
  num('presence_context_size', 1, 2048, 1, 'int'),
]

interface Param {
  section: Section
  field: WorkflowFieldSpec
  tab: TabId
  label: string
  help: string
  official: SettingValue | undefined
  officialText: string
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

export default function ModelSettingsSection({ isAdmin, adapterId, destination, localRuntime }: Props) {
  const { t, tn, locale } = useI18n()
  const inference = useInference()
  const [items, setItems] = useState<ModelSettingsItem[]>([])
  const [chosenId, setChosenId] = useState('')
  // Edits belong to one model: switching model (or saving) starts again from what is saved.
  const [edits, setEdits] = useState<{ id: string; values: Values } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const [tab, setTab] = useState<TabId>('essentials')

  const selectedId = adapterId || chosenId || inference.adapterId || items[0]?.adapter_id || ''
  const selected = useMemo(() => items.find((item) => item.adapter_id === selectedId) ?? null, [items, selectedId])

  useEffect(() => {
    let alive = true
    setLoading(true)
    apiGet<{ items: ModelSettingsItem[] }>('/system/model-settings')
      .then((res) => { if (alive) setItems(res.items) })
      .catch((e) => { if (alive) setError(e) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  const draft: Values = edits && selected && edits.id === selected.adapter_id ? edits.values : (selected?.effective ?? {})

  const pixels = new Intl.NumberFormat(locale, { maximumFractionDigits: 1 })
  const plain = new Intl.NumberFormat(locale, { maximumFractionDigits: 6 })
  const show = (key: string, value: SettingValue | undefined): string => {
    if (value == null) return t('settings.params.vendorAuto')
    if (typeof value === 'number' && key.endsWith('_pixels')) {
      return `${plain.format(value)} (${pixels.format(value / 1_000_000)} MP)`
    }
    if (typeof value === 'number') return plain.format(value)
    return String(value)
  }

  // Sampling is per task: say what each task really sends instead of "automatic".
  const perTask = (key: string): string => {
    const byValue = new Map<string, string[]>()
    for (const task of TASKS) {
      const value = selected?.task_sampling?.[task]?.[key]
      if (value == null) continue
      const text = show(key, value)
      byValue.set(text, [...(byValue.get(text) ?? []), t(`settings.params.task.${task}`)])
    }
    if (byValue.size === 0) return t('settings.params.modelDecides')
    const sent = Object.keys(selected?.task_sampling ?? {}).length
    if (byValue.size === 1 && [...byValue.values()][0].length === sent) return [...byValue.keys()][0]
    return [...byValue.entries()].map(([value, tasks]) => `${value} (${tasks.join(', ')})`).join(' · ')
  }

  const params = useMemo<Param[]>(() => {
    if (!selected) return []
    const sections = new Set(selected.sections ?? SECTIONS)
    const rec = selected.recommended
    const out: Param[] = []
    const native = (section: Section, field: WorkflowFieldSpec, tab: TabId, labelKey: string) => {
      const official = rec[section]?.[field.key]
      out.push({
        section, field, tab,
        label: t(`settings.params.${labelKey}.label`),
        help: t(`settings.params.${labelKey}.help`),
        official,
        officialText: official == null && section === 'generation' ? perTask(field.key) : show(field.key, official),
      })
    }
    // Essentials: what changes the result or the time per page.
    const imageFields = selected.image_fields ?? []
    if (sections.has('image') && imageFields.includes('max_pixels')) native('image', IMAGE.max_pixels, 'essentials', 'max_pixels')
    const unsupported = new Set(selected.generation_unsupported ?? [])
    if (sections.has('generation') && !unsupported.has('max_tokens')) native('generation', MAX_TOKENS, 'essentials', 'max_tokens')
    if (sections.has('workflow')) {
      for (const field of selected.workflow_fields ?? []) {
        out.push({
          section: 'workflow', field, tab: field.essential ? 'essentials' : 'pipeline',
          label: workflowLabel(field), help: '',
          official: rec.workflow?.[field.key],
          officialText: workflowValueText(field, rec.workflow?.[field.key]),
        })
      }
    }
    if (sections.has('image') && imageFields.includes('min_pixels')) native('image', IMAGE.min_pixels, 'image', 'min_pixels')
    if (sections.has('generation')) {
      for (const field of GENERATION) {
        if (unsupported.has(field.key)) continue
        if (field.key === 'no_repeat_ngram_size' && !selected.no_repeat_ngram) continue
        native('generation', field, 'text', field.key)
      }
    }
    // The server group is the destination's: vLLM flags wherever vLLM runs, mlx-vlm
    // flags only on Apple Silicon. Without a destination both are offered.
    const mlxHere = destination === 'local' && localRuntime === 'mlx-vlm'
    if (sections.has('serving') && !mlxHere && destination !== 'llamaindex') {
      for (const field of SERVING) {
        if (field.key === 'dtype' && selected.serving_dtype === false) continue
        native('serving', field, 'server', field.key)
      }
    }
    if (sections.has('mlx') && selected.mlx_available && (mlxHere || !destination)) {
      const choices = selected.mlx_repo_choices ?? []
      if (choices.length > 0) {
        native('mlx', { key: 'repo', kind: 'enum', nullable: true, values: choices.map((c) => c.repo) }, 'server', 'mlx_repo')
      }
      for (const field of MLX_SERVER) native('mlx', field, 'server', `mlx_${field.key}`)
      for (const field of MLX_GUARD) {
        if (rec.mlx?.[field.key] === undefined) continue
        native('mlx', field, 'text', `mlx_${field.key}`)
      }
    }
    return out
    // `t`, `show` and `perTask` only read the locale and `selected`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, destination, localRuntime, locale])

  const valueOf = (p: Param) => draft[p.section]?.[p.field.key]
  const differs = (p: Param) => valueOf(p) != null && !same(valueOf(p), p.official)
  const changed = params.filter(differs)
  const tabs = (['essentials', 'image', 'text', 'pipeline', 'server'] as TabId[])
    .filter((id) => params.some((p) => p.tab === id))
  const activeTab = tabs.includes(tab) ? tab : tabs[0]
  const editable = isAdmin && !loading && !saving

  const set = (p: Param, value: SettingValue) => {
    if (!selected) return
    setEdits({ id: selected.adapter_id, values: { ...draft, [p.section]: { ...(draft[p.section] ?? {}), [p.field.key]: value } } })
    setSaved(false)
  }
  const reset = (p: Param) => set(p, p.official ?? null)

  const payload = (): Values => {
    const result: Values = {}
    for (const section of SECTIONS) {
      const values = draft[section] ?? {}
      const defaults = selected?.recommended[section] ?? {}
      const diff = Object.fromEntries(
        Object.entries(values).filter(([key, value]) => value !== null && !same(value, defaults[key])),
      ) as Record<string, SettingValue>
      if (Object.keys(diff).length) result[section] = diff
    }
    return result
  }

  const save = async (resetAll = false) => {
    if (!selected || !isAdmin) return
    setSaving(true)
    setError(null)
    try {
      const next = await apiPut<ModelSettingsItem>(`/system/model-settings/${selected.adapter_id}`, resetAll ? {} : payload())
      setItems((current) => current.map((item) => (item.adapter_id === next.adapter_id ? { ...item, ...next } : item)))
      setEdits(null)
      setSaved(!resetAll)
    } catch (e) {
      setError(e)
    } finally {
      setSaving(false)
    }
  }

  const tabLabel = (id: TabId) =>
    id === 'server' && destination
      ? t('settings.params.tabServerAt', { destination: t(`recognition.provider.${destination}`) })
      : t(`settings.params.tab.${id}`)

  return (
    <div className="space-y-3">
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
      {saved && <Notice tone="ok">{t('settings.modelSaved')}</Notice>}
      {!isAdmin && <Notice tone="warn">{t('settings.modelAdminOnly')}</Notice>}
      <Module
        tab={selected ? t('settings.params.title', { model: selected.display_name }) : t('settings.modelTuning')}
        quiet
        flush
        aux={
          changed.length > 0
            ? <Badge tone="sig">{tn('settings.params.differCount', changed.length)}</Badge>
            : selected ? <Badge tone="ok">{t('settings.params.allOfficial')}</Badge> : null
        }
      >
        {!adapterId && (
          <label className="block max-w-xl border-b border-[color:var(--color-rule)] p-3">
            <span className="lbl">{t('settings.modelChoose')}</span>
            <select className="fld mt-1 w-full" value={selectedId} disabled={loading}
              onChange={(event) => setChosenId(event.target.value)}>
              {items.map((item) => <option key={item.adapter_id} value={item.adapter_id}>{item.display_name}</option>)}
            </select>
          </label>
        )}
        {loading && <div className="p-3"><SkeletonLines lines={5} label={t('common.loading')} /></div>}
        {!loading && adapterId && !selected && <div className="p-3"><Notice tone="warn">{t('settings.params.noSettings')}</Notice></div>}
        {selected && (
          <>
            <p className="max-w-[80ch] px-3 pt-3 text-[12px] text-[color:var(--color-ink-2)]">{t('settings.params.intro')}</p>
            {selected.restart_required && <div className="px-3 pt-2"><Notice tone="warn">{t('settings.modelRestartHint')}</Notice></div>}

            <div role="tablist" aria-label={t('settings.params.tabsLabel')} className="mt-3 flex flex-wrap border-y border-[color:var(--color-rule)]">
              {tabs.map((id) => {
                const n = params.filter((p) => p.tab === id && differs(p)).length
                const on = id === activeTab
                return (
                  <button key={id} type="button" role="tab" aria-selected={on} id={`params-tab-${id}`}
                    aria-controls="params-panel" onClick={() => setTab(id)}
                    className={`flex items-center gap-1.5 px-3.5 py-2 text-[12px] ${on ? 'bg-[color:var(--color-ink)] font-semibold text-white shadow-[inset_0_-3px_0_var(--color-sig)]' : 'hover:bg-[color:var(--color-fill)]'}`}>
                    {tabLabel(id)}
                    {n > 0 && <span className="badge border-[color:var(--color-sig)] bg-[color:var(--color-sig-wash)] text-[color:var(--color-sig-text)]">{n}</span>}
                  </button>
                )
              })}
            </div>

            {/* Every difference, wherever it lives: a value changed in another tab
                must not be invisible. */}
            {changed.length > 0 && (
              <div className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-sig-wash)] px-3 py-2 text-[12px] text-[color:var(--color-sig-text)]">
                <span className="font-semibold">{t('settings.params.differ')}</span>{' '}
                {changed.map((p, index) => (
                  <span key={`${p.section}.${p.field.key}`}>
                    {index > 0 && ' · '}
                    {p.label}: {p.section === 'workflow' ? workflowValueText(p.field, valueOf(p)) : show(p.field.key, valueOf(p))}{' '}
                    <span className="text-[color:var(--color-ink-2)]">({t('settings.params.officialShort', { value: p.officialText })})</span>{' '}
                    <button type="button" className="underline" disabled={!editable} onClick={() => reset(p)}
                      aria-label={t('settings.params.resetOne', { name: p.label })}>
                      {t('settings.params.reset')}
                    </button>
                  </span>
                ))}
              </div>
            )}

            <div id="params-panel" role="tabpanel" aria-labelledby={`params-tab-${activeTab}`}>
              <TabIntro tab={activeTab} item={selected} destination={destination} localRuntime={localRuntime} />
              {params.filter((p) => p.tab === activeTab).map((p) => {
                const id = `param-${p.section}-${p.field.key}`
                const diff = differs(p)
                return (
                  <div key={id}
                    className={`grid gap-x-4 gap-y-1 border-t border-[color:var(--color-rule)] px-3 py-2.5 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_7rem] sm:items-center ${diff ? 'bg-[color:var(--color-sig-wash)]' : ''}`}>
                    <div className="min-w-0">
                      <label htmlFor={id} className="text-[13px] font-semibold">{p.label}</label>
                      {p.help && <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">{p.help}</p>}
                      <span className="mono text-[11px] text-[color:var(--color-ink-3)]">{p.section}.{p.field.key}</span>
                    </div>
                    <div>
                      <WorkflowControl field={p.field} value={valueOf(p)} id={id} label={p.label}
                        disabled={!editable} onChange={(value) => set(p, value)} />
                      <span className={`mt-0.5 block text-[11px] ${diff ? 'text-[color:var(--color-sig-text)]' : 'text-[color:var(--color-ink-3)]'}`}>
                        {t('settings.params.officialShort', { value: p.officialText })}
                      </span>
                    </div>
                    <div className="text-[11px] sm:text-right">
                      {diff ? (
                        <button type="button" className="btn btn-sm" disabled={!editable} onClick={() => reset(p)}
                          aria-label={t('settings.params.resetOne', { name: p.label })}>
                          ↺ {t('settings.params.reset')}
                        </button>
                      ) : (
                        <span className="text-[color:var(--color-ink-3)]">{t('settings.params.isOfficial')}</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            <div className="flex flex-wrap gap-2 border-t border-[color:var(--color-rule)] p-3">
              <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={!editable}>
                {saving ? t('common.saving') : t('settings.params.save')}
              </button>
              <button type="button" className="btn" onClick={() => void save(true)} disabled={!editable}>
                {t('settings.params.resetAll')}
              </button>
            </div>
          </>
        )}
      </Module>
    </div>
  )
}

/** One line under the tabs: what the tab's values do and when they apply. */
function TabIntro({ tab, item, destination, localRuntime }: {
  tab: TabId
  item: ModelSettingsItem
  destination?: string | null
  localRuntime?: string | null
}) {
  const { t } = useI18n()
  let text: ReactNode = null
  const essentialWorkflow = (item.workflow_fields ?? []).some((f) => f.essential)
  if (tab === 'text') text = t('settings.params.textIntro')
  if (item.workflow_intro && ((tab === 'pipeline' && !essentialWorkflow) || (tab === 'essentials' && essentialWorkflow))) {
    text = t(item.workflow_intro)
  }
  if (tab === 'server') {
    text = destination === 'local' && localRuntime === 'mlx-vlm'
      ? t('settings.params.serverMlxIntro')
      : item.mlx_available && destination
        ? `${t('settings.params.serverIntro')} ${t('settings.params.serverMlxElsewhere')}`
        : t('settings.params.serverIntro')
  }
  if (!text) return null
  return <p className="max-w-[80ch] px-3 py-2 text-[12px] text-[color:var(--color-ink-2)]">{text}</p>
}
