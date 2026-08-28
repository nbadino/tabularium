/**
 * Primo avvio: creazione dell'amministratore dell'istanza (self-hosted).
 *
 * Il backend accetta lo setup una sola volta, finché non esistono utenti.
 * Chi lo completa diventa amministratore e le sessioni/progetti orfani
 * (creati in modalità locale) vengono assegnati a questo account.
 */
import { useState } from 'react'
import { Field } from '../app/ui'
import { useAuth } from '../app/auth'
import { describeError } from '../lib/errors'
import { useI18n } from '../i18n'

export default function SetupPage() {
  const { t } = useI18n()
  const { setup } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) {
      setError(t('auth.fillRequired'))
      return
    }
    if (password !== confirm) {
      setError(t('auth.passwordsDiffer'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      await setup(username.trim(), password, email)
      // Il gate reagisce allo stato aggiornato: si entra nell'app.
    } catch (err) {
      setError(describeError(err).messaggio)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[color:var(--color-fill)] p-4">
      <div className="mod w-full max-w-sm border-[color:var(--color-rule-strong)]">
        <div className="mod-head border-b-[color:var(--color-rule-strong)]">
          <span className="mod-tab">{t('auth.setupTitle')}</span>
        </div>
        <div className="mod-body">
          <h1 className="text-[22px] font-bold leading-tight tracking-[-0.03em]">
            Tabularium<span className="text-[color:var(--color-sig-text)]">Lab</span>
          </h1>
          <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">
            {t('auth.setupSubtitle')}
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
            <Field label={t('auth.password')} hint={t('auth.passwordHint')}>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                className="fld"
              />
            </Field>
            <Field label={t('auth.confirmPassword')}>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                className="fld"
              />
            </Field>
            <Field label={t('auth.email')} hint={t('auth.emailHint')}>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                className="fld"
              />
            </Field>
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
              {busy ? t('auth.busy') : t('auth.setupSubmit')}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
