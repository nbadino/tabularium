/**
 * Il guscio: un rail di navigazione multi-riga, persistente, in alto.
 *
 * La riga 1 porta l'identità e lo stato globale; la riga 2 le cinque
 * destinazioni globali, come linguette bordo a bordo. Il rail scorre
 * orizzontalmente al proprio interno e non cresce a ogni strumento nuovo:
 * gli strumenti contestuali entrano nel contesto che li rende sensati.
 *
 * La colonna sinistra non è sprecata su link fissi — nelle pagine che ne
 * hanno bisogno resta libera per il contesto (le pagine, i progetti).
 */
import { Link, NavLink, Outlet, useLocation } from 'react-router'
import { Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { apiGet } from '../lib/api'
import { syncInferenceFromBackend, useInference } from './inference'
import { useModelNames } from './models/names'
import type { HealthResponse } from '../lib/types'
import { LOCALES, LOCALE_LABELS, useI18n } from '../i18n'
import { useAuth } from './auth'
import { CommandPalette } from './CommandPalette'
import { Menu, PageSkeleton } from './ui'
import {
  IconArchive,
  IconAnnotate,
  IconDataset,
  IconPlayground,
  IconTraining,
} from './icons'

interface NavItem {
  to: string
  labelKey: string
  Icon: (p: { size?: number }) => React.ReactElement
  end?: boolean
}

/**
 * Le destinazioni globali sono cinque. Le prime quattro portano il percorso
 * primario — riconoscere, correggere, rivedere i risultati, gestire il corpus —
 * e Modelli è la quinta: il luogo unico in cui si sceglie il modello, dove
 * eseguirlo e come migliorarlo.
 *
 * Dataset, fine-tuning, valutazione e playground non stanno qui: sono
 * strumenti del modello e delle singole attività, non percorsi globali
 * concorrenti. Si aprono dal contesto che li rende sensati — il progetto, la
 * pagina dei risultati, l'hub Modelli — e restano raggiungibili come rotte.
 */
const NAV: NavItem[] = [
  { to: '/', labelKey: 'nav.recognize', Icon: IconPlayground, end: true },
  { to: '/annotazione', labelKey: 'nav.annotate', Icon: IconAnnotate },
  { to: '/risultati', labelKey: 'nav.results', Icon: IconDataset },
  { to: '/archivio', labelKey: 'nav.archive', Icon: IconArchive },
  { to: '/modelli', labelKey: 'nav.models', Icon: IconTraining },
]

/** IT · EN · FR in una riga: la lingua corrente in grassetto, il nome intero nel titolo. */
function LocaleSwitch() {
  const { t, locale, setLocale } = useI18n()
  return (
    <div className="flex items-center px-1 py-0.5" role="group" aria-label={t('layout.language')}>
      {LOCALES.map((l, i) => (
        <span key={l} className="flex items-center">
          {i > 0 && (
            <span aria-hidden="true" className="px-0.5 text-[color:var(--color-ink-3)]">
              ·
            </span>
          )}
          <button
            type="button"
            onClick={() => setLocale(l)}
            aria-pressed={l === locale}
            aria-label={LOCALE_LABELS[l]}
            title={LOCALE_LABELS[l]}
            className="menu-choice"
          >
            {l.toUpperCase()}
          </button>
        </span>
      ))}
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
        unfold
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

/**
 * Ultimo indirizzo visto per ogni sezione. Lo Studio e Risultati tengono la
 * pagina aperta e i filtri nella query (`?project=&page=`): una linguetta che
 * puntasse alla sezione nuda li buttava via a ogni ritorno. Vive per la sessione
 * del documento; dopo un ricaricamento è l'URL stesso a riportarli.
 */
const lastSearch = new Map<string, string>()

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
  const inference = useInference()
  const nameOf = useModelNames()
  const location = useLocation()
  // Il contenuto è scorso sotto la testata: lei lo dice con un'ombra.
  const [scrolled, setScrolled] = useState(false)
  if (NAV.some(({ to }) => to === location.pathname)) lastSearch.set(location.pathname, location.search)
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
        <div className="flex min-w-0 flex-wrap items-center gap-3 border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-1.5">
          <span className="text-[13px] font-bold tracking-[-0.02em]">
            Tabularium<span className="text-[color:var(--color-sig-text)]">Lab</span>
          </span>
          <div className="ml-auto flex min-w-0 max-w-full flex-wrap items-center gap-2">
            {location.pathname !== '/modelli' && (() => {
              const providerKey = inference.provider && ['local', 'ssh', 'vast', 'runpod', 'modal', 'custom'].includes(inference.provider)
                ? `recognition.provider.${inference.provider}`
                : null
              const providerLabel = providerKey
                ? t(providerKey)
                : inference.isCloud
                  ? t('recognition.locationCloud')
                  : t('recognition.locationLocal')
              const statusLabel = inference.enabled && inference.available
                ? t('recognition.modelReady')
                : !inference.enabled
                  ? t('recognition.modelOff')
                  : inference.model
                    ? t('recognition.modelUnavailable')
                    : t('recognition.modelOff')
              return (
            <Link
              to="/modelli"
              className="flex min-w-0 items-center gap-1.5 rounded-full border border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] px-3 py-1 text-[11px] no-underline hover:bg-[color:var(--color-fill)]"
              title={t('recognition.changeModel')}
            >
              <span className="max-w-[24ch] truncate font-semibold" title={inference.model || undefined}>{nameOf(inference.adapterId) ?? (inference.model || t('recognition.activeModel'))}</span>
              <span className="text-[color:var(--color-ink-3)]">· {providerLabel}</span>
              <span className={inference.enabled && inference.available ? 'text-[color:var(--color-ok)]' : 'text-[color:var(--color-warn)]'}>
                {statusLabel}
              </span>
            </Link>
              )
            })()}
            <CommandPalette />
            <AccountMenu />
          </div>
        </div>

        {/* riga 2 — le sezioni, linguette bordo a bordo */}
        <nav aria-label={t('app.navSections')} className="nav-rail relative flex min-w-0 max-w-full overflow-x-auto">
          <span className="w-px shrink-0 bg-[color:var(--color-rule)]" />
          {NAV.map(({ to, labelKey, Icon, end }) => (
            <NavLink key={to} to={{ pathname: to, search: lastSearch.get(to) ?? '' }} end={end} className="shrink-0">
              {({ isActive }) => (
                <span className="navtab" data-active={isActive}>
                  <Icon size={13} />
                  {t(labelKey)}
                </span>
              )}
            </NavLink>
          ))}
          <NavMark pathname={location.pathname} />
        </nav>
      </header>

      <main
        id="contenuto"
        className="glide min-h-0 flex-1 overflow-y-auto"
        onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 4)}
      >
        {/* Cambiando sezione il contenuto entra (non compare di colpo), e
            mentre il suo codice arriva resta la sua forma — la testata e le
            linguette restano dove sono. */}
        <Suspense fallback={<PageSkeleton label={t('app.loadingModule')} />}>
          <div key={location.pathname} className="enter h-full">
            <Outlet />
          </div>
        </Suspense>
      </main>
    </div>
  )
}
