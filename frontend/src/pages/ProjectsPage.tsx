import { FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ApiError, apiGet, apiPost } from '../lib/api'
import type { Project } from '../lib/types'
import { pages, projects } from '../lib/vocab'
import { ErrorNotice, Field, Module, PageHeader } from '../app/ui'
import { IconPlus, IconProjects } from '../app/icons'
import { useI18n } from '../i18n'
import { useCanAdminister } from '../app/auth'
import { FolderPicker } from '../app/FolderPicker'

export default function ProjectsPage() {
  const { t } = useI18n()
  const [items, setItems] = useState<Project[]>([])
  const [name, setName] = useState('')
  const [archiveDir, setArchiveDir] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [dirTouched, setDirTouched] = useState(false)
  const [picking, setPicking] = useState(false)
  const [nativePicking, setNativePicking] = useState(false)
  // La navigazione mostra il filesystem del server: solo gli amministratori.
  const canBrowse = useCanAdminister()

  // Un percorso scelto (dalla finestra di sistema o dal browser web) vale come
  // digitato: riempie il campo e, se il nome è vuoto, lo ricava dalla cartella.
  const applyPath = (path: string) => {
    setArchiveDir(path)
    setDirTouched(true)
    if (!name.trim()) setName(path.split(/[\\/]/).filter(Boolean).pop() ?? '')
  }

  // percorso assoluto POSIX o Windows; null = campo vuoto (validato da required)
  const dirError = (() => {
    const v = archiveDir.trim()
    if (!v) return null
    if (/^\//.test(v) || /^[A-Za-z]:[\\/]/.test(v)) return null
    return t('projects.dirError')
  })()

  const load = () =>
    apiGet<{ items: Project[] }>('/projects')
      .then((r) => setItems(r.items))
      .catch(setError)
      .finally(() => setLoading(false))

  useEffect(() => {
    void load()
  }, [])

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setDirTouched(true)
    if (dirError) return
    setBusy(true)
    setError(null)
    try {
      await apiPost<Project>('/projects', { name, archive_dir: archiveDir })
      setName('')
      setArchiveDir('')
      setDirTouched(false)
      await load()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  // Un unico pulsante «Sfoglia»: apre la finestra di sistema sulla macchina
  // del backend, che è quella dell'utente quando il server gira in locale. Se
  // la finestra non può comparire (server headless o remoto) l'API risponde
  // 501: si ripiega sulla navigazione web del filesystem, senza un secondo
  // pulsante da spiegare.
  const pickNative = async () => {
    setNativePicking(true)
    setError(null)
    try {
      const start = archiveDir.trim()
      const out = await apiPost<{ path: string | null }>(
        `/system/fs/pick${start ? `?start=${encodeURIComponent(start)}` : ''}`,
      )
      if (out.path) applyPath(out.path)
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) setPicking(true)
      else setError(err)
    } finally {
      setNativePicking(false)
    }
  }

  return (
    <div className="p-3">
      <PageHeader title={t('projects.title')} intro={t('projects.intro')} />

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <div className="mb-3">
        <Module tab={t('projects.new')}>
          <form onSubmit={create}>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('projects.name')}>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  placeholder={t('projects.namePlaceholder')}
                  className="fld"
                />
              </Field>
              <Field
                label={t('projects.archiveDir')}
                error={dirTouched && dirError ? dirError : null}
              >
                <div>
                  <div className="flex gap-2">
                    <input
                      value={archiveDir}
                      onChange={(e) => setArchiveDir(e.target.value)}
                      onBlur={() => setDirTouched(true)}
                      required
                      placeholder={t('projects.archiveDirPlaceholder')}
                      aria-invalid={dirTouched && dirError ? true : undefined}
                      className="fld fld-mono"
                    />
                    {canBrowse && (
                      <button
                        type="button"
                        className="btn shrink-0"
                        disabled={nativePicking}
                        onClick={() => void pickNative()}
                      >
                        {t('projects.picker.open')}
                      </button>
                    )}
                  </div>
                  {nativePicking && (
                    <span className="mt-1 block text-[11px] text-[color:var(--color-ink-2)]">
                      {t('projects.picker.nativeWaiting')}
                    </span>
                  )}
                </div>
              </Field>
            </div>
            <button type="submit" disabled={busy} className="btn btn-primary mt-3">
              <IconPlus size={13} />
              {busy ? t('projects.creating') : t('projects.create')}
            </button>
          </form>
          {picking && (
            <FolderPicker
              start={archiveDir}
              onClose={() => setPicking(false)}
              onPick={(path) => {
                applyPath(path)
                setPicking(false)
              }}
            />
          )}
        </Module>
      </div>

      <Module
        tab={t('projects.list')}
        quiet
        flush
        aux={<span>{projects(items.length)}</span>}
      >
        {items.length > 0 ? (
          <ul className="cascade ruled">
            {items.map((p) => (
              <li key={p.id}>
                <Link
                  to={`/progetti/${p.id}`}
                  className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-3 py-2 text-[color:var(--color-ink)] no-underline hover:bg-[color:var(--color-fill)]"
                >
                  <span className="text-[15px] font-semibold tracking-[-0.015em]">{p.name}</span>
                  <span className="mono min-w-0 flex-1 truncate text-[11px] text-[color:var(--color-ink-3)]">
                    {p.archive_dir ?? '—'}
                  </span>
                  <span className="mono text-[12px] text-[color:var(--color-ink-2)]">
                    {pages(p.pages_count)}
                  </span>
                  <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
                    {p.created_at.slice(0, 10)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex flex-col items-start gap-2 p-6">
            <IconProjects size={22} />
            <p className="text-[13px] font-semibold">
              {loading ? t('common.loading') : t('projects.emptyTitle')}
            </p>
            <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('projects.emptyBody')}
            </p>
          </div>
        )}
      </Module>
    </div>
  )
}