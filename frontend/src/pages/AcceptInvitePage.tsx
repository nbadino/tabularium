/**
 * Accettazione di un invito: l'invitato sceglie la propria password dal link
 * che l'amministratore gli ha consegnato.
 *
 * Fuori dal Layout e dal gate di login: chi arriva qui non ha ancora un
 * account utilizzabile. Il token viaggia nella query string e viene consumato
 * una sola volta.
 */
import { useEffect, useState } from 'react'
import { apiGet } from '../lib/api'
import type { InviteStatus } from '../lib/types'
import { Field } from '../app/ui'
import { useAuth } from '../app/auth'
import { useI18n } from '../i18n'

export default function AcceptInvitePage() {
  const { t } = useI18n()
  const { acceptInvite } = useAuth()
  const [token] = useState(() => new URLSearchParams(window.location.search).get('token') ?? '')
  const [status, setStatus] = useState<InviteStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!token) {
      setLoading(false)
      return
    }
    apiGet<InviteStatus>(`/auth/invite/${encodeURIComponent(token)}`)
      .then(setStatus)
      .catch(() => setStatus({ valid: false, username: null, email: null, expires_at: null }))
      .finally(() => setLoading(false))
  }, [token])

  const tooShort = password.length > 0 && password.length < 8
  const mismatch = confirm.length > 0 && password !== confirm
  const valid = password.length >= 8 && password === confirm && !busy

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      await acceptInvite(token, password)
      // L'invito è consumato: si entra nell'app a tutti gli effetti.
      window.location.assign('/')
    } catch {
      setError(t('auth.inviteFailed'))
      setBusy(false)
    }
  }

  const invalid = !token || (status != null && !status.valid)

  return (
    <div className="flex min-h-screen items-center justify-center bg-[color:var(--color-fill)] p-4">
      <div className="mod w-full max-w-sm border-[color:var(--color-rule-strong)]">
        <div className="mod-head border-b-[color:var(--color-rule-strong)]">
          <span className="mod-tab">{t('auth.inviteTitle')}</span>
        </div>
        <div className="mod-body">
          {loading ? (
            <p className="text-[13px] text-[color:var(--color-ink-2)]">{t('auth.loading')}</p>
          ) : invalid ? (
            <>
              <p className="text-[13px] font-semibold">{t('auth.inviteInvalidTitle')}</p>
              <p className="mt-1 text-[12px] text-[color:var(--color-ink-2)]">{t('auth.inviteInvalidBody')}</p>
            </>
          ) : (
            <form onSubmit={submit} className="space-y-3">
              <p className="text-[12px] text-[color:var(--color-ink-2)]">
                {t('auth.inviteFor', { name: status?.username ?? '' })}
              </p>
              <Field label={t('auth.newPassword')} hint={t('auth.passwordHint')}>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="new-password"
                  autoFocus
                  className="fld"
                  aria-invalid={tooShort}
                />
              </Field>
              <Field label={t('auth.confirmPassword')}>
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
              {mismatch && <p className="text-[12px] text-[color:var(--color-sig-text)]">{t('auth.passwordsDiffer')}</p>}
              {error && <p role="alert" className="text-[12px] text-[color:var(--color-sig-text)]">{error}</p>}
              <button type="submit" disabled={!valid} className="btn btn-primary w-full justify-center">
                {busy ? t('auth.acceptBusy') : t('auth.acceptInvite')}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
