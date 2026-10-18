import { useEffect, useRef, useState } from 'react'
import { useFitOneRow } from '../lib/fitRow'
import { Link, useSearchParams } from 'react-router'
import { ApiError, apiDelete, apiGet, apiPatch, apiPost, apiPut } from '../lib/api'
import { loadImageSize, PixelSize, scaleRatio } from '../lib/coords'
import type { BlockOut, LabelDef, PageItem, PageSums, RecognitionRun } from '../lib/types'
import StudioCanvas from '../studio/StudioCanvas'
import PageSidebar from '../studio/components/PageSidebar'
import ContentPane from '../studio/components/ContentPane'
import NewLabelDialog from '../studio/components/NewLabelDialog'
import Splitter from '../studio/components/Splitter'
import { clamp } from '../studio/canvasGeometry'
import PageWorkControl from '../studio/components/PageWorkControl'
import { useDraftEdits } from '../studio/useDraftEdits'
import { useAnnotationState } from '../studio/useAnnotationState'
import { runPrelabelStream } from '../studio/prefillStream'
import PrefillDialog from '../studio/components/PrefillDialog'
import {
  summarizePage,
  type PrefillMode,
} from '../studio/prefill'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useConfirm } from '../app/confirm'
import { connectPage, disconnectPage, notifyEditing, onAnnotationsChanged, useLive } from '../app/live'
import { initialsOf } from '../lib/avatar'
import type { DisplayBlock, LivePrefillOutput, PrefillDraft, Tool } from '../studio/types'
import type { PrefillEngines, TableDetectOut, TableDetectRequest, TableGrid, TableSaveOut } from '../lib/types'
import { useI18n, tn } from '../i18n'
import {
  IconNext,
  IconCheck,
  IconFlow,
  IconHand,
  IconPrefill,
  IconPolygon,
  IconPlus,
  IconRectangle,
  IconRedo,
  IconSave,
  IconSelect,
  IconTrash,
  IconUndo,
} from '../app/icons'

import { useInference } from '../app/inference'
import { useModelNames } from '../app/models/names'
import { ErrorNotice, Menu, PageHeader } from '../app/ui'
import { pageLabel } from '../lib/pageLabel'

// --- larghezze dei pannelli dello studio (splitter, persistite) --------------
// La scelta segue l'utente tra le sessioni; un valore corrotto o assente
// ricade sulla misura di riposo.
const SPLIT_KEY = 'tabularium.studio.split'
const MODE_KEY = 'tabularium.studio.mode'
const SPLIT_DEFAULT = { sidebar: 208, content: 420 }
const SIDEBAR_MIN = 176
const SIDEBAR_MAX = 420
const CONTENT_MIN = 320
type StudioMode = 'all' | 'canvas' | 'transcription'

function loadStudioMode(): StudioMode {
  try {
    const value = localStorage.getItem(MODE_KEY)
    return value === 'canvas' || value === 'transcription' ? value : 'all'
  } catch {
    return 'all'
  }
}

/** Bozza di prefill: generata e non ancora confermata da un umano. */
function isPendingDraft(b: BlockOut): boolean {
  return Boolean(b.prefill_source) && !b.confirmed
}

/**
 * Blocchi che stanno sul canvas.
 *
 * Le bozze del prefill non verificate restano fuori dall'immagine: sullo
 * schermo non deve comparire nulla di generato. Le TABELLE sono l'eccezione,
 * e per una ragione misurata: la regione di una tabella è ciò che il modello
 * sbaglia più spesso, e senza riquadro sull'immagine non c'è modo di
 * correggerla (i confini della griglia vivono DENTRO il bbox del blocco).
 */
function mapCanvasBlocks(items: BlockOut[], r: number): DisplayBlock[] {
  return items
    .filter((b) => !isPendingDraft(b) || b.label === 'Table')
    .map((b) => ({
      id: `srv-${b.id}`,
      serverId: b.id,
      label: b.label,
      kind: b.kind,
      points: b.points.map(([x, y]) => ({ x: x * r, y: y * r })),
      content: b.content,
      orderIdx: b.order_idx,
      confirmed: b.confirmed,
      prefill: b.prefill_source ?? null,
    }))
}

/** Bozze di prefill che vivono nel pannello contenuti (mai sull'immagine). */
function mapDrafts(items: BlockOut[]): PrefillDraft[] {
  return items
    .filter((b) => isPendingDraft(b) && b.label !== 'Table')
    .map((b) => ({ serverId: b.id, label: b.label, content: b.content, confirmed: false, grid: null }))
}

function loadSplit(): { sidebar: number; content: number } {
  try {
    const raw = localStorage.getItem(SPLIT_KEY)
    if (!raw) return SPLIT_DEFAULT
    const parsed = JSON.parse(raw) as { sidebar?: number; content?: number }
    return {
      sidebar: clamp(Number(parsed.sidebar) || SPLIT_DEFAULT.sidebar, SIDEBAR_MIN, SIDEBAR_MAX),
      content: clamp(Number(parsed.content) || SPLIT_DEFAULT.content, CONTENT_MIN, 900),
    }
  } catch {
    return SPLIT_DEFAULT
  }
}

export default function AnnotationPage() {
  const { t } = useI18n()
  const confirm = useConfirm()
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedProject = Number(searchParams.get('project')) || null
  const requestedPage = Number(searchParams.get('page')) || null
  const requestedRun = Number(searchParams.get('run')) || null
  const inf = useInference()
  const nameOf = useModelNames()
  const [projectId, setProjectId] = useState<number | ''>('')
  // Somme che non tornano, per pagina: la lista le mostra accanto allo stato.
  // Si rileggono al cambio di progetto o di pagina e dopo ogni salvataggio
  // di tabella; il server ricalcola solo le tabelle cambiate.
  const [sums, setSums] = useState<PageSums>({})
  const sumsFor = useRef<number | ''>('')
  const loadSums = (pid: number | '') => {
    sumsFor.current = pid
    if (pid === '') return setSums({})
    apiGet<{ pages: PageSums }>(`/projects/${pid}/sum-failures`)
      .then((out) => { if (sumsFor.current === pid) setSums(out.pages ?? {}) })
      .catch(() => { if (sumsFor.current === pid) setSums({}) })
  }
  const [pages, setPages] = useState<PageItem[]>([])
  const [pagesLoading, setPagesLoading] = useState(false)
  const [reviewRun, setReviewRun] = useState<RecognitionRun | null>(null)
  const [page, setPage] = useState<PageItem | null>(null)
  const [labels, setLabels] = useState<LabelDef[]>([])
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewSize, setPreviewSize] = useState<PixelSize>({ w: 0, h: 0 })
  const [error, setError] = useState<unknown>(null)
  const pageIdRef = useRef<number | null>(null)
  const [prefillBusy, setPrefillBusy] = useState(false)
  const [prefillProgress, setPrefillProgress] = useState<{ blocks: number; last: string | null } | null>(
    null,
  )
  // Istante di partenza del prefill corrente (ref: non deve causare render).
  const workingStartedAt = useRef(Date.now())
  // Risultati del prefill da revisionare nel pannello: testi formattati,
  // mai riquadri sull'immagine. Restano finché revisionati o cambi pagina.
  const [prefillDrafts, setPrefillDrafts] = useState<PrefillDraft[]>([])
  const [liveOutput, setLiveOutput] = useState<LivePrefillOutput | null>(null)
  const [showFlow, setShowFlow] = useState(false)
  const prefillAbortRef = useRef<AbortController | null>(null)

  /** Modifica al testo di una bozza: aggiorna il pannello subito e il
   *  database con debounce (le bozze non sono nel canvas). */
  const onDraftContent = (serverId: number, content: string) => {
    setPrefillDrafts((d) => d.map((x) => x.serverId === serverId ? { ...x, content } : x))
    draftEdits.schedule(serverId, content)
  }
  /** Verifica una bozza: da questo momento il blocco esiste anche sul canvas
   *  (filtro in applyBlocks) — è il gesto che trasforma output in annotazione. */
  const onDraftConfirmed = async (serverId: number, confirmed: boolean) => {
    setPrefillDrafts((d) => d.map((x) => (x.serverId === serverId ? { ...x, confirmed } : x)))
    try {
      await draftEdits.flush()
      const out = await ann.runExternal(() => apiPatch<BlockOut>(`/blocks/${serverId}?expected_revision=${ann.getRevision()}`, { confirmed }))
      ann.syncRevision(out.annotation_revision)
      if (page) await applyBlocks(page, ratio)
    } catch (e) {
      setError(e)
    }
  }
  /** Cambia la classe di una bozza: il pannello la aggiorna subito e la
   *  classe si persiste sul blocco (la bozza non è sul canvas). */
  const onDraftLabel = (serverId: number, label: string) => {
    void (async () => {
      await draftEdits.flush()
      await ann.runExternal(() => apiPatch<BlockOut>(`/blocks/${serverId}?expected_revision=${ann.getRevision()}`, { label }))
      setPrefillDrafts((d) => d.map((x) => x.serverId === serverId ? { ...x, label } : x))
      if (label === 'Table' && page) await applyBlocks(page, ratio)
    })().catch(setError)
  }
  const onDraftGrid = (serverId: number, grid: TableGrid) => {
    setPrefillDrafts((drafts) => drafts.map((draft) => (draft.serverId === serverId ? { ...draft, grid } : draft)))
  }
  const onSaveDraftGrid = async (serverId: number, grid: TableGrid): Promise<string> => {
    const out = await ann.runExternal(() => apiPut<TableSaveOut>(`/blocks/${serverId}/table?expected_revision=${ann.getRevision()}`, grid))
    ann.syncRevision(out.annotation_revision)
    loadSums(projectId)
    return out.otsl
  }
  /** Scarta una bozza non verificata: sparisce dal pannello e dal server —
   *  non è mai stata sul canvas, quindi non c'è nulla da annullare lì. */
  const onDraftReject = async (serverId: number) => {
    try {
      await draftEdits.flush()
      await ann.runExternal(() => apiDelete<{ annotation_revision: number }>(`/blocks/${serverId}?expected_revision=${ann.getRevision()}`))
      setPrefillDrafts((d) => d.filter((x) => x.serverId !== serverId))
    } catch (e) { setError(e) }
  }
  const [prefillEngine, setPrefillEngine] = useState<
    'off' | 'ocr' | 'model-native'
  >('ocr')
  // Un solo comando: il backend risolve il workflow nativo del modello attivo
  // (che può avere uno o più passaggi secondo il produttore).
  const modelMode = 'native' as const
  const [engines, setEngines] = useState<PrefillEngines | null>(null)
  const prefillConfidence = 0.5
  const [prefillNotice, setPrefillNotice] = useState<string | null>(null)
  // Il dialog di conferma: il prefill può cancellare lavoro esistente, quindi
  // non parte mai direttamente quando la pagina contiene blocchi.
  const [prefillOpen, setPrefillOpen] = useState(false)
  const [navBusy, setNavBusy] = useState(false)
  const [nextTask, setNextTask] = useState<{ id: number; reason: string; rel_path?: string } | null>(null)

  // --- larghezze pannelli ------------------------------------------------------
  const [split, setSplit] = useState(loadSplit)
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth)
  useEffect(() => {
    const resize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [])
  const sidebarWidth = Math.min(split.sidebar, Math.max(SIDEBAR_MIN, viewportWidth * 0.22))
  const contentWidth = Math.min(split.content, Math.max(CONTENT_MIN, viewportWidth - sidebarWidth - 320))
  const [studioMode, setStudioMode] = useState<StudioMode>(loadStudioMode)
  const setMode = (mode: StudioMode) => {
    setStudioMode(mode)
    try {
      localStorage.setItem(MODE_KEY, mode)
    } catch {
      // Storage bloccato: la modalità resta valida per la sessione.
    }
  }
  const setSplitPane = (pane: 'sidebar' | 'content', value: number) =>
    setSplit((cur) => {
      const next = { ...cur, [pane]: Math.round(value) }
      try {
        localStorage.setItem(SPLIT_KEY, JSON.stringify(next))
      } catch {
        // Storage bloccato (es. navigazione privata): la scelta vale per la sessione.
      }
      return next
    })
  const resetSplitPane = (pane: 'sidebar' | 'content') => setSplitPane(pane, SPLIT_DEFAULT[pane])
  /** Il pannello contenuti non può mangiare più della metà della finestra. */
  const contentMax = () => Math.max(CONTENT_MIN, Math.round(viewportWidth - sidebarWidth - 320))

  const ratio =
    page && previewSize.w > 0
      ? scaleRatio(previewSize, { w: page.width, h: page.height })
      : 1

  const ann = useAnnotationState(page, ratio, labels, !navBusy && !prefillBusy)
  const draftEdits = useDraftEdits((id, content) => ann.runExternal(() => apiPatch<BlockOut>(`/blocks/${id}?expected_revision=${ann.getRevision()}`, { content })), (e) => setError(e))
  const flushEdits = async () => { await draftEdits.flush(); await ann.flush() }


  // Collaborazione live: presenza e avviso di modifiche altrui.
  const live = useLive()
  const [remoteEdit, setRemoteEdit] = useState<{ username: string; revision: number } | null>(null)

  const appliedRevision = ann.getRevision()
  useEffect(() => {
    // L'eco WebSocket può precedere la risposta HTTP: appena la risposta
    // conferma la stessa revisione non è più un conflitto remoto.
    if (remoteEdit?.revision === appliedRevision) setRemoteEdit(null)
  }, [remoteEdit?.revision, appliedRevision])

  // «Verificata» spuntata nella finestra della tabella: è già sul server,
  // qui si riallinea solo lo stato (un blocco del canvas o una bozza).
  const verifiedElsewhereRef = useRef<(serverId: number, confirmed: boolean) => void>(() => {})
  verifiedElsewhereRef.current = (serverId, confirmed) => {
    const block = ann.blocks.find((b) => b.serverId === serverId)
    if (block) {
      if (block.confirmed !== confirmed) ann.setBlockConfirmed(block.id, confirmed)
      return
    }
    if (!prefillDrafts.some((d) => d.serverId === serverId)) return
    setPrefillDrafts((d) => d.map((x) => (x.serverId === serverId ? { ...x, confirmed } : x)))
    if (page) void applyBlocks(page, ratio)
  }

  // Il foglio può essere aperto in un'altra scheda/finestra. Le sue scritture
  // avanzano la revisione della pagina: riallinea l'autosave qui per evitare
  // un 409 alla prossima modifica del canvas.
  useEffect(() => {
    if (!('BroadcastChannel' in window)) return
    const channel = new BroadcastChannel('tabularium.annotation-revision')
    channel.onmessage = ({ data }) => {
      if (data?.pageId === pageIdRef.current && Number.isInteger(data?.revision)) {
        ann.syncRevision(data.revision)
        if (Number.isInteger(data.blockId) && typeof data.confirmed === 'boolean') {
          verifiedElsewhereRef.current(data.blockId, data.confirmed)
        }
      }
    }
    return () => channel.close()
  }, [ann.syncRevision])

  // --- carica blocchi dal server e li applica allo stato -----------------------
  const applyBlocks = async (p: PageItem, r: number) => {
    const rep = await apiGet<{ items: BlockOut[]; annotation_revision?: number }>(`/pages/${p.id}/annotations`)
    // Le bozze del prefill non verificate restano fuori dal canvas: sull'immagine
    // non deve comparire nulla di generato. Vivono nel pannello contenuti
    // (ContentPane → `drafts`) finché l'utente non le verifica.
    //
    // Le TABELLE sono l'eccezione (v. `mapCanvasBlocks`): la loro regione va
    // corretta sull'immagine.
    ann.reset(
      mapCanvasBlocks(rep.items, r),
      rep.annotation_revision ?? p.annotation_revision ?? 0,
    )
    // Il backend scrive le bozze in un thread indipendente dalla connessione:
    // un prefill avviato e poi abbandonato (cambio pagina, altra scheda) continua
    // e finisce comunque. Ricaricandole qui, invece di fidarsi solo dello stream
    // dal vivo, sopravvivono alla navigazione — si ritrovano al ritorno sulla
    // pagina anche se nessuno ha guardato lo stream fino alla fine.
    setPrefillDrafts(mapDrafts(rep.items))
  }

  /** Riallinea il canvas alla versione del server (canale live). Non azzera il
   *  lavoro locale: lo decide chi chiama (adotta solo se non ci sono modifiche
   *  non salvate). */
  const refreshRemote = async (p: PageItem, r: number) => {
    const rep = await apiGet<{ items: BlockOut[]; annotation_revision?: number }>(`/pages/${p.id}/annotations`)
    ann.adoptRemote(
      mapCanvasBlocks(rep.items, r),
      rep.annotation_revision ?? p.annotation_revision ?? 0,
    )
    setPrefillDrafts(mapDrafts(rep.items))
    setRemoteEdit(null)
  }

  const reloadRemote = async () => {
    if (!page) return
    if ((ann.getDirty() || draftEdits.dirty) && !await confirm({ title: t('work.reloadTitle'), message: t('work.reloadHint') })) return
    await draftEdits.discard()
    try { await applyBlocks(page, ratio); setRemoteEdit(null) } catch (e) { setError(e) }
  }

  // --- canale live: presenza e riallineamento --------------------------------
  // L'effetto si aggancia (e si stacca) al cambio pagina; il gestore sta in un
  // ref perché dipende da stato che cambia a ogni render (revisione, sporco,
  // rapporto di scala) e non deve far riaprire il socket a ogni modifica.
  const liveHandlerRef = useRef<(ev: { pageId: number; revision: number; authorId: number | null; authorName: string | null }) => void>(() => {})
  liveHandlerRef.current = (ev) => {
    if (!page || ev.pageId !== page.id) return
    // Eco della nostra stessa modifica: la revisione combacia, nulla da fare.
    if (ev.revision === ann.getRevision()) return
    if (ann.getDirty()) {
      // C'è lavoro locale non salvato: non lo si butta. Si avvisa e si lascia
      // scegliere — adottare la versione remota scarta ciò che non è salvato.
      const peer = live.participants.find((p) => p.userId === ev.authorId)
      setRemoteEdit({ username: peer?.username ?? ev.authorName ?? t('live.someone'), revision: ev.revision })
      return
    }
    void refreshRemote(page, ratio).catch(() => {
      // Un errore di rete transitorio: il prossimo evento (o un refresh) riallinea.
    })
  }

  useEffect(() => {
    if (!page) {
      disconnectPage()
      return
    }
    connectPage(page.id)
    const off = onAnnotationsChanged((ev) => liveHandlerRef.current(ev))
    return () => {
      off()
      disconnectPage()
    }
  }, [page?.id])

  // «Sta scrivendo»: vero mentre ci sono modifiche non ancora salvate.
  useEffect(() => {
    if (!page) return
    notifyEditing(ann.dirty)
  }, [ann.dirty, page?.id])

  // --- pseudo-labeling OCR ----------------------------------------------------
  // Streaming: ogni blocco scritto dal backend arriva come evento SSE e
  // compare subito sul canvas e nel pannello — la risposta batch esiste
  // ancora per chi la vuole intera, ma l'attesa cieca no.
  const runPrelabel = async (mode: PrefillMode) => {
    if (!page || !projectId) return
    // Se l'utente cambia pagina mentre gira, la run prosegue sul server (thread
    // indipendente dalla connessione: v. applyBlocks) ma smette di toccare lo
    // stato React — altrimenti i suoi eventi arriverebbero addosso a un'altra
    // pagina, mischiando bozze e azzerando blocchi che non c'entrano nulla.
    const targetPageId = page.id
    const isCurrent = () => pageIdRef.current === targetPageId
    try { await flushEdits() } catch (e) { setError(e); return }
    setPrefillOpen(false)
    setPrefillBusy(true)
    workingStartedAt.current = Date.now()
    setPrefillProgress({ blocks: 0, last: null })
    setPrefillDrafts([])
    setLiveOutput(null)
    setError(null)
    setPrefillNotice(null)
    const abortController = new AbortController()
    prefillAbortRef.current = abortController
    let summary: Record<string, unknown> | null = null
    let streamError: string | null = null
    try {
      await runPrelabelStream({
        projectId,
        body: {
          page_ids: [page.id],
          mode,
          confidence: prefillConfidence,
          min_size: 10,
          engine: prefillEngine === 'ocr' ? 'ocr' : 'model',
          model_mode: modelMode,
        },
        signal: abortController.signal,
        onEvent: (ev) => {
          if (ev.type === 'start') {
            if (!isCurrent()) return
            // Ciò che le modalità replace hanno già cancellato lato server
            // non deve restare disegnato: il lavoro umano resta solo in
            // merge, e nelle bozze si mantiene ciò che non è una bozza.
            if (mode === 'replace_all') {
              ann.reset([])
            } else if (mode === 'replace_drafts') {
              ann.reset(
                ann.blocks.filter((b) => !(b.prefill && !b.confirmed)),
              )
            }
          } else if (ev.type === 'block') {
            if (!isCurrent()) return
            // Durante e dopo lo stream il canvas resta PULITO: l'output
            // nativo va nel pannello come testo formattato, non come
            // riquadri sull'immagine. Il blocco è già nel database.
            // Unica eccezione le tabelle: la loro regione va corretta
            // sull'immagine, quindi compaiono subito come riquadro non
            // verificato (stessa regola di `applyBlocks`).
            if (ev.block.label === 'Table') {
              ann.insertServerBlock({
                id: `srv-${ev.block.id}`,
                serverId: ev.block.id,
                label: ev.block.label,
                kind: 'rect',
                points: ev.block.points.map(([x, y]) => ({ x: x * ratio, y: y * ratio })),
                content: ev.block.content,
                orderIdx: ev.block.order_idx,
                confirmed: false,
                prefill: ev.block.prefill_source ?? null,
              })
            } else {
              setPrefillDrafts((d) => [
                ...d,
                {
                  serverId: ev.block.id,
                  label: ev.block.label,
                  content: ev.block.content,
                  confirmed: false,
                  grid: (ev.block.grid as TableGrid | null) ?? null,
                },
              ])
            }
            setPrefillProgress((p) =>
              p ? { blocks: p.blocks + 1, last: ev.block.label } : p,
            )
          } else if (ev.type === 'output') {
            if (!isCurrent()) return
            setLiveOutput((previous) => ({
              phase: ev.phase,
              text: `${previous?.text ?? ''}${ev.text}`,
            }))
          } else if (ev.type === 'output_reset') {
            if (!isCurrent()) return
            setLiveOutput({ phase: ev.phase, text: '' })
          } else if (ev.type === 'page_done') {
            summary = ev.summary
          } else if (ev.type === 'error') {
            streamError = ev.message
          }
        },
      })
    } catch (e) {
      if (isCurrent()) {
        if (abortController.signal.aborted) {
          setPrefillNotice(t('annotate.prefillStopped'))
        } else {
          setError(e)
        }
        setPrefillBusy(false)
        setPrefillProgress(null)
      }
      if (prefillAbortRef.current === abortController) prefillAbortRef.current = null
      return
    }
    if (prefillAbortRef.current === abortController) prefillAbortRef.current = null
    if (!isCurrent()) return
    if (streamError) setError(streamError)
    const s = summary as {
      inserted?: number
      replaced_blocks?: number
      deskew_angle?: number
      tables?: number
      grids?: number
      unmapped_labels?: Record<string, number>
    } | null
    if (s) {
      // L'avviso dice solo ciò che il pannello non mostra già da sé: quante
      // bozze siano arrivate e che siano da verificare si legge nelle righe,
      // ripeterlo in un riquadro sopra la pagina era rumore. Restano le due
      // cose che sono *successe* alla pagina e non si vedono altrove.
      const unmapped = Object.keys(s.unmapped_labels ?? {})
      const parts = [
        (s.replaced_blocks ?? 0) > 0 ? tn('annotate.prefillReplaced', s.replaced_blocks ?? 0) : null,
        s.deskew_angle && Math.abs(s.deskew_angle) >= 0.05
          ? t('annotate.deskewApplied', { angle: s.deskew_angle.toFixed(2) })
          : null,
        unmapped.length > 0
          ? `${t('annotate.unmappedLabels', { labels: unmapped.join(', ') })} ${t('annotate.unmappedLabelsHint')}`
          : null,
      ].filter(Boolean)
      setPrefillNotice(parts.length > 0 ? parts.join(' ') : null)
    }
    setPrefillBusy(false)
    setPrefillProgress(null)
    // Le bozze RESTANO nel pannello per la revisione: sull'immagine non
    // compare nulla (nessun applyBlocks — i blocchi non confermati non
    // entrano nel canvas, v. applyBlocks).
  }

  const stopPrelabel = () => {
    prefillAbortRef.current?.abort()
  }

  const clearPageAnnotations = async () => {
    if (!page || prefillBusy) return
    const ok = await confirm({
      title: t('annotate.clearAll'),
      message: `${t('annotate.clearAllConfirm')} ${t('annotate.clearAllCount', { n: prefillDrafts.length + ann.blocks.length })}`,
    })
    if (!ok) return
    setError(null)
    try {
      await draftEdits.flush()
      const out = await ann.runExternal(() => apiDelete<{ annotation_revision: number }>(`/pages/${page.id}/annotations?expected_revision=${ann.getRevision()}`))
      ann.reset([], out.annotation_revision)
      setPrefillDrafts([])
      setLiveOutput(null)
      setPrefillNotice(t('annotate.clearAllDone'))
    } catch (e) {
      setError(e)
    }
  }

  /** Etichetta leggibile del motore scelto, per il pannello di lavoro. */
  const workingEngineName = () =>
    prefillEngine === 'ocr'
      ? t('annotate.prefillEngineOcr')
      : t('annotate.prefillEngineModelNative')

  /** Il pulsante Prefill: con blocchi presenti apre il dialog di conferma,
   *  su pagina vuota le modalità sono equivalenti e si parte subito. */
  const onPrefillClick = () => {
    if (!page || prefillBusy) return
    setPrefillOpen(true)
  }

  // --- seleziona progetto -----------------------------------------------------
  const onProjectChange = async (pid: number | '') => {
    // Il lavoro sporco della pagina corrente va a dormire prima di lasciarla:
    // il cambio progetto smonta tutto lo stato, il debounce non basterebbe.
    try {
      await flushEdits()
    } catch (e) {
      setError(e)
      return
    }
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    // L'URL dice dove si lavora: un refresh o un link riaprono lo stesso
    // punto. Cambiare progetto lascia cadere pagina e sessione, che
    // appartenevano all'altro.
    if (pid !== requestedProject) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (pid === '') next.delete('project')
        else next.set('project', String(pid))
        next.delete('page')
        next.delete('run')
        return next
      }, { replace: true })
    }
    loadSums(pid)
    setPage(null)
    pageIdRef.current = null
    setPages([])
    setReviewRun(null)
    setPreviewUrl(null)
    ann.reset()
    if (pid === '') return
    setPagesLoading(true)
    try {
      const runPromise = requestedRun && (requestedProject == null || requestedProject === pid)
        ? apiGet<RecognitionRun>(`/projects/${pid}/recognition-runs/${requestedRun}${requestedPage ? `?result_page=${requestedPage}` : ''}`)
        : Promise.resolve(null)
      const [pr, pg, queue, scopedRun] = await Promise.all([
        apiGet<{ items: PageItem[] }>(`/projects/${pid}/pages`),
        apiGet<{ labels: LabelDef[] }>(`/projects/${pid}/labels`),
        apiGet<{ items: Array<{ id: number; reason: string; rel_path?: string }> }>(`/projects/${pid}/annotation-queue?limit=1`),
        runPromise,
      ])
      if (scopedRun) {
        const byId = new Map(pr.items.map((item) => [item.id, item]))
        setPages(
          (scopedRun.items ?? [])
            .map((item) => byId.get(item.page_id))
            .filter((item): item is PageItem => Boolean(item)),
        )
        setReviewRun(scopedRun)
      } else {
        setPages(pr.items)
      }
      setNextTask(queue.items[0] ?? null)
      setLabels(pg.labels)
      ann.setActiveLabel(pg.labels.some((l) => l.name === 'Text') ? 'Text' : pg.labels[0]?.name ?? 'Text')
    } catch (e) {
      setError(e)
    } finally {
      setPagesLoading(false)
    }
  }

  // Una run bulk è persistente e il backend salva ogni blocco appena viene
  // emesso.  Lo Studio osserva la stessa risorsa: così «Vedi nello studio»
  // diventa una superficie live, anche con una pagina ancora in lavorazione.
  useEffect(() => {
    if (!reviewRun || !requestedRun || !page) return
    let stopped = false
    const updateOutput = (run: RecognitionRun) => {
      const item = run.items?.find((candidate) => candidate.page_id === page.id)
      const blocks = item?.result?.blocks ?? []
      const text = blocks
        .map((block) => {
          const label = String(block.label ?? block.kind ?? '').trim()
          const content = String(block.content ?? '').trim()
          return content ? (label ? `[${label}]\n${content}` : content) : label
        })
        .filter(Boolean)
        .join('\n\n')
      setLiveOutput(text ? { phase: item?.state === 'finished' ? 'completed' : 'streaming', text } : null)
    }
    updateOutput(reviewRun)
    // La sessione arriva senza output grezzo (pesava megabyte): lo si chiede
    // per la sola pagina aperta, subito, e di nuovo a ogni giro se è viva.
    const tick = async () => {
      try {
        const next = await apiGet<RecognitionRun>(`/projects/${reviewRun.project_id}/recognition-runs/${reviewRun.id}?result_page=${page.id}`)
        if (!stopped) {
          setReviewRun(next)
          updateOutput(next)
        }
      } catch {
        // Il canvas resta utilizzabile anche durante un'interruzione breve
        // del polling; il risultato salvato riapparirà al tick successivo.
      }
    }
    void tick()
    if (reviewRun.state !== 'queued' && reviewRun.state !== 'running') {
      return () => {
        stopped = true
      }
    }
    const timer = window.setInterval(() => void tick(), 1200)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [reviewRun?.id, reviewRun?.state, page?.id, requestedRun])

  // --- motori di prefill disponibili -------------------------------------------
  // Il default non può essere fisso: `ocr` non riconosce le tabelle, `model` sì.
  // Se il server di inferenza risponde si parte da lui, così l'utente non
  // ricade sul motore peggiore per dimenticanza.
  useEffect(() => {
    let cancelled = false
    apiGet<PrefillEngines>('/system/prefill-engines')
      .then((out) => {
        if (cancelled) return
        setEngines(out)
        if (out.recommended === 'model' || out.recommended === 'ocr') {
          setPrefillEngine(out.recommended === 'model' ? 'model-native' : 'ocr')
        }
      })
      .catch(() => {
        // Nessun motore interrogabile: si resta sul default, il prefill
        // risponderà comunque con un messaggio chiaro.
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Ri-sonda quando la configurazione di inferenza cambia (deploy da Cloud,
  // cambio endpoint/adapter nelle Impostazioni): debounce per assorbire i save
  // multipli di una stessa configurazione. La sonda fallita lascia lo stato
  // precedente (il chip GPU in header è la fonte della verità visibile).
  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(() => {
      apiGet<PrefillEngines>('/system/prefill-engines')
        .then((out) => {
          if (cancelled) return
          setEngines(out)
          // Disabilita il prefill modello se l'adapter non ha ancora un
          // workflow del produttore integrato.
          setPrefillEngine((cur) => {
            if (cur === 'model-native' && out.model.supports_native === false) {
              return 'off'
            }
            return cur
          })
        })
        .catch(() => {})
    }, 800)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [inf.url, inf.enabled, inf.model])

  // --- stato GPU: ri-sonda finché il modello risulta offline -------------------
  // Modal a freddo (o in riavvio dopo un deploy) non risponde per minuti: la
  // sonda al mount marcarebbe "offline" per sempre. Finché il modello è
  // abilitato ma risulta non disponibile, si ri-sonda a intervalli — il pass-
  // aggio a online deve comparire da solo, senza costringere l'utente a
  // ricaricare la pagina.
  useEffect(() => {
    if (!inf.enabled || engines?.model.available !== false) return
    const id = setInterval(() => {
      apiGet<PrefillEngines>('/system/prefill-engines')
        .then(setEngines)
        .catch(() => {})
    }, 30_000)
    return () => clearInterval(id)
  }, [inf.enabled, engines?.model.available])

  // --- carica progetti e riapre subito quello attivo ---------------------------
  const projects = useProjects(
    (pid) => void onProjectChange(requestedProject ?? pid),
    (e) => setError(e),
  )

  // I risultati bulk aprono direttamente la pagina da verificare. Il link è
  // riproducibile e non dipende dal progetto ricordato nell'ultimo browser.
  useEffect(() => {
    if (
      requestedProject != null &&
      projectId !== requestedProject &&
      projects.some((project) => project.id === requestedProject)
    ) {
      void onProjectChange(requestedProject)
    }
  }, [requestedProject, projectId, projects])

  useEffect(() => {
    if (
      requestedPage != null &&
      page?.id !== requestedPage &&
      pages.some((candidate) => candidate.id === requestedPage) &&
      !navBusy
    ) {
      void onPageSelect(requestedPage)
    }
  }, [requestedPage, page?.id, pages, navBusy])

  useEffect(() => {
    if (!page && requestedPage == null && pages.length && !navBusy) {
      const next = pages.find((p) => p.work?.can_submit || p.work?.can_review) ?? pages[0]
      void onPageSelect(next.id)
    }
  }, [pages, page, requestedPage, navBusy])

  // --- seleziona pagina -------------------------------------------------------
  const onPageSelect = async (pid: number) => {
    if (!pages.some((x) => x.id === pid)) return
    loadSums(projectId)
    if (navBusy) return
    setNavBusy(true)
    try {
      // Prima di abbandonare la pagina si aspetta l'autosave: il timer con
      // debounce verrebbe cancellato dal cambio pagina e le ultime modifiche
      // resterebbero solo nel browser.
      await flushEdits()
    } catch (e) {
      setError(e); setNavBusy(false)
      return
    }
    const p = pages.find((x) => x.id === pid)
    if (!p) {
      setNavBusy(false)
      return
    }
    setPage(p)
    setRemoteEdit(null)
    pageIdRef.current = p.id
    // Senza, un refresh riportava lo Studio vuoto e la pagina aperta si perdeva.
    if (requestedPage !== p.id) {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        if (projectId !== '') next.set('project', String(projectId))
        next.set('page', String(p.id))
        return next
      }, { replace: true })
    }
    // Una run avviata sulla pagina precedente prosegue sul server ma non
    // riguarda più questa vista: si azzerano gli indicatori locali, i suoi
    // eventi restano ignorati (v. isCurrent in runPrelabel). Le bozze che
    // ha già scritto si ricaricano da applyBlocks, qui sotto, per QUESTA
    // pagina — non per quella abbandonata.
    setPrefillBusy(false)
    setPrefillProgress(null)
    setLiveOutput(null)
    setPrefillDrafts([])
    const url = `/api/pages/${pid}/preview`
    setPreviewUrl(url)
    try {
      const size = await loadImageSize(url)
      setPreviewSize(size)
      const r = size.w > 0 ? scaleRatio(size, { w: p.width, h: p.height }) : 1
      await applyBlocks(p, r)
    } catch (e) {
      setError(e)
    } finally {
      setNavBusy(false)
    }
  }

  // --- pannello contenuto: tabelle --------------------------------------------
  // La griglia la carica ContentPane alla selezione; qui restano solo le
  // chiamate al server, referenziate sul blocco selezionato al momento
  // dell'invocazione (il pannello le passa insieme al blocco).
  const saveTable = async (serverId: number, grid: TableGrid): Promise<string> => {
    const out = await ann.runExternal(() => apiPut<TableSaveOut>(`/blocks/${serverId}/table?expected_revision=${ann.getRevision()}`, grid))
    // Il server ha fatto avanzare la revisione della pagina: senza allinearla
    // il prossimo autosave del canvas verrebbe respinto con un 409 inventato.
    ann.syncRevision(out.annotation_revision)
    loadSums(projectId)
    return out.otsl
  }

  // DEPRECATO: `/table/detect` è il percorso geometrico (`services/table_detect.py`),
  // che i modelli non ci danno. Resta per «Rileva griglia» e il ri-rilevamento al
  // resize, ma la correzione model-agnostic passa dal foglio. Non estendere.
  const detectTable = (serverId: number, opts: TableDetectRequest): Promise<TableDetectOut> =>
    apiPost<TableDetectOut>(`/blocks/${serverId}/table/detect`, opts)

  // --- la regione è cambiata: la griglia va rifatta ----------------------------
  // I confini della griglia sono normalizzati 0–1 SUL RITAGLIO, cioè sul bbox
  // del blocco. Spostare o allargare il riquadro li lascia dov'erano in
  // frazione e quindi altrove sull'inchiostro: una griglia che sembra salva ma
  // non lo è più. Dopo un ridimensionamento si rilegge dunque la regione nuova.
  //
  // Il ri-riconoscimento è automatico solo finché non c'è lavoro umano da
  // perdere: se una cella trascritta è già stata verificata, la decisione
  // torna all'annotatore con il pulsante «Rileva» dell'editor, che chiede
  // conferma prima di sovrascrivere.
  const [tableRedetect, setTableRedetect] = useState<
    { serverId: number; state: 'busy' | 'done' | 'stale' | 'error'; message?: string } | null
  >(null)
  /** Contatore per ritaglio: forza il ricarico di immagine e griglia. */
  const [tableVersions, setTableVersions] = useState<Record<number, number>>({})
  const bumpTable = (serverId: number) =>
    setTableVersions((v) => ({ ...v, [serverId]: (v[serverId] ?? 0) + 1 }))
  const redetectTimers = useRef<Record<number, ReturnType<typeof setTimeout>>>({})

  const redetectTable = async (serverId: number, force = false) => {
    try {
      // Il rilevamento gira sul bbox salvato: senza flush leggerebbe il vecchio.
      await flushEdits()
      const current = await apiGet<{ grid: TableGrid | null }>(`/blocks/${serverId}/table`)
      const verified = (current.grid?.cells ?? []).some(
        (c) => (c.text ?? '').trim() !== '' && c.verified,
      )
      if (verified && !force) {
        bumpTable(serverId)
        setTableRedetect({ serverId, state: 'stale' })
        return
      }
      setTableRedetect({ serverId, state: 'busy' })
      // Con quale motore si rilegge. La regola è: **quello che ha scritto la
      // griglia**. Una tabella nata da PaddleOCR-VL riletta con l'OCR di riga
      // locale peggiora, e di molto: sono due modelli diversi, e il secondo
      // legge una cella alla volta senza vedere la tabella. Si scende all'OCR
      // locale solo se il modello non è servito, e in quel caso lo si dice.
      const source = ann.blocks.find((b) => b.serverId === serverId)?.prefill ?? ''
      const wantsModel = source.startsWith('model:')
      const fill: 'model' | 'ocr' | 'none' = wantsModel && engines?.model.available
        ? 'model'
        : engines?.ocr.available
          ? 'ocr'
          : 'none'
      const downgraded = wantsModel && fill !== 'model'
      const out = await detectTable(serverId, { fill })
      const saved = await apiPut<TableSaveOut>(`/blocks/${serverId}/table`, out.grid)
      ann.syncRevision(saved.annotation_revision)
      bumpTable(serverId)
      setTableRedetect({
        serverId,
        state: 'done',
        message:
          t('table.redetectDone', {
            rows: out.grid.rows,
            cols: out.grid.cols,
            fill: fill === 'none' ? t('table.redetectNoFill') : (out.ocr?.engine ?? fill),
          }) + (downgraded ? ` — ${t('table.redetectDowngraded')}` : ''),
      })
    } catch (e) {
      bumpTable(serverId)
      setTableRedetect({ serverId, state: 'error', message: String(e) })
    }
  }

  /** Ridimensionamento/spostamento di un blocco: per le tabelle trascina con sé
   *  la griglia. Il debounce evita una raffica di rilevamenti mentre si
   *  aggiusta il riquadro a più riprese. */
  const updateBlockPoints = (id: string, points: { x: number; y: number }[]) => {
    ann.updateBlockPoints(id, points)
    const block = ann.blocks.find((b) => b.id === id)
    if (!block || block.label !== 'Table' || !block.serverId) return
    const serverId = block.serverId
    setTableRedetect({ serverId, state: 'busy' })
    clearTimeout(redetectTimers.current[serverId])
    redetectTimers.current[serverId] = setTimeout(() => void redetectTable(serverId), 900)
  }

  // --- cancellazione blocco: toast con annullamento esplicito ------------------
  const [deleteToast, setDeleteToast] = useState<{ label: string } | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleDeleteBlock = (id: string) => {
    const b = ann.blocks.find((x) => x.id === id)
    ann.deleteBlock(id)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    setDeleteToast({ label: b?.label ?? t('layers.tab') })
    toastTimer.current = setTimeout(() => setDeleteToast(null), 6000)
  }

  const undoDelete = () => {
    ann.undo()
    if (toastTimer.current) clearTimeout(toastTimer.current)
    setDeleteToast(null)
  }

  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current)
    },
    [],
  )

  // --- creazione rapida di una classe (a livello di progetto) -------------------
  const [newLabelOpen, setNewLabelOpen] = useState(false)
  const [labelToast, setLabelToast] = useState<string | null>(null)
  const labelToastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleLabelCreated = (next: LabelDef[], name: string) => {
    setLabels(next)
    ann.setActiveLabel(name)
    setNewLabelOpen(false)
    if (labelToastTimer.current) clearTimeout(labelToastTimer.current)
    setLabelToast(t('labels.createdToast', { name }))
    labelToastTimer.current = setTimeout(() => setLabelToast(null), 6000)
  }
  useEffect(
    () => () => {
      if (labelToastTimer.current) clearTimeout(labelToastTimer.current)
    },
    [],
  )

  // --- toolbar ----------------------------------------------------------------
  // La barra dello Studio resta su una riga: v. `useFitOneRow` e i livelli in index.css.
  const barRef = useRef<HTMLDivElement>(null)
  useFitOneRow(barRef, 7, [t, ann.tool, page?.id, prefillBusy, labels.length])

  const tools = [
    { id: 'select' as Tool, labelKey: 'annotate.select', key: 'V', Icon: IconSelect },
    { id: 'rect' as Tool, labelKey: 'annotate.rect', key: 'R', Icon: IconRectangle },
    { id: 'polygon' as Tool, labelKey: 'annotate.polygon', key: 'P', Icon: IconPolygon },
    { id: 'pan' as Tool, labelKey: 'annotate.pan', key: 'H', Icon: IconHand },
  ]

  const reviewIndex = reviewRun && page ? pages.findIndex((item) => item.id === page.id) : -1
  const nextReviewPage = reviewIndex >= 0 ? pages[reviewIndex + 1] ?? null : pages[0] ?? null
  const openNextTask = () => {
    if (reviewRun && nextReviewPage) void onPageSelect(nextReviewPage.id)
    else {
      const next = pages.find((p) => p.id !== page?.id && (p.work?.can_submit || p.work?.can_review))
        ?? pages.find((p) => p.id !== page?.id && !p.work?.assignee_id && !['approved', 'exported'].includes(p.status))
      if (next) void onPageSelect(next.id)
    }
  }

  // Approvazione dallo Studio: prima si salva, perché il server giudica ciò
  // che ha, poi lo stesso controllo di «pagina pronta» dell'Archivio.
  const [approving, setApproving] = useState(false)
  const approveCurrentPage = async (confirmDrafts = false) => {
    if (!page || projectId === '') return
    setError(null)
    setApproving(true)
    try {
      await flushEdits()
      await apiPost(`/pages/${page.id}/approve`, confirmDrafts ? { confirm_drafts: true } : undefined)
      setPage((cur) => (cur && cur.id === page.id ? { ...cur, status: 'approved' } : cur))
      setPages((cur) => cur.map((p) => (p.id === page.id ? { ...p, status: 'approved' } : p)))
      const queue = await apiGet<{ items: Array<{ id: number; reason: string; rel_path?: string }> }>(`/projects/${projectId}/annotation-queue?limit=1`)
      setNextTask(queue.items[0] ?? null)
    } catch (e) {
      // 409: la pagina non è pronta (di solito bozze non verificate). Non è un
      // vicolo cieco: si offre di confermarle e approvare, scelta esplicita.
      if (!confirmDrafts && e instanceof ApiError && e.status === 409) {
        let reasons: string[] = []
        try {
          reasons = (JSON.parse(e.body)?.detail?.errors ?? []) as string[]
        } catch {
          /* corpo non JSON: resta il messaggio generico */
        }
        const ok = await confirm({
          title: t('annotate.approveDraftsTitle'),
          message: reasons.length ? `${t('annotate.approveDraftsBody')} ${reasons.join(' · ')}` : t('annotate.approveDraftsBody'),
          acceptLabel: t('annotate.approveDraftsConfirm'),
        })
        if (ok) {
          setApproving(false)
          return approveCurrentPage(true)
        }
        setApproving(false)
        return
      }
      setError(e)
    } finally {
      setApproving(false)
    }
  }
  const approved = page?.status === 'approved' || page?.status === 'exported'

  const contentCount = prefillDrafts.length + ann.blocks.length

  // Collaboratori sulla pagina (noi esclusi) per la fila di avatar.
  const peers = live.participants.filter((p) => p.connId !== live.selfId)
  const editingPeer = peers.find((p) => p.editing) ?? null

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <PageHeader title={t('annotate.title')} intro={t('annotate.intro')} className="shrink-0 px-3 py-1.5" aux={<>
        <select className="fld !w-auto text-[12px]" aria-label={t('annotate.viewModeAria')} value={studioMode} onChange={(e) => setMode(e.target.value as StudioMode)}>
          {(['all', 'canvas', 'transcription'] as const).map((mode) => <option key={mode} value={mode}>{t(`annotate.view${mode[0].toUpperCase()}${mode.slice(1)}`)}</option>)}
        </select>
        {page && <PageWorkControl
        key={page.id} page={page} revision={ann.getRevision()} disabled={navBusy || prefillBusy}
        beforeAction={async () => { await flushEdits(); return ann.getRevision() }}
        onReview={() => setMode('all')}
        onChanged={(work) => {
          const status = work.state === 'pending' ? 'qa' : work.state === 'approved' ? 'approved' : work.state === 'changes_requested' ? 'review' : page.status === 'approved' || page.status === 'qa' || page.status === 'exported' ? 'annotated' : page.status
          setPage((p) => p ? { ...p, work, status } : p)
          setPages((items) => items.map((p) => p.id === work.page_id ? { ...p, work, status } : p))
        }}
      />}
            {!page?.work?.assignee_id && !page?.work?.reviewer_id && <button
              type="button"
              onClick={() => void approveCurrentPage()}
              disabled={!page || approved || approving || navBusy || prefillBusy}
              className="btn whitespace-nowrap"
              title={t('project.approveTitle')}
            >
              <IconCheck size={12} />
              {approved ? t('annotate.approved') : t('project.approve')}
            </button>}
            <button
              type="button"
              onClick={openNextTask}
              disabled={reviewRun ? !nextReviewPage : !pages.some((p) => p.id !== page?.id && (p.work?.can_submit || p.work?.can_review || (!p.work?.assignee_id && !['approved', 'exported'].includes(p.status))))}
              className="btn btn-primary whitespace-nowrap"
              title={reviewRun ? t('annotate.nextRunPage') : nextTask?.reason}
            >
              <IconNext size={12} />
              {t('sidebar.nextPage')}
            </button>

      </>} />
      {error != null && <div className="shrink-0 px-3 py-2"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}
      {reviewRun && (
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] px-2 py-1 text-[11px]">
          <b>{t('annotate.reviewRun', { id: reviewRun.id, count: pages.length })}</b>
          <Link to={`/risultati?project=${reviewRun.project_id}&run=${reviewRun.id}`} className="btn btn-sm no-underline">
            {t('annotate.backToResults')}
          </Link>
        </div>
      )}
      <div className="flex min-h-0 flex-1 overflow-hidden">
      {studioMode === 'all' && <PageSidebar
        projects={projects}
        projectId={projectId}
        pages={pages}
        currentPage={page}
        onProjectChange={onProjectChange}
        onPageSelect={onPageSelect}
        sums={sums}
        loading={pagesLoading}
        width={sidebarWidth}
        reviewScope={reviewRun ? {
          label: t('annotate.reviewRun', { id: reviewRun.id, count: pages.length }),
          backTo: `/risultati?project=${reviewRun.project_id}&run=${reviewRun.id}`,
          backLabel: t('annotate.backToResults'),
        } : null}
      />}

      {studioMode === 'all' && <Splitter
        value={sidebarWidth}
        min={SIDEBAR_MIN}
        max={SIDEBAR_MAX}
        side="left"
        label={t('annotate.splitSidebar')}
        onChange={(v) => setSplitPane('sidebar', v)}
        onReset={() => resetSplitPane('sidebar')}
      />}

      {/* Centro: toolbar + canvas */}
      <main className={studioMode === 'transcription' ? 'hidden' : 'flex min-w-0 flex-1 flex-col'}>
        {/* Una riga sola: ciò che serve a ogni pagina sta in vista (strumenti,
            Annulla, stato del salvataggio, Prossimo compito); il resto — vista,
            flusso di lettura, prefill, salvataggio manuale, «riparti da zero» —
            sta in «Altro», scritto per esteso e a un clic. */}
        <div ref={barRef} className="studio-bar flex flex-wrap items-start justify-end gap-2 border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] px-2 py-1.5">
          {/* A sinistra come si disegna, a destra cosa viene dopo. Ogni
              controllo è alto uguale (`.studio-bar`, index.css). La barra
              stringe i dettagli per gradi (`useFitOneRow`, livelli in index.css);
              quando lo spazio non basta più, le azioni di destra vanno a capo
              una per volta. Sono figli diretti della barra — il loro
              contenitore è `display: contents` — e il `mr-auto` sull'ultimo
              controllo di sinistra le tiene a destra, con `justify-end` che le
              allinea a destra anche sulla riga successiva. */}
            <div className="flex overflow-hidden rounded-md border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]" role="toolbar" aria-label={t('annotate.toolsAria')}>
              {tools.map((tool) => {
                const on = ann.tool === tool.id
                return (
                  <button
                    key={tool.id}
                    type="button"
                    onClick={() => ann.setTool(tool.id)}
                    aria-pressed={on}
                    aria-keyshortcuts={tool.key}
                    title={t('annotate.toolTitle', { tool: t(tool.labelKey), key: tool.key })}
                    className={`btn whitespace-nowrap rounded-none border-0 border-r border-[color:var(--color-rule)] last:border-r-0 ${
                      on ? 'bg-[color:var(--color-sig-plate)] text-white' : ''
                    }`}
                  >
                    <tool.Icon size={13} />
                    <span className="tb-label">{t(tool.labelKey)}</span>
                    <span aria-hidden="true" className={`tb-key mono text-[10.5px] ${on ? 'text-white/70' : 'text-[color:var(--color-ink-3)]'}`}>
                      {tool.key}
                    </span>
                  </button>
                )
              })}
            </div>
            {/* Sempre presente, spenta quando non si disegna: comparire e
                sparire spostava tutto ciò che le stava a destra. */}
            <select
              value={ann.activeLabel}
              onChange={(e) => ann.setActiveLabel(e.target.value)}
              disabled={ann.tool !== 'rect' && ann.tool !== 'polygon'}
              aria-label={t('inspector.class')}
              title={t('inspector.class')}
              className="fld !w-auto"
            >
              {labels.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={ann.undo}
              disabled={!ann.canUndo || navBusy || prefillBusy}
              title={t('annotate.undoTitle')}
              className="btn mr-auto"
            >
              <IconUndo size={12} />
              <span className="tb-undo-label">{t('annotate.undo')}</span>
            </button>
          <div className="contents">
            {peers.length > 0 && (
              <div
                className="flex items-center -space-x-1"
                title={tn('live.onlineTitle', peers.length, { n: peers.length })}
                aria-label={tn('live.onlineTitle', peers.length, { n: peers.length })}
              >
                {peers.slice(0, 4).map((p) => (
                  <span
                    key={p.connId}
                    className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[color:var(--color-sheet)] text-[9px] font-semibold uppercase text-white"
                    style={{ backgroundColor: p.color }}
                    title={p.username}
                  >
                    {initialsOf(p.username)}
                  </span>
                ))}
                {peers.length > 4 && (
                  <span className="inline-flex h-5 w-5 items-center justify-center rounded-full border border-[color:var(--color-sheet)] bg-[color:var(--color-ink-3)] text-[9px] font-semibold text-white">
                    +{peers.length - 4}
                  </span>
                )}
              </div>
            )}
            {editingPeer && (
              <span className="tb-label whitespace-nowrap text-[11px] text-[color:var(--color-ink-3)]">
                {t('live.editing', { name: editingPeer.username })}
              </span>
            )}
            {page && (
              <span className="tb-name mono max-w-[16ch] truncate text-[11px] text-[color:var(--color-ink-3)]" title={`${pageLabel(page)} · ${page.width}×${page.height}`}>
                {pageLabel(page)}
              </span>
            )}
            {prefillBusy && (
              <button
                type="button"
                onClick={stopPrelabel}
                className="btn btn-danger"
                aria-label={t('annotate.stopInference')}
              >
                {t('annotate.stopInference')}
              </button>
            )}
            <button type="button" className="btn" disabled={!page || navBusy || prefillBusy} onClick={onPrefillClick}><IconPrefill size={12} />{t('work.recognize')}</button>
            <Menu label={t('annotate.more')}>
            <button
              type="button"
              onClick={() => setNewLabelOpen(true)}
              title={t('labels.newTitle')}
              aria-label={t('labels.newTitle')}
              className="menu-item"
            >
              <IconPlus size={12} />
              <span className="tb-label">{t('labels.new')}</span>
            </button>

              <button
                type="button"
                onClick={() => setShowFlow((value) => !value)}
                aria-pressed={showFlow}
                className="menu-item"
              >
                <IconFlow size={12} />
                {showFlow ? t('annotate.showFlow') : t('annotate.viewFlow')}
                {showFlow && <IconCheck size={12} className="ml-auto text-[color:var(--color-sig-text)]" />}
              </button>
              <button
                type="button"
                onClick={ann.redo}
                disabled={!ann.canRedo}
                title={t('annotate.redoTitle')}
                className="menu-item"
              >
                <IconRedo size={12} />
                {t('annotate.redo')}
              </button>
              <hr className="menu-sep" />
              <button
                type="button"
                onClick={() => void ann.saveNow()}
                disabled={!page || prefillBusy}
                className="menu-item"
              >
                <IconSave size={12} />
                {t('annotate.save')}
              </button>
              {/* Distruttivo e raro: in fondo al menu, con la sua conferma. */}
              <button
                type="button"
                onClick={() => void clearPageAnnotations()}
                disabled={!page || prefillBusy || contentCount === 0}
                className="menu-item !text-[color:var(--color-sig-text)]"
                title={t('annotate.clearAllTitle')}
              >
                <IconTrash size={12} />
                {t('annotate.clearAll')}
              </button>
            </Menu>
          </div>
        </div>
        <div className="relative min-h-0 flex-1">
          {navBusy && <div className="absolute inset-0 z-30 flex items-center justify-center bg-[color:var(--color-sheet)]/80" role="status">{t('common.loading')}</div>}
          {/* Lo stato del salvataggio sta fermo nell'angolo del foglio, non nella
              barra: cambiando parola ne cambiava la larghezza e la mandava a capo. */}
          <div
            className="pointer-events-auto absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-full border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] px-2.5 py-0.5 text-[11px] empty:hidden"
            aria-live="polite"
          >
            {ann.save.state === 'saving' && (
              <span className="text-[color:var(--color-ink-2)]">{t('annotate.saving')}</span>
            )}
            {ann.save.state !== 'saving' && (ann.dirty || draftEdits.dirty) && (
              <span className="text-[color:var(--color-warn)]">{t('annotate.unsaved')}</span>
            )}
            {ann.save.state === 'saved' && !ann.dirty && !draftEdits.dirty && (
              <span className="flex items-center gap-1 text-[color:var(--color-ok)]">
                <IconCheck size={11} />
                {t('annotate.saved')}
              </span>
            )}
            {ann.save.state === 'error' && (
              <span className="flex items-center gap-1.5 text-[color:var(--color-sig-text)]">
                {t('annotate.saveFailed')}
                <button onClick={() => void ann.saveNow()} className="btn btn-sm">
                  {t('annotate.retry')}
                </button>
              </span>
            )}
            {ann.save.state === 'conflict' && (
              <span className="flex items-center gap-1.5 text-[color:var(--color-sig-text)]">
                {t('annotate.conflictRemote')}
                <button onClick={() => void reloadRemote()} className="btn btn-sm">
                  {t('annotate.conflictReload')}
                </button>
              </span>
            )}
          </div>
          {remoteEdit && page && (
            <div className="pointer-events-auto absolute right-3 top-12 z-10 flex items-center gap-2 rounded-full border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] px-2.5 py-0.5 text-[11px] text-[color:var(--color-warn)]">
              <span>{t('live.remoteChanged', { name: remoteEdit.username })}</span>
              <button onClick={() => void reloadRemote()} className="btn btn-sm">
                {t('live.loadRemote')}
              </button>
              <button onClick={() => setRemoteEdit(null)} className="btn btn-sm">
                {t('common.cancel')}
              </button>
            </div>
          )}

          {prefillNotice && (
            <div className="absolute left-3 top-16 z-10 max-w-md border border-[color:var(--color-ok)] bg-[color:var(--color-ok-wash)] p-3 text-xs text-[color:var(--color-ok)]">
              {prefillNotice}
            </div>
          )}
          {deleteToast && (
            <div className="float-layer absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-3 px-3 py-2 text-xs text-[color:var(--color-ink)]">
              <span>
                {t('annotate.deleteToast', { label: deleteToast.label })}
              </span>
              <button onClick={undoDelete} className="btn btn-sm">
                {t('common.cancel')}
              </button>
            </div>
          )}
          {labelToast && (
            <div
              role="status"
              className="float-layer absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-2 border border-[color:var(--color-ok)] bg-[color:var(--color-ok-wash)] px-3 py-2 text-xs text-[color:var(--color-ok)]"
            >
              <IconCheck size={12} />
              <span>{labelToast}</span>
            </div>
          )}
          {!page ? (
            // Senza pagina lo Studio propone la prossima, invece di un invito
            // generico a «scegliere progetto e pagina» anche a progetto scelto.
            <div className="flex h-full items-center justify-center p-6">
              {projectId !== '' && (reviewRun ? nextReviewPage : nextTask) ? (
                <div className="max-w-[46ch] border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] p-4">
                  <span className="lbl">{t('annotate.nextTask')}</span>
                  <div className="mono truncate text-[13px] font-semibold">
                    {reviewRun ? nextReviewPage?.rel_path : nextTask?.rel_path ?? pages.find((item) => item.id === nextTask?.id)?.rel_path}
                  </div>
                  {!reviewRun && nextTask?.reason && (
                    <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">{nextTask.reason}</p>
                  )}
                  <button type="button" className="btn btn-primary mt-3" onClick={openNextTask}>
                    {t('annotate.openNextTask')}
                  </button>
                  <p className="mt-2 text-[11px] text-[color:var(--color-ink-3)]">{t('annotate.pickPageHint')}</p>
                </div>
              ) : (
                <p className="text-sm text-[color:var(--color-ink-3)]">
                  {projectId === '' ? t('annotate.selectHint') : t('annotate.pickPageHint')}
                </p>
              )}
            </div>
          ) : (
            previewUrl && (
              <div className={`h-full ${prefillBusy ? 'pointer-events-none' : ''}`}>
              <StudioCanvas
                interactive={!navBusy && !prefillBusy && studioMode !== 'transcription'}
                imageUrl={previewUrl}
                imageNatural={previewSize}
                blocks={ann.blocks}
                selectedId={ann.selectedId}
                tool={ann.tool}
                activeLabel={ann.activeLabel}
                showFlow={showFlow}
                colorFor={ann.colorFor}
                onSelect={ann.setSelectedId}
                onAddBlock={ann.addBlock}
                onUpdateBlock={updateBlockPoints}
                onDeleteBlock={handleDeleteBlock}
              />
              </div>
            )
          )}
        </div>
      </main>

      {/* Rail destro: una zona sola — il contenuto della pagina. L'output
          del modello arriva in diretta in cima, i blocchi si correggono
          riga per riga sotto (ritaglio a sinistra, testo o foglio a
          destra), l'ordine di lettura si governa dalle righe stesse.
          Larghezza governata dallo splitter. */}
      {studioMode === 'all' && <Splitter
        value={contentWidth}
        min={CONTENT_MIN}
        max={contentMax()}
        side="right"
        label={t('annotate.splitContent')}
        onChange={(v) => setSplitPane('content', v)}
        onReset={() => resetSplitPane('content')}
      />}
      <section
        aria-label={t('content.paneAria')}
        className={`${studioMode === 'canvas' ? 'hidden' : 'flex'} min-w-0 flex-col overflow-y-auto bg-[color:var(--color-sheet)] p-3 ${studioMode === 'all' ? 'shrink-0' : 'flex-1'}`}
        style={studioMode === 'all' ? { width: contentWidth } : undefined}
      >
        <fieldset disabled={prefillBusy || navBusy} className="min-w-0 border-0 p-0">
        <ContentPane
          blocks={ann.blocks}
          drafts={prefillDrafts}
          pageId={page?.id ?? null}
          pageImageUrl={previewUrl}
          pageImageSize={page ? { w: page.width, h: page.height } : null}
          labels={labels}
          selectedId={ann.selectedId}
          onSelect={ann.setSelectedId}
          onContent={ann.setBlockContent}
          onLabel={ann.setBlockLabel}
          onConfirmed={ann.setBlockConfirmed}
          onDelete={handleDeleteBlock}
          onSaveTable={saveTable}
          tableVersions={tableVersions}
          tableRedetect={tableRedetect}
          onDismissRedetect={() => setTableRedetect(null)}
          onRedetectNow={(serverId) => {
            setTableRedetect({ serverId, state: 'busy' })
            void redetectTable(serverId, true)
          }}
          onDraftContent={onDraftContent}
          onDraftLabel={onDraftLabel}
          onDraftGrid={onDraftGrid}
          onSaveDraftGrid={onSaveDraftGrid}
          onDraftConfirmed={onDraftConfirmed}
          onDraftReject={(id) => void onDraftReject(id)}
          onMove={ann.moveBlock}
          onReorderReset={ann.reorderReset}
          colorFor={ann.colorFor}
          projectId={projectId !== '' ? projectId : undefined}
          liveOutput={liveOutput}
          activeAdapterId={inf.adapterId}
          onLabelsSaved={setLabels}
          working={
            prefillBusy && prefillProgress
              ? {
                  engine: workingEngineName(),
                  startedAt: workingStartedAt.current,
                  blocks: prefillProgress.blocks,
                  last: prefillProgress.last,
                  output: liveOutput,
                }
              : null
          }
        />
        </fieldset>
      </section>

      </div>

      {prefillOpen && page && projectId !== '' && (
        <PrefillDialog
          canRun={prefillEngine !== 'off' && (prefillEngine === 'ocr' ? engines?.ocr.available !== false : inf.enabled && engines?.model.available !== false)}
          engineControl={<label className="block"><span className="lbl">{t('annotate.prefillEngine')}</span>              <select
                value={prefillEngine}
                onChange={(e) => setPrefillEngine(e.target.value as 'off' | 'ocr' | 'model-native')}
                disabled={prefillBusy}
                aria-label={t('annotate.prefillEngine')}
                className="fld !w-auto text-xs"
              >
                <option value="off">{t('annotate.prefillOff')}</option>
                <option value="ocr">{t('annotate.prefillEngineOcr')} (CPU)</option>
                {/* Un solo percorso modello: l'inferenza NATIVA — immagine
                    com'è, prompt di default del modello, sampling suo. Il
                    layout che chiedevamo noi al due-stadi è uscito: i
                    risultati devono coincidere con l'uso diretto del modello. */}
                <option
                  value="model-native"
                  disabled={
                    !inf.enabled ||
                    (engines ? !engines.model.available || engines.model.supports_native === false : false)
                  }
                >
                  {!inf.enabled
                    ? `${t('annotate.prefillEngineModelNative')} — ${t('annotate.gpuOff')}`
                    : engines && !engines.model.available
                      ? t('annotate.prefillEngineModelOff')
                      : engines && engines.model.supports_native === false
                        ? `${t('annotate.prefillEngineModelNative')} — ${t('annotate.prefillModeUnsupported')}`
                        : `${t('annotate.prefillEngineModelNative')} — ${nameOf(engines?.model.adapter_id ?? inf.adapterId) ?? inf.model} (${(engines?.model.is_cloud ?? inf.isCloud) ? 'Cloud' : 'GPU'})`}
                </option>
              </select></label>}
          summary={summarizePage(ann.blocks, prefillDrafts)}
          busy={prefillBusy}
          onRun={(mode) => void runPrelabel(mode)}
          onClose={() => setPrefillOpen(false)}
        />
      )}

      {newLabelOpen && projectId !== '' && (
        <NewLabelDialog
          projectId={projectId}
          existingNames={labels.map((l) => l.name)}
          onClose={() => setNewLabelOpen(false)}
          onCreated={handleLabelCreated}
        />
      )}
    </div>
  )
}
