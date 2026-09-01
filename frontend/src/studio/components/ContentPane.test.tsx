// @vitest-environment jsdom
/**
 * Test del pannello contenuto: **l'unica zona del rail destro**. Lista di
 * righe editabili (una per blocco) con ritaglio + editor affiancati, ordine
 * di lettura governato dalle righe stesse, output del modello in diretta
 * sopra la lista. Le tabelle caricano il foglio dal server.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('l’ordine di lettura si governa dalla riga: niente secondo elenco', async () => {
    const user = userEvent.setup()
    const onMove = vi.fn()
    render(
      <ContentPane
        blocks={[
          makeBlock({ id: 'b1', serverId: 11, orderIdx: 0, label: 'Title' }),
          makeBlock({ id: 'b2', serverId: 12, orderIdx: 1, label: 'Text' }),
        ]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        onMove={onMove}
        {...noop}
      />,
    )
    // Una sola regione: il contenuto. Livelli e regole non sono più moduli.
    expect(screen.getAllByRole('region')).toHaveLength(1)
    expect(screen.queryByRole('region', { name: 'Livelli' })).toBeNull()

    await user.click(screen.getByLabelText('Sposta Text prima nell’ordine di lettura'))
    expect(onMove).toHaveBeenCalledWith('b2', -1)
  })

  it('Alt+freccia sulla riga riordina, Canc elimina: la lista resta l’equivalente del canvas', async () => {
    const user = userEvent.setup()
    const onMove = vi.fn()
    const onDelete = vi.fn()
    render(
      <ContentPane
        blocks={[makeBlock({ id: 'b1', orderIdx: 0, label: 'Title' })]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        onMove={onMove}
        {...noop}
        onDelete={onDelete}
      />,
    )
    const handle = screen.getByRole('button', { name: 'Blocco 1: Title' })
    handle.focus()
    await user.keyboard('{Alt>}{ArrowDown}{/Alt}')
    expect(onMove).toHaveBeenCalledWith('b1', 1)
    await user.keyboard('{Delete}')
    expect(onDelete).toHaveBeenCalledWith('b1')
  })

  it('le regole di trascrizione stanno dietro il loro pulsante, non nel rail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ conventions: [{ id: 'soft_hyphen', label: 'x', checked: false }] }),
      }),
    )
    const user = userEvent.setup()
    render(
      <ContentPane
        blocks={[makeBlock({})]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        projectId={3}
        {...noop}
      />,
    )
    expect(screen.queryByText(/Ricomporre le parole spezzate/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Regole' }))
    expect(await screen.findByRole('dialog', { name: /Regole di trascrizione/ })).toBeTruthy()
    expect(await screen.findByText(/Ricomporre le parole spezzate/)).toBeTruthy()
  })

  it('mentre il modello scrive: stato vivo, testo in diretta e cursore', () => {
    render(
      <ContentPane
        blocks={[]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={{
          engine: 'MonkeyOCRv2',
          startedAt: Date.now(),
          blocks: 2,
          last: 'Table',
          output: { phase: 'end2end', text: 'Prima riga della pagina' },
        }}
        {...noop}
      />,
    )
    const stream = screen.getByRole('region', { name: 'Output del modello' })
    expect(stream).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByText('Prima riga della pagina')).toBeTruthy()
    expect(screen.getByText(/2 blocchi proposti/)).toBeTruthy()
  })

  it('la tabella Markdown usa l’intestazione dichiarata, non la prima riga a caso', () => {
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
          output: { phase: 'table', text: '| Vessel | Tons |\n|---|---|\n| Aagtekerk | 1.240 |' },
        }}
        {...noop}
      />,
    )
    expect(screen.getByRole('columnheader', { name: 'Vessel' })).toBeTruthy()
    // La colonna numerica si incolonna a destra: è una superficie di misura.
    expect(screen.getByText('1.240')).toHaveAttribute('data-num', 'true')
  })

  it('OTSL: nessuna intestazione indovinata — la prima riga è già un dato', () => {
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
          output: { phase: 'table', text: '<fcel>A. C. Bedford<fcel>Am<nl><fcel>Aagtekerk<fcel>Du' },
        }}
        {...noop}
      />,
    )
    expect(screen.queryAllByRole('columnheader')).toHaveLength(0)
    expect(screen.getByText('A. C. Bedford')).toBeTruthy()
  })
})
