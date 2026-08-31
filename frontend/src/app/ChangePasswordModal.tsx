/**
 * Cambio password autonomo dell'utente connesso.
 *
 * L'endpoint admin di reset non tocca mai il proprio account (regola di
 * sicurezza): questo dialog è la via per cambiarsi la password — chiede la
 * corrente, come deve. La sessione corrente resta valida, le altre decadono.
 */
import { useState } from 'react'
import { Modal } from './ui'
import { apiPost } from '../lib/api'
import { useI18n } from '../i18n'

export default function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const mismatch = confirm.length > 0 && next !== confirm
  const tooShort = next.length > 0 && next.length < 8
  const valid =
    current.length > 0 && next.length >= 8 && next === confirm && !busy

  const submit = async () => {
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      await apiPost('/auth/change-password', {
        current_password: current,
        new_password: next,
      })
      setDone(true)
    } catch (e) {
      const msg = String(e)
      setError(
        msg.includes('401')
          ? t('account.wrongCurrent')
          : t('account.changeFailed'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title={t('account.changeTitle')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-sm" onClick={onClose}>
            {t('common.close')}
          </button>
          {!done && (
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={!valid}
              onClick={() => void submit()}
            >
              {busy ? t('account.changing') : t('account.changeApply')}
            </button>
          )}
        </>
      }
    >
      {done ? (
        <p className="text-[13px] text-[color:var(--color-ok)]">
          {t('account.changeOk')}
        </p>
      ) : (
        <div className="space-y-3">
          <p className="text-[12px] text-[color:var(--color-ink-2)]">
            {t('account.changeIntro')}
          </p>
          <label className="block">
            <span className="lbl">{t('account.currentPassword')}</span>
            <input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              autoComplete="current-password"
              className="fld w-full"
            />
          </label>
          <label className="block">
            <span className="lbl">{t('account.newPassword')}</span>
            <input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password"
              className="fld w-full"
              aria-invalid={tooShort}
            />
            {tooShort && (
              <span className="mt-1 block text-[11px] text-[color:var(--color-sig-text)]">
                {t('account.tooShort')}
              </span>
            )}
          </label>
          <label className="block">
            <span className="lbl">{t('account.confirmPassword')}</span>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="new-password"
              className="fld w-full"
              aria-invalid={mismatch}
            />
            {mismatch && (
              <span className="mt-1 block text-[11px] text-[color:var(--color-sig-text)]">
                {t('account.mismatch')}
              </span>
            )}
          </label>
          {error && (
            <p className="text-[12px] text-[color:var(--color-sig-text)]">{error}</p>
          )}
        </div>
      )}
    </Modal>
  )
}
