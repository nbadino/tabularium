import { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../lib/api'
import { IconCloud } from './icons'
import { Badge, Field } from './ui'
import { saveInferenceToBackend, testInferenceConnection, useInference } from './inference'

interface CloudControlModalProps {
  open: boolean
  onClose: () => void
}

interface TunnelState {
  running: boolean
  host?: string | null
  port?: number | null
  local_port?: number
  pid?: number | null
  error?: string | null
}

interface VastInstance {
  id: number
  status: string
  gpu_name: string
  num_gpus: number
  dph_total: number
  ssh_host: string
  ssh_port: number
  is_running: boolean
  label: string
}

export function CloudControlModal({ open, onClose }: CloudControlModalProps) {
  const inf = useInference()
  const [tab, setTab] = useState<'tunnel' | 'vast' | 'direct'>('tunnel')

  // Tunnel state
  const [sshHost, setSshHost] = useState('')
  const [sshPort, setSshPort] = useState('')
  const [sshUser, setSshUser] = useState('root')
  const [tunnelState, setTunnelState] = useState<TunnelState>({ running: false })
  const [tunnelBusy, setTunnelBusy] = useState(false)
  const [tunnelNotice, setTunnelNotice] = useState<string | null>(null)

  // Vast.ai API state
  const [vastApiKey, setVastApiKey] = useState(() => localStorage.getItem('lloyds.vast_api_key') || '')
  const [vastInstances, setVastInstances] = useState<VastInstance[]>([])
  const [vastBusy, setVastBusy] = useState(false)
  const [vastNotice, setVastNotice] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    void pollTunnelStatus()
  }, [open])

  const pollTunnelStatus = async () => {
    try {
      const res = await apiGet<TunnelState>('/system/cloud/tunnel')
      setTunnelState(res)
      if (res.host) setSshHost(res.host)
      if (res.port) setSshPort(String(res.port))
    } catch {
      /* ignore */
    }
  }

  // --- SSH Tunnel Handlers ---
  const handleStartTunnel = async () => {
    if (!sshHost.trim() || !sshPort.trim()) {
      setTunnelNotice('Inserisci sia l\'host che la porta SSH.')
      return
    }
    setTunnelBusy(true)
    setTunnelNotice(null)
    try {
      const res = await apiPost<TunnelState>('/system/cloud/tunnel/start', {
        host: sshHost.trim(),
        port: parseInt(sshPort.trim(), 10),
        user: sshUser.trim() || 'root',
        local_port: 8888,
        remote_port: 8888,
      })
      setTunnelState(res)
      setTunnelNotice(`Tunnel SSH avviato con successo su 127.0.0.1:8888 (PID: ${res.pid})!`)

      // Auto-salva la configurazione locale e testa la connessione
      await saveInferenceToBackend({
        enabled: true,
        url: 'http://127.0.0.1:8888/v1',
        model: 'MonkeyOCRv2',
      })
      await testInferenceConnection({ url: 'http://127.0.0.1:8888/v1' })
    } catch (e) {
      setTunnelNotice(`Errore avvio tunnel: ${e}`)
    } finally {
      setTunnelBusy(false)
    }
  }

  const handleStopTunnel = async () => {
    setTunnelBusy(true)
    try {
      await apiPost('/system/cloud/tunnel/stop', {})
      setTunnelState({ running: false })
      setTunnelNotice('Tunnel SSH terminato.')
    } catch (e) {
      setTunnelNotice(`Errore arresto tunnel: ${e}`)
    } finally {
      setTunnelBusy(false)
    }
  }

  // --- Vast.ai Handlers ---
  const handleLoadVast = async () => {
    if (!vastApiKey.trim()) {
      setVastNotice('Inserisci la tua API Key di Vast.ai.')
      return
    }
    setVastBusy(true)
    setVastNotice(null)
    try {
      localStorage.setItem('lloyds.vast_api_key', vastApiKey.trim())
      const res = await apiPost<{ items: VastInstance[] }>('/system/cloud/vast/instances', {
        api_key: vastApiKey.trim(),
      })
      setVastInstances(res.items)
      if (res.items.length === 0) {
        setVastNotice('Nessuna istanza attiva trovata sul tuo account Vast.ai.')
      } else {
        setVastNotice(`Trovate ${res.items.length} istanze su Vast.ai.`)
      }
    } catch (e) {
      setVastNotice(`Errore caricamento Vast.ai: ${e}`)
    } finally {
      setVastBusy(false)
    }
  }

  const handleControlVast = async (instanceId: number, action: 'start' | 'stop') => {
    setVastBusy(true)
    try {
      await apiPost('/system/cloud/vast/control', {
        api_key: vastApiKey.trim(),
        instance_id: instanceId,
        action,
      })
      setVastNotice(`Comando "${action === 'start' ? 'Avvia' : 'Metti in pausa'}" inviato con successo!`)
      await handleLoadVast()
    } catch (e) {
      setVastNotice(`Errore comando Vast.ai: ${e}`)
    } finally {
      setVastBusy(false)
    }
  }

  const handleConnectToVastInstance = (inst: VastInstance) => {
    if (!inst.ssh_host || !inst.ssh_port) {
      setVastNotice('Questa istanza non ha ancora un host/porta SSH assegnati (forse è in fase di avvio).')
      return
    }
    setSshHost(inst.ssh_host)
    setSshPort(String(inst.ssh_port))
    setTab('tunnel')
    setTunnelNotice(`Dati di connessione dell'istan #${inst.id} caricati! Clicca su "Avvia Tunnel da UI".`)
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-panel)] px-4 py-3">
          <div className="flex items-center gap-2">
            <IconCloud size={16} />
            <h2 className="text-[15px] font-bold">
              Gestione Cloud & Connessione GPU 100% da UI
            </h2>
          </div>
          <button type="button" onClick={onClose} className="btn btn-sm">
            ✕ Chiudi
          </button>
        </div>

        {/* Navigation Tabs */}
        <div className="flex border-b border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-4 pt-2">
          <button
            type="button"
            onClick={() => setTab('tunnel')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'tunnel'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            🔌 Tunnel SSH Automatico (1 Click)
          </button>
          <button
            type="button"
            onClick={() => setTab('vast')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'vast'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            ☁️ Gestione Istanze Vast.ai
          </button>
          <button
            type="button"
            onClick={() => setTab('direct')}
            className={`border-b-2 px-3 py-1.5 text-[12px] font-medium transition-colors ${
              tab === 'direct'
                ? 'border-[color:var(--color-ink)] text-[color:var(--color-ink)] font-semibold'
                : 'border-transparent text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)]'
            }`}
          >
            🌐 Endpoint Diretto / RunPod Proxy
          </button>
        </div>

        {/* Content Body */}
        <div className="overflow-y-auto p-4 text-[13px] leading-relaxed">
          {tab === 'tunnel' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-bold text-[14px]">Tunnel SSH Locale Automatizzato</h3>
                    <p className="mt-0.5 text-[12px] text-[color:var(--color-ink-2)]">
                      Inoltra la porta 8888 della tua GPU remota su <code className="mono">http://127.0.0.1:8888/v1</code> senza toccare il terminale.
                    </p>
                  </div>
                  {tunnelState.running ? (
                    <Badge tone="ok">🟢 Tunnel Attivo (PID {tunnelState.pid})</Badge>
                  ) : (
                    <Badge tone="neutral">⚪ Tunnel Inattivo</Badge>
                  )}
                </div>
              </div>

              {tunnelNotice && (
                <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-3 py-2 text-[12px]">
                  {tunnelNotice}
                </div>
              )}

              <div className="grid gap-3 sm:grid-cols-2">
                <Field
                  label="Host Remoto SSH"
                  hint="Fornito dal provider (es. ssh5.vast.ai o 192.168.1.100)"
                >
                  <input
                    value={sshHost}
                    onChange={(e) => setSshHost(e.target.value)}
                    placeholder="ssh5.vast.ai"
                    disabled={tunnelState.running}
                    className="fld fld-mono"
                  />
                </Field>

                <Field
                  label="Porta SSH"
                  hint="Porta SSH remota assegnata (es. 38291 o 22)"
                >
                  <input
                    value={sshPort}
                    onChange={(e) => setSshPort(e.target.value)}
                    placeholder="38291"
                    disabled={tunnelState.running}
                    className="fld fld-mono"
                  />
                </Field>

                <Field label="Utente SSH" hint="Default: root">
                  <input
                    value={sshUser}
                    onChange={(e) => setSshUser(e.target.value)}
                    placeholder="root"
                    disabled={tunnelState.running}
                    className="fld fld-mono"
                  />
                </Field>

                <div className="flex items-end gap-2">
                  {tunnelState.running ? (
                    <button
                      type="button"
                      onClick={() => void handleStopTunnel()}
                      disabled={tunnelBusy}
                      className="btn !border-red-700 !bg-red-950 !text-red-300 w-full"
                    >
                      {tunnelBusy ? 'Arresto in corso…' : '⏹️ Arresta Tunnel'}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void handleStartTunnel()}
                      disabled={tunnelBusy}
                      className="btn btn-primary w-full"
                    >
                      {tunnelBusy ? 'Connessione in corso…' : '🔌 Avvia Tunnel da UI'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {tab === 'vast' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">Gestione Istanze Vast.ai</h3>
                <p className="mt-0.5 text-[12px] text-[color:var(--color-ink-2)]">
                  Inserisci la tua API Key di Vast.ai per vedere le tue GPU, avviarle, metterle in pausa quando non le usi e connetterti con un click.
                </p>
              </div>

              {vastNotice && (
                <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-sheet-dim)] px-3 py-2 text-[12px]">
                  {vastNotice}
                </div>
              )}

              <div className="flex gap-2">
                <div className="flex-1">
                  <Field
                    label="Vast.ai API Key"
                    hint="Trova la tua chiave su vast.ai ➔ Account ➔ API Keys"
                  >
                    <input
                      type="password"
                      value={vastApiKey}
                      onChange={(e) => setVastApiKey(e.target.value)}
                      placeholder="Incolla la tua API Key di Vast.ai"
                      className="fld fld-mono"
                    />
                  </Field>
                </div>
                <div className="flex items-end">
                  <button
                    type="button"
                    onClick={() => void handleLoadVast()}
                    disabled={vastBusy}
                    className="btn btn-primary"
                  >
                    {vastBusy ? 'Caricamento…' : '🔄 Carica Istanze'}
                  </button>
                </div>
              </div>

              {vastInstances.length > 0 && (
                <div className="space-y-2">
                  <span className="lbl">Istanze trovate sul tuo account:</span>
                  <div className="divide-y divide-[color:var(--color-rule)] border border-[color:var(--color-rule)]">
                    {vastInstances.map((inst) => (
                      <div
                        key={inst.id}
                        className="flex flex-wrap items-center justify-between gap-3 bg-[color:var(--color-sheet)] p-3"
                      >
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="font-bold">{inst.label}</span>
                            <Badge tone={inst.is_running ? 'ok' : 'neutral'}>
                              {inst.status}
                            </Badge>
                            {inst.dph_total && (
                              <span className="mono text-[11px] text-[color:var(--color-sig)] font-semibold">
                                ${inst.dph_total.toFixed(3)}/ora
                              </span>
                            )}
                          </div>
                          <div className="mono text-[11px] text-[color:var(--color-ink-3)] mt-0.5">
                            ID: {inst.id} {inst.ssh_host && `· SSH: ${inst.ssh_host}:${inst.ssh_port}`}
                          </div>
                        </div>

                        <div className="flex items-center gap-2">
                          {inst.is_running ? (
                            <>
                              <button
                                type="button"
                                onClick={() => handleConnectToVastInstance(inst)}
                                className="btn btn-sm btn-primary"
                              >
                                🔌 Connetti Tunnel
                              </button>
                              <button
                                type="button"
                                onClick={() => void handleControlVast(inst.id, 'stop')}
                                disabled={vastBusy}
                                className="btn btn-sm"
                                title="Metti in pausa l'istanza per interrompere il costo orario"
                              >
                                ⏸️ Pausa
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => void handleControlVast(inst.id, 'start')}
                              disabled={vastBusy}
                              className="btn btn-sm btn-primary"
                            >
                              ▶️ Riattiva Istanza
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {tab === 'direct' && (
            <div className="space-y-4">
              <div className="rounded border border-[color:var(--color-rule)] bg-[color:var(--color-panel)] p-3">
                <h3 className="font-bold text-[14px]">Connessione Diretta (RunPod Proxy / VPS)</h3>
                <p className="mt-0.5 text-[12px] text-[color:var(--color-ink-2)]">
                  Se hai già un URL HTTPS pubblico fornito da RunPod (o un IP pubblico diretto), inseriscilo qui per connetterti direttamente senza tunnel.
                </p>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <Field label="URL Server vLLM" hint="Es: https://<POD_ID>-8888.proxy.runpod.net/v1">
                    <input
                      defaultValue={inf.url}
                      id="direct-url-input"
                      placeholder="https://<POD_ID>-8888.proxy.runpod.net/v1"
                      className="fld fld-mono"
                    />
                  </Field>
                </div>
                <div className="sm:col-span-2">
                  <Field label="API Key (Opzionale)">
                    <input
                      type="password"
                      defaultValue={inf.apiKey}
                      id="direct-apikey-input"
                      placeholder="Chiave segreta"
                      className="fld fld-mono"
                    />
                  </Field>
                </div>
                <div className="sm:col-span-2 flex justify-end">
                  <button
                    type="button"
                    onClick={async () => {
                      const u = (document.getElementById('direct-url-input') as HTMLInputElement)?.value;
                      const k = (document.getElementById('direct-apikey-input') as HTMLInputElement)?.value;
                      await saveInferenceToBackend({ enabled: true, url: u, apiKey: k });
                      await testInferenceConnection({ url: u, apiKey: k });
                      onClose();
                    }}
                    className="btn btn-primary"
                  >
                    💾 Salva e Applica
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-4 py-2.5">
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
            Tutti i comandi vengono eseguiti dal backend locale
          </span>
          <button type="button" onClick={onClose} className="btn btn-sm">
            Chiudi
          </button>
        </div>
      </div>
    </div>
  )
}
