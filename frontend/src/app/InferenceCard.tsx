import { useEffect, useState } from 'react'
import { useI18n } from '../i18n'
import {
  INFERENCE_PRESETS,
  saveInferenceToBackend,
  setInference,
  syncInferenceFromBackend,
  testInferenceConnection,
  useInference,
} from './inference'
import { Badge, Field } from './ui'
import { IconCloud, IconEnv } from './icons'
import { CloudControlModal } from './CloudControlModal'
import { ModelsModal } from './ModelsModal'

export function InferenceCard() {
  const { t } = useI18n()
  const inf = useInference()
  const [url, setUrl] = useState(inf.url)
  const [model, setModel] = useState(inf.model)
  const [apiKey, setApiKey] = useState(inf.apiKey)
  const [showKey, setShowKey] = useState(false)
  const [controlOpen, setControlOpen] = useState(false)
  const [modelsOpen, setModelsOpen] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testResult, setTestResult] = useState<{
    ok: boolean
    latencyMs?: number | null
    isCloud?: boolean
    models?: string[]
    error?: string | null
  } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    void syncInferenceFromBackend()
  }, [])

  useEffect(() => {
    setUrl(inf.url)
    setModel(inf.model)
    setApiKey(inf.apiKey)
  }, [inf.url, inf.model, inf.apiKey])

  const applyPreset = (presetId: string) => {
    const preset = INFERENCE_PRESETS.find((p) => p.id === presetId)
    if (!preset) return
    setUrl(preset.url)
    setModel(preset.model)
    setTestResult(null)
    setNotice(
      t('cloud.card.presetSelected', { name: t(preset.labelKey) }),
    )
  }

  const runTest = async () => {
    setTesting(true)
    setNotice(null)
    try {
      const res = await testInferenceConnection({
        url: url.trim() || 'http://127.0.0.1:8888/v1',
        model: model.trim() || 'MonkeyOCRv2',
        apiKey: apiKey.trim(),
      })
      setTestResult({
        ok: res.ok,
        latencyMs: res.latency_ms,
        isCloud: res.is_cloud,
        models: res.models_available,
        error: res.error,
      })
      if (res.ok) {
        setNotice(
          t('cloud.card.connOk', {
            latency: String(res.latency_ms ?? '?'),
            where: res.is_cloud ? t('cloud.card.whereCloud') : t('cloud.card.whereLocal'),
          }),
        )
      } else {
        setNotice(t('cloud.card.connFail', { error: String(res.error) }))
      }
    } catch (e) {
      setTestResult({ ok: false, error: String(e) })
      setNotice(t('cloud.card.testError', { error: String(e) }))
    } finally {
      setTesting(false)
    }
  }

  const runSave = async () => {
    setSaving(true)
    setNotice(null)
    try {
      const updated = await saveInferenceToBackend({
        enabled: inf.enabled,
        url: url.trim(),
        model: model.trim() || 'MonkeyOCRv2',
        apiKey: apiKey.trim(),
      })
      setNotice(t('cloud.card.saved'))
      setInference(updated)
    } catch (e) {
      setNotice(t('cloud.card.saveError', { error: String(e) }))
    } finally {
      setSaving(false)
    }
  }

  const toggleEnabled = async () => {
    const next = !inf.enabled
    await saveInferenceToBackend({ enabled: next })
    setNotice(next ? t('cloud.card.enabledOn') : t('cloud.card.enabledOff'))
  }

  const isOnline = inf.enabled && (testResult ? testResult.ok : inf.available)
  const isCloud = testResult ? testResult.isCloud : inf.isCloud
  const latency = testResult ? testResult.latencyMs : inf.latencyMs

  return (
    <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] p-4">
      {/* Header with Title, On/Off Toggle, and Status Badge */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--color-rule)] pb-3">
        <div className="flex items-center gap-2">
          {isCloud ? <IconCloud size={16} /> : <IconEnv size={16} />}
          <h3 className="text-[14px] font-bold">{t('cloud.card.title')}</h3>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void toggleEnabled()}
            className={`btn btn-sm text-[11px] font-semibold ${
              inf.enabled ? 'text-[color:var(--color-ok)]' : ''
            }`}
            title={inf.enabled ? t('cloud.card.gpuOnTitle') : t('cloud.card.gpuOffTitle')}
          >
            {inf.enabled ? t('cloud.card.gpuOn') : t('cloud.card.gpuOff')}
          </button>

          {inf.enabled ? (
            isOnline ? (
              <Badge tone="ok">
                {isCloud ? t('cloud.card.onlineCloud') : t('cloud.card.onlineLocal')}
                {latency != null && ` (${latency} ms)`}
              </Badge>
            ) : (
              <Badge tone="neutral">
                {t('cloud.card.offline')}
              </Badge>
            )
          ) : (
            <Badge tone="neutral">
              {t('cloud.card.disabled')}
            </Badge>
          )}

          <button
            type="button"
            onClick={() => setControlOpen(true)}
            className="btn btn-sm text-[11px]"
            title={t('cloud.card.manageTitle')}
          >
            {t('cloud.card.manage')}
          </button>

          <button
            type="button"
            onClick={() => setModelsOpen(true)}
            className="btn btn-sm text-[11px]"
            title={t('cloud.card.modelsTitle')}
          >
            {t('cloud.card.models')}
          </button>
        </div>
      </div>

      {/* Notice Message */}
      {notice && (
        <div className="mt-3 rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-3 py-2 text-[12px]">
          {notice}
        </div>
      )}

      {/* Presets Bar */}
      <div className="mt-3">
        <span className="lbl !mb-1">{t('cloud.card.presetsLabel')}</span>
        <div className="flex flex-wrap gap-1.5">
          {INFERENCE_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p.id)}
              className="btn btn-sm text-[11px]"
              title={t(p.hintKey)}
            >
              {t(p.labelKey)}
            </button>
          ))}
        </div>
      </div>

      {/* Form Fields */}
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="sm:col-span-2">
          <Field
            label={t('cloud.card.endpointLabel')}
            hint={t('cloud.card.endpointHint')}
          >
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="http://127.0.0.1:8888/v1"
              className="fld fld-mono"
            />
          </Field>
        </div>

        <Field label={t('cloud.card.modelLabel')} hint={t('cloud.card.modelHint')}>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="MonkeyOCRv2"
            className="fld fld-mono"
          />
        </Field>

        <div className="sm:col-span-2">
          <Field
            label={t('cloud.card.apiKeyLabel')}
            hint={t('cloud.card.apiKeyHint')}
          >
            <div className="flex gap-1.5">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={t('cloud.card.apiKeyPlaceholder')}
                className="fld fld-mono flex-1"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="btn btn-sm px-2 text-[11px]"
              >
                {showKey ? t('cloud.card.hide') : t('cloud.card.show')}
              </button>
            </div>
          </Field>
        </div>

        <div className="flex items-end gap-2">
          <button
            type="button"
            onClick={() => void runTest()}
            disabled={testing}
            className="btn btn-sm flex-1"
          >
            {testing ? t('cloud.card.testing') : t('cloud.card.test')}
          </button>
          <button
            type="button"
            onClick={() => void runSave()}
            disabled={saving}
            className="btn btn-primary btn-sm flex-1"
          >
            {saving ? t('cloud.card.saving') : t('cloud.card.save')}
          </button>
        </div>
      </div>

      <CloudControlModal open={controlOpen} onClose={() => setControlOpen(false)} />
      <ModelsModal open={modelsOpen} onClose={() => setModelsOpen(false)} />
    </div>
  )
}
