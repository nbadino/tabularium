import type { PageItem } from './types'

/** Nome leggibile e univoco per una pagina immagine o per una pagina PDF. */
export function pageLabel(page: Pick<PageItem, 'rel_path' | 'source_kind' | 'pdf_page' | 'page_no'> | { rel_path: string; pdf_page?: number | null; page_no?: string | null }): string {
  if (page.pdf_page != null) {
    return `${page.rel_path} · p. ${page.pdf_page + 1}`
  }
  if (page.page_no) return `${page.rel_path} · ${page.page_no}`
  return page.rel_path
}

/** Etichetta compatta per card e sidebar strette: preserva il riferimento utile. */
export function pageShortLabel(page: Pick<PageItem, 'rel_path' | 'source_kind' | 'pdf_page' | 'page_no'>): string {
  if (page.pdf_page != null) return `p. ${page.pdf_page + 1}`
  return page.page_no ? `n. ${page.page_no}` : page.rel_path
}
