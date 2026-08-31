import { useCallback, useEffect, useRef, useState } from 'react'
import { apiPut } from '../lib/api'
import { bboxPoints, toPage } from '../lib/coords'
import type { BlockBulkWrite, BlockOut, LabelDef, PageItem } from '../lib/types'
import type { DisplayBlock, SaveStatus, Tool } from './types'
import { useHistory } from './useHistory'

let _cid = 0
function nextId(): string {
  _cid += 1
  return `c${_cid}`
}

export interface UseAnnotationStateReturn {
  blocks: DisplayBlock[]
  selectedId: string | null
  setSelectedId: (id: string | null) => void
  tool: Tool
  setTool: (t: Tool) => void
  activeLabel: string
  setActiveLabel: (l: string) => void
  save: SaveStatus
  canUndo: boolean
  canRedo: boolean
  undo: () => void
  redo: () => void
  addBlock: (input: { kind: 'rect' | 'polygon'; points: { x: number; y: number }[]; label: string }) => void
  updateBlockPoints: (id: string, points: { x: number; y: number }[]) => void
  /** Blocco già scritto dal backend (streaming prefill): si aggiunge al
   *  canvas senza selezionarlo, con `serverId` noto. */
  insertServerBlock: (block: DisplayBlock) => void
  setBlockLabel: (id: string, label: string) => void
  setBlockContent: (id: string, content: string) => void
  setBlockConfirmed: (id: string, confirmed: boolean) => void
  deleteBlock: (id: string) => void
  moveBlock: (id: string, dir: -1 | 1) => void
  reorderReset: () => void
  saveNow: () => Promise<void>
  /** Vero se ci sono modifiche non ancora persistite. */
  dirty: boolean
  /** Lettura sincrona di `dirty`: serve a chi deve decidere *prima* di
   *  cambiare pagina, senza attendere il ciclo di render. */
  getDirty: () => boolean
  /** Salva ora se sporco, altrimenti non fa nulla. Da attendere prima di
   *  cambiare pagina o progetto: l'autosave con debounce non proteggerebbe
   *  la navigazione interna, solo la chiusura del browser. */
  flush: () => Promise<void>
  reset: (blocks?: DisplayBlock[], annotationRevision?: number) => void
  selectedBlock: DisplayBlock | null
  selectedBboxPage: { x: number; y: number; w: number; h: number } | null
  colorFor: (label: string) => string
}

export function useAnnotationState(
  page: PageItem | null,
  ratio: number,
  labels: LabelDef[],
): UseAnnotationStateReturn {
  const hist = useHistory<DisplayBlock[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tool, setTool] = useState<Tool>('select')
  const [activeLabel, setActiveLabel] = useState('Text')
  const [save, setSave] = useState<SaveStatus>({ state: 'idle' })
  const dirtyRef = useRef(false)
  const annotationRevisionRef = useRef(page?.annotation_revision ?? 0)
  // Copia reattiva di dirtyRef: l'indicatore «non salvato» in UI deve
  // aggiornarsi al render, la navigazione legge invece il ref in modo
  // sincrono (getDirty) senza attendere il ciclo di render.
  const [dirty, setDirty] = useState(false)
  const markDirty = useCallback(() => {
    if (!dirtyRef.current) {
      dirtyRef.current = true
      setDirty(true)
    }
  }, [])
  const clearDirty = useCallback(() => {
    dirtyRef.current = false
    setDirty(false)
  }, [])

  // Undo/redo modificano i blocchi tanto quanto un'aggiunta: vanno salvati.
  // Dopo il loro uso segnaliamo lo stato come «sporco» perché l'autosave
  // persista anche la versione ripristinata (altrimenti cambiando pagina o
  // ricaricando si tornava allo stato pre-annullamento).
  const undo = useCallback(() => {
    if (!hist.canUndo) return
    markDirty()
    hist.undo()
  }, [hist, markDirty])

  const redo = useCallback(() => {
    if (!hist.canRedo) return
    markDirty()
    hist.redo()
  }, [hist, markDirty])

  const reset = useCallback(
    (blocks: DisplayBlock[] = [], annotationRevision = page?.annotation_revision ?? 0) => {
      hist.reset(blocks)
      annotationRevisionRef.current = annotationRevision
      setSelectedId(null)
      clearDirty()
      setSave({ state: 'idle' })
    },
    [hist, clearDirty, page?.annotation_revision],
  )

  // --- operazioni blocchi ---------------------------------------------------
  const addBlock = useCallback(
    (input: { kind: 'rect' | 'polygon'; points: { x: number; y: number }[]; label: string }) => {
      markDirty()
      const block: DisplayBlock = {
        id: nextId(),
        serverId: null,
        label: input.label,
        kind: input.kind,
        points: input.points,
        content: '',
        orderIdx: null,
        confirmed: false,
      }
      hist.set((prev) => [...prev, block])
      setSelectedId(block.id)
    },
    [hist],
  )

  const updateBlockPoints = useCallback(
    (id: string, points: { x: number; y: number }[]) => {
      markDirty()
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, points } : b)))
    },
    [hist],
  )

  /** Inserisce un blocco arrivato dal server durante lo streaming del
   *  prefill, senza selezionarlo: il canvas si popola man mano che il
   *  backend scrive. `serverId` è già noto, quindi l'autosave lo aggiorna
   *  senza reinserirlo. */
  const insertServerBlock = useCallback(
    (block: DisplayBlock) => {
      markDirty()
      hist.set((prev) => [...prev, block])
    },
    [hist],
  )

  const setBlockLabel = useCallback(
    (id: string, label: string) => {
      markDirty()
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, label } : b)))
    },
    [hist],
  )

  const setBlockContent = useCallback(
    (id: string, content: string) => {
      markDirty()
      // La digitazione è un unico gesto: le battute ravvicinate sulla stessa
      // cella non generano un passo di undo per ogni lettera.
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, content } : b)), {
        coalesceKey: `content:${id}`,
      })
    },
    [hist],
  )

  const setBlockConfirmed = useCallback(
    (id: string, confirmed: boolean) => {
      markDirty()
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, confirmed } : b)))
    },
    [hist],
  )

  const deleteBlock = useCallback(
    (id: string) => {
      markDirty()
      hist.set((prev) => {
        const next = prev.filter((b) => b.id !== id)
        if (selectedId === id) setSelectedId(null)
        return next
      })
    },
    [hist, selectedId],
  )

  const moveBlock = useCallback(
    (id: string, dir: -1 | 1) => {
      markDirty()
      hist.set((prev) => {
        const sorted = [...prev].sort(
          (a, b) => (a.orderIdx ?? Number.MAX_SAFE_INTEGER) - (b.orderIdx ?? Number.MAX_SAFE_INTEGER),
        )
        const i = sorted.findIndex((b) => b.id === id)
        const j = i + dir
        if (i < 0 || j < 0 || j >= sorted.length) return prev
        ;[sorted[i], sorted[j]] = [sorted[j], sorted[i]]
        const posById = new Map(sorted.map((b, idx) => [b.id, idx]))
        return prev.map((b) => ({ ...b, orderIdx: posById.get(b.id) ?? b.orderIdx }))
      })
    },
    [hist],
  )

  const reorderReset = useCallback(() => {
    markDirty()
    hist.set((prev) => {
      const sorted = [...prev].sort(
        (a, b) => (a.orderIdx ?? Number.MAX_SAFE_INTEGER) - (b.orderIdx ?? Number.MAX_SAFE_INTEGER),
      )
      const posById = new Map(sorted.map((b, idx) => [b.id, idx]))
      return prev.map((b) => ({ ...b, orderIdx: posById.get(b.id) ?? b.orderIdx }))
    })
  }, [hist])

  // --- salvataggio ----------------------------------------------------------
  const saveNow = useCallback(async () => {
    if (!page) return
    setSave({ state: 'saving' })
    const r = ratio
    const body: BlockBulkWrite = {
      expected_revision: annotationRevisionRef.current,
      items: hist.present.map((b) => ({
        id: b.serverId ?? undefined,
        label: b.label,
        kind: b.kind,
        points: b.points.map((p) => {
          const pg = toPage(p, r)
          return [Number(pg.x.toFixed(2)), Number(pg.y.toFixed(2))]
        }),
        content: b.content,
        order_idx: b.orderIdx,
        confirmed: b.confirmed,
      })),
    }
    try {
      const res = await apiPut<{ items: BlockOut[] }>(
        `/pages/${page.id}/annotations`,
        body,
      )
      clearDirty()
      // Il mapping degli id di server non è una modifica: non deve finire
      // nello storico, altrimenti il prossimo «Annulla» sembrava un no-op.
      hist.replacePresent((prev) =>
        prev.map((b, i) => ({ ...b, serverId: res.items[i]?.id ?? b.serverId })),
      )
      // The response includes the revision even though the historical type
      // only declared items. Keep this tolerant for older deployments.
      annotationRevisionRef.current = Number((res as { annotation_revision?: number }).annotation_revision ?? annotationRevisionRef.current + 1)
      setSave({ state: 'saved' })
    } catch (e) {
      const message = String(e)
      setSave({ state: message.includes('→ 409') ? 'conflict' : 'error', message })
    }
  }, [page, ratio, hist, clearDirty])

  // autosave con debounce 700ms
  useEffect(() => {
    if (!page || !dirtyRef.current) return
    const t = setTimeout(() => void saveNow(), 700)
    return () => clearTimeout(t)
  }, [hist.present, page, saveNow])

  const flush = useCallback(async () => {
    if (!dirtyRef.current) return
    await saveNow()
  }, [saveNow])

  // Riferimento sempre aggiornato per il cleanup allo smontaggio: la closure
  // dell'effetto non vede l'ultimo saveNow altrimenti.
  const saveNowRef = useRef(saveNow)
  useEffect(() => {
    saveNowRef.current = saveNow
  }, [saveNow])

  // Cambiare pagina/progetto dentro l'app non passa da beforeunload: lo
  // smontaggio con lavoro sporco salva ciò che può essere salvato, così
  // l'autosave non dipende dal tempismo del debounce.
  useEffect(
    () => () => {
      if (dirtyRef.current) void saveNowRef.current()
    },
    [],
  )

  // avvisa se si chiude/cambia pagina con modifiche non ancora salvate
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (dirtyRef.current) e.preventDefault()
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  // --- scorciatoie da tastiera (undo/redo + strumenti) ----------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo()
        else undo()
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') {
        e.preventDefault()
        redo()
        return
      }
      const toolKeys: Record<string, Tool> = { v: 'select', r: 'rect' }
      const tk = toolKeys[e.key.toLowerCase()]
      if (tk) setTool(tk)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo])

  // --- derived --------------------------------------------------------------
  const selectedBlock = hist.present.find((b) => b.id === selectedId) ?? null
  const selectedBboxPage = selectedBlock
    ? (() => {
        const bb = bboxPoints(selectedBlock.points)
        const tl = toPage({ x: bb.x, y: bb.y }, ratio)
        const br = toPage({ x: bb.x + bb.w, y: bb.y + bb.h }, ratio)
        return { x: tl.x, y: tl.y, w: br.x - tl.x, h: br.y - tl.y }
      })()
    : null

  const colorFor = (label: string) => labels.find((l) => l.name === label)?.color ?? '#64748b'

  return {
    blocks: hist.present,
    selectedId,
    setSelectedId,
    tool,
    setTool,
    activeLabel,
    setActiveLabel,
    save,
    canUndo: hist.canUndo,
    canRedo: hist.canRedo,
    undo,
    redo,
    addBlock,
    updateBlockPoints,
    insertServerBlock,
    setBlockLabel,
    setBlockContent,
    setBlockConfirmed,
    deleteBlock,
    moveBlock,
    reorderReset,
    saveNow,
    dirty,
    getDirty: () => dirtyRef.current,
    flush,
    reset,
    selectedBlock,
    selectedBboxPage,
    colorFor,
  }
}
