import { useMemo, useRef } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { apiPost, apiPut } from '../lib/api'
import type { TableDetectOut, TableDetectRequest, TableGrid, TableSaveOut } from '../lib/types'
import { TableWorkspacePanel } from '../studio/components/ContentPane'
import { useI18n } from '../i18n'

export default function TableWorkspacePage() {
  const { blockId = '' } = useParams()
  const [params] = useSearchParams()
  const { t } = useI18n()
  const serverId = Number(blockId)
  const pageId = Number(params.get('page')) || null
  const width = Number(params.get('width')) || 0
  const height = Number(params.get('height')) || 0
  const imageSize = width > 0 && height > 0 ? { w: width, h: height } : null
  const imageUrl = useMemo(() => pageId ? `/api/pages/${pageId}/preview` : null, [pageId])
  const navigate = useNavigate()
  const flushRef = useRef<(() => Promise<void>) | null>(null)
  // Aperta dallo Studio («Apri su un altro schermo») questa finestra ne ha una
  // d'origine: tornare vuol dire chiudersi e lasciare il posto a quella, non
  // aprire una seconda annotazione qui dentro.
  const fromStudio = Boolean(window.opener && !window.opener.closed)
  const closeAndReturn = async () => {
    await flushRef.current?.()
    window.opener?.focus()
    window.close()
    // Se il browser non lascia chiudere, si torna all'annotazione qui.
    window.setTimeout(() => {
      if (!window.closed) navigate('/annotazione')
    }, 200)
  }

  if (!Number.isInteger(serverId) || serverId < 1) {
    return <div className="p-4 text-sm">{t('table.loadFailed')}</div>
  }

  const saveTable = async (_id: number, grid: TableGrid) => {
    const out = await apiPut<TableSaveOut>(`/blocks/${serverId}/table`, grid)
    if (pageId && 'BroadcastChannel' in window) {
      const channel = new BroadcastChannel('tabularium.annotation-revision')
      channel.postMessage({ pageId, revision: out.annotation_revision })
      channel.close()
    }
    return out.otsl
  }
  const detectTable = (_id: number, request: TableDetectRequest) =>
    apiPost<TableDetectOut>(`/blocks/${serverId}/table/detect`, request)

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
          <Link to="/annotazione" className="btn btn-sm no-underline">{t('table.backToAnnotation')}</Link>
        )}
        <h1 className="mod-tab title-plate m-0">{t('table.workspaceTitle')}</h1>
        <span className="flex-1" />
        <span className="mono text-[11px] text-[color:var(--color-ink-2)]">#{serverId}</span>
      </header>
      {/* Aperta in un'altra finestra, la vista entra come le sezioni dell'app. */}
      <div className="enter flex min-h-0 flex-1 flex-col p-2">
        <TableWorkspacePanel
          key={serverId}
          id={`standalone-${serverId}`}
          serverId={serverId}
          pageImageUrl={imageUrl}
          pageId={pageId}
          pageImageSize={imageSize}
          onSaveTable={saveTable}
          onDetectTable={detectTable}
          flushRef={flushRef}
        />
      </div>
    </main>
  )
}
