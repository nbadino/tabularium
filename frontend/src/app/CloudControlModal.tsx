import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiDelete, apiGet, apiPost } from '../lib/api'
import { useI18n } from '../i18n'
import { IconCopy } from './icons'
import { Badge, Collapsible, Field, Modal, Module, Notice, Progress } from './ui'
import { useConfirm } from './confirm'
import { saveInferenceToBackend, testInferenceConnection, useInference } from './inference'
import { ChecklistItem, type ChecklistState } from './models/Checklist'

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
  /** Reso nella pagina (passo «Configura» dell'hub Modelli) invece che in una
   *  modale: niente cornice né riga «in uso ora», che l'hub mostra già. */
  inline?: boolean
  /** Mostra solo la sezione di questo provider: nel percorso guidato la
   *  destinazione è già decisa e le altre tre sarebbero rumore. */
  onlyProvider?: Provider | null
}

/** In linea la sezione del provider è l'unica visibile: un soffietto che si
 *  può chiudere su se stesso non ha senso, quindi diventa un modulo fisso,
 *  numerato come il passo «Configura» dell'hub in cui vive. */
function InlineSection({ tab, aux, children }: { tab: string; quiet?: boolean; aux?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode }) {
  return <Module tab={`3 · ${tab}`} aux={aux}>{children}</Module>
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
  // Vast.ai pubblica due vie diverse verso la stessa istanza: l'IP della
  // macchina («Direct SSH Connect») e il forwarder ssh*.vast.ai. Il backend le
  // tiene separate perché non sono combinabili e non falliscono allo stesso modo.
  ssh_direct_host?: string | null
  ssh_direct_port?: number | null
  ssh_proxy_host?: string | null
  ssh_proxy_port?: number | null
  ssh_via?: 'direct' | 'proxy' | 'unknown'
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
  /** Spazio libero da avere nel container prima del provisioning. */
  min_free_disk_gb: number
  /** VRAM libera minima consigliata dal preset del modello. */
  min_free_vram_gb: number
}

/**
 * Preparazione Vast.ai in corso, sopravvissuta al ricaricamento.
 *
 * Lo script di setup gira sulla GPU e non si ferma se la pagina si chiude:
 * senza questo promemoria la UI, riaperta, non sapeva più che cosa stava
 * seguendo e mostrava «non preparato» mentre il server si installava. Non
 * contiene segreti: solo quale istanza, quale endpoint SSH e quale nome
 * servito aspettare.
 */
const VAST_JOB_KEY = 'tabularium.vast.job'
interface VastJob {
  instanceId: string
  host: string
  port: number
  servedName: string
  adapterId: string
}

export function readVastJob(): VastJob | null {
  try {
    const raw = localStorage.getItem(VAST_JOB_KEY)
    if (!raw) return null
    const job = JSON.parse(raw) as Partial<VastJob>
    if (!job.host || !job.port || !job.instanceId) return null
    return {
      instanceId: String(job.instanceId),
      host: String(job.host),
      port: Number(job.port),
      servedName: String(job.servedName || ''),
      adapterId: String(job.adapterId || ''),
    }
  } catch {
    return null
  }
}

export function writeVastJob(job: VastJob | null) {
  try {
    if (job) localStorage.setItem(VAST_JOB_KEY, JSON.stringify(job))
    else localStorage.removeItem(VAST_JOB_KEY)
  } catch {
    /* storage non disponibile: la ripresa dopo un refresh passa dalla sonda del log */
  }
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

/**
 * Endpoint SSH su cui agire: l'override dell'utente vince su quello dell'API.
 *
 * Serve al caso in cui Vast.ai pubblica solo il forwarder `ssh*.vast.ai` (che
 * può rifiutare la chiave) e tiene `public_ipaddr` a `null`: l'indirizzo
 * diretto esiste allora solo nella console del provider. L'override vale per
 * intero o per niente — host di una via e porta dell'altra non descrivono
 * nessuna destinazione reale.
 */
export function resolveSshEndpoint(
  inst: Pick<RentedInstance, 'ssh_host' | 'ssh_port'>,
  overrideHost: string,
  overridePort: string,
): { host: string; port: number } | null {
  const host = overrideHost.trim()
  const port = parseInt(overridePort.trim(), 10)
  if (host && Number.isFinite(port) && port > 0 && port < 65536) return { host, port }
  if (inst.ssh_host && inst.ssh_port) return { host: inst.ssh_host, port: inst.ssh_port }
  return null
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
  if (url.includes('teleocr')) return 'teleocr'
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
  teleocr: { model: 'StarDoc-AI/TeleOCR', adapterId: 'teleocr' },
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
  { id: 'teleocr', label: 'TeleOCR' },
]

function copyToClipboard(text: string, onDone: () => void) {
  navigator.clipboard?.writeText(text).then(onDone).catch(() => {})
}

export function CloudControlModal({ open, onClose, focusProvider, focusAdapterId, focusModelLabel, inline = false, onlyProvider = null }: CloudControlModalProps) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const inf = useInference()
  // La destinazione esplicita vince sempre: il guess dall'URL vale solo come
  // ripiego quando l'apertura non nasce da una scelta di modello.
  const initialProvider = focusProvider ?? guessProvider(inf.url)
  // Deploy guidato: l'apertura nasce da «Deploya su <provider>» e deve
  // dichiararsi, o il pannello è indistinguibile da un'apertura normale.
  const guided = Boolean(open && focusAdapterId && focusProvider && focusProvider !== 'manual')
  const publishingCheckpoint = Boolean(guided && focusProvider === 'modal' && focusAdapterId?.startsWith('custom-'))
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
  const [vastModels, setVastModels] = useState<VastModel[]>([])
  const [vastAdapter, setVastAdapter] = useState('monkeyocrv2-parsing')
  const [vastModelCustom, setVastModelCustom] = useState('')
  // Il nome da chiedere all'endpoint dopo un refresh: quello del lavoro in
  // corso, altrimenti quello del profilo Vast attivo. Il vecchio default fisso
  // (`MonkeyOCRv2`) faceva fallire la verifica di qualunque altro modello.
  const [vastServedName, setVastServedName] = useState(
    () => readVastJob()?.servedName || (inf.provider === 'vast' && inf.model ? inf.model : 'MonkeyOCRv2'),
  )
  /** Istanza su cui lavora il percorso guidato (con più istanze nell'account). */
  const [vastSelectedId, setVastSelectedId] = useState<string | null>(() => readVastJob()?.instanceId ?? null)
  const [vastAccountEdit, setVastAccountEdit] = useState(false)
  /** Istanza di cui si è già letto lo stato del server: una sonda per istanza. */
  const [vastProbedId, setVastProbedId] = useState<string | null>(null)
  const [vastProbing, setVastProbing] = useState(false)
  /** Chiave nel vault ma account non ancora letto: non è «da collegare». */
  const [vastAccountLoading, setVastAccountLoading] = useState(true)
  /** Un altro modello trovato sulla GPU (il log remoto è uno per istanza). */
  const [vastOtherModel, setVastOtherModel] = useState<{ adapterId: string; state: 'ready' | 'failed' | 'preparing' } | null>(null)
  /** Il modello che il percorso sta configurando: quello scelto nell'hub vince
   *  sullo stato locale, che al primo render non l'ha ancora recepito. */
  const wantedAdapter = guided && focusAdapterId && !focusAdapterId.startsWith('custom-') ? focusAdapterId : vastAdapter
  const selectedVastRecipe = vastModels.find((item) => item.adapter_id === wantedAdapter)
  const recipeFreeDiskGb = selectedVastRecipe?.min_free_disk_gb ?? 22
  // Vast's `disk` is the container quota, not the free space seen by the
  // installer. Reserve 28 GB for the base image and common system tools, then
  // add the model recipe's free-space budget, rounded to a 10 GB tier.
  const recommendedVastDiskGb = selectedVastRecipe
    ? Math.ceil((recipeFreeDiskGb + 28) / 10) * 10
    : 80
  const recommendedVastVramGb = selectedVastRecipe
    ? Math.ceil((selectedVastRecipe.min_free_vram_gb + 2) / 4) * 4
    : 24

  useEffect(() => {
    setVastDiskGb(String(recommendedVastDiskGb))
  }, [recommendedVastDiskGb])
  useEffect(() => {
    setVastVram(String(recommendedVastVramGb))
  }, [recommendedVastVramGb])
  const [vastMonkeyRef, setVastMonkeyRef] = useState('')
  const [sshHost, setSshHost] = useState('')
  const [sshPort, setSshPort] = useState('')
  const [sshUser, setSshUser] = useState('root')
  // Override dell'endpoint SSH: quando Vast.ai non pubblica `public_ipaddr`
  // l'unico posto dove l'indirizzo diretto esiste è la console del provider.
  // La porta invece arriva dall'API, quindi si precompila.
  const [vastDirectHost, setVastDirectHost] = useState('')
  const [vastDirectPort, setVastDirectPort] = useState('')
  const [vastDirectChecking, setVastDirectChecking] = useState(false)
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
  // In un deploy guidato il modello scelto dall'utente ha precedenza sulla
  // preferenza Modal rimasta in localStorage. Così anche il primo render usa
  // subito la template corretta, senza mostrare per un istante il profilo
  // MonkeyOCRv2 mentre l'effetto di sincronizzazione aggiorna lo stato.
  const effectiveModalTemplate = guided && focusProvider === 'modal' && focusAdapterId
    ? focusAdapterId.startsWith('custom-')
      ? 'monkeyocrv2'
      : TEMPLATE_BY_ADAPTER[focusAdapterId] ?? 'monkeyocrv2'
    : modalTemplate
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
  const showMonkeyRunner = vastAdapter === 'monkeyocrv2-parsing' || vastAdapter.startsWith('custom-')

  const [copied, setCopied] = useState<string | null>(null)
  const copy = (text: string, id: string) => copyToClipboard(text, () => { setCopied(id); setTimeout(() => setCopied(null), 2000) })

  useEffect(() => {
    if (!open) return
    setManualUrl(inf.url)
    setManualModel(focusProvider === 'manual' && focusModelLabel ? focusModelLabel : inf.model)
    setManualKey(inf.apiKey)
    void pollTunnelStatus()
    void refreshVastSshKey()
    void refreshVastKeyStatus()
    void refreshVastModels()
  }, [open, focusProvider, focusModelLabel])

  // Apertura «guidata» dalla libreria modelli: il modello è già stato scelto,
  // qui si atterra sulla scheda giusta con quel modello preselezionato.
  useEffect(() => {
    if (!open || !focusAdapterId) return
    if (focusProvider === 'vast') setVastAdapter(focusAdapterId)
    if (focusProvider === 'modal') {
      const template = TEMPLATE_BY_ADAPTER[focusAdapterId]
      // Un checkpoint custom è servibile solo con il runner Monkey completo:
      // il provider è già scelto, quindi qui non lasciamo riaprire la scelta
      // del modello/template.
      if (focusAdapterId.startsWith('custom-')) setModalTemplate('monkeyocrv2')
      else if (template) setModalTemplate(template)
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
    void handleLoadVast(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, vastCredentialReady])

  // Il task Modal è long-running (deploy: minuti): si interroga lo stato
  // finché la finestra è aperta. Ogni template ha il proprio stato/endpoint.
  useEffect(() => {
    if (!open) return
    let stop = false
    const tick = async () => {
      try {
        const res = await apiGet<ModalStatus>(`/system/cloud/modal?template=${effectiveModalTemplate}`)
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
  }, [open, effectiveModalTemplate])

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
          writeVastJob(null)
          return
        }
        if (res.failed) {
          setVastPhase('failed')
          // Lo script è morto: continuare a interrogarlo non cambia nulla e
          // nasconderebbe l'errore dietro una barra che gira per sempre.
          setVastProvisionTarget(null)
          writeVastJob(null)
          setVastNotice(t('cloud.control.provisionFailed', { error: res.error }))
          return
        }
        if (res.ready) {
          setVastProvisionTarget(null)
          setVastPhase('ready')
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

  // Ripresa dopo un refresh: se una preparazione era in corso la si torna a
  // seguire, e quando il server è pronto il tunnel parte da solo come se la
  // pagina non si fosse mai chiusa.
  useEffect(() => {
    if (!open) return
    const job = readVastJob()
    if (!job) return
    // Un lavoro lasciato a metà per un altro modello non si riprende qui: lo
    // racconta la sonda del log, come «sulla GPU c'è un altro modello».
    if (job.adapterId && job.adapterId !== wantedAdapter) return
    if (job.servedName) setVastServedName(job.servedName)
    setVastSelectedId(job.instanceId)
    setSshHost(job.host)
    setSshPort(String(job.port))
    setVastProvisionTarget({ host: job.host, port: job.port })
    setVastNotice(t('cloud.control.vastGuide.resumed', { id: job.instanceId }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Il lavoro è finito quando l'endpoint risponde: da lì in poi lo stato vero
  // è il profilo attivo, non un promemoria nel browser.
  useEffect(() => {
    if (inferenceOk) writeVastJob(null)
  }, [inferenceOk])

  // Il nome servito segue la ricetta del modello scelto, finché non c'è una
  // preparazione in corso che ne ha già fissato uno.
  useEffect(() => {
    if (vastProvisionTarget || readVastJob() || vastModelCustom.trim()) return
    const recipe = vastModels.find((item) => item.adapter_id === vastAdapter)
    if (recipe?.served_model_name) setVastServedName(recipe.served_model_name)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vastModels, vastAdapter])

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
      // The readiness effect below is the only writer allowed to mark a
      // tunnel active: it probes the endpoint and persists the profile as one
      // operation. A separate probe here could set inferenceOk before saving,
      // causing that effect to bail out and leave the UI checking forever.
      if (!res.running) setInferenceOk(false)
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
        await saveInferenceToBackend({
          enabled: true,
          url: localUrl,
          model: vastServedName,
          adapterId: vastAdapter,
        })
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
  }, [open, tunnelState.running, tunnelState.local_port, inferenceOk, vastServedName, vastAdapter])

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
      if (!res.configured) {
        setVastAccountLoading(false)
        return
      }
      // Chiave già nel vault: il pannello mostra lo stato reale senza che
      // l'utente debba rieseguire la prima configurazione.
      const ref = `vault:${VAST_SECRET}`
      try {
        setVastAccount(await apiPost<VastAccount>('/system/cloud/vast/account', { credential_ref: ref }))
      } catch {
        /* account non raggiungibile ora: il badge resta assente */
      } finally {
        setVastAccountLoading(false)
      }
      await resolveMonkeyRef()
      // Con la chiave nel vault non c'è ragione di aspettare un click per
      // sapere che istanze esistono: la catena deve nascere già vera.
      await handleLoadVast(true)
    } catch {
      /* vault non configurato: la chiave resta da incollare a ogni sessione */
      setVastAccountLoading(false)
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

  /**
   * Precompila l'override con la mappatura della 22 pubblicata dall'API.
   *
   * Quando manca `public_ipaddr` la porta diretta c'è comunque: all'utente
   * resta da incollare solo l'host, che è l'unica metà non ottenibile. Non
   * sovrascrive mai quello che ha già scritto.
   */
  const prefillDirectEndpoint = (items: RentedInstance[]) => {
    const candidate = items.find((inst) => inst.is_running && inst.ssh_direct_port)
    if (!candidate) return
    setVastDirectPort((current) => current.trim() || String(candidate.ssh_direct_port))
    if (candidate.ssh_direct_host) {
      setVastDirectHost((current) => current.trim() || String(candidate.ssh_direct_host))
    }
  }

  /** Endpoint su cui lavorare: l'override vince, altrimenti quello dell'API. */
  const effectiveEndpoint = (inst: RentedInstance) =>
    resolveSshEndpoint(inst, vastDirectHost, vastDirectPort)

  // Un'istanza accesa che Vast.ai pubblica solo via forwarder: è lo scenario in
  // cui la chiave può essere rifiutata e l'override diventa l'unica strada.
  const vastNeedsDirectOverride = vastInstances.some(
    (inst) => inst.is_running && inst.ssh_via === 'proxy',
  )

  /** Prova l'endpoint prima di spenderci una preparazione (o un noleggio). */
  const handleCheckDirectSsh = async () => {
    const host = vastDirectHost.trim()
    const port = parseInt(vastDirectPort.trim(), 10)
    if (!host || !Number.isFinite(port) || port <= 0) {
      setVastNotice(t('cloud.control.missingHostPort'))
      return
    }
    setVastDirectChecking(true)
    try {
      // La host key va fissata prima: senza, il preflight fallirebbe sulla
      // verifica invece che dire qualcosa sull'accesso.
      await apiPost('/system/cloud/vast/hostkey', { host, port })
      const res = await apiPost<{ ok: boolean; reason: string; message?: string }>(
        '/system/cloud/vast/ssh-check',
        { host, port, user: sshUser.trim() || 'root', attempts: 2 },
      )
      setVastNotice(
        res.ok
          ? t('cloud.control.sshCheckOk', { host, port: String(port) })
          : res.message || t('cloud.control.sshCheckFailed', { host, port: String(port) }),
      )
    } catch (e) {
      setVastNotice(t('cloud.control.sshCheckFailed', { host, port: String(port) }) + ` ${String(e)}`)
    } finally {
      setVastDirectChecking(false)
    }
  }

  /** Pin della host key: idempotente, richiesto prima di ogni uso di SSH. */
  const pinHostKey = async (endpoint: { host: string; port: number }) => {
    const pin = await apiPost<{ host: string; port: number; key_types: string[] }>(
      '/system/cloud/vast/hostkey',
      { host: endpoint.host, port: endpoint.port },
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
    const endpoint = effectiveEndpoint(inst)
    if (!endpoint) {
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
      await pinHostKey(endpoint)
      const res = await apiPost<{ served_model_name: string; already_ready?: boolean }>('/system/cloud/vast/provision', {
        host: endpoint.host,
        port: endpoint.port,
        monkeyocr_ref: ref,
        adapter_id: vastAdapter,
        // Vuoto = il checkpoint ufficiale della ricetta; valorizzato = un tuo
        // fine-tuned con la stessa architettura.
        model: vastModelCustom.trim(),
        remote_port: 8888,
      })
      setVastServedName(res.served_model_name)
      setSshHost(endpoint.host)
      setSshPort(String(endpoint.port))
      setVastProvisionLog([])
      setVastOtherModel(null)
      setVastSelectedId(String(inst.id))
      setVastProvisionTarget({ host: endpoint.host, port: endpoint.port })
      writeVastJob({
        instanceId: String(inst.id),
        host: endpoint.host,
        port: endpoint.port,
        servedName: res.served_model_name,
        adapterId: vastAdapter,
      })
      if (res.already_ready) {
        // Il server remoto è già pronto: non serve aspettare il log di setup;
        // apriamo subito il solo tunnel e poi il probe mostrerà la conferma.
        setVastNotice(t('cloud.control.serverAlreadyReady'))
        await handleStartTunnel(endpoint.host, endpoint.port)
      } else {
        setVastNotice(t('cloud.control.provisionStarted'))
      }
    } catch (e) {
      setVastNotice(t('cloud.control.provisionError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handlePublishVast = async (inst: RentedInstance) => {
    if (!focusAdapterId || !focusAdapterId.startsWith('custom-')) return
    const endpoint = effectiveEndpoint(inst)
    if (!endpoint) {
      setVastNotice(t('cloud.control.noSsh'))
      return
    }
    const credential = vastCredential()
    if (!credential) {
      setVastNotice(t('cloud.control.missingKey'))
      return
    }
    setVastBusy(true)
    try {
      setVastNotice(t('cloud.control.checkpointUploading'))
      const res = await apiPost<{ served_model_name: string }>('/system/cloud/vast/publish-checkpoint', {
        ...credential,
        host: endpoint.host,
        port: endpoint.port,
        adapter_id: focusAdapterId,
        // Il backend richiede il ref solo se il modello base usa la recipe
        // MonkeyOCRv2. Per gli altri runtime il checkpoint non deve dipendere
        // da una chiamata GitHub aggiuntiva.
        monkeyocr_ref: vastMonkeyRef.trim(),
        remote_port: 8888,
      })
      setVastServedName(res.served_model_name)
      setVastNotice(t('cloud.control.checkpointUploaded'))
      await handleStartTunnel(endpoint.host, endpoint.port)
    } catch (e) {
      setVastNotice(t('cloud.control.checkpointUploadError', { error: String(e) }))
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
      prefillDirectEndpoint(res.items)
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
        disk_gb: Math.max(Number(vastDiskGb) || 40, recommendedVastDiskGb),
        min_gpu_ram_gb: vastVram ? Math.max(Number(vastVram), recommendedVastVramGb) : recommendedVastVramGb,
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
    const rentOk = await confirm({
      title: t('cloud.control.rentTitle'),
      message: t('cloud.control.rentConfirm', { gpu: `${offer.num_gpus}× ${offer.gpu_name || 'GPU'}`, price }),
      acceptLabel: t('cloud.control.rentTitle'),
    })
    if (!rentOk) return
    setVastBusy(true)
    setVastNotice(null)
    try {
      const res = await apiPost<{ contract_id: number | null }>('/system/cloud/vast/rent', {
        ...credential, offer_id: offer.id,
        disk_gb: Math.max(Number(vastDiskGb) || 40, recommendedVastDiskGb),
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
      if (res.contract_id != null) {
        setVastWaitingId(res.contract_id)
        setVastSelectedId(String(res.contract_id))
      }
      await handleLoadVast()
    } catch (e) {
      setVastNotice(t('cloud.control.rentError', { error: String(e) }))
    } finally {
      setVastBusy(false)
    }
  }

  const handleControlVast = async (instanceId: number | string, action: 'start' | 'stop' | 'delete') => {
    if (action === 'delete') {
      const ok = await confirm({
        title: t('cloud.control.deleteResourceTitle'),
        message: t('cloud.control.deleteResourceConfirm', { id: String(instanceId) }),
        acceptLabel: t('cloud.control.deleteResourceTitle'),
      })
      if (!ok) return
    }
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
    const endpoint = effectiveEndpoint(inst)
    if (!endpoint) {
      setVastNotice(t('cloud.control.noSsh'))
      return
    }
    setSshHost(endpoint.host)
    setSshPort(String(endpoint.port))
    // La host key va fissata prima del tunnel: il backend usa
    // StrictHostKeyChecking=yes e senza pinning la connessione fallirebbe.
    setTunnelBusy(true)
    try {
      await pinHostKey(endpoint)
    } catch (e) {
      setVastNotice(t('cloud.control.hostKeyError', { error: String(e) }))
      return
    } finally {
      setTunnelBusy(false)
    }
    await handleStartTunnel(endpoint.host, endpoint.port)
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
    if (action === 'delete') {
      const ok = await confirm({
        title: t('cloud.control.deleteResourceTitle'),
        message: t('cloud.control.deleteResourceConfirm', { id: String(podId) }),
        acceptLabel: t('cloud.control.deleteResourceTitle'),
      })
      if (!ok) return
    }
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
      await saveInferenceToBackend({
        enabled: true,
        url,
        model: guided && focusModelLabel ? focusModelLabel : undefined,
        apiKey: runpodApiKey.trim() || undefined,
        adapterId: guided && focusAdapterId ? focusAdapterId : inf.adapterId,
      })
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
        template: effectiveModalTemplate,
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

  const handleModalPublish = async () => {
    if (!focusAdapterId) return
    setModalBusy(true)
    setModalNotice(null)
    try {
      await apiPost('/system/cloud/modal/publish-checkpoint', {
        adapter_id: focusAdapterId,
        keep_warm: modalKeepWarm,
      })
      setModalNotice(t('cloud.control.modalCheckpointStarted'))
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    } finally {
      setModalBusy(false)
    }
  }

  const handleModalStop = async () => {
    const ok = await confirm({
      title: t('cloud.control.modalStopTitle'),
      message: t('cloud.control.modalStopConfirm'),
    })
    if (!ok) return
    setModalBusy(true)
    setModalNotice(null)
    try {
      await apiPost('/system/cloud/modal/stop', { template: effectiveModalTemplate })
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
    const target = MODAL_TEMPLATE_TARGET[effectiveModalTemplate] ?? MODAL_TEMPLATE_TARGET.monkeyocrv2
    try {
      const connection = {
        enabled: true,
        url,
        // Il nome da chiedere all'endpoint è quello servito dalla template
        // (`MonkeyOCRv2`), non il nome mostrato nella libreria
        // (`MonkeyOCRv2-Parsing`): solo un checkpoint pubblicato non ha una
        // template propria e resta sul nome scelto.
        model: guided && focusModelLabel && focusAdapterId?.startsWith('custom-') ? focusModelLabel : target.model,
        adapterId: guided && focusAdapterId ? focusAdapterId : target.adapterId,
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
        adapterId: focusAdapterId ?? undefined,
      }
      await testInferenceConnection(connection)
      await saveInferenceToBackend(connection)
      onClose()
    } catch (e) {
      setModalNotice(t('cloud.control.modalTaskError', { error: String(e) }))
    }
  }

  // --- Percorso guidato Vast.ai: l'istanza su cui si lavora ---
  // Quella scelta, altrimenti quella che serve già il tunnel, altrimenti la
  // prima accesa: con una sola istanza nell'account non c'è niente da scegliere.
  const vastSelected =
    vastInstances.find((inst) => String(inst.id) === vastSelectedId) ??
    vastInstances.find((inst) => tunnelState.running && effectiveEndpoint(inst)?.host === tunnelState.host) ??
    vastInstances.find((inst) => inst.is_running) ??
    vastInstances[0] ??
    null
  const vastSelectedEndpoint = vastSelected?.is_running ? effectiveEndpoint(vastSelected) : null

  // Una sola lettura del log remoto per istanza: dice se il server è già
  // pronto, se si sta preparando (e allora lo si torna a seguire) o se non è
  // mai stato toccato. È ciò che rende vero lo stato dopo un refresh anche
  // senza promemoria nel browser (altra scheda, altro computer).
  useEffect(() => {
    if (!open || !vastSelected || !vastSelectedEndpoint || vastProvisionTarget) return
    const id = String(vastSelected.id)
    if (vastProbedId === id) return
    setVastProbedId(id)
    setVastProbing(true)
    void (async () => {
      try {
        await apiPost('/system/cloud/vast/hostkey', vastSelectedEndpoint)
        const res = await apiPost<{ lines: string[]; ready: boolean; phase: string; failed: boolean; error: string; present: boolean; adapter_id?: string }>(
          '/system/cloud/vast/provision/log',
          { ...vastSelectedEndpoint, lines: 60 },
        )
        setVastProvisionLog(res.lines)
        const customPublishing = guided && focusProvider === 'vast' && !!focusAdapterId?.startsWith('custom-')
        if (res.present && res.adapter_id && res.adapter_id !== wantedAdapter && !customPublishing) {
          // Lo stato è di un altro modello: per quello scelto la GPU è da
          // preparare, e prepararla sostituisce l'altro.
          setVastOtherModel({
            adapterId: res.adapter_id,
            state: res.ready ? 'ready' : res.failed ? 'failed' : 'preparing',
          })
          setVastPhase('absent')
        } else if (!res.present) setVastPhase('absent')
        else if (res.ready) setVastPhase('ready')
        else if (res.failed) {
          setVastPhase('failed')
          setVastNotice(t('cloud.control.provisionFailed', { error: res.error }))
        } else {
          setVastPhase(res.phase || 'starting')
          setVastProvisionTarget(vastSelectedEndpoint)
        }
      } catch {
        // SSH non ancora raggiungibile: lo stato resta «da verificare» e il
        // pulsante di preparazione resta disponibile.
        setVastPhase('')
      } finally {
        setVastProbing(false)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, vastSelected?.id, vastSelectedEndpoint?.host, vastSelectedEndpoint?.port, vastProvisionTarget])

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
  const renderInstanceList = (
    items: RentedInstance[],
    busy: boolean,
    onConnect: (inst: RentedInstance) => void,
    connectLabel: string,
    onStart: (id: number | string) => void,
    onStop: (id: number | string) => void,
    onDelete: (id: number | string) => void,
    onProvision?: (inst: RentedInstance) => void,
    onPublish?: (inst: RentedInstance) => void,
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
          // Con l'override attivo l'istanza si prepara su un endpoint diverso
          // da quello pubblicato: il confronto deve guardare quello vero,
          // altrimenti la scheda resta muta mentre il setup è in corso.
          const target = onProvision ? effectiveEndpoint(inst) : null
          const targetHost = target?.host ?? inst.ssh_host
          const targetPort = target?.port ?? inst.ssh_port
          const isPreparing = Boolean(
            onProvision && vastProvisionTarget?.host === targetHost && vastProvisionTarget?.port === targetPort,
          )
          const isConnected = Boolean(
            onProvision && inferenceOk && tunnelState.running && tunnelState.host === targetHost,
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
                ID: {inst.id} {targetHost && `· SSH: ${targetHost}:${targetPort}`}
                {inst.ssh_via === 'proxy' && ` · ${t('cloud.control.sshViaProxy')}`}
                {inst.ssh_via === 'direct' && ` · ${t('cloud.control.sshViaDirect')}`}
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
                      disabled={busy || tunnelBusy || isPreparing || !targetHost || !targetPort}
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
                  {onPublish && (
                    <button
                      type="button"
                      onClick={() => onPublish(inst)}
                      disabled={busy || tunnelBusy || !targetHost || !targetPort}
                      className="btn btn-sm btn-primary"
                    >
                      {t('cloud.control.publishCheckpoint')}
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

  const currentProvider = inf.provider ?? (inf.url ? guessProvider(inf.url) : null)
  const activeProviderLabel =
    currentProvider === 'modal'
      ? t('recognition.provider.modal')
      : currentProvider === 'vast'
        ? t('recognition.provider.vast')
        : currentProvider === 'runpod'
          ? t('recognition.provider.runpod')
          : currentProvider === 'local'
            ? t('recognition.provider.local')
            : t('cloud.control.tabManual')

  const shows = (provider: Provider) => !onlyProvider || onlyProvider === provider
  // Solo le quattro sezioni dei provider diventano fisse: i soffietti annidati
  // (SSH diretto, tunnel manuale) restano soffietti anche in linea.
  const Section = inline ? InlineSection : Collapsible

  const body = (
      <div className={inline ? 'space-y-3' : 'space-y-3 p-3'}>
        {/* Cosa sta usando Tabularium ORA, visibile qualunque pannello sia
            aperto: la domanda "cosa sto usando?" non deve richiedere di
            aprire ogni scheda per dedurlo (riprodotto: un deploy avviato ma
            non attivato è invisibile senza questa riga). */}
        {/* Deploy guidato: primo, perché è l'intento con cui la finestra è
            stata aperta; «In uso ora» è il contesto, questo è il compito. */}
        {guided && !inline && (
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
        {!inline && <div className="flex flex-wrap items-center gap-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-panel)] p-2 text-[12px]">
          <span className="lbl !mb-0">{t('cloud.control.activeNowLabel')}</span>
          <Badge tone={inf.enabled ? 'ok' : 'neutral'}>
            {inf.enabled ? `${inf.model || '—'} · ${activeProviderLabel}` : t('cloud.control.activeNowDisabled')}
          </Badge>
        </div>}

        {/* --- Vast.ai --- */}
        {shows('vast') && <Section tab={t('cloud.control.tabVast')} defaultOpen={initialProvider === 'vast'} aux={
          vastSelected?.is_running && vastSelected.dph_total != null ? (
            <span className="mono text-[11px] font-semibold">
              {t('cloud.control.vastGuide.billing', { price: vastSelected.dph_total.toFixed(3) })}
            </span>
          ) : undefined
        }>
          {(() => {
            // Le quattro tappe, ognuna ricavata dallo stato vero (account,
            // istanza, log remoto, tunnel) e non da ciò che si è cliccato: un
            // refresh non può quindi farle regredire.
            const customPublish = guided && focusProvider === 'vast' && !!focusAdapterId?.startsWith('custom-')
            const recipe = vastModels.find((item) => item.adapter_id === vastAdapter)
            const accountDone = Boolean(vastAccount && vastSshKey?.exists && (vastKeySaved || vastApiKey.trim()) && !vastAccountEdit)
            const gpuDone = accountDone && Boolean(vastSelectedEndpoint)
            const serverDone = gpuDone && (vastPhase === 'ready' || inferenceOk)
            const preparing = Boolean(vastProvisionTarget)
            const accountChecking = vastAccountLoading && vastKeySaved && !vastAccount
            const accountState: ChecklistState = accountDone ? 'done' : 'active'
            const gpuState: ChecklistState = !accountDone ? 'waiting' : gpuDone ? 'done' : 'active'
            const serverState: ChecklistState = !gpuDone
              ? 'waiting'
              : serverDone
                ? 'done'
                : vastPhase === 'failed'
                  ? 'failed'
                  : 'active'
            const connState: ChecklistState = !serverDone ? 'waiting' : inferenceOk ? 'done' : 'active'
            const phaseLabel = t(`cloud.control.${phaseWord[vastPhase] ?? 'phaseStarting'}`)
            const localUrl = `http://127.0.0.1:${tunnelState.local_port || 8888}/v1`
            const selectInstance = (inst: RentedInstance) => {
              if (String(inst.id) === String(vastSelected?.id)) return
              setVastSelectedId(String(inst.id))
              setVastPhase('')
              setVastProvisionLog([])
              setVastOtherModel(null)
            }
            const searchForm = (
              <div className="mt-2 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-3">
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label={t('cloud.control.gpuFilterLabel')} hint={t('cloud.control.gpuFilterHint')}>
                    <input value={vastGpu} onChange={(e) => setVastGpu(e.target.value)} placeholder="RTX 4090" className="fld fld-mono" />
                  </Field>
                  <Field label={t('cloud.control.vramLabel')} hint={t('cloud.control.vramHint')}>
                    <input type="number" min="0" step="1" value={vastVram} onChange={(e) => setVastVram(e.target.value)} className="fld fld-mono" />
                  </Field>
                  <Field label={t('cloud.control.maxPriceLabel')}>
                    <input type="number" min="0" step="0.01" value={vastMaxDph} onChange={(e) => setVastMaxDph(e.target.value)} placeholder="0.50" className="fld fld-mono" />
                  </Field>
                </div>
                <details className="mt-2">
                  <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                    {t('cloud.control.vastGuide.moreFilters')}
                  </summary>
                  <div className="mt-2 grid gap-3 sm:grid-cols-3">
                    <Field label={t('cloud.control.diskLabel')} hint={t('cloud.control.diskHint', { recommended: recommendedVastDiskGb, free: recipeFreeDiskGb })}>
                      <input type="number" min="10" value={vastDiskGb} onChange={(e) => setVastDiskGb(e.target.value)} className="fld fld-mono" />
                    </Field>
                    <Field label={t('cloud.control.netLabel')} hint={t('cloud.control.netHint')}>
                      <input type="number" min="0" step="10" value={vastNet} onChange={(e) => setVastNet(e.target.value)} placeholder="100" className="fld fld-mono" />
                    </Field>
                    <Field label={t('cloud.control.cudaLabel')} hint={t('cloud.control.cudaHint')}>
                      <input type="number" min="0" step="0.1" value={vastCuda} onChange={(e) => setVastCuda(e.target.value)} placeholder="12.4" className="fld fld-mono" />
                    </Field>
                    <label className="flex items-center gap-2 text-[12px] sm:col-span-3">
                      <input type="checkbox" checked={vastVerified} onChange={(e) => setVastVerified(e.target.checked)} />
                      {t('cloud.control.verifiedOnly')}
                    </label>
                  </div>
                </details>
                <button type="button" onClick={() => void handleSearchVast()} disabled={vastBusy} className="btn btn-primary mt-3">
                  {vastBusy ? t('cloud.control.loading') : t('cloud.control.findOffers')}
                </button>
                {vastOffers.length > 0 && (
                  <div className="mt-3 divide-y divide-[color:var(--color-rule)] border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]">
                    {vastOffers.map((offer) => (
                      <div key={offer.id} className="flex flex-wrap items-center justify-between gap-3 p-2.5">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-[color:var(--color-ink)]">{offer.num_gpus}× {offer.gpu_name || 'GPU'}</span>
                            {offer.verified && <Badge tone="ok">{t('cloud.control.verified')}</Badge>}
                            <span className="mono text-[12px] font-semibold text-[color:var(--color-ink)]">
                              {offer.dph_total == null ? '—' : `$${offer.dph_total.toFixed(3)}/h`}
                            </span>
                          </div>
                          <div className="mono text-[11px] text-[color:var(--color-ink-2)]">
                            {t('cloud.control.offerSpecs', {
                              vram: offer.gpu_ram == null ? '—' : String(Math.round(offer.gpu_ram / 1024)),
                              disk: offer.disk_space == null ? '—' : String(Math.round(offer.disk_space)),
                              net: offer.inet_down == null ? '—' : String(Math.round(offer.inet_down)),
                            })}
                            {offer.cuda_max_good != null && ` · CUDA ${offer.cuda_max_good}`}
                            {' · '}{offer.reliability == null ? '—' : `${(offer.reliability * 100).toFixed(1)}%`} · {offer.location || t('cloud.control.locationUnknown')}
                          </div>
                        </div>
                        <button type="button" onClick={() => void handleRentVast(offer)} disabled={vastBusy} className="btn btn-sm btn-primary">
                          {t('cloud.control.rent')}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )

            return (
              <div className="space-y-2">
                {vastNotice && <Notice tone={inferenceOk ? 'ok' : vastPhase === 'failed' ? 'warn' : 'neutral'}>{vastNotice}</Notice>}
                <ol>
                  {/* 1 — Account */}
                  <ChecklistItem
                    n={1}
                    title={t('cloud.control.vastGuide.accountTitle')}
                    state={accountState}
                    stateLabel={
                      accountDone
                        ? t('cloud.control.vastGuide.accountOk')
                        : accountChecking
                          ? t('cloud.control.vastGuide.checking')
                          : t('cloud.control.vastGuide.accountTodo')
                    }
                    aux={accountDone ? (
                      <button type="button" className="btn btn-sm" onClick={() => setVastAccountEdit(true)}>
                        {t('cloud.control.vastGuide.change')}
                      </button>
                    ) : undefined}
                  >
                    {accountDone ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone={vastAccount!.balance_ok ? 'ok' : 'warn'}>
                          {t('cloud.control.setupAccountOk', { email: vastAccount!.email || '—', balance: vastAccount!.balance.toFixed(2) })}
                        </Badge>
                        <Badge tone="ok">
                          {t('cloud.control.setupKeyReady', { fingerprint: `${vastSshKey!.fingerprint.slice(0, 18)}…` })}
                        </Badge>
                      </div>
                    ) : accountChecking ? (
                      <Progress indeterminate label={t('cloud.control.vastGuide.checking')} />
                    ) : (
                      <div className="space-y-2">
                        <p>
                          {t('cloud.control.vastGuide.accountBody')}{' '}
                          <a href="https://cloud.vast.ai/manage-keys/" target="_blank" rel="noopener noreferrer">
                            {t('cloud.control.vastGuide.openConsole')}
                          </a>
                        </p>
                        <div className="flex flex-wrap items-end gap-2">
                          <div className="min-w-[16rem] flex-1">
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
                          </div>
                          <button
                            type="button"
                            onClick={() => void handleVastSetup().then(() => setVastAccountEdit(false))}
                            disabled={vastBusy || (!vastApiKey.trim() && !vastKeySaved)}
                            className="btn btn-primary"
                          >
                            {vastBusy ? t('cloud.control.setupRunning') : t('cloud.control.setupRun')}
                          </button>
                        </div>
                        {vastKeySaved && (
                          <div className="flex items-center gap-2">
                            <Badge tone="ok">{t('cloud.control.keySaved')}</Badge>
                            <button type="button" className="btn btn-sm" onClick={() => void handleForgetVastKey()}>
                              {t('cloud.control.forgetKey')}
                            </button>
                            {vastAccountEdit && (
                              <button type="button" className="btn btn-sm" onClick={() => setVastAccountEdit(false)}>
                                {t('cloud.control.vastGuide.keepKey')}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                    {vastAccount && !vastAccount.balance_ok && (
                      <p className="mt-1 text-[color:var(--color-warn)]">{t('cloud.control.setupNoCredit')}</p>
                    )}
                  </ChecklistItem>

                  {/* 2 — GPU */}
                  <ChecklistItem
                    n={2}
                    title={t('cloud.control.vastGuide.gpuTitle')}
                    state={gpuState}
                    stateLabel={
                      gpuDone && vastSelected
                        ? vastSelected.label
                        : vastWaitingId !== null || vastInstances.some((inst) => !inst.is_running && /load|creat|unknown/i.test(inst.status))
                          ? t('cloud.control.vastGuide.gpuStarting')
                          : !vastLoaded && accountDone
                            ? t('cloud.control.loading')
                            : t('cloud.control.vastGuide.gpuTodo')
                    }
                  >
                    {accountDone && (
                      <div className="space-y-2">
                        {!vastLoaded && <Progress indeterminate label={t('cloud.control.loading')} />}
                        {vastWaitingId !== null && (
                          <div>
                            <Progress indeterminate label={t('cloud.control.vastGuide.gpuStarting')} />
                            <p className="mt-1 text-[11px]">{t('cloud.control.vastGuide.gpuStartingBody', { id: String(vastWaitingId) })}</p>
                          </div>
                        )}
                        {vastInstances.length > 0 ? (
                          <div className="divide-y divide-[color:var(--color-rule)] border border-[color:var(--color-rule)]">
                            {vastInstances.map((inst) => {
                              const chosen = String(inst.id) === String(vastSelected?.id)
                              return (
                                <div
                                  key={inst.id}
                                  className={`flex flex-wrap items-center gap-3 p-2.5 ${chosen ? 'bg-[color:var(--color-sig-wash)]' : 'bg-[color:var(--color-sheet)]'}`}
                                >
                                  {vastInstances.length > 1 && (
                                    <input
                                      type="radio"
                                      name="vast-instance"
                                      checked={chosen}
                                      onChange={() => selectInstance(inst)}
                                      aria-label={t('cloud.control.vastGuide.useInstance', { id: String(inst.id) })}
                                    />
                                  )}
                                  <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <span className="font-bold text-[color:var(--color-ink)]">{inst.label}</span>
                                      <Badge tone={statusTone(inst.is_running, inst.status)}>{statusLabel(inst.status)}</Badge>
                                      {inst.dph_total != null && (
                                        <span className="mono text-[11px] font-semibold text-[color:var(--color-ink)]">${inst.dph_total.toFixed(3)}/h</span>
                                      )}
                                      {inst.cost_estimate && (
                                        <span className="mono text-[11px]">
                                          {t('cloud.control.costSoFar', { cost: inst.cost_estimate.estimated_usd.toFixed(2) })}
                                        </span>
                                      )}
                                    </div>
                                    <div className="mono mt-0.5 text-[11px] text-[color:var(--color-ink-3)]">
                                      ID {inst.id}
                                      {effectiveEndpoint(inst) && ` · SSH ${effectiveEndpoint(inst)!.host}:${effectiveEndpoint(inst)!.port}`}
                                      {inst.ssh_via === 'proxy' && ` · ${t('cloud.control.sshViaProxy')}`}
                                    </div>
                                  </div>
                                  <div className="flex flex-wrap gap-2">
                                    {inst.is_running ? (
                                      <button type="button" onClick={() => void handleControlVast(inst.id, 'stop')} disabled={vastBusy} className="btn btn-sm" title={t('cloud.control.pauseTitle')}>
                                        {t('cloud.control.pause')}
                                      </button>
                                    ) : (
                                      <button type="button" onClick={() => void handleControlVast(inst.id, 'start')} disabled={vastBusy} className="btn btn-sm btn-primary">
                                        {t('cloud.control.resume')}
                                      </button>
                                    )}
                                    <button type="button" onClick={() => void handleControlVast(inst.id, 'delete')} disabled={vastBusy} className="btn btn-sm btn-danger">
                                      {t('cloud.control.deleteResource')}
                                    </button>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        ) : (
                          vastLoaded && <p>{t('cloud.control.vastGuide.noInstances')}</p>
                        )}
                        {vastInstances.length > 0 ? (
                          <details>
                            <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                              {t('cloud.control.vastGuide.rentAnother')}
                            </summary>
                            {searchForm}
                          </details>
                        ) : (
                          vastLoaded && searchForm
                        )}
                        {vastNeedsDirectOverride && <p className="text-[11px] text-[color:var(--color-warn)]">{t('cloud.control.vastGuide.proxyHint')}</p>}
                      </div>
                    )}
                  </ChecklistItem>

                  {/* 3 — Server */}
                  <ChecklistItem
                    n={3}
                    title={t('cloud.control.vastGuide.serverTitle', { model: focusModelLabel || recipe?.hf_repo || vastAdapter })}
                    state={serverState}
                    stateLabel={
                      !gpuDone
                        ? t('cloud.control.vastGuide.waitingGpu')
                        : serverDone
                          ? t('cloud.control.phaseReady')
                          : vastProbing
                            ? t('cloud.control.vastGuide.checking')
                            : preparing
                              ? phaseLabel
                              : vastPhase === 'failed'
                                ? t('cloud.control.phaseFailed')
                                : t('cloud.control.vastGuide.serverTodo')
                    }
                  >
                    {gpuDone && vastSelected && (
                      <div className="space-y-2">
                        {!guided && (
                          <div className="grid gap-2 sm:grid-cols-2">
                            <Field label={t('cloud.control.modelLabel')} hint={t('cloud.control.modelHint')}>
                              <select value={vastAdapter} onChange={(e) => setVastAdapter(e.target.value)} className="fld fld-mono" disabled={preparing}>
                                {vastModels.map((item) => (
                                  <option key={item.adapter_id} value={item.adapter_id}>{item.hf_repo}</option>
                                ))}
                              </select>
                            </Field>
                            <Field label={t('cloud.control.modelCustom')} hint={t('cloud.control.modelCustomHint')}>
                              <input value={vastModelCustom} onChange={(e) => setVastModelCustom(e.target.value)} placeholder="org/checkpoint" className="fld fld-mono" disabled={preparing} />
                            </Field>
                          </div>
                        )}
                        {recipe?.needs_own_image && (
                          <p className="text-[11px]">{t('cloud.control.modelOwnImage', { image: recipe.docker_image })}</p>
                        )}
                        {preparing ? (
                          <div>
                            <Progress indeterminate label={phaseLabel} />
                            <p className="mt-1 text-[11px]">{t('cloud.control.vastGuide.preparingBody')}</p>
                          </div>
                        ) : serverDone ? (
                          <p>{t('cloud.control.vastGuide.serverReadyBody')}</p>
                        ) : (
                          <div>
                            {vastOtherModel && (
                              <p className="mb-1 text-[color:var(--color-warn)]">
                                {t(`cloud.control.vastGuide.otherModel.${vastOtherModel.state}`, { model: vastOtherModel.adapterId })}
                              </p>
                            )}
                            {!vastProbing && <p>{t(customPublish ? 'cloud.control.vastGuide.publishBody' : 'cloud.control.vastGuide.serverBody')}</p>}
                            <button
                              type="button"
                              className="btn btn-primary mt-2"
                              disabled={vastBusy || tunnelBusy || vastProbing}
                              onClick={() => void (customPublish ? handlePublishVast(vastSelected) : handleProvisionVast(vastSelected))}
                            >
                              {vastBusy
                                ? t('cloud.control.setupRunning')
                                : customPublish
                                  ? t('cloud.control.publishCheckpoint')
                                  : vastPhase === 'failed'
                                    ? t('cloud.control.vastGuide.retry')
                                    : t('cloud.control.vastGuide.prepareCta')}
                            </button>
                          </div>
                        )}
                        {vastProvisionLog.length > 0 && (
                          <details open={preparing || (vastPhase === 'failed' && !vastOtherModel)}>
                            <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
                              {t('cloud.control.provisionLogLabel')}
                            </summary>
                            <pre className="mono mt-1 max-h-56 overflow-auto whitespace-pre-wrap border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] leading-[1.5]">
                              {vastProvisionLog.join('\n')}
                            </pre>
                          </details>
                        )}
                      </div>
                    )}
                  </ChecklistItem>

                  {/* 4 — Connessione */}
                  <ChecklistItem
                    n={4}
                    title={t('cloud.control.vastGuide.connTitle')}
                    state={connState}
                    stateLabel={
                      inferenceOk
                        ? t('cloud.control.inferenceOn')
                        : tunnelState.running
                          ? t('cloud.control.connectionChecking')
                          : t('cloud.control.tunnelClosed')
                    }
                  >
                    {serverDone && (
                      inferenceOk ? (
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="mono text-[11px]">{localUrl}</span>
                          <Link to="/" className="btn btn-primary no-underline">{t('modelsHub.goRecognize')}</Link>
                          <button type="button" className="btn btn-sm" disabled={tunnelBusy} onClick={() => void handleStopTunnel()}>
                            {t('cloud.control.vastGuide.disconnect')}
                          </button>
                        </div>
                      ) : tunnelState.running ? (
                        <div>
                          <Progress indeterminate label={t('cloud.control.connectionChecking')} />
                          <p className="mt-1 text-[11px]">{t('cloud.control.endpointVerifying', { url: localUrl })}</p>
                        </div>
                      ) : (
                        <div>
                          <p>{t('cloud.control.vastGuide.connBody')}</p>
                          <button
                            type="button"
                            className="btn btn-primary mt-2"
                            disabled={tunnelBusy || !vastSelected}
                            onClick={() => vastSelected && void handleConnectVast(vastSelected)}
                          >
                            {tunnelBusy ? t('cloud.control.starting') : t('cloud.control.connect')}
                          </button>
                        </div>
                      )
                    )}
                  </ChecklistItem>
                </ol>

                {/* Le vie d'uscita per quando la via automatica non basta:
                    endpoint diretto (Vast.ai non pubblica l'IP), tunnel a
                    mano, runner fissato a un altro commit. */}
                <Collapsible
                  key={vastNeedsDirectOverride ? 'adv-open' : 'adv'}
                  tab={t('cloud.control.vastGuide.advanced')}
                  quiet
                  defaultOpen={vastNeedsDirectOverride}
                >
                  <div className="space-y-4">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <p className="text-[12px] font-semibold sm:col-span-2">{t('cloud.control.directSshTitle')}</p>
                      <p className="text-[12px] text-[color:var(--color-ink-2)] sm:col-span-2">{t('cloud.control.directSshHint')}</p>
                      <Field label={t('cloud.control.directHostLabel')} hint={t('cloud.control.directHostHint')}>
                        <input value={vastDirectHost} onChange={(e) => setVastDirectHost(e.target.value)} placeholder="173.239.92.155" className="fld fld-mono" />
                      </Field>
                      <Field label={t('cloud.control.directPortLabel')} hint={t('cloud.control.directPortHint')}>
                        <input value={vastDirectPort} onChange={(e) => setVastDirectPort(e.target.value)} placeholder="41934" className="fld fld-mono" />
                      </Field>
                      <div className="flex items-end gap-2 sm:col-span-2">
                        <button type="button" onClick={() => void handleCheckDirectSsh()} disabled={vastDirectChecking || !vastDirectHost.trim() || !vastDirectPort.trim()} className="btn btn-sm">
                          {vastDirectChecking ? t('cloud.control.sshChecking') : t('cloud.control.sshCheckRun')}
                        </button>
                        <button type="button" onClick={() => { setVastDirectHost(''); setVastDirectPort('') }} disabled={vastDirectChecking || (!vastDirectHost.trim() && !vastDirectPort.trim())} className="btn btn-sm">
                          {t('cloud.control.directSshClear')}
                        </button>
                      </div>
                    </div>
                    <div className="grid gap-3 border-t border-[color:var(--color-rule)] pt-3 sm:grid-cols-2">
                      <p className="text-[12px] font-semibold sm:col-span-2">{t('cloud.control.manualTunnelTitle')}</p>
                      <p className="text-[12px] text-[color:var(--color-ink-2)] sm:col-span-2">{t('cloud.control.manualTunnelHint')}</p>
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
                    {showMonkeyRunner && (
                      <div className="border-t border-[color:var(--color-rule)] pt-3">
                        <Field label={t('cloud.control.monkeyRefLabel')} hint={t('cloud.control.monkeyRefHint')}>
                          <input value={vastMonkeyRef} onChange={(e) => setVastMonkeyRef(e.target.value)} className="fld fld-mono" />
                        </Field>
                      </div>
                    )}
                  </div>
                </Collapsible>
              </div>
            )
          })()}
        </Section>}

        {/* --- RunPod --- */}
        {shows('runpod') && <Section tab={t('cloud.control.tabRunpod')} defaultOpen={initialProvider === 'runpod'}>
          <div className="space-y-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.runpodBody')}</p>

            {guided && focusProvider === 'runpod' && (
              <div className="border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet-dim)] p-2 text-[12px]">
                <span className="lbl">{t('cloud.control.modelLabel')}</span>
                <div className="mt-1 font-semibold">{focusModelLabel || focusAdapterId}</div>
                <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">{t('cloud.control.guidedModelHint')}</p>
              </div>
            )}

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
        </Section>}

        {/* --- Modal serverless --- */}
        {shows('modal') && <Section
          tab={t('cloud.control.tabModal')}
          // Si apre anche se la template guardata l'ultima volta (deploy in
          // corso o appena fatto) non è quella attiva per l'inferenza: non
          // deve sparire dalla vista a un refresh (v. commento su modalTemplate).
          defaultOpen={initialProvider === 'modal' || effectiveModalTemplate !== 'monkeyocrv2'}
          aux={<Badge tone={modalStatus?.token ? 'ok' : 'neutral'}>{modalStatus?.token ? t('cloud.control.modalTokenOk') : t('cloud.control.modalTokenMissing')}</Badge>}
        >
          <div className="space-y-3">
            <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('cloud.control.modalBody')}</p>

            <div>
              <span className="lbl !mb-1">{t('cloud.control.modalModelLabel')}</span>
              <div className="flex flex-wrap gap-1.5">
                {(guided
                  ? MODAL_TEMPLATES.filter((tpl) => tpl.id === (TEMPLATE_BY_ADAPTER[focusAdapterId ?? ''] ?? 'monkeyocrv2'))
                  : MODAL_TEMPLATES
                ).map((tpl) => (
                  <button
                    key={tpl.id}
                    type="button"
                    onClick={() => setModalTemplate(tpl.id)}
                    disabled={!!runningTask || publishingCheckpoint}
                    className={`btn btn-sm ${effectiveModalTemplate === tpl.id ? 'btn-primary' : ''}`}
                  >
                    {tpl.label}
                  </button>
                ))}
              </div>
            </div>

            {effectiveModalTemplate === 'paddleocr-vl' && (
              <p className="border border-[color:var(--color-warn-rule)] bg-[color:var(--color-warn-wash)] p-2 text-[12px] text-[color:var(--color-warn)]">
                {t('cloud.control.modalPaddleCaveat')}
              </p>
            )}
            {effectiveModalTemplate === 'monkeyocrv2' && (
              <p className="border border-[color:var(--color-ok)] bg-[color:var(--color-ok-wash)] p-2 text-[12px] text-[color:var(--color-ok)]">
                {t('cloud.control.modalMonkeyPerformance')}
              </p>
            )}
            {effectiveModalTemplate === 'mineru' && (
              <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[12px] text-[color:var(--color-ink-2)]">
                {t('cloud.control.modalMineruCaveat')}
              </p>
            )}
            {effectiveModalTemplate === 'unlimited-ocr' && (
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
                  <button type="button" onClick={() => void (publishingCheckpoint ? handleModalPublish() : handleModalDeploy())} disabled={modalBusy} className="btn btn-primary">
                    {publishingCheckpoint
                      ? t('cloud.control.modalPublishCheckpoint')
                      : modalStatus.endpoint ? t('cloud.control.modalRedeploy') : t('cloud.control.modalDeploy')}
                  </button>
                  {!modalStatus.endpoint && !publishingCheckpoint && (
                    <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('cloud.control.modalDeployHint')}</p>
                  )}
                  {publishingCheckpoint && (
                    <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('cloud.control.modalPublishCheckpointHint')}</p>
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
        </Section>}

        {/* --- Manuale --- */}
        {shows('manual') && <Section tab={t('cloud.control.tabManual')} defaultOpen={initialProvider === 'manual'}>
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
        </Section>}
      </div>
  )

  if (inline) return body
  return (
    <Modal title={t('cloud.control.title')} onClose={onClose} wide>
      {body}
    </Modal>
  )
}
