import { useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { apiPatch, apiPut } from '../lib/api'
import type { BlockOut, TableGrid, TableSaveOut } from '../lib/types'
import { ErrorNotice } from '../app/ui'
import { VerifiedToggle } from '../studio/components/ContentPane'
import { TableWorkspacePanel } from '../studio/components/TableWorkspacePanel'
import { useI18n } from '../i18n'

export default function TableWorkspacePage({ block }: { block?: BlockOut }) {
  const { blockId = '' } = useParams()
  const [params] = useSearchParams()
  const { t } = useI18n()
  const serverId = Number(blockId)
  const revision = useRef(block?.annotation_revision ?? 0)
  const [error, setError] = useState<unknown>(null)
  const pageId = block?.page_id ?? (Number(params.get('page')) || null)
  const width = Number(params.get('width')) || 0
  const height = Number(params.get('height')) || 0
  const imageSize = width > 0 && height > 0 ? { w: width, h: height } : null
  const imageUrl = useMemo(() => pageId ? `/api/pages/${pageId}/preview` : null, [pageId])
  const navigate = useNavigate()
  const flushRef = useRef<(() => Promise<void>) | null>(null)
  const [confirmed, setConfirmed] = useState(block ? Boolean(block.confirmed) : params.get('confirmed') === '1')
  // Aperta dallo Studio («Apri su un altro schermo») questa finestra ne ha una
  // d'origine: tornare vuol dire chiudersi e lasciare il posto a quella, non
  // aprire una seconda annotazione qui dentro.
  const fromStudio = Boolean(window.opener && !window.opener.closed)
  const studioUrl = `/annotazione?${block?.project_id ? `project=${block.project_id}&` : ''}${pageId ? `page=${pageId}` : ''}`
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
      <header className="flex shrink-0 items-center gap-3 border-b border-[color:var(--color-rule-strong)] bg-[color:var(--color-fill)] px-3 py-2">
        {fromStudio ? (
          <button
            type="button"
            onClick={() => void closeAndReturn()}
            className="btn btn-sm"
            title={t('table.closeAndReturnHint')}
          >
            {t('table.closeAndReturn')}
          </button>
        ) : (
          <button type="button" onClick={() => void closeAndReturn()} className="btn btn-sm">{t('table.backToAnnotation')}</button>
        )}
        <h1 className="mod-tab title-plate m-0">{t('table.workspaceTitle')}</h1>
        <VerifiedToggle checked={confirmed} onChange={(v) => void setVerified(v)} />
        <span className="flex-1" />
        <span className="mono text-[11px] text-[color:var(--color-ink-2)]">#{serverId}</span>
      </header>
      {error != null && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
      {/* Aperta in un'altra finestra, la vista entra come le sezioni dell'app. */}
      <div className="enter flex min-h-0 flex-1 flex-col p-2">
        <TableWorkspacePanel
          key={serverId}
          serverId={serverId}
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
