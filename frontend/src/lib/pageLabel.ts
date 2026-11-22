import type { PageItem } from './types'
import { compareNames } from './uploadBatches'

/** Strip the internal 6-digit upload counter prefix (`000000_`) added for disk uniqueness. */
export function cleanRelPath(relPath: string): string {
  const idx = relPath.lastIndexOf('/')
  if (idx === -1) return relPath.replace(/^\d{6}_/, '')
  return `${relPath.slice(0, idx + 1)}${relPath.slice(idx + 1).replace(/^\d{6}_/, '')}`
}

/** Nome leggibile e univoco per una pagina immagine o per una pagina PDF. */
export function pageLabel(page: Pick<PageItem, 'rel_path' | 'source_kind' | 'pdf_page' | 'page_no'> | { rel_path: string; pdf_page?: number | null; page_no?: string | null }): string {
  const clean = cleanRelPath(page.rel_path)
  if (page.pdf_page != null) {
    return `${clean} · p. ${page.pdf_page + 1}`
  }
  if (page.page_no) return `${clean} · ${page.page_no}`
  return clean
}

/** Etichetta compatta per card e sidebar strette: preserva il riferimento utile. */
export function pageShortLabel(page: Pick<PageItem, 'rel_path' | 'source_kind' | 'pdf_page' | 'page_no'>): string {
  if (page.pdf_page != null) return `p. ${page.pdf_page + 1}`
  return page.page_no ? `n. ${page.page_no}` : cleanRelPath(page.rel_path)
}

/**
 * Pages in file-name order (natural: `_9` before `_10`), then by PDF page. The server
 * lists by `rel_path`, where older uploads carry the `000000_` counter: sorting on the
 * clean name puts a folder back in its own order, so a gap is easy to see.
 */
export function comparePagesByName(
  a: Pick<PageItem, 'rel_path' | 'pdf_page' | 'id'>,
  b: Pick<PageItem, 'rel_path' | 'pdf_page' | 'id'>,
): number {
  return compareNames(cleanRelPath(a.rel_path), cleanRelPath(b.rel_path))
    || (a.pdf_page ?? -1) - (b.pdf_page ?? -1)
    || a.id - b.id
}

/** `pages` sorted with `comparePagesByName`, each clean name computed once (projects hold thousands). */
export function sortPagesByName<T extends Pick<PageItem, 'rel_path' | 'pdf_page' | 'id'>>(pages: T[]): T[] {
  return pages
    .map((page) => ({ page, name: cleanRelPath(page.rel_path) }))
    .sort((a, b) => compareNames(a.name, b.name) || (a.page.pdf_page ?? -1) - (b.page.pdf_page ?? -1) || a.page.id - b.page.id)
    .map(({ page }) => page)
}
