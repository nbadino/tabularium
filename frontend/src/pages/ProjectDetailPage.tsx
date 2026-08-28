import { useEffect, useMemo, useRef, useState } from 'react'
import type { InputHTMLAttributes } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from '../lib/api'
import { PAGE_STATUSES, PAGE_TYPES } from '../lib/types'
import type { PageItem, Project, ScanReport } from '../lib/types'
import { pageTypeLabel, pages as pagesN, statusLabel, STATUS_TONE } from '../lib/vocab'
import { Badge, Collapsible, ErrorNotice, Field, Modal, Module, WarnNotice } from '../app/ui'
import { IconCheck, IconPrev, IconScan, IconTrash } from '../app/icons'
import { useI18n, tn } from '../i18n'

type StudyProtocol = {
  corpus_scope: string
  time_start: string | null
  time_end: string | null
  objective: string
  domain_profile: string
  model_adapter: string
  gold_pages: number[]
  version: number
  updated_at: string
}

/** Il referto di scansione sopravvive alla navigazione: è un risultato, non un lampo. */
const reportKey = (id: number) => `tabularium.scanReport.${id}`

function loadReport(id: number): ScanReport | null {
  try {
    const raw = sessionStorage.getItem(reportKey(id))
    return raw ? (JSON.parse(raw) as ScanReport) : null
  } catch {
    return null
  }
}

export default function ProjectDetailPage() {
  const { t } = useI18n()
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()

  const [project, setProject] = useState<Project | null>(null)
  const [pages, setPages] = useState<PageItem[]>([])
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<ScanReport | null>(() => loadReport(projectId))
  const [error, setError] = useState<unknown>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [typedName, setTypedName] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [protocol, setProtocol] = useState<StudyProtocol | null>(null)
  const [protocolBusy, setProtocolBusy] = useState(false)
  const [pilot, setPilot] = useState<{ pages: Array<{ id: number; rel_path: string }>; target: number } | null>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [domainProfiles, setDomainProfiles] = useState<Array<{ id: string; name: string }>>([])
  const [modelAdapters, setModelAdapters] = useState<Array<{ adapter_id: string; display_name: string }>>([])

  useEffect(() => {
    apiGet<{ items: Array<{ id: string; name: string }> }>('/system/domain-profiles')
      .then((r) => setDomainProfiles(r.items))
      .catch(() => setDomainProfiles([]))
    apiGet<{ items: Array<{ adapter_id: string; display_name: string }> }>('/system/model-adapters')
      .then((r) => setModelAdapters(r.items))
      .catch(() => setModelAdapters([]))
  }, [])

  useEffect(() => {
    setReport(loadReport(projectId))
    setSelected(new Set())
    apiGet<Project>(`/projects/${projectId}`).then(setProject).catch(setError)
    apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)
      .then((r) => setPages(r.items))
      .catch(setError)
    apiGet<StudyProtocol>(`/projects/${projectId}/study-protocol`)
      .then(setProtocol)
      .catch(() => setProtocol(null))
  }, [projectId])

  const scan = async () => {
    setBusy(true)
    setError(null)
    try {
      const rep = await apiPost<ScanReport>(`/projects/${projectId}/scan`)
      setReport(rep)
      sessionStorage.setItem(reportKey(projectId), JSON.stringify(rep))
      const r = await apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)
      setPages(r.items)
      setProject(await apiGet<Project>(`/projects/${projectId}`))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const uploadFolder = async (files: FileList | null) => {
    if (!files?.length) return
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      Array.from(files).forEach((file) => form.append('files', file, file.name))
      const response = await fetch(`/api/projects/${projectId}/import-upload`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(t('project.importFailed', { status: response.status, detail: await response.text() }))
      const rep = await response.json() as ScanReport
      setReport(rep)
      sessionStorage.setItem(reportKey(projectId), JSON.stringify(rep))
      setPages((await apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)).items)
    } catch (e) { setError(e) } finally {
      setUploading(false)
      if (folderInputRef.current) folderInputRef.current.value = ''
    }
  }

  const updateField = async (pageId: number, field: string, value: string) => {
    setPages((cur) => cur.map((p) => (p.id === pageId ? { ...p, [field]: value } : p)))
    try {
      await apiPatch<PageItem>(`/pages/${pageId}`, { [field]: value || null })
    } catch (e) {
      setError(e)
    }
  }

  /** Applica un campo a tutte le pagine selezionate: un edit invece di N. */
  const bulkField = async (field: string, value: string) => {
    const ids = [...selected]
    if (ids.length === 0 || !value) return
    setPages((cur) => cur.map((p) => (selected.has(p.id) ? { ...p, [field]: value } : p)))
    try {
      await Promise.all(
        ids.map((pid) => apiPatch<PageItem>(`/pages/${pid}`, { [field]: value })),
      )
    } catch (e) {
      setError(e)
    }
  }

  const setGoldSet = async () => {
    if (!selected.size) return
    try {
      await apiPost(`/projects/${projectId}/gold-set`, [...selected])
    } catch (e) {
      setError(e)
    }
  }

  const approvePage = async (pageId: number) => {
    setError(null)
    try {
      await apiPost(`/pages/${pageId}/approve`)
      setPages((cur) => cur.map((p) => (p.id === pageId ? { ...p, status: 'approved' } : p)))
    } catch (e) {
      setError(e)
    }
  }

  const saveProtocol = async () => {
    if (!protocol) return
    setProtocolBusy(true)
    try {
      const saved = await apiPut<StudyProtocol>(`/projects/${projectId}/study-protocol`, protocol)
      setProtocol(saved)
    } catch (e) {
      setError(e)
    } finally {
      setProtocolBusy(false)
    }
  }

  const generatePilot = async () => {
    try {
      const sample = await apiGet<{ pages: Array<{ id: number; rel_path: string }>; target: number }>(`/projects/${projectId}/pilot-sample?target=40&seed=42`)
      setPilot(sample)
    } catch (e) { setError(e) }
  }

  const savePilot = async () => {
    if (!pilot) return
    try {
      await apiPost(`/projects/${projectId}/pilot-sample/save`, pilot.pages.map((p) => p.id))
    } catch (e) { setError(e) }
  }

  const removeProject = async () => {
    if (!project || typedName !== project.name) return
    setDeleting(true)
    try {
      await apiDelete(`/projects/${projectId}?confirm=true`)
      sessionStorage.removeItem(reportKey(projectId))
      navigate('/progetti')
    } catch (e) {
      setError(e)
      setDeleting(false)
      setConfirmingDelete(false)
    }
  }

  const allSelected = pages.length > 0 && selected.size === pages.length
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(pages.map((p) => p.id)))
  const toggleOne = (pid: number) =>
    setSelected((cur) => {
      const next = new Set(cur)
      if (next.has(pid)) next.delete(pid)
      else next.add(pid)
      return next
    })

  const th =
    'px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]'
  const td = 'px-2 py-1 align-middle'
  const errorCount = useMemo(() => report?.errors.length ?? 0, [report])

  return (
    <div className="p-3">
      <div className="mb-3 flex flex-wrap items-end gap-x-4 gap-y-2 border-b border-[color:var(--color-rule-strong)] pb-3">
        <div className="min-w-0">
          <Link
            to="/progetti"
            className="mb-0.5 inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-[0.04em] no-underline"
          >
            <IconPrev size={11} />
            {t('project.back')}
          </Link>
          <h1 className="truncate text-[26px] font-bold leading-tight tracking-[-0.03em]">
            {project?.name ?? t('project.fallback')}
          </h1>
          {project?.archive_dir && (
            <p className="mono mt-0.5 truncate text-[11px] text-[color:var(--color-ink-3)]">
              {project.archive_dir}
            </p>
          )}
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="mono text-[12px] text-[color:var(--color-ink-2)]">
            {pagesN(pages.length)}
          </span>
          <button onClick={() => void scan()} disabled={busy} className="btn btn-primary">
            <IconScan size={13} />
            {busy ? t('project.scanBusy') : t('project.scan')}
          </button>
          <button type="button" onClick={() => folderInputRef.current?.click()} disabled={uploading} className="btn">
            {uploading ? t('project.uploading') : t('project.chooseFolder')}
          </button>
          <input
            ref={folderInputRef}
            type="file"
            multiple
            onChange={(e) => void uploadFolder(e.target.files)}
            className="hidden"
            {...({ webkitdirectory: '', directory: '' } as InputHTMLAttributes<HTMLInputElement>)}
          />
          <button
            onClick={() => {
              setTypedName('')
              setConfirmingDelete(true)
            }}
            className="btn btn-danger"
          >
            <IconTrash size={13} />
            {t('project.delete')}
          </button>
        </div>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {protocol && (
        <div className="mb-3">
          <Collapsible
            tab={t('project.protocolTab', { version: protocol.version })}
            quiet
            aux={<span>{protocolBusy ? t('project.protocolSaving') : t('project.protocolVersioned')}</span>}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('project.objective')}>
                <textarea value={protocol.objective} onChange={(e) => setProtocol((p) => p ? { ...p, objective: e.target.value } : p)} className="fld min-h-16" placeholder={t('project.objectivePlaceholder')} />
              </Field>
              <Field label={t('project.corpusScope')} hint={t('project.corpusScopeHint')}>
                <input value={protocol.corpus_scope} onChange={(e) => setProtocol((p) => p ? { ...p, corpus_scope: e.target.value } : p)} className="fld" />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label={t('project.from')}><input type="date" value={protocol.time_start ?? ''} onChange={(e) => setProtocol((p) => p ? { ...p, time_start: e.target.value || null } : p)} className="fld" /></Field>
                <Field label={t('project.to')}><input type="date" value={protocol.time_end ?? ''} onChange={(e) => setProtocol((p) => p ? { ...p, time_end: e.target.value || null } : p)} className="fld" /></Field>
              </div>
              <Field label={t('project.domainProfile')}>
                <div className="flex gap-2">
                  <select
                    value={protocol.domain_profile}
                    onChange={(e) => setProtocol((p) => p ? { ...p, domain_profile: e.target.value } : p)}
                    className="fld"
                  >
                    {(domainProfiles.some((d) => d.id === protocol.domain_profile) || !protocol.domain_profile
                      ? domainProfiles
                      : [{ id: protocol.domain_profile, name: protocol.domain_profile }, ...domainProfiles]
                    ).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                  </select>
                  <select
                    value={protocol.model_adapter}
                    onChange={(e) => setProtocol((p) => p ? { ...p, model_adapter: e.target.value } : p)}
                    className="fld"
                  >
                    {(modelAdapters.some((a) => a.adapter_id === protocol.model_adapter) || !protocol.model_adapter
                      ? modelAdapters
                      : [{ adapter_id: protocol.model_adapter, display_name: protocol.model_adapter }, ...modelAdapters]
                    ).map((a) => <option key={a.adapter_id} value={a.adapter_id}>{a.display_name}</option>)}
                  </select>
                </div>
              </Field>
              <div className="flex items-end justify-end">
                <button type="button" onClick={() => void saveProtocol()} disabled={protocolBusy} className="btn btn-sm btn-primary">{t('project.saveProtocol')}</button>
              </div>
            </div>
          </Collapsible>
        </div>
      )}

      <div className="mb-3">
        <Module tab={t('project.pilotTab')} quiet aux={<span>{t('project.pilotRange')}</span>}>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => void generatePilot()} className="btn btn-sm">{t('project.generateSample')}</button>
            {pilot && <><span className="mono text-[12px]">{tn('project.pilotSelected', pilot.pages.length)}</span><button type="button" onClick={() => void savePilot()} className="btn btn-sm btn-primary">{t('project.saveToProtocol')}</button></>}
          </div>
          {pilot && <p className="mt-2 mono truncate text-[11px] text-[color:var(--color-ink-3)]">{pilot.pages.slice(0, 5).map((p) => p.rel_path).join(' · ')}{pilot.pages.length > 5 ? ' …' : ''}</p>}
        </Module>
      </div>

      {busy && (
        <div className="mb-3">
          <Module tab={t('project.scanningTitle')}>
            <p className="text-[13px]">{t('project.scanningBody')}</p>
            <p className="mt-1 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('project.scanningHint')}
            </p>
            <div
              className="mt-2 h-1.5 overflow-hidden border border-[color:var(--color-rule-strong)]"
              role="progressbar"
              aria-label={t('project.scanningLabel')}
            >
              <div className="h-full w-1/3 animate-pulse bg-[color:var(--color-sig)]" />
            </div>
          </Module>
        </div>
      )}

      {report && !busy && (
        <div className="mb-3 grid gap-3 sm:grid-cols-2">
          <Module
            tab={t('project.report')}
            quiet
            aux={
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => {
                  sessionStorage.removeItem(reportKey(projectId))
                  setReport(null)
                }}
              >
                {t('project.archive')}
              </button>
            }
          >
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
              <div>
                <dt className="lbl !mb-0">{t('project.foundFiles')}</dt>
                <dd className="mono text-[17px] font-semibold leading-none">
                  {report.found_files}
                </dd>
              </div>
              <div>
                <dt className="lbl !mb-0">{t('project.registered')}</dt>
                <dd className="mono text-[17px] font-semibold leading-none">{report.registered}</dd>
              </div>
              <div>
                <dt className="lbl !mb-0">{t('project.duplicates')}</dt>
                <dd className="mono text-[17px] font-semibold leading-none">{report.duplicates}</dd>
              </div>
              <div>
                <dt className="lbl !mb-0">{t('project.unsupported')}</dt>
                <dd className="mono text-[17px] font-semibold leading-none">
                  {report.unsupported}
                </dd>
              </div>
            </dl>
          </Module>
          {errorCount > 0 ? (
            <WarnNotice title={tn('project.skipCount', errorCount)}>
              <ul className="mono max-h-32 list-inside list-disc space-y-0.5 overflow-auto text-[11px]">
                {report.errors.map((e, i) => (
                  <li key={i} className="break-all">
                    {e}
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-[12px]">
                <b className="font-semibold">{t('errors.whatToDo')}</b>
                {t('project.skippedBody')}
              </p>
            </WarnNotice>
          ) : (
            <Module tab={t('project.noErrors')} quiet>
              <p className="text-[12px] text-[color:var(--color-ink-2)]">
                {t('project.noErrorsBody')}
              </p>
            </Module>
          )}
        </div>
      )}

      {selected.size > 0 && (
        <div className="mb-3">
          <Module
            tab={tn('project.bulkTab', selected.size)}
            aux={
              <button type="button" className="btn btn-sm" onClick={() => setSelected(new Set())}>
                {t('project.deselect')}
              </button>
            }
          >
            <p className="mb-2 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('project.bulkBody')}
            </p>
            <div className="grid gap-3 sm:grid-cols-4">
              <Field label={t('project.issueDate')}>
                <input
                  type="date"
                  onChange={(e) => void bulkField('issue_date', e.target.value)}
                  className="fld"
                />
              </Field>
              <Field label={t('project.issueYear')} hint={t('project.tabHint')}>
                <input
                  placeholder={t('project.issueNoPlaceholder')}
                  onBlur={(e) => void bulkField('issue_no', e.target.value)}
                  className="fld"
                />
              </Field>
              <Field label={t('project.pageType')}>
                <select
                  defaultValue=""
                  onChange={(e) => void bulkField('page_type', e.target.value)}
                  className="fld"
                >
                  <option value="">{t('project.noChange')}</option>
                  {PAGE_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {pageTypeLabel(type)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t('project.status')}>
                <select
                  defaultValue=""
                  onChange={(e) => void bulkField('status', e.target.value)}
                  className="fld"
                >
                  <option value="">{t('project.noChange')}</option>
                  {PAGE_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {statusLabel(s)}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <button type="button" onClick={() => void setGoldSet()} className="btn btn-sm mt-3">
              {t('project.protectGold')}
            </button>
          </Module>
        </div>
      )}

      <Module
        tab={t('project.registry')}
        quiet
        flush
        aux={<span>{pagesN(pages.length)}</span>}
      >
        {pages.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-[12px]">
              <thead className="border-b border-[color:var(--color-rule)] bg-[color:var(--color-fill)]">
                <tr>
                  <th className={`${th} w-8`}>
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label={t('project.selectAll')}
                    />
                  </th>
                  <th className={`${th} w-12`}>{t('project.colPage')}</th>
                  <th className={th}>{t('project.colFile')}</th>
                  <th className={`${th} w-32`}>{t('project.colDate')}</th>
                  <th className={`${th} w-28`}>{t('project.colIssue')}</th>
                  <th className={`${th} w-16`}>{t('project.colNumber')}</th>
                  <th className={`${th} w-36`}>{t('project.colType')}</th>
                  <th className={`${th} w-32`}>{t('project.colStatus')}</th>
                  <th className={`${th} w-24`}>{t('project.colAction')}</th>
                </tr>
              </thead>
              <tbody className="ruled">
                {pages.map((p) => (
                  <tr
                    key={p.id}
                    className={
                      selected.has(p.id)
                        ? 'bg-[color:var(--color-sig-wash)]'
                        : 'hover:bg-[color:var(--color-fill)]'
                    }
                  >
                    <td className={td}>
                      <input
                        type="checkbox"
                        checked={selected.has(p.id)}
                        onChange={() => toggleOne(p.id)}
                        aria-label={t('project.selectPage', { path: p.rel_path })}
                      />
                    </td>
                    <td className={td}>
                      <img
                        src={`/api/pages/${p.id}/thumbnail`}
                        alt={t('project.thumbnailAlt', { path: p.rel_path })}
                        loading="lazy"
                        className="h-10 w-8 border border-[color:var(--color-rule)] bg-white object-cover object-top"
                      />
                    </td>
                    <td className={`${td} min-w-0`}>
                      <span className="mono block max-w-[24rem] truncate" title={p.rel_path}>
                        {p.rel_path}
                      </span>
                      <span className="mono text-[11px] text-[color:var(--color-ink-3)]">
                        {p.width}×{p.height}
                      </span>
                    </td>
                    <td className={td}>
                      <input
                        type="date"
                        value={p.issue_date ?? ''}
                        onChange={(e) => void updateField(p.id, 'issue_date', e.target.value)}
                        aria-label={t('project.dateOf', { path: p.rel_path })}
                        className="fld !py-0.5 !text-[12px]"
                      />
                    </td>
                    <td className={td}>
                      <input
                        value={p.issue_no ?? ''}
                        onChange={(e) => void updateField(p.id, 'issue_no', e.target.value)}
                        placeholder={t('project.issueNoPlaceholder')}
                        aria-label={t('project.issueOf', { path: p.rel_path })}
                        className="fld !py-0.5 !text-[12px]"
                      />
                    </td>
                    <td className={td}>
                      <input
                        value={p.page_no ?? ''}
                        onChange={(e) => void updateField(p.id, 'page_no', e.target.value)}
                        placeholder="1"
                        aria-label={t('project.pageNumOf', { path: p.rel_path })}
                        className="fld !py-0.5 !text-[12px]"
                      />
                    </td>
                    <td className={td}>
                      <select
                        value={p.page_type ?? ''}
                        onChange={(e) => void updateField(p.id, 'page_type', e.target.value)}
                        aria-label={t('project.typeOf', { path: p.rel_path })}
                        className="fld !py-0.5 !text-[12px]"
                      >
                        <option value="">—</option>
                        {PAGE_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {pageTypeLabel(type)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={td}>
                      <select
                        value={p.status}
                        onChange={(e) => void updateField(p.id, 'status', e.target.value)}
                        aria-label={t('project.statusOf', { path: p.rel_path })}
                        className="fld !py-0.5 !text-[12px]"
                      >
                        {PAGE_STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {statusLabel(s)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className={td}>
                      {p.status !== 'approved' && p.status !== 'exported' ? (
                        <button
                          onClick={() => void approvePage(p.id)}
                          className="btn btn-sm"
                          title={t('project.approveTitle')}
                        >
                          <IconCheck size={11} />
                          {t('project.approve')}
                        </button>
                      ) : (
                        <Badge tone={STATUS_TONE[p.status] ?? 'ok'}>{statusLabel(p.status)}</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-2 p-6">
            <IconScan size={22} />
            <p className="text-[13px] font-semibold">{t('project.emptyTitle')}</p>
            <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('project.emptyBody')}
            </p>
            <button onClick={() => void scan()} disabled={busy} className="btn btn-primary">
              <IconScan size={13} />
              {t('project.scan')}
            </button>
          </div>
        )}
      </Module>

      {confirmingDelete && project && (
        <Modal
          title={t('project.deleteModalTitle')}
          onClose={() => !deleting && setConfirmingDelete(false)}
          footer={
            <>
              <button onClick={() => setConfirmingDelete(false)} disabled={deleting} className="btn">
                {t('common.cancel')}
              </button>
              <button
                onClick={() => void removeProject()}
                disabled={typedName !== project.name || deleting}
                className="btn btn-primary"
              >
                <IconTrash size={13} />
                {deleting ? t('project.deleting') : t('project.deleteForever')}
              </button>
            </>
          }
        >
          <div className="p-3">
            <p className="text-[13px]">{tn('project.deleteBody', pages.length)}</p>
            <p className="mt-2 text-[12px] text-[color:var(--color-ink-2)]">
              {t('project.deleteBody2', { dir: project.archive_dir ?? '—' })}
            </p>
            <div className="mt-3">
              <Field
                label={t('project.typeName')}
                hint={t('project.typeNameHint', { name: project.name })}
              >
                <input
                  autoFocus
                  value={typedName}
                  onChange={(e) => setTypedName(e.target.value)}
                  placeholder={project.name}
                  className="fld"
                />
              </Field>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
