import { describe, expect, it } from 'vitest'
import { comparePagesByName, sortPagesByName } from './pageLabel'

describe('comparePagesByName', () => {
  it('orders by the file name, ignoring the old upload counter', () => {
    const pages = [
      { id: 1, rel_path: '000000_LSI_5294_010.tif', pdf_page: null },
      { id: 2, rel_path: '000003_LSI_5294_002.tif', pdf_page: null },
      { id: 3, rel_path: 'LSI_5294_003.tif', pdf_page: null },
      { id: 4, rel_path: '000001_LSI_5290_220.tif', pdf_page: null },
    ]
    expect([...pages].sort(comparePagesByName).map((p) => p.id)).toEqual([4, 2, 3, 1])
  })
  it('keeps the pages of one PDF in page order', () => {
    const pages = [
      { id: 7, rel_path: 'a.pdf', pdf_page: 2 },
      { id: 8, rel_path: 'a.pdf', pdf_page: 0 },
      { id: 9, rel_path: 'a.pdf', pdf_page: 1 },
    ]
    expect([...pages].sort(comparePagesByName).map((p) => p.pdf_page)).toEqual([0, 1, 2])
  })
})

describe('sortPagesByName', () => {
  it('matches comparePagesByName and leaves the input alone', () => {
    const pages = [
      { id: 1, rel_path: '000000_b_10.tif', pdf_page: null },
      { id: 2, rel_path: 'b_9.tif', pdf_page: null },
      { id: 3, rel_path: '000002_a.tif', pdf_page: null },
    ]
    expect(sortPagesByName(pages).map((p) => p.id)).toEqual([...pages].sort(comparePagesByName).map((p) => p.id))
    expect(pages.map((p) => p.id)).toEqual([1, 2, 3])
  })
})
