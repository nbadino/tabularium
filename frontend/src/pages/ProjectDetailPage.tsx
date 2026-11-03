import { useEffect, useMemo, useRef, useState } from 'react'
import { projectPath } from '../app/projectPaths'
import type { InputHTMLAttributes, ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { apiDelete, apiGet, apiPatch, apiPost } from '../lib/api'
import { PAGE_STATUSES, PAGE_TYPES } from '../lib/types'
import type { PageItem, PrefillEngines, Project, RecognitionRun, ScanReport } from '../lib/types'
import { pageTypeLabel, pages as pagesN, statusLabel, STATUS_TONE } from '../lib/vocab'
import { Badge, ErrorNotice, Field, Loading, Menu, Modal, Module, WarnNotice } from '../app/ui'
import { PageWall } from '../app/PageWall'
import { useInference } from '../app/inference'
import { defaultEngine } from '../lib/defaultEngine'
import { primaryAction } from '../lib/primaryAction'
import ShareDialog from '../app/ShareDialog'
import { useConfirm } from '../app/confirm'
import { useAuth, useCanAdminister } from '../app/auth'
import { IconCheck, IconScan, IconTrash, IconUsers } from '../app/icons'
import { useI18n, tn } from '../i18n'
import { writeActiveProject } from '../app/activeProject'
import { accessLabel, projectAccess } from '../app/projectAccess'
import { pageLabel } from '../lib/pageLabel'
import { API_BASE } from '../lib/endpoints'
import ZipImportDialog from '../app/ZipImportDialog'
import { isZip } from '../lib/zipImport'
import CollectionsPanel from '../app/CollectionsPanel'
import { CollectionNameDialog, DeleteCollectionDialog } from '../app/CollectionDialogs'
import { matchesCollection, parseCollectionFilter, useCollections } from '../app/collections'
import type { CollectionFilter } from '../app/collections'
import type { CollectionItem } from '../lib/types'
import { describeError } from '../lib/errors'

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
  const confirm = useConfirm()
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()

  const [project, setProject] = useState<Project | null>(null)
  const [pages, setPages] = useState<PageItem[]>([])
  const [busy, setBusy] = useState(false)
  const [pagesLoading, setPagesLoading] = useState(true)
  const [pageQuery, setPageQuery] = useState('')
  const [pageLimit, setPageLimit] = useState(50)
  const [scanProgress, setScanProgress] = useState<{ current: number; total: number; file?: string }>({ current: 0, total: 0 })
  const [report, setReport] = useState<ScanReport | null>(() => loadReport(projectId))
  const [error, setError] = useState<unknown>(null)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [typedName, setTypedName] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [approveDrafts, setApproveDrafts] = useState(false)
  const [approveBusy, setApproveBusy] = useState(false)
  const [approveNotice, setApproveNotice] = useState<string | null>(null)
  const filesInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [params, setParams] = useSearchParams()
  const view = params.get('vista') === 'tabella' ? 'table' : 'thumbs'
  // The collection being looked at lives in the URL, next to the view.
  const collectionFilter = parseCollectionFilter(params.get('collezione'))
  const { overview: collections, refresh: refreshCollections } = useCollections(projectId)
  const hasCollections = (collections?.collections.length ?? 0) > 0
  const [nameDialog, setNameDialog] = useState<{ collection: CollectionItem | null } | null>(null)
  const [deleteDialog, setDeleteDialog] = useState<CollectionItem | null>(null)
  const [dialogBusy, setDialogBusy] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [moveNotice, setMoveNotice] = useState<string | null>(null)
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value == null) next.delete(key)
    else next.set(key, value)
    setParams(next, { replace: true })
  }
  const [engines, setEngines] = useState<PrefillEngines | null>(null)
  const [recognizing, setRecognizing] = useState(false)
  const [dragging, setDragging] = useState(false)
  // ZIP archives go through their own dialog, one at a time.
  const [zipQueue, setZipQueue] = useState<File[]>([])
  const inference = useInference()
  const canAdminister = useCanAdminister()
  const auth = useAuth()
  const access = project ? projectAccess(project, auth) : null
  const canManage = access?.can_manage === true
  const canEdit = access?.can_edit === true

  useEffect(() => {
    setProject(null)
    setPages([])
    setPagesLoading(true)
    setPageQuery('')
    setPageLimit(50)
    setReport(loadReport(projectId))
    setSelected(new Set())
    apiGet<Project>(`/projects/${projectId}`).then((p) => { setProject(p); writeActiveProject(p.id) }).catch(setError)
    apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)
      .then((r) => setPages(r.items))
      .catch(setError)
      .finally(() => setPagesLoading(false))
    apiGet<PrefillEngines>('/system/prefill-engines').then(setEngines).catch(() => setEngines(null))
  }, [projectId])

  const scan = async () => {
    setBusy(true)
    setError(null)
    try {
      const started = await apiPost<{ job_id: string }>(`/projects/${projectId}/scan/start`)
      let job: { status: string; report?: ScanReport; error?: string; current?: number; total?: number; current_file?: string } = { status: 'running' }
      for (;;) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        job = await apiGet<typeof job>(`/projects/${projectId}/scan/jobs/${started.job_id}`)
        setScanProgress({ current: job.current ?? 0, total: job.total ?? 0, file: job.current_file })
        if (job.status !== 'running') break
      }
      if (job.status === 'error') throw new Error(job.error || 'Scansione fallita')
      const rep = job.report ?? { found_files: 0, registered: 0, duplicates: 0, unsupported: 0, errors: [] }
      if ((rep.missing ?? 0) > 0) {
        const confirmed = await confirm({
          title: t('project.missingPagesTitle'),
          message: t('project.missingPagesConfirm', { n: rep.missing ?? 0 }),
        })
        if (confirmed) {
          await apiPost<ScanReport>(`/projects/${projectId}/scan?confirm_missing=true`)
        }
      }
      setReport(rep)
      sessionStorage.setItem(reportKey(projectId), JSON.stringify(rep))
      const r = await apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)
      setPages(r.items)
      setProject(await apiGet<Project>(`/projects/${projectId}`))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
      setScanProgress({ current: 0, total: 0 })
    }
  }

  const uploadFolder = async (input: FileList | File[] | null) => {
    if (!input || input.length === 0) return
    const all = Array.from(input)
    const zips = all.filter(isZip)
    if (zips.length) setZipQueue((cur) => [...cur, ...zips])
    const files = all.filter((file) => !isZip(file))
    if (files.length === 0) {
      if (filesInputRef.current) filesInputRef.current.value = ''
      return
    }
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      files.forEach((file) => form.append('files', file, file.name))
      const into = typeof collectionFilter === 'number' ? `?collection_id=${collectionFilter}` : ''
      const response = await fetch(`${API_BASE}/projects/${projectId}/import-upload${into}`, { method: 'POST', body: form })
      if (!response.ok) throw new Error(t('project.importFailed', { status: response.status, detail: await response.text() }))
      const rep = await response.json() as ScanReport
      setReport(rep)
      sessionStorage.setItem(reportKey(projectId), JSON.stringify(rep))
      setPages((await apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)).items)
      void refreshCollections()
    } catch (e) { setError(e) } finally {
      setUploading(false)
      if (folderInputRef.current) folderInputRef.current.value = ''
      if (filesInputRef.current) filesInputRef.current.value = ''
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

  const refreshPages = async () => {
    const r = await apiGet<{ items: PageItem[] }>(`/projects/${projectId}/pages`)
    setPages(r.items)
  }

  // Approvazione in blocco: `pageIds` null = tutte le pagine del progetto.
  // Senza `approveDrafts` restano fuori le pagine con bozze non verificate; con
  // il flag le bozze vengono confermate (diventano verità) e approvate.
  const bulkApprove = async (pageIds: number[] | null) => {
    setApproveBusy(true)
    setError(null)
    setApproveNotice(null)
    try {
      const res = await apiPost<{ approved: number; confirmed_blocks: number; skipped_count: number }>(
        `/projects/${projectId}/pages/approve`,
        { page_ids: pageIds, confirm_drafts: approveDrafts },
      )
      setApproveNotice(t('project.approveResult', { approved: res.approved, skipped: res.skipped_count }))
      setSelected(new Set())
      await refreshPages()
    } catch (e) {
      setError(e)
    } finally {
      setApproveBusy(false)
    }
  }

  const engine = defaultEngine(engines ? { recommended: engines.recommended, ocrAvailable: !!engines.ocr?.available } : null, inference)

  // One click from "pages added" to "pages read": the same request as the
  // Recognitions tab, on the pages no engine has read yet (so no drafts to replace).
  const recognizeUnread = async (pageIds: number[]) => {
    setRecognizing(true)
    setError(null)
    try {
      const run = await apiPost<RecognitionRun>(`/projects/${projectId}/recognition-runs`, {
        page_ids: pageIds,
        engine,
        mode: 'replace_drafts',
        model_mode: 'native',
        stop_policy: canAdminister && engine === 'model' ? 'disable_inference' : 'none',
      })
      navigate(projectPath(projectId, `riconoscimenti/${run.id}`))
    } catch (e) {
      setError(e)
      setRecognizing(false)
    }
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

  // The pages of the collection being looked at (all of them without a filter).
  const scopedPages = useMemo(() => pages.filter((page) => matchesCollection(page, collectionFilter)), [pages, collectionFilter])
  const filteredPages = useMemo(() => {
    const query = pageQuery.trim().toLocaleLowerCase()
    return query ? scopedPages.filter((page) => pageLabel(page).toLocaleLowerCase().includes(query)) : scopedPages
  }, [scopedPages, pageQuery])

  const selectCollection = (filter: CollectionFilter) => {
    setSelected(new Set())
    setMoveNotice(null)
    setParam('collezione', filter === 'all' ? null : String(filter))
  }

  const movePages = async (pageIds: number[], target: number | null) => {
    if (!pageIds.length) return
    setError(null)
    try {
      await apiPost(`/projects/${projectId}/pages/collection`, { page_ids: pageIds, collection_id: target })
      const ids = new Set(pageIds)
      setPages((cur) => cur.map((p) => (ids.has(p.id) ? { ...p, collection_id: target } : p)))
      setSelected(new Set())
      const name = target == null ? t('collections.none') : collections?.collections.find((c) => c.id === target)?.name ?? ''
      setMoveNotice(tn('collections.moved', pageIds.length, { name }))
      await refreshCollections()
    } catch (e) {
      setError(e)
    }
  }

  const saveName = async (name: string) => {
    if (!nameDialog) return
    setDialogBusy(true)
    setDialogError(null)
    try {
      if (nameDialog.collection) {
        await apiPatch(`/projects/${projectId}/collections/${nameDialog.collection.id}`, { name })
      } else {
        const created = await apiPost<CollectionItem>(`/projects/${projectId}/collections`, { name })
        // Pages picked before creating the collection go into it.
        if (selected.size) await movePages([...selected], created.id)
      }
      await refreshCollections()
      setNameDialog(null)
    } catch (e) {
      setDialogError(describeError(e).messaggio)
    } finally {
      setDialogBusy(false)
    }
  }

  const deleteCollection = async (mode: 'unassign' | 'delete') => {
    if (!deleteDialog) return
    setDialogBusy(true)
    try {
      await apiDelete(`/projects/${projectId}/collections/${deleteDialog.id}?pages=${mode}${mode === 'delete' ? '&confirm=true' : ''}`)
      if (collectionFilter === deleteDialog.id) selectCollection('all')
      await refreshPages()
      await refreshCollections()
      setDeleteDialog(null)
    } catch (e) {
      setError(e)
      setDeleteDialog(null)
    } finally {
      setDialogBusy(false)
    }
  }

  const allSelected = scopedPages.length > 0 && scopedPages.every((p) => selected.has(p.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(scopedPages.map((p) => p.id)))
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

  if (!project && error == null) return <div className="p-3"><Loading label={t('common.loading')} /></div>

  const dropAnywhere = canEdit && pages.length > 0
  return (
    <div
      className="p-3 data-[dragging]:outline-2 data-[dragging]:outline-dashed data-[dragging]:outline-[color:var(--color-sig)]"
      data-dragging={(dropAnywhere && dragging) || undefined}
      onDragOver={dropAnywhere ? (e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true) } } : undefined}
      onDragLeave={dropAnywhere ? (e) => { if (e.currentTarget === e.target) setDragging(false) } : undefined}
      onDrop={dropAnywhere ? (e) => { e.preventDefault(); setDragging(false); void uploadFolder(e.dataTransfer.files) } : undefined}
    >
      {zipQueue.length > 0 && (
        <ZipImportDialog
          key={`${zipQueue[0].name}-${zipQueue.length}`}
          projectId={projectId}
          file={zipQueue[0]}
          collectionId={typeof collectionFilter === 'number' ? collectionFilter : null}
          onClose={() => setZipQueue((cur) => cur.slice(1))}
          onImported={() => {
            void refreshPages()
            void refreshCollections()
            apiGet<Project>(`/projects/${projectId}`).then(setProject).catch(() => undefined)
          }}
        />
      )}
      <input ref={filesInputRef} type="file" multiple accept=".jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.pdf,.zip" className="hidden" onChange={(e) => void uploadFolder(e.target.files)} />
      <input
        ref={folderInputRef}
        type="file"
        multiple
        onChange={(e) => void uploadFolder(e.target.files)}
        className="hidden"
        {...({ webkitdirectory: '', directory: '' } as InputHTMLAttributes<HTMLInputElement>)}
      />
      <div className="mb-3 flex flex-wrap items-end gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h1 className="truncate text-[26px] font-bold leading-tight tracking-[-0.03em]">
            {project?.name ?? t('project.fallback')}
          </h1>
          <p className="mt-0.5 text-[12px] text-[color:var(--color-ink-2)]">
            {pagesLoading ? t('common.loading') : pagesN(pages.length)}
            {auth.enabled && project && (project.access?.source === 'member' || (project.members_count ?? 1) > 1) && <> · {accessLabel(access)} · {t('team.ownerLine', { name: project.owner_name ?? t('team.noOwner') })}</>}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {canEdit && pages.length > 0 && (
            <Menu label={uploading ? t('project.uploading') : busy ? t('project.scanBusy') : t('project.addPages')}>
              <button type="button" className="menu-item" disabled={uploading} onClick={() => filesInputRef.current?.click()}>{t('workflow.importFiles')}</button>
              <button type="button" className="menu-item" disabled={uploading} onClick={() => folderInputRef.current?.click()}>{t('project.chooseFolder')}</button>
              {project?.archive_dir && <button type="button" className="menu-item" disabled={busy} onClick={() => void scan()}><IconScan size={13} />{t('project.scan')}</button>}
            </Menu>
          )}
          <Menu label={t('ux.projectActions')}>
            <button type="button" onClick={() => setShareOpen(true)} className="menu-item">
              <IconUsers size={13} />
              {t(canManage ? 'share.button' : 'team.peopleAccess')}
            </button>
            <Link to={projectPath(projectId, 'avanzate')} className="menu-item no-underline">{t('project.advanced')}</Link>
            {canManage && (
              <button
                onClick={() => {
                  setTypedName('')
                  setConfirmingDelete(true)
                }}
                className="menu-item text-[color:var(--color-sig-text)]"
              >
                <IconTrash size={13} />
                {t('project.delete')}
              </button>
            )}
          </Menu>
        </div>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {canEdit && !pagesLoading && (() => {
        const action = pages.length === 0 || scopedPages.length === 0 ? primaryAction(pages) : primaryAction(scopedPages)
        if (action.kind === 'import') return (
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); void uploadFolder(e.dataTransfer.files) }}
            data-dragging={dragging || undefined}
            className="mb-4 flex flex-col items-center gap-2 border-2 border-dashed border-[color:var(--color-rule-strong)] px-6 py-12 text-center data-[dragging]:border-[color:var(--color-sig)] data-[dragging]:bg-[color:var(--color-fill)]"
          >
            <p className="text-[16px] font-semibold">{t('project.dropTitle')}</p>
            <p className="max-w-[60ch] text-[12px] text-[color:var(--color-ink-2)]">{t('project.dropHint')}</p>
            <div className="mt-2 flex flex-wrap justify-center gap-2">
              <button type="button" className="btn btn-primary" disabled={uploading} onClick={() => filesInputRef.current?.click()}>
                {uploading ? t('project.uploading') : t('project.chooseFiles')}
              </button>
              <button type="button" className="btn" disabled={uploading} onClick={() => folderInputRef.current?.click()}>{t('project.chooseFolder')}</button>
              {project?.archive_dir && <button type="button" className="btn" disabled={busy} onClick={() => void scan()}>{busy ? t('project.scanBusy') : t('project.scan')}</button>}
            </div>
          </div>
        )
        const bar = (button: ReactNode, hint: string) => (
          <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-[color:var(--color-rule)] py-3">
            {button}
            <span className="text-[12px] text-[color:var(--color-ink-2)]">{hint}</span>
          </div>
        )
        if (action.kind === 'recognize') return bar(
          <button type="button" className="btn btn-primary" disabled={recognizing} onClick={() => void recognizeUnread(action.pageIds)}>
            {tn('project.recognizeN', action.pageIds.length)}
          </button>,
          t('project.recognizeHint', { engine: engine === 'model' ? (inference.model || t('recognition.activeModel')) : t('recognition.localOcr') }),
        )
        if (action.kind === 'review') return bar(
          <Link to={projectPath(projectId, 'studio', { page: action.firstPageId })} className="btn btn-primary no-underline">{tn('project.reviewN', action.count)}</Link>,
          t('project.reviewHint'),
        )
        return bar(
          <Link to={projectPath(projectId, 'riconoscimenti')} className="btn btn-primary no-underline">{t('project.exportResults')}</Link>,
          t('project.exportHint'),
        )
      })()}

      {busy && (
        <div className="mb-3">
          <Module tab={t('project.scanningTitle')}>
            <p className="text-[13px]">{t('project.scanningBody')}</p>
            <p className="mt-1 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
              {scanProgress.total > 0
                ? `File ${scanProgress.current}/${scanProgress.total}${scanProgress.file ? ` · ${scanProgress.file}` : ''}`
                : t('project.scanningHint')}
            </p>
            <div
              className="mt-2 h-1.5 overflow-hidden border border-[color:var(--color-rule-strong)]"
              role="progressbar"
              aria-label={t('project.scanningLabel')}
            >
              <div className="h-full animate-pulse bg-[color:var(--color-sig)] transition-[width] duration-300" style={{ width: scanProgress.total ? `${Math.max(3, (scanProgress.current / scanProgress.total) * 100)}%` : '25%' }} />
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

      {nameDialog && (
        <CollectionNameDialog
          collection={nameDialog.collection}
          busy={dialogBusy}
          error={dialogError}
          onSubmit={(name) => void saveName(name)}
          onClose={() => { setNameDialog(null); setDialogError(null) }}
        />
      )}
      {deleteDialog && (
        <DeleteCollectionDialog
          collection={deleteDialog}
          canDeletePages={canManage}
          busy={dialogBusy}
          onConfirm={(mode) => void deleteCollection(mode)}
          onClose={() => setDeleteDialog(null)}
        />
      )}

      <div className={hasCollections && collections ? 'grid gap-3 md:grid-cols-[208px_minmax(0,1fr)]' : ''}>
      {hasCollections && collections && (
        <CollectionsPanel
          overview={collections}
          active={collectionFilter}
          canEdit={canEdit}
          onSelect={selectCollection}
          onCreate={() => setNameDialog({ collection: null })}
          onRename={(collection) => setNameDialog({ collection })}
          onDelete={setDeleteDialog}
          onDropPages={(ids, target) => void movePages(ids, target)}
        />
      )}
      <div className="min-w-0">
      {pages.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-1" role="group" aria-label={t('project.viewLabel')}>
          {(['thumbs', 'table'] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => setParam('vista', v === 'table' ? 'tabella' : null)}
              className={`btn btn-sm ${view === v ? '!border-[color:var(--color-ink)] !bg-[color:var(--color-ink)] !text-white' : ''}`}
            >
              {t(v === 'thumbs' ? 'project.viewThumbs' : 'project.viewTable')}
            </button>
          ))}
          {/* A project that does not use collections looks as before: one quiet link. */}
          {canEdit && collections && !hasCollections && (
            <button type="button" className="ml-auto text-[12px] text-[color:var(--color-sig-text)] underline-offset-2 hover:underline" onClick={() => setNameDialog({ collection: null })}>
              + {t('collections.createFirst')}
            </button>
          )}
        </div>
      )}

      {canEdit && pages.length > 0 && selected.size > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-[var(--radius-lg)] border border-[color:var(--color-ink)] px-3 py-2 text-[13px]">
          <b className="font-semibold">{tn('collections.selected', selected.size)}</b>
          <Menu label={t('collections.moveTo')} align="left">
            {collections?.collections.map((c) => (
              <button key={c.id} type="button" className="menu-item" onClick={() => void movePages([...selected], c.id)}>{c.name}</button>
            ))}
            <button type="button" className="menu-item italic" onClick={() => void movePages([...selected], null)}>{t('collections.none')}</button>
            <button type="button" className="menu-item text-[color:var(--color-sig-text)]" onClick={() => setNameDialog({ collection: null })}>+ {t('collections.createWithSelection')}</button>
          </Menu>
          {hasCollections && view === 'thumbs' && <span className="text-[12px] text-[color:var(--color-ink-2)]">{t('collections.dragHint')}</span>}
          <button type="button" className="btn btn-sm ml-auto" onClick={() => setSelected(new Set())}>{t('project.deselect')}</button>
        </div>
      )}
      {moveNotice && selected.size === 0 && <p role="status" className="mb-3 text-[12px] text-[color:var(--color-ok)]">{moveNotice}</p>}

      {view === 'thumbs' && pages.length > 0 && (
        scopedPages.length > 0
          ? <PageWall pages={scopedPages} loading={false} selection={canEdit ? { selected, onToggle: toggleOne } : undefined} />
          : <p className="py-6 text-[13px] text-[color:var(--color-ink-2)]">{t('collections.emptyCollection')}</p>
      )}

      {view === 'table' && pages.length > 0 && (<>
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

        <div className="mb-3 max-w-md">
          <Field label={t('recognition.filterPages')}>
            <input type="search" className="fld" value={pageQuery} onChange={(e) => { setPageQuery(e.target.value); setPageLimit(50) }} placeholder={t('recognition.filterPagesHint')} />
          </Field>
        </div>
      <fieldset disabled={!canEdit} className="min-w-0">
      <Module
        tab={t('project.registry')}
        quiet
        flush
        aux={
          <span className="flex flex-wrap items-center gap-2">
            {approveNotice && <span className="text-[11px] text-[color:var(--color-ink-2)]">{approveNotice}</span>}
            <label className="flex items-center gap-1 text-[11px]" title={t('project.approveConfirmDraftsHint')}>
              <input type="checkbox" checked={approveDrafts} onChange={(e) => setApproveDrafts(e.target.checked)} />
              {t('project.approveConfirmDrafts')}
            </label>
            {selected.size > 0 && (
              <button type="button" className="btn btn-sm" disabled={approveBusy} onClick={() => void bulkApprove([...selected])}>
                {tn('project.approveSelected', selected.size)}
              </button>
            )}
            <button type="button" className="btn btn-sm btn-primary" disabled={approveBusy || pages.length === 0} onClick={() => void bulkApprove(null)}>
              {t('project.approveAll')}
            </button>
            <span>{pagesLoading ? t('common.loading') : pagesN(pages.length)}</span>
          </span>
        }
      >

        {pagesLoading ? <Loading label={t('common.loading')} /> : filteredPages.length > 0 ? (
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
                {filteredPages.slice(0, pageLimit).map((p) => (
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
                        aria-label={t('project.selectPage', { path: pageLabel(p) })}
                      />
                    </td>
                    <td className={td}>
                      <img data-fade
                        src={`${API_BASE}/pages/${p.id}/thumbnail`}
                        alt={t('project.thumbnailAlt', { path: pageLabel(p) })}
                        loading="lazy"
                        className="h-10 w-8 border border-[color:var(--color-rule)] bg-white object-cover object-top"
                      />
                    </td>
                    <td className={`${td} min-w-0`}>
                      <span className="mono block max-w-[24rem] truncate" title={pageLabel(p)}>
                        {pageLabel(p)}
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
            <p className="text-[13px] font-semibold">{t(pageQuery ? 'recognition.noFilteredPages' : 'project.emptyTitle')}</p>

          </div>
        )}
        {filteredPages.length > pageLimit && <button type="button" className="btn m-3" onClick={() => setPageLimit((count) => count + 50)}>{t('recognition.showMorePages', { n: filteredPages.length - pageLimit })}</button>}
      </Module>

      </fieldset>
      </>)}
      </div>
      </div>

      {shareOpen && project && (
        <ShareDialog
          projectId={projectId}
          projectName={project.name}
          canManage={canManage}
          onAccessChanged={() => { void apiGet<Project>(`/projects/${projectId}`).then(setProject).catch(setError) }}
          onClose={() => setShareOpen(false)}
        />
      )}

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
