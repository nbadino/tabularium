import { useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { Badge } from './ui'

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
  installed: boolean
  downloading: boolean
  path: string | null
  size_bytes: number
  state: string
  error: string | null
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

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] shadow-2xl">
        <div className="flex items-center justify-between border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-panel)] px-4 py-3">
          <h2 className="text-[15px] font-bold">📦 {t('cloud.models.title')}</h2>
          <button type="button" onClick={onClose} className="btn btn-sm">
            {t('cloud.models.close')}
          </button>
        </div>

        <div className="overflow-y-auto p-4 text-[13px] leading-relaxed space-y-4">
          <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.models.intro')}</p>
          </div>

          {notice && (
            <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-3 py-2 text-[12px]">
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
                      {m.serve_backend === 'none' && ` · ${t('cloud.models.inferenceUnavailable')}`}
                    </div>
                    {m.license_note && (
                      <div className="text-[11px] text-[color:var(--color-ink-3)] mt-0.5">{m.license_note}</div>
                    )}
                    {m.error && <div className="text-[11px] text-red-400 mt-0.5">{m.error}</div>}
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
                        {m.serve_backend !== 'none' && (
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
                              className="btn btn-sm !border-emerald-600 !bg-emerald-950 !text-emerald-300"
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
                            confirmDelete === m.adapter_id ? '!border-red-700 !bg-red-950 !text-red-300' : ''
                          }`}
                        >
                          {confirmDelete === m.adapter_id ? t('cloud.models.confirmDelete') : t('cloud.models.delete')}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void handleDownload(m.adapter_id)}
                        disabled={rowBusy}
                        className="btn btn-sm btn-primary"
                      >
                        {rowBusy ? t('cloud.models.downloadStarting') : t('cloud.models.download')}
                      </button>
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

        <div className="flex items-center justify-end border-t border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-4 py-2.5">
          <button type="button" onClick={onClose} className="btn btn-sm">
            {t('cloud.models.close')}
          </button>
        </div>
      </div>
    </div>
  )
}
