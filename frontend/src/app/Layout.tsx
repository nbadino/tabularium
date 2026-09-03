/**
 * Il guscio: un rail di navigazione multi-riga, persistente, in alto.
 *
 * La riga 1 porta l'identità e lo stato globale; la riga 2 le sezioni, come
 * linguette bordo a bordo. Il rail regge più delle sette sezioni attuali
 * senza ridisegnarsi: le milestone successive entrano come nuove linguette.
 *
 * La colonna sinistra non è più sprecata su sette link fissi — nelle pagine
 * che ne hanno bisogno resta libera per il contesto (le pagine, i progetti).
 */
import { Link, NavLink, Outlet } from 'react-router'
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/api'
import { syncInferenceFromBackend, useInference } from './inference'
import type { HealthResponse } from '../lib/types'
import { LOCALES, LOCALE_LABELS, useI18n } from '../i18n'
import type { Locale } from '../i18n'
import { useAuth } from './auth'
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

const NAV: NavItem[] = [
  { to: '/', labelKey: 'nav.recognize', Icon: IconPlayground, end: true },
  { to: '/annotazione', labelKey: 'nav.annotate', Icon: IconAnnotate },
  { to: '/risultati', labelKey: 'nav.results', Icon: IconDataset },
  { to: '/archivio', labelKey: 'nav.archive', Icon: IconArchive },
  { to: '/modelli', labelKey: 'nav.models', Icon: IconTraining },
]

function LocaleSwitch() {
  const { locale, setLocale } = useI18n()
  return (
    <select
      value={locale}
      onChange={(e) => setLocale(e.target.value as Locale)}
      aria-label={LOCALE_LABELS[locale]}
      className="border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] px-1 py-0.5 text-[11px] font-medium text-[color:var(--color-ink)] focus:outline-none focus-visible:outline-2 focus-visible:outline-[color:var(--color-sig)]"
    >
      {LOCALES.map((l) => (
        <option key={l} value={l}>
          {LOCALE_LABELS[l]}
        </option>
      ))}
    </select>
  )
}

function BackendState() {
  const { t } = useI18n()
  const [version, setVersion] = useState<string | null>(null)
  const [down, setDown] = useState(false)

  useEffect(() => {
    apiGet<HealthResponse>('/health')
      .then((h) => setVersion(h.version))
      .catch(() => setDown(true))
  }, [])

  if (down) {
    return (
      <span className="badge border-[color:var(--color-sig-plate)] bg-[color:var(--color-sig-plate)] text-white">
        {t('app.backendDown')}
      </span>
    )
  }
  return (
    <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
      v{version ?? '…'}
    </span>
  )
}

/**
 * Chi è connesso: nome, ruolo, e i comandi dell'istanza. Le pagine
 * Impostazioni e Utenti esistono solo per l'amministratore; il logout valido
 * per tutti. Nascosto finché non c'è un utente (modalità locale).
 */
function UserMenu() {
  const { t } = useI18n()
  const { user, logout } = useAuth()
  if (!user) return null
  const roleKey = `users.role${user.role.charAt(0).toUpperCase()}${user.role.slice(1)}`
  return (
    <div className="flex items-center gap-1.5">
      {user.role === 'admin' && (
        <NavLink to="/utenti" className="btn btn-sm">
          {t('nav.users')}
        </NavLink>
      )}
      <NavLink to="/impostazioni" className="btn btn-sm">
        {t('nav.settings')}
      </NavLink>
      <span className="flex min-w-0 items-baseline gap-1.5">
        <span className="max-w-[14ch] truncate text-[12px] font-semibold text-[color:var(--color-ink)]">
          {user.username}
        </span>
        <span className="badge">{t(roleKey)}</span>
      </span>
      <button type="button" onClick={() => void logout()} className="btn btn-sm">
        {t('layout.logout')}
      </button>
    </div>
  )
}

export default function Layout() {
  const { t } = useI18n()
  const inference = useInference()
  // Ogni pagina legge lo stato inferenza (chip GPU, banner, motori prefill):
  // il sync all'avvio evita di mostrare disponibilità/latenza stantie da
  // localStorage, che prima si aggiornavano solo aprendo la Home.
  useEffect(() => {
    void syncInferenceFromBackend().catch(() => {})
    const id = setInterval(() => void syncInferenceFromBackend().catch(() => {}), 30_000)
    return () => clearInterval(id)
  }, [])
  return (
    <div className="flex h-screen flex-col bg-[color:var(--color-sheet)]">
      <a
        href="#contenuto"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-[color:var(--color-ink)] focus:px-3 focus:py-1.5 focus:text-white"
      >
        {t('app.skipToContent')}
      </a>

      <header className="shrink-0 border-b border-[color:var(--color-rule-strong)]">
        {/* riga 1 — identità e stato globale */}
        <div className="flex items-center gap-3 border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-1.5">
          <span className="text-[13px] font-bold tracking-[-0.02em]">
            Tabularium<span className="text-[color:var(--color-sig-text)]">Lab</span>
          </span>
          <span className="text-[11px] text-[color:var(--color-ink-3)]">
            {t('app.tagline')}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Link
              to="/modelli"
              className="flex min-w-0 items-center gap-1.5 border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] px-2 py-1 text-[11px] no-underline hover:bg-[color:var(--color-fill)]"
              title={t('recognition.changeModel')}
            >
              <span className="max-w-[24ch] truncate font-semibold">{inference.model || t('recognition.activeModel')}</span>
              <span className="text-[color:var(--color-ink-3)]">· {inference.isCloud ? 'Cloud' : 'Locale'}</span>
              <span className={inference.enabled && inference.available ? 'text-[color:var(--color-ok)]' : 'text-[color:var(--color-warn)]'}>
                {inference.enabled && inference.available ? t('recognition.modelReady') : t('recognition.modelOff')}
              </span>
            </Link>
            <UserMenu />
            <LocaleSwitch />
            <BackendState />
          </div>
        </div>

        {/* riga 2 — le sezioni, linguette bordo a bordo */}
        <nav aria-label={t('app.navSections')} className="flex overflow-x-auto">
          <span className="w-px shrink-0 bg-[color:var(--color-rule)]" />
          {NAV.map(({ to, labelKey, Icon, end }) => (
            <NavLink key={to} to={to} end={end} className="shrink-0">
              {({ isActive }) => (
                <span className="navtab" data-active={isActive}>
                  <Icon size={13} />
                  {t(labelKey)}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </header>

      <main id="contenuto" className="min-h-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
