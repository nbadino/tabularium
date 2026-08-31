import { useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { Badge, Modal } from './ui'
import { IconWarn } from './icons'

interface ModelsModalProps {
  open: boolean
  onClose: () => void
}

interface ModelItem {
  adapter_id: string
  display_name: string
  tasks: string[]
  table_format: string | null
  hf_repo: string
  approx_size_gb: number | null
  license_note: string
  train_toolchain: string
  serve_backend: string
  supports_native: boolean
  supports_two_stage: boolean
  supports_end2end: boolean
  export_ready: boolean
  local_serve_ready: boolean
  cloud_serve_ready: boolean
  cloud_template: string | null
  download_only: boolean
  installed: boolean
  downloading: boolean
  path: string | null
  size_bytes: number
  state: string
  error: string | null
  vram_warning: string | null
}

const CUSTOM_ID_PREFIX = 'custom-'

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

interface ServeStatus {
  running: boolean
  adapter_id: string | null
  port: number | null
  pid: number | null
  error: string | null
}

function fmtBytes(n: number): string {
  if (n <= 0) return '0 B'
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(0)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(1)} GB`
}

/**
 * Registro modelli OCR: sfoglia, scarica, cancella. Componente separato da
 * `CloudControlModal.tsx` (istanze/tunnel) per non far dipendere download dei
 * pesi dalla gestione della connessione cloud.
 */
export function ModelsModal({ open, onClose }: ModelsModalProps) {
  const { t } = useI18n()
  const [models, setModels] = useState<ModelItem[]>([])
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const confirmDeleteTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [showAddCustom, setShowAddCustom] = useState(false)
  const [customForm, setCustomForm] = useState<CustomModelForm>(EMPTY_CUSTOM_FORM)
  const [customBusy, setCustomBusy] = useState(false)
  const [serveStatus, setServeStatus] = useState<ServeStatus>({
    running: false,
    adapter_id: null,
    port: null,
    pid: null,
    error: null,
  })

  const load = async () => {
    try {
      const res = await apiGet<{ items: ModelItem[] }>('/models')
      setModels(res.items)
    } catch (e) {
      setNotice(t('cloud.models.loadError', { error: String(e) }))
    }
  }

  const loadServeStatus = async () => {
    try {
      setServeStatus(await apiGet<ServeStatus>('/models/serve/status'))
    } catch {
      /* ignore: la card mostra comunque lo stato precedente */
    }
  }

  useEffect(() => {
    if (!open) return
    void load()
    void loadServeStatus()
  }, [open])

  useEffect(() => {
    if (!open) return
    const id = setInterval(() => void loadServeStatus(), 3000)
    return () => clearInterval(id)
  }, [open])

  useEffect(() => {
    if (!open || !models.some((m) => m.downloading)) return
    const id = setInterval(() => void load(), 2000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, models.some((m) => m.downloading)])

  const toolchainLabel = (id: string): string => {
    switch (id) {
      case 'ms-swift':
        return t('cloud.models.toolchainMsSwift')
      case 'llama-factory':
        return t('cloud.models.toolchainLlamaFactory')
      case 'unsloth':
        return t('cloud.models.toolchainUnsloth')
      case 'community':
        return t('cloud.models.toolchainCommunity')
      default:
        return t('cloud.models.toolchainNone')
    }
  }

  const handleDownload = async (adapterId: string) => {
    setBusy((b) => ({ ...b, [adapterId]: true }))
    setNotice(null)
    try {
      await apiPost(`/models/${adapterId}/download`, {})
      await load()
    } catch (e) {
      setNotice(t('cloud.models.downloadError', { error: String(e) }))
    } finally {
      setBusy((b) => ({ ...b, [adapterId]: false }))
    }
  }

  const handleCancel = async (adapterId: string) => {
    setBusy((b) => ({ ...b, [adapterId]: true }))
    try {
      await apiPost(`/models/${adapterId}/download/cancel`, {})
      await load()
    } catch (e) {
      setNotice(t('cloud.models.cancelError', { error: String(e) }))
    } finally {
      setBusy((b) => ({ ...b, [adapterId]: false }))
    }
  }

  const handleDelete = async (adapterId: string) => {
    if (confirmDelete !== adapterId) {
      setConfirmDelete(adapterId)
      if (confirmDeleteTimer.current) clearTimeout(confirmDeleteTimer.current)
      confirmDeleteTimer.current = setTimeout(() => setConfirmDelete(null), 5000)
      return
    }
    setConfirmDelete(null)
    setBusy((b) => ({ ...b, [adapterId]: true }))
    try {
      await apiDelete(`/models/${adapterId}`)
      setNotice(t('cloud.models.deletedNotice'))
      await load()
    } catch (e) {
      setNotice(t('cloud.models.deleteError', { error: String(e) }))
    } finally {
      setBusy((b) => ({ ...b, [adapterId]: false }))
    }
  }

  const handleServeStart = async (adapterId: string) => {
    setBusy((b) => ({ ...b, [adapterId]: true }))
    setNotice(null)
    try {
      await apiPost(`/models/${adapterId}/serve/start`, { port: 8888 })
      setNotice(t('cloud.models.serveStarted'))
      await loadServeStatus()
    } catch (e) {
      setNotice(t('cloud.models.serveError', { error: String(e) }))
    } finally {
      setBusy((b) => ({ ...b, [adapterId]: false }))
    }
  }

  const handleServeStop = async (adapterId: string) => {
    setBusy((b) => ({ ...b, [adapterId]: true }))
    try {
      await apiPost('/models/serve/stop', {})
      await loadServeStatus()
    } catch (e) {
      setNotice(t('cloud.models.serveError', { error: String(e) }))
    } finally {
      setBusy((b) => ({ ...b, [adapterId]: false }))
    }
  }

  const handleAddCustom = async () => {
    setCustomBusy(true)
    setNotice(null)
    try {
      const payload: Record<string, unknown> = {
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
      }
      await apiPost('/models/custom', payload)
      setCustomForm(EMPTY_CUSTOM_FORM)
      setShowAddCustom(false)
      await load()
    } catch (e) {
      setNotice(t('cloud.models.addCustomError', { error: String(e) }))
    } finally {
      setCustomBusy(false)
    }
  }

  const handleRemoveCustom = async (adapterId: string) => {
    setBusy((b) => ({ ...b, [adapterId]: true }))
    try {
      await apiDelete(`/models/custom/${adapterId}`)
      await load()
    } catch (e) {
      setNotice(t('cloud.models.deleteError', { error: String(e) }))
    } finally {
      setBusy((b) => ({ ...b, [adapterId]: false }))
    }
  }

  if (!open) return null

  return (
    <Modal
      title={t('cloud.models.title')}
      onClose={onClose}
      wide
      footer={(
        <div className="flex items-center justify-end border-t border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-4 py-2.5">
          <button type="button" onClick={onClose} className="btn btn-sm">
            {t('cloud.models.close')}
          </button>
        </div>
      )}
    >
      <div className="space-y-4 p-4 text-[13px] leading-relaxed">
          <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.models.intro')}</p>
          </div>

          <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
            <div className="flex items-center justify-between">
              <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.models.addCustomHint')}</p>
              <button type="button" className="btn btn-sm" onClick={() => setShowAddCustom((v) => !v)}>
                {showAddCustom ? t('cloud.models.addCustomCollapse') : t('cloud.models.addCustomOpen')}
              </button>
            </div>
            {showAddCustom && (
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('cloud.models.fieldHfRepo')}
                  <input
                    className="fld fld-mono"
                    placeholder="org/model-name"
                    value={customForm.hf_repo}
                    onChange={(e) => setCustomForm((f) => ({ ...f, hf_repo: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('cloud.models.fieldDisplayName')}
                  <input
                    className="fld fld-mono"
                    value={customForm.display_name}
                    onChange={(e) => setCustomForm((f) => ({ ...f, display_name: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('cloud.models.fieldHfRevision')}
                  <input
                    className="fld fld-mono"
                    value={customForm.hf_revision}
                    onChange={(e) => setCustomForm((f) => ({ ...f, hf_revision: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('cloud.models.fieldServedName')}
                  <input
                    className="fld fld-mono"
                    value={customForm.served_model_name}
                    onChange={(e) => setCustomForm((f) => ({ ...f, served_model_name: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('cloud.models.fieldMaxModelLen')}
                  <input
                    className="fld fld-mono"
                    type="number"
                    value={customForm.max_model_len}
                    onChange={(e) => setCustomForm((f) => ({ ...f, max_model_len: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('cloud.models.fieldGpuMemUtil')}
                  <input
                    className="fld fld-mono"
                    type="number"
                    step="0.05"
                    min="0.1"
                    max="0.99"
                    value={customForm.gpu_memory_utilization}
                    onChange={(e) => setCustomForm((f) => ({ ...f, gpu_memory_utilization: e.target.value }))}
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] sm:col-span-2">
                  {t('cloud.models.fieldExtraArgs')}
                  <input
                    className="fld fld-mono"
                    placeholder="--dtype bfloat16 --tensor-parallel-size 1"
                    value={customForm.extra_args}
                    onChange={(e) => setCustomForm((f) => ({ ...f, extra_args: e.target.value }))}
                  />
                </label>
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

          {notice && (
            <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-3 py-2 text-[12px]">
              {notice}
            </div>
          )}

          <div className="divide-y divide-[color:var(--color-rule)] border border-[color:var(--color-rule)]">
            {models.map((m) => {
              const rowBusy = !!busy[m.adapter_id]
              return (
                <div
                  key={m.adapter_id}
                  className="flex flex-wrap items-start justify-between gap-3 bg-[color:var(--color-sheet)] p-3"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-bold">{m.display_name}</span>
                      {m.installed ? (
                        <Badge tone="ok">{t('cloud.models.installed', { size: fmtBytes(m.size_bytes) })}</Badge>
                      ) : m.downloading ? (
                        <Badge tone="neutral">{t('cloud.models.downloading', { size: fmtBytes(m.size_bytes) })}</Badge>
                      ) : m.state === 'failed' ? (
                        <Badge tone="warn">{t('cloud.models.failed')}</Badge>
                      ) : (
                        <Badge tone="neutral">{t('cloud.models.notInstalled')}</Badge>
                      )}
                      {m.adapter_id === 'monkeyocrv2-parsing' && <Badge tone="ok">{t('cloud.models.defaultBadge')}</Badge>}
                      {m.adapter_id.startsWith(CUSTOM_ID_PREFIX) && (
                        <Badge tone="neutral">{t('cloud.models.customBadge')}</Badge>
                      )}
                      {m.supports_native && <Badge tone="ok">{t('cloud.models.prefillReady')}</Badge>}
                      {m.export_ready && <Badge tone="ok">{t('cloud.models.exportReady')}</Badge>}
                      {m.cloud_serve_ready && <Badge tone="ok">{t('cloud.models.cloudReady')}</Badge>}
                      {m.download_only && <Badge tone="neutral">{t('cloud.models.downloadOnly')}</Badge>}
                      {serveStatus.running && serveStatus.adapter_id === m.adapter_id && (
                        <Badge tone="ok">{t('cloud.models.serving', { port: String(serveStatus.port) })}</Badge>
                      )}
                    </div>
                    <div className="mono text-[11px] text-[color:var(--color-ink-3)] mt-0.5">
                      {m.hf_repo}
                      {m.approx_size_gb != null && ` · ~${m.approx_size_gb} GB`}
                      {m.table_format && ` · ${m.table_format}`}
                    </div>
                    <div className="text-[11px] text-[color:var(--color-ink-2)] mt-0.5">
                      {t('cloud.models.trainingLabel', { toolchain: toolchainLabel(m.train_toolchain) })}
                      {!m.local_serve_ready && !m.cloud_serve_ready && ` · ${t('cloud.models.inferenceUnavailable')}`}
                    </div>
                    {m.license_note && (
                      <div className="text-[11px] text-[color:var(--color-ink-3)] mt-0.5">{m.license_note}</div>
                    )}
                    {m.error && <div className="mt-0.5 text-[11px] text-[color:var(--color-sig-text)]">{m.error}</div>}
                    {m.vram_warning && (
                      <div className="mt-0.5 flex items-start gap-1 text-[11px] text-[color:var(--color-warn)]">
                        <IconWarn size={12} />
                        <span>{m.vram_warning}</span>
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2">
                    {m.downloading ? (
                      <button
                        type="button"
                        onClick={() => void handleCancel(m.adapter_id)}
                        disabled={rowBusy}
                        className="btn btn-sm"
                      >
                        {t('cloud.models.cancel')}
                      </button>
                    ) : m.installed ? (
                      <>
                        {m.local_serve_ready && (
                          serveStatus.running && serveStatus.adapter_id === m.adapter_id ? (
                            <button
                              type="button"
                              onClick={() => void handleServeStop(m.adapter_id)}
                              disabled={rowBusy}
                              className="btn btn-sm"
                            >
                              {t('cloud.models.serveStop')}
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => void handleServeStart(m.adapter_id)}
                              disabled={rowBusy}
                              className="btn btn-sm text-[color:var(--color-ok)]"
                            >
                              {t('cloud.models.serveStart')}
                            </button>
                          )
                        )}
                        <button
                          type="button"
                          onClick={() => void handleDelete(m.adapter_id)}
                          disabled={rowBusy}
                          className={`btn btn-sm ${
                            confirmDelete === m.adapter_id ? 'text-[color:var(--color-sig-text)]' : ''
                          }`}
                        >
                          {confirmDelete === m.adapter_id ? t('cloud.models.confirmDelete') : t('cloud.models.delete')}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => void handleDownload(m.adapter_id)}
                          disabled={rowBusy}
                          className="btn btn-sm btn-primary"
                        >
                          {rowBusy ? t('cloud.models.downloadStarting') : t('cloud.models.download')}
                        </button>
                        {m.adapter_id.startsWith(CUSTOM_ID_PREFIX) && (
                          <button
                            type="button"
                            onClick={() => void handleRemoveCustom(m.adapter_id)}
                            disabled={rowBusy}
                            className="btn btn-sm"
                          >
                            {t('cloud.models.removeCustom')}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )
            })}
            {models.length === 0 && (
              <div className="p-3 text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.models.loading')}</div>
            )}
          </div>
      </div>
    </Modal>
  )
}
