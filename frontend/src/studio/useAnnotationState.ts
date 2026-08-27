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
  setBlockLabel: (id: string, label: string) => void
  setBlockContent: (id: string, content: string) => void
  setBlockConfirmed: (id: string, confirmed: boolean) => void
  deleteBlock: (id: string) => void
  moveBlock: (id: string, dir: -1 | 1) => void
  reorderReset: () => void
  saveNow: () => Promise<void>
  reset: (blocks?: DisplayBlock[]) => void
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

  // Undo/redo modificano i blocchi tanto quanto un'aggiunta: vanno salvati.
  // Dopo il loro uso segnaliamo lo stato come «sporco» perché l'autosave
  // persista anche la versione ripristinata (altrimenti cambiando pagina o
  // ricaricando si tornava allo stato pre-annullamento).
  const undo = useCallback(() => {
    if (!hist.canUndo) return
    dirtyRef.current = true
    hist.undo()
  }, [hist])

  const redo = useCallback(() => {
    if (!hist.canRedo) return
    dirtyRef.current = true
    hist.redo()
  }, [hist])

  const reset = useCallback(
    (blocks: DisplayBlock[] = []) => {
      hist.reset(blocks)
      setSelectedId(null)
      dirtyRef.current = false
      setSave({ state: 'idle' })
    },
    [hist],
  )

  // --- operazioni blocchi ---------------------------------------------------
  const addBlock = useCallback(
    (input: { kind: 'rect' | 'polygon'; points: { x: number; y: number }[]; label: string }) => {
      dirtyRef.current = true
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
      dirtyRef.current = true
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, points } : b)))
    },
    [hist],
  )

  const setBlockLabel = useCallback(
    (id: string, label: string) => {
      dirtyRef.current = true
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, label } : b)))
    },
    [hist],
  )

  const setBlockContent = useCallback(
    (id: string, content: string) => {
      dirtyRef.current = true
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
      dirtyRef.current = true
      hist.set((prev) => prev.map((b) => (b.id === id ? { ...b, confirmed } : b)))
    },
    [hist],
  )

  const deleteBlock = useCallback(
    (id: string) => {
      dirtyRef.current = true
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
      dirtyRef.current = true
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
    dirtyRef.current = true
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
      dirtyRef.current = false
      // Il mapping degli id di server non è una modifica: non deve finire
      // nello storico, altrimenti il prossimo «Annulla» sembrava un no-op.
      hist.replacePresent((prev) =>
        prev.map((b, i) => ({ ...b, serverId: res.items[i]?.id ?? b.serverId })),
      )
      setSave({ state: 'saved' })
    } catch (e) {
      setSave({ state: 'error', message: String(e) })
    }
  }, [page, ratio, hist])

  // autosave con debounce 700ms
  useEffect(() => {
    if (!page || !dirtyRef.current) return
    const t = setTimeout(() => void saveNow(), 700)
    return () => clearTimeout(t)
  }, [hist.present, page, saveNow])

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
      const toolKeys: Record<string, Tool> = { v: 'select', r: 'rect', p: 'polygon', h: 'pan' }
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
    setBlockLabel,
    setBlockContent,
    setBlockConfirmed,
    deleteBlock,
    moveBlock,
    reorderReset,
    saveNow,
    reset,
    selectedBlock,
    selectedBboxPage,
    colorFor,
  }
}
