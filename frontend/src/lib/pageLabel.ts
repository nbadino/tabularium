import type { PageItem } from './types'

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
