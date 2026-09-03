import { useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { IconCopy } from './icons'
import { Badge, Collapsible, Field, Modal, Module, Notice } from './ui'
import { saveInferenceToBackend, testInferenceConnection, useInference } from './inference'

interface CloudControlModalProps {
  open: boolean
  onClose: () => void
  /** Scheda da aprire quando l'apertura nasce da una scelta fatta fuori
   *  (es. «Deploya su Vast» dalla libreria modelli): vince sul guess
   *  dedotto dall'URL attivo. */
  focusProvider?: Provider | null
  /** Adapter scelto nella libreria: preseleziona il modello da deployare
   *  nella scheda del provider indicato. */
  focusAdapterId?: string | null
  /** Nome mostrato del modello scelto: serve a dichiarare l'intento nella
   *  striscia di deploy guidato (l'adapter da solo non parla all'utente). */
  focusModelLabel?: string | null
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

/** Preflight dell'account Vast.ai: valida la chiave e mostra il credito. */
interface VastAccount {
  id: number | null
  email: string | null
  balance: number
  balance_ok: boolean
}

/** Chiave SSH dedicata al cloud: il backend non espone mai la parte privata. */
interface VastSshKey {
  exists: boolean
  fingerprint: string
  key_type: string
  public_key: string
  key_path: string
}

interface VastOffer {
  id: number
  gpu_name: string | null
  num_gpus: number
  /** VRAM per GPU, in MB come la espone il provider. */
  gpu_ram: number | null
  dph_total: number | null
  reliability: number | null
  location: string | null
  verified?: boolean | null
  disk_space?: number | null
  inet_down?: number | null
  cuda_max_good?: number | null
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

/** Nome del credential Vast nel vault del backend: la chiave non torna mai al browser. */
const VAST_SECRET = 'vast_api_key'

/** Modello servibile su GPU a noleggio, con la ricetta ufficiale che lo governa
 *  (`backend/app/services/serve_recipes.py`): framework, versione di vLLM e
 *  flag che determinano la precisione. `supported: false` = richiede
 *  un'immagine Docker dedicata, quindi passa dalle template Modal. */
interface VastModel {
  adapter_id: string
  hf_repo: string
  served_model_name: string
  runtime: string
  supported: boolean
  /** L'architettura vive in un'immagine dedicata: l'istanza va noleggiata con
   *  quella, il che rende la scelta del modello un passo *prima* del noleggio. */
  needs_own_image: boolean
  docker_image: string
}

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

/** Inverso di `MODAL_TEMPLATE_TARGET`: dalla scelta fatta nella libreria
 *  modelli (adapter) alla template Modal che lo serve. */
const TEMPLATE_BY_ADAPTER: Record<string, string> = Object.fromEntries(
  Object.entries(MODAL_TEMPLATE_TARGET).map(([template, target]) => [target.adapterId, template]),
)

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
  { id: 'dots-ocr', label: 'dots.mocr' },
  { id: 'glm-ocr', label: 'GLM-OCR' },
  { id: 'deepseek-ocr', label: 'DeepSeek-OCR-2' },
  { id: 'qwen3-vl', label: 'Qwen3-VL-8B' },
]

function copyToClipboard(text: string, onDone: () => void) {
  navigator.clipboard?.writeText(text).then(onDone).catch(() => {})
}

export function CloudControlModal({ open, onClose, focusProvider, focusAdapterId, focusModelLabel }: CloudControlModalProps) {
  const { t } = useI18n()
  const inf = useInference()
  // La destinazione esplicita vince sempre: il guess dall'URL vale solo come
  // ripiego quando l'apertura non nasce da una scelta di modello.
  const initialProvider = focusProvider ?? guessProvider(inf.url)
  // Deploy guidato: l'apertura nasce da «Deploya su <provider>» e deve
  // dichiararsi, o il pannello è indistinguibile da un'apertura normale.
  const guided = Boolean(open && focusAdapterId && focusProvider && focusProvider !== 'manual')
  const FOCUS_PROVIDER_LABEL: Record<string, string> = {
    vast: 'Vast.ai',
    runpod: 'RunPod',
    modal: 'Modal',
  }

  // --- Vast.ai + tunnel SSH ---
  const [vastApiKey, setVastApiKey] = useState('')
  const [vastInstances, setVastInstances] = useState<RentedInstance[]>([])
  const [vastBusy, setVastBusy] = useState(false)
  const [vastNotice, setVastNotice] = useState<string | null>(null)
  const [vastOffers, setVastOffers] = useState<VastOffer[]>([])
  const [vastGpu, setVastGpu] = useState('')
  const [vastMaxDph, setVastMaxDph] = useState('')
  const [vastDiskGb, setVastDiskGb] = useState('80')
  const [vastVram, setVastVram] = useState('24')
  const [vastNet, setVastNet] = useState('')
  // 12.9 è il minimo per compilare i kernel delle GPU sm_120 (Blackwell).
  const [vastCuda, setVastCuda] = useState('12.9')
  const [vastVerified, setVastVerified] = useState(true)
  const [vastAccount, setVastAccount] = useState<VastAccount | null>(null)
  const [vastSshKey, setVastSshKey] = useState<VastSshKey | null>(null)
  const [vastWaitingId, setVastWaitingId] = useState<number | string | null>(null)
  const [vastProvisionTarget, setVastProvisionTarget] = useState<{ host: string; port: number } | null>(null)
  const [vastProvisionLog, setVastProvisionLog] = useState<string[]>([])
  const [vastPhase, setVastPhase] = useState<string>('')
  const [inferenceOk, setInferenceOk] = useState(false)
  const [vastKeySaved, setVastKeySaved] = useState(false)
  // "Non ancora interrogato" non è "nessuna istanza": senza distinguerli la UI
  // afferma cose sull'account che non ha verificato.
  const [vastLoaded, setVastLoaded] = useState(false)
  const [vastRunnerOpen, setVastRunnerOpen] = useState(false)
  const [vastModels, setVastModels] = useState<VastModel[]>([])
  const [vastAdapter, setVastAdapter] = useState('monkeyocrv2-parsing')
  const [vastModelCustom, setVastModelCustom] = useState('')
  const [vastServedName, setVastServedName] = useState('MonkeyOCRv2')
  const [vastMonkeyRef, setVastMonkeyRef] = useState('')
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
    void refreshVastSshKey()
    void refreshVastKeyStatus()
    void refreshVastModels()
  }, [open])

  // Apertura «guidata» dalla libreria modelli: il modello è già stato scelto,
  // qui si atterra sulla scheda giusta con quel modello preselezionato.
  useEffect(() => {
    if (!open || !focusAdapterId) return
    if (focusProvider === 'vast') setVastAdapter(focusAdapterId)
    if (focusProvider === 'modal') {
      const template = TEMPLATE_BY_ADAPTER[focusAdapterId]
      if (template) setModalTemplate(template)
    }
  }, [open, focusProvider, focusAdapterId])

  // L'adapter preselezionato deve esistere nella ricetta del provider: se la
  // lista caricata non lo conosce, torna il default invece di lasciare un
  // <select> senza voce selezionata.
  useEffect(() => {
    if (vastModels.length === 0) return
    if (!vastModels.some((item) => item.adapter_id === vastAdapter)) {
      setVastAdapter('monkeyocrv2-parsing')
    }
  }, [vastModels, vastAdapter])

  // All'apertura la lista istanze va caricata subito: senza, resta
  // "Caricamento…" finché l'utente non preme il bottone a mano. Attende la
  // credenziale (chiave salvata nel vault o digitata): prima sarebbe nulla.
  const vastCredentialReady = Boolean(vastApiKey.trim()) || vastKeySaved
  useEffect(() => {
    if (!open || !vastCredentialReady) return
    void handleLoadVast(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, vastCredentialReady])

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

  // Dopo il noleggio l'istanza impiega minuti ad accendersi: si interroga
  // finché Vast.ai non pubblica host e porta SSH (`ssh_ready`).
  useEffect(() => {
    if (!open || vastWaitingId === null) return
    let stop = false
    const tick = async () => {
      try {
        const credential = vastCredential()
        if (!credential) return
        const inst = await apiPost<RentedInstance & { ssh_ready: boolean }>('/system/cloud/vast/instance', {
          ...credential,
          instance_id: vastWaitingId,
        })
        if (stop) return
        setVastInstances((prev) =>
          prev.some((item) => String(item.id) === String(inst.id))
            ? prev.map((item) => (String(item.id) === String(inst.id) ? { ...item, ...inst } : item))
            : [...prev, inst],
        )
        if (inst.ssh_ready) {
          setVastWaitingId(null)
          setVastNotice(
            t('cloud.control.instanceReady', {
              id: String(inst.id),
              host: String(inst.ssh_host),
              port: String(inst.ssh_port),
            }),
          )
        } else {
          setVastNotice(t('cloud.control.waitingInstance', { id: String(inst.id), status: inst.status }))
        }
      } catch {
        /* transitorio: si resta sull'ultimo stato noto e si riprova */
      }
    }
    void tick()
    const id = setInterval(() => void tick(), 8000)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [open, vastWaitingId, vastApiKey, vastKeySaved])

  // Il setup remoto dura minuti: si segue il log finché vLLM non è in ascolto,
  // poi il tunnel parte da solo. Nessun comando da copiare a mano.
  useEffect(() => {
    if (!open || !vastProvisionTarget) return
    let stop = false
    let consecutiveFailures = 0
    const tick = async () => {
      try {
        const res = await apiPost<{
          lines: string[]
          ready: boolean
          phase: string
          failed: boolean
          error: string
          present: boolean
        }>('/system/cloud/vast/provision/log', {
          host: vastProvisionTarget.host,
          port: vastProvisionTarget.port,
          lines: 60,
        })
        if (stop) return
        consecutiveFailures = 0
        setVastProvisionLog(res.lines)
        setVastPhase(res.phase)
        if (!res.present) {
          // Un'istanza running non implica che Tabularium abbia già avviato il
          // provisioning. Senza questo reset il target restava appeso a
          // «non preparato» e il pulsante diventava grigio per sempre.
          setVastProvisionTarget(null)
          setVastPhase('absent')
          return
        }
        if (res.failed) {
          setVastPhase('failed')
          // Lo script è morto: continuare a interrogarlo non cambia nulla e
          // nasconderebbe l'errore dietro una barra che gira per sempre.
          setVastProvisionTarget(null)
          setVastNotice(t('cloud.control.provisionFailed', { error: res.error }))
          return
        }
        if (res.ready) {
          setVastProvisionTarget(null)
          setVastNotice(t('cloud.control.provisionReady'))
          await handleStartTunnel(vastProvisionTarget.host, vastProvisionTarget.port)
        }
      } catch (e) {
        // Un singolo buco SSH durante l'avvio è normale. Tre di fila meritano
        // invece di essere visibili: il polling continua, quindi «Riprova» è
        // implicito e un ritorno della macchina recupera senza altro click.
        consecutiveFailures += 1
        if (!stop && consecutiveFailures >= 3) {
          setVastNotice(t('cloud.control.provisionStatusError', { error: String(e) }))
        }
      }
    }
    void tick()
    const id = setInterval(() => void tick(), 10000)
    return () => {
      stop = true
      clearInterval(id)
    }
  }, [open, vastProvisionTarget])

  // Il riavvio del modale non deduce più una preparazione dal solo stato
  // `running`: un'istanza accesa può essere semplicemente pronta per il primo
  // click. Un provisioning già iniziato resta seguito dal target impostato
  // quando l'utente ha premuto il pulsante.

  // Un'istanza appena noleggiata passa per `loading`/`unknown` prima di essere
  // `running`: la lista si aggiorna da sola finché qualcuna non è pronta,
  // altrimenti l'utente resta a premere "Carica istanze".
  const vastStarting = vastInstances.some((inst) => !inst.is_running)
  useEffect(() => {
    if (!open || !vastStarting) return
    const id = setInterval(() => void handleLoadVast(true), 10000)
    return () => clearInterval(id)
  }, [open, vastStarting])

  const pollTunnelStatus = async () => {
    try {
      const res = await apiGet<TunnelState>('/system/cloud/tunnel')
      setTunnelState(res)
      if (res.host) setSshHost(res.host)
      if (res.port) setSshPort(String(res.port))
      if (res.running) {
        const url = `http://127.0.0.1:${res.local_port || 8888}/v1`
        const probe = await testInferenceConnection({ url, model: vastServedName })
        setInferenceOk(Boolean(probe.ok))
      } else {
        setInferenceOk(false)
      }
    } catch {
      /* ignore */
    }
  }

  // Il tunnel può aprirsi qualche secondo prima che /v1/models risponda. Non
  // rendiamo attiva una configurazione non verificata e non costringiamo
  // l'utente a ricliccare: il probe prosegue finché l'endpoint è realmente
  // utilizzabile, poi salva in un'unica volta la destinazione valida.
  useEffect(() => {
    if (!open || !tunnelState.running || inferenceOk) return
    let stopped = false
    const localUrl = `http://127.0.0.1:${tunnelState.local_port || 8888}/v1`
    const tick = async () => {
      try {
        const probe = await testInferenceConnection({ url: localUrl, model: vastServedName })
        if (stopped || !probe.ok) return
        await saveInferenceToBackend({ enabled: true, url: localUrl, model: vastServedName })
        if (!stopped) {
          setInferenceOk(true)
          setVastNotice(t('cloud.control.endpointReady', { url: localUrl }))
        }
      } catch {
        /* il tunnel resta aperto e il giro successivo riprova */
      }
    }
    void tick()
    const id = setInterval(() => void tick(), 5000)
    return () => {
      stopped = true
      clearInterval(id)
    }
  }, [open, tunnelState.running, tunnelState.local_port, inferenceOk, vastServedName])

  // --- Tunnel SSH ---
  // Gli override servono a "Connetti": lo stato React non è ancora aggiornato
  // quando la connessione parte subito dopo aver scelto l'istanza.
  const handleStartTunnel = async (hostOverride?: string, portOverride?: number) => {
    const host = (hostOverride ?? sshHost).trim()
    const port = portOverride ?? parseInt(sshPort.trim(), 10)
    if (!host || !Number.isFinite(port)) {
      setVastNotice(t('cloud.control.missingHostPort'))
      return
    }
    setTunnelBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<TunnelState>('/system/cloud/tunnel/start', {
        host,
        port,
        user: sshUser.trim() || 'root',
        // Zero = una porta locale libera scelta dal backend. La porta 8888 è
        // spesso già occupata dall'inferenza locale: non è un errore cloud.
        local_port: 0,
        remote_port: 8888,
      })
      setTunnelState(res)
      const localUrl = `http://127.0.0.1:${res.local_port || 8888}/v1`
      setVastNotice(t('cloud.control.endpointVerifying', { url: localUrl }))
      // Il probe e il salvataggio passano dall'effetto di readiness qui sopra:
      // una sola writer evita doppie attivazioni quando React rende subito
      // dopo setTunnelState.
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
  /** Chiave appena incollata, altrimenti quella già cifrata nel vault. */
  const vastCredential = (): Record<string, string> | null => {
    if (vastApiKey.trim()) return { api_key: vastApiKey.trim() }
    if (vastKeySaved) return { credential_ref: `vault:${VAST_SECRET}` }
    return null
  }

  const refreshVastKeyStatus = async () => {
    try {
      const res = await apiGet<{ configured: boolean }>(`/system/secrets/${VAST_SECRET}`)
      setVastKeySaved(res.configured)
      if (!res.configured) return
      // Chiave già nel vault: il pannello mostra lo stato reale senza che
      // l'utente debba rieseguire la prima configurazione.
      const ref = `vault:${VAST_SECRET}`
      try {
        setVastAccount(await apiPost<VastAccount>('/system/cloud/vast/account', { credential_ref: ref }))
      } catch {
        /* account non raggiungibile ora: il badge resta assente */
      }
      await resolveMonkeyRef()
      // Con la chiave nel vault non c'è ragione di aspettare un click per
      // sapere che istanze esistono: la catena deve nascere già vera.
      await handleLoadVast(true)
    } catch {
      /* vault non configurato: la chiave resta da incollare a ogni sessione */
    }
  }

  const handleForgetVastKey = async () => {
    try {
      await apiDelete(`/system/secrets/${VAST_SECRET}`)
      setVastKeySaved(false)
      setVastApiKey('')
      setVastNotice(t('cloud.control.keyForgotten'))
    } catch (e) {
      setVastNotice(t('cloud.control.loadError', { error: String(e) }))
    }
  }

  const refreshVastModels = async () => {
    try {
      const res = await apiGet<{ items: VastModel[] }>('/system/cloud/vast/models')
      setVastModels(res.items)
    } catch {
      /* la lista resta vuota: il provisioning userà comunque il default */
    }
  }

  const refreshVastSshKey = async () => {
    try {
      setVastSshKey(await apiGet<VastSshKey>('/system/cloud/vast/ssh-key'))
    } catch {
      /* la chiave resta "non generata": il wizard la crea al primo click */
    }
  }

  /** Pin del runner ufficiale: risolto una volta, poi resta esplicito nella recipe. */
  const resolveMonkeyRef = async () => {
    if (vastMonkeyRef.trim()) return vastMonkeyRef.trim()
    try {
      const res = await apiGet<{ ref: string }>('/system/cloud/vast/monkeyocr-ref')
      setVastMonkeyRef(res.ref)
      return res.ref
    } catch (e) {
      setVastNotice(t('cloud.control.refResolveError', { error: String(e) }))
      return ''
    }
  }

  /** Pin della host key: idempotente, richiesto prima di ogni uso di SSH. */
  const pinHostKey = async (inst: RentedInstance) => {
    const pin = await apiPost<{ host: string; port: number; key_types: string[] }>(
      '/system/cloud/vast/hostkey',
      { host: inst.ssh_host, port: inst.ssh_port },
    )
    setVastNotice(
      t('cloud.control.hostKeyPinned', {
        host: pin.host,
        port: String(pin.port),
        types: pin.key_types.join(', '),
      }),
    )
  }

  /** Prepara la GPU: script consegnato via SSH dal checkout locale. */
  const handleProvisionVast = async (inst: RentedInstance) => {
    if (!inst.ssh_host || !inst.ssh_port) {
      setVastNotice(t('cloud.control.noSsh'))
      return
    }
    setVastBusy(true)
    try {
      const credential = vastCredential()
      if (!credential) {
        setVastNotice(t('cloud.control.missingKey'))
        return
      }
      const ref = await resolveMonkeyRef()
      if (!ref) return
      setVastNotice(t('cloud.control.attachingKey'))
      // Le chiavi registrate sull'account vengono ereditate dalle nuove
      // istanze, non necessariamente da quelle già esistenti. Allegarla qui
      // rende davvero autosufficiente «Prepara e connetti» in entrambi i casi.
      await apiPost('/system/cloud/vast/ssh-key', { ...credential, instance_id: inst.id })
      await refreshVastSshKey()
      await pinHostKey(inst)
      const res = await apiPost<{ served_model_name: string; already_ready?: boolean }>('/system/cloud/vast/provision', {
        host: inst.ssh_host,
        port: inst.ssh_port,
        monkeyocr_ref: ref,
        adapter_id: vastAdapter,
        // Vuoto = il checkpoint ufficiale della ricetta; valorizzato = un tuo
        // fine-tuned con la stessa architettura.
        model: vastModelCustom.trim(),
        remote_port: 8888,
      })
      setVastServedName(res.served_model_name)
      setSshHost(inst.ssh_host)
      setSshPort(String(inst.ssh_port))
      setVastProvisionLog([])
      setVastProvisionTarget({ host: inst.ssh_host, port: inst.ssh_port })
      if (res.already_ready) {
        // Il server remoto è già pronto: non serve aspettare il log di setup;
        // apriamo subito il solo tunnel e poi il probe mostrerà la conferma.
        setVastNotice(t('cloud.control.serverAlreadyReady'))
        await handleStartTunnel(inst.ssh_host, inst.ssh_port)
      } else {
        setVastNotice(t('cloud.control.provisionStarted'))
      }
    } catch (e) {
      setVastNotice(t('cloud.control.provisionError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  /** Prima configurazione: preflight account + chiave SSH registrata sull'account. */
  const handleVastSetup = async () => {
    const credential = vastCredential()
    if (!credential) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    setVastBusy(true)
    setVastNotice(null)
    try {
      const account = await apiPost<VastAccount>('/system/cloud/vast/account', credential)
      setVastAccount(account)
      const key = await apiPost<{ already_registered: boolean }>('/system/cloud/vast/ssh-key', credential)
      // Chiave validata: si conserva cifrata lato server, così non va
      // reincollata a ogni sessione. Il campo si svuota subito dopo.
      if (vastApiKey.trim()) {
        try {
          await apiPost('/system/secrets', { name: VAST_SECRET, value: vastApiKey.trim() })
          setVastKeySaved(true)
          setVastApiKey('')
        } catch {
          /* vault non disponibile: si continua con la chiave in memoria */
        }
      }
      await refreshVastSshKey()
      setVastNotice(
        key.already_registered ? t('cloud.control.setupKeyAlready') : t('cloud.control.setupKeyRegistered'),
      )
      await resolveMonkeyRef()
      await handleLoadVast()
    } catch (e) {
      setVastNotice(t('cloud.control.setupError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleLoadVast = async (quiet = false) => {
    const credential = vastCredential()
    if (!credential) {
      if (!quiet) setVastNotice(t('cloud.control.missingKey'))
      return
    }
    if (!quiet) {
      setVastBusy(true)
      setVastNotice(null)
    }
    try {
      const res = await apiPost<{ items: RentedInstance[] }>('/system/cloud/vast/instances', credential)
      setVastInstances(res.items)
      setVastLoaded(true)
      if (!quiet) {
        setVastNotice(
          res.items.length === 0
            ? t('cloud.control.noneFound')
            : t('cloud.control.found', { count: res.items.length }),
        )
      }
    } catch (e) {
      if (!quiet) setVastNotice(t('cloud.control.loadError', { error: String(e) }))
    } finally {
      if (!quiet) setVastBusy(false)
    }
  }

  const handleSearchVast = async () => {
    const credential = vastCredential()
    if (!credential) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    setVastBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<{ items: VastOffer[] }>('/system/cloud/vast/offers', {
        ...credential, gpu_name: vastGpu.trim(), max_dph: vastMaxDph ? Number(vastMaxDph) : null,
        disk_gb: Number(vastDiskGb) || 40,
        min_gpu_ram_gb: vastVram ? Number(vastVram) : null,
        min_inet_down: vastNet ? Number(vastNet) : null,
        min_cuda: vastCuda ? Number(vastCuda) : null,
        verified_only: vastVerified,
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
    const credential = vastCredential()
    if (!credential) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    const price = offer.dph_total == null ? t('cloud.control.priceUnknown') : `$${offer.dph_total.toFixed(3)}/h`
    if (!window.confirm(t('cloud.control.rentConfirm', { gpu: `${offer.num_gpus}× ${offer.gpu_name || 'GPU'}`, price }))) return
    setVastBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<{ contract_id: number | null }>('/system/cloud/vast/rent', {
        ...credential, offer_id: offer.id, disk_gb: Number(vastDiskGb) || 40,
        // L'immagine del container si fissa al noleggio: il modello scelto la
        // determina quando ne pretende una propria.
        adapter_id: vastAdapter,
        dph_total: offer.dph_total,
        // Il CUDA massimo dell'host è noto dall'offerta: il backend sceglie
        // l'immagine giusta prima del noleggio, invece di rimediare dopo.
        cuda_max_good: offer.cuda_max_good ?? null,
        // L'istanza nasce nuda: la prepariamo via SSH subito dopo, con lo
        // script di questo checkout (niente hook onstart da GitHub).
        prepare_server: false, port: 8888,
      })
      setVastOffers([])
      setVastNotice(t('cloud.control.rentStarted'))
      if (res.contract_id != null) setVastWaitingId(res.contract_id)
      await handleLoadVast()
    } catch (e) {
      setVastNotice(t('cloud.control.rentError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleControlVast = async (instanceId: number | string, action: 'start' | 'stop' | 'delete') => {
    if (action === 'delete' && !window.confirm(t('cloud.control.deleteResourceConfirm', { id: String(instanceId) }))) return
    const credential = vastCredential()
    if (!credential) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    setVastBusy(true)
    try {
      await apiPost('/system/cloud/vast/control', { ...credential, instance_id: instanceId, action })
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

  const handleConnectVast = async (inst: RentedInstance) => {
    if (!inst.ssh_host || !inst.ssh_port) {
      setVastNotice(t('cloud.control.noSsh'))
      return
    }
    setSshHost(inst.ssh_host)
    setSshPort(String(inst.ssh_port))
    // La host key va fissata prima del tunnel: il backend usa
    // StrictHostKeyChecking=yes e senza pinning la connessione fallirebbe.
    setTunnelBusy(true)
    try {
      await pinHostKey(inst)
    } catch (e) {
      setVastNotice(t('cloud.control.hostKeyError', { error: String(e) }))
      return
    } finally {
      setTunnelBusy(false)
    }
    await handleStartTunnel(inst.ssh_host, inst.ssh_port)
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

  /** Vocabolario dei provider tradotto: `unknown` non dice nulla all'utente. */
  const statusLabel = (status: string) => {
    const key: string | undefined = {
      running: 'statusRunning',
      loading: 'statusLoading',
      created: 'statusProvisioning',
      stopped: 'statusStopped',
      paused: 'statusStopped',
      exited: 'statusExited',
      terminated: 'statusExited',
      frozen: 'statusFrozen',
      rebooting: 'statusRebooting',
      unknown: 'statusNoContact',
      offline: 'statusNoContact',
    }[String(status || '').toLowerCase()]
    return key ? t(`cloud.control.${key}`) : status
  }

  const vastLive = vastInstances.find((inst) => inst.is_running) ?? vastInstances[0]
  const phaseWord: Record<string, string> = {
    absent: 'phaseAbsent',
    starting: 'phaseStarting',
    system: 'phaseSystem',
    clone: 'phaseClone',
    python: 'phasePython',
    weights: 'phaseWeights',
    serving: 'phaseServing',
    ready: 'phaseReady',
    failed: 'phaseFailed',
  }
  const chainSteps: { key: string; label: string; value: string; tone: 'ok' | 'warn' | 'neutral' }[] = [
    {
      key: 'instance',
      label: t('cloud.control.chainInstance'),
      value: vastLive
        ? statusLabel(vastLive.status)
        : vastLoaded
          ? t('cloud.control.chainNone')
          : t('cloud.control.chainUnknown'),
      tone: vastLive?.is_running ? 'ok' : 'neutral',
    },
    {
      key: 'server',
      label: t('cloud.control.chainServer'),
      value: vastPhase
        ? t(`cloud.control.${phaseWord[vastPhase] ?? 'phaseStarting'}`)
        : t('cloud.control.phaseAbsent'),
      tone: vastPhase === 'ready' ? 'ok' : vastPhase === 'failed' ? 'warn' : 'neutral',
    },
    {
      key: 'tunnel',
      label: t('cloud.control.chainTunnel'),
      value: tunnelState.running ? t('cloud.control.tunnelOpen') : t('cloud.control.tunnelClosed'),
      tone: tunnelState.running ? 'ok' : 'neutral',
    },
    {
      key: 'inference',
      label: t('cloud.control.chainInference'),
      value: inferenceOk ? t('cloud.control.inferenceOn') : t('cloud.control.inferenceOff'),
      tone: inferenceOk ? 'ok' : 'neutral',
    },
  ]

  const renderInstanceList = (
    items: RentedInstance[],
    busy: boolean,
    onConnect: (inst: RentedInstance) => void,
    connectLabel: string,
    onStart: (id: number | string) => void,
    onStop: (id: number | string) => void,
    onDelete: (id: number | string) => void,
    onProvision?: (inst: RentedInstance) => void,
    onReload?: () => void,
    loaded = true,
  ) => (
    <Module
      tab={t('cloud.control.instancesLabel')}
      quiet
      flush
      aux={
        onReload && (
          <button type="button" onClick={onReload} disabled={busy} className="btn btn-sm">
            {busy ? t('cloud.control.loading') : t('cloud.control.load')}
          </button>
        )
      }
    >
      {items.length === 0 && (
        <p className="p-3 text-[12px] text-[color:var(--color-ink-2)]">
          {loaded ? t('cloud.control.noneFound') : t('cloud.control.loading')}
        </p>
      )}
      <div className="divide-y divide-[color:var(--color-rule)]">
        {items.map((inst) => {
          const isPreparing = Boolean(
            onProvision && vastProvisionTarget?.host === inst.ssh_host && vastProvisionTarget?.port === inst.ssh_port,
          )
          const isConnected = Boolean(
            onProvision && inferenceOk && tunnelState.running && tunnelState.host === inst.ssh_host,
          )
          return (
          <div key={inst.id} className="flex flex-wrap items-center justify-between gap-3 p-3">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-bold">{inst.label}</span>
                <Badge tone={statusTone(inst.is_running, inst.status)}>{statusLabel(inst.status)}</Badge>
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
              {!inst.is_running && onProvision && (
                <div className="mt-0.5 text-[11px] text-[color:var(--color-ink-2)]">
                  {t('cloud.control.instanceStarting', { action: t('cloud.control.provisionRun') })}
                </div>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {inst.is_running ? (
                <>
                  {isConnected ? (
                    <Badge tone="ok">{t('cloud.control.instanceInUse')}</Badge>
                  ) : onProvision ? (
                    <button
                      type="button"
                      onClick={() => onProvision(inst)}
                      disabled={busy || tunnelBusy || isPreparing || !inst.ssh_host || !inst.ssh_port}
                      className="btn btn-sm btn-primary"
                    >
                      {isPreparing
                        ? t(`cloud.control.${phaseWord[vastPhase] ?? 'phaseStarting'}`)
                        : t('cloud.control.provisionRun')}
                    </button>
                  ) : (
                    <button type="button" onClick={() => onConnect(inst)} disabled={busy} className="btn btn-sm">
                      {connectLabel}
                    </button>
                  )}
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
          )
        })}
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
        {/* Deploy guidato: primo, perché è l'intento con cui la finestra è
            stata aperta; «In uso ora» è il contesto, questo è il compito. */}
        {guided && (
          <div className="border border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] p-2 text-[12px]">
            <div className="flex flex-wrap items-center gap-2">
              <b className="font-semibold">
                {t('cloud.control.deployGuidedTitle', {
                  model: focusModelLabel || focusAdapterId || '',
                  provider: FOCUS_PROVIDER_LABEL[focusProvider ?? ''] ?? '',
                })}
              </b>
              <Badge tone="ok">{focusModelLabel || focusAdapterId}</Badge>
            </div>
            <p className="mt-0.5 text-[11px] text-[color:var(--color-ink-2)]">{t('cloud.control.deployGuidedHint')}</p>
          </div>
        )}
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
            {vastNotice && <Notice tone={inferenceOk ? 'ok' : tunnelState.running ? 'progress' : 'warn'}>{vastNotice}</Notice>}
            <div className={`flex items-center gap-2 border px-2 py-1.5 text-[12px] ${inferenceOk ? 'border-[color:var(--color-ok)] bg-[color:var(--color-ok-wash)]' : tunnelState.running ? 'border-[color:var(--color-warn)] bg-[color:var(--color-fill)]' : 'border-[color:var(--color-rule)] bg-[color:var(--color-fill)]'}`} role="status" aria-live="polite">
              <span className={`h-2 w-2 rounded-full ${inferenceOk ? 'bg-[color:var(--color-ok)]' : tunnelState.running ? 'bg-[color:var(--color-warn)]' : 'bg-[color:var(--color-ink-3)]'}`} />
              <span>{inferenceOk ? t('cloud.control.connectionConfirmed', { url: `http://127.0.0.1:${tunnelState.local_port || 8888}/v1` }) : tunnelState.running ? t('cloud.control.connectionChecking') : t('cloud.control.connectionNotActive')}</span>
            </div>

            <Module tab={t('cloud.control.chainLabel')} quiet flush>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2 p-3">
                {chainSteps.map((step) => (
                  <div key={step.key} className="flex items-center gap-2">
                    <span className="lbl !mb-0">{step.label}</span>
                    <Badge tone={step.tone}>{step.value}</Badge>
                  </div>
                ))}
              </div>
            </Module>

            <div className="flex gap-2">
              <div className="flex-1">
                <Field
                  label={t('cloud.control.apiKeyLabel')}
                  hint={vastKeySaved ? t('cloud.control.keySavedHint') : t('cloud.control.apiKeyHint')}
                >
                  <input
                    type="password"
                    value={vastApiKey}
                    onChange={(e) => setVastApiKey(e.target.value)}
                    placeholder={t('cloud.control.apiKeyPlaceholder')}
                    className="fld fld-mono"
                  />
                </Field>
                {vastKeySaved && (
                  <div className="mt-1 flex items-center gap-2">
                    <Badge tone="ok">{t('cloud.control.keySaved')}</Badge>
                    <button type="button" className="btn btn-sm" onClick={() => void handleForgetVastKey()}>
                      {t('cloud.control.forgetKey')}
                    </button>
                  </div>
                )}
              </div>
              <div className="flex items-end">
                <button type="button" onClick={() => void handleVastSetup()} disabled={vastBusy} className="btn btn-primary">
                  {vastBusy ? t('cloud.control.setupRunning') : t('cloud.control.setupRun')}
                </button>
              </div>
            </div>

            <Module
              tab={t('cloud.control.setupTitle')}
              quiet
              flush
              aux={
                guided && focusProvider === 'vast' && focusAdapterId ? (
                  <Badge tone="ok">{focusModelLabel || focusAdapterId}</Badge>
                ) : undefined
              }
            >
              <div className="space-y-2 p-3">
                {/* L'istruzione serve solo finché manca qualcosa: dopo, il
                    pannello è lo stato dell'accesso, non un promemoria. */}
                {!(vastAccount && vastSshKey?.exists) && (
                  <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.setupBody')}</p>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  {vastAccount && (
                    <Badge tone={vastAccount.balance_ok ? 'ok' : 'warn'}>
                      {t('cloud.control.setupAccountOk', {
                        email: vastAccount.email || '—',
                        balance: vastAccount.balance.toFixed(2),
                      })}
                    </Badge>
                  )}
                  <Badge tone={vastSshKey?.exists ? 'ok' : 'neutral'}>
                    {vastSshKey?.exists
                      ? t('cloud.control.setupKeyReady', {
                          fingerprint: `${vastSshKey.fingerprint.slice(0, 18)}…`,
                        })
                      : t('cloud.control.setupKeyMissing')}
                  </Badge>
                  {vastMonkeyRef && !vastRunnerOpen && (
                    <>
                      <Badge tone="neutral">
                        {t('cloud.control.runnerBadge', { ref: vastMonkeyRef.slice(0, 8) })}
                      </Badge>
                      <button type="button" className="btn btn-sm" onClick={() => setVastRunnerOpen(true)}>
                        {t('cloud.control.changeRunner')}
                      </button>
                    </>
                  )}
                </div>
                {vastAccount && !vastAccount.balance_ok && (
                  <p className="text-[12px] text-[color:var(--color-warn)]">{t('cloud.control.setupNoCredit')}</p>
                )}
                <Field label={t('cloud.control.modelLabel')} hint={t('cloud.control.modelHint')}>
                  <select
                    value={vastAdapter}
                    onChange={(e) => setVastAdapter(e.target.value)}
                    className="fld fld-mono"
                  >
                    {vastModels.map((item) => (
                      <option key={item.adapter_id} value={item.adapter_id}>
                        {item.hf_repo}
                      </option>
                    ))}
                  </select>
                </Field>
                {vastModels.find((item) => item.adapter_id === vastAdapter)?.needs_own_image && (
                  <p className="text-[11px] text-[color:var(--color-ink-2)]">
                    {t('cloud.control.modelOwnImage', {
                      image: vastModels.find((item) => item.adapter_id === vastAdapter)?.docker_image || '',
                    })}
                  </p>
                )}
                <Field label={t('cloud.control.modelCustom')} hint={t('cloud.control.modelCustomHint')}>
                  <input
                    value={vastModelCustom}
                    onChange={(e) => setVastModelCustom(e.target.value)}
                    placeholder="org/checkpoint"
                    className="fld fld-mono"
                  />
                </Field>
                {(vastRunnerOpen || !vastMonkeyRef) && (
                  <Field label={t('cloud.control.monkeyRefLabel')} hint={t('cloud.control.monkeyRefHint')}>
                    <input
                      value={vastMonkeyRef}
                      onChange={(e) => setVastMonkeyRef(e.target.value)}
                      className="fld fld-mono"
                    />
                  </Field>
                )}
              </div>
            </Module>

            <Module tab={t('cloud.control.findOfferLabel')} quiet flush>
              <div className="grid gap-3 p-3 sm:grid-cols-3">
                <Field label={t('cloud.control.gpuFilterLabel')} hint={t('cloud.control.gpuFilterHint')}>
                  <input value={vastGpu} onChange={(e) => setVastGpu(e.target.value)} placeholder="RTX 4090" className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.vramLabel')} hint={t('cloud.control.vramHint')}>
                  <input type="number" min="0" step="1" value={vastVram} onChange={(e) => setVastVram(e.target.value)} className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.maxPriceLabel')}>
                  <input type="number" min="0" step="0.01" value={vastMaxDph} onChange={(e) => setVastMaxDph(e.target.value)} placeholder="0.50" className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.diskLabel')} hint={t('cloud.control.diskHint')}>
                  <input type="number" min="10" value={vastDiskGb} onChange={(e) => setVastDiskGb(e.target.value)} className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.netLabel')} hint={t('cloud.control.netHint')}>
                  <input type="number" min="0" step="10" value={vastNet} onChange={(e) => setVastNet(e.target.value)} placeholder="100" className="fld fld-mono" />
                </Field>
                <Field label={t('cloud.control.cudaLabel')} hint={t('cloud.control.cudaHint')}>
                  <input type="number" min="0" step="0.1" value={vastCuda} onChange={(e) => setVastCuda(e.target.value)} placeholder="12.4" className="fld fld-mono" />
                </Field>
                <label className="flex items-center gap-2 text-[12px] sm:col-span-2">
                  <input type="checkbox" checked={vastVerified} onChange={(e) => setVastVerified(e.target.checked)} />
                  {t('cloud.control.verifiedOnly')}
                </label>
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
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-bold">{offer.num_gpus}× {offer.gpu_name || 'GPU'}</span>
                          {offer.verified && <Badge tone="ok">{t('cloud.control.verified')}</Badge>}
                        </div>
                        <div className="mono text-[11px] text-[color:var(--color-ink-2)]">
                          {t('cloud.control.offerSpecs', {
                            vram: offer.gpu_ram == null ? '—' : String(Math.round(offer.gpu_ram / 1024)),
                            disk: offer.disk_space == null ? '—' : String(Math.round(offer.disk_space)),
                            net: offer.inet_down == null ? '—' : String(Math.round(offer.inet_down)),
                          })}
                          {offer.cuda_max_good != null && ` · CUDA ${offer.cuda_max_good}`}
                        </div>
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

            {renderInstanceList(
              vastInstances,
              vastBusy,
              handleConnectVast,
              t('cloud.control.connect'),
              (id) => void handleControlVast(id, 'start'),
              (id) => void handleControlVast(id, 'stop'),
              (id) => void handleControlVast(id, 'delete'),
              (inst) => void handleProvisionVast(inst),
              () => void handleLoadVast(),
              vastLoaded,
            )}

            {(vastProvisionTarget || vastProvisionLog.length > 0) && (
              <Module tab={t('cloud.control.provisionLogLabel')} quiet flush>
                <pre className="mono max-h-56 overflow-auto p-3 text-[11px] leading-[1.5]">
                  {vastProvisionLog.join('\n')}
                </pre>
              </Module>
            )}

            <Collapsible
              tab={t('cloud.control.manualTunnelTitle')}
              quiet
              aux={tunnelState.running ? <Badge tone="ok">{t('cloud.control.tunnelOpen')}</Badge> : undefined}
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <p className="text-[12px] text-[color:var(--color-ink-2)] sm:col-span-2">
                  {t('cloud.control.manualTunnelHint')}
                </p>
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
            </Collapsible>

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
