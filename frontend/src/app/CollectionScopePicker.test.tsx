// @vitest-environment jsdom
/**
 * Fine-tuning on the whole project or on chosen collections: the picker builds
 * the `scope` the dataset build receives, and an empty choice is called out.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import CollectionScopePicker, { scopeIsEmpty, WHOLE_PROJECT } from './CollectionScopePicker'
import type { ScopeChoice } from './CollectionScopePicker'
import { matchesCollection, parseCollectionFilter, scopeLabel } from './collections'
import type { CollectionsOverview, PageItem } from '../lib/types'

const overview: CollectionsOverview = {
  collections: [
    { id: 1, name: 'Registri', created_at: '', updated_at: '', pages: 10, with_drafts: 0, by_status: { approved: 4, new: 6 } },
    { id: 2, name: 'Lettere', created_at: '', updated_at: '', pages: 3, with_drafts: 0, by_status: { exported: 1, new: 2 } },
  ],
  unassigned: { key: 'none', pages: 2, with_drafts: 0, by_status: { new: 2 } },
  total: { pages: 15, with_drafts: 0, by_status: { approved: 4, exported: 1, new: 10 } },
}

afterEach(cleanup)

function Harness({ seen }: { seen: (v: ScopeChoice) => void }) {
  const [value, setValue] = useState<ScopeChoice>(WHOLE_PROJECT)
  return <CollectionScopePicker overview={overview} value={value} onChange={(v) => { setValue(v); seen(v) }} />
}

describe('CollectionScopePicker', () => {
  it('starts on the whole project and builds a collections scope', () => {
    const seen = vi.fn()
    render(<Harness seen={seen} />)
    expect(screen.getByLabelText(/Tutto il progetto/)).toBeChecked()
    expect(screen.getByText('5 verificate')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Solo queste collezioni'))
    expect(screen.getByText('Scegli almeno una collezione.')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(/Lettere/))
    fireEvent.click(screen.getByLabelText(/Senza collezione/))
    expect(seen).toHaveBeenLastCalledWith({ kind: 'collections', collection_ids: [2], include_unassigned: true })
    expect(screen.queryByText('Scegli almeno una collezione.')).not.toBeInTheDocument()
  })

  it('names the scope in plain language', () => {
    expect(scopeLabel(undefined)).toBe('Tutto il progetto')
    expect(scopeLabel({ kind: 'collections', collections: [{ id: 1, name: 'Registri' }], include_unassigned: true })).toBe('Registri, Senza collezione')
    expect(scopeIsEmpty({ kind: 'collections', collection_ids: [], include_unassigned: false })).toBe(true)
    expect(scopeIsEmpty(WHOLE_PROJECT)).toBe(false)
  })

  it('filters pages by collection, with "none" as its own state', () => {
    const page = (collection_id: number | null) => ({ id: 1, collection_id } as PageItem)
    expect(matchesCollection(page(null), 'none')).toBe(true)
    expect(matchesCollection(page(3), 'none')).toBe(false)
    expect(matchesCollection(page(3), 3)).toBe(true)
    expect(matchesCollection(page(3), 'all')).toBe(true)
    expect(parseCollectionFilter('none')).toBe('none')
    expect(parseCollectionFilter('7')).toBe(7)
    expect(parseCollectionFilter('x')).toBe('all')
  })
})
