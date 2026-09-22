/**
 * Il proprio account: chi sei per questa istanza e le due sole cose che puoi
 * cambiare da solo — la lingua dell'interfaccia e la password.
 *
 * I valori d'identità (utente, ruolo, email) sono di sola lettura per
 * chiunque, admin compreso: si cambiano nella pagina Utenti, dove vive
 * l'amministrazione degli account.
 */
import { useState } from 'react'
import { useAuth } from '../../app/auth'
import ChangePasswordModal from '../../app/ChangePasswordModal'
import { Field, Module } from '../../app/ui'
import { LOCALES, LOCALE_LABELS, useI18n } from '../../i18n'
import type { Locale } from '../../i18n'

export default function AccountSection() {
  const { locale, setLocale, t } = useI18n()
  const { user } = useAuth()
  const [pwOpen, setPwOpen] = useState(false)

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
        <div className="mt-3 grid items-end gap-3 sm:grid-cols-[minmax(220px,360px)_auto]">
          <Field label={t('settings.language')} hint={t('settings.languageHint')}>
            <select
              className="fld"
              value={locale}
              onChange={(e) => setLocale(e.target.value as Locale)}
            >
              {LOCALES.map((item) => (
                <option key={item} value={item}>
                  {LOCALE_LABELS[item]}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Module>
    )
  }

  const roleKey = `users.role${user.role.charAt(0).toUpperCase()}${user.role.slice(1)}`

  return (
    <>
      <Module tab={t('settings.account')}>
        <dl className="grid gap-x-6 gap-y-3 border-b border-[color:var(--color-rule)] pb-3 sm:grid-cols-3">
          <div className="min-w-0">
            <dt className="lbl">{t('settings.accountUser')}</dt>
            <dd className="truncate text-[13px] font-semibold">{user.username}</dd>
          </div>
          <div className="min-w-0">
            <dt className="lbl">{t('settings.accountRole')}</dt>
            <dd className="text-[13px]">{t(roleKey)}</dd>
          </div>
          <div className="min-w-0">
            <dt className="lbl">{t('settings.accountEmail')}</dt>
            <dd className="truncate text-[13px] text-[color:var(--color-ink-2)]" title={user.email || undefined}>
              {user.email || t('settings.accountNoEmail')}
            </dd>
          </div>
        </dl>

        <div className="mt-3 grid items-end gap-3 sm:grid-cols-[minmax(220px,360px)_auto]">
          <Field label={t('settings.language')} hint={t('settings.languageHint')}>
            <select
              className="fld"
              value={locale}
              onChange={(e) => setLocale(e.target.value as Locale)}
            >
              {LOCALES.map((item) => (
                <option key={item} value={item}>
                  {LOCALE_LABELS[item]}
                </option>
              ))}
            </select>
          </Field>
          <div>
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
