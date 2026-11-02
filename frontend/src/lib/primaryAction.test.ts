import { describe, expect, it } from 'vitest'
import { primaryAction } from './primaryAction'
import type { PageItem } from './types'

const page = (id: number, status: string, blocks = 0, drafts = 0) => ({ id, status, blocks, drafts }) as PageItem

describe('primaryAction', () => {
  it('asks for pages when there are none', () => {
    expect(primaryAction([])).toEqual({ kind: 'import' })
  })

  it('recognizes the pages no engine has read yet', () => {
    expect(primaryAction([page(1, 'new'), page(2, 'new'), page(3, 'new', 4, 4)])).toEqual({ kind: 'recognize', pageIds: [1, 2] })
  })

  it('then reviews the drafts, starting from the first page that has them', () => {
    expect(primaryAction([page(1, 'approved', 3), page(2, 'new', 5, 5), page(3, 'annotated', 4, 1)])).toEqual({ kind: 'review', count: 2, firstPageId: 2 })
  })

  it('offers the export when everything has been read and checked', () => {
    expect(primaryAction([page(1, 'approved', 3), page(2, 'exported', 2)])).toEqual({ kind: 'export' })
  })
})
