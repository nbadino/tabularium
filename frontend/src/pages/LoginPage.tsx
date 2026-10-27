/**
 * Schermata di login (self-hosted).
 *
 * A tutto schermo, fuori dal Layout: l'autenticazione precede l'app, quindi
 * non c'è ancora navigazione. Se l'amministratore ha aperto la registrazione,
 * si può anche creare un account dal link in fondo alla scheda.
 */
import { useState } from 'react'
import { Field } from '../app/ui'
import { useAuth } from '../app/auth'
import { describeError } from '../lib/errors'
import { API_BASE } from '../lib/endpoints'
import { useI18n } from '../i18n'

/** The backend sends a code; the page owns the sentence. */
const SSO_ERROR_KEYS: Record<string, string> = {
  oidc_state: 'auth.oidcErrorState',
  oidc_domain: 'auth.oidcErrorDomain',
  oidc_email: 'auth.oidcErrorEmail',
  oidc_exchange: 'auth.oidcErrorProvider',
  oidc_discovery: 'auth.oidcErrorProvider',
  oidc_userinfo: 'auth.oidcErrorProvider',
  oidc_user: 'auth.oidcErrorUser',
  oidc_disabled: 'auth.oidcErrorDisabled',
}

export default function LoginPage() {
  const { t } = useI18n()
  const { instanceName, allowRegistration, oidcEnabled, oidcButtonLabel, login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  // A failed SSO round trip comes back as `?auth_error=<code>`: the backend sends a
  // code, not a sentence, so the page can translate it.
  const [ssoError] = useState(() => new URLSearchParams(window.location.search).get('auth_error'))
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) {
      setError(t('auth.fillRequired'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (mode === 'login') {
        await login(username.trim(), password)
      } else {
        await register(username.trim(), password, email)
      }
      // Il gate reagisce allo stato aggiornato: non serve navigare qui.
    } catch (err) {
      setError(describeError(err).messaggio)
    } finally {
      setBusy(false)
    }
  }

  const switchMode = () => {
    setMode(mode === 'login' ? 'register' : 'login')
    setError(null)
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[color:var(--color-fill)] p-4">
      <div className="mod w-full max-w-sm rounded-lg p-6">
        <div className="mod-head border-b-[color:var(--color-rule-strong)]">
          <span className="mod-tab">
            {mode === 'login' ? t('auth.loginTitle') : t('auth.registerTitle')}
          </span>
        </div>
        <div className="mod-body">
          <h1 className="text-[22px] font-bold leading-tight tracking-[-0.03em]">
            {instanceName || 'Tabularium'}
            <span className="text-[color:var(--color-sig-text)]">Lab</span>
          </h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
            {mode === 'login' ? t('auth.loginSubtitle') : t('auth.registerSubtitle')}
          </p>

          {ssoError && (
            <p
              role="alert"
              className="mt-3 rounded border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sig-soft)] px-3 py-2 text-[12px] text-[color:var(--color-sig-text)]"
            >
              {t(SSO_ERROR_KEYS[ssoError] ?? 'auth.oidcErrorGeneric')}
            </p>
          )}

          {oidcEnabled && mode === 'login' && (
            <>
              <a href={`${API_BASE}/auth/oidc/start`} className="btn mt-4 w-full justify-center">
                {oidcButtonLabel || t('auth.oidcFallback')}
              </a>
              <p className="mt-2 text-center text-[10px] uppercase tracking-wide text-[color:var(--color-ink-3)]">
                {t('auth.orLocal')}
              </p>
            </>
          )}

          <form onSubmit={submit} className="mt-4 space-y-3">
            <Field label={t('auth.username')}>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                className="fld"
              />
            </Field>
            <Field label={t('auth.password')} hint={mode === 'register' ? t('auth.passwordHint') : undefined}>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                minLength={mode === 'register' ? 8 : undefined}
                className="fld"
              />
            </Field>
            {mode === 'register' && (
              <Field label={t('auth.email')} hint={t('auth.emailHint')}>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                  className="fld"
                />
              </Field>
            )}
            {error && (
              <p role="alert" className="text-[12px] text-[color:var(--color-sig-text)]">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={busy || !username.trim() || !password || (mode === 'register' && password.length < 8)}
              className="btn btn-primary w-full justify-center"
            >
              {busy
                ? t('auth.busy')
                : mode === 'login'
                  ? t('auth.login')
                  : t('auth.register')}
            </button>
          </form>

          {allowRegistration && (
            <div className="mt-4 border-t border-[color:var(--color-rule)] pt-3">
              <button
                type="button"
                onClick={switchMode}
                className="text-[12px] font-medium text-[color:var(--color-sig-text)] hover:underline"
              >
                {mode === 'login'
                  ? t('auth.switchToRegister')
                  : t('auth.switchToLogin')}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
