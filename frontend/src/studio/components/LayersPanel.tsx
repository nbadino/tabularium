import { useRef } from 'react'
import type { DisplayBlock } from '../types'
import { Module } from '../../app/ui'
import { IconDown, IconTrash, IconUp } from '../../app/icons'
import { useI18n } from '../../i18n'

interface LayersPanelProps {
  blocks: DisplayBlock[]
  selectedId: string | null
  colorFor: (label: string) => string
  onSelect: (id: string | null) => void
  onMove: (id: string, dir: -1 | 1) => void
  onDelete: (id: string) => void
  onReorderReset: () => void
}

/**
 * L'elenco dei livelli è anche l'equivalente accessibile del canvas: i
 * blocchi disegnati su Konva non esistono nel DOM, quindi qui vivono come
 * lista vera, navigabile da tastiera e leggibile da uno screen reader.
 *
 * Le azioni non compaiono al passaggio del mouse: sono sempre nel DOM, con
 * il loro nome accessibile, e diventano visibili anche col solo focus.
 */
export default function LayersPanel({
  blocks,
  selectedId,
  colorFor,
  onSelect,
  onMove,
  onDelete,
  onReorderReset,
}: LayersPanelProps) {
  const { t } = useI18n()
  const listRef = useRef<HTMLUListElement>(null)

  const sorted = [...blocks].sort(
    (a, b) =>
      (a.orderIdx ?? Number.MAX_SAFE_INTEGER) - (b.orderIdx ?? Number.MAX_SAFE_INTEGER),
  )

  /** Frecce per scorrere l'elenco, Alt+frecce per riordinare il blocco. */
  const onKeyDown = (e: React.KeyboardEvent, b: DisplayBlock, i: number) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (e.altKey) {
        onMove(b.id, e.key === 'ArrowDown' ? 1 : -1)
        return
      }
      const next = sorted[i + (e.key === 'ArrowDown' ? 1 : -1)]
      if (next) {
        onSelect(next.id)
        listRef.current
          ?.querySelector<HTMLElement>(`[data-block="${CSS.escape(next.id)}"]`)
          ?.focus()
      }
      return
    }
    if (e.key === 'Delete') {
      e.preventDefault()
      onDelete(b.id)
    }
  }

  return (
    <Module
      tab={t('layers.tab')}
      quiet
      flush
      aux={
        <button
          type="button"
          onClick={onReorderReset}
          className="btn btn-sm"
          title={t('layers.renumberTitle')}
        >
          {t('layers.renumber')}
        </button>
      }
    >
      {sorted.length === 0 ? (
        <p className="p-2 text-[12px] text-[color:var(--color-ink-2)]">
          {t('layers.empty')}
        </p>
      ) : (
        <>
          <p className="border-b border-[color:var(--color-rule)] px-2 py-1 text-[11px] text-[color:var(--color-ink-3)]">
            {t('layers.keys')}
          </p>
          <ul ref={listRef} className="ruled max-h-64 overflow-y-auto">
            {sorted.map((b, i) => {
              const selected = selectedId === b.id
              const prefillSuffix = b.prefill ? t('layers.prefillSuffix') : ''
              return (
                <li key={b.id}>
                  <div
                    data-block={b.id}
                    role="button"
                    tabIndex={selected || (selectedId === null && i === 0) ? 0 : -1}
                    aria-pressed={selected}
                    aria-label={t('layers.blockAria', { n: i + 1, label: b.label }) + prefillSuffix}
                    onClick={() => onSelect(b.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        onSelect(b.id)
                        return
                      }
                      onKeyDown(e, b, i)
                    }}
                    className={`group flex cursor-pointer items-center gap-2 px-2 py-1 text-[12px] ${
                      selected
                        ? 'bg-[color:var(--color-sig-wash)] font-semibold'
                        : 'hover:bg-[color:var(--color-fill)]'
                    }`}
                  >
                    <span className="mono w-5 shrink-0 text-right text-[11px] text-[color:var(--color-ink-3)]">
                      {b.orderIdx ?? i}
                    </span>
                    <span
                      className="h-3 w-3 shrink-0 border border-[color:var(--color-rule-strong)]"
                      style={{ background: colorFor(b.label) }}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 truncate">{b.label}</span>
                    {b.prefill && (
                      <span
                        className="badge shrink-0 text-[color:var(--color-ink-3)]"
                        title={t('layers.prefillTitle', { source: b.prefill })}
                      >
                        OCR
                      </span>
                    )}
                    <span className="flex shrink-0 items-center opacity-45 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          onMove(b.id, -1)
                        }}
                        disabled={i === 0}
                        aria-label={t('layers.moveUp', { label: b.label })}
                        className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)] disabled:opacity-30"
                      >
                        <IconUp size={11} />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          onMove(b.id, 1)
                        }}
                        disabled={i === sorted.length - 1}
                        aria-label={t('layers.moveDown', { label: b.label })}
                        className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-ink)] disabled:opacity-30"
                      >
                        <IconDown size={11} />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          onDelete(b.id)
                        }}
                        aria-label={t('layers.delete', { label: b.label })}
                        className="p-1 text-[color:var(--color-ink-2)] hover:text-[color:var(--color-sig-text)]"
                      >
                        <IconTrash size={11} />
                      </button>
                    </span>
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </Module>
  )
}