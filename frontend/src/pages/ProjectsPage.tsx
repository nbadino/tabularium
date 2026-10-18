import { FormEvent, useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { writeActiveProject } from '../app/activeProject'
import { ApiError, apiGet, apiPost } from '../lib/api'
import type { Project } from '../lib/types'
import { pages, projects } from '../lib/vocab'
import { Badge, ErrorNotice, Field, Modal, Module, PageHeader } from '../app/ui'
import { IconPlus, IconProjects } from '../app/icons'
import { useI18n, tn } from '../i18n'
import { useAuth, useCanAdminister } from '../app/auth'
import ShareDialog from '../app/ShareDialog'
import { accessLabel, projectAccess } from '../app/projectAccess'
import { FolderPicker } from '../app/FolderPicker'

export default function ProjectsPage() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const auth = useAuth()
  const canCreate = !auth.enabled || auth.user?.role === 'admin' || auth.user?.role === 'editor'
  const [filter, setFilter] = useState<'all' | 'mine' | 'shared'>('all')
  const [sharing, setSharing] = useState<Project | null>(null)
  const [params, setParams] = useSearchParams()
  const creating = params.get('nuovo') === '1'
  const [query, setQuery] = useState('')
  const closeCreate = () => {
    if (busy) return
    const next = new URLSearchParams(params)
    next.delete('nuovo')
    setParams(next)
    setError(null)
  }
  const openCreate = () => { if (canCreate) { setError(null); setParams({ nuovo: '1' }) } }
  const [source, setSource] = useState<'upload' | 'server'>('upload')
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
      .then((r) => { setItems(r.items); setSharing((prev) => prev ? r.items.find((p) => p.id === prev.id) ?? null : null) })
      .catch(setError)
      .finally(() => setLoading(false))

  useEffect(() => {
    void load()
  }, [])

  const create = async (e: FormEvent) => {
    e.preventDefault()
    setDirTouched(true)
    if (!canCreate || !name.trim() || (source === 'server' && (!archiveDir.trim() || dirError))) return
    setBusy(true)
    setError(null)
    try {
      const project = await apiPost<Project>('/projects', {
        name: name.trim(), archive_dir: source === 'server' ? archiveDir.trim() : null,
      })
      writeActiveProject(project.id)
      navigate(`/progetti/${project.id}`)
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

  const filtered = items.filter((p) => p.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) && (
    filter === 'all' || (filter === 'mine' ? p.owner_id === auth.user?.id : p.access?.source === 'member')
  ))

  return (
    <div className="p-3">
      <PageHeader title={t('projects.title')} intro={t(canCreate ? 'projects.intro' : 'team.projectsIntro')}
        aux={canCreate && <button type="button" className="btn btn-primary" onClick={openCreate}><IconPlus size={13} />{t('projects.new')}</button>}
      />

      {error != null && !creating && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {creating && canCreate && <Modal title={t('projects.new')} onClose={closeCreate}>
        <div className="p-4">
          {error != null && <div className="mb-3"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}
          <p className="mb-3 text-[13px] text-[color:var(--color-ink-2)]">{t('workflow.createHint')}</p>
          <form onSubmit={create}>
            <div className="grid gap-3">
              <Field label={t('projects.name')}>
                <input
                  data-autofocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  required
                  placeholder={t('projects.namePlaceholder')}
                  className="fld"
                />
              </Field>
            <Field label={t('workflow.importSource')}>
              <select className="fld mb-3" value={source} onChange={(e) => setSource(e.target.value as 'upload' | 'server')}>
                <option value="upload">{t('workflow.fromComputer')}</option>
                <option value="server">{t('workflow.fromServer')}</option>
              </select>
            </Field>
              {source === 'server' && <Field
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
              </Field>}
            </div>
            <button type="submit" disabled={busy || !name.trim()} className="btn btn-primary mt-3">
              <IconPlus size={13} />
              {busy ? t('projects.creating') : t('projects.create')}
            </button>
            <button type="button" className="btn ml-2" disabled={busy} onClick={closeCreate}>{t('common.cancel')}</button>
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
        </div>
      </Modal>}

      {auth.enabled && <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label={t('team.projectFilters')}>
        {(['all', 'mine', 'shared'] as const).map((key) => <button key={key} type="button" className="btn btn-sm" aria-pressed={filter === key} onClick={() => setFilter(key)}>{t(`team.filter.${key}`)}</button>)}
      </div>}
      <div className="mb-3 max-w-lg">
        <Field label={t('ux.projectsSearch')}>
          <input type="search" className="fld" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('ux.projectsSearchHint')} />
        </Field>
      </div>
      <Module
        tab={t('projects.list')}
        quiet
        flush
        aux={<span>{projects(items.length)}</span>}
      >
        {filtered.length > 0 ? (
          <ul className="cascade ruled">
            {filtered.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-3 px-3 py-3 hover:bg-[color:var(--color-fill)]">
                <Link to={`/progetti/${p.id}`} className="min-w-0 flex-1 basis-56 no-underline text-[color:var(--color-ink)]">
                  <span className="block text-[15px] font-semibold">{p.name}</span>
                  <span className="mt-1 block text-[12px] text-[color:var(--color-ink-2)]">{t('team.ownerLine', { name: p.owner_name ?? t(auth.enabled ? 'team.noOwner' : 'team.localProject') })}</span>
                </Link>
                <Badge tone="neutral">{accessLabel(projectAccess(p, auth))}</Badge>
                <span className="text-[12px] text-[color:var(--color-ink-2)]">{pages(p.pages_count)}</span>
                {p.members_count != null && <span className="text-[12px] text-[color:var(--color-ink-2)]">{tn('team.peopleCount', p.members_count)}</span>}
                <button type="button" className="btn btn-sm" onClick={() => setSharing(p)} aria-label={t('team.accessFor', { name: p.name })}>{t('team.peopleAccess')}</button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="flex flex-col items-start gap-2 p-6">
            <IconProjects size={22} />
            <p className="text-[13px] font-semibold">
              {loading ? t('common.loading') : items.length > 0 ? t('ux.noProjectMatch') : t('projects.emptyTitle')}
            </p>
            <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">
              {items.length > 0 ? t('ux.noProjectMatchHint') : t(canCreate ? 'ux.firstProjectHint' : 'team.noAssignedProjects')}
            </p>
            {!loading && (items.length > 0
              ? <button type="button" className="btn" onClick={() => { setQuery(''); setFilter('all') }}>{t('ux.clearSearch')}</button>
              : canCreate && <button type="button" className="btn btn-primary" onClick={openCreate}>{t('projects.new')}</button>)}
          </div>
        )}
      </Module>
      {sharing && <ShareDialog key={sharing.id} projectId={sharing.id} projectName={sharing.name} canManage={projectAccess(sharing, auth)?.can_manage === true} onClose={() => setSharing(null)} onAccessChanged={() => void load()} />}
    </div>
  )
}
