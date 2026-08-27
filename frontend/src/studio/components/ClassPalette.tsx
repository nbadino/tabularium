import { useEffect } from 'react'
import type { LabelDef } from '../../lib/types'
import { Module } from '../../app/ui'

/**
 * Le classi, con le scorciatoie che promettono davvero qualcosa: i tasti
 * 1–9 selezionano le prime nove classi. Prima il codice `short` era stampato
 * accanto al nome come se fosse una scorciatoia, e non lo era.
 */
export default function ClassPalette({
  labels,
  active,
  onSelect,
}: {
  labels: LabelDef[]
  active: string
  onSelect: (name: string) => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const n = Number(e.key)
      if (!Number.isInteger(n) || n < 1 || n > 9) return
      const label = labels[n - 1]
      if (label) {
        e.preventDefault()
        onSelect(label.name)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [labels, onSelect])

  return (
    <Module tab="Tassonomia" quiet flush aux={<span>tasti 1–9</span>}>
      <ul className="ruled max-h-56 overflow-y-auto">
        {labels.map((l, i) => (
          <li key={l.name}>
            <button
              type="button"
              onClick={() => onSelect(l.name)}
              aria-pressed={active === l.name}
              className={`flex w-full items-center gap-2 px-2 py-1 text-left text-[12px] ${
                active === l.name
                  ? 'bg-[color:var(--color-sig-wash)] font-semibold'
                  : 'hover:bg-[color:var(--color-fill)]'
              }`}
            >
              <span
                className="h-3 w-3 shrink-0 border border-[color:var(--color-rule-strong)]"
                style={{ background: l.color }}
                aria-hidden="true"
              />
              <span className="min-w-0 flex-1 truncate">{l.name}</span>
              {i < 9 && (
                <kbd className="mono shrink-0 border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-1 text-[10px] text-[color:var(--color-ink-2)]">
                  {i + 1}
                </kbd>
              )}
            </button>
          </li>
        ))}
      </ul>
    </Module>
  )
}
