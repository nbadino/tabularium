import type { PageItem } from './types'

/**
 * The one thing a project needs next (docs/UX_SIMPLIFICATION.md, principle 3):
 * pages first, then reading them, then checking what the engine wrote, then
 * taking the results out.
 */
export type PrimaryAction =
  | { kind: 'import' }
  | { kind: 'recognize'; pageIds: number[] }
  | { kind: 'review'; count: number; firstPageId: number }
  | { kind: 'export' }

export function primaryAction(pages: PageItem[]): PrimaryAction {
  if (pages.length === 0) return { kind: 'import' }
  const unread = pages.filter((p) => p.status === 'new' && (p.blocks ?? 0) === 0)
  if (unread.length > 0) return { kind: 'recognize', pageIds: unread.map((p) => p.id) }
  const withDrafts = pages.filter((p) => (p.drafts ?? 0) > 0)
  if (withDrafts.length > 0) return { kind: 'review', count: withDrafts.length, firstPageId: withDrafts[0].id }
  return { kind: 'export' }
}
