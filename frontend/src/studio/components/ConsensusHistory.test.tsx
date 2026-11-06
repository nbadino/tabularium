// @vitest-environment jsdom
/**
 * The shared history of a comparison: another person's decisions with who and when,
 * a revert that asks first and can carry a reason, comments and replies, and nothing
 * to act on for a reader.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom/vitest'
import { ConsensusHistory } from './ConsensusHistory'
import { setLocale } from '../../i18n'
import type { ConsensusDecision } from '../consensus'

afterEach(cleanup)
beforeEach(() => setLocale('en'))

const base = {
  run_id: 7, page_id: 3, block_id: null, model_key: null, model_name: null, comment: null,
  about_id: null, reverted_by: null, created_at: '2026-11-06T10:15:00Z', revertible: false,
} as const

const decision = (over: Partial<ConsensusDecision>): ConsensusDecision => ({
  ...base, id: 1, cluster_id: null, kind: 'resolved', target_kind: 'resolved',
  before: null, after: null, user: { id: 1, username: 'anna' }, ...over,
})

const items: ConsensusDecision[] = [
  decision({
    id: 4, kind: 'comment', target_kind: 'comment', about_id: 3, comment: 'ok, it was «porto»',
    user: { id: 1, username: 'anna' },
  }),
  decision({
    id: 3, kind: 'revert', target_kind: 'resolved', cluster_id: 0, revertible: true,
    comment: '«porta» is a model typo', user: { id: 2, username: 'bruno' },
    before: { resolved: true }, after: { resolved: false },
  }),
  decision({
    id: 2, kind: 'group_confirm', target_kind: 'group_confirm', block_id: 9, revertible: true,
    after: { cells: [{ r: 1, c: 0, verified: true }, { r: 0, c: 0, verified: true }] },
  }),
  decision({
    id: 1, kind: 'resolved', target_kind: 'resolved', cluster_id: 0, reverted_by: 3,
    comment: 'spelling of the original', before: { resolved: false }, after: { resolved: true },
  }),
  decision({
    id: 0, kind: 'adopt_page', target_kind: 'adopt_page', model_key: 'p1', model_name: 'OCRia',
    user: { id: null, username: null },
  }),
]

describe('ConsensusHistory', () => {
  it('shows who decided what, where, and what was reverted by whom', async () => {
    render(<ConsensusHistory items={items} canEdit onRevert={vi.fn()} onComment={vi.fn()} />)
    await userEvent.click(screen.getByText('Decision history (5)'))
    const rows = screen.getAllByTestId('decision')
    expect(rows).toHaveLength(5)
    expect(rows[1]).toHaveTextContent('Reverted: Fine as it is')
    expect(rows[1]).toHaveTextContent('area 1')
    expect(rows[1]).toHaveTextContent('bruno')
    expect(rows[1]).toHaveTextContent('«porta» is a model typo')
    expect(rows[2]).toHaveTextContent('Group confirmed')
    expect(rows[2]).toHaveTextContent('2 cells')
    expect(rows[3]).toHaveTextContent('reverted by bruno')
    expect(rows[0]).toHaveTextContent('on #3')
    expect(rows[4]).toHaveTextContent("OCRia's reading adopted for the page")
    expect(rows[4]).toHaveTextContent('deleted user')
    expect(rows[4]).toHaveTextContent('It replaced blocks')
    // Only what can still be taken back offers it.
    expect(within(rows[3]).queryByRole('button', { name: 'Revert' })).toBeNull()
    expect(within(rows[4]).queryByRole('button', { name: 'Revert' })).toBeNull()
  })

  it('reverts only after a confirmation, with the reason given', async () => {
    const onRevert = vi.fn()
    render(<ConsensusHistory items={items} canEdit onRevert={onRevert} onComment={vi.fn()} />)
    await userEvent.click(screen.getByText('Decision history (5)'))
    const row = screen.getAllByTestId('decision')[2]
    await userEvent.click(within(row).getByRole('button', { name: 'Revert' }))
    expect(onRevert).not.toHaveBeenCalled()
    const confirm = within(row).getByRole('group', { name: 'Revert' })
    expect(confirm).toHaveTextContent("anna's decision is taken back")
    await userEvent.type(within(confirm).getByLabelText('Why (optional)'), 'wrong column')
    await userEvent.click(within(confirm).getByRole('button', { name: 'Revert' }))
    expect(onRevert).toHaveBeenCalledWith(items[2], 'wrong column')
    expect(within(row).queryByRole('group')).toBeNull()
  })

  it('sends a comment on the page or in reply to one decision', async () => {
    const onComment = vi.fn()
    render(<ConsensusHistory items={items} canEdit onRevert={vi.fn()} onComment={onComment} />)
    await userEvent.click(screen.getByText('Decision history (5)'))
    const box = screen.getByLabelText('Comment')
    await userEvent.type(box, 'checking the totals tomorrow')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onComment).toHaveBeenLastCalledWith('checking the totals tomorrow', null)
    expect(box).toHaveValue('')

    await userEvent.click(within(screen.getAllByTestId('decision')[1]).getByRole('button', { name: 'Reply' }))
    expect(screen.getByText('Reply to bruno')).toBeInTheDocument()
    await userEvent.type(box, 'agreed')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(onComment).toHaveBeenLastCalledWith('agreed', 3)
  })

  it('keeps the text and the confirmation when the server refuses', async () => {
    const onComment = vi.fn().mockRejectedValue(new Error('403'))
    const onRevert = vi.fn().mockRejectedValue(new Error('409'))
    render(<ConsensusHistory items={items} canEdit onRevert={onRevert} onComment={onComment} />)
    await userEvent.click(screen.getByText('Decision history (5)'))
    await userEvent.type(screen.getByLabelText('Comment'), 'not lost')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(screen.getByLabelText('Comment')).toHaveValue('not lost')
    const row = screen.getAllByTestId('decision')[2]
    await userEvent.click(within(row).getByRole('button', { name: 'Revert' }))
    await userEvent.click(within(within(row).getByRole('group', { name: 'Revert' })).getByRole('button', { name: 'Revert' }))
    expect(within(row).getByRole('group', { name: 'Revert' })).toBeInTheDocument()
  })

  it('offers nothing to a reader', async () => {
    render(<ConsensusHistory items={items} canEdit={false} onRevert={vi.fn()} onComment={vi.fn()} />)
    await userEvent.click(screen.getByText('Decision history (5)'))
    expect(screen.queryByLabelText('Comment')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Revert' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Reply' })).toBeNull()
  })
})
