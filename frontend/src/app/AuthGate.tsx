/**
 * Gate di autenticazione: decide cosa vedere prima che l'app si monti.
 *
 * All'avvio interroga `/auth/status` (pubblico) e sceglie:
 * - backend in modalità locale (`auth_enabled=false`) → l'app senza login;
 * - nessun utente registrato → il setup del primo amministratore;
 * - utente non autenticato → la schermata di login;
 * - utente autenticato → l'app.
 *
 * Un 401 da qualsiasi API (sessione scaduta, account disattivato) azzera lo
 * stato via `tabularium:unauthorized` e riporta qui al login.
 */
import { lazy, Suspense, useEffect, useState } from 'react'
import { useI18n } from '../i18n'
import { bootstrap, useAuth } from './auth'

const SetupPage = lazy(() => import('../pages/SetupPage'))
const LoginPage = lazy(() => import('../pages/LoginPage'))

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { t } = useI18n()
  const { phase, enabled, needsSetup, user } = useAuth()
  const [booted, setBooted] = useState(false)

  useEffect(() => {
    let alive = true
    bootstrap().then(() => alive && setBooted(true))
    return () => {
      alive = false
    }
  }, [])

  if (!booted || phase === 'loading') {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-[color:var(--color-ink-2)]">
        {t('auth.loading')}
      </div>
    )
  }

  if (!enabled) return <>{children}</>

  if (needsSetup) {
    return (
      <Suspense fallback={null}>
        <SetupPage />
      </Suspense>
    )
  }
  if (!user) {
    return (
      <Suspense fallback={null}>
        <LoginPage />
      </Suspense>
    )
  }
  return <>{children}</>
}
