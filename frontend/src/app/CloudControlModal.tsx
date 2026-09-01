import { useEffect, useState } from 'react'
import { apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { IconCopy } from './icons'
import { Badge, Collapsible, Field, Modal, Module } from './ui'
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

/** Une istanza affittata (Vast.ai o RunPod): stessa forma per entrambi i provider. */
interface RentedInstance {
  id: number | string
  status: string
  gpu_name: string | null
  num_gpus: number
  dph_total: number | null
  ssh_host: string | null
  ssh_port: number | null
  is_running: boolean
  label: string
  cost_estimate?: { estimated_usd: number; hours: number; hourly_rate: number }
}

interface VastOffer {
  id: number
  gpu_name: string | null
  num_gpus: number
  gpu_ram: number | null
  dph_total: number | null
  reliability: number | null
  location: string | null
  verified?: boolean | null
}

interface ModalStatus {
  cli: boolean
  token: boolean
  templates: { id: string; label: string }[]
  template: string
  endpoint: string | null
  task: { kind: string; done: boolean; ok: boolean | null; log: string[] } | null
}

type Provider = 'vast' | 'runpod' | 'modal' | 'manual'

const MODAL_TEMPLATE_KEY = 'tabularium.modal.template'
const MODAL_KEEP_WARM_KEY = 'tabularium.modal.keep_warm'

/** Migra una preferenza innocua dal prefisso storico senza migrare segreti. */
export function readMigratedPreference(key: string, legacyKey: string): string | null {
  try {
    const current = localStorage.getItem(key)
    if (current !== null) {
      // Elimina anche un eventuale residuo storico: il valore corrente ha già
      // precedenza, quindi tenere la chiave legacy può solo creare ambiguità.
      if (localStorage.getItem(legacyKey) !== null) localStorage.removeItem(legacyKey)
      return current
    }
    const legacy = localStorage.getItem(legacyKey)
    if (legacy !== null) {
      localStorage.setItem(key, legacy)
      localStorage.removeItem(legacyKey)
    }
    return legacy
  } catch {
    return null
  }
}

/** Su quale provider aprire il pannello: quello che l'endpoint salvato indica già. */
function guessProvider(url: string): Provider {
  if (url.includes('.modal.run')) return 'modal'
  if (url.includes('proxy.runpod.net') || url.includes('api.runpod.ai')) return 'runpod'
  if (url.includes('127.0.0.1') || url.includes('localhost')) return 'vast'
  return url ? 'manual' : 'vast'
}

/** Quale template Modal è già in uso: si legge dal nome dell'app nell'URL salvato. */
export function guessModalTemplate(url: string): string {
  if (url.includes('paddleocr')) return 'paddleocr-vl'
  if (url.includes('mineru')) return 'mineru'
  if (url.includes('unlimited-ocr')) return 'unlimited-ocr'
  if (url.includes('dots-ocr')) return 'dots-ocr'
  if (url.includes('glm-ocr')) return 'glm-ocr'
  if (url.includes('deepseek-ocr')) return 'deepseek-ocr'
  if (url.includes('qwen3-vl')) return 'qwen3-vl'
  return 'monkeyocrv2'
}

/** Modello servito + adapter da attivare quando si usa l'endpoint di una template. */
export const MODAL_TEMPLATE_TARGET: Record<string, { model: string; adapterId: string }> = {
  monkeyocrv2: { model: 'MonkeyOCRv2', adapterId: 'monkeyocrv2-parsing' },
  'paddleocr-vl': { model: 'PaddleOCR-VL-1.6', adapterId: 'paddleocr-vl' },
  mineru: { model: 'mineru2.5', adapterId: 'mineru2.5' },
  'unlimited-ocr': { model: 'Unlimited-OCR', adapterId: 'unlimited-ocr' },
  'dots-ocr': { model: 'dots-mocr', adapterId: 'dots-ocr' },
  'glm-ocr': { model: 'glm-ocr', adapterId: 'glm-ocr' },
  'deepseek-ocr': { model: 'deepseek-ocr-2', adapterId: 'deepseek-ocr' },
  'qwen3-vl': { model: 'qwen3-vl-8b', adapterId: 'qwen3-vl-8b' },
}

/** Solo le template il cui adapter ha un percorso OCR verificato (prompt/
 * parsing, v. `supported_prefill_modes` in model_adapters.py) abilitano il
 * prefill pagina intera. GLM-OCR/DeepSeek-OCR-2/Qwen3-VL sono deployabili
 * (v. LOCAL_INFERENCE_GUIDE.md) ma non ancora integrati nel prefill: la GPU
 * si può comunque accendere e testare via playground/valutazione. */
export const PREFILL_MODAL_TEMPLATES = ['monkeyocrv2', 'paddleocr-vl', 'mineru', 'unlimited-ocr', 'dots-ocr']

/** Elenco statico: rispecchia `modal_manager.TEMPLATES` sul backend, non
 *  dipende dal primo round-trip di stato per apparire nella UI. */
export const MODAL_TEMPLATES = [
  { id: 'monkeyocrv2', label: 'MonkeyOCRv2-Parsing' },
  { id: 'paddleocr-vl', label: 'PaddleOCR-VL-1.6' },
  { id: 'mineru', label: 'MinerU2.5' },
  { id: 'unlimited-ocr', label: 'Unlimited-OCR' },
]

function copyToClipboard(text: string, onDone: () => void) {
  navigator.clipboard?.writeText(text).then(onDone).catch(() => {})
}

export function CloudControlModal({ open, onClose }: CloudControlModalProps) {
  const { t } = useI18n()
  const inf = useInference()
  const initialProvider = guessProvider(inf.url)

  // --- Vast.ai + tunnel SSH ---
  const [vastApiKey, setVastApiKey] = useState('')
  const [vastInstances, setVastInstances] = useState<RentedInstance[]>([])
  const [vastBusy, setVastBusy] = useState(false)
  const [vastNotice, setVastNotice] = useState<string | null>(null)
  const [vastOffers, setVastOffers] = useState<VastOffer[]>([])
  const [vastGpu, setVastGpu] = useState('')
  const [vastMaxDph, setVastMaxDph] = useState('')
  const [vastDiskGb, setVastDiskGb] = useState('40')
  const [vastPrepare, setVastPrepare] = useState(true)
  const [vastMonkeyRef, setVastMonkeyRef] = useState('')
  const [vastTabulariumRef, setVastTabulariumRef] = useState('')
  const [sshHost, setSshHost] = useState('')
  const [sshPort, setSshPort] = useState('')
  const [sshUser, setSshUser] = useState('root')
  const [tunnelState, setTunnelState] = useState<TunnelState>({ running: false })
  const [tunnelBusy, setTunnelBusy] = useState(false)

  // --- RunPod ---
  const [runpodApiKey, setRunpodApiKey] = useState('')
  const [runpodPods, setRunpodPods] = useState<RentedInstance[]>([])
  const [runpodBusy, setRunpodBusy] = useState(false)
  const [runpodNotice, setRunpodNotice] = useState<string | null>(null)

  // --- Modal serverless ---
  // Ricorda l'ultima template guardata (non solo quella attiva per
  // l'inferenza): senza, un deploy avviato e non ancora attivato spariva
  // dalla vista a ogni refresh — riportava sempre alla template attualmente
  // in uso, nascondendo il task in corso su un'altra. Riprodotto dal vivo.
  const [modalTemplate, setModalTemplateRaw] = useState(
    () => readMigratedPreference(MODAL_TEMPLATE_KEY, 'lloyds.modal_template') || guessModalTemplate(inf.url),
  )
  const setModalTemplate = (id: string) => {
    setModalTemplateRaw(id)
    try {
      localStorage.setItem(MODAL_TEMPLATE_KEY, id)
    } catch {
      /* storage non disponibile: la scelta resta valida per la sessione */
    }
  }
  const [modalStatus, setModalStatus] = useState<ModalStatus | null>(null)
  const [modalApiKey, setModalApiKey] = useState('')
  const [modalKeepWarm, setModalKeepWarm] = useState(
    () => readMigratedPreference(MODAL_KEEP_WARM_KEY, 'lloyds.modal_keep_warm') === '1',
  )
  const [modalBusy, setModalBusy] = useState(false)
  const [modalNotice, setModalNotice] = useState<string | null>(null)
  const runningTask = modalStatus?.task && !modalStatus.task.done ? modalStatus.task : null

  // --- Manuale ---
  const [manualUrl, setManualUrl] = useState(inf.url)
  const [manualModel, setManualModel] = useState(inf.model)
  const [manualKey, setManualKey] = useState(inf.apiKey)

  const [copied, setCopied] = useState<string | null>(null)
  const copy = (text: string, id: string) => copyToClipboard(text, () => { setCopied(id); setTimeout(() => setCopied(null), 2000) })

  useEffect(() => {
    if (!open) return
    setManualUrl(inf.url)
    setManualModel(inf.model)
    setManualKey(inf.apiKey)
    void pollTunnelStatus()
  }, [open])

  // Il task Modal è long-running (deploy: minuti): si interroga lo stato
  // finché la finestra è aperta. Ogni template ha il proprio stato/endpoint.
  useEffect(() => {
    if (!open) return
    let stop = false
    const tick = async () => {
      try {
        const res = await apiGet<ModalStatus>(`/system/cloud/modal?template=${modalTemplate}`)
        if (!stop) setModalStatus(res)
      } catch {
        /* ignore */
      }
    }
    void tick()
    const id = setInterval(() => void tick(), 2000)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [open, modalTemplate])

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

  // --- Tunnel SSH ---
  const handleStartTunnel = async () => {
    if (!sshHost.trim() || !sshPort.trim()) {
      setVastNotice(t('cloud.control.missingHostPort'))
      return
    }
    setTunnelBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<TunnelState>('/system/cloud/tunnel/start', {
        host: sshHost.trim(),
        port: parseInt(sshPort.trim(), 10),
        user: sshUser.trim() || 'root',
        local_port: 8888,
        remote_port: 8888,
      })
      setTunnelState(res)
      setVastNotice(t('cloud.control.started', { pid: String(res.pid) }))
      await saveInferenceToBackend({ enabled: true, url: 'http://127.0.0.1:8888/v1', model: 'MonkeyOCRv2' })
      await testInferenceConnection({ url: 'http://127.0.0.1:8888/v1' })
    } catch (e) {
      setVastNotice(t('cloud.control.startError', { error: String(e) }))
    } finally {
      setTunnelBusy(false)
    }
  }

  const handleStopTunnel = async () => {
    setTunnelBusy(true)
    try {
      await apiPost('/system/cloud/tunnel/stop', {})
      setTunnelState({ running: false })
      setVastNotice(t('cloud.control.stopped'))
    } catch (e) {
      setVastNotice(t('cloud.control.stopError', { error: String(e) }))
    } finally {
      setTunnelBusy(false)
    }
  }

  // --- Vast.ai ---
  const handleLoadVast = async () => {
    if (!vastApiKey.trim()) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    setVastBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<{ items: RentedInstance[] }>('/system/cloud/vast/instances', {
        api_key: vastApiKey.trim(),
      })
      setVastInstances(res.items)
      setVastNotice(
        res.items.length === 0
          ? t('cloud.control.noneFound')
          : t('cloud.control.found', { count: res.items.length }),
      )
    } catch (e) {
      setVastNotice(t('cloud.control.loadError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleSearchVast = async () => {
    if (!vastApiKey.trim()) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    setVastBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<{ items: VastOffer[] }>('/system/cloud/vast/offers', {
        api_key: vastApiKey.trim(), gpu_name: vastGpu.trim(), max_dph: vastMaxDph ? Number(vastMaxDph) : null,
        num_gpus: 1, min_reliability: 0.95, instance_type: 'on-demand',
      })
      setVastOffers(res.items)
      setVastNotice(res.items.length ? t('cloud.control.offersFound', { count: res.items.length }) : t('cloud.control.noOffers'))
    } catch (e) {
      setVastNotice(t('cloud.control.loadError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleRentVast = async (offer: VastOffer) => {
    if (vastPrepare && !vastMonkeyRef.trim()) {
      setVastNotice(t('cloud.control.monkeyRefRequired'))
      return
    }
    if (vastPrepare && !vastTabulariumRef.trim()) {
      setVastNotice(t('cloud.control.tabulariumRefRequired'))
      return
    }
    const price = offer.dph_total == null ? t('cloud.control.priceUnknown') : `$${offer.dph_total.toFixed(3)}/h`
    if (!window.confirm(t('cloud.control.rentConfirm', { gpu: `${offer.num_gpus}× ${offer.gpu_name || 'GPU'}`, price }))) return
    setVastBusy(true)
    setVastNotice(null)
    try {
      await apiPost('/system/cloud/vast/rent', {
        api_key: vastApiKey.trim(), offer_id: offer.id, disk_gb: Number(vastDiskGb) || 40,
        dph_total: offer.dph_total,
        prepare_server: vastPrepare, monkeyocr_ref: vastMonkeyRef.trim(), tabularium_ref: vastTabulariumRef.trim(), model: 'zenosai/MonkeyOCRv2-B-Parsing', port: 8888,
      })
      setVastOffers([])
      setVastNotice(t('cloud.control.rentStarted'))
      await handleLoadVast()
    } catch (e) {
      setVastNotice(t('cloud.control.rentError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleControlVast = async (instanceId: number | string, action: 'start' | 'stop' | 'delete') => {
    if (action === 'delete' && !window.confirm(t('cloud.control.deleteResourceConfirm', { id: String(instanceId) }))) return
    setVastBusy(true)
    try {
      await apiPost('/system/cloud/vast/control', { api_key: vastApiKey.trim(), instance_id: instanceId, action })
      setVastNotice(
        t('cloud.control.commandSent', {
          action: action === 'start' ? t('cloud.control.actionStart') : action === 'stop' ? t('cloud.control.actionPause') : t('cloud.control.actionDelete'),
        }),
      )
      await handleLoadVast()
    } catch (e) {
      setVastNotice(t('cloud.control.commandError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleConnectVast = (inst: RentedInstance) => {
    if (!inst.ssh_host || !inst.ssh_port) {
      setVastNotice(t('cloud.control.noSsh'))
      return
    }
    setSshHost(inst.ssh_host)
    setSshPort(String(inst.ssh_port))
    setVastNotice(t('cloud.control.connected', { id: String(inst.id) }))
  }

  // --- RunPod ---
  const handleLoadRunpod = async () => {
    if (!runpodApiKey.trim()) {
      setRunpodNotice(t('cloud.control.missingKey'))
      return
    }
    setRunpodBusy(true)
    setRunpodNotice(null)
    try {
      const res = await apiPost<{ items: RentedInstance[] }>('/system/cloud/runpod/pods', {
        api_key: runpodApiKey.trim(),
      })
      setRunpodPods(res.items)
      setRunpodNotice(
        res.items.length === 0
          ? t('cloud.control.noneFound')
          : t('cloud.control.found', { count: res.items.length }),
      )
    } catch (e) {
      setRunpodNotice(t('cloud.control.loadError', { error: String(e) }))
    } finally {
      setRunpodBusy(false)
    }
  }

  const handleControlRunpod = async (podId: number | string, action: 'start' | 'stop' | 'delete') => {
    if (action === 'delete' && !window.confirm(t('cloud.control.deleteResourceConfirm', { id: String(podId) }))) return
    setRunpodBusy(true)
    try {
      await apiPost('/system/cloud/runpod/control', { api_key: runpodApiKey.trim(), pod_id: podId, action })
      setRunpodNotice(
        t('cloud.control.commandSent', {
          action: action === 'start' ? t('cloud.control.actionStart') : action === 'stop' ? t('cloud.control.actionPause') : t('cloud.control.actionDelete'),
        }),
      )
      await handleLoadRunpod()
    } catch (e) {
      setRunpodNotice(t('cloud.control.commandError', { error: String(e) }))
    } finally {
      setRunpodBusy(false)
    }
  }

  const handleUseRunpodProxy = async (pod: RentedInstance) => {
    const url = `https://${pod.id}-8888.proxy.runpod.net/v1`
    try {
      await saveInferenceToBackend({ enabled: true, url, apiKey: runpodApiKey.trim() || undefined })
      await testInferenceConnection({ url, apiKey: runpodApiKey.trim() || undefined })
      setRunpodNotice(t('cloud.control.runpodProxySet', { url }))
    } catch (e) {
      setRunpodNotice(t('cloud.control.commandError', { error: String(e) }))
    }
  }

  // --- Modal serverless ---
  const handleModalSetup = async () => {
    setModalBusy(true)
    setModalNotice(null)
    try {
      await apiPost('/system/cloud/modal/setup', {})
      setModalNotice(t('cloud.control.modalSetupStarted'))
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    } finally {
      setModalBusy(false)
    }
  }

  const handleModalDeploy = async () => {
    setModalBusy(true)
    setModalNotice(null)
    try {
      await apiPost('/system/cloud/modal/deploy', {
        template: modalTemplate,
        api_key: modalApiKey.trim() || null,
        keep_warm: modalKeepWarm,
      })
      setModalNotice(t('cloud.control.modalDeployStarted'))
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    } finally {
      setModalBusy(false)
    }
  }

  const handleModalStop = async () => {
    if (!window.confirm(t('cloud.control.modalStopConfirm'))) return
    setModalBusy(true)
    setModalNotice(null)
    try {
      await apiPost('/system/cloud/modal/stop', { template: modalTemplate })
      setModalNotice(t('cloud.control.modalStopStarted'))
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    } finally {
      setModalBusy(false)
    }
  }

  const handleUseModalEndpoint = async () => {
    const endpoint = modalStatus?.endpoint
    if (!endpoint) return
    const url = endpoint.replace(/\/$/, '') + '/v1'
    const target = MODAL_TEMPLATE_TARGET[modalTemplate] ?? MODAL_TEMPLATE_TARGET.monkeyocrv2
    try {
      const connection = {
        enabled: true,
        url,
        model: target.model,
        adapterId: target.adapterId,
        apiKey: modalApiKey.trim() || undefined,
      }
      // Verifica prima l'endpoint reale e il nome modello esposto da /v1/models:
      // un deploy ancora in avvio o una template diversa non deve sovrascrivere
      // una configurazione funzionante.
      await testInferenceConnection(connection)
      await saveInferenceToBackend(connection)
      onClose()
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    }
  }

  // --- Manuale ---
  const handleSaveManual = async () => {
    try {
      const connection = {
        enabled: true,
        url: manualUrl,
        model: manualModel,
        apiKey: manualKey,
      }
      await testInferenceConnection(connection)
      await saveInferenceToBackend(connection)
      onClose()
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    }
  }

  if (!open) return null

  const statusTone = (running: boolean, status: string) => {
    if (running) return 'ok' as const
    if (/fail|error/i.test(status)) return 'warn' as const
    return 'neutral' as const
  }

  const renderInstanceList = (
    items: RentedInstance[],
    busy: boolean,
    onConnect: (inst: RentedInstance) => void,
    connectLabel: string,
    onStart: (id: number | string) => void,
    onStop: (id: number | string) => void,
    onDelete: (id: number | string) => void,
  ) => (
    <Module tab={t('cloud.control.instancesLabel')} quiet flush>
      <div className="divide-y divide-[color:var(--color-rule)]">
        {items.map((inst) => (
          <div key={inst.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold">{inst.label}</span>
                <Badge tone={statusTone(inst.is_running, inst.status)}>{inst.status}</Badge>
                {inst.dph_total != null && (
                  <span className="mono text-[11px] font-semibold text-[color:var(--color-sig)]">
                    ${inst.dph_total.toFixed(3)}/h
                  </span>
                )}
                {inst.cost_estimate && (
                  <span className="mono text-[11px] text-[color:var(--color-ink-2)]">
                    {t('cloud.control.costSoFar', { cost: inst.cost_estimate.estimated_usd.toFixed(2) })}
                  </span>
                )}
              </div>
              <div className="mono mt-0.5 text-[11px] text-[color:var(--color-ink-3)]">
                ID: {inst.id} {inst.ssh_host && `· SSH: ${inst.ssh_host}:${inst.ssh_port}`}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {inst.is_running ? (
                <>
                  <button type="button" onClick={() => onConnect(inst)} className="btn btn-sm btn-primary">
                    {connectLabel}
                  </button>
                  <button
                    type="button"
                    onClick={() => onStop(inst.id)}
                    disabled={busy}
                    className="btn btn-sm"
                    title={t('cloud.control.pauseTitle')}
                  >
                    {t('cloud.control.pause')}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => onStart(inst.id)}
                  disabled={busy}
                  className="btn btn-sm btn-primary"
                >
                  {t('cloud.control.resume')}
                </button>
              )}
              <button
                type="button"
                onClick={() => onDelete(inst.id)}
                disabled={busy}
                className="btn btn-sm btn-danger"
                title={t('cloud.control.deleteResource')}
              >
                {t('cloud.control.deleteResource')}
              </button>
            </div>
          </div>
        ))}
      </div>
    </Module>
  )

  const commandBox = (cmd: string, id: string) => (
    <div className="mt-1.5 flex items-center gap-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] p-2 font-mono text-[11px] text-[color:var(--color-ink)]">
      <code className="flex-1 overflow-x-auto">{cmd}</code>
      <button type="button" onClick={() => copy(cmd, id)} className="btn btn-sm shrink-0">
        <IconCopy size={12} />
        {copied === id ? t('cloud.control.copied') : t('cloud.control.copy')}
      </button>
    </div>
  )

  const activeProviderLabel =
    initialProvider === 'modal'
      ? MODAL_TEMPLATES.find((m) => m.id === guessModalTemplate(inf.url))?.label ?? 'Modal'
      : initialProvider === 'vast'
        ? 'Vast.ai'
        : initialProvider === 'runpod'
          ? 'RunPod'
          : t('cloud.control.tabManual')

  return (
    <Modal title={t('cloud.control.title')} onClose={onClose} wide>
      <div className="space-y-3 p-3">
        {/* Cosa sta usando Tabularium ORA, visibile qualunque pannello sia
            aperto: la domanda "cosa sto usando?" non deve richiedere di
            aprire ogni scheda per dedurlo (riprodotto: un deploy avviato ma
            non attivato è invisibile senza questa riga). */}
        <div className="flex flex-wrap items-center gap-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-panel)] p-2 text-[12px]">
          <span className="lbl !mb-0">{t('cloud.control.activeNowLabel')}</span>
          <Badge tone={inf.enabled ? 'ok' : 'neutral'}>
            {inf.enabled ? `${inf.model || '—'} · ${activeProviderLabel}` : t('cloud.control.activeNowDisabled')}
          </Badge>
        </div>

        {/* --- Vast.ai --- */}
        <Collapsible tab={t('cloud.control.tabVast')} defaultOpen={initialProvider === 'vast'} aux={
          tunnelState.running ? <Badge tone="ok">{t('cloud.control.tunnelActive', { pid: String(tunnelState.pid) })}</Badge> : undefined
        }>
          <div className="space-y-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.vastBody')}</p>

            {vastNotice && (
              <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px]">
                {vastNotice}
              </div>
            )}

            <div className="flex gap-2">
              <div className="flex-1">
                <Field label={t('cloud.control.apiKeyLabel')} hint={t('cloud.control.apiKeyHint')}>
                  <input
                    type="password"
                    value={vastApiKey}
                    onChange={(e) => setVastApiKey(e.target.value)}
                    placeholder={t('cloud.control.apiKeyPlaceholder')}
                    className="fld fld-mono"
                  />
                </Field>
              </div>
              <div className="flex items-end">
                <button type="button" onClick={() => void handleLoadVast()} disabled={vastBusy} className="btn btn-primary">
                  {vastBusy ? t('cloud.control.loading') : t('cloud.control.load')}
                </button>
              </div>
            </div>

            <Module tab={t('cloud.control.findOfferLabel')} quiet flush>
              <div className="grid gap-3 p-3 sm:grid-cols-3">
                <Field label={t('cloud.control.gpuFilterLabel')} hint={t('cloud.control.gpuFilterHint')}>
                  <input value={vastGpu} onChange={(e) => setVastGpu(e.target.value)} placeholder="RTX 4090" className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.maxPriceLabel')} hint={t('cloud.control.maxPriceHint')}>
                  <input type="number" min="0" step="0.01" value={vastMaxDph} onChange={(e) => setVastMaxDph(e.target.value)} placeholder="0.50" className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.diskLabel')} hint={t('cloud.control.diskHint')}>
                  <input type="number" min="10" value={vastDiskGb} onChange={(e) => setVastDiskGb(e.target.value)} className="fld fld-mono" />
                </Field>
                <label className="flex items-center gap-2 text-[12px] sm:col-span-2">
                  <input type="checkbox" checked={vastPrepare} onChange={(e) => setVastPrepare(e.target.checked)} />
                  {t('cloud.control.prepareServer')}
                </label>
                {vastPrepare && (
                  <div className="sm:col-span-2 grid gap-3 md:grid-cols-2">
                    <Field label={t('cloud.control.monkeyRefLabel')} hint={t('cloud.control.monkeyRefHint')}>
                      <input value={vastMonkeyRef} onChange={(e) => setVastMonkeyRef(e.target.value)} placeholder="commit SHA o tag verificato" className="fld fld-mono" />
                    </Field>
                    <Field label={t('cloud.control.tabulariumRefLabel')} hint={t('cloud.control.tabulariumRefHint')}>
                      <input value={vastTabulariumRef} onChange={(e) => setVastTabulariumRef(e.target.value)} placeholder="commit SHA o tag verificato" className="fld fld-mono" />
                    </Field>
                  </div>
                )}
                <div className="flex items-end">
                  <button type="button" onClick={() => void handleSearchVast()} disabled={vastBusy} className="btn btn-primary w-full">
                    {vastBusy ? t('cloud.control.loading') : t('cloud.control.findOffers')}
                  </button>
                </div>
              </div>
              {vastOffers.length > 0 && (
                <div className="divide-y divide-[color:var(--color-rule)] border-t border-[color:var(--color-rule)]">
                  {vastOffers.map((offer) => (
                    <div key={offer.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
                      <div>
                        <div className="font-bold">{offer.num_gpus}× {offer.gpu_name || 'GPU'} {offer.verified ? '· ✓' : ''}</div>
                        <div className="mono text-[11px] text-[color:var(--color-ink-3)]">
                          {offer.dph_total == null ? '—' : `$${offer.dph_total.toFixed(3)}/h`} · {offer.reliability == null ? '—' : `${(offer.reliability * 100).toFixed(1)}%`} · {offer.location || t('cloud.control.locationUnknown')}
                        </div>
                      </div>
                      <button type="button" onClick={() => void handleRentVast(offer)} disabled={vastBusy} className="btn btn-sm btn-primary">
                        {t('cloud.control.rent')}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </Module>

            {vastInstances.length > 0 &&
              renderInstanceList(
                vastInstances,
                vastBusy,
                handleConnectVast,
                t('cloud.control.connect'),
                (id) => void handleControlVast(id, 'start'),
                (id) => void handleControlVast(id, 'stop'),
                (id) => void handleControlVast(id, 'delete'),
              )}

            <div className="grid gap-3 border border-[color:var(--color-rule)] p-3 sm:grid-cols-2">
              <div className="sm:col-span-2 text-[12px] font-semibold">{t('cloud.control.tunnelTitle')}</div>
              <Field label={t('cloud.control.hostLabel')} hint={t('cloud.control.hostHint')}>
                <input value={sshHost} onChange={(e) => setSshHost(e.target.value)} placeholder="ssh5.vast.ai" disabled={tunnelState.running} className="fld fld-mono" />
              </Field>
              <Field label={t('cloud.control.portLabel')} hint={t('cloud.control.portHint')}>
                <input value={sshPort} onChange={(e) => setSshPort(e.target.value)} placeholder="38291" disabled={tunnelState.running} className="fld fld-mono" />
              </Field>
              <Field label={t('cloud.control.userLabel')} hint={t('cloud.control.userHint')}>
                <input value={sshUser} onChange={(e) => setSshUser(e.target.value)} placeholder="root" disabled={tunnelState.running} className="fld fld-mono" />
              </Field>
              <div className="flex items-end">
                {tunnelState.running ? (
                  <button type="button" onClick={() => void handleStopTunnel()} disabled={tunnelBusy} className="btn btn-danger w-full">
                    {tunnelBusy ? t('cloud.control.stopping') : t('cloud.control.stop')}
                  </button>
                ) : (
                  <button type="button" onClick={() => void handleStartTunnel()} disabled={tunnelBusy} className="btn btn-primary w-full">
                    {tunnelBusy ? t('cloud.control.starting') : t('cloud.control.start')}
                  </button>
                )}
              </div>
            </div>

            <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('cloud.control.vastUxNote')}</p>
          </div>
        </Collapsible>

        {/* --- RunPod --- */}
        <Collapsible tab={t('cloud.control.tabRunpod')} defaultOpen={initialProvider === 'runpod'}>
          <div className="space-y-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.runpodBody')}</p>

            {runpodNotice && (
              <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px]">
                {runpodNotice}
              </div>
            )}

            <div className="flex gap-2">
              <div className="flex-1">
                <Field label={t('cloud.control.runpodKeyLabel')} hint={t('cloud.control.runpodKeyHint')}>
                  <input
                    type="password"
                    value={runpodApiKey}
                    onChange={(e) => setRunpodApiKey(e.target.value)}
                    placeholder={t('cloud.control.apiKeyPlaceholder')}
                    className="fld fld-mono"
                  />
                </Field>
              </div>
              <div className="flex items-end">
                <button type="button" onClick={() => void handleLoadRunpod()} disabled={runpodBusy} className="btn btn-primary">
                  {runpodBusy ? t('cloud.control.loading') : t('cloud.control.load')}
                </button>
              </div>
            </div>

            {runpodPods.length > 0 &&
              renderInstanceList(
                runpodPods,
                runpodBusy,
                (pod) => void handleUseRunpodProxy(pod),
                t('cloud.control.runpodUseProxy'),
                (id) => void handleControlRunpod(id, 'start'),
                (id) => void handleControlRunpod(id, 'stop'),
                (id) => void handleControlRunpod(id, 'delete'),
              )}

            <details>
              <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
                {t('cloud.control.runpodSetupTitle')}
              </summary>
              <div className="mt-2 space-y-2 text-[12px] text-[color:var(--color-ink-2)]">
                <p>{t('cloud.control.runpodSetupBody')}</p>
                {commandBox('export MONKEYOCR_REF=COMMIT_O_TAG_MONKEYOCR_VERIFICATO; export TABULARIUM_REF=COMMIT_O_TAG_TABULARIUM_VERIFICATO; export TABULARIUM_SERVER_API_KEY=TOKEN_DEL_SERVER; curl -fsSL "https://raw.githubusercontent.com/nbadino/tabularium/${TABULARIUM_REF}/scripts/cloud/setup_cloud_vllm.sh" | bash -s -- --port 8888 --ref "$MONKEYOCR_REF"', 'runpod-setup')}
              </div>
            </details>
          </div>
        </Collapsible>

        {/* --- Modal serverless --- */}
        <Collapsible
          tab={t('cloud.control.tabModal')}
          // Si apre anche se la template guardata l'ultima volta (deploy in
          // corso o appena fatto) non è quella attiva per l'inferenza: non
          // deve sparire dalla vista a un refresh (v. commento su modalTemplate).
          defaultOpen={initialProvider === 'modal' || modalTemplate !== 'monkeyocrv2'}
          aux={<Badge tone={modalStatus?.token ? 'ok' : 'neutral'}>{modalStatus?.token ? t('cloud.control.modalTokenOk') : t('cloud.control.modalTokenMissing')}</Badge>}
        >
          <div className="space-y-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.modalBody')}</p>

            <div>
              <span className="lbl !mb-1">{t('cloud.control.modalModelLabel')}</span>
              <div className="flex flex-wrap gap-1.5">
                {MODAL_TEMPLATES.map((tpl) => (
                  <button
                    key={tpl.id}
                    type="button"
                    onClick={() => setModalTemplate(tpl.id)}
                    disabled={!!runningTask}
                    className={`btn btn-sm ${modalTemplate === tpl.id ? 'btn-primary' : ''}`}
                  >
                    {tpl.label}
                  </button>
                ))}
              </div>
            </div>

            {modalTemplate === 'paddleocr-vl' && (
              <p className="border border-[color:var(--color-warn-rule)] bg-[color:var(--color-warn-wash)] p-2 text-[12px] text-[color:var(--color-warn)]">
                {t('cloud.control.modalPaddleCaveat')}
              </p>
            )}
            {modalTemplate === 'monkeyocrv2' && (
              <p className="border border-[color:var(--color-ok)] bg-[color:var(--color-ok-wash)] p-2 text-[12px] text-[color:var(--color-ok)]">
                {t('cloud.control.modalMonkeyPerformance')}
              </p>
            )}
            {modalTemplate === 'mineru' && (
              <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[12px] text-[color:var(--color-ink-2)]">
                {t('cloud.control.modalMineruCaveat')}
              </p>
            )}
            {modalTemplate === 'unlimited-ocr' && (
              <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[12px] text-[color:var(--color-ink-2)]">
                {t('cloud.control.modalUnlimitedCaveat')}
              </p>
            )}

            <label className="flex cursor-pointer items-start gap-2 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[12px]">
              <input
                type="checkbox"
                checked={modalKeepWarm}
                onChange={(event) => {
                  const value = event.target.checked
                  setModalKeepWarm(value)
                  try {
                    localStorage.setItem(MODAL_KEEP_WARM_KEY, value ? '1' : '0')
                  } catch {
                    /* storage non disponibile: la scelta vale per la sessione */
                  }
                }}
              />
              <span>
                <span className="block font-semibold">{t('cloud.control.modalKeepWarm')}</span>
                <span className="block text-[color:var(--color-ink-3)]">{t('cloud.control.modalKeepWarmHint')}</span>
              </span>
            </label>

            {modalNotice && (
              <div className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px]">
                {modalNotice}
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {!modalStatus?.token && !runningTask && (
                <button type="button" onClick={() => void handleModalSetup()} disabled={modalBusy || !modalStatus?.cli} className="btn btn-primary">
                  {t('cloud.control.modalConnect')}
                </button>
              )}
              {modalStatus?.token && !runningTask && (
                <div>
                  <button type="button" onClick={() => void handleModalDeploy()} disabled={modalBusy} className="btn btn-primary">
                    {modalStatus.endpoint ? t('cloud.control.modalRedeploy') : t('cloud.control.modalDeploy')}
                  </button>
                  {!modalStatus.endpoint && (
                    <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('cloud.control.modalDeployHint')}</p>
                  )}
                </div>
              )}
              {modalStatus?.token && modalStatus?.endpoint && !runningTask && (
                <button type="button" onClick={() => void handleModalStop()} disabled={modalBusy} className="btn btn-sm btn-danger">
                  {t('cloud.control.modalStop')}
                </button>
              )}
              {runningTask && (
                <Badge tone="progress">
                  {runningTask.kind === 'setup' ? t('cloud.control.modalWaitingBrowser') : t('cloud.control.modalDeploying')}
                </Badge>
              )}
              {modalStatus?.task?.done && (
                <Badge tone={modalStatus.task.ok ? 'ok' : 'warn'}>
                  {modalStatus.task.ok ? t('cloud.control.modalTaskDone') : t('cloud.control.modalTaskFailed')}
                </Badge>
              )}
              {!modalStatus?.cli && <span className="text-[12px] text-[color:var(--color-warn)]">{t('cloud.control.modalCliMissing')}</span>}
            </div>

            {modalStatus?.endpoint && (
              <div className="space-y-2 border border-[color:var(--color-rule)] p-3">
                <span className="lbl">{t('cloud.control.modalEndpoint')}</span>
                <div className="mono overflow-x-auto text-[11px] text-[color:var(--color-ink)]">
                  {modalStatus.endpoint.replace(/\/$/, '') + '/v1'}
                </div>
                <button type="button" onClick={() => void handleUseModalEndpoint()} className="btn btn-primary btn-sm">
                  {t('cloud.control.modalUseEndpoint')}
                </button>
              </div>
            )}

            <Field label={t('cloud.control.modalKeyLabel')} hint={t('cloud.control.modalKeyHint')}>
              <input type="password" value={modalApiKey} onChange={(e) => setModalApiKey(e.target.value)} className="fld fld-mono" />
            </Field>

            {modalStatus?.task && modalStatus.task.log.length > 0 && (
              <Module tab={t('cloud.control.modalLog')} quiet flush>
                <pre className="mono max-h-48 overflow-auto p-2 text-[11px] leading-relaxed">
                  {modalStatus.task.log.join('\n')}
                </pre>
              </Module>
            )}

            <details>
              <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
                {t('cloud.control.modalSetupTitle')}
              </summary>
              <p className="mt-2 text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.modalSetupBody')}</p>
            </details>
          </div>
        </Collapsible>

        {/* --- Manuale --- */}
        <Collapsible tab={t('cloud.control.tabManual')} defaultOpen={initialProvider === 'manual'}>
          <div className="space-y-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.manualBody')}</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Field label={t('cloud.control.urlLabel')} hint={t('cloud.control.urlHint')}>
                  <input
                    value={manualUrl}
                    onChange={(e) => setManualUrl(e.target.value)}
                    placeholder="https://<POD_ID>-8888.proxy.runpod.net/v1"
                    className="fld fld-mono"
                  />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label={t('cloud.card.modelLabel')} hint={t('cloud.card.modelHint')}>
                  <input
                    value={manualModel}
                    onChange={(e) => setManualModel(e.target.value)}
                    placeholder="Unlimited-OCR"
                    className="fld fld-mono"
                  />
                </Field>
              </div>
              <div className="sm:col-span-2">
                <Field label={t('cloud.control.directKeyLabel')}>
                  <input
                    type="password"
                    value={manualKey}
                    onChange={(e) => setManualKey(e.target.value)}
                    placeholder={t('cloud.control.directKeyPlaceholder')}
                    className="fld fld-mono"
                  />
                </Field>
              </div>
              <div className="flex justify-end sm:col-span-2">
                <button type="button" onClick={() => void handleSaveManual()} className="btn btn-primary">
                  {t('cloud.control.saveApply')}
                </button>
              </div>
            </div>
          </div>
        </Collapsible>
      </div>
    </Modal>
  )
}
