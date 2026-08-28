/**
 * Gestione utenti dell'istanza (self-hosted, solo amministratore).
 *
 * Creazione, cambio ruolo/attivazione, reset password (invalida le sessioni
 * esistenti dell'utente) ed eliminazione. Il backend protegge le regole
 * sensibili (niente auto-declassamento, niente eliminazione dell'ultimo admin);
 * qui i controlli disabilitano i comandi che finirebbero in un 400.
 */
import { FormEvent, useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPatch, apiPost } from '../lib/api'
import type { User } from '../lib/types'
import { ErrorNotice, Field, Modal, Module } from '../app/ui'
import { useAuth } from '../app/auth'
import { useI18n } from '../i18n'

const ROLES: Array<User['role']> = ['admin', 'editor', 'viewer']

export default function UsersPage() {
  const { t } = useI18n()
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  // form «nuovo utente»
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<User['role']>('editor')
  const [active, setActive] = useState(true)

  // modali
  const [resetTarget, setResetTarget] = useState<User | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null)

  const load = () =>
    apiGet<User[]>('/users')
      .then(setUsers)
      .catch(setError)
      .finally(() => setLoading(false))

  useEffect(() => {
    void load()
  }, [])

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await apiPost<User>('/users', {
        username: username.trim(),
        password,
        email: email.trim() || undefined,
        role,
        active,
      })
      setUsername('')
      setPassword('')
      setEmail('')
      setRole('editor')
      setActive(true)
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const patch = (id: number, body: Record<string, unknown>) =>
    apiPatch<User>(`/users/${id}`, body)
      .then(() => load())
      .catch(setError)

  const doReset = async () => {
    if (!resetTarget || !resetPassword) return
    setBusy(true)
    setError(null)
    try {
      await apiPost(`/users/${resetTarget.id}/reset-password`, {
        password: resetPassword,
      })
      setResetTarget(null)
      setResetPassword('')
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async () => {
    if (!deleteTarget) return
    setBusy(true)
    setError(null)
    try {
      await apiDelete(`/users/${deleteTarget.id}`)
      setDeleteTarget(null)
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('users.title')}
        </h1>
        <p className="mt-1 max-w-[78ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('users.intro')}
        </p>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <div className="mb-3">
        <Module tab={t('users.newUser')}>
          <form onSubmit={create}>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label={t('auth.username')}>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  autoComplete="off"
                  className="fld"
                />
              </Field>
              <Field label={t('auth.password')} hint={t('auth.passwordHint')}>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  autoComplete="new-password"
                  className="fld"
                />
              </Field>
              <Field label={t('auth.email')}>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="off"
                  className="fld"
                />
              </Field>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-4">
              <Field label={t('users.role')}>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as User['role'])}
                  className="fld"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {t(`users.role${r.charAt(0).toUpperCase()}${r.slice(1)}`)}
                    </option>
                  ))}
                </select>
              </Field>
              <label className="mt-4 flex items-center gap-2 text-[13px]">
                <input
                  type="checkbox"
                  checked={active}
                  onChange={(e) => setActive(e.target.checked)}
                  className="h-4 w-4 accent-[color:var(--color-sig)]"
                />
                {t('users.active')}
              </label>
              <button type="submit" disabled={busy} className="btn btn-primary mt-4">
                {busy ? t('common.loading') : t('users.create')}
              </button>
            </div>
          </form>
        </Module>
      </div>

      <Module tab={t('users.list')} quiet flush aux={<span>{users.length}</span>}>
        {users.length > 0 ? (
          <div>
            {/* intestazione colonne */}
            <div className="grid grid-cols-[1.4fr_1fr_0.7fr_1fr_auto] gap-x-3 border-b border-[color:var(--color-rule)] px-3 py-1.5">
              {[t('users.username'), t('users.role'), t('users.active'), t('users.createdAt'), ''].map(
                (h, i) => (
                  <span key={i} className="lbl !mb-0">
                    {h}
                  </span>
                ),
              )}
            </div>
            <ul className="ruled">
              {users.map((u) => {
                const isMe = me?.id === u.id
                const isLastAdmin = u.role === 'admin' && users.filter((x) => x.role === 'admin').length === 1
                return (
                  <li
                    key={u.id}
                    className="grid grid-cols-[1.4fr_1fr_0.7fr_1fr_auto] items-center gap-x-3 px-3 py-1.5"
                  >
                    <span className="flex min-w-0 items-baseline gap-2">
                      <span className="truncate text-[13px] font-semibold">{u.username}</span>
                      {isMe && (
                        <span className="badge text-[color:var(--color-sig-text)] bg-[color:var(--color-sig-wash)]">
                          {t('users.me')}
                        </span>
                      )}
                    </span>
                    <select
                      value={u.role}
                      onChange={(e) =>
                        void patch(u.id, { role: e.target.value as User['role'] })
                      }
                      disabled={isMe || isLastAdmin}
                      title={
                        isLastAdmin && u.role === 'admin'
                          ? t('users.lastAdminTitle')
                          : undefined
                      }
                      className="fld !w-auto"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {t(`users.role${r.charAt(0).toUpperCase()}${r.slice(1)}`)}
                        </option>
                      ))}
                    </select>
                    <input
                      type="checkbox"
                      checked={u.active}
                      onChange={(e) => void patch(u.id, { active: e.target.checked })}
                      disabled={isMe}
                      aria-label={t('users.active')}
                      className="h-4 w-4 accent-[color:var(--color-sig)]"
                    />
                    <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
                      {u.created_at.slice(0, 10)}
                    </span>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setResetTarget(u)
                          setResetPassword('')
                        }}
                        disabled={isMe}
                        className="btn btn-sm"
                      >
                        {t('users.resetPassword')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setDeleteTarget(u)}
                        disabled={isMe || isLastAdmin}
                        title={
                          isLastAdmin && u.role === 'admin'
                            ? t('users.lastAdminTitle')
                            : undefined
                        }
                        className="btn btn-sm btn-danger"
                      >
                        {t('users.delete')}
                      </button>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        ) : (
          <p className="p-6 text-[13px] text-[color:var(--color-ink-2)]">
            {loading ? t('common.loading') : t('users.empty')}
          </p>
        )}
      </Module>

      {/* reset password */}
      {resetTarget && (
        <Modal
          title={t('users.resetPasswordFor', { name: resetTarget.username })}
          onClose={() => setResetTarget(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setResetTarget(null)}>
                {t('common.cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy || !resetPassword}
                onClick={() => void doReset()}
              >
                {t('users.resetPassword')}
              </button>
            </>
          }
        >
          <div className="p-3">
            <p className="mb-3 max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('users.resetPasswordHint')}
            </p>
            <Field label={t('auth.password')} hint={t('auth.passwordHint')}>
              <input
                type="password"
                value={resetPassword}
                onChange={(e) => setResetPassword(e.target.value)}
                autoFocus
                className="fld"
              />
            </Field>
          </div>
        </Modal>
      )}

      {/* conferma eliminazione */}
      {deleteTarget && (
        <Modal
          title={t('users.deleteTitle')}
          onClose={() => setDeleteTarget(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setDeleteTarget(null)}>
                {t('common.cancel')}
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy}
                onClick={() => void doDelete()}
              >
                {t('users.delete')}
              </button>
            </>
          }
        >
          <div className="p-3">
            <p className="text-[13px]">
              {t('users.deleteBody', { name: deleteTarget.username })}
            </p>
          </div>
        </Modal>
      )}
    </div>
  )
}
