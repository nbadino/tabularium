/** Assegnazione, consegna e decisione: una sola azione contestuale accanto alla pagina. */
import { useEffect, useRef, useState } from 'react'
import type { PageItem, PageWork } from '../../lib/types'
import { apiGet, apiPost, apiPut } from '../../lib/api'
import { onAnnotationsChanged } from '../../app/live'
import { useAuth } from '../../app/auth'
import { Badge, ErrorNotice, Field, Loading, Modal } from '../../app/ui'
import { useConfirm } from '../../app/confirm'
import { useI18n } from '../../i18n'

export default function PageWorkControl({ page, revision, beforeAction, onChanged, onReview, disabled }: {
  page: PageItem
  revision: number
  beforeAction: () => Promise<number>
  onChanged: (work: PageWork, mutated?: boolean) => void
  onReview?: () => void
  disabled?: boolean
}) {
  const { t } = useI18n()
  const auth = useAuth()
  const confirm = useConfirm()
  const changed = useRef(onChanged); changed.current = onChanged
  const [loadedWork, setWork] = useState<PageWork | null>(null)
  // Un cambio pagina non deve esporre neppure per un frame le azioni della precedente.
  const work = loadedWork?.page_id === page.id ? loadedWork : null
  const [open, setOpen] = useState(false)
  const [members, setMembers] = useState<Array<{ id: number; username: string }>>([])
  const [assignee, setAssignee] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  useEffect(() => { setOpen(false); setError(null); setMembers([]) }, [page.id])
  useEffect(() => {
    if (!auth.enabled) return
    let alive = true
    apiGet<PageWork>(`/pages/${page.id}/work`).then((value) => { if (alive) { setWork(value); changed.current(value); setError(null) } }).catch((e) => { if (alive) setError(e) })
    const off = onAnnotationsChanged((event) => {
      if (event.pageId === page.id && event.action === 'workflow') {
        apiGet<PageWork>(`/pages/${page.id}/work`).then((value) => { if (alive) { setWork(value); changed.current(value) } }).catch((e) => { if (alive) setError(e) })
      }
    })
    return () => { alive = false; off() }
  }, [page.id, revision, open, auth.enabled])
  useEffect(() => {
    if (!open || !work) return
    setAssignee(String(work.assignee_id ?? '')); setReviewer(String(work.reviewer_id ?? '')); setNote(''); if (work) setError(null)
    if (!work.can_assign) return
    let alive = true
    apiGet<{ items: typeof members }>(`/projects/${page.project_id}/work-members`).then((out) => { if (alive) setMembers(out.items) }).catch(setError)
    return () => { alive = false }
  }, [open, page.id, work?.version])
  if (!auth.enabled) return null
  const assigned = Boolean(work?.assignee_id || work?.reviewer_id)
  const label = work?.can_submit ? t('work.submit') : work?.can_review ? t('work.review') : assigned ? t(`work.state.${work!.state}`) : t(work?.can_assign ? 'work.assign' : 'work.title')
  const update = (value: PageWork) => { setWork(value); onChanged(value, true) }
  const run = async (action: 'assign' | 'submit' | 'approved' | 'changes_requested') => {
    if (!work || busy) return
    if (action === 'assign' && work.state !== 'annotation' && (assignee !== String(work.assignee_id ?? '') || reviewer !== String(work.reviewer_id ?? ''))) {
      if (!await confirm({ title: t('work.reassign'), message: t('work.reassignHint') })) return
    }
    setBusy(true); setError(null)
    try {
      const current = await beforeAction()
      const value = action === 'assign'
        ? await apiPut<PageWork>(`/pages/${page.id}/work`, { assignee_id: assignee ? Number(assignee) : null, reviewer_id: reviewer ? Number(reviewer) : null, expected_version: work.version })
        : await apiPost<PageWork>(`/pages/${page.id}/work/${action === 'submit' ? 'submit' : 'review'}`, {
          expected_version: work.version, annotation_revision: current,
          ...(action === 'submit' ? {} : { decision: action, note }),
        })
      update(value); setOpen(false)
    } catch (e) { setError(e) } finally { setBusy(false) }
  }
  return <>
    <button type="button" disabled={disabled} className={`btn btn-sm ${work?.can_submit || work?.can_review ? 'btn-primary' : ''}`} onClick={() => setOpen(true)}>{label}</button>
    {open && <Modal title={t('work.title')} onClose={() => { if (!busy) setOpen(false) }} footer={<>
      <button className="btn" disabled={busy} onClick={() => setOpen(false)}>{t('common.cancel')}</button>
      {work?.can_submit && <button className="btn btn-primary" disabled={busy} onClick={() => void run('submit')}>{t('work.submit')}</button>}
      {work?.can_review && <>
        <button className="btn" disabled={busy || !note.trim()} onClick={() => void run('changes_requested')}>{t('work.requestChanges')}</button>
        <button className="btn btn-primary" disabled={busy} onClick={() => void run('approved')}>{t('work.approve')}</button>
      </>}
    </>}>
      <div className="space-y-3 p-3">
        {error != null && <ErrorNotice error={error} />}
        {!work ? error ? <p>{t('work.errorLoad')}</p> : <Loading label={t('work.loading')} /> : <>
          <div className="flex flex-wrap items-center gap-2"><span>{page.page_no || page.rel_path}</span><Badge tone={work.state === 'approved' ? 'ok' : 'neutral'}>{t(`work.state.${work.state}`)}</Badge></div>
          {assigned && <dl className="grid grid-cols-2 gap-2 text-[13px]"><dt>{t('work.annotator')}</dt><dd>{work.assignee_name ?? t('work.unassigned')}</dd><dt>{t('work.reviewer')}</dt><dd>{work.reviewer_name ?? t('work.unassigned')}</dd></dl>}
          {work.note && <p className="whitespace-pre-wrap border-l-2 border-[color:var(--color-warn)] pl-2 text-[13px]">{work.note}</p>}
          {work.can_review && <>
            {work.changed_since_submission && <p className="text-[12px] text-[color:var(--color-warn)]">{t('work.changedSinceSubmission')}</p>}
            <button className="btn" onClick={() => { setOpen(false); onReview?.() }}>{t('work.inspect')}</button>
            <Field label={t('work.note')} hint={t('work.noteHint')}><textarea className="fld" rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          </>}
          {!work.can_review && work.state === 'pending' && <p className="text-[13px]">{t('work.waiting', { name: work.reviewer_name ?? t('work.reviewer') })}</p>}
          {!assigned && !work.can_assign && <p className="text-[13px]">{t('work.unassignedHint')}</p>}
          {work.can_assign && <details open={!assigned}>
            <summary className="cursor-pointer text-[13px] font-semibold">{t('work.assignment')}</summary>
            <div className="mt-3 space-y-3">
              <Field label={t('work.annotator')}><select aria-label={t('work.annotator')} className="fld" value={assignee} disabled={busy} onChange={(e) => setAssignee(e.target.value)}><option value="">{t('work.unassigned')}</option>{members.map((m) => <option key={m.id} value={m.id}>{m.username}</option>)}</select></Field>
              <Field label={t('work.reviewer')} hint={t('work.distinct')}><select aria-label={t('work.reviewer')} className="fld" value={reviewer} disabled={busy} onChange={(e) => setReviewer(e.target.value)}><option value="">{t('work.unassigned')}</option>{members.map((m) => <option key={m.id} value={m.id} disabled={String(m.id) === assignee}>{m.username}</option>)}</select></Field>
              <button className="btn" disabled={busy || Boolean(assignee && assignee === reviewer)} onClick={() => void run('assign')}>{t('work.saveAssignment')}</button>
            </div>
          </details>}
        </>}
      </div>
    </Modal>}
  </>
}
