// @vitest-environment jsdom
/**
 * The comparison inside the page content: the summary at the head, the verdict of an
 * area in words, the readings diffed against the user's text, what each action will
 * do, and the areas only the models read.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ClusterChip, ClusterDetail, ConsensusOffer, ConsensusSummary, ModelOnlyClusters, type ConsensusActions } from './ConsensusPane'
import { setLocale } from '../../i18n'
import type { MineItem, PageConsensus } from '../consensus'

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
    conventions: {},
    clusters: [
      { id: 0, members: { p1: 0, p2: 0 }, parts: { p1: [0], p2: [0] }, split: false, label: 'Title', labels: { p1: 'Title', p2: 'Title' }, bbox: [0, 0, 100, 20], text: 0, text_raw: 0, text_kind: 'same', conventions: [], layout: 0, label_agree: true, missing: [], score: 0, table: null },
      {
        id: 1, members: { p1: 1, p2: 1 }, parts: { p1: [1], p2: [1] }, split: false, label: 'Table', labels: { p1: 'Table', p2: 'Table' }, bbox: [0, 30, 100, 90], text: 0.1, text_raw: 0.1, text_kind: 'text', conventions: [], layout: 0, label_agree: true, missing: [], score: 0.1,
        table: {
          reference: 'p1', rows: 2, cols: 2, score: 0.1, raw_score: 0.1,
          kinds: { same: 3, convention: 0, missing: 0, text: 1, digits: 0 }, conventions: {},
          cells: [
            { r: 0, c: 0, rowspan: 1, colspan: 1, values: { p1: 'Nave', p2: 'Nave' }, score: 0, raw_score: 0, kind: 'same', conventions: [] },
            { r: 0, c: 1, rowspan: 1, colspan: 1, values: { p1: 'Capitano', p2: 'Capitano' }, score: 0, raw_score: 0, kind: 'same', conventions: [] },
            { r: 1, c: 0, rowspan: 1, colspan: 1, values: { p1: 'Aurora', p2: 'Aurora' }, score: 0, raw_score: 0, kind: 'same', conventions: [] },
            { r: 1, c: 1, rowspan: 1, colspan: 1, values: { p1: 'Rossi', p2: 'Russo' }, score: 0.4, raw_score: 0.4, kind: 'text', conventions: [] },
          ],
          extra_rows: [{ model: 'p2', index: 2, texts: ['Lampo', 'Bari'] }],
          extra_cols: [],
        },
      },
      { id: 2, members: { p1: null, p2: 2 }, parts: { p2: [2] }, split: false, label: 'Note', labels: { p2: 'Note' }, bbox: [0, 95, 50, 99], text: 0, text_raw: 0, text_kind: 'same', conventions: [], layout: 0, label_agree: true, missing: ['p1'], score: 1, table: null },
    ],
  },
}

const actions = (over: Partial<ConsensusActions> = {}): ConsensusActions => ({
  mine: () => [],
  resolved: new Set(),
  canEdit: true,
  onUse: vi.fn(),
  onRemove: vi.fn(),
  onToggleResolved: vi.fn(),
  onNext: vi.fn(),
  onAdopt: vi.fn(),
  ...over,
})

const clusterOf = (data: PageConsensus, id: number) => data.comparison!.clusters.find((c) => c.id === id)!
const mineTitle: MineItem[] = [{ kind: 'block', id: 'c1', label: 'Title', content: 'Arivi', box: [0, 0, 100, 20] }]

describe('ConsensusSummary', () => {
  it('counts what is left by kind and offers the next one', async () => {
    setLocale('en')
    const act = actions()
    render(<ConsensusSummary page={page} loading={false} view="disagreement" onView={() => {}} onClose={() => {}} actions={act} />)
    const counts = screen.getByTestId('open-count')
    expect(counts).toHaveTextContent('1 found by one model only')
    expect(counts).toHaveTextContent('1 to correct')
    await userEvent.click(screen.getByRole('button', { name: /Next/ }))
    expect(act.onNext).toHaveBeenCalled()
  })

  it('keeps the views and the base behind more options', async () => {
    setLocale('en')
    const act = actions()
    const onView = vi.fn()
    render(<ConsensusSummary page={page} loading={false} view="disagreement" onView={onView} onClose={() => {}} actions={act} />)
    await userEvent.click(screen.getByText(/More options · overall agreement 81%/))
    await userEvent.click(within(screen.getByTestId('adopt')).getByRole('button', { name: 'Argon' }))
    expect(act.onAdopt).toHaveBeenCalledWith('p2')
    await userEvent.click(screen.getByRole('button', { name: 'Argon', pressed: false }))
    expect(onView).toHaveBeenCalledWith('p2')
  })

  it('says when there is nothing to compare, and closes', async () => {
    setLocale('en')
    const onClose = vi.fn()
    render(<ConsensusSummary page={{ run_id: null, models: [], outputs: {}, comparison: null }} loading={false} view="disagreement" onView={() => {}} onClose={onClose} />)
    expect(screen.getByText('No comparison for this page.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Close the comparison' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('says when nothing is left to review', () => {
    setLocale('en')
    render(<ConsensusSummary page={page} loading={false} view="disagreement" onView={() => {}} onClose={() => {}} actions={actions({ resolved: new Set([1, 2]) })} />)
    expect(screen.getByTestId('open-count')).toHaveTextContent('Nothing left to review')
  })
})

describe('ConsensusSummary while a run is reading the page', () => {
  it('says which model is reading, which is queued, and that the comparison will appear', () => {
    setLocale('it')
    const waiting: PageConsensus = {
      run_id: 5,
      models: [
        { ...page.models[0], state: 'finished' },
        { ...page.models[1], state: 'running' },
      ],
      outputs: {},
      comparison: null,
    }
    render(<ConsensusSummary page={waiting} loading={false} view="disagreement" onView={() => {}} onClose={() => {}} />)
    const box = screen.getByTestId('consensus-waiting')
    expect(box).toHaveTextContent('OCRia-1: letta')
    expect(box).toHaveTextContent('Argon: sta leggendo questa pagina…')
    expect(box).toHaveTextContent('Il confronto compare da solo')
    expect(screen.queryByText(/Servono almeno due modelli/)).toBeNull()
  })
})

describe('ClusterDetail', () => {
  it('says the text is the same when only the class differs, and in grey', () => {
    setLocale('en')
    const relabelled: PageConsensus = {
      ...page,
      outputs: { ...page.outputs, p2: [{ ...page.outputs.p2[0], label: 'Issue-number' }, ...page.outputs.p2.slice(1)] },
      comparison: { ...page.comparison!, clusters: [{ ...clusterOf(page, 0), labels: { p1: 'Text', p2: 'Issue-number' }, label_agree: false, score: 0.5 }] },
    }
    const cluster = clusterOf(relabelled, 0)
    render(<ClusterChip cluster={cluster} page={relabelled} resolved={false} selected={false} onSelect={() => {}} />)
    expect(screen.getByTestId('chip-0')).toHaveTextContent('class')
    render(<ClusterDetail cluster={cluster} page={relabelled} mine={mineTitle} resolved={false} actions={actions()} />)
    const detail = screen.getByTestId('cluster-detail')
    expect(detail).toHaveTextContent('A structure choice: same text')
    expect(detail).toHaveTextContent('Text: identical')
    expect(detail).toHaveTextContent('Class: OCRia-1: Title · Argon: Issue number')
  })

  it("diffs the models against the user's text and says what Use will do", async () => {
    setLocale('en')
    const act = actions()
    render(<ClusterDetail cluster={clusterOf(page, 0)} page={page} mine={mineTitle} resolved={false} actions={act} />)
    expect(screen.getByTestId('cluster-detail')).toHaveTextContent('Your text differs from the models')
    expect(screen.getByTestId('reading-p1').querySelector('ins')).toHaveTextContent('r')
    expect(screen.getByTestId('use-preview')).toHaveTextContent('Use Argon → text and class (Title) into your block')
    await userEvent.click(screen.getByRole('button', { name: /Use Argon/ }))
    expect(act.onUse).toHaveBeenCalledWith(expect.objectContaining({ id: 0 }), 'p2')
    await userEvent.click(screen.getByRole('button', { name: /Delete mine/ }))
    expect(act.onRemove).toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /Fine as it is/ }))
    expect(act.onToggleResolved).toHaveBeenCalled()
  })

  it('names the segmentation of each model for a split area', () => {
    setLocale('en')
    const split: PageConsensus = {
      ...page,
      outputs: { ...page.outputs, p1: [...page.outputs.p1, { label: 'Text', points: [[0, 96], [50, 99]], content: 'capitaneria' }] },
      comparison: {
        ...page.comparison!,
        clusters: [{ ...clusterOf(page, 2), members: { p1: 0, p2: 2 }, parts: { p1: [0, 2], p2: [2] }, split: true, missing: [], score: 0.3, labels: { p1: 'Title + Text', p2: 'Note' }, label_agree: false }],
      },
    }
    render(<ClusterDetail cluster={clusterOf(split, 2)} page={split} mine={[]} resolved={false} actions={actions()} />)
    const detail = screen.getByTestId('cluster-detail')
    expect(detail).toHaveTextContent('Segmentation: OCRia-1: 2 blocks (Title + Text) · Argon: 1 block (Note)')
    expect(screen.getByTestId('use-preview')).toHaveTextContent('Use OCRia-1 → adds 2 blocks: Title, Text')
  })

  it('sends a table to its editor and counts the cells that differ', () => {
    setLocale('en')
    const mine: MineItem[] = [{ kind: 'block', id: 't1', label: 'Table', content: '', box: [0, 30, 100, 90] }]
    render(<ClusterDetail cluster={clusterOf(page, 1)} page={page} mine={mine} resolved={false} actions={actions()} />)
    const detail = screen.getByTestId('cluster-detail')
    expect(detail).toHaveTextContent('1 of 4 cells differ')
    expect(detail).toHaveTextContent('1 rows in one model only')
    expect(within(screen.getByTestId('cluster-actions')).queryByRole('button', { name: /Use/ })).toBeNull()
  })
})

describe('ModelOnlyClusters', () => {
  it("lists what only the models read, and opens one with the model's table as a heatmap", async () => {
    setLocale('en')
    const onSelect = vi.fn()
    const { rerender } = render(<ModelOnlyClusters page={page} clusters={[clusterOf(page, 1), clusterOf(page, 2)]} selected={null} onSelect={onSelect} actions={actions()} />)
    expect(screen.getByTestId('chip-2')).toHaveTextContent('only Argon')
    await userEvent.click(screen.getByTestId('chip-1'))
    expect(onSelect).toHaveBeenCalledWith(1)
    rerender(<ModelOnlyClusters page={page} clusters={[clusterOf(page, 1), clusterOf(page, 2)]} selected={1} onSelect={onSelect} actions={actions()} />)
    expect(screen.getByTestId('cell-1-1').className).toContain('color-sig-wash')
    await userEvent.click(screen.getByRole('button', { name: 'Next cell' }))
    expect(screen.getByTestId('reading-p2').querySelector('ins')).toHaveTextContent('u')
  })

  // Lloyd's p. 13: the table is only in the models; the heatmap only shows it.
  it("offers to add a model's table as a draft, to open it in the table editor", async () => {
    setLocale('en')
    const onAddBlock = vi.fn()
    render(<ModelOnlyClusters page={page} clusters={[clusterOf(page, 1)]} selected={1} onSelect={() => {}} actions={actions({ onAddBlock })} />)
    const add = screen.getByTestId('add-table')
    expect(add).toHaveTextContent('Only the models have this table')
    await userEvent.click(within(add).getByRole('button', { name: "Add Argon's table" }))
    expect(onAddBlock).toHaveBeenCalledWith(clusterOf(page, 1), 'p2')
    // A reader cannot add anything.
    cleanup()
    render(<ModelOnlyClusters page={page} clusters={[clusterOf(page, 1)]} selected={1} onSelect={() => {}} actions={actions({ onAddBlock, canEdit: false })} />)
    expect(screen.queryByTestId('add-table')).toBeNull()
  })
})

describe('Conventions', () => {
  it('counts a table\'s cells by kind, names the convention rules, and does not count them as disputed', async () => {
    setLocale('en')
    const table = clusterOf(page, 1).table!
    const lloyds: PageConsensus = {
      ...page,
      comparison: {
        ...page.comparison!,
        conventions: { dash: 1, leaders: 1 },
        clusters: [{
          ...clusterOf(page, 2),
          members: { p1: 1, p2: 1 },
          parts: { p1: [1], p2: [1] },
          missing: [],
          text_kind: 'digits',
          conventions: ['dash', 'leaders'],
          table: {
            ...table,
            cells: [
              { r: 0, c: 0, rowspan: 1, colspan: 1, values: { p1: 'STEAMER—Owner', p2: 'STEAMER-Owner' }, score: 0, raw_score: 0.08, kind: 'convention', conventions: ['dash'] },
              { r: 0, c: 1, rowspan: 1, colspan: 1, values: { p1: 'Year', p2: 'Year' }, score: 0, raw_score: 0, kind: 'same', conventions: [] },
              { r: 1, c: 0, rowspan: 1, colspan: 1, values: { p1: 'Admiralen Iron', p2: 'Admiralen ... Iron' }, score: 0, raw_score: 0.3, kind: 'convention', conventions: ['leaders'] },
              { r: 1, c: 1, rowspan: 1, colspan: 1, values: { p1: '18', p2: '' }, score: 1, raw_score: 1, kind: 'digits', conventions: [] },
            ],
            extra_rows: [],
            kinds: { same: 1, convention: 2, missing: 0, text: 0, digits: 1 },
            conventions: { dash: 1, leaders: 1 },
          },
        }],
      },
    }
    render(<ModelOnlyClusters page={lloyds} clusters={lloyds.comparison!.clusters} selected={2} onSelect={() => {}} />)
    expect(screen.getByTestId('chip-2')).toHaveTextContent('1 cells differ')
    expect(screen.getByTestId('disputed-count')).toHaveTextContent('1 cells to check')
    const kinds = screen.getByTestId('cell-kinds')
    expect(kinds).toHaveTextContent('1 different number')
    expect(kinds).toHaveTextContent('2 cells differ by convention only: dash 1 · dot leaders 1')
    await userEvent.click(screen.getByTestId('cell-0-0'))
    expect(screen.getByTestId('cell-kind')).toHaveTextContent('convention: dash')
    render(<ConsensusSummary page={lloyds} loading={false} view="disagreement" onView={() => {}} onClose={() => {}} />)
    expect(screen.getByTestId('page-conventions')).toHaveTextContent('Convention-only differences on this page: dash 1 · dot leaders 1')
  })
})

describe('ConsensusOffer', () => {
  it('says the page has a comparison, with which models and what is left, and opens it', async () => {
    setLocale('en')
    const onOpen = vi.fn()
    render(<ConsensusOffer page={page} resolved={new Set()} onOpen={onOpen} />)
    const offer = screen.getByTestId('consensus-offer')
    expect(offer).toHaveTextContent('Comparison available: OCRia-1 · Argon.')
    expect(offer).toHaveTextContent('1 to correct · 1 found by one model only')
    await userEvent.click(screen.getByRole('button', { name: 'Show the comparison' }))
    expect(onOpen).toHaveBeenCalled()
  })
})
