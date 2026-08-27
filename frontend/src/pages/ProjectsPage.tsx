import { FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import type { Project } from '../lib/types'
import { pages, projects } from '../lib/vocab'
import { ErrorNotice, Field, Module } from '../app/ui'
import { IconPlus, IconProjects } from '../app/icons'
import { useI18n } from '../i18n'

export default function ProjectsPage() {
  const { t } = useI18n()
  const [items, setItems] = useState<Project[]>([])
  const [name, setName] = useState('')
  const [archiveDir, setArchiveDir] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [dirTouched, setDirTouched] = useState(false)

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

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('projects.title')}
        </h1>
        <p className="mt-1 max-w-[78ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('projects.intro')}
        </p>
      </div>

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
                hint={t('projects.archiveDirHint')}
                error={dirTouched && dirError ? dirError : null}
              >
                <input
                  value={archiveDir}
                  onChange={(e) => setArchiveDir(e.target.value)}
                  onBlur={() => setDirTouched(true)}
                  required
                  placeholder={t('projects.archiveDirPlaceholder')}
                  aria-invalid={dirTouched && dirError ? true : undefined}
                  className="fld fld-mono"
                />
              </Field>
            </div>
            <button type="submit" disabled={busy} className="btn btn-primary mt-3">
              <IconPlus size={13} />
              {busy ? t('projects.creating') : t('projects.create')}
            </button>
          </form>
        </Module>
      </div>

      <Module
        tab={t('projects.list')}
        quiet
        flush
        aux={<span>{projects(items.length)}</span>}
      >
        {items.length > 0 ? (
          <ul className="ruled">
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