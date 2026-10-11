/**
 * Tavolozza dei comandi: ⌘K (Ctrl+K fuori dal Mac) da qualunque schermata.
 *
 * Porta a una sezione, a un progetto, a una pagina del progetto attivo, o
 * esegue un'azione rara (lingua, uscita) senza passare dai menu. Non sostituisce
 * il rail né i menu — ogni comando resta raggiungibile anche col mouse — ma
 * chi lavora da tastiera su centinaia di pagine non deve staccare le mani.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router'
import { apiGet } from '../lib/api'
import { pageLabel } from '../lib/pageLabel'
import type { PageItem, Project } from '../lib/types'
import { LOCALES, LOCALE_LABELS, useI18n } from '../i18n'
import { readActiveProject, writeActiveProject } from './activeProject'
import { useAuth } from './auth'
import { IconSearch } from './icons'

// `kind` è ciò che il comando è (sezione, progetto, pagina, azione): sta a destra della riga.
type Command = { id: string; kind: string; label: string; run: () => void }

// Oltre, la lista smette di essere una scelta: si affina la ricerca.
const MAX_RESULTS = 50

const SECTIONS = [
  { to: '/', key: 'nav.recognize' },
  { to: '/annotazione', key: 'nav.annotate' },
  { to: '/risultati', key: 'nav.results' },
  { to: '/archivio', key: 'nav.archive' },
  { to: '/modelli', key: 'nav.models' },
  { to: '/progetti', key: 'nav.projects' },
  { to: '/dataset', key: 'nav.dataset' },
  { to: '/training', key: 'nav.training' },
  { to: '/valutazione', key: 'nav.evaluation' },
  { to: '/playground', key: 'nav.playground' },
  { to: '/impostazioni', key: 'nav.settings' },
] as const

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

/** Ogni parola della ricerca deve comparire nell'etichetta, in qualunque ordine. */
export function matchesQuery(label: string, query: string): boolean {
  const l = label.toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => l.includes(w))
}

export function CommandPalette() {
  const { t, locale, setLocale } = useI18n()
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [projects, setProjects] = useState<Project[]>([])
  const [pages, setPages] = useState<{ project: Project; items: PageItem[] } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const returnTo = useRef<HTMLElement | null>(null)
  const listId = useId()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Sul Mac solo ⌘K: Ctrl+K nei campi di testo cancella fino a fine riga,
      // e trascrivendo serve. Altrove Ctrl+K, come nelle app del sistema.
      if ((IS_MAC ? e.metaKey : e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  // All'apertura: ricerca vuota, fuoco nel campo, dati freschi. Alla chiusura
  // il fuoco torna dov'era.
  useEffect(() => {
    if (!open) return
    returnTo.current = document.activeElement as HTMLElement | null
    setQuery('')
    setActive(0)
    inputRef.current?.focus()
    let alive = true
    apiGet<{ items: Project[] }>('/projects')
      .then((r) => {
        if (!alive) return
        setProjects(r.items)
        const pid = readActiveProject()
        const project = r.items.find((p) => p.id === pid)
        if (project)
          apiGet<{ items: PageItem[] }>(`/projects/${project.id}/pages`)
            .then((p) => alive && setPages({ project, items: p.items }))
            .catch(() => {})
      })
      .catch(() => {})
    return () => {
      alive = false
      returnTo.current?.focus?.()
    }
  }, [open])

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to)
    const out: Command[] = SECTIONS.map((s) => ({
      id: `go:${s.to}`,
      kind: t('commands.groupGo'),
      label: t(s.key),
      run: go(s.to),
    }))
    if (user?.role === 'admin')
      out.push({ id: 'go:/utenti', kind: t('commands.groupGo'), label: t('nav.users'), run: go('/utenti') })
    for (const p of projects)
      out.push({
        id: `project:${p.id}`,
        kind: t('commands.kindProject'),
        label: p.name,
        run: () => {
          writeActiveProject(p.id)
          go(`/annotazione?project=${p.id}`)()
        },
      })
    // Le pagine sono centinaia: entrano solo quando si cerca qualcosa.
    if (pages && query.trim())
      for (const pg of pages.items)
        out.push({
          id: `page:${pg.id}`,
          kind: `${t('commands.kindPage')} · ${pages.project.name}`,
          label: pageLabel(pg),
          run: go(`/annotazione?project=${pages.project.id}&page=${pg.id}`),
        })
    for (const l of LOCALES)
      if (l !== locale)
        out.push({
          id: `locale:${l}`,
          kind: t('commands.kindAction'),
          label: `${t('commands.language')}: ${LOCALE_LABELS[l]}`,
          run: () => setLocale(l),
        })
    if (user)
      out.push({ id: 'logout', kind: t('commands.kindAction'), label: t('layout.logout'), run: () => void logout() })
    return out
  }, [t, user, projects, pages, query, locale, setLocale, logout, navigate])

  const results = useMemo(
    () => commands.filter((c) => matchesQuery(`${c.label} ${c.kind}`, query)).slice(0, MAX_RESULTS),
    [commands, query],
  )
  const current = Math.min(active, Math.max(0, results.length - 1))

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${current}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [current])

  const runAt = (i: number) => {
    const cmd = results[i]
    if (!cmd) return
    setOpen(false)
    cmd.run()
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn btn-sm text-[color:var(--color-ink-2)]"
        aria-keyshortcuts={IS_MAC ? 'Meta+K' : 'Control+K'}
      >
        <IconSearch size={12} />
        {t('commands.open')}
        <kbd className="mono rounded-[var(--radius-sm)] border border-[color:var(--color-rule)] px-1 text-[11px] text-[color:var(--color-ink-3)]">
          {IS_MAC ? '⌘K' : 'Ctrl K'}
        </kbd>
      </button>
      {open &&
        createPortal(
          <div
            className="modal-backdrop fixed inset-0 z-50 flex items-start justify-center bg-[rgb(17_17_17/0.3)] px-4 pt-[10vh]"
            onMouseDown={(e) => {
              if (e.target === e.currentTarget) setOpen(false)
            }}
          >
            {/* Una barra larga e bassa, come Spotlight: il campo in alto, i
                risultati in un elenco breve sotto, ciò che ogni voce è a destra. */}
            <div
              role="dialog"
              aria-modal="true"
              aria-label={t('commands.title')}
              className="modal-panel float-layer flex w-full max-w-3xl flex-col overflow-hidden"
            >
              <div className="flex items-center gap-3 px-4">
                <IconSearch size={16} className="shrink-0 text-[color:var(--color-ink-3)]" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    setActive(0)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'ArrowDown') {
                      e.preventDefault()
                      setActive((current + 1) % Math.max(1, results.length))
                    } else if (e.key === 'ArrowUp') {
                      e.preventDefault()
                      setActive((current - 1 + results.length) % Math.max(1, results.length))
                    } else if (e.key === 'Enter') {
                      e.preventDefault()
                      runAt(current)
                    } else if (e.key === 'Escape') {
                      e.preventDefault()
                      setOpen(false)
                    } else if (e.key === 'Tab') {
                      e.preventDefault() // il fuoco resta nel campo: la lista si scorre con le frecce
                    }
                  }}
                  role="combobox"
                  aria-expanded="true"
                  aria-controls={listId}
                  aria-activedescendant={results[current] ? `${listId}-${current}` : undefined}
                  aria-autocomplete="list"
                  placeholder={t('commands.placeholder')}
                  className="h-14 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-[color:var(--color-ink-3)]"
                />
                <kbd className="mono shrink-0 rounded-[var(--radius-sm)] border border-[color:var(--color-rule)] px-1.5 text-[11px] text-[color:var(--color-ink-3)]">
                  Esc
                </kbd>
              </div>
              <ul
                ref={listRef}
                id={listId}
                role="listbox"
                className="max-h-[min(14.5rem,45vh)] overflow-y-auto border-t border-[color:var(--color-rule)] p-1.5"
              >
                {results.length === 0 && (
                  <li className="px-3 py-5 text-center text-[12px] text-[color:var(--color-ink-3)]">{t('commands.empty')}</li>
                )}
                {results.map((c, i) => (
                  <li
                    key={c.id}
                    id={`${listId}-${i}`}
                    data-index={i}
                    role="option"
                    aria-selected={i === current}
                    onMouseMove={() => i !== current && setActive(i)}
                    onClick={() => runAt(i)}
                    className={`flex cursor-pointer items-baseline justify-between gap-4 rounded-[var(--radius-md)] px-3 py-2 text-[13px] ${
                      i === current ? 'bg-[color:var(--color-sig-wash)]' : ''
                    }`}
                  >
                    <span className="truncate">{c.label}</span>
                    <span className="shrink-0 truncate text-[11px] text-[color:var(--color-ink-3)]">{c.kind}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}
