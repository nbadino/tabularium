import { describe, expect, it } from 'vitest'
import { lastPageId, mergeNewPages } from './pageList'

const p = (id: number, rel_path: string, pdf_page: number | null = null) => ({ id, rel_path, pdf_page })

describe('lastPageId', () => {
  it('is 0 for an empty list and the max id otherwise', () => {
    expect(lastPageId([])).toBe(0)
    expect(lastPageId([p(3, 'a'), p(9, 'b'), p(5, 'c')])).toBe(9)
  })
})

describe('mergeNewPages', () => {
  it('keeps the same list when nothing is new', () => {
    const cur = [p(1, 'a')]
    expect(mergeNewPages(cur, [])).toBe(cur)
    expect(mergeNewPages(cur, [p(1, 'a')])).toBe(cur)
  })
  it('inserts new pages in server order, pdf pages by number', () => {
    const cur = [p(1, 'a.tif'), p(2, 'c.tif')]
    const out = mergeNewPages(cur, [p(5, 'doc.pdf', 2), p(4, 'b.tif'), p(6, 'doc.pdf', 1)])
    expect(out.map((x) => x.id)).toEqual([1, 4, 2, 6, 5])
  })
})
