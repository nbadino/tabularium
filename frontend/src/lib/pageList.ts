import type { PageItem } from './types'

/** The id after which a poll asks for new pages: the highest one already listed. */
export function lastPageId(pages: Pick<PageItem, 'id'>[]): number {
  return pages.reduce((max, p) => (p.id > max ? p.id : max), 0)
}

/** Same order as the server list (`ORDER BY rel_path, pdf_page`, NULL first). */
export function comparePages(a: Pick<PageItem, 'rel_path' | 'pdf_page'>, b: Pick<PageItem, 'rel_path' | 'pdf_page'>): number {
  if (a.rel_path !== b.rel_path) return a.rel_path < b.rel_path ? -1 : 1
  return (a.pdf_page ?? -1) - (b.pdf_page ?? -1)
}

/**
 * Adds the pages a poll returned (`?after_id=`) to the list already on screen.
 * During an import the list was polled whole every 3 seconds: on a project of
 * 15,000 pages that was megabytes per poll. Now only the new pages travel.
 */
export function mergeNewPages<T extends Pick<PageItem, 'id' | 'rel_path' | 'pdf_page'>>(current: T[], incoming: T[]): T[] {
  if (incoming.length === 0) return current
  const known = new Set(current.map((p) => p.id))
  const fresh = incoming.filter((p) => !known.has(p.id))
  if (fresh.length === 0) return current
  return [...current, ...fresh].sort(comparePages)
}
