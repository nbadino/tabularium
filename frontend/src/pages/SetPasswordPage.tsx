/**
 * Cambio password forzato al primo accesso (o dopo un reset dell'admin).
 *
 * Schermata bloccante: finché l'utente non sceglie una password propria il
 * backend rifiuta ogni altra API (v. `services/auth.py::get_current_user`),
 * quindi il gate mostra solo questo. Richiede la password corrente — quella
 * temporanea appena usata per entrare.
 */
import { useState } from 'react'
import { apiPost } from '../lib/api'
import { Field } from '../app/ui'
import { useAuth } from '../app/auth'
import { useI18n } from '../i18n'

export default function SetPasswordPage() {
  const { t } = useI18n()
  const { user, refresh } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const tooShort = next.length > 0 && next.length < 8
  const mismatch = confirm.length > 0 && next !== confirm
  const valid = current.length > 0 && next.length >= 8 && next === confirm && !busy

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      await apiPost('/auth/change-password', { current_password: current, new_password: next })
      // Il gate legge di nuovo l'utente: senza il flag, entra nell'app.
      await refresh()
    } catch {
      setError(t('account.wrongCurrent'))
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[color:var(--color-fill)] p-4">
      <div className="mod w-full max-w-sm border-[color:var(--color-rule-strong)]">
        <div className="mod-head border-b-[color:var(--color-rule-strong)]">
          <span className="mod-tab">{t('account.setTitle')}</span>
        </div>
        <div className="mod-body">
          <p className="text-[12px] text-[color:var(--color-ink-2)]">{t('account.setIntro')}</p>
          <form onSubmit={submit} className="mt-3 space-y-3">
            <Field label={t('account.currentPassword')}>
              <input
                type="password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                autoComplete="current-password"
                autoFocus
                className="fld"
              />
            </Field>
            <Field label={t('account.newPassword')} hint={t('auth.passwordHint')}>
              <input
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                autoComplete="new-password"
                className="fld"
                aria-invalid={tooShort}
              />
            </Field>
            <Field label={t('account.confirmPassword')}>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                className="fld"
                aria-invalid={mismatch}
              />
            </Field>
            {tooShort && <p className="text-[12px] text-[color:var(--color-sig-text)]">{t('account.tooShort')}</p>}
            {mismatch && <p className="text-[12px] text-[color:var(--color-sig-text)]">{t('account.mismatch')}</p>}
            {error && <p role="alert" className="text-[12px] text-[color:var(--color-sig-text)]">{error}</p>}
            <button type="submit" disabled={!valid} className="btn btn-primary w-full justify-center">
              {busy ? t('account.changing') : t('account.setSubmit')}
            </button>
          </form>
          {user && (
            <p className="mt-3 text-[11px] text-[color:var(--color-ink-3)]">
              {t('auth.inviteFor', { name: user.username })}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
