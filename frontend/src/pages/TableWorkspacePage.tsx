import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { apiGet, apiPatch, apiPut } from '../lib/api'
import type { BlockOut, PageItem, TableGrid, TableSaveOut } from '../lib/types'
import { ErrorNotice } from '../app/ui'
import { IconPrev, IconTable } from '../app/icons'
import { VerifiedToggle } from '../studio/components/ContentPane'
import { TableWorkspacePanel } from '../studio/components/TableWorkspacePanel'
import { useI18n } from '../i18n'
import { API_BASE } from '../lib/endpoints'
import { projectPath } from '../app/projectPaths'
import { cleanRelPath } from '../lib/pageLabel'

export default function TableWorkspacePage({ block, page: initialPage }: { block?: BlockOut; page?: PageItem | null }) {
  const { blockId = '' } = useParams()
  const [params] = useSearchParams()
  const { t } = useI18n()
  const serverId = Number(blockId)
  const revision = useRef(block?.annotation_revision ?? 0)
  const [error, setError] = useState<unknown>(null)
  const pageId = block?.page_id ?? (Number(params.get('page')) || null)
  const projectForPage = block?.project_id ?? (Number(params.get('project')) || null)
  const [loadedPage, setLoadedPage] = useState<PageItem | null>(initialPage ?? null)
  useEffect(() => {
    if (initialPage) { setLoadedPage(initialPage); return }
    if (!pageId || !projectForPage) return
    let alive = true
    void apiGet<{ items: PageItem[] }>(`/projects/${projectForPage}/pages`)
      .then((res) => {
        if (!alive) return
        const found = res.items.find((item) => item.id === pageId) ?? null
        setLoadedPage(found)
      })
      .catch(() => {})
    return () => { alive = false }
  }, [initialPage, pageId, projectForPage])
  const page = initialPage ?? loadedPage
  const width = Number(params.get('width')) || 0
  const height = Number(params.get('height')) || 0
  const imageSize = width > 0 && height > 0 ? { w: width, h: height } : null
  const imageUrl = useMemo(() => pageId ? `${API_BASE}/pages/${pageId}/preview` : null, [pageId])
  const navigate = useNavigate()
  const flushRef = useRef<(() => Promise<void>) | null>(null)
  const [confirmed, setConfirmed] = useState(block ? Boolean(block.confirmed) : params.get('confirmed') === '1')
  // Opened from Studio as a secondary window: returning closes/focuses the Studio tab instead of duplicating it.
  const fromStudio = Boolean(window.opener && !window.opener.closed)
  const studioUrl = projectForPage
    ? projectPath(projectForPage, 'studio', pageId ? { page: pageId } : undefined)
    : '/progetti'
  const queryTitle = params.get('title')?.trim() || null
  const pageTitle = page ? cleanRelPath(page.rel_path) : queryTitle
  const pageNum = page ? (page.pdf_page != null ? ` · p. ${page.pdf_page}` : page.page_no ? ` · p. ${page.page_no}` : '') : ''
  const closeAndReturn = async () => {
    try {
      await flushRef.current?.()
      if (!fromStudio) { navigate(studioUrl); return }
      window.opener?.focus()
      window.close()
      window.setTimeout(() => { if (!window.closed) navigate(studioUrl) }, 200)
    } catch (e) { setError(e) }
  }

  if (!Number.isInteger(serverId) || serverId < 1) {
    return <div className="p-4 text-sm">{t('table.loadFailed')}</div>
  }

  // Lo Studio ascolta: riallinea la revisione e, se c'è, la «Verificata».
  const notifyStudio = (message: { revision?: number; blockId?: number; confirmed?: boolean }) => {
    if (!pageId || !('BroadcastChannel' in window)) return
    const channel = new BroadcastChannel('tabularium.annotation-revision')
    channel.postMessage({ pageId, ...message })
    channel.close()
  }
  const saveTable = async (_id: number, grid: TableGrid) => {
    const out = await apiPut<TableSaveOut>(`/blocks/${serverId}/table?expected_revision=${revision.current}`, grid)
    if (out.annotation_revision != null) revision.current = out.annotation_revision
    notifyStudio({ revision: out.annotation_revision })
    return out.otsl
  }
  const setVerified = async (next: boolean) => {
    setConfirmed(next)
    try {
      await flushRef.current?.()
      const out = await apiPatch<BlockOut>(`/blocks/${serverId}?expected_revision=${revision.current}`, { confirmed: next })
      if (out.annotation_revision != null) revision.current = out.annotation_revision
      notifyStudio({ revision: out.annotation_revision, blockId: serverId, confirmed: next })
    } catch (e) {
      setError(e)
      setConfirmed(!next)
    }
  }


  return (
    <main className="flex h-[100dvh] min-h-0 flex-col overflow-hidden bg-[color:var(--color-sheet)]">
      <header className="flex min-h-11 shrink-0 items-center gap-2 border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] px-2 py-1.5">
        <button
          type="button"
          onClick={() => void closeAndReturn()}
          className="btn btn-sm"
          title={fromStudio ? t('table.closeAndReturnHint') : t('table.backToAnnotation')}
        >
          <IconPrev size={13} />
          {fromStudio ? t('table.closeWindow') : t('table.backToStudio')}
        </button>
        <span aria-hidden="true" className="h-5 w-px bg-[color:var(--color-rule)]" />
        <IconTable size={14} />
        <h1 className="min-w-0 truncate text-[13px] font-semibold">
          {t('table.workspaceTitle')}
          {pageTitle && <span className="font-normal text-[color:var(--color-ink-2)]"> · {pageTitle}{pageNum}</span>}
        </h1>
        <div className="ml-auto shrink-0">
          <VerifiedToggle checked={confirmed} onChange={(v) => void setVerified(v)} compact />
        </div>
      </header>
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
      {/* Aperta in un'altra finestra, la vista entra come le sezioni dell'app. */}
      <div className="enter flex min-h-0 flex-1 flex-col p-2">
        <TableWorkspacePanel
          key={serverId}
          serverId={serverId}
          projectId={block?.project_id ?? null}
          pageImageUrl={imageUrl}
          pageId={pageId}
          pageImageSize={imageSize}
          onSaveTable={saveTable}
          flushRef={flushRef}
        />
      </div>
    </main>
  )
}
