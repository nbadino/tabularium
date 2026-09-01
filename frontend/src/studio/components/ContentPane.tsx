/**
 * Il contenuto della pagina: **una zona sola** nel rail destro.
 *
 * Prima erano tre moduli impilati — le regole di trascrizione, i livelli, il
 * contenuto — e nessuno dei primi due mostrava quello che si è lì per
 * guardare: i dati estratti. L'elenco dei livelli era poi la stessa lista di
 * blocchi del contenuto, scritta due volte; le regole sono materiale di
 * consultazione, non un pannello che occupa il rail per sempre.
 *
 * Qui c'è una riga per blocco — numero d'ordine, ritaglio, testo o foglio —
 * e sopra, mentre il modello scrive, l'output in diretta. L'ordine di lettura
 * si governa dalle stesse righe (frecce, Alt+frecce, Canc), quindi la lista
 * resta l'equivalente DOM del canvas Konva: navigabile da tastiera e
 * leggibile da uno screen reader, che è la ragione per cui esisteva
 * l'elenco dei livelli. Le regole vivono dietro il loro pulsante.
 */
import { useEffect, useRef, useState } from 'react'
import type {
  TableDetectOut,
  TableDetectRequest,
  TableGrid,
  TableGridOut,
} from '../../lib/types'
import { emptyGrid } from '../../lib/grid'
import { apiGet } from '../../lib/api'
import { Modal, Module } from '../../app/ui'
import { IconDown, IconTrash, IconUp } from '../../app/icons'
import { useI18n } from '../../i18n'
import type { LabelDef } from '../../lib/types'
import type { DisplayBlock, LivePrefillOutput, PrefillDraft } from '../types'
import SheetEditor from './SheetEditor'
import LiveStream from './LiveStream'
import ConventionsChecklist from './ConventionsChecklist'

/** Le classi che portano testo da trascrivere. Le altre (Picture, Column)
 *  entrano solo nel layout: la riga lo dichiara invece di fingere un editor. */
const NO_CONTENT_LABELS = new Set(['Picture', 'Column'])

/** Foglio di calcolo per una riga Table (blocco confermato o bozza): la
 *  griglia arriva dal server alla prima renderizzazione della riga, per
 *  `serverId` — non fidarsi di una griglia passata dallo stream live, che
 *  non sopravvive a un ricaricamento (cambio pagina e ritorno, refresh):
 *  qui l'unica fonte di verità è il server. */
function TableBlockEditor({
  id,
  serverId,
  onSaveTable,
  onDetectTable,
  initialGrid,
  version = 0,
}: {
  id: string
  serverId: number | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  initialGrid?: TableGrid | null
  /** Cambia quando la regione del blocco è stata rifatta: la griglia in
   *  memoria non vale più e si rilegge dal server. */
  version?: number
}) {
  const { t } = useI18n()
  const [grid, setGrid] = useState<TableGrid | null>(initialGrid ?? null)
  const [error, setError] = useState<string | null>(null)
  const loadedFor = useRef<string | null>(null)

  useEffect(() => {
    if (!serverId) return
    if (initialGrid && version === 0) return
    const key = `${id}:${version}`
    if (loadedFor.current === key) return
    loadedFor.current = key
    setError(null)
    apiGet<TableGridOut>(`/blocks/${serverId}/table`)
      .then((out) => setGrid(out.grid ?? emptyGrid(3, 4)))
      .catch(() => setError(t('table.loadFailed')))
  }, [id, serverId, initialGrid, version, t])

  if (!serverId) {
    return (
      <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] text-[color:var(--color-ink-2)]">
        {t('content.cropUnsaved')}
      </p>
    )
  }
  if (!grid) {
    return <p className="text-[12px] text-[color:var(--color-ink-2)]">{error ?? t('content.tableLoading')}</p>
  }
  return (
    <SheetEditor
      key={`${id}:${version}`}
      grid={grid}
      onSave={(g) => onSaveTable(serverId, g)}
      onDetect={onDetectTable ? (opts) => onDetectTable(serverId, opts) : undefined}
    />
  )
}

/** Esito del ri-rilevamento dopo un ridimensionamento del riquadro.
 *
 *  Vive accanto alla tabella e non altrove perché è di quella tabella che
 *  parla: la riga dice cosa è successo alla griglia mentre l'utente guardava
 *  l'immagine, e nel caso `stale` chiede il permesso invece di prenderselo. */
function RedetectNotice({
  state,
  message,
  onRun,
  onDismiss,
}: {
  state: 'busy' | 'done' | 'stale' | 'error'
  message?: string
  onRun: () => void
  onDismiss: () => void
}) {
  const { t } = useI18n()
  const tone =
    state === 'error'
      ? 'border-[color:var(--color-danger)] text-[color:var(--color-danger)]'
      : state === 'stale'
        ? 'border-[color:var(--color-warn)] text-[color:var(--color-warn)]'
        : 'border-[color:var(--color-rule-strong)] text-[color:var(--color-ink-2)]'
  return (
    <div className={`mb-2 flex items-center gap-2 border ${tone} bg-[color:var(--color-fill)] px-2 py-1 text-[11px]`}>
      <span className="min-w-0 flex-1">
        {state === 'busy' && t('table.redetectBusy')}
        {state === 'stale' && t('table.redetectStale')}
        {state === 'done' && (message ?? t('table.redetectDoneShort'))}
        {state === 'error' && t('table.redetectFailedWith', { msg: message ?? '' })}
      </span>
      {(state === 'stale' || state === 'error') && (
        <button type="button" onClick={onRun} className="btn btn-sm">
          {t('table.redetectRun')}
        </button>
      )}
      {state !== 'busy' && (
        <button type="button" onClick={onDismiss} className="btn btn-sm">
          {t('common.close')}
        </button>
      )}
    </div>
  )
}

interface RowShellProps {
  id: string
  /** Numero d'ordine di lettura, o `null` per una bozza non ancora sul canvas. */
  order: number | null
  label: string
  color?: string
  labels?: LabelDef[]
  onLabel?: (label: string) => void
  /** Origine automatica del contenuto, con il testo che la spiega. */
  badge?: { text: string; title: string }
  confirmed: boolean
  onConfirmed: (v: boolean) => void
  onDelete?: () => void
  deleteTitle: string
  selected: boolean
  onSelect?: () => void
  onMove?: (dir: -1 | 1) => void
  canMoveUp?: boolean
  canMoveDown?: boolean
  rowAria: string
  cropUrl: string | null
  cropAlt: string
  cropUnsavedHint: string
  wide?: boolean
  children: React.ReactNode
}

/** Guscio comune a ogni riga: numero e comandi in testata, ritaglio a
 *  sinistra, editor a destra. Un blocco confermato e una bozza di prefill
 *  condividono lo stesso guscio — cambia solo da dove arrivano dati e
 *  callback. I comandi stanno sempre nel DOM, visibili: non compaiono al
 *  passaggio del mouse (v. DESIGN.md). */
function RowShell({
  id,
  order,
  label,
  color,
  labels,
  onLabel,
  badge,
  confirmed,
  onConfirmed,
  onDelete,
  deleteTitle,
  selected,
  onSelect,
  onMove,
  canMoveUp,
  canMoveDown,
  rowAria,
  cropUrl,
  cropAlt,
  cropUnsavedHint,
  wide,
  children,
}: RowShellProps) {
  const { t } = useI18n()
  return (
    <li
      id={`content-row-${id}`}
      data-block={id}
      className={`stream-in border-b border-[color:var(--color-rule)] p-2.5 last:border-b-0 ${
        selected ? 'bg-[color:var(--color-sig-wash)]' : ''
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          aria-label={rowAria}
          onKeyDown={(e) => {
            if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
              e.preventDefault()
              onMove?.(e.key === 'ArrowUp' ? -1 : 1)
              return
            }
            if (e.key === 'Delete') {
              e.preventDefault()
              onDelete?.()
            }
          }}
          className="mono flex shrink-0 items-center gap-1.5 text-[11px] text-[color:var(--color-ink-3)]"
        >
          <span className="w-5 text-right">{order ?? '—'}</span>
          {color && (
            <span
              aria-hidden
              className="h-3 w-3 border border-[color:var(--color-rule-strong)]"
              style={{ background: color }}
            />
          )}
        </button>

        {onLabel && labels ? (
          <select
            value={label}
            onChange={(e) => onLabel(e.target.value)}
            onFocus={onSelect}
            className="fld !w-auto text-xs"
          >
            {labels.map((l) => (
              <option key={l.name} value={l.name}>
                {l.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="mono text-[11px] uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
            {label}
          </span>
        )}

        {badge && (
          <span className="badge text-[color:var(--color-ink-3)]" title={badge.title}>
            {badge.text}
          </span>
        )}

        <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-[color:var(--color-ink-2)]">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => onConfirmed(e.target.checked)}
            className="accent-[color:var(--color-sig)]"
          />
          {t('content.confirmDraft')}
        </label>

        <span className="ml-auto flex shrink-0 items-center gap-1">
          {onMove && (
            <>
              <button
                type="button"
                onClick={() => onMove(-1)}
                disabled={!canMoveUp}
                aria-label={t('layers.moveUp', { label })}
                className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)] disabled:opacity-30"
              >
                <IconUp size={11} />
              </button>
              <button
                type="button"
                onClick={() => onMove(1)}
                disabled={!canMoveDown}
                aria-label={t('layers.moveDown', { label })}
                className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)] disabled:opacity-30"
              >
                <IconDown size={11} />
              </button>
            </>
          )}
          {onDelete && (
            <button type="button" onClick={onDelete} title={deleteTitle} aria-label={deleteTitle} className="btn btn-sm">
              <IconTrash size={12} />
            </button>
          )}
        </span>
      </div>

      <div className={`flex min-w-0 gap-3 ${wide ? '' : 'items-start'}`}>
        <figure
          onClick={onSelect}
          className={`m-0 w-1/4 shrink-0 ${onSelect ? 'cursor-pointer' : ''}`}
        >
          {cropUrl ? (
            <img
              src={cropUrl}
              alt={cropAlt}
              loading="lazy"
              className="w-full border border-[color:var(--color-rule-strong)] bg-[color:var(--color-table)] object-contain"
            />
          ) : (
            <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] text-[color:var(--color-ink-2)]">
              {cropUnsavedHint}
            </p>
          )}
        </figure>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </li>
  )
}

interface ContentPaneProps {
  /** Blocchi confermati e blocchi disegnati a mano: quelli che vivono anche
   *  sul canvas. */
  blocks: DisplayBlock[]
  /** Bozze del prefill non ancora verificate: vivono solo qui, mai sul
   *  canvas, finché l'utente non le conferma. */
  drafts: PrefillDraft[]
  labels: LabelDef[]
  selectedId: string | null
  onSelect: (id: string | null) => void
  onContent: (id: string, content: string) => void
  onLabel: (id: string, label: string) => void
  onConfirmed: (id: string, confirmed: boolean) => void
  onDelete: (id: string) => void
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  /** Versione del ritaglio per blocco: cresce quando la regione cambia, e
   *  costringe griglia e immagine a rileggersi dal server. */
  tableVersions?: Record<number, number>
  /** Esito del ri-rilevamento seguito all'ultimo ridimensionamento. */
  tableRedetect?: { serverId: number; state: 'busy' | 'done' | 'stale' | 'error'; message?: string } | null
  onDismissRedetect?: () => void
  onRedetectNow?: (serverId: number) => void
  onDraftContent: (serverId: number, content: string) => void
  onDraftGrid: (serverId: number, grid: TableGrid) => void
  onSaveDraftGrid: (serverId: number, grid: TableGrid) => Promise<string>
  onDraftConfirmed: (serverId: number, confirmed: boolean) => void
  onDraftReject: (serverId: number) => void
  /** Ordine di lettura: governato dalle righe, non da un secondo elenco. */
  onMove?: (id: string, dir: -1 | 1) => void
  onReorderReset?: () => void
  colorFor?: (label: string) => string
  /** Serve solo ad aprire le regole del progetto: qui non si annota nulla. */
  projectId?: number
  liveOutput?: LivePrefillOutput | null
  /** Presente mentre il modello scrive: l'output live sta sopra la lista e i
   *  blocchi arrivano SOTTO, in diretta, senza coprire nulla. */
  working: { engine: string; startedAt: number; blocks: number; last: string | null; output?: LivePrefillOutput | null } | null
}

export default function ContentPane({
  blocks,
  drafts,
  labels,
  selectedId,
  onSelect,
  onContent,
  onLabel,
  onConfirmed,
  onDelete,
  onSaveTable,
  onDetectTable,
  tableVersions,
  tableRedetect,
  onDismissRedetect,
  onRedetectNow,
  onDraftContent,
  onDraftGrid,
  onSaveDraftGrid,
  onDraftConfirmed,
  onDraftReject,
  onMove,
  onReorderReset,
  colorFor,
  projectId,
  liveOutput,
  working,
}: ContentPaneProps) {
  const { t, tn } = useI18n()
  const [rulesOpen, setRulesOpen] = useState(false)

  // Il ritaglio è servito dal bbox corrente del blocco: cambiata la regione,
  // l'URL è lo stesso e il browser mostrerebbe l'immagine vecchia.
  const cropUrlFor = (serverId: number) => {
    const version = tableVersions?.[serverId] ?? 0
    return version ? `/api/blocks/${serverId}/crop?v=${version}` : `/api/blocks/${serverId}/crop`
  }

  // Le bozze già confermate sono già ricomparse fra i blocchi dopo il ricarico
  // dal server: qui restano solo quelle ancora da revisionare, altrimenti la
  // stessa trascrizione apparirebbe due volte.
  const confirmedServerIds = new Set(blocks.map((b) => b.serverId).filter((x): x is number => x != null))
  const pendingDrafts = drafts.filter((d) => !confirmedServerIds.has(d.serverId))

  const sortedBlocks = [...blocks].sort(
    (a, b) => (a.orderIdx ?? Number.MAX_SAFE_INTEGER) - (b.orderIdx ?? Number.MAX_SAFE_INTEGER),
  )

  // Segue la selezione fatta sul canvas: la riga corrispondente entra in vista.
  useEffect(() => {
    if (!selectedId) return
    document.getElementById(`content-row-${selectedId}`)?.scrollIntoView({ block: 'nearest' })
  }, [selectedId])

  const total = sortedBlocks.length + pendingDrafts.length
  const stream = working?.output ?? liveOutput ?? null
  const empty = total === 0

  return (
    <Module
      tab={t('content.paneTab')}
      flush
      aux={
        <>
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
            {tn('content.blocksCount', total)}
          </span>
          {onReorderReset && !empty && (
            <button
              type="button"
              onClick={onReorderReset}
              className="btn btn-sm"
              title={t('layers.renumberTitle')}
            >
              {t('layers.renumber')}
            </button>
          )}
          {projectId != null && (
            <button type="button" onClick={() => setRulesOpen(true)} className="btn btn-sm">
              {t('content.rules')}
            </button>
          )}
        </>
      }
    >
      {(working || stream?.text) && (
        <LiveStream
          working={working}
          text={stream?.text ?? ''}
          phase={stream?.phase}
        />
      )}

      {empty ? (
        <p className="p-3 text-[12px] text-[color:var(--color-ink-2)]">{t('content.emptyPageBody')}</p>
      ) : (
        <>
          {onMove && (
            <p className="border-b border-[color:var(--color-rule)] px-2.5 py-1 text-[11px] text-[color:var(--color-ink-3)]">
              {t('layers.keys')}
            </p>
          )}
          <ul className="flex flex-col">
            {sortedBlocks.map((block, i) => {
              const isTable = block.label === 'Table'
              const noContent = NO_CONTENT_LABELS.has(block.label)
              return (
                <RowShell
                  key={block.id}
                  id={block.id}
                  order={block.orderIdx ?? i}
                  label={block.label}
                  color={colorFor?.(block.label)}
                  labels={labels}
                  onLabel={(label) => onLabel(block.id, label)}
                  badge={
                    block.prefill
                      ? { text: 'OCR', title: t('layers.prefillTitle', { source: block.prefill }) }
                      : undefined
                  }
                  confirmed={block.confirmed}
                  onConfirmed={(v) => onConfirmed(block.id, v)}
                  onDelete={() => onDelete(block.id)}
                  deleteTitle={t('inspector.deleteBlock')}
                  selected={selectedId === block.id}
                  onSelect={() => onSelect(block.id)}
                  onMove={onMove ? (dir) => onMove(block.id, dir) : undefined}
                  canMoveUp={i > 0}
                  canMoveDown={i < sortedBlocks.length - 1}
                  rowAria={t('layers.blockAria', { n: i + 1, label: block.label })}
                  cropUrl={block.serverId ? cropUrlFor(block.serverId) : null}
                  cropAlt={t('content.cropAlt', { label: block.label })}
                  cropUnsavedHint={t('content.cropUnsaved')}
                  wide={isTable}
                >
                  {isTable ? (
                    <>
                      {tableRedetect && tableRedetect.serverId === block.serverId && (
                        <RedetectNotice
                          state={tableRedetect.state}
                          message={tableRedetect.message}
                          onRun={() => onRedetectNow?.(tableRedetect.serverId)}
                          onDismiss={() => onDismissRedetect?.()}
                        />
                      )}
                      <TableBlockEditor
                        id={block.id}
                        serverId={block.serverId}
                        onSaveTable={onSaveTable}
                        onDetectTable={onDetectTable}
                        version={block.serverId ? (tableVersions?.[block.serverId] ?? 0) : 0}
                      />
                    </>
                  ) : noContent ? (
                    <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('content.noContentBody')}</p>
                  ) : (
                    <textarea
                      value={block.content}
                      onChange={(e) => onContent(block.id, e.target.value)}
                      onFocus={() => onSelect(block.id)}
                      rows={Math.min(10, Math.max(3, block.content.split('\n').length + 1))}
                      placeholder={t('inspector.transcriptionPlaceholder')}
                      aria-label={t('content.blockAria', { label: block.label })}
                      className="fld resize-y font-mono text-[12px] leading-relaxed"
                    />
                  )}
                </RowShell>
              )
            })}

            {pendingDrafts.map((draft) => {
              const isTable = draft.label === 'Table'
              const noContent = NO_CONTENT_LABELS.has(draft.label)
              return (
                <RowShell
                  key={`draft-${draft.serverId}`}
                  id={`draft-${draft.serverId}`}
                  order={null}
                  label={draft.label}
                  badge={{ text: 'OCR', title: t('content.prefillDraftBody') }}
                  confirmed={draft.confirmed}
                  onConfirmed={(v) => onDraftConfirmed(draft.serverId, v)}
                  onDelete={() => onDraftReject(draft.serverId)}
                  deleteTitle={t('content.rejectDraft')}
                  selected={false}
                  rowAria={t('content.draftAria', { label: draft.label })}
                  cropUrl={`/api/blocks/${draft.serverId}/crop`}
                  cropAlt={t('content.cropAlt', { label: draft.label })}
                  cropUnsavedHint={t('content.cropUnsaved')}
                  wide={isTable}
                >
                  {isTable ? (
                    <TableBlockEditor
                      id={`draft-${draft.serverId}`}
                      serverId={draft.serverId}
                      initialGrid={draft.grid}
                      onSaveTable={(serverId, grid) => {
                        onDraftGrid(serverId, grid)
                        return onSaveDraftGrid(serverId, grid)
                      }}
                    />
                  ) : noContent ? (
                    <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('content.noContentBody')}</p>
                  ) : (
                    <textarea
                      value={draft.content}
                      onChange={(e) => onDraftContent(draft.serverId, e.target.value)}
                      rows={Math.min(10, Math.max(3, draft.content.split('\n').length + 1))}
                      aria-label={t('content.draftAria', { label: draft.label })}
                      className="fld resize-y font-mono text-[12px] leading-relaxed"
                    />
                  )}
                </RowShell>
              )
            })}
          </ul>
        </>
      )}

      {rulesOpen && projectId != null && (
        <Modal title={t('conventions.tab')} onClose={() => setRulesOpen(false)}>
          <div className="p-3">
            <ConventionsChecklist projectId={projectId} />
          </div>
        </Modal>
      )}
    </Module>
  )
}
