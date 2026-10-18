/**
 * «Condividi» — gestione dell'accesso al progetto, stile Drive.
 *
 * L'unica superficie in cui si aggiungono persone, si cambia ruolo e si
 * trasferisce la proprietà. Riusa le API già esistenti (`members`,
 * `member-candidates`, `owner`, `activity`); non introduce un modello di
 * permessi nuovo.
 *
 * Chi non è proprietario (né admin) vede la lista in sola lettura: il backend
 * è la fonte di verità, qui si disabilita solo ciò che finirebbe in un 403.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut } from '../lib/api'
import type { ProjectMember } from '../lib/types'
import { avatarColor, initialsOf } from '../lib/avatar'
import { Badge, ErrorNotice, Field, Loading, Modal, Notice } from './ui'
import { ActivityFeed, type ProjectActivity } from './ActivityFeed'
import { IconPlus, IconTrash } from './icons'
import { useConfirm } from './confirm'
import { useI18n } from '../i18n'

type Role = 'owner' | 'editor' | 'viewer'

export function Avatar({ name, id, size = 28 }: { name: string; id?: number | null; size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center rounded-full font-semibold uppercase text-white"
      style={{ backgroundColor: avatarColor(id ?? name), width: size, height: size, fontSize: Math.round(size * 0.4) }}
    >
      {initialsOf(name)}
    </span>
  )
}

export default function ShareDialog({
  projectId,
  projectName,
  canManage,
  onClose,
  onAccessChanged,
}: {
  projectId: number
  projectName: string
  canManage: boolean
  onClose: () => void
  onAccessChanged?: () => void
}) {
  const { t } = useI18n()
  const confirm = useConfirm()
  const [members, setMembers] = useState<ProjectMember[]>([])
  const [candidates, setCandidates] = useState<ProjectMember[]>([])
  const [activity, setActivity] = useState<ProjectActivity[]>([])
  const [query, setQuery] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<ProjectMember | null>(null)
  const [newRole, setNewRole] = useState<'editor' | 'viewer'>('viewer')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [tab, setTab] = useState<'people' | 'activity'>('people')
  const searchRef = useRef<HTMLInputElement>(null)

  const load = async () => {
    setMembers(sortMembers(await apiGet<ProjectMember[]>(`/projects/${projectId}/members`)))
    void refreshActivity()
    if (canManage) {
      setCandidates(await apiGet<ProjectMember[]>(`/projects/${projectId}/member-candidates`))
    } else {
      setCandidates([])
      setSelected(null)
      setPickerOpen(false)
    }
  }

  useEffect(() => {
    // Si ricarica solo al cambio progetto: `load` cambia a ogni render e
    // metterlo nelle dipendenze riaprirebbe la richiesta in loop.
    setLoading(true)
    void load().catch(setError).finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, canManage])

  const owner = members.find((m) => m.role === 'owner') ?? null

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return candidates
    return candidates.filter(
      (c) => c.username.toLowerCase().includes(q) || (c.email ?? '').toLowerCase().includes(q),
    )
  }, [candidates, query])

  const addMember = async () => {
    const candidate = selected
    if (!candidate || !canManage || busy) return
    setBusy(true)
    setError(null)
    try {
      await apiPut<ProjectMember>(`/projects/${projectId}/members/${candidate.user_id}`, {
        user_id: candidate.user_id,
        role: newRole,
      })
      setQuery('')
      setSelected(null)
      setPickerOpen(false)
      setNotice(t('team.accessAdded', { name: candidate.username }))
      await load()
      onAccessChanged?.()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const updateRole = async (member: ProjectMember, role: Exclude<Role, 'owner'>) => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await apiPut<ProjectMember>(`/projects/${projectId}/members/${member.user_id}`, {
        user_id: member.user_id,
        role,
      })
      setMembers((cur) => cur.map((m) => (m.user_id === member.user_id ? { ...m, role } : m)))
      void refreshActivity()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const removeMember = async (member: ProjectMember) => {
    if (busy) return
    const ok = await confirm({
      title: t('share.removeTitle'),
      message: t('share.removeConfirm', { name: member.username }),
    })
    if (!ok) return
    setError(null)
    setBusy(true)
    try {
      await apiDelete(`/projects/${projectId}/members/${member.user_id}`)
      setMembers((cur) => cur.filter((m) => m.user_id !== member.user_id))
      if (canManage) setCandidates((cur) => [...cur, member].sort((a, b) => a.username.localeCompare(b.username)))
      void refreshActivity()
      onAccessChanged?.()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const transferOwner = async (member: ProjectMember) => {
    const ok = await confirm({
      title: t('project.transferOwnerTitle'),
      message: t('project.transferOwnerConfirm'),
    })
    if (!ok) return
    setError(null)
    setBusy(true)
    try {
      await apiPost<ProjectMember>(`/projects/${projectId}/owner`, { user_id: member.user_id })
      onAccessChanged?.()
      await load()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const refreshActivity = async () => {
    try {
      setActivity((await apiGet<{ items: ProjectActivity[] }>(`/projects/${projectId}/activity?limit=15`)).items)
    } catch {
      // L'attività è informativa: non deve bloccare una modifica già riuscita.
    }
  }

  return (
    <Modal title={t('share.title', { name: projectName })} wide onClose={onClose}>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-[color:var(--color-rule)] px-3 pt-2" role="tablist">
          {(['people', 'activity'] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={`-mb-px border-b-2 px-3 py-1.5 text-[12px] font-semibold ${
                tab === key
                  ? 'border-[color:var(--color-sig)] text-[color:var(--color-ink)]'
                  : 'border-transparent text-[color:var(--color-ink-3)] hover:text-[color:var(--color-ink)]'
              }`}
            >
              {key === 'people' ? t('share.tabPeople', { n: members.length }) : t('share.tabActivity')}
            </button>
          ))}
        </div>

        {error != null && <div className="p-3"><ErrorNotice error={error} onDismiss={() => setError(null)} /></div>}
        {notice && <div className="px-3 pt-3"><Notice tone="ok">{notice}</Notice></div>}
        <p className="px-3 pt-3 text-[13px] text-[color:var(--color-ink-2)]">{t(canManage ? 'team.sharingHint' : 'team.sharingReadOnly')}</p>

        {tab === 'people' ? (
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {canManage && (
              <div className="relative mb-3" onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setPickerOpen(false) }}>
                <div className="flex items-center gap-2">
                  <span className="lbl !mb-0 shrink-0">{t('share.addPeople')}</span>
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={(e) => {
                      setQuery(e.target.value)
                      setSelected(null)
                      setPickerOpen(true)
                    }}
                    onFocus={() => setPickerOpen(true)}
                    placeholder={t('share.addPlaceholder')}
                    className="fld"
                    disabled={busy || loading}
                    aria-label={t('share.addPlaceholder')}
                  />
                </div>
                {pickerOpen && (
                  <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-56 overflow-auto border border-[color:var(--color-rule-strong)] bg-[color:var(--color-sheet)] shadow-lg">
                    {filtered.slice(0, 12).map((candidate) => (
                      <button
                        key={candidate.user_id}
                        type="button"
                        disabled={busy}
                        // `onMouseDown` scatta prima del blur che chiuderebbe il menu.
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => { setSelected(candidate); setQuery(candidate.username); setPickerOpen(false); setNotice(null) }}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] hover:bg-[color:var(--color-fill)] disabled:opacity-50"
                      >
                        <Avatar name={candidate.username} id={candidate.user_id} size={22} />
                        <span className="min-w-0 truncate font-semibold">{candidate.username}</span>
                        {candidate.email && <span className="min-w-0 truncate text-[11px] text-[color:var(--color-ink-3)]">{candidate.email}</span>}
                        <IconPlus size={11} className="ml-auto shrink-0" />
                      </button>
                    ))}
                    {!filtered.length && (
                      <p className="px-3 py-2 text-[12px] text-[color:var(--color-ink-3)]">
                        {candidates.length ? t('share.noMatches') : t('share.noCandidates')}
                      </p>
                    )}
                  </div>
                )}
                <div className="mt-3 flex flex-wrap items-end gap-2">
                  <div className="min-w-0 flex-1">
                    <Field label={t('team.newRole')} hint={t(newRole === 'editor' ? 'team.editorHelp' : 'team.viewerHelp')}>
                      <select className="fld" aria-label={t('team.newRole')} value={newRole} disabled={busy} onChange={(e) => setNewRole(e.target.value as 'editor' | 'viewer')}>
                        <option value="viewer">{t('share.roleViewer')}</option>
                        <option value="editor">{t('share.roleEditor')}</option>
                      </select>
                    </Field>
                  </div>
                  <button type="button" className="btn btn-primary" disabled={!selected || busy || loading} onClick={() => void addMember()}>{t('team.addToProject')}</button>
                </div>
                {!loading && !candidates.length && <p className="mt-2 text-[13px] text-[color:var(--color-ink-2)]">{t('team.newAccountHint')}</p>}
              </div>
            )}

            <p className="lbl">{t('share.whoHasAccess')}</p>
            {loading && <Loading label={t('common.loading')} />}
            <div className="divide-y divide-[color:var(--color-rule)] border-y border-[color:var(--color-rule)]">
              {members.map((member) => (
                <div key={member.user_id} className="flex flex-wrap items-center gap-2 py-2 text-[12px]">
                  <Avatar name={member.username} id={member.user_id} size={30} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate font-semibold" title={member.username}>{member.username}</span>
                      {!member.active && <Badge tone="warn">{t('share.inactive')}</Badge>}
                    </div>
                    {member.email && <span className="mono block truncate text-[11px] text-[color:var(--color-ink-3)]">{member.email}</span>}
                  </div>
                  <div className="ml-auto flex items-center gap-2">
                    {member.role === 'owner' || !canManage ? (
                      <span className="mono text-[12px] text-[color:var(--color-ink-2)]">
                        {t(member.role === 'owner' ? 'share.roleOwner' : member.role === 'editor' ? 'share.roleEditor' : 'share.roleViewer')}
                      </span>
                    ) : (
                      <>
                        <select
                          className="fld w-auto min-w-[8rem]"
                          value={member.role}
                          disabled={busy}
                          onChange={(e) => void updateRole(member, e.target.value as 'editor' | 'viewer')}
                          aria-label={t('project.memberRoleFor', { name: member.username })}
                        >
                          <option value="editor">{t('share.roleEditor')}</option>
                          <option value="viewer">{t('share.roleViewer')}</option>
                        </select>
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={busy}
                          onClick={() => void transferOwner(member)}
                          title={t('project.transferOwner')}
                        >
                          {t('share.makeOwner')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={busy}
                          onClick={() => void removeMember(member)}
                          aria-label={t('share.removeFor', { name: member.username })}
                          title={t('project.removeMember')}
                        >
                          <IconTrash size={11} />
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
              {!loading && !members.length && <p className="py-2 text-[12px] text-[color:var(--color-ink-2)]">{t('project.noMembers')}</p>}
            </div>
            {owner && (
              <p className="mt-2 text-[11px] text-[color:var(--color-ink-3)]">
                {t('share.ownerHint', { name: owner.username })}
              </p>
            )}
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-auto p-3">
            <ActivityFeed items={activity} projectId={projectId} />
          </div>
        )}
      </div>
    </Modal>
  )
}

function sortMembers(members: ProjectMember[]): ProjectMember[] {
  const rank: Record<Role, number> = { owner: 0, editor: 1, viewer: 2 }
  return [...members].sort(
    (a, b) => rank[a.role] - rank[b.role] || a.username.localeCompare(b.username),
  )
}
