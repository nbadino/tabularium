// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import BenchmarkPage from './BenchmarkPage'

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async (path: string) => {
    if (path === '/projects') return { items: [{ id: 1, name: 'Registro' }] }
    if (path === '/models') return { items: [{ adapter_id: 'monkeyocrv2-parsing', display_name: 'MonkeyOCRv2', local: { runnable: false } }] }
    if (path.endsWith('/benchmark/pages')) return { items: [{ page_id: 1, rel_path: '001.png', status: 'approved', blocks: 2, unconfirmed: 0, ready: true }] }
    if (path.endsWith('/benchmark/status')) return { state: 'done', gold_pages: 1, results: [] }
    if (path.endsWith('/benchmark/latest')) {
      return {
        available: true,
        project_id: 1,
        gold_pages: 1,
        scope: 'gold',
        config: { with_text: true, limit: 10, use_cache: true },
        leaderboard: [],
        targets: [{
          adapter_id: 'monkeyocrv2-parsing',
          model: 'MonkeyOCRv2',
          url: 'http://127.0.0.1:8888/v1',
          reachable: false,
          failed: true,
          error: 'Endpoint non raggiungibile',
          pages_evaluated: 0,
          seconds: 0.1,
          aggregates: { layout: { f1: 0 }, layout_strict: { f1: 0 }, order: { mean_levenshtein_norm: null }, text: { n: 0, mean_cer: null }, tables: { n: 0, structure_ok_pct: null, mean_teds: null } },
        }],
      }
    }
    return {}
  }),
  apiPost: vi.fn(),
}))

afterEach(() => cleanup())

describe('BenchmarkPage', () => {
  it('renders unreachable targets with "—" rank and explicit error badge instead of ranking #1', async () => {
    render(<MemoryRouter><BenchmarkPage /></MemoryRouter>)
    expect(await screen.findByText('Non raggiungibile')).toBeInTheDocument()
    expect(screen.getByText('Endpoint non raggiungibile')).toBeInTheDocument()
    const row = screen.getByText('Non raggiungibile').closest('tr')!
    expect(row.querySelector('td')?.textContent).toBe('—')
  })
})
