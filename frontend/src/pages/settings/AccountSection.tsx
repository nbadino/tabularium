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

  if (!user) return <></>

  const roleKey = `users.role${user.role.charAt(0).toUpperCase()}${user.role.slice(1)}`

  return (
    <>
      <Module tab={t('settings.account')}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label={t('settings.accountUser')}>
            <div className="fld bg-[color:var(--color-fill)]">{user.username}</div>
          </Field>
          <Field label={t('settings.accountRole')}>
            <div className="fld bg-[color:var(--color-fill)]">{t(roleKey)}</div>
          </Field>
          <Field label={t('settings.accountEmail')}>
            <div className="fld bg-[color:var(--color-fill)] text-[color:var(--color-ink-2)]">
              {user.email || t('settings.accountNoEmail')}
            </div>
          </Field>
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

        <button type="button" className="btn mt-3" onClick={() => setPwOpen(true)}>
          {t('settings.changePassword')}
        </button>
      </Module>

      {pwOpen && <ChangePasswordModal onClose={() => setPwOpen(false)} />}
    </>
  )
}
