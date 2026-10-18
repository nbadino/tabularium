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
import { useCallback, useEffect, useRef, useState } from 'react'
import { Menu, Modal, Module, Select } from '../../app/ui'
import { IconDown, IconTrash, IconUp } from '../../app/icons'
import { useI18n } from '../../i18n'
import type { LabelDef, TableGrid } from '../../lib/types'
import type { DisplayBlock, LivePrefillOutput, PrefillDraft } from '../types'
import { TableWorkspacePanel } from './TableWorkspacePanel'
import { useFlipList, useHeightTransition } from '../../lib/motion'
import LiveStream from './LiveStream'
import ConventionsChecklist from './ConventionsChecklist'
import LabelsEditor from './LabelsEditor'
import TableHintEditor from './TableHintEditor'

/** Le classi che portano testo da trascrivere. Le altre (Picture, Column)
 *  entrano solo nel layout: la riga lo dichiara invece di fingere un editor. */
const NO_CONTENT_LABELS = new Set(['Picture', 'Column'])

/** Il ritaglio è servito dal bbox corrente del blocco: cambiata la regione,
 *  l'URL è lo stesso e il browser mostrerebbe l'immagine vecchia. */
const cropUrlFor = (serverId: number, version = 0) =>
  version ? `/api/blocks/${serverId}/crop?v=${version}` : `/api/blocks/${serverId}/crop`

/** La tabella non vive nel rail: nel rail ci sta la sua scheda, con il
 *  ritaglio e il comando che la apre.
 *
 *  Il foglio si apre quasi a tutto schermo: il ricercatore lavora sui dati in
 *  una sola superficie ampia, mentre il ritaglio resta visibile nella scheda. */
function TableBlockEditor({
  serverId,
  pageId,
  pageImageUrl,
  pageImageSize,
  onSaveTable,
  confirmed,
  onConfirmed,
}: {
  serverId: number | null
  pageId?: number | null
  pageImageUrl?: string | null
  pageImageSize?: { w: number; h: number } | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  /** «Verificata» del blocco, la stessa casella della scheda. */
  confirmed: boolean
  onConfirmed: (confirmed: boolean) => void
}) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const flushRef = useRef<(() => Promise<void>) | null>(null)
  const close = useCallback(() => {
    void (async () => {
      try {
        await flushRef.current?.()
        setOpen(false)
      } catch { /* Il pannello mostra l'errore e rimane aperto per riprovare. */ }
    })()
  }, [])

  if (!serverId) {
    return (
      <p className="border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] text-[color:var(--color-ink-2)]">
        {t('content.cropUnsaved')}
      </p>
    )
  }
  return (
    <>
      <div className="flex items-center gap-2">
        {pageId && (
          // Una finestra sua, aperta da script e con un nome per tabella: così
          // sa da quale finestra viene (il suo «Chiudi e torna» la richiude e
          // riporta qui), e un secondo clic la richiama invece di duplicarla.
          <button
            type="button"
            onClick={() => {
              const url = `/tabella/${serverId}?page=${pageId}&width=${pageImageSize?.w ?? ''}&height=${pageImageSize?.h ?? ''}&confirmed=${confirmed ? 1 : 0}`
              const w = Math.round(window.screen.availWidth * 0.85)
              const h = Math.round(window.screen.availHeight * 0.85)
              window.open(url, `tabularium-table-${serverId}`, `popup,width=${w},height=${h}`)?.focus()
            }}
            className="btn btn-sm"
            title={t('table.openSecondScreen')}
          >
            {t('table.openSecondScreen')}
          </button>
        )}
        <button type="button" onClick={() => setOpen(true)} className="btn btn-sm btn-primary">
          {t('table.openWorkspace')}
        </button>
      </div>
      {open && (
        <Modal title={t('table.workspaceTitle')} extraWide onClose={close}>
          {/* La griglia si rilegge dal server all'apertura: la scheda non tiene
              una seconda copia del modello da tenere allineata. */}
          <div className="flex min-h-0 flex-1 flex-col p-3">
            <VerifiedToggle checked={confirmed} onChange={onConfirmed} className="mb-2" />
            <TableWorkspacePanel
              serverId={serverId}
              flushRef={flushRef}
              pageImageUrl={pageImageUrl ?? null}
              pageId={pageId ?? null}
              pageImageSize={pageImageSize ?? null}
              onSaveTable={onSaveTable}
            />
          </div>
        </Modal>
      )}
    </>
  )
}

/** La casella «Verificata» del blocco, anche dentro le viste della tabella. */
export function VerifiedToggle({ checked, onChange, className = '' }: { checked: boolean; onChange: (checked: boolean) => void; className?: string }) {
  const { t } = useI18n()
  return (
    <label className={`flex w-fit cursor-pointer items-center gap-1.5 text-[12px] text-[color:var(--color-ink-2)] ${className}`}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="accent-[color:var(--color-sig)]" />
      {t('content.confirmDraft')}
    </label>
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
  /** Una riga sola: blocco già verificato e non selezionato. Il lavoro
   *  finito si riduce a numero, classe, anteprima e ✓; si riapre col clic. */
  collapsed?: boolean
  preview?: string
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
  collapsed,
  preview,
  children,
}: RowShellProps) {
  const { t } = useI18n()
  // Verificata, la riga si chiude in una riga sola: ci arriva scorrendo, e chi
  // le sta sotto sale con lei invece di saltare.
  const rowRef = useRef<HTMLLIElement>(null)
  useHeightTransition(rowRef, collapsed)
  const orderButton = (
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
          className="h-3 w-3 rounded-sm border border-[color:var(--color-rule-strong)]"
          style={{ background: color }}
        />
      )}
    </button>
  )
  if (collapsed) {
    return (
      <li
        ref={rowRef}
        id={`content-row-${id}`}
        data-block={id}
        className="stream-in flex cursor-pointer items-center gap-2 border-b border-[color:var(--color-rule)] px-2.5 py-1.5 last:border-b-0 hover:bg-[color:var(--color-fill)]"
        onClick={(e) => {
          if (!(e.target as HTMLElement).closest('button')) onSelect?.()
        }}
      >
        {orderButton}
        <span className="shrink-0 text-[12px] font-semibold">{label}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-[color:var(--color-ink-2)]">
          {preview}
        </span>
        <span className="shrink-0 text-[11px] text-[color:var(--color-ok)]" title={t('content.confirmDraft')}>
          ✓ {t('content.confirmDraft')}
        </span>
      </li>
    )
  }
  return (
    <li
      ref={rowRef}
      id={`content-row-${id}`}
      data-block={id}
      // Ogni riga entra animata e diventa un proprio contesto di sovrapposizione:
      // senza `z-40` la tendina della classe finirebbe sotto la riga successiva.
      className={`stream-in relative border-b border-[color:var(--color-rule)] p-2.5 last:border-b-0 has-[details[open]]:z-40 ${
        selected ? 'bg-[color:var(--color-sig-wash)]' : ''
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        {orderButton}

        {onLabel && labels ? (
          <Select
            value={label}
            onChange={onLabel}
            onFocus={onSelect}
            ariaLabel={t('inspector.class')}
            className="fld !w-auto text-xs"
            options={labels.map((l) => ({ value: l.name, label: l.name }))}
          />
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

      {/* Una tabella ha bisogno di tutta la larghezza della riga: un registro
          a otto colonne in un quarto di pannello mostra due colonne e mezza. Il
          suo ritaglio qui è il riferimento della scheda; il lavoro vero si fa
          nella vista di lavoro, che si apre a parte. */}
      <div className={wide ? 'flex min-w-0 flex-col gap-2' : 'flex min-w-0 items-start gap-3'}>
        <figure
          onClick={onSelect}
          className={`m-0 shrink-0 ${wide ? 'max-h-32 w-full overflow-hidden' : 'w-1/4'} ${onSelect ? 'cursor-pointer' : ''}`}
        >
          {cropUrl ? (
            <img data-fade
              src={cropUrl}
              alt={cropAlt}
              loading="lazy"
              className={`w-full rounded-sm border border-[color:var(--color-rule)] bg-[color:var(--color-table)] object-contain ${wide ? 'max-h-32 object-top' : ''}`}
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
  pageId?: number | null
  pageImageUrl?: string | null
  pageImageSize?: { w: number; h: number } | null
  /** Versione del ritaglio per blocco: cresce quando la regione cambia, e
   *  costringe griglia e immagine a rileggersi dal server. */
  tableVersions?: Record<number, number>
  /** Esito del ri-rilevamento seguito all'ultimo ridimensionamento. */
  tableRedetect?: { serverId: number; state: 'busy' | 'done' | 'stale' | 'error'; message?: string } | null
  onDismissRedetect?: () => void
  onRedetectNow?: (serverId: number) => void
  onDraftContent: (serverId: number, content: string) => void
  /** Cambia la classe di una bozza: le bozze vivono solo qui, quindi la loro
   *  label si governa da qui (non dal canvas). */
  onDraftLabel?: (serverId: number, label: string) => void
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
  /** Modello attivo: serve a dire quali classi conosce già (badge). */
  activeAdapterId?: string | null
  /** Il pannello classi ha salvato: il chiamante riallinea la tassonomia. */
  onLabelsSaved?: (labels: LabelDef[]) => void
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
  pageId,
  pageImageUrl,
  pageImageSize,
  tableVersions,
  tableRedetect,
  onDismissRedetect,
  onRedetectNow,
  onDraftContent,
  onDraftLabel,
  onDraftGrid,
  onSaveDraftGrid,
  onDraftConfirmed,
  onDraftReject,
  onMove,
  onReorderReset,
  colorFor,
  projectId,
  liveOutput,
  activeAdapterId,
  onLabelsSaved,
  working,
}: ContentPaneProps) {
  const { t, tn } = useI18n()
  // Le righe che cambiano posto scivolano (v. lib/motion.ts).
  const listRef = useRef<HTMLUListElement>(null)
  useFlipList(listRef)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [labelsOpen, setLabelsOpen] = useState(false)
  const [hintOpen, setHintOpen] = useState(false)

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
    document.getElementById(`content-row-${selectedId}`)?.scrollIntoView?.({ block: 'nearest' })
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
          <span className="mono text-[11px] text-[color:var(--color-ink-3)]" title={onMove ? t('layers.keys') : undefined}>
            {tn('content.blocksCount', total)}
          </span>
          <Menu label={t('work.contentOptions')}>
          {onReorderReset && !empty && (
            <button
              type="button"
              aria-label={t('layers.renumber')}
              onClick={onReorderReset}
              className="menu-item"
              title={t('layers.renumberTitle')}
            >
              {t('layers.renumber')}
            </button>
          )}
          {projectId != null && (
            <button type="button" aria-label={t('content.rules')} onClick={() => setRulesOpen(true)} className="menu-item">
              {t('content.rules')}
            </button>
          )}
          {projectId != null && (
            <button type="button" aria-label={t('labels.tab')} onClick={() => setLabelsOpen(true)} className="menu-item">
              {t('labels.tab')}
            </button>
          )}
          {projectId != null && (
            <button type="button" aria-label={t('tableHint.tab')} onClick={() => setHintOpen(true)} className="menu-item">
              {t('tableHint.tab')}
            </button>
          )}
          </Menu>
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
          <ul ref={listRef} className="cascade relative flex flex-col">
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
                    block.prefill && !block.confirmed
                      ? { text: 'OCR', title: t('layers.prefillTitle', { source: block.prefill }) }
                      : undefined
                  }
                  collapsed={block.confirmed && selectedId !== block.id && !isTable}
                  preview={noContent ? '' : block.content.replace(/\s+/g, ' ')}
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
                  cropUrl={block.serverId ? cropUrlFor(block.serverId, tableVersions?.[block.serverId] ?? 0) : null}
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
                        serverId={block.serverId}
                        pageId={pageId ?? null}
                        pageImageUrl={pageImageUrl ?? null}
                        pageImageSize={pageImageSize ?? null}
                        onSaveTable={onSaveTable}
                                  confirmed={block.confirmed}
                        onConfirmed={(v) => onConfirmed(block.id, v)}
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
                  labels={labels}
                  onLabel={onDraftLabel ? (label) => onDraftLabel(draft.serverId, label) : undefined}
                  badge={{ text: 'OCR', title: t('content.prefillDraftBody') }}
                  confirmed={draft.confirmed}
                  onConfirmed={(v) => onDraftConfirmed(draft.serverId, v)}
                  onDelete={() => onDraftReject(draft.serverId)}
                  deleteTitle={t('content.rejectDraft')}
                  selected={false}
                  rowAria={t('content.draftAria', { label: draft.label })}
                  cropUrl={cropUrlFor(draft.serverId)}
                  cropAlt={t('content.cropAlt', { label: draft.label })}
                  cropUnsavedHint={t('content.cropUnsaved')}
                  wide={isTable}
                >
                  {isTable ? (
                    <TableBlockEditor
                      serverId={draft.serverId}
                      pageId={pageId ?? null}
                      pageImageUrl={pageImageUrl ?? null}
                      pageImageSize={pageImageSize ?? null}
                      onSaveTable={(serverId, grid) => {
                        onDraftGrid(serverId, grid)
                        return onSaveDraftGrid(serverId, grid)
                      }}
                      confirmed={draft.confirmed}
                      onConfirmed={(v) => onDraftConfirmed(draft.serverId, v)}
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

      {labelsOpen && projectId != null && (
        <Modal title={t('labels.tab')} wide onClose={() => setLabelsOpen(false)}>
          <LabelsEditor
            projectId={projectId}
            labels={labels}
            activeAdapterId={activeAdapterId}
            onSaved={(next) => onLabelsSaved?.(next)}
          />
        </Modal>
      )}

      {hintOpen && projectId != null && (
        <Modal title={t('tableHint.tab')} onClose={() => setHintOpen(false)}>
          <TableHintEditor projectId={projectId} />
        </Modal>
      )}
    </Module>
  )
}
