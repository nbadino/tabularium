// @vitest-environment jsdom
/**
 * Test del pannello contenuto: lista di righe editabili (una per blocco),
 * ognuna con ritaglio + editor affiancati; le tabelle caricano il foglio
 * dal server.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import ContentPane from './ContentPane'
import { emptyGrid } from '../../lib/grid'
import type { DisplayBlock } from '../types'

afterEach(() => cleanup())

function makeBlock(patch: Partial<DisplayBlock>): DisplayBlock {
  return {
    id: 'b1',
    serverId: 11,
    label: 'Text',
    kind: 'rect',
    points: [
      { x: 0, y: 0 },
      { x: 10, y: 10 },
    ],
    content: '',
    orderIdx: 1,
    confirmed: false,
    prefill: null,
    ...patch,
  }
}

const noop = {
  onSelect: vi.fn(),
  onContent: vi.fn(),
  onLabel: vi.fn(),
  onConfirmed: vi.fn(),
  onDelete: vi.fn(),
  onSaveTable: vi.fn(),
  onDraftContent: vi.fn(),
  onDraftGrid: vi.fn(),
  onSaveDraftGrid: vi.fn(),
  onDraftConfirmed: vi.fn(),
  onDraftReject: vi.fn(),
}

describe('ContentPane', () => {
  it('pagina senza blocchi: stato vuoto nominato', () => {
    render(<ContentPane blocks={[]} drafts={[]} labels={[]} selectedId={null} working={null} {...noop} />)
    expect(screen.getByText(/Nessun blocco/)).toBeTruthy()
  })

  it('le classi strutturali dichiarano che non portano testo', () => {
    render(
      <ContentPane
        blocks={[makeBlock({ label: 'Picture' })]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        {...noop}
      />,
    )
    expect(screen.getByText(/classe è strutturale/)).toBeTruthy()
    expect(screen.queryByLabelText(/Bozza/)).toBeNull()
  })

  it('blocco di testo: la riga mostra l’editor con il contenuto', () => {
    const onContent = vi.fn()
    render(
      <ContentPane
        blocks={[makeBlock({ content: 'Aagtekerk .. (Vereenigde)' })]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        {...noop}
        onContent={onContent}
      />,
    )
    const editor = screen.getByLabelText(/Trascrizione del blocco/) as HTMLTextAreaElement
    expect(editor.value).toBe('Aagtekerk .. (Vereenigde)')
  })

  it('blocco Table: carica la griglia dal server e monta il foglio', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ grid: emptyGrid(2, 2), otsl: '' }),
      }),
    )
    render(
      <ContentPane
        blocks={[makeBlock({ label: 'Table' })]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        {...noop}
      />,
    )
    await waitFor(() => expect(screen.getByLabelText('Cella riga 1, colonna 1')).toBeTruthy())
    expect(screen.getByAltText(/Ritaglio del blocco Table/)).toBeTruthy()
  })

  it('blocco non ancora salvato: il ritaglio lo dice, non scompare', () => {
    render(
      <ContentPane
        blocks={[makeBlock({ serverId: null })]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        {...noop}
      />,
    )
    expect(screen.getByText(/Salva il blocco per vedere il ritaglio/)).toBeTruthy()
  })

  it('renderizza una tabella Markdown anche mentre lo stream è incompleto', () => {
    render(
      <ContentPane
        blocks={[]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={{
          engine: 'Unlimited-OCR',
          startedAt: Date.now(),
          blocks: 0,
          last: null,
          output: { phase: 'end2end', text: '| Vessel | Port |\n|---|---|\n| Aagtekerk | Calcutta |' },
        }}
        {...noop}
      />,
    )
    expect(screen.getByText('Aagtekerk')).toBeTruthy()
    expect(screen.getByText('Calcutta')).toBeTruthy()
  })

  it('renderizza celle HTML prima della chiusura della tabella', () => {
    render(
      <ContentPane
        blocks={[]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={{
          engine: 'Unlimited-OCR',
          startedAt: Date.now(),
          blocks: 0,
          last: null,
          output: { phase: 'end2end', text: '<table><tr><td>Vessel</td><td>Calcutta' },
        }}
        {...noop}
      />,
    )
    expect(screen.getByText('Vessel')).toBeTruthy()
    expect(screen.getByText('Calcutta')).toBeTruthy()
  })

  it('renderizza OTSL nativo anche mentre l’ultima riga è incompleta', () => {
    render(
      <ContentPane
        blocks={[]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={{
          engine: 'MinerU2.5',
          startedAt: Date.now(),
          blocks: 0,
          last: null,
          output: {
            phase: 'table',
            text: '<fcel>VESSEL—Owner<fcel>Flg<fcel>Reg<nl><fcel>A. C. Bedford<fcel>Am<fcel>*R<nl><fcel>Incomplete',
          },
        }}
        {...noop}
      />,
    )
    expect(screen.getByText('VESSEL—Owner')).toBeTruthy()
    expect(screen.getByText('A. C. Bedford')).toBeTruthy()
    expect(screen.getByText('Incomplete')).toBeTruthy()
  })
})
