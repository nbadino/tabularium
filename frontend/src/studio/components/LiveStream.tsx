/**
 * L'output del modello mentre arriva.
 *
 * Il prefill non restituisce un risultato: lo scrive. Questa superficie lo
 * mostra come viene prodotto — testo, elenchi, tabelle — con il cursore che
 * dice «sto ancora scrivendo» e lo scorrimento che resta incollato in fondo
 * finché non sei tu a risalire. Niente percentuali: il backend non ne emette,
 * e fingerle sarebbe teatro; l'unica misura onesta è il tempo trascorso.
 *
 * Il parsing è volutamente tollerante: durante lo streaming una cella OTSL
 * non ha il token successivo, una `<table>` HTML non ha ancora il tag di
 * chiusura, una riga Markdown è a metà. Le righe già complete sono comunque
 * leggibili, e si dipingono subito invece di aspettare la fine.
 */
import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n'

/* ------------------------------------------------------------- parsing --- */

/** Una tabella riconosciuta: `head` esiste solo se la sorgente la dichiara. */
interface ParsedTable {
  head: string[] | null
  rows: string[][]
}

type Chunk =
  | { kind: 'table'; table: ParsedTable }
  | { kind: 'heading'; text: string }
  | { kind: 'list'; items: string[] }
  | { kind: 'code'; text: string }
  | { kind: 'text'; text: string }

/** Righe OTSL già emesse: una cella finisce dove comincia la successiva.
 *  Nessuna intestazione: su questi registri la prima riga è spesso già una
 *  nave, e promuoverla a titolo di colonna sarebbe una bugia (v. AGENTS.md
 *  §2.3.3 — l'intestazione ripetuta va dichiarata, non indovinata). */
function otslRows(text: string): string[][] {
  return text
    .split('<nl>')
    .map((row) =>
      Array.from(
        row.matchAll(/<(fcel|ecel|lcel|ucel|xcel)>([\s\S]*?)(?=<(?:fcel|ecel|lcel|ucel|xcel)>|$)/gi),
      ).map((match) => (match[1].toLowerCase() === 'fcel' ? match[2].trim() : '')),
    )
    .filter((row) => row.length > 0)
}

/** Markdown: qui l'intestazione è dichiarata dalla riga `|---|---|`, quindi
 *  si può usare — è la sorgente a dirlo, non un'ipotesi nostra. */
function markdownTable(text: string): ParsedTable | null {
  const rows = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith('|') && line.endsWith('|') && line.split('|').length >= 3)
    .map((line) => line.slice(1, -1).split('|').map((cell) => cell.trim()))
  const sep = rows.findIndex((row) => row.length > 0 && row.every((cell) => /^:?-{2,}:?$/.test(cell)))
  const body = rows.filter((_, i) => i !== sep)
  if (body.length === 0) return null
  return sep === 1 ? { head: body[0], rows: body.slice(1) } : { head: null, rows: body }
}

/** HTML: l'intestazione esiste se il modello ha emesso `<th>`. */
function htmlTable(text: string): ParsedTable | null {
  const headerFirst = /<tr\b[^>]*>\s*<th\b/i.test(text)
  const rows = Array.from(text.matchAll(/<tr\b[^>]*>([\s\S]*?)(?=<tr\b|<\/table\b|$)/gi))
    .map((match) =>
      Array.from(match[1].matchAll(/<t[dh]\b[^>]*>([\s\S]*?)(?=<t[dh]\b|<\/tr\b|$)/gi)).map((cell) =>
        cell[1]
          .replace(/<br\s*\/?\s*>/gi, '\n')
          .replace(/<[^>]*>/g, '')
          .replace(/&nbsp;/gi, ' ')
          .replace(/&amp;/gi, '&')
          .replace(/&lt;/gi, '<')
          .replace(/&gt;/gi, '>')
          .trim(),
      ),
    )
    .filter((row) => row.length > 0)
  if (rows.length === 0) return null
  return headerFirst ? { head: rows[0], rows: rows.slice(1) } : { head: null, rows }
}

/** Il testo grezzo diventa i pezzi da dipingere. Esportata per i test. */
export function parseStream(text: string): Chunk[] {
  const otsl = otslRows(text)
  const table: ParsedTable | null =
    otsl.length > 0 ? { head: null, rows: otsl } : markdownTable(text) ?? htmlTable(text)
  if (table && table.rows.length > 0) return [{ kind: 'table', table }]

  // Struttura riconoscibile ma non ancora leggibile come tabella (JSON di
  // blocchi, OTSL senza righe complete): si mostra com'è, senza truccarla.
  if (/<\/?(?:table|fcel|ecel|lcel|ucel|xcel|nl)/i.test(text) || /^\s*[[{]/.test(text.trim())) {
    return [{ kind: 'code', text }]
  }

  return text
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part): Chunk => {
      if (/^#{1,3}\s+/.test(part)) return { kind: 'heading', text: part.replace(/^#{1,3}\s+/, '') }
      const lines = part.split('\n')
      if (lines.every((line) => /^\s*[-*]\s+/.test(line))) {
        return { kind: 'list', items: lines.map((line) => line.replace(/^\s*[-*]\s+/, '')) }
      }
      return { kind: 'text', text: part }
    })
}

/* --------------------------------------------------------------- resa ---- */

/** Numero, data, tonnellaggio: va a destra e incolonnato. Una cella con una
 *  parola dentro non lo è, anche se comincia per cifra. */
function isNumeric(cell: string): boolean {
  return cell.trim() !== '' && /^[\d.,'’\s/:+-]+$/.test(cell.trim())
}

function Table({ table }: { table: ParsedTable }) {
  const columns = Math.max(table.head?.length ?? 0, ...table.rows.map((r) => r.length))
  // Una colonna è numerica se lo sono le sue celle piene: si decide sulla
  // colonna, non sulla cella, altrimenti l'allineamento balla riga per riga.
  const numeric = Array.from({ length: columns }, (_, c) => {
    const values = table.rows.map((row) => row[c] ?? '').filter((v) => v.trim() !== '')
    return values.length > 0 && values.every(isNumeric)
  })
  return (
    <div className="overflow-x-auto border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]">
      <table className="dtable">
        {table.head && (
          <thead>
            <tr>
              {table.head.map((cell, c) => (
                <th key={c} scope="col">
                  {cell}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {table.rows.map((row, r) => (
            <tr key={r} className="stream-in">
              {Array.from({ length: columns }, (_, c) => (
                <td key={c} data-num={numeric[c] ? 'true' : undefined}>
                  {row[c] ?? ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Il cursore: mentre il modello scrive, la riga non è finita. */
function Caret() {
  return <span aria-hidden className="caret" />
}

function Chunks({ chunks, streaming }: { chunks: Chunk[]; streaming: boolean }) {
  const last = chunks.length - 1
  return (
    <div className="space-y-2 text-[13px] leading-relaxed">
      {chunks.map((chunk, i) => {
        const tail = streaming && i === last
        switch (chunk.kind) {
          case 'table':
            return (
              <div key={i} className="stream-in">
                <Table table={chunk.table} />
              </div>
            )
          case 'heading':
            return (
              <h4 key={i} className="stream-in text-[14px] font-bold">
                {chunk.text}
                {tail && <Caret />}
              </h4>
            )
          case 'list':
            return (
              <ul key={i} className="stream-in list-disc space-y-0.5 pl-5">
                {chunk.items.map((item, n) => (
                  <li key={n}>{item}</li>
                ))}
                {tail && <Caret />}
              </ul>
            )
          case 'code':
            return (
              <pre
                key={i}
                className="mono stream-in whitespace-pre-wrap break-words text-[11px] leading-relaxed"
              >
                {chunk.text}
                {tail && <Caret />}
              </pre>
            )
          default:
            return (
              <p key={i} className="stream-in m-0 whitespace-pre-wrap">
                {chunk.text}
                {tail && <Caret />}
              </p>
            )
        }
      })}
    </div>
  )
}

/* -------------------------------------------------------------- pannello -- */

/** Secondi trascorsi, al secondo: l'unica metrica che il backend garantisce. */
function Elapsed({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const s = Math.max(0, Math.round((now - startedAt) / 1000))
  return (
    <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
      {String(Math.floor(s / 60)).padStart(2, '0')}:{String(s % 60).padStart(2, '0')}
    </span>
  )
}

export interface LiveStreamProps {
  /** Presente solo mentre il modello sta scrivendo. */
  working: { engine: string; startedAt: number; blocks: number; last: string | null } | null
  /** Testo prodotto finora (resta visibile anche a stream finito). */
  text: string
  phase?: string
}

export default function LiveStream({ working, text, phase }: LiveStreamProps) {
  const { t, tn } = useI18n()
  const scroller = useRef<HTMLDivElement>(null)
  // Incollato in fondo mentre scrive, ma solo finché sei tu a lasciarlo lì:
  // se risali per rileggere, lo streaming non ti strappa via la riga.
  const stick = useRef(true)

  useEffect(() => {
    const el = scroller.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [text])

  const chunks = text ? parseStream(text) : []
  // La fase è un enum del backend: si mostra tradotta, e se è una fase nuova
  // che il dizionario non conosce ancora si tace invece di stampare il codice.
  const phaseKey = phase ? `content.phase.${phase}` : null
  const phaseLabel = phaseKey && t(phaseKey) !== phaseKey ? t(phaseKey) : null

  return (
    <section
      aria-label={t('content.streamTitle')}
      aria-busy={working != null}
      className="border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)]"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-2 py-1.5">
        {working && (
          <span
            aria-hidden
            className="h-3 w-3 shrink-0 animate-spin border-[1.5px] border-[color:var(--color-rule)] border-t-[color:var(--color-sig)]"
          />
        )}
        <span className="lbl !mb-0">{t('content.streamTitle')}</span>
        {phaseLabel && (
          <span className="text-[11px] text-[color:var(--color-ink-3)]">{phaseLabel}</span>
        )}
        {working && (
          <>
            <span className="text-[11px] text-[color:var(--color-ink-2)]">
              {working.blocks > 0
                ? tn('content.blocksSoFar', working.blocks, { last: working.last ?? '—' })
                : t('content.blocksSoFarNone')}
            </span>
            <span className="ml-auto flex items-center gap-2">
              <span className="text-[11px] text-[color:var(--color-ink-3)]">{working.engine}</span>
              <Elapsed startedAt={working.startedAt} />
            </span>
          </>
        )}
      </div>

      {(chunks.length > 0 || working) && (
        <div
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
          }}
          className="max-h-[38vh] overflow-y-auto border-t border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] px-2.5 py-2"
        >
          {chunks.length > 0 ? (
            <Chunks chunks={chunks} streaming={working != null} />
          ) : (
            <p className="m-0 text-[12px] text-[color:var(--color-ink-3)]">
              {t('content.streamWaiting')}
              <Caret />
            </p>
          )}
        </div>
      )}
    </section>
  )
}
