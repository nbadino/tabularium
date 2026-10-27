/** Configurazione dedicata di OCRia-1 tramite l'API nativa LlamaCloud. */
import { useEffect, useMemo, useState } from 'react'
import { apiGet, apiPut } from '../../lib/api'
import { useI18n } from '../../i18n'
import { Badge, ErrorNotice, Field, Module, Notice } from '../ui'
import {
  saveInferenceToBackend,
  syncInferenceFromBackend,
  testInferenceConnection,
  useInference,
} from '../inference'
import type { ModelItem } from './registry'

type WorkflowValue = string | number | boolean | string[] | Record<string, number>
type Workflow = Record<string, WorkflowValue>

interface ModelSettingsItem {
  adapter_id: string
  display_name: string
  recommended: { workflow?: Workflow }
  overrides: { workflow?: Workflow }
  effective: { workflow?: Workflow }
  restart_required: boolean
}

interface LlamaIndexSetupProps {
  model: ModelItem
  onClose: () => void
}

const ADAPTER_ID = 'llamaindex-ocria-1'
const DEFAULT_URL = 'https://api.cloud.llamaindex.ai'
const MODEL = 'ocria-1'

const LANGS = [
  'abq', 'ach', 'afr', 'aka', 'amh', 'ara', 'asm', 'ava', 'aym', 'aze', 'bak', 'bam',
  'bel', 'bem', 'ben', 'bod', 'bos', 'bre', 'bul', 'cat', 'ceu', 'ces', 'cha', 'che',
  'chi_sim', 'chi_tra', 'chr', 'cos', 'cym', 'dan', 'deu', 'div', 'dzo', 'ell', 'eng',
  'enm', 'epo', 'est', 'eus', 'fao', 'fas', 'fij', 'fin', 'fra', 'frk', 'frm', 'fry',
  'gla', 'gle', 'glg', 'grc', 'grn', 'guj', 'hat', 'hau', 'heb', 'hin', 'hrv', 'hun',
  'hye', 'ibo', 'ido', 'iku', 'ile', 'ina', 'ind', 'isl', 'ita', 'jav', 'jpn', 'kan',
  'kat', 'kaz', 'khm', 'kir', 'kmr', 'kor', 'lao', 'lat', 'lav', 'lit', 'ltz', 'mal',
  'mar', 'mkd', 'mlt', 'mon', 'mri', 'msa', 'mya', 'nep', 'nld', 'nor', 'oci', 'ori',
  'orm', 'pan', 'pol', 'por', 'pus', 'que', 'ron', 'rus', 'san', 'sin', 'slk', 'slv',
  'snd', 'spa', 'sqi', 'srp', 'sun', 'swa', 'swe', 'syr', 'tam', 'tat', 'tel', 'tgk',
  'tha', 'tir', 'ton', 'tur', 'uig', 'ukr', 'urd', 'uzb', 'vie', 'yid', 'yor',
] as const

const LANGUAGE_LABELS: Record<string, string> = {
  ara: 'Arabic', chi_sim: 'Chinese simplified', chi_tra: 'Chinese traditional',
  deu: 'German', ell: 'Greek', eng: 'English', fas: 'Persian', fra: 'French',
  grc: 'Ancient Greek', heb: 'Hebrew', hin: 'Hindi', ita: 'Italian', jpn: 'Japanese',
  kor: 'Korean', lat: 'Latin', nld: 'Dutch', pol: 'Polish', por: 'Portuguese',
  ron: 'Romanian', rus: 'Russian', spa: 'Spanish', swe: 'Swedish', tur: 'Turkish',
}

function str(value: WorkflowValue | undefined, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function bool(value: WorkflowValue | undefined, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function num(value: WorkflowValue | undefined, fallback = 0): number {
  return typeof value === 'number' ? value : fallback
}

function ToggleRow({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string
  hint: string
  checked: boolean
  disabled?: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <label className="flex items-start justify-between gap-4 border-t border-[color:var(--color-rule)] py-3 first:border-t-0 first:pt-0">
      <span className="min-w-0">
        <span className="block text-[13px] font-semibold">{label}</span>
        <span className="mt-0.5 block text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">{hint}</span>
      </span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0"
      />
    </label>
  )
}

function SectionNav({ label, items }: { label: string; items: { id: string; label: string }[] }) {
  return (
    <nav aria-label={label} className="flex flex-wrap gap-x-4 gap-y-1 border-y border-[color:var(--color-rule)] py-2">
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className="text-[12px] font-semibold text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]"
          onClick={() => document.getElementById(item.id)?.scrollIntoView({ block: 'start' })}
        >
          {item.label}
        </button>
      ))}
    </nav>
  )
}

export function LlamaIndexSetup({ model, onClose }: LlamaIndexSetupProps) {
  const { t } = useI18n()
  const inference = useInference()
  const hasStoredLlamaKey = (
    inference.provider === 'llamaindex'
    && inference.adapterId === ADAPTER_ID
    && inference.hasApiKey
  )
  const [settings, setSettings] = useState<ModelSettingsItem | null>(null)
  const [workflow, setWorkflow] = useState<Workflow>({})
  const [baseUrl, setBaseUrl] = useState(
    inference.provider === 'llamaindex' && inference.adapterId === ADAPTER_ID
      ? inference.url || DEFAULT_URL
      : DEFAULT_URL,
  )
  const [apiKey, setApiKey] = useState('')
  const [revealKey, setRevealKey] = useState(false)
  const [useSavedKey, setUseSavedKey] = useState(hasStoredLlamaKey)
  const [languageToAdd, setLanguageToAdd] = useState('eng')
  const [versions, setVersions] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [activating, setActivating] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const next = await apiGet<ModelSettingsItem>(`/system/model-settings/${ADAPTER_ID}`)
      setSettings(next)
      setWorkflow(next.effective.workflow ?? {})
      const currentVersion = str(next.effective.workflow?.version, 'latest')
      if (currentVersion !== 'latest') setVersions([currentVersion])
    } catch (cause) {
      setError(cause)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  const tier = str(workflow.tier, 'agentic')
  const effort = str(workflow.confidence_score_effort, 'standard')
  const languages = Array.isArray(workflow.languages) ? workflow.languages : ['eng']
  const cropBox = (workflow.crop_box && !Array.isArray(workflow.crop_box) && typeof workflow.crop_box === 'object')
    ? workflow.crop_box as Record<string, number>
    : { top: 0, bottom: 0, left: 0, right: 0 }
  const customPrompt = str(workflow.custom_prompt)

  const versionChoices = useMemo(
    () => Array.from(new Set(['latest', ...versions, str(workflow.version, 'latest')])),
    [versions, workflow.version],
  )

  const update = (key: string, value: WorkflowValue) => {
    setWorkflow((current) => ({ ...current, [key]: value }))
    setNotice(null)
  }

  const payloadWorkflow = (): Workflow => ({
    ...workflow,
    languages,
    crop_box: cropBox,
    custom_prompt: customPrompt.trim(),
  })

  const persistSettings = async () => {
    const next = await apiPut<ModelSettingsItem>(`/system/model-settings/${ADAPTER_ID}`, {
      workflow: payloadWorkflow(),
    })
    setSettings(next)
    setWorkflow(next.effective.workflow ?? {})
    return next
  }

  const saveOnly = async (reset = false) => {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      if (reset) {
        const next = await apiPut<ModelSettingsItem>(`/system/model-settings/${ADAPTER_ID}`, {})
        setSettings(next)
        setWorkflow(next.effective.workflow ?? {})
        setNotice(t('modelsHub.llamaIndex.resetDone'))
      } else {
        await persistSettings()
        setNotice(t('modelsHub.llamaIndex.settingsSaved'))
      }
    } catch (cause) {
      setError(cause)
    } finally {
      setSaving(false)
    }
  }

  const testConnection = async () => {
    setTesting(true)
    setError(null)
    setNotice(null)
    try {
      const usingSaved = useSavedKey && hasStoredLlamaKey && !apiKey.trim()
      const result = await testInferenceConnection({
        url: baseUrl,
        model: MODEL,
        apiKey: usingSaved ? '' : apiKey,
        adapterId: ADAPTER_ID,
        provider: 'llamaindex',
        useSavedCredentials: usingSaved,
        timeout: 15,
      })
      if (!result.ok) throw new Error(result.error || t('modelsHub.llamaIndex.testFailed'))
      const nativeVersions = result.models_available.filter((value) => value !== MODEL)
      setVersions(nativeVersions)
      setNotice(t('modelsHub.llamaIndex.testOk', { latency: String(result.latency_ms ?? '—') }))
    } catch (cause) {
      setError(cause)
    } finally {
      setTesting(false)
    }
  }

  const activate = async () => {
    if (!apiKey.trim() && !(useSavedKey && hasStoredLlamaKey)) {
      setError(new Error(t('modelsHub.llamaIndex.apiKeyRequired')))
      return
    }
    setActivating(true)
    setError(null)
    setNotice(null)
    try {
      await persistSettings()
      await saveInferenceToBackend({
        enabled: true,
        url: baseUrl.trim() || DEFAULT_URL,
        model: MODEL,
        adapterId: ADAPTER_ID,
        provider: 'llamaindex',
        apiKey: apiKey.trim() ? apiKey : undefined,
        timeout: 120,
      })
      await syncInferenceFromBackend()
      onClose()
    } catch (cause) {
      setError(cause)
    } finally {
      setActivating(false)
    }
  }

  const addLanguage = () => {
    if (languages.includes(languageToAdd) || languages.length >= 10) return
    update('languages', [...languages, languageToAdd])
  }

  const moveLanguage = (index: number, delta: number) => {
    const next = [...languages]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    update('languages', next)
  }

  const toggle = (key: string, label: string, hint: string, disabled = false) => (
    <ToggleRow
      label={label}
      hint={hint}
      checked={bool(workflow[key])}
      disabled={disabled || loading || saving || activating}
      onChange={(checked) => update(key, checked)}
    />
  )

  const sectionItems = [
    { id: 'ocria-connection', label: t('modelsHub.llamaIndex.navConnection') },
    { id: 'ocria-routing', label: t('modelsHub.llamaIndex.navRouting') },
    { id: 'ocria-quality', label: t('modelsHub.llamaIndex.navQuality') },
    { id: 'ocria-input', label: t('modelsHub.llamaIndex.navInput') },
    { id: 'ocria-output', label: t('modelsHub.llamaIndex.navOutput') },
    { id: 'ocria-agentic', label: t('modelsHub.llamaIndex.navAgentic') },
    { id: 'ocria-controls', label: t('modelsHub.llamaIndex.navControls') },
  ]

  return (
    <div className="space-y-3">
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
      {notice && <Notice tone="ok">{notice}</Notice>}

      <Module
        tab={t('modelsHub.llamaIndex.panelTitle')}
        aux={<Badge tone={inference.provider === 'llamaindex' && inference.adapterId === ADAPTER_ID ? 'ok' : 'progress'}>
          {inference.provider === 'llamaindex' && inference.adapterId === ADAPTER_ID
            ? t('modelsHub.llamaIndex.inUse')
            : t('modelsHub.llamaIndex.notConfigured')}
        </Badge>}
      >
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(17rem,0.8fr)]">
          <div>
            <h3 className="max-w-[24ch] text-[22px] font-bold leading-tight tracking-[-0.025em]">
              {t('modelsHub.llamaIndex.question')}
            </h3>
            <p className="mt-2 max-w-[68ch] text-[13px] leading-relaxed text-[color:var(--color-ink-2)]">
              {t('modelsHub.llamaIndex.intro', { model: model.display_name })}
            </p>
          </div>
          <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-3">
            <span className="lbl">{t('modelsHub.llamaIndex.findingLabel')}</span>
            <p className="mt-1 text-[13px] font-semibold leading-relaxed">{t('modelsHub.llamaIndex.finding')}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Badge tone="sig">{tier === 'agentic' ? t('modelsHub.llamaIndex.agentic') : t('modelsHub.llamaIndex.costEffective')}</Badge>
              <Badge tone="neutral">{effort === 'high' ? t('modelsHub.llamaIndex.effortHigh') : t('modelsHub.llamaIndex.effortStandard')}</Badge>
              <Badge tone="neutral">{languages.join(' · ')}</Badge>
            </div>
          </div>
        </div>
        <SectionNav label={t('modelsHub.llamaIndex.navLabel')} items={sectionItems} />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button type="button" className="btn btn-primary" disabled={loading || saving || activating} onClick={() => void activate()}>
            {activating ? t('modelsHub.llamaIndex.activating') : t('modelsHub.llamaIndex.saveAndUse')}
          </button>
          <button type="button" className="btn" disabled={loading || saving || activating} onClick={() => void saveOnly()}>
            {saving ? t('modelsHub.llamaIndex.saving') : t('modelsHub.llamaIndex.saveSettings')}
          </button>
          <button type="button" className="btn" disabled={loading || saving || activating} onClick={() => void saveOnly(true)}>
            {t('modelsHub.llamaIndex.reset')}
          </button>
        </div>
      </Module>

      <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.25fr)_minmax(20rem,0.75fr)]">
        <div className="space-y-3">
          <section id="ocria-connection" className="scroll-mt-3">
            <Module
              tab={t('modelsHub.llamaIndex.connectionTitle')}
              aux={<Badge tone={hasStoredLlamaKey ? 'ok' : 'warn'}>
                {hasStoredLlamaKey ? t('modelsHub.llamaIndex.keyStored') : t('modelsHub.llamaIndex.keyMissing')}
              </Badge>}
            >
              <p className="max-w-[75ch] text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">
                {t('modelsHub.llamaIndex.connectionIntro')}
              </p>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label={t('modelsHub.llamaIndex.baseUrl')} hint={t('modelsHub.llamaIndex.baseUrlHint')}>
                  <input className="fld mt-1 w-full" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} disabled={loading || activating} />
                </Field>
                <div className="block min-w-0">
                  <label htmlFor="ocria-api-key" className="lbl">{t('modelsHub.llamaIndex.apiKey')}</label>
                  <div className="mt-1 flex">
                    <input
                      id="ocria-api-key"
                      className="fld w-full !rounded-r-none"
                      type={revealKey ? 'text' : 'password'}
                      autoComplete="off"
                      value={apiKey}
                      placeholder={hasStoredLlamaKey ? '••••••••••••' : 'llx-…'}
                      onChange={(event) => { setApiKey(event.target.value); setUseSavedKey(false) }}
                      disabled={loading || activating}
                    />
                    <button type="button" className="btn !rounded-l-none" onClick={() => setRevealKey((value) => !value)}>
                      {revealKey ? t('modelsHub.llamaIndex.hideKey') : t('modelsHub.llamaIndex.showKey')}
                    </button>
                  </div>
                  <span className="mt-1 block text-[12px] text-[color:var(--color-ink-3)]">
                    {t('modelsHub.llamaIndex.apiKeyHint')}
                  </span>
                </div>
              </div>
              {hasStoredLlamaKey && (
                <label className="mt-3 flex items-start gap-2 border-t border-[color:var(--color-rule)] pt-3 text-[12px]">
                  <input type="checkbox" className="mt-0.5" checked={useSavedKey} onChange={(event) => setUseSavedKey(event.target.checked)} />
                  <span>
                    <strong className="block font-semibold">{t('modelsHub.llamaIndex.useSavedKey')}</strong>
                    <span className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.useSavedKeyHint')}</span>
                  </span>
                </label>
              )}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button type="button" className="btn" disabled={testing || loading || activating || (!apiKey.trim() && !(useSavedKey && hasStoredLlamaKey))} onClick={() => void testConnection()}>
                  {testing ? t('modelsHub.llamaIndex.testing') : t('modelsHub.llamaIndex.testConnection')}
                </button>
                <span className="text-[12px] text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.testHint')}</span>
              </div>
            </Module>
          </section>

          <section id="ocria-routing" className="scroll-mt-3">
            <Module tab={t('modelsHub.llamaIndex.routingTitle')}>
              <p className="max-w-[75ch] text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">
                {t('modelsHub.llamaIndex.routingIntro')}
              </p>
              <div role="radiogroup" aria-label={t('modelsHub.llamaIndex.tier')} className="mt-3 grid gap-2 md:grid-cols-2">
                {(['cost_effective', 'agentic'] as const).map((value) => {
                  const selected = tier === value
                  return (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      onClick={() => {
                        update('tier', value)
                        if (value === 'cost_effective') update('cost_optimizer', false)
                      }}
                      className={`border p-3 text-left ${selected ? 'border-[color:var(--color-sig)] bg-[color:var(--color-sig-wash)]' : 'border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]'}`}
                    >
                      <span className="flex flex-wrap items-center gap-2">
                        <strong className="text-[14px]">{value === 'agentic' ? t('modelsHub.llamaIndex.agentic') : t('modelsHub.llamaIndex.costEffective')}</strong>
                        {value === 'agentic' && <Badge tone="ok">{t('modelsHub.llamaIndex.recommended')}</Badge>}
                      </span>
                      <span className="mt-1 block text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">
                        {value === 'agentic' ? t('modelsHub.llamaIndex.agenticHint') : t('modelsHub.llamaIndex.costEffectiveHint')}
                      </span>
                    </button>
                  )
                })}
              </div>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label={t('modelsHub.llamaIndex.version')} hint={t('modelsHub.llamaIndex.versionHint')}>
                  <input className="fld mt-1 w-full" list="ocria-versions" value={str(workflow.version, 'latest')} onChange={(event) => update('version', event.target.value)} />
                  <datalist id="ocria-versions">
                    {versionChoices.map((value) => <option key={value} value={value}>{value === 'latest' ? t('modelsHub.llamaIndex.latest') : value}</option>)}
                  </datalist>
                </Field>
                <div>
                  <span className="lbl">{t('modelsHub.llamaIndex.effort')}</span>
                  <div role="radiogroup" aria-label={t('modelsHub.llamaIndex.effort')} className="mt-1 grid grid-cols-2">
                    {(['standard', 'high'] as const).map((value) => (
                      <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={effort === value}
                        className={`btn ${effort === value ? '!bg-[color:var(--color-ink)] !text-white' : ''} ${value === 'high' ? '-ml-px' : ''}`}
                        onClick={() => update('confidence_score_effort', value)}
                      >
                        {value === 'high' ? t('modelsHub.llamaIndex.effortHigh') : t('modelsHub.llamaIndex.effortStandard')}
                      </button>
                    ))}
                  </div>
                  <span className="mt-1 block text-[12px] text-[color:var(--color-ink-3)]">
                    {effort === 'high' ? t('modelsHub.llamaIndex.effortHighHint') : t('modelsHub.llamaIndex.effortStandardHint')}
                  </span>
                </div>
              </div>
              <Notice tone="neutral">{t('modelsHub.llamaIndex.tierScope')}</Notice>
            </Module>
          </section>

          <section id="ocria-quality" className="scroll-mt-3">
            <Module tab={t('modelsHub.llamaIndex.qualityTitle')}>
              <div className="grid gap-x-6 md:grid-cols-2">
                <div>
                  {toggle('aggressive_table_extraction', t('modelsHub.llamaIndex.aggressiveTable'), t('modelsHub.llamaIndex.aggressiveTableHint'))}
                  {toggle('disable_heuristics', t('modelsHub.llamaIndex.disableHeuristics'), t('modelsHub.llamaIndex.disableHeuristicsHint'))}
                  {toggle('cost_optimizer', t('modelsHub.llamaIndex.costOptimizer'), t('modelsHub.llamaIndex.costOptimizerHint'), tier !== 'agentic')}
                </div>
                <div>
                  {toggle('ignore_diagonal_text', t('modelsHub.llamaIndex.ignoreDiagonal'), t('modelsHub.llamaIndex.ignoreDiagonalHint'))}
                  {toggle('ignore_hidden_text', t('modelsHub.llamaIndex.ignoreHidden'), t('modelsHub.llamaIndex.ignoreHiddenHint'))}
                  {toggle('ignore_text_in_image', t('modelsHub.llamaIndex.ignoreTextInImage'), t('modelsHub.llamaIndex.ignoreTextInImageHint'))}
                </div>
              </div>
              <div className="mt-3 grid gap-3 border-t border-[color:var(--color-rule)] pt-3 md:grid-cols-2">
                <Field label={t('modelsHub.llamaIndex.chartParsing')} hint={t('modelsHub.llamaIndex.chartParsingHint')}>
                  <select className="fld mt-1 w-full" value={str(workflow.specialized_chart_parsing, 'off')} onChange={(event) => update('specialized_chart_parsing', event.target.value)}>
                    {(['off', 'efficient', 'agentic', 'agentic_plus'] as const).map((value) => (
                      <option key={value} value={value}>{t(`modelsHub.llamaIndex.chart.${value}`)}</option>
                    ))}
                  </select>
                </Field>
              </div>
            </Module>
          </section>

          <section id="ocria-input" className="scroll-mt-3">
            <Module tab={t('modelsHub.llamaIndex.inputTitle')}>
              <div>
                <span className="lbl">{t('modelsHub.llamaIndex.languages')}</span>
                <p className="mt-1 text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.languagesHint')}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {languages.map((code, index) => (
                    <span key={code} className="inline-flex items-center border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] text-[12px]">
                      <span className="px-2 py-1 font-semibold" title={LANGUAGE_LABELS[code] || code}>{code}</span>
                      <button type="button" className="border-l border-[color:var(--color-rule)] px-1.5 py-1 disabled:opacity-40" aria-label={t('modelsHub.llamaIndex.moveUp', { code })} disabled={index === 0} onClick={() => moveLanguage(index, -1)}>↑</button>
                      <button type="button" className="border-l border-[color:var(--color-rule)] px-1.5 py-1 disabled:opacity-40" aria-label={t('modelsHub.llamaIndex.moveDown', { code })} disabled={index === languages.length - 1} onClick={() => moveLanguage(index, 1)}>↓</button>
                      <button type="button" className="border-l border-[color:var(--color-rule)] px-1.5 py-1 text-[color:var(--color-sig-text)] disabled:opacity-40" aria-label={t('modelsHub.llamaIndex.removeLanguage', { code })} disabled={languages.length === 1} onClick={() => update('languages', languages.filter((item) => item !== code))}>×</button>
                    </span>
                  ))}
                </div>
                <div className="mt-2 flex max-w-md">
                  <select className="fld w-full !rounded-r-none" value={languageToAdd} onChange={(event) => setLanguageToAdd(event.target.value)}>
                    {LANGS.map((code) => <option key={code} value={code}>{code}{LANGUAGE_LABELS[code] ? ` — ${LANGUAGE_LABELS[code]}` : ''}</option>)}
                  </select>
                  <button type="button" className="btn !rounded-l-none" disabled={languages.includes(languageToAdd) || languages.length >= 10} onClick={addLanguage}>
                    {t('modelsHub.llamaIndex.addLanguage')}
                  </button>
                </div>
              </div>

              <div className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
                {toggle('camera_photo_correction', t('modelsHub.llamaIndex.cameraCorrection'), t('modelsHub.llamaIndex.cameraCorrectionHint'))}
                {toggle('crop_enabled', t('modelsHub.llamaIndex.crop'), t('modelsHub.llamaIndex.cropHint'))}
                {bool(workflow.crop_enabled) && (
                  <div className="mt-2 grid grid-cols-2 gap-2 border-t border-[color:var(--color-rule)] pt-3 sm:grid-cols-4">
                    {(['top', 'bottom', 'left', 'right'] as const).map((edge) => (
                      <Field key={edge} label={t(`modelsHub.llamaIndex.cropEdge.${edge}`)}>
                        <div className="mt-1 flex items-center">
                          <input
                            type="number"
                            min={0}
                            max={99}
                            step={1}
                            className="fld w-full !rounded-r-none"
                            value={Math.round(num(cropBox[edge]) * 100)}
                            onChange={(event) => update('crop_box', { ...cropBox, [edge]: Math.max(0, Math.min(99, Number(event.target.value) || 0)) / 100 })}
                          />
                          <span className="border border-l-0 border-[color:var(--color-control)] px-2 py-1.5 text-[12px] text-[color:var(--color-ink-2)]">%</span>
                        </div>
                      </Field>
                    ))}
                  </div>
                )}
              </div>
            </Module>
          </section>

          <section id="ocria-output" className="scroll-mt-3">
            <Module tab={t('modelsHub.llamaIndex.outputTitle')}>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label={t('modelsHub.llamaIndex.watermark')} hint={t('modelsHub.llamaIndex.watermarkHint')}>
                  <select className="fld mt-1 w-full" value={str(workflow.watermark_handling, 'move_to_end')} onChange={(event) => update('watermark_handling', event.target.value)}>
                    {(['nothing', 'move_to_end', 'return_image'] as const).map((value) => (
                      <option key={value} value={value}>{t(`modelsHub.llamaIndex.watermarkMode.${value}`)}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <div className="mt-2 grid gap-x-6 md:grid-cols-2">
                <div>
                  {toggle('extract_printed_page_number', t('modelsHub.llamaIndex.printedPageNumber'), t('modelsHub.llamaIndex.printedPageNumberHint'))}
                  {toggle('markdown_annotate_links', t('modelsHub.llamaIndex.annotateLinks'), t('modelsHub.llamaIndex.annotateLinksHint'))}
                </div>
                <div>
                  {toggle('markdown_annotate_revisions', t('modelsHub.llamaIndex.annotateRevisions'), t('modelsHub.llamaIndex.annotateRevisionsHint'))}
                  {toggle('markdown_annotate_line_numbers', t('modelsHub.llamaIndex.annotateLineNumbers'), t('modelsHub.llamaIndex.annotateLineNumbersHint'))}
                </div>
              </div>
              <div className="border-t border-[color:var(--color-rule)]">
                {toggle('markdown_compact_tables', t('modelsHub.llamaIndex.compactTables'), t('modelsHub.llamaIndex.compactTablesHint'))}
              </div>
              <Notice tone="neutral">{t('modelsHub.llamaIndex.htmlTablesFixed')}</Notice>
            </Module>
          </section>

          <section id="ocria-agentic" className="scroll-mt-3">
            <Module tab={t('modelsHub.llamaIndex.agenticTitle')}>
              <Field label={t('modelsHub.llamaIndex.customPrompt')} hint={t('modelsHub.llamaIndex.customPromptHint')}>
                <textarea
                  className="fld mt-1 min-h-28 w-full"
                  maxLength={4000}
                  value={customPrompt}
                  onChange={(event) => update('custom_prompt', event.target.value)}
                  placeholder={t('modelsHub.llamaIndex.customPromptPlaceholder')}
                />
              </Field>
              {customPrompt.trim() && (
                <Notice tone="warn">{t('modelsHub.llamaIndex.customPromptWarning')}</Notice>
              )}
            </Module>
          </section>

          <section id="ocria-controls" className="scroll-mt-3">
            <Module tab={t('modelsHub.llamaIndex.controlsTitle')}>
              <div className="grid gap-3 md:grid-cols-3">
                <Field label={t('modelsHub.llamaIndex.jobTimeout')} hint={t('modelsHub.llamaIndex.jobTimeoutHint')}>
                  <input type="number" min={30} max={86400} className="fld mt-1 w-full" value={num(workflow.job_timeout_in_seconds, 300)} onChange={(event) => update('job_timeout_in_seconds', Number(event.target.value))} />
                </Field>
                <Field label={t('modelsHub.llamaIndex.extraPageTimeout')} hint={t('modelsHub.llamaIndex.extraPageTimeoutHint')}>
                  <input type="number" min={0} max={3600} className="fld mt-1 w-full" value={num(workflow.job_timeout_extra_time_per_page_in_seconds, 30)} onChange={(event) => update('job_timeout_extra_time_per_page_in_seconds', Number(event.target.value))} />
                </Field>
                <Field label={t('modelsHub.llamaIndex.failureRatio')} hint={t('modelsHub.llamaIndex.failureRatioHint')}>
                  <input type="number" min={0} max={1} step={0.01} className="fld mt-1 w-full" value={num(workflow.allowed_failure_ratio, 0)} onChange={(event) => update('allowed_failure_ratio', Number(event.target.value))} />
                </Field>
              </div>
              <div className="mt-3 grid gap-x-6 md:grid-cols-2">
                <div>
                  {toggle('fail_on_job_timeout', t('modelsHub.llamaIndex.failJobTimeout'), t('modelsHub.llamaIndex.failJobTimeoutHint'))}
                  {toggle('fail_on_page_timeout', t('modelsHub.llamaIndex.failPageTimeout'), t('modelsHub.llamaIndex.failPageTimeoutHint'))}
                </div>
                <div>{toggle('disable_cache', t('modelsHub.llamaIndex.disableCache'), t('modelsHub.llamaIndex.disableCacheHint'))}</div>
              </div>
            </Module>
          </section>
        </div>

        <aside className="space-y-3 xl:sticky xl:top-3">
          <Module tab={t('modelsHub.llamaIndex.summaryTitle')} quiet>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-[12px]">
              <dt className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.tier')}</dt>
              <dd className="text-right font-semibold">{tier === 'agentic' ? t('modelsHub.llamaIndex.agentic') : t('modelsHub.llamaIndex.costEffective')}</dd>
              <dt className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.effort')}</dt>
              <dd className="text-right font-semibold">{effort === 'high' ? t('modelsHub.llamaIndex.effortHigh') : t('modelsHub.llamaIndex.effortStandard')}</dd>
              <dt className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.version')}</dt>
              <dd className="text-right font-semibold">{str(workflow.version, 'latest')}</dd>
              <dt className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.languages')}</dt>
              <dd className="text-right font-semibold">{languages.join(', ')}</dd>
              <dt className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.tableMode')}</dt>
              <dd className="text-right font-semibold">{bool(workflow.aggressive_table_extraction) ? t('modelsHub.llamaIndex.aggressiveShort') : t('modelsHub.llamaIndex.balancedShort')}</dd>
              <dt className="text-[color:var(--color-ink-2)]">{t('modelsHub.llamaIndex.promptMode')}</dt>
              <dd className="text-right font-semibold">{customPrompt.trim() ? t('modelsHub.llamaIndex.customPromptOn') : t('modelsHub.llamaIndex.customPromptOff')}</dd>
            </dl>
            <div className="mt-3 flex flex-col gap-2">
              <button type="button" className="btn btn-primary justify-center" disabled={loading || saving || activating} onClick={() => void activate()}>
                {activating ? t('modelsHub.llamaIndex.activating') : t('modelsHub.llamaIndex.saveAndUse')}
              </button>
              <button type="button" className="btn justify-center" disabled={loading || saving || activating} onClick={() => void saveOnly()}>
                {saving ? t('modelsHub.llamaIndex.saving') : t('modelsHub.llamaIndex.saveSettings')}
              </button>
            </div>
          </Module>

          <Module tab={t('modelsHub.llamaIndex.exclusionsTitle')} quiet>
            <p className="text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">
              {t('modelsHub.llamaIndex.exclusionsBody')}
            </p>
          </Module>

          {settings?.restart_required && <Notice tone="warn">{t('modelsHub.llamaIndex.restartHint')}</Notice>}
          {loading && <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('common.loading')}</p>}
        </aside>
      </div>
    </div>
  )
}
