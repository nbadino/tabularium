/**
 * Your account: who you are on this instance, your email and your password.
 * The interface language lives in the user menu (one place per setting).
 */
import { useState } from 'react'
import { useAuth } from '../../app/auth'
import ChangePasswordModal from '../../app/ChangePasswordModal'
import { Field, Module } from '../../app/ui'
import { useI18n } from '../../i18n'

export default function AccountSection() {
  const { t } = useI18n()
  const { user, updateProfile } = useAuth()
  const [pwOpen, setPwOpen] = useState(false)
  const [email, setEmail] = useState(user?.email ?? '')
  const [emailState, setEmailState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  // In modalità locale non esistono utenti: la zona Account resta viva
  // perché la lingua è comunque una scelta di questo browser, e dice
  // esplicitamente perché non c'è nient'altro da configurare qui.
  if (!user) {
    return (
      <Module tab={t('settings.account')}>
        <p className="text-[13px] font-semibold">{t('settings.localModeTitle')}</p>
        <p className="mt-1 max-w-[72ch] text-[12px] text-[color:var(--color-ink-2)]">
          {t('settings.localModeBody')}
        </p>
      </Module>
    )
  }

  const roleKey = `users.role${user.role.charAt(0).toUpperCase()}${user.role.slice(1)}`

  const saveEmail = async () => {
    setEmailState('saving')
    try {
      await updateProfile(email.trim() || null)
      setEmailState('saved')
      window.setTimeout(() => setEmailState('idle'), 2500)
    } catch {
      setEmailState('error')
    }
  }

  return (
    <>
      <Module tab={t('settings.account')}>
        {user.must_change_password && (
          <p className="mb-3 border-l-2 border-[color:var(--color-warn)] pl-2 text-[12px] text-[color:var(--color-warn)]">
            {t('account.mustChangeNotice')}
          </p>
        )}
        <dl className="grid gap-x-6 gap-y-3 border-b border-[color:var(--color-rule)] pb-3 sm:grid-cols-2">
          <div className="min-w-0">
            <dt className="lbl">{t('settings.accountUser')}</dt>
            <dd className="truncate text-[13px] font-semibold">{user.username}</dd>
          </div>
          <div className="min-w-0">
            <dt className="lbl">{t('settings.accountRole')}</dt>
            <dd className="text-[13px]">{t(roleKey)}</dd>
          </div>
        </dl>

        <div className="mt-3 grid items-end gap-3 sm:grid-cols-[minmax(200px,320px)_auto]">
          <Field
            label={t('account.email')}
            hint={
              emailState === 'saved'
                ? t('account.emailSaved')
                : emailState === 'error'
                  ? t('account.emailFailed')
                  : t('account.emailHint')
            }
          >
            <input
              type="email"
              value={email}
              onChange={(e) => { setEmail(e.target.value); setEmailState('idle') }}
              autoComplete="email"
              className="fld"
            />
          </Field>
          <div className="flex gap-2">
            <button
              type="button"
              className="btn"
              disabled={emailState === 'saving' || (email.trim() || '') === (user.email ?? '')}
              onClick={() => void saveEmail()}
            >
              {emailState === 'saving' ? t('account.saving') : t('account.saveProfile')}
            </button>
            <button type="button" className="btn" onClick={() => setPwOpen(true)}>
              {t('settings.changePassword')}
            </button>
          </div>
        </div>
      </Module>

      {pwOpen && <ChangePasswordModal onClose={() => setPwOpen(false)} />}
    </>
  )
}
