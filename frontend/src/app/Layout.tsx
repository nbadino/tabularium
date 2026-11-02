/** Navigazione globale per attività, con sottosezioni visibili nel loro contesto. */
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { apiGet } from '../lib/api'
import { syncInferenceFromBackend } from './inference'
import type { HealthResponse, Project } from '../lib/types'
import { projectIdOf, projectPath, switchProjectPath } from './projectPaths'
import { writeActiveProject } from './activeProject'
import { LOCALES, LOCALE_LABELS, useI18n } from '../i18n'
import { useAuth } from './auth'
import { CommandPalette } from './CommandPalette'
import { Menu, PageSkeleton } from './ui'
import { IconArchive, IconModels } from './icons'

interface NavItem {
  to: string
  labelKey: string
  Icon: (p: { size?: number }) => React.ReactElement
}

// The project is the container of the work (docs/UX_SIMPLIFICATION.md): the
// main navigation only chooses between projects and the instance's models.
const NAV: NavItem[] = [
  { to: '/progetti', labelKey: 'nav.projects', Icon: IconArchive },
  { to: '/modelli', labelKey: 'nav.models', Icon: IconModels },
]

/** IT · EN · FR in una riga: la lingua corrente in grassetto, il nome intero nel titolo. */
function LocaleSwitch() {
  const { t, locale, setLocale } = useI18n()
  return (
    <div className="flex items-center px-1 py-0.5" role="group" aria-label={t('layout.language')}>
      {LOCALES.map((l, i) => [
        i > 0 && (
          <span key={`sep-${l}`} aria-hidden="true" className="px-0.5 text-[color:var(--color-ink-3)]">
            ·
          </span>
        ),
        <button
          key={l}
          type="button"
          onClick={() => setLocale(l)}
          aria-pressed={l === locale}
          aria-label={LOCALE_LABELS[l]}
          title={LOCALE_LABELS[l]}
          className="menu-choice"
        >
          {l.toUpperCase()}
        </button>,
      ])}
    </div>
  )
}

/**
 * Chi è connesso e i comandi dell'istanza in un menu solo: Utenti e
 * Impostazioni (per l'admin), lingua, uscita. Servono di rado e
 * stavano tutti in testata. L'unica cosa che resta fuori è l'allarme
 * «backend non raggiungibile», che non può aspettare un clic.
 */
function AccountMenu() {
  const { t } = useI18n()
  const { user, logout } = useAuth()
  const [down, setDown] = useState(false)

  // Solo l'allarme: la versione sta in Impostazioni.
  useEffect(() => {
    apiGet<HealthResponse>('/health').catch(() => setDown(true))
  }, [])

  const roleKey = user ? `users.role${user.role.charAt(0).toUpperCase()}${user.role.slice(1)}` : null
  return (
    <>
      {down && (
        <span className="badge border-[color:var(--color-sig-plate)] bg-[color:var(--color-sig-plate)] text-white">
          {t('app.backendDown')}
        </span>
      )}
      <Menu
        label={<span className="max-w-[14ch] truncate">{user?.username ?? t('layout.menu')}</span>}
      >
        {user && roleKey && (
          <>
            <div className="px-2 pt-1 pb-1.5">
              <div className="max-w-[24ch] truncate text-[13px] font-semibold">{user.username}</div>
              <div className="text-[11px] text-[color:var(--color-ink-3)]">{t(roleKey)}</div>
            </div>
            <hr className="menu-sep" />
          </>
        )}
        {user?.role === 'admin' && (
          <NavLink to="/utenti" className="menu-item">
            {t('nav.users')}
          </NavLink>
        )}
        <NavLink to="/impostazioni" className="menu-item">
          {t('nav.settings')}
        </NavLink>
        <LocaleSwitch />
        {user && (
          <>
            <hr className="menu-sep" />
            <button type="button" onClick={() => void logout()} className="menu-item">
              {t('layout.logout')}
            </button>
          </>
        )}
      </Menu>
    </>
  )
}

const MODEL_PATHS = ['/modelli', '/playground']
function sectionOf(path: string): string {
  if (MODEL_PATHS.includes(path)) return '/modelli'
  if (path === '/' || path === '/progetti' || path.startsWith('/progetti/')) return '/progetti'
  return path
}

const PROJECT_TABS = [
  { section: '', key: 'nav.pages' },
  { section: 'riconoscimenti', key: 'nav.recognitions' },
  { section: 'fine-tuning', key: 'nav.training' },
] as const

const FINE_TUNING_STEPS = [
  { section: 'fine-tuning', key: 'ux.overview' },
  { section: 'fine-tuning/dati', key: 'workflow.dataset' },
  { section: 'fine-tuning/addestra', key: 'workflow.train' },
  { section: 'fine-tuning/valuta', key: 'workflow.evaluate' },
  { section: 'fine-tuning/confronta', key: 'nav.benchmark' },
] as const

/**
 * What remounts the page: a new section or a new project, not a new item inside
 * the same section (opening another run keeps the results page mounted).
 */
function pageKey(pathname: string): string {
  const m = /^\/progetti\/(\d+)\/?([^/]*)/.exec(pathname)
  return m ? `/progetti/${m[1]}/${m[2]}` : pathname
}

/** The project's own bar: which project (switchable) and its three sections. */
function ProjectNav({ id, pathname }: { id: number; pathname: string }) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const [projects, setProjects] = useState<Project[] | null>(null)
  useEffect(() => {
    writeActiveProject(id)
  }, [id])
  useEffect(() => {
    let alive = true
    apiGet<{ items: Project[] }>('/projects').then((r) => alive && setProjects(r.items)).catch(() => {})
    return () => {
      alive = false
    }
  }, [])
  const rest = pathname.replace(/^\/progetti\/\d+\/?/, '')
  const current = rest.startsWith('riconoscimenti') ? 'riconoscimenti' : rest.startsWith('fine-tuning') ? 'fine-tuning' : rest.startsWith('studio') ? 'studio' : ''
  return (
    <>
      <nav aria-label={t('nav.projectNav')} className="section-nav items-center">
        {projects && projects.length > 1 ? (
          <select
            aria-label={t('nav.switchProject')}
            value={id}
            onChange={(e) => navigate(switchProjectPath(pathname, Number(e.target.value)))}
            className="fld mr-2 w-auto max-w-[28ch] py-0.5 text-[13px] font-semibold"
          >
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        ) : (
          <span className="mr-2 max-w-[28ch] truncate text-[13px] font-semibold">
            {projects?.find((p) => p.id === id)?.name ?? ''}
          </span>
        )}
        {PROJECT_TABS.map(({ section, key }) => (
          <Link
            key={key}
            to={section ? projectPath(id, section) : projectPath(id)}
            aria-current={current === section ? 'page' : undefined}
            className="section-link"
          >
            {t(key)}
          </Link>
        ))}
      </nav>
      {current === 'fine-tuning' && (
        <nav aria-label={t('workflow.refineNav')} className="section-nav">
          {FINE_TUNING_STEPS.map(({ section, key }) => (
            <NavLink key={section} to={projectPath(id, section)} end className="section-link">
              {t(key)}
            </NavLink>
          ))}
        </nav>
      )}
    </>
  )
}

/**
 * La piastra rossa sotto la linguetta viva. Una sola per tutto il rail: cambiando
 * sezione scorre dalla linguetta vecchia alla nuova invece di saltare. La
 * posizione si misura dopo il render (e a ogni cambio di larghezza: lingua,
 * finestra); la transizione si accende solo dopo la prima misura, così
 * all'avvio la piastra non arriva scivolando da sinistra.
 */
function NavMark({ pathname }: { pathname: string }) {
  // Il rail si trova risalendo dalla piastra: un ref sul <nav> arriverebbe
  // dopo questo effetto (React aggancia i ref del genitore dopo i figli).
  const ref = useRef<HTMLSpanElement>(null)
  const [box, setBox] = useState<{ x: number; w: number } | null>(null)
  const [ready, setReady] = useState(false)
  useLayoutEffect(() => {
    const root = ref.current?.parentElement
    if (!root) return
    const measure = () => {
      const el = root.querySelector<HTMLElement>('.navtab[data-active="true"]')
      setBox(el ? { x: el.offsetLeft, w: el.offsetWidth } : null)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(root)
    root.querySelectorAll('.navtab').forEach((el) => ro.observe(el))
    return () => ro.disconnect()
  }, [pathname])
  // Un timer e non requestAnimationFrame: in una scheda in background i frame
  // sono sospesi e la piastra non scorrerebbe mai.
  useEffect(() => {
    if (!box || ready) return
    const id = window.setTimeout(() => setReady(true), 50)
    return () => window.clearTimeout(id)
  }, [box, ready])
  return (
    <span
      ref={ref}
      aria-hidden="true"
      className="navtab-mark"
      data-ready={ready || undefined}
      style={box ? { width: box.w, transform: `translateX(${box.x}px)` } : { display: 'none' }}
    />
  )
}

export default function Layout() {
  const { t } = useI18n()
  const location = useLocation()
  // Il contenuto è scorso sotto la testata: lei lo dice con un'ombra.
  const [scrolled, setScrolled] = useState(false)
  const projectId = projectIdOf(location.pathname)
  // Ogni pagina legge lo stato inferenza (chip GPU, banner, motori prefill):
  // il sync all'avvio evita di mostrare disponibilità/latenza stantie da
  // localStorage, che prima si aggiornavano solo aprendo la Home.
  useEffect(() => {
    void syncInferenceFromBackend().catch(() => {})
    const id = setInterval(() => void syncInferenceFromBackend().catch(() => {}), 30_000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="flex h-screen min-w-0 flex-col overflow-hidden bg-[color:var(--color-sheet)]">
      <a
        href="#contenuto"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-[color:var(--color-ink)] focus:px-3 focus:py-1.5 focus:text-white"
      >
        {t('app.skipToContent')}
      </a>

      <header className="app-header relative z-40 shrink-0 border-b border-[color:var(--color-rule-strong)]" data-scrolled={scrolled || undefined}>
        {/* riga 1 — identità e stato globale */}
        <div className="flex min-w-0 flex-wrap items-center gap-3 border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] py-1.5 pl-4 pr-3">
          <span className="text-[13px] font-bold tracking-[-0.02em]">
            Tabularium
          </span>
          <div className="ml-auto flex min-w-0 max-w-full flex-wrap items-center gap-2">
            <CommandPalette />
            <AccountMenu />
          </div>
        </div>

        {/* riga 2 — le sezioni, linguette bordo a bordo */}
        <nav aria-label={t('app.navSections')} className="nav-rail relative flex min-w-0 max-w-full flex-wrap md:flex-nowrap md:overflow-x-auto">

          {NAV.map(({ to, labelKey, Icon }) => (
            <Link key={to} to={to} aria-current={sectionOf(location.pathname) === to ? 'page' : undefined} className="shrink-0">
                <span className="navtab" data-active={sectionOf(location.pathname) === to}>
                  <Icon size={13} />
                  {t(labelKey)}
                </span>
            </Link>
          ))}
          <NavMark pathname={location.pathname} />
        </nav>
        {projectId != null && <ProjectNav id={projectId} pathname={location.pathname} />}
        {MODEL_PATHS.includes(location.pathname) && (
          <nav aria-label={t('workflow.modelNav')} className="section-nav">
            {[
              { to: '/modelli', key: 'workflow.configure' },
              { to: '/playground', key: 'nav.playground' },
            ].map(({ to, key }) => (
              <NavLink key={to} to={to} className="section-link">
                {t(key)}
              </NavLink>
            ))}
          </nav>
        )}
      </header>

      <main
        id="contenuto"
        className="min-h-0 flex-1 overflow-y-auto"
        onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 4)}
      >
        {/* Cambiando sezione il contenuto entra (non compare di colpo), e
            mentre il suo codice arriva resta la sua forma — la testata e le
            linguette restano dove sono. */}
        <Suspense fallback={<PageSkeleton label={t('app.loadingModule')} />}>
          <div key={pageKey(location.pathname)} className={`h-full ${/^\/progetti\/\d+\/studio/.test(location.pathname) || location.pathname.startsWith('/tabella') ? '' : 'workspace-page'}`}>
            <Outlet />
          </div>
        </Suspense>
      </main>
    </div>
  )
}
