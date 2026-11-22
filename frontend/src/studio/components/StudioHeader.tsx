/**
 * The Studio's header (docs/UX_SIMPLIFICATION.md, Studio): back to the pages,
 * which page this is, and one way to finish it — "verify and go to the next",
 * also as Ctrl/Cmd+Enter. J and K move between pages when not typing.
 */
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { useI18n } from '../../i18n'
import { IconCheck, IconNext, IconPrev, IconRedo, IconUndo } from '../../app/icons'
import { Modal } from '../../app/ui'

export type StudioFinish = {
  /** verify: approve then move on; next: already approved, only move on; last: approve, nothing after. */
  kind: 'verify' | 'next' | 'last'
  onClick: () => void
  disabled: boolean
}

/** Assignment and review only matter once more than one person works on the project. */
export function isTeamWork(
  project: { members_count?: number | null } | undefined,
  page: { work?: { assignee_id?: number | null; reviewer_id?: number | null } | null } | undefined,
): boolean {
  if ((project?.members_count ?? 1) > 1) return true
  return Boolean(page?.work?.assignee_id || page?.work?.reviewer_id)
}

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
}

export default function StudioHeader({
  backTo,
  pageName,
  index,
  total,
  finish,
  onPrev,
  onNext,
  sidebarOpen,
  onToggleSidebar,
  mode,
  onMode,
  undo,
  children,
}: {
  backTo: string
  pageName: string | null
  /** Zero-based position of the page in the list, or -1. */
  index: number
  total: number
  finish: StudioFinish | null
  onPrev: () => void
  onNext: () => void
  sidebarOpen: boolean
  onToggleSidebar: () => void
  mode?: 'all' | 'canvas' | 'transcription'
  onMode?: (mode: 'all' | 'canvas' | 'transcription') => void
  undo?: {
    canUndo: boolean
    canRedo: boolean
    onUndo: () => void
    onRedo: () => void
    disabled?: boolean
  }
  /** Team controls (assignment, review), rendered only for team work. */
  children?: ReactNode
}) {
  const { t } = useI18n()
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const latest = useRef({ finish, onPrev, onNext })
  latest.current = { finish, onPrev, onNext }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const { finish: f, onPrev: prev, onNext: next } = latest.current
      if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        if (f && !f.disabled) {
          e.preventDefault()
          f.onClick()
        }
        return
      }
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return
      if (e.key === 'j') next()
      else if (e.key === 'k') prev()
      else if (e.key === '?') {
        e.preventDefault()
        setShortcutsOpen((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const finishLabel = finish?.kind === 'next' ? t('studio.next') : finish?.kind === 'last' ? t('studio.verify') : t('studio.verifyNext')

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[color:var(--color-rule)] px-3 py-1.5">
      <Link to={backTo} className="btn btn-sm no-underline">
        <IconPrev size={11} />
        {t('studio.backToPages')}
      </Link>
      <button type="button" className="btn btn-sm" aria-pressed={sidebarOpen} onClick={onToggleSidebar}>
        {t('studio.pagesList')}
      </button>
      {mode && onMode && (
        <div
          role="group"
          aria-label={t('annotate.viewModeAria')}
          className="inline-flex overflow-hidden rounded-md border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)]"
        >
          {(['all', 'canvas', 'transcription'] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => onMode(m)}
              className={`btn btn-sm whitespace-nowrap rounded-none border-0 border-r border-[color:var(--color-rule)] last:border-r-0 ${
                mode === m ? 'bg-[color:var(--color-ink)] text-white' : ''
              }`}
            >
              {t(`annotate.view${m[0].toUpperCase()}${m.slice(1)}`)}
            </button>
          ))}
        </div>
      )}
      {pageName && (
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="mono max-w-[32ch] truncate text-[13px] font-semibold">{pageName}</span>
          {index >= 0 && total > 0 && (
            <span className="whitespace-nowrap text-[12px] text-[color:var(--color-ink-2)]">{t('studio.position', { n: index + 1, total })}</span>
          )}
        </span>
      )}
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {undo && (
          <div className="inline-flex items-center gap-1">
            <button
              type="button"
              onClick={undo.onUndo}
              disabled={!undo.canUndo || undo.disabled}
              title={t('annotate.undoTitle')}
              className="btn btn-sm"
            >
              <IconUndo size={12} />
              <span className="hidden sm:inline">{t('annotate.undo')}</span>
            </button>
            <button
              type="button"
              onClick={undo.onRedo}
              disabled={!undo.canRedo || undo.disabled}
              title={t('annotate.redoTitle')}
              className="btn btn-sm"
            >
              <IconRedo size={12} />
              <span className="hidden sm:inline">{t('annotate.redo')}</span>
            </button>
          </div>
        )}
        <button
          type="button"
          className="btn btn-sm mono"
          aria-label={t('studio.shortcutsTitle')}
          title={t('studio.shortcutsTitle')}
          onClick={() => setShortcutsOpen(true)}
        >
          ?
        </button>
        {children}
        {finish && (
          <button
            type="button"
            onClick={finish.onClick}
            disabled={finish.disabled}
            aria-keyshortcuts="Control+Enter Meta+Enter"
            title={t('studio.finishShortcut')}
            className="btn btn-primary whitespace-nowrap"
          >
            {finish.kind === 'next' ? <IconNext size={12} /> : <IconCheck size={12} />}
            {finishLabel}
          </button>
        )}
      </div>
      {shortcutsOpen && (
        <Modal title={t('studio.shortcutsTitle')} onClose={() => setShortcutsOpen(false)}>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 p-3 text-[13px]">
            {[
              ['V / R / P / H', t('studio.shortcutTools')],
              ['1 / 2 / 3', t('studio.shortcutViews')],
              ['J / K', t('studio.shortcutPages')],
              ['Space', t('studio.shortcutPan')],
              ['Ctrl+Z / Ctrl+Shift+Z', t('studio.shortcutUndoRedo')],
              ['Ctrl+Enter', t('studio.shortcutVerify')],
            ].map(([keys, desc]) => (
              <div key={keys} className="contents">
                <dt className="mono font-semibold text-[color:var(--color-ink)]">{keys}</dt>
                <dd className="text-[color:var(--color-ink-2)]">{desc}</dd>
              </div>
            ))}
          </dl>
        </Modal>
      )}
    </div>
  )
}
