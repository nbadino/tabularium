import { useEffect, useRef, useState } from 'react'
import type {
  TableDetectOut,
  TableDetectRequest,
  TableGrid,
  TableGridOut,
} from '../../lib/types'
import { emptyGrid } from '../../lib/grid'
import { apiGet } from '../../lib/api'
import { Module } from '../../app/ui'
import { IconTrash } from '../../app/icons'
import { useI18n } from '../../i18n'
import type { LabelDef } from '../../lib/types'
import type { DisplayBlock, LivePrefillOutput, PrefillDraft } from '../types'
import SheetEditor from './SheetEditor'

/** Le classi che portano testo da trascrivere. Le altre (Picture, Column)
 *  entrano solo nel layout: la riga lo dichiara invece di fingere un editor. */
const NO_CONTENT_LABELS = new Set(['Picture', 'Column'])

/** Secondi trascorsi, aggiornati al secondo: l'unica metrica onesta durante
 *  il prefill — il backend non emette percentuali, fingerle sarebbe teatro. */
function Elapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const s = Math.max(0, Math.round((now - startedAt) / 1000))
  return (
    <span className="mono text-[11px] text-[color:var(--color-ink-2)]">
      {String(Math.floor(s / 60)).padStart(2, '0')}:{String(s % 60).padStart(2, '0')}
    </span>
  )
}

/** Striscia di stato durante il prefill: NON copre la lista, i blocchi che
 *  arrivano dallo stream compaiono subito sotto, come tutti gli altri. */
function WorkingStrip({
  working,
}: {
  working: { engine: string; startedAt: number; blocks: number; last: string | null; output?: LivePrefillOutput | null }
}) {
  const { t } = useI18n()
  return (
    <>
      <div className="mb-2 flex items-center gap-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] px-2 py-1.5">
        <div
          aria-hidden
          className="h-3.5 w-3.5 shrink-0 animate-spin border-[1.5px] border-[color:var(--color-rule)] border-t-[color:var(--color-sig)]"
        />
        <span className="text-[12px] text-[color:var(--color-ink-2)]">
          {t('content.workingBody', { engine: working.engine })}
        </span>
        <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
          {working.blocks > 0
            ? t('content.blocksSoFar', { n: working.blocks, last: working.last ?? '—' })
            : t('content.blocksSoFarNone')}
        </span>
        <Elapsed startedAt={working.startedAt} />
      </div>
      {working.output?.text && (
        <div className="mb-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] p-2">
          <div className="lbl mb-1">{t('content.liveOutput', { phase: working.output.phase })}</div>
          <LiveOutput text={working.output.text} />
        </div>
      )}
    </>
  )
}

function LiveOutput({ text }: { text: string }) {
  // OTSL non usa tag di chiusura: una cella termina al token successivo.
  // Questo parser volutamente tollerante permette di mostrare subito le righe
  // Paddle già emesse, anche mentre l'ultima cella è ancora in streaming.
  const otslRows = text
    .split('<nl>')
    .map((row) => Array.from(row.matchAll(/<(fcel|ecel|lcel|ucel|xcel)>([\s\S]*?)(?=<(?:fcel|ecel|lcel|ucel|xcel)>|$)/gi))
      .map((match) => ({ kind: match[1].toLowerCase(), text: match[2].trim() })))
    .filter((row) => row.some((cell) => cell.kind === 'fcel' || cell.kind === 'ecel'))
  if (otslRows.length > 0) {
    return (
      <div className="max-h-[34vh] overflow-auto border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]">
        <table className="min-w-full border-collapse text-[12px]">
          <tbody>
            {otslRows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className="border border-[color:var(--color-rule)] px-2 py-1 align-top whitespace-pre-wrap">
                    {cell.kind === 'fcel' ? cell.text : ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  // Unlimited-OCR emette le tabelle in Markdown. Durante lo streaming non
  // c'è ancora un tag di chiusura: le righe già complete sono comunque una
  // tabella utile da leggere, quindi le dipingiamo subito.
  const markdownRows = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && line.endsWith('|') && line.split('|').length >= 3)
    .map((line) => line.slice(1, -1).split('|').map((cell) => cell.trim()))
  const separator = markdownRows.findIndex((row) => row.length > 0 && row.every((cell) => /^:?-{2,}:?$/.test(cell)))
  const markdownVisibleRows = markdownRows.filter((_, index) => index !== separator)
  // HTML parziale: il modello può avere già emesso <tr><td>... senza aver
  // ancora chiuso <table>, <tr> o <td>. Estraiamo le celle disponibili con
  // un parser tollerante invece di aspettare il tag finale.
  const htmlRows = Array.from(text.matchAll(/<tr\b[^>]*>([\s\S]*?)(?=<tr\b|<\/table\b|$)/gi))
    .map((match) => Array.from(match[1].matchAll(/<t[dh]\b[^>]*>([\s\S]*?)(?=<t[dh]\b|<\/tr\b|$)/gi))
      .map((cell) => cell[1]
        .replace(/<br\s*\/?\s*>/gi, '\n')
        .replace(/<[^>]*>/g, '')
        .replace(/&nbsp;/gi, ' ')
        .replace(/&amp;/gi, '&')
        .replace(/&lt;/gi, '<')
        .replace(/&gt;/gi, '>')
        .trim()))
    .filter((row) => row.length > 0)
  const visibleRows = markdownVisibleRows.length > 0 ? markdownVisibleRows : htmlRows
  if (visibleRows.length > 0) {
    return (
      <div className="max-h-[34vh] overflow-auto border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)]">
        <table className="min-w-full border-collapse text-[12px]">
          <tbody>
            {visibleRows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className="border border-[color:var(--color-rule)] px-2 py-1 align-top whitespace-pre-wrap">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  const structured = /<\/?(?:table|fcel|ecel|lcel|ucel|xcel|nl)/i.test(text) || /^\s*\[\s*\{/.test(text)
  if (structured) {
    return <pre className="max-h-[34vh] overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed">{text}</pre>
  }
  return (
    <div className="max-h-[34vh] overflow-auto space-y-2 text-[12px] leading-relaxed">
      {text.split(/\n{2,}/).map((part, index) => {
        const lines = part.split('\n')
        if (lines.every((line) => /^\s*[-*]\s+/.test(line))) {
          return <ul key={index} className="list-disc pl-5">{lines.map((line) => <li key={line}>{line.replace(/^\s*[-*]\s+/, '')}</li>)}</ul>
        }
        if (/^\s*#{1,3}\s+/.test(part)) {
          return <h4 key={index} className="font-semibold">{part.replace(/^\s*#{1,3}\s+/, '')}</h4>
        }
        return <p key={index} className="m-0 whitespace-pre-wrap">{part}</p>
      })}
    </div>
  )
}

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
}: {
  id: string
  serverId: number | null
  onSaveTable: (serverId: number, grid: TableGrid) => Promise<string>
  onDetectTable?: (serverId: number, opts: TableDetectRequest) => Promise<TableDetectOut>
  initialGrid?: TableGrid | null
}) {
  const { t } = useI18n()
  const [grid, setGrid] = useState<TableGrid | null>(initialGrid ?? null)
  const [error, setError] = useState<string | null>(null)
  const loadedFor = useRef<string | null>(null)

  useEffect(() => {
    if (!serverId) return
    if (initialGrid) return
    if (loadedFor.current === id) return
    loadedFor.current = id
    setError(null)
    apiGet<TableGridOut>(`/blocks/${serverId}/table`)
      .then((out) => setGrid(out.grid ?? emptyGrid(3, 4)))
      .catch(() => setError(t('table.loadFailed')))
  }, [id, serverId, initialGrid, t])

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
      key={id}
      grid={grid}
      onSave={(g) => onSaveTable(serverId, g)}
      onDetect={onDetectTable ? (opts) => onDetectTable(serverId, opts) : undefined}
    />
  )
}

interface RowShellProps {
  id: string
  label: string
  labels?: LabelDef[]
  onLabel?: (label: string) => void
  confirmed: boolean
  onConfirmed: (v: boolean) => void
  onDelete?: () => void
  deleteTitle: string
  selected: boolean
  onSelect?: () => void
  cropUrl: string | null
  cropAlt: string
  cropUnsavedHint: string
  wide?: boolean
  children: React.ReactNode
}

/** Guscio comune a ogni riga: ritaglio a sinistra, azioni in testata, editor
 *  a destra. Un blocco confermato e una bozza di prefill condividono lo
 *  stesso guscio — cambia solo da dove arrivano dati e callback. */
function RowShell({
  id,
  label,
  labels,
  onLabel,
  confirmed,
  onConfirmed,
  onDelete,
  deleteTitle,
  selected,
  onSelect,
  cropUrl,
  cropAlt,
  cropUnsavedHint,
  wide,
  children,
}: RowShellProps) {
  return (
    <div
      id={`content-row-${id}`}
      className={`border-b border-[color:var(--color-rule)] p-2.5 last:border-b-0 ${
        selected ? 'bg-[color:var(--color-sig-wash)]' : ''
      }`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
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
        <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-[color:var(--color-ink-2)]">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => onConfirmed(e.target.checked)}
            className="accent-[color:var(--color-sig)]"
          />
          {confirmed ? '✓' : ''}
        </label>
        {onDelete && (
          <button type="button" onClick={onDelete} title={deleteTitle} className="btn btn-danger btn-sm ml-auto">
            <IconTrash size={12} />
          </button>
        )}
      </div>
      <div className={`flex min-w-0 gap-3 ${wide ? '' : 'items-start'}`}>
        <figure
          onClick={onSelect}
          className={`m-0 shrink-0 ${wide ? 'w-1/4' : 'w-1/4'} ${onSelect ? 'cursor-pointer' : ''}`}
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
    </div>
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
  onDraftContent: (serverId: number, content: string) => void
  onDraftGrid: (serverId: number, grid: TableGrid) => void
  onSaveDraftGrid: (serverId: number, grid: TableGrid) => Promise<string>
  onDraftConfirmed: (serverId: number, confirmed: boolean) => void
  onDraftReject: (serverId: number) => void
  liveOutput?: LivePrefillOutput | null
  /** Streaming del prefill in corso: una striscia sottile sopra la lista,
   *  non un pannello che la sostituisce — i blocchi arrivano SOTTO, in
   *  diretta. */
  working: { engine: string; startedAt: number; blocks: number; last: string | null; output?: LivePrefillOutput | null } | null
}

/** Pannello contenuto: l'intero output OCR/LLM della pagina, una riga per
 *  blocco — ritaglio e testo insieme, sempre visibili, senza dover cliccare
 *  ogni riquadro sul canvas uno alla volta. Le tabelle si correggono come un
 *  foglio di calcolo (SheetEditor), non come una riga di testo grezzo. */
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
  onDraftContent,
  onDraftGrid,
  onSaveDraftGrid,
  onDraftConfirmed,
  onDraftReject,
  liveOutput,
  working,
}: ContentPaneProps) {
  const { t } = useI18n()

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

  if (sortedBlocks.length === 0 && pendingDrafts.length === 0) {
    return (
      <Module tab={t('content.paneTab')} quiet>
        {working && <WorkingStrip working={working} />}
        {!working && liveOutput?.text && (
          <div className="mb-2 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] p-2">
            <div className="lbl mb-1">{t('content.liveOutput', { phase: liveOutput.phase })}</div>
            <LiveOutput text={liveOutput.text} />
          </div>
        )}
        <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('content.emptyPageBody')}</p>
      </Module>
    )
  }

  return (
    <Module tab={t('content.paneTab')} flush>
      <div className="flex flex-col">
        {working && (
          <div className="p-2 pb-0">
            <WorkingStrip working={working} />
          </div>
        )}
        {!working && liveOutput?.text && (
          <div className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2">
            <div className="lbl mb-1">{t('content.liveOutput', { phase: liveOutput.phase })}</div>
            <LiveOutput text={liveOutput.text} />
          </div>
        )}
        {sortedBlocks.map((block) => {
          const isTable = block.label === 'Table'
          const noContent = NO_CONTENT_LABELS.has(block.label)
          return (
            <RowShell
              key={block.id}
              id={block.id}
              label={block.label}
              labels={labels}
              onLabel={(label) => onLabel(block.id, label)}
              confirmed={block.confirmed}
              onConfirmed={(v) => onConfirmed(block.id, v)}
              onDelete={() => onDelete(block.id)}
              deleteTitle={t('inspector.deleteBlock')}
              selected={selectedId === block.id}
              onSelect={() => onSelect(block.id)}
              cropUrl={block.serverId ? `/api/blocks/${block.serverId}/crop` : null}
              cropAlt={t('content.cropAlt', { label: block.label })}
              cropUnsavedHint={t('content.cropUnsaved')}
              wide={isTable}
            >
              {isTable ? (
                <TableBlockEditor
                  id={block.id}
                  serverId={block.serverId}
                  onSaveTable={onSaveTable}
                  onDetectTable={onDetectTable}
                />
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
              label={draft.label}
              confirmed={draft.confirmed}
              onConfirmed={(v) => onDraftConfirmed(draft.serverId, v)}
              onDelete={() => onDraftReject(draft.serverId)}
              deleteTitle={t('content.rejectDraft')}
              selected={false}
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
      </div>
    </Module>
  )
}
