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

// Univer disegna su canvas: in jsdom non può montare. Qui interessa il nostro
// cablaggio (scheda → vista di lavoro → confini → salvataggio); che il foglio
// si veda davvero lo prova il browser, non questo test.
vi.mock('./UniverSheet', () => ({
  default: ({
    suspects,
    onSelectionChange,
    disagreements,
    cellHint,
    cellChoices,
    grid,
  }: {
    grid?: { cells: { r: number; c: number; verified?: boolean }[] }
    suspects?: [number, number][]
    onSelectionChange?: (s: { startRow: number; startColumn: number; endRow: number; endColumn: number }) => void
    disagreements?: { partly: [number, number][]; disagree: [number, number][] }
    cellHint?: (row: number, column: number) => React.ReactNode
    cellChoices?: (row: number, column: number) => { label: string; value: string }[]
  }) => (
    <div
      data-testid="univer-sheet"
      data-suspects={JSON.stringify(suspects ?? [])}
      data-disagreements={JSON.stringify(disagreements ?? null)}
      data-verified={JSON.stringify((grid?.cells ?? []).filter((cell) => cell.verified).map((cell) => [cell.r, cell.c]))}
    >
      <div data-testid="hint-1-1">{cellHint?.(1, 1)}</div>
      <ul data-testid="choices-1-1">{(cellChoices?.(1, 1) ?? []).map((choice) => <li key={choice.label}>{choice.label}</li>)}</ul>
      <button type="button" onClick={() => onSelectionChange?.({ startRow: 2, startColumn: 3, endRow: 2, endColumn: 3 })}>
        seleziona D3
      </button>
    </div>
  ),
}))

import ContentPane from './ContentPane'
import { emptyGrid } from '../../lib/grid'
import type { DisplayBlock, PrefillDraft } from '../types'
import type { LabelDef } from '../../lib/types'

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
  onDraftLabel: vi.fn(),
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

  it('blocco Table: la riga è una scheda, il foglio si apre nella vista di lavoro', async () => {
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
        pageId={1}
        pageImageUrl="/api/pages/1/preview"
        {...noop}
      />,
    )
    // Nel rail non c'è nessun foglio: una tabella non ci sta (520 px di rail).
    expect(screen.queryByTestId('univer-sheet')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Apri la tabella' }))

    // La vista di lavoro monta la superficie e, accanto, i confini.
    await waitFor(() => expect(screen.getByTestId('univer-sheet')).toBeTruthy())
    // Le dimensioni sono contesto di struttura, non un'altra riga di stato.
    await userEvent.click(screen.getByText('Struttura'))
    expect(screen.getByText(/\d+ righe × \d+ colonne/)).toBeTruthy()
    expect(screen.getByAltText(/Scansione della pagina/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Salvato' })).toBeTruthy()
  })

  it('vista di lavoro: le somme che non tornano arrivano al foglio e lo dice', async () => {
    const checks = {
      checks: [
        { kind: 'row', cells: [[1, 1], [1, 2]], total: [1, 3], ok: true },
        { kind: 'row', cells: [[2, 1], [2, 2]], total: [2, 3], ok: false },
      ],
      passed: 1,
      failed: 1,
      suspects: [[2, 3]],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(String(url).includes('/tables/checks') ? checks : { grid: emptyGrid(3, 4) }),
        }),
      ),
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
    await userEvent.click(screen.getByRole('button', { name: 'Apri la tabella' }))
    await waitFor(() => expect(screen.getByTitle(/Somme: 1 su 2 tornano · 1 no/)).toHaveTextContent(/Σ\s*1\/2/))
    expect(screen.getByTestId('univer-sheet')).toHaveAttribute('data-suspects', '[[2,3]]')
    // Selezionata la cella, la riga dice quale somma non torna.
    await userEvent.click(screen.getByRole('button', { name: 'seleziona D3' }))
    expect(screen.getByText('Summation Error in column (4) ≠ column (2) + (3)')).toBeTruthy()
  })

  // Lloyd's Daily Index, 1 April 1920: Gemini drops the year where Reg is blank.
  it('confronto modelli: conta per tipo, va alla cella successiva (numeri prima) e conferma la mia lettura', async () => {
    const grid = emptyGrid(4, 4)
    const mine: Record<string, string> = { '0:0': 'STEAMER—Owner', '0:3': 'Year', '1:1': 'Genva', '2:3': '18', '3:3': '06' }
    grid.cells = grid.cells.map((cell) => ({ ...cell, text: mine[`${cell.r}:${cell.c}`] ?? cell.text, source: 'ocr' as const }))
    const consensus = {
      run_id: 2,
      models: [{ key: 'p3', name: 'OCRia-1' }, { key: 'p1', name: 'Gemini' }],
      cells: [
        { r: 0, c: 0, values: { p3: 'STEAMER—Owner', p1: 'STEAMER-Owner' }, score: 0, kind: 'convention', conventions: ['dash'] },
        { r: 1, c: 1, values: { p3: 'Genova', p1: '' }, score: 1, kind: 'missing', conventions: [] },
        { r: 2, c: 3, values: { p3: '18', p1: '' }, score: 1, kind: 'digits', conventions: [] },
        { r: 3, c: 3, values: { p3: '06', p1: '' }, score: 1, kind: 'digits', conventions: [] },
      ],
      extra_rows: [],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(
            String(url).includes('/consensus') ? consensus
              : String(url).includes('/tables/checks') ? { checks: [], passed: 0, failed: 0, suspects: [] }
                : { grid },
          ),
        }),
      ),
    )
    render(<ContentPane blocks={[makeBlock({ label: 'Table' })]} drafts={[]} labels={[]} selectedId={null} working={null} {...noop} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apri la tabella' }))
    const bar = await screen.findByTestId('consensus-bar')
    await waitFor(() => expect(bar).toHaveTextContent('3 celle in disaccordo'))
    expect(bar).toHaveTextContent('2 numeri diversi')
    expect(bar).toHaveTextContent('1 letta da un modello solo')
    expect(bar).toHaveTextContent('1 cella diversa solo per convenzione')
    // The convention is not painted; a reading missing is red like a number.
    expect(screen.getByTestId('univer-sheet')).toHaveAttribute('data-disagreements', JSON.stringify({ partly: [], disagree: [[2, 3], [3, 3], [1, 1]], resolved: [] }))
    // The same slip twice in one column: offered as a group.
    expect(screen.getByTestId('confirm-groups')).toHaveTextContent('Colonna D (Year): 2 numeri che leggi come OCRia-1 e che Gemini lascia vuoti.')
    // Hover says what kind of difference; emptying the cell is said as such, and offered last.
    expect(screen.getByTestId('hint-1-1')).toHaveTextContent('lettura mancante')
    expect(screen.getByTestId('hint-1-1')).toHaveTextContent('Gemini: (vuota)')
    expect(screen.getByTestId('choices-1-1').textContent).toBe('Usa OCRia-1: «Genova»Svuota la cella (Gemini non legge niente)')
    // Next goes to the numbers first; confirming marks the cell verified and moves on.
    await userEvent.click(screen.getByRole('button', { name: /Cella successiva/ }))
    await userEvent.click(screen.getByRole('button', { name: /La mia lettura è giusta/ }))
    await waitFor(() => expect(screen.getByTestId('univer-sheet')).toHaveAttribute('data-verified', '[[2,3]]'))
    await waitFor(() => expect(bar).toHaveTextContent('2 celle in disaccordo'))
    await userEvent.click(screen.getByRole('button', { name: /La mia lettura è giusta/ }))
    await waitFor(() => expect(screen.getByTestId('univer-sheet')).toHaveAttribute('data-verified', '[[2,3],[3,3]]'))
    expect(bar).toHaveTextContent('2 corrette')
  })

  it('confronto modelli: le celle in disaccordo si colorano nel foglio e il passaggio del mouse mostra le letture', async () => {
    const grid = emptyGrid(3, 4)
    grid.cells = grid.cells.map((cell) =>
      cell.r === 1 && cell.c === 1 ? { ...cell, text: 'Genva', source: 'ocr' as const }
        // Already corrected by hand: resolved, painted violet.
        : cell.r === 2 && cell.c === 2 ? { ...cell, text: '1523', source: 'manual' as const }
          : { ...cell, source: 'ocr' as const })
    const consensus = {
      run_id: 2,
      models: [{ key: 'p3', name: 'OCRia-1' }, { key: 'p1', name: 'Argon' }],
      cells: [
        { r: 0, c: 0, values: { p3: 'Nave', p1: 'Nave' }, score: 0, kind: 'same', conventions: [] },
        { r: 1, c: 1, values: { p3: 'Genova', p1: 'Genovo' }, score: 0.17, kind: 'text', conventions: [] },
        { r: 2, c: 2, values: { p3: '1523', p1: null }, score: 1, kind: 'digits', conventions: [] },
      ],
      extra_rows: [],
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: () => Promise.resolve(
            String(url).includes('/consensus') ? consensus
              : String(url).includes('/tables/checks') ? { checks: [], passed: 0, failed: 0, suspects: [] }
                : { grid },
          ),
        }),
      ),
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
    await userEvent.click(screen.getByRole('button', { name: 'Apri la tabella' }))
    await waitFor(() => expect(screen.getByTestId('consensus-bar')).toHaveTextContent('1 cella in disaccordo'))
    expect(screen.getByTestId('consensus-bar')).toHaveTextContent('1 corretta')
    expect(screen.getByTestId('univer-sheet')).toHaveAttribute('data-disagreements', JSON.stringify({ partly: [[1, 1]], disagree: [], resolved: [[2, 2]] }))
    // Right click on a disputed cell offers each model's reading.
    expect(screen.getByTestId('choices-1-1')).toHaveTextContent('Usa OCRia-1: «Genova»')
    expect(screen.getByTestId('choices-1-1')).toHaveTextContent('Usa Argon: «Genovo»')
    const hint = screen.getByTestId('hint-1-1')
    expect(hint).toHaveTextContent('La tua trascrizione: Genva')
    expect(hint).toHaveTextContent('OCRia-1: Genova')
    expect(hint).toHaveTextContent('Argon: Genovo')
    // Turned off, the sheet is plain again.
    await userEvent.click(screen.getByLabelText('Mostra disaccordi'))
    expect(screen.getByTestId('univer-sheet')).toHaveAttribute('data-disagreements', 'null')
  })

  it('una somma sbagliata dalla stampa si conferma come refuso e smette di contare', async () => {
    const checks = {
      checks: [{ kind: 'row', cells: [[2, 1], [2, 2]], total: [2, 3], ok: false, key: 'row:79,492=17,411+62,031', misprint: false }],
      passed: 0,
      failed: 1,
      suspects: [[2, 3]],
    }
    const fetchMock = vi.fn().mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(String(url).includes('/tables/checks') ? checks : { grid: emptyGrid(3, 4) }),
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
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
    await userEvent.click(screen.getByRole('button', { name: 'Apri la tabella' }))
    await waitFor(() => expect(screen.getByTitle(/Somme: 0 su 1 tornano · 1 no/)).toHaveTextContent(/Σ\s*0\/1/))
    await userEvent.click(screen.getByRole('button', { name: 'seleziona D3' }))
    await userEvent.click(screen.getByRole('button', { name: 'Refuso della stampa' }))
    // La conferma entra nella griglia: le somme si ricalcolano con lei.
    await waitFor(() => {
      const sent = fetchMock.mock.calls
        .filter(([url]) => String(url).includes('/tables/checks'))
        .map(([, init]) => JSON.parse((init as RequestInit).body as string))
      expect(sent[sent.length - 1]?.misprints).toEqual(['row:79,492=17,411+62,031'])
    }, { timeout: 2000 })
  })

  it('un blocco verificato e non selezionato sta su una riga; selezionato si riapre', () => {
    const block = makeBlock({ confirmed: true, content: 'TRADE WITH COUNTRIES.' })
    const { rerender } = render(
      <ContentPane blocks={[block]} drafts={[]} labels={[]} selectedId={null} working={null} {...noop} />,
    )
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.getByText('TRADE WITH COUNTRIES.')).toBeTruthy()
    rerender(<ContentPane blocks={[block]} drafts={[]} labels={[]} selectedId="b1" working={null} {...noop} />)
    expect(screen.getByRole('textbox')).toHaveValue('TRADE WITH COUNTRIES.')
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
    await user.click(screen.getByText('Opzioni'))
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

  it('a stream finito resta chiuso: il testo grezzo si apre a richiesta', async () => {
    const user = userEvent.setup()
    render(
      <ContentPane
        blocks={[]}
        drafts={[]}
        labels={[]}
        selectedId={null}
        working={null}
        liveOutput={{ phase: 'completed', text: 'Titolo riconosciuto' }}
        {...noop}
      />,
    )
    const stream = screen.getByRole('region', { name: 'Output del modello' })
    expect(stream).toHaveAttribute('aria-busy', 'false')
    // Chiuso di default: sotto c'è già il parsing, il grezzo non occupa il rail.
    expect(screen.queryByText('Titolo riconosciuto')).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Apri l’output del modello' }))
    expect(screen.getByText('Titolo riconosciuto')).toBeTruthy()
  })

  it('una bozza del prefill può cambiare classe dal pannello', async () => {
    const user = userEvent.setup()
    const onDraftLabel = vi.fn()
    const labels: LabelDef[] = [
      { name: 'Page-header', color: '#45594f', short: 'PGH', prompt_kind: 'text', prompt: 't', origin: 'public', builtin: true, aliases: [], known_models: [] },
      { name: 'Issue-date', color: '#6b4f1d', short: 'DAT', prompt_kind: 'text', prompt: 't', origin: 'custom', builtin: true, aliases: [], known_models: [] },
    ]
    const drafts: PrefillDraft[] = [
      { serverId: 42, label: 'Page-header', content: '[NOV.21925]', confirmed: false, grid: null },
    ]
    render(
      <ContentPane
        blocks={[]}
        drafts={drafts}
        labels={labels}
        selectedId={null}
        working={null}
        {...noop}
        onDraftLabel={onDraftLabel}
      />,
    )
    // La bozza porta il menu della classe, come un blocco confermato.
    await user.click(screen.getByRole('option', { name: 'Data dell’uscita' }))
    expect(onDraftLabel).toHaveBeenCalledWith(42, 'Issue-date')
  })
})

describe('ContentPane in model comparison', () => {
  it('heads the rows with the summary, puts each verdict in its row and the rest below', () => {
    const draft: PrefillDraft = { serverId: 21, label: 'Text', content: 'Fonte', confirmed: false, grid: null }
    render(
      <ContentPane
        blocks={[makeBlock({ id: 'b1', content: 'Arrivi', confirmed: true })]}
        drafts={[draft]}
        labels={[]}
        selectedId={null}
        working={null}
        {...noop}
        consensus={{
          summary: <p>summary</p>,
          row: (rowId) => rowId === 'b1'
            ? { chip: <span>chip-b1</span>, detail: <div>detail-b1</div> }
            : rowId === 'draft-21' ? { chip: <span>chip-draft</span>, detail: null } : null,
          footer: <p>only in the models</p>,
        }}
      />,
    )
    expect(screen.getByText('summary')).toBeInTheDocument()
    // A verified block with an open detail is not folded away into one line.
    expect(screen.getByText('detail-b1')).toBeInTheDocument()
    expect(screen.getByText('chip-b1')).toBeInTheDocument()
    expect(screen.getByText('chip-draft')).toBeInTheDocument()
    expect(screen.getByText('only in the models')).toBeInTheDocument()
  })
})

describe('ContentPane source badge', () => {
  it('names the model a draft came from, and keeps OCR for the OCR engine', () => {
    const drafts: PrefillDraft[] = [
      { serverId: 31, label: 'Text', content: 'Arrivi', confirmed: false, grid: null, source: 'model:ocria-1:official' },
      { serverId: 32, label: 'Text', content: 'Fonte', confirmed: false, grid: null, source: 'ocr:rapidocr' },
    ]
    render(
      <ContentPane blocks={[]} drafts={drafts} labels={[]} selectedId={null} working={null} {...noop}
        sourceLabel={(source) => source.startsWith('model:ocria-1') ? 'OCRia-1 (LlamaIndex)' : 'OCR'} />,
    )
    expect(screen.getByText('OCRia-1 (LlamaIndex)')).toBeInTheDocument()
    expect(screen.getByText('OCR')).toBeInTheDocument()
  })
})
