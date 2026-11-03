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
import ConsensusPane from './ConsensusPane'
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
      { id: 0, members: { p1: 0, p2: 0 }, label: 'Title', labels: { p1: 'Title', p2: 'Title' }, bbox: [0, 0, 100, 20], text: 0, layout: 0, label_agree: true, missing: [], score: 0, table: null },
      {
        id: 1, members: { p1: 1, p2: 1 }, label: 'Table', labels: { p1: 'Table', p2: 'Table' }, bbox: [0, 30, 100, 90], text: 0.1, layout: 0, label_agree: true, missing: [], score: 0.1,
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
      { id: 2, members: { p1: null, p2: 2 }, label: 'Note', labels: { p2: 'Note' }, bbox: [0, 95, 50, 99], text: 0, layout: 0, label_agree: true, missing: ['p1'], score: 1, table: null },
    ],
  },
}

function Harness({ data = page, onClose = () => {} }: { data?: PageConsensus | null; onClose?: () => void }) {
  const [view, setView] = useState<ConsensusView>('disagreement')
  const [selected, setSelected] = useState<number | null>(null)
  return (
    <>
      <span data-testid="view">{view}</span>
      <ConsensusPane page={data} loading={false} view={view} onView={setView} selected={selected} onSelect={setSelected} onClose={onClose} />
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

  it('says when there is nothing to compare, and closes', async () => {
    setLocale('en')
    const onClose = vi.fn()
    render(<Harness data={{ run_id: null, models: [], outputs: {}, comparison: null }} onClose={onClose} />)
    expect(screen.getByText('No comparison for this page.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Close the comparison' }))
    expect(onClose).toHaveBeenCalled()
  })
})
