import { useCallback, useEffect, useState } from 'react'
import { apiGet } from '../lib/api'
import type { CollectionScope, CollectionsOverview, PageItem } from '../lib/types'
import { t } from '../i18n'

/** The filter value of the pages without a collection, as in the API. */
export const UNASSIGNED = 'none'

/** A page filter: every page, one collection, or the pages without one. */
export type CollectionFilter = 'all' | typeof UNASSIGNED | number

export function matchesCollection(page: PageItem, filter: CollectionFilter): boolean {
  if (filter === 'all') return true
  if (filter === UNASSIGNED) return page.collection_id == null
  return page.collection_id === filter
}

export function parseCollectionFilter(raw: string | null): CollectionFilter {
  if (!raw) return 'all'
  if (raw === UNASSIGNED) return UNASSIGNED
  const id = Number(raw)
  return Number.isInteger(id) && id > 0 ? id : 'all'
}

/** The project's collections with their counts; `null` while loading or on error. */
export function useCollections(projectId: number | null): {
  overview: CollectionsOverview | null
  refresh: () => Promise<void>
} {
  const [overview, setOverview] = useState<CollectionsOverview | null>(null)
  const refresh = useCallback(async () => {
    if (projectId == null) return
    try {
      setOverview(await apiGet<CollectionsOverview>(`/projects/${projectId}/collections`))
    } catch {
      setOverview(null)
    }
  }, [projectId])
  useEffect(() => {
    setOverview(null)
    void refresh()
  }, [refresh])
  return { overview, refresh }
}

/** Verified pages: approved, or exported after approval. */
export function verifiedCount(byStatus: Record<string, number>): number {
  return (byStatus.approved ?? 0) + (byStatus.exported ?? 0)
}

/** "Whole project", or the collection names, in plain language. */
export function scopeLabel(scope: CollectionScope | null | undefined): string {
  if (!scope || scope.kind === 'project') return t('scope.wholeProject')
  const names = scope.collections.map((c) => c.name)
  if (scope.include_unassigned) names.push(t('collections.none'))
  return names.join(', ')
}
