import { useEffect, useState } from 'react'
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
import { CloudGuideModal } from './CloudGuideModal'

export function InferenceCard() {
  const inf = useInference()
  const [url, setUrl] = useState(inf.url)
  const [model, setModel] = useState(inf.model)
  const [apiKey, setApiKey] = useState(inf.apiKey)
  const [showKey, setShowKey] = useState(false)
  const [guideOpen, setGuideOpen] = useState(false)
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
    setNotice(`Preset "${preset.label}" selezionato. Ricorda di verificare la connessione prima di salvare.`)
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
          `Connessione riuscita! Latenza: ${res.latency_ms ?? '?'} ms (${res.is_cloud ? 'Cloud GPU' : 'Locale'})`,
        )
      } else {
        setNotice(`Connessione fallita: ${res.error}`)
      }
    } catch (e) {
      setTestResult({ ok: false, error: String(e) })
      setNotice(`Errore durante il test: ${e}`)
    } finally {
      setTesting(false)
    }
  }

  const runSave = async () => {
    setSaving(true)
    setNotice(null)
    try {
      const updated = await saveInferenceToBackend({
        url: url.trim(),
        model: model.trim() || 'MonkeyOCRv2',
        apiKey: apiKey.trim(),
      })
      setNotice('Configurazione inferenza salvata e applicata con successo!')
      setInference(updated)
    } catch (e) {
      setNotice(`Errore salvataggio: ${e}`)
    } finally {
      setSaving(false)
    }
  }

  const isOnline = testResult ? testResult.ok : inf.available
  const isCloud = testResult ? testResult.isCloud : inf.isCloud
  const latency = testResult ? testResult.latencyMs : inf.latencyMs

  return (
    <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] p-4">
      {/* Header with Title and Live Status Badge */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--color-rule)] pb-3">
        <div className="flex items-center gap-2">
          {isCloud ? <IconCloud size={16} /> : <IconEnv size={16} />}
          <h3 className="text-[14px] font-bold">
            Inferenza Modello & Cloud Offloading (vLLM)
          </h3>
        </div>

        <div className="flex items-center gap-2">
          {isOnline ? (
            <Badge tone="ok">
              🟢 {isCloud ? 'Cloud Remoto Connesso' : 'Locale Connesso'}
              {latency != null && ` (${latency} ms)`}
            </Badge>
          ) : (
            <Badge tone="neutral">
              ⚪ Non raggiungibile (offline)
            </Badge>
          )}

          <button
            type="button"
            onClick={() => setGuideOpen(true)}
            className="btn btn-sm text-[11px]"
          >
            📖 Guida Vast.ai / RunPod
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
        <span className="lbl !mb-1">Preset di connessione rapida:</span>
        <div className="flex flex-wrap gap-1.5">
          {INFERENCE_PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p.id)}
              className="btn btn-sm text-[11px]"
              title={p.hint}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Form Fields */}
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="sm:col-span-2">
          <Field
            label="Endpoint Server vLLM"
            hint="Es: http://127.0.0.1:8888/v1 (Tunnel SSH) oppure https://<POD>-8888.proxy.runpod.net/v1"
          >
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="http://127.0.0.1:8888/v1"
              className="fld fld-mono"
            />
          </Field>
        </div>

        <Field label="Modello Servito" hint="Default: MonkeyOCRv2">
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="MonkeyOCRv2"
            className="fld fld-mono"
          />
        </Field>

        <div className="sm:col-span-2">
          <Field
            label="API Key / Bearer Token (Opzionale)"
            hint="Necessario se il server vLLM cloud è avviato con flag --api-key"
          >
            <div className="flex gap-1.5">
              <input
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="Lascia vuoto se non richiesta autenticazione"
                className="fld fld-mono flex-1"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="btn btn-sm px-2 text-[11px]"
              >
                {showKey ? 'Nascondi' : 'Mostra'}
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
            {testing ? 'Test in corso…' : '⚡ Test Connessione'}
          </button>
          <button
            type="button"
            onClick={() => void runSave()}
            disabled={saving}
            className="btn btn-primary btn-sm flex-1"
          >
            {saving ? 'Salvataggio…' : '💾 Salva'}
          </button>
        </div>
      </div>

      <CloudGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />
    </div>
  )
}
