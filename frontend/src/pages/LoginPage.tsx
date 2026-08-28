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
import { useI18n } from '../i18n'

export default function LoginPage() {
  const { t } = useI18n()
  const { instanceName, allowRegistration, login, register } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
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
      <div className="mod w-full max-w-sm border-[color:var(--color-rule-strong)]">
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
            <Field label={t('auth.password')}>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
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
              disabled={busy}
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
