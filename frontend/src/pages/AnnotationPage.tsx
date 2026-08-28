import { useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from '../lib/api'
import { loadImageSize, PixelSize, scaleRatio } from '../lib/coords'
import type { BlockOut, LabelDef, PageItem } from '../lib/types'
import { PAGE_STATUSES } from '../lib/types'
import { statusLabel } from '../lib/vocab'
import StudioCanvas from '../studio/StudioCanvas'
import ClassPalette from '../studio/components/ClassPalette'
import ConventionsChecklist from '../studio/components/ConventionsChecklist'
import Inspector from '../studio/components/Inspector'
import LayersPanel from '../studio/components/LayersPanel'
import PageSidebar from '../studio/components/PageSidebar'
import TableCellsEditor from '../studio/components/TableCellsEditor'
import PageTransformReview from '../studio/components/PageTransformReview'
import { useAnnotationState } from '../studio/useAnnotationState'
import { useProjects, writeActiveProject } from '../app/activeProject'
import type { Tool } from '../studio/types'
import { emptyGrid } from '../lib/grid'
import type { PrefillEngines, TableDetectOut, TableDetectRequest, TableGrid, TableGridOut, TableSaveOut } from '../lib/types'
import { useI18n, tn } from '../i18n'
import {
  IconFlow,
  IconHand,
  IconPolygon,
  IconRectangle,
  IconRedo,
  IconSave,
  IconSelect,
  IconUndo,
} from '../app/icons'

import { saveInferenceToBackend, useInference } from '../app/inference'

export default function AnnotationPage() {
  const { t } = useI18n()
  const inf = useInference()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [pages, setPages] = useState<PageItem[]>([])
  const [page, setPage] = useState<PageItem | null>(null)
  const [labels, setLabels] = useState<LabelDef[]>([])
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewSize, setPreviewSize] = useState<PixelSize>({ w: 0, h: 0 })
  const [error, setError] = useState<string | null>(null)
  const [showFlow, setShowFlow] = useState(false)
  const [tableBlockId, setTableBlockId] = useState<string | null>(null)
  const [tableGrid, setTableGrid] = useState<TableGrid | null>(null)
  const [prefillBusy, setPrefillBusy] = useState(false)
  const [prefillEngine, setPrefillEngine] = useState<
    'off' | 'ocr' | 'model-two-stage' | 'model-end2end'
  >('ocr')
  const [engines, setEngines] = useState<PrefillEngines | null>(null)
  const [transformOpen, setTransformOpen] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const prefillConfidence = 0.5
  const [prefillNotice, setPrefillNotice] = useState<string | null>(null)
  const [nextTask, setNextTask] = useState<{ id: number; reason: string } | null>(null)
  const [readiness, setReadiness] = useState<{ ready: boolean; stages: Record<string, boolean>; warnings: string[] } | null>(null)
  const [reviewNotice, setReviewNotice] = useState<string | null>(null)

  const ratio =
    page && previewSize.w > 0
      ? scaleRatio(previewSize, { w: page.width, h: page.height })
      : 1

  const ann = useAnnotationState(page, ratio, labels)

  // --- carica blocchi dal server e li applica allo stato -----------------------
  const applyBlocks = async (p: PageItem, r: number) => {
    const rep = await apiGet<{ items: BlockOut[] }>(`/pages/${p.id}/annotations`)
    ann.reset(
      rep.items.map((b) => ({
        id: `srv-${b.id}`,
        serverId: b.id,
        label: b.label,
        kind: b.kind,
        points: b.points.map(([x, y]) => ({ x: x * r, y: y * r })),
        content: b.content,
        orderIdx: b.order_idx,
        confirmed: b.confirmed,
        prefill: b.prefill_source ?? null,
      })),
    )
  }

  // --- pseudo-labeling OCR ----------------------------------------------------
  const runPrelabel = async () => {
    if (!page || !projectId) return
    setPrefillBusy(true)
    setError(null)
    setPrefillNotice(null)
    try {
      const res = await apiPost<{ engine: string; results: Array<{ page_id: number; detected: number; inserted: number; tables?: number; grids?: number; deskew_angle?: number }> }>(
        `/projects/${projectId}/prelabel`,
        {
          page_ids: [page.id],
          mode: 'replace',
          confidence: prefillConfidence,
          min_size: 10,
          engine: prefillEngine === 'ocr' ? 'ocr' : 'model',
          model_mode: prefillEngine === 'model-end2end' ? 'end2end' : 'two_stage',
        },
      )
      const inserted = res.results[0]?.inserted ?? 0
      const deskewAngle = res.results[0]?.deskew_angle as number | undefined
      const base = tn('annotate.ocrNotice', inserted, { engine: res.engine })
      // La promozione a tabella cambia il lavoro da fare (si verificano celle,
      // non si trascrivono righe): vale la pena dirlo esplicitamente.
      const tablePart =
        (res.results[0]?.tables ?? 0) > 0
          ? ' ' + t('annotate.ocrTableNotice', { grids: res.results[0]?.grids ?? 0 })
          : ''
      setPrefillNotice(
        (deskewAngle && Math.abs(deskewAngle) >= 0.05
          ? `${base} ${t('annotate.deskewApplied', { angle: deskewAngle.toFixed(2) })}`
          : base) + tablePart,
      )
      await applyBlocks(page, ratio)
    } catch (e) {
      setError(String(e))
    } finally {
      setPrefillBusy(false)
    }
  }

  const resetTransforms = async () => {
    if (!page) return
    const hasBlocks = ann.blocks.length > 0
    if (!window.confirm(t('annotate.resetConfirm'))) return
    setResetBusy(true)
    setError(null)
    setPrefillNotice(null)
    try {
      await apiDelete(`/pages/${page.id}/deskew${hasBlocks ? '?confirm=true' : ''}`)
      await onPageSelect(page.id)
      setPreviewUrl(`/api/pages/${page.id}/preview?t=${Date.now()}`)
      setPrefillNotice(t('annotate.resetApplied'))
    } catch (e) {
      setError(String(e))
    } finally {
      setResetBusy(false)
    }
  }

  // --- seleziona progetto -----------------------------------------------------
  const onProjectChange = async (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setPage(null)
    setPages([])
    setPreviewUrl(null)
    ann.reset()
    if (pid === '') return
    try {
      const [pr, pg, queue] = await Promise.all([
        apiGet<{ items: PageItem[] }>(`/projects/${pid}/pages`),
        apiGet<{ labels: LabelDef[] }>(`/projects/${pid}/labels`),
        apiGet<{ items: Array<{ id: number; reason: string }> }>(`/projects/${pid}/annotation-queue?limit=1`),
      ])
      setPages(pr.items)
      setNextTask(queue.items[0] ?? null)
      setLabels(pg.labels)
      ann.setActiveLabel(pg.labels.some((l) => l.name === 'Text') ? 'Text' : pg.labels[0]?.name ?? 'Text')
    } catch (e) {
      setError(String(e))
    }
  }

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
          setPrefillEngine(out.recommended === 'model' ? 'model-two-stage' : 'ocr')
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

  // --- carica progetti e riapre subito quello attivo ---------------------------
  const projects = useProjects(
    (pid) => void onProjectChange(pid),
    (e) => setError(String(e)),
  )

  // --- seleziona pagina -------------------------------------------------------
  const onPageSelect = async (pid: number) => {
    const p = pages.find((x) => x.id === pid)
    if (!p) return
    setPage(p)
    apiGet<{ ready: boolean; stages: Record<string, boolean>; warnings: string[] }>(`/pages/${pid}/readiness`)
      .then(setReadiness)
      .catch(() => setReadiness(null))
    const url = `/api/pages/${pid}/preview`
    setPreviewUrl(url)
    try {
      const size = await loadImageSize(url)
      setPreviewSize(size)
      const r = size.w > 0 ? scaleRatio(size, { w: p.width, h: p.height }) : 1
      await applyBlocks(p, r)
    } catch (e) {
      setError(String(e))
    }
  }

  // --- editor tabella ---------------------------------------------------------
  const openTableEditor = async (block: { id: string; serverId: number | null }) => {
    if (!block.serverId) {
      setError(t('annotate.saveFirstBlock'))
      return
    }
    setTableBlockId(block.id)
    try {
      const out = await apiGet<TableGridOut>(`/blocks/${block.serverId}/table`)
      setTableGrid(out.grid ?? emptyGrid(3, 4))
    } catch (e) {
      setError(String(e))
      setTableBlockId(null)
    }
  }

  const detectTableGrid = async (opts: TableDetectRequest): Promise<TableDetectOut> => {
    const block = ann.blocks.find((b) => b.id === tableBlockId)
    if (!block?.serverId) {
      throw new Error(t('annotate.blockNotSaved'))
    }
    return apiPost<TableDetectOut>(`/blocks/${block.serverId}/table/detect`, opts)
  }

  const saveTableEditor = async (grid: TableGrid): Promise<string> => {
    const block = ann.blocks.find((b) => b.id === tableBlockId)
    if (!block?.serverId) {
      throw new Error(t('annotate.blockNotSaved'))
    }
    const out = await apiPut<TableSaveOut>(`/blocks/${block.serverId}/table`, grid)
    return out.otsl
  }

  const tableBlock = tableBlockId
    ? (ann.blocks.find((b) => b.id === tableBlockId) ?? null)
    : null

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

  // --- toolbar ----------------------------------------------------------------
  const tools = [
    { id: 'select' as Tool, labelKey: 'annotate.select', key: 'V', Icon: IconSelect },
    { id: 'rect' as Tool, labelKey: 'annotate.rect', key: 'R', Icon: IconRectangle },
    { id: 'polygon' as Tool, labelKey: 'annotate.polygon', key: 'P', Icon: IconPolygon },
    { id: 'pan' as Tool, labelKey: 'annotate.pan', key: 'H', Icon: IconHand },
  ]

  const openNextTask = () => {
    if (nextTask) void onPageSelect(nextTask.id)
  }

  const recordReview = async (reviewStatus: 'pass' | 'fail') => {
    if (!page) return
    try {
      await apiPost(`/pages/${page.id}/reviews`, { reviewer: 'local', status: reviewStatus, errors: reviewStatus === 'fail' ? (readiness?.warnings ?? []) : [] })
      setReviewNotice(reviewStatus === 'pass' ? t('annotate.qaRegistered') : t('annotate.qaFailedNotice'))
      const next = await apiGet<{ ready: boolean; stages: Record<string, boolean>; warnings: string[] }>(`/pages/${page.id}/readiness`)
      setReadiness(next)
    } catch (e) {
      setError(String(e))
    }
  }

  // --- guida contestuale --------------------------------------------------------
  const prefillCount = ann.blocks.filter((b) => b.prefill).length
  const guideHint = !page
    ? t('annotate.selectHint')
    : ann.blocks.length === 0
      ? t('annotate.guideStart')
      : prefillCount > 0
        ? t('annotate.guideFix', { n: prefillCount })
        : t('annotate.guideReady')

  return (
    <div className="flex h-full overflow-hidden">
      <PageSidebar
        projects={projects}
        projectId={projectId}
        pages={pages}
        currentPage={page}
        onProjectChange={onProjectChange}
        onPageSelect={onPageSelect}
      />

      {/* Centro: toolbar + canvas */}
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] px-2 py-1.5">
          <div className="flex border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]" role="toolbar" aria-label={t('annotate.toolsAria')}>
            {tools.map((tool) => (
              <button
                key={tool.id}
                type="button"
                onClick={() => ann.setTool(tool.id)}
                aria-pressed={ann.tool === tool.id}
                title={t('annotate.toolTitle', { tool: t(tool.labelKey), key: tool.key })}
                className={
                  ann.tool === tool.id
                    ? 'btn btn-sm border-y-0 border-l-0 border-r-[color:var(--color-rule)] bg-[color:var(--color-sig-plate)] text-white last:border-r-0'
                    : 'btn btn-sm border-y-0 border-l-0 border-r-[color:var(--color-rule)] last:border-r-0'
                }
              >
                <tool.Icon size={13} />
                {t(tool.labelKey)}
                <kbd className={`mono ml-1 border px-1 text-[10px] ${ann.tool === tool.id ? 'border-white/60 text-white' : 'border-[color:var(--color-rule)] text-[color:var(--color-ink-3)]'}`}>
                  {tool.key}
                </kbd>
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={ann.undo}
              disabled={!ann.canUndo}
              title={t('annotate.undoTitle')}
              className="btn btn-sm"
            >
              <IconUndo size={12} />
              {t('annotate.undo')}
            </button>
            <button
              type="button"
              onClick={ann.redo}
              disabled={!ann.canRedo}
              title={t('annotate.redoTitle')}
              className="btn btn-sm"
            >
              <IconRedo size={12} />
              {t('annotate.redo')}
            </button>
            <button
              type="button"
              onClick={() => setShowFlow((v) => !v)}
              aria-pressed={showFlow}
              className={`btn btn-sm ${
                showFlow
                  ? 'border-[color:var(--color-sig)] bg-[color:var(--color-sig-wash)] text-[color:var(--color-sig-text)]'
                  : ''
              }`}
            >
              <IconFlow size={12} />
              {showFlow ? t('annotate.showFlow') : t('annotate.viewFlow')}
            </button>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="flex items-center">
              <button
                onClick={() => void runPrelabel()}
                disabled={prefillBusy || !page || prefillEngine === 'off'}
                className={`rounded-l border px-2 py-1 text-xs transition-colors ${
                  prefillEngine === 'off'
                    ? 'border-slate-700 bg-slate-900 text-slate-500 cursor-not-allowed'
                    : 'border-violet-700 bg-violet-950 text-violet-300 hover:bg-violet-900 disabled:opacity-40'
                }`}
                title={
                  prefillEngine === 'off'
                    ? 'Prefill disattivato'
                    : prefillEngine !== 'ocr'
                      ? t('annotate.prefillTitleModel')
                      : t('annotate.prefillTitleOcr')
                }
              >
                {prefillBusy ? t('annotate.ocrBusy') : prefillEngine === 'off' ? 'Prefill Off' : t('annotate.prefill')}
              </button>
              {/* I due motori non sono intercambiabili: `ocr` trova righe di testo
                  e le etichetta tutte Text, `model` restituisce blocchi già
                  classificati e riconosce le tabelle. Su una pagina indice la
                  differenza è fra centinaia di blocchi Text e un blocco Table. */}
              <select
                value={prefillEngine}
                onChange={(e) =>
                  setPrefillEngine(
                    e.target.value as 'off' | 'ocr' | 'model-two-stage' | 'model-end2end',
                  )
                }
                disabled={prefillBusy}
                aria-label={t('annotate.prefillEngine')}
                className="rounded-r border border-l-0 border-violet-700 bg-violet-950 px-1 py-1 text-xs text-violet-300 disabled:opacity-40"
              >
                <option value="off">🚫 Prefill Disattivato</option>
                <option value="ocr">📄 {t('annotate.prefillEngineOcr')} (CPU)</option>
                <option
                  value="model-two-stage"
                  disabled={!inf.enabled || (engines ? !engines.model.available : false)}
                >
                  {!inf.enabled
                    ? '⚡ Modello 2-Stadi (GPU Disattivata)'
                    : engines && !engines.model.available
                      ? t('annotate.prefillEngineModelOff')
                      : `⚡ ${t('annotate.prefillEngineModelTwoStage')} (${inf.isCloud ? 'Cloud' : 'GPU'})`}
                </option>
                <option
                  value="model-end2end"
                  disabled={!inf.enabled || (engines ? !engines.model.available : false)}
                >
                  {!inf.enabled
                    ? '⚡ Modello End2End (GPU Disattivata)'
                    : engines && !engines.model.available
                      ? t('annotate.prefillEngineModelOff')
                      : `⚡ ${t('annotate.prefillEngineModelEnd2end')} (${inf.isCloud ? 'Cloud' : 'GPU'})`}
                </option>
              </select>
            </div>

            {/* Pulsante rapido Attiva/Disattiva GPU */}
            <button
              type="button"
              onClick={async () => {
                const next = !inf.enabled
                await saveInferenceToBackend({ enabled: next })
                try {
                  const out = await apiGet<PrefillEngines>('/system/prefill-engines')
                  setEngines(out)
                } catch {}
              }}
              className={`rounded border px-2 py-1 text-xs font-medium transition-colors ${
                inf.enabled && engines?.model.available
                  ? 'border-emerald-700 bg-emerald-950 text-emerald-300 hover:bg-emerald-900'
                  : inf.enabled
                    ? 'border-amber-700 bg-amber-950 text-amber-300 hover:bg-amber-900'
                    : 'border-slate-700 bg-slate-900 text-slate-400 hover:bg-slate-800'
              }`}
              title={
                inf.enabled
                  ? `GPU ${inf.isCloud ? 'Cloud' : 'Locale'} Attiva. Clicca per disattivare tutte le chiamate GPU.`
                  : 'GPU Disattivata. Clicca per attivare l\'inferenza GPU/Cloud.'
              }
            >
              {inf.enabled ? (
                engines?.model.available ? (
                  `🟢 GPU ${inf.isCloud ? 'Cloud' : 'On'}`
                ) : (
                  '🟡 GPU Offline'
                )
              ) : (
                '⚪ GPU Off'
              )}
            </button>
            <button
              type="button"
              onClick={() => setTransformOpen(true)}
              disabled={!page}
              className="rounded border border-cyan-700 bg-cyan-950 px-2 py-1 text-xs text-cyan-300 hover:bg-cyan-900 disabled:opacity-40"
              title={t('annotate.transformTitle')}
            >
              {t('annotate.transformOpen')}
            </button>
            <button
              onClick={() => void resetTransforms()}
              disabled={resetBusy || !page}
              className="rounded border border-slate-600 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
              title={t('annotate.resetTitle')}
            >
              {resetBusy ? t('annotate.resetBusy') : t('annotate.reset')}
            </button>
          </div>
          <button
            type="button"
            onClick={() => void ann.saveNow()}
            className="btn btn-primary btn-sm"
          >
            <IconSave size={12} />
            {t('annotate.save')}
          </button>
          {readiness && (
            <div className="flex items-center gap-1 text-[10px]" title={t('annotate.stagesTitle')}>
              {(['structure', 'content', 'table', 'review'] as const).map((stage) => (
                <span key={stage} className={`rounded px-1.5 py-1 text-white ${readiness.stages[stage] ? 'bg-emerald-950' : 'bg-slate-800'}`}>
                  {stage === 'structure'
                    ? t('annotate.stageStructure')
                    : stage === 'content'
                      ? t('annotate.stageContent')
                      : stage === 'table'
                        ? t('annotate.stageTable')
                        : t('annotate.stageReview')}
                </span>
              ))}
            </div>
          )}
          {page && (
            <div className="flex items-center gap-1">
              <button onClick={() => void recordReview('pass')} className="rounded border border-emerald-700 px-2 py-1 text-[10px] text-emerald-300 hover:bg-emerald-950">{t('annotate.qaPass')}</button>
              <button onClick={() => void recordReview('fail')} className="rounded border border-red-800 px-2 py-1 text-[10px] text-red-300 hover:bg-red-950">{t('annotate.qaFail')}</button>
            </div>
          )}
          {reviewNotice && <span className="text-[10px] text-[color:var(--color-ink-2)]">{reviewNotice}</span>}
          <button
            onClick={openNextTask}
            disabled={!nextTask}
            className="rounded border border-amber-700 bg-amber-950 px-2 py-1 text-xs text-amber-300 hover:bg-amber-900 disabled:opacity-40"
            title={nextTask?.reason}
          >
            {t('annotate.nextTask')}
          </button>
          {ann.save.state === 'saving' && (
            <span className="text-xs text-amber-400">{t('annotate.saving')}</span>
          )}
          {ann.save.state === 'saved' && <span className="text-xs text-emerald-400">{t('annotate.saved')}</span>}
          {ann.save.state === 'error' && (
            <span className="flex items-center gap-1.5 text-xs text-red-400">
              {t('annotate.saveFailed')}
              <button
                onClick={() => void ann.saveNow()}
                className="rounded border border-red-800 px-2 py-0.5 text-red-300 hover:bg-red-950"
              >
                {t('annotate.retry')}
              </button>
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {page && (
              <>
                <span className="font-mono text-[10px] text-slate-500">
                  {page.rel_path} · {page.width}×{page.height}
                </span>
                <select
                  value={page.status}
                  onChange={(e) =>
                    apiPatch(`/pages/${page.id}`, { status: e.target.value }).catch((err) =>
                      setError(String(err)),
                    )
                  }
                  className="rounded-md border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs text-slate-200 focus:border-sky-500 focus:outline-none"
                >
                  {PAGE_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {statusLabel(s)}
                    </option>
                  ))}
                </select>
              </>
            )}
          </div>
        </div>
        <div className="border-b border-slate-800 bg-slate-950/70 px-3 py-1.5 text-[11px] text-slate-400">
          <b className="text-sky-300">{t('annotate.guideLabel')}</b> {guideHint}
        </div>
        <div className="relative flex-1">
          {error && (
            <div className="absolute left-3 top-3 z-10 max-w-md rounded-lg border border-red-900 bg-red-950/95 p-3 text-xs text-red-300">
              {error}
            </div>
          )}
          {prefillNotice && (
            <div className="absolute left-3 top-16 z-10 max-w-md rounded-lg border border-violet-800 bg-violet-950/95 p-3 text-xs text-violet-200">
              {prefillNotice}
            </div>
          )}
          {deleteToast && (
            <div className="absolute bottom-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-3 rounded-md border border-slate-700 bg-slate-900/95 px-3 py-2 text-xs text-slate-200 shadow-lg">
              <span>
                {t('annotate.deleteToast', { label: deleteToast.label })}
              </span>
              <button
                onClick={undoDelete}
                className="rounded border border-sky-700 bg-sky-950 px-2 py-1 text-sky-300 hover:bg-sky-900"
              >
                {t('common.cancel')}
              </button>
            </div>
          )}
          {!page ? (
            <div className="flex h-full items-center justify-center text-sm text-slate-600">
              {t('annotate.selectHint')}
            </div>
          ) : (
            previewUrl && (
              <StudioCanvas
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
                onUpdateBlock={ann.updateBlockPoints}
                onDeleteBlock={handleDeleteBlock}
              />
            )
          )}
        </div>
      </main>

      {/* Colonna strumenti */}
      <aside className="flex w-72 shrink-0 flex-col gap-6 overflow-y-auto border-l border-slate-800 bg-slate-900 p-3">
        {projectId !== '' && <ConventionsChecklist projectId={Number(projectId)} />}
        <ClassPalette labels={labels} active={ann.activeLabel} onSelect={ann.setActiveLabel} />
        <LayersPanel
          blocks={ann.blocks}
          selectedId={ann.selectedId}
          colorFor={ann.colorFor}
          onSelect={ann.setSelectedId}
          onMove={ann.moveBlock}
          onDelete={handleDeleteBlock}
          onReorderReset={ann.reorderReset}
        />
        <Inspector
          block={ann.selectedBlock}
          labels={labels}
          bboxPage={ann.selectedBboxPage}
          onLabel={ann.setBlockLabel}
          onContent={ann.setBlockContent}
          onConfirmed={ann.setBlockConfirmed}
          onDelete={handleDeleteBlock}
        />
        {ann.selectedBlock?.label === 'Table' && (
          <button
            onClick={() => void openTableEditor(ann.selectedBlock!)}
            className="w-full rounded-md border border-fuchsia-800 bg-fuchsia-950 px-3 py-2 text-sm text-fuchsia-300 hover:bg-fuchsia-900"
          >
            {t('annotate.openTableEditor')}
          </button>
        )}
      </aside>

      {transformOpen && page && (
        <PageTransformReview
          pageId={page.id}
          width={page.width}
          height={page.height}
          hasBlocks={ann.blocks.length > 0}
          onClose={() => setTransformOpen(false)}
          onAccepted={async () => {
            await onPageSelect(page.id)
            setPreviewUrl(`/api/pages/${page.id}/preview?t=${Date.now()}`)
            setPrefillNotice(t('annotate.transformAcceptedNotice'))
          }}
        />
      )}

      {tableGrid && tableBlock?.serverId && (
        <TableCellsEditor
          grid={tableGrid}
          cropUrl={`/api/blocks/${tableBlock.serverId}/crop`}
          onSave={saveTableEditor}
          onDetect={detectTableGrid}
          onClose={() => {
            setTableBlockId(null)
            setTableGrid(null)
          }}
        />
      )}
    </div>
  )
}
