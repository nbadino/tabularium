// @vitest-environment jsdom
/**
 * The consensus pane: blocks listed from the most disputed, one block explained,
 * a table shown cell by cell, and the models' readings side by side.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import ConsensusPane, { type ConsensusActions } from './ConsensusPane'
import { setLocale } from '../../i18n'
import type { ConsensusView, PageConsensus } from '../consensus'

afterEach(cleanup)

const page: PageConsensus = {
  run_id: 7,
  models: [
    { key: 'p1', profile_id: 1, name: 'OCRia-1', adapter_id: 'llamaindex-ocria-1', model_name: 'ocria-1', state: 'finished', error: null },
    { key: 'p2', profile_id: 2, name: 'Argon', adapter_id: 'gemini-argon', model_name: 'gemini-4-argon', state: 'finished', error: null },
  ],
  outputs: {
    p1: [
      { label: 'Title', points: [[0, 0], [100, 20]], content: 'Arrivi' },
      { label: 'Table', points: [[0, 30], [100, 90]], content: '' },
    ],
    p2: [
      { label: 'Title', points: [[0, 0], [100, 20]], content: 'Arrivi' },
      { label: 'Table', points: [[0, 30], [100, 90]], content: '' },
      { label: 'Note', points: [[0, 95], [50, 99]], content: 'Fonte' },
    ],
  },
  comparison: {
    models: ['p1', 'p2'],
    agreement: 0.81,
    clusters: [
      { id: 0, members: { p1: 0, p2: 0 }, parts: { p1: [0], p2: [0] }, split: false, label: 'Title', labels: { p1: 'Title', p2: 'Title' }, bbox: [0, 0, 100, 20], text: 0, layout: 0, label_agree: true, missing: [], score: 0, table: null },
      {
        id: 1, members: { p1: 1, p2: 1 }, parts: { p1: [1], p2: [1] }, split: false, label: 'Table', labels: { p1: 'Table', p2: 'Table' }, bbox: [0, 30, 100, 90], text: 0.1, layout: 0, label_agree: true, missing: [], score: 0.1,
        table: {
          reference: 'p1', rows: 2, cols: 2, score: 0.1,
          cells: [
            { r: 0, c: 0, rowspan: 1, colspan: 1, values: { p1: 'Nave', p2: 'Nave' }, score: 0 },
            { r: 0, c: 1, rowspan: 1, colspan: 1, values: { p1: 'Capitano', p2: 'Capitano' }, score: 0 },
            { r: 1, c: 0, rowspan: 1, colspan: 1, values: { p1: 'Aurora', p2: 'Aurora' }, score: 0 },
            { r: 1, c: 1, rowspan: 1, colspan: 1, values: { p1: 'Rossi', p2: 'Russo' }, score: 0.4 },
          ],
          extra_rows: [{ model: 'p2', index: 2, texts: ['Lampo', 'Bari'] }],
          extra_cols: [],
        },
      },
      { id: 2, members: { p1: null, p2: 2 }, parts: { p2: [2] }, split: false, label: 'Note', labels: { p2: 'Note' }, bbox: [0, 95, 50, 99], text: 0, layout: 0, label_agree: true, missing: ['p1'], score: 1, table: null },
    ],
  },
}

function Harness({ data = page, onClose = () => {}, actions }: { data?: PageConsensus | null; onClose?: () => void; actions?: ConsensusActions }) {
  const [view, setView] = useState<ConsensusView>('disagreement')
  const [selected, setSelected] = useState<number | null>(null)
  return (
    <>
      <span data-testid="view">{view}</span>
      <ConsensusPane page={data} loading={false} view={view} onView={setView} selected={selected} onSelect={setSelected} onClose={onClose} actions={actions} />
    </>
  )
}

describe('ConsensusPane', () => {
  it('lists blocks from the most disputed and explains the selected one', async () => {
    setLocale('en')
    render(<Harness />)
    expect(screen.getByTestId('agreement')).toHaveTextContent('Agreement 81%')
    const items = screen.getAllByRole('button', { pressed: false }).filter((b) => b.closest('li'))
    expect(items[0]).toHaveTextContent('missing in OCRia-1')
    await userEvent.click(items[0])
    const detail = screen.getByTestId('cluster-detail')
    expect(detail).toHaveTextContent('only in Argon')
    expect(within(detail).getByTestId('reading-p2')).toHaveTextContent('Fonte')
  })

  it('shows a table as a heatmap and the two readings of a cell with a diff', async () => {
    setLocale('en')
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: /Table/ }))
    const cell = screen.getByTestId('cell-1-1')
    expect(cell.className).toContain('color-sig-wash')
    expect(screen.getByTestId('cell-0-0').className).toContain('color-ok-wash')
    expect(screen.getByText(/Row only in Argon: Lampo · Bari/)).toBeInTheDocument()
    expect(screen.getByTestId('disputed-count')).toHaveTextContent('1 cells to check')
    await userEvent.click(screen.getByRole('button', { name: 'Next cell' }))
    const reading = screen.getByTestId('reading-p2')
    expect(reading.querySelector('ins')).toHaveTextContent('u')
    expect(reading.querySelector('del')).toHaveTextContent('o')
    expect(screen.getByTestId('reading-p1')).toHaveTextContent('Rossi')
  })

  it("switches the canvas to one model's boxes and back", async () => {
    setLocale('en')
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Argon' }))
    expect(screen.getByTestId('view')).toHaveTextContent('p2')
    await userEvent.click(screen.getByRole('button', { name: 'Disagreement' }))
    expect(screen.getByTestId('view')).toHaveTextContent('disagreement')
  })

  it('joins the pieces of a block one model split, and says so', async () => {
    setLocale('en')
    const split: PageConsensus = {
      ...page,
      outputs: { ...page.outputs, p1: [...page.outputs.p1, { label: 'Text', points: [[0, 96], [50, 99]], content: 'capitaneria' }] },
      comparison: {
        ...page.comparison!,
        clusters: [{
          ...page.comparison!.clusters[2], members: { p1: 0, p2: 2 }, parts: { p1: [0, 2], p2: [2] }, split: true, missing: [], score: 0.3,
          labels: { p1: 'Title + Text', p2: 'Note' }, label_agree: false,
        }],
      },
    }
    render(<Harness data={split} />)
    await userEvent.click(screen.getByRole('button', { name: /segmented differently/ }))
    expect(screen.getByText(/Same area, segmented differently/)).toBeInTheDocument()
    expect(screen.getByTestId('reading-p1').textContent).toContain('Arrivi')
    expect(screen.getByTestId('reading-p1').textContent).toContain('capitaneria')
  })

  it('says when there is nothing to compare, and closes', async () => {
    setLocale('en')
    const onClose = vi.fn()
    render(<Harness data={{ run_id: null, models: [], outputs: {}, comparison: null }} onClose={onClose} />)
    expect(screen.getByText('No comparison for this page.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Close the comparison' }))
    expect(onClose).toHaveBeenCalled()
  })

  describe('settling a block', () => {
    const actions = (over: Partial<ConsensusActions> = {}): ConsensusActions => ({
      mine: (cluster) => cluster.id === 0 ? [{ kind: 'block', id: 'c1', label: 'Title', content: 'Arivi', box: [0, 0, 100, 20] }] : [],
      resolved: new Set(),
      canEdit: true,
      onUse: vi.fn(),
      onRemove: vi.fn(),
      onToggleResolved: vi.fn(),
      onNext: vi.fn(),
      onOpenTable: vi.fn(),
      ...over,
    })

    it("compares the models with the user's reading and uses one", async () => {
      setLocale('en')
      const act = actions()
      render(<Harness actions={act} />)
      expect(screen.getByTestId('open-count')).toHaveTextContent('2 blocks to review')
      await userEvent.click(screen.getByRole('button', { name: /^Title/ }))
      expect(screen.getByTestId(`reading-__mine__`)).toHaveTextContent('Arivi')
      expect(screen.getByTestId('reading-p1').querySelector('ins')).toHaveTextContent('r')
      await userEvent.click(screen.getByRole('button', { name: /Use Argon/ }))
      expect(act.onUse).toHaveBeenCalledWith(expect.objectContaining({ id: 0 }), 'p2')
      await userEvent.click(screen.getByRole('button', { name: /Delete mine/ }))
      expect(act.onRemove).toHaveBeenCalled()
      await userEvent.click(screen.getByRole('button', { name: /Fine as it is/ }))
      expect(act.onToggleResolved).toHaveBeenCalled()
    })

    it('offers only the model that found a block the user lacks', async () => {
      setLocale('en')
      render(<Harness actions={actions()} />)
      await userEvent.click(screen.getByRole('button', { name: /missing in OCRia-1/ }))
      const detail = within(screen.getByTestId('cluster-actions'))
      expect(detail.getByRole('button', { name: /Use Argon/ })).toBeInTheDocument()
      expect(detail.queryByRole('button', { name: /Use OCRia-1/ })).toBeNull()
      expect(detail.queryByRole('button', { name: /Delete mine/ })).toBeNull()
      expect(screen.getByText(/using a reading adds the model's block/)).toBeInTheDocument()
    })

    it('sends a table to the table editor instead of replacing it', async () => {
      setLocale('en')
      const act = actions({ mine: () => [{ kind: 'block', id: 't1', label: 'Table', content: '', box: [0, 30, 100, 90] }] })
      render(<Harness actions={act} />)
      await userEvent.click(screen.getByRole('button', { name: /Table/ }))
      const detail = within(screen.getByTestId('cluster-actions'))
      expect(detail.queryByRole('button', { name: /Use/ })).toBeNull()
      await userEvent.click(detail.getByRole('button', { name: 'Open the table' }))
      expect(act.onOpenTable).toHaveBeenCalled()
    })

    it('says when nothing is left to review', () => {
      setLocale('en')
      render(<Harness actions={actions({ resolved: new Set([1, 2]) })} />)
      expect(screen.getByTestId('open-count')).toHaveTextContent('Nothing left to review')
    })
  })
})
