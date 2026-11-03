// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { readFileSync } from 'node:fs'
import { apiGet, apiPut } from '../../lib/api'
import ModelSettingsSection, { type ModelSettingsItem } from './ModelSettingsSection'
import type { WorkflowFieldSpec } from './WorkflowFields'

const wf = (
  key: string,
  kind: WorkflowFieldSpec['kind'],
  extra: Partial<WorkflowFieldSpec> = {},
): WorkflowFieldSpec => ({ key, kind, default: null, ...extra })

const SERVING = { dtype: null, gpu_memory_utilization: null, max_model_len: null, max_num_seqs: null, max_num_batched_tokens: null }

function item(over: Partial<ModelSettingsItem> & { adapter_id: string }): ModelSettingsItem {
  const recommended = over.recommended ?? { serving: SERVING, generation: {}, image: { max_pixels: null }, workflow: {} }
  return {
    display_name: over.adapter_id,
    recommended,
    overrides: {},
    effective: JSON.parse(JSON.stringify(recommended)),
    restart_required: false,
    image_fields: ['max_pixels'],
    task_sampling: {},
    ...over,
  }
}

const MONKEY = item({
  adapter_id: 'monkeyocrv2-parsing',
  display_name: 'MonkeyOCRv2-Parsing',
  recommended: {
    serving: SERVING,
    generation: { temperature: 0 },
    image: { max_pixels: 1003520 },
    workflow: { dflash_enabled: true, dflash_num_speculative_tokens: null },
  },
  workflow_fields: [
    wf('dflash_enabled', 'bool', { default: true, label: 'settings.monkeyDFlashEnabled', essential: true }),
    wf('dflash_num_speculative_tokens', 'int', { minimum: 1, maximum: 16, nullable: true, label: 'settings.monkeyDFlashTokens' }),
  ],
  workflow_intro: 'settings.monkeyDFlashIntro',
  serving_dtype: false,
})

vi.mock('../../lib/api', () => ({
  apiGet: vi.fn(),
  apiPut: vi.fn(async (path: string, payload: Record<string, unknown>) => ({
    adapter_id: path.split('/').pop(), overrides: payload, effective: payload, restart_required: false,
  })),
}))

const respond = (...items: ModelSettingsItem[]) => vi.mocked(apiGet).mockResolvedValue({ items } as never)
const openTab = async (name: RegExp) => fireEvent.click(await screen.findByRole('tab', { name }))
const save = () => fireEvent.click(screen.getByRole('button', { name: 'Salva' }))

describe('ModelSettingsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    respond(MONKEY)
  })
  afterEach(cleanup)

  it('opens on the essentials of the model chosen in the hub, with the official values', async () => {
    respond(item({ adapter_id: 'glm-ocr', display_name: 'GLM-OCR' }), MONKEY)
    render(<ModelSettingsSection isAdmin adapterId="monkeyocrv2-parsing" destination="vast" />)
    expect(await screen.findByRole('tab', { name: /Essenziali/, selected: true })).toBeInTheDocument()
    expect(screen.getByLabelText('Dettaglio dell’immagine')).toHaveValue(1003520)
    expect(screen.getByLabelText('DFlash ufficiale')).toHaveValue('true')
    // No second model picker: the hub already chose.
    expect(screen.queryByLabelText('Modello supportato')).not.toBeInTheDocument()
    expect(screen.getByText('Tutto ufficiale')).toBeInTheDocument()
  })

  it('marks a value that differs from the official one and resets it on its own', async () => {
    render(<ModelSettingsSection isAdmin adapterId="monkeyocrv2-parsing" destination="vast" />)
    const detail = await screen.findByLabelText('Dettaglio dell’immagine')
    fireEvent.change(detail, { target: { value: '2000000' } })
    expect(screen.getByText('1 diverso dall’ufficiale')).toBeInTheDocument()
    expect(within(screen.getByRole('tab', { name: /Essenziali/ })).getByText('1')).toBeInTheDocument()
    expect(screen.getByText('Diverso dall’ufficiale:')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Ripristina il valore ufficiale di Dettaglio dell’immagine' })[0])
    expect(detail).toHaveValue(1003520)
    expect(screen.getByText('Tutto ufficiale')).toBeInTheDocument()
  })

  it('saves only what differs, from any tab', async () => {
    render(<ModelSettingsSection isAdmin adapterId="monkeyocrv2-parsing" destination="vast" />)
    fireEvent.change(await screen.findByLabelText('DFlash ufficiale'), { target: { value: 'false' } })
    await openTab(/Pipeline del modello/)
    fireEvent.change(screen.getByLabelText('Token speculativi DFlash'), { target: { value: '4' } })
    await openTab(/Testo/)
    fireEvent.change(screen.getByLabelText('Casualità'), { target: { value: '0.2' } })
    save()
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith('/system/model-settings/monkeyocrv2-parsing', {
      generation: { temperature: 0.2 },
      workflow: { dflash_enabled: false, dflash_num_speculative_tokens: 4 },
    }))
  })

  it('shows the sampling each task really sends instead of "automatic"', async () => {
    respond(item({
      adapter_id: 'mineru2.5', display_name: 'MinerU2.5', image_fields: [],
      task_sampling: {
        layout: { temperature: 0, top_k: 1 },
        text: { temperature: 0, top_k: 1, presence_penalty: 1 },
        table: { temperature: 0, top_k: 1, presence_penalty: 1, frequency_penalty: 0.005 },
      },
    }))
    render(<ModelSettingsSection isAdmin adapterId="mineru2.5" destination="vast" />)
    await openTab(/Testo/)
    expect(screen.getAllByText('ufficiale: 0').length).toBeGreaterThan(0)
    expect(screen.getByText('ufficiale: 1 (testo, tabelle)')).toBeInTheDocument()
    expect(screen.getByText('ufficiale: 0,005 (tabelle)')).toBeInTheDocument()
  })

  it('offers the server group of the destination: vLLM remotely, mlx-vlm on a Mac', async () => {
    respond(item({
      adapter_id: 'qwen3-vl-8b', display_name: 'Qwen3-VL-8B', mlx_available: true,
      recommended: { serving: SERVING, generation: {}, image: { max_pixels: null }, workflow: {}, mlx: { kv_bits: null, repetition_penalty: 1.1, repetition_context_size: 128, presence_context_size: 256 } },
    }))
    const { unmount } = render(<ModelSettingsSection isAdmin adapterId="qwen3-vl-8b" destination="vast" />)
    await openTab(/Server · Vast\.ai/)
    expect(screen.getByLabelText('Quota di memoria GPU')).toBeInTheDocument()
    expect(screen.queryByLabelText('Bit della cache')).not.toBeInTheDocument()
    unmount()

    render(<ModelSettingsSection isAdmin adapterId="qwen3-vl-8b" destination="local" localRuntime="mlx-vlm" />)
    await openTab(/Server · Locale/)
    expect(screen.getByLabelText('Bit della cache')).toBeInTheDocument()
    expect(screen.queryByLabelText('Quota di memoria GPU')).not.toBeInTheDocument()
    await openTab(/Testo/)
    // The MLX anti-loop guard and its presence window are reachable.
    expect(screen.getByLabelText('Finestra della penalità di presenza (MLX)')).toHaveValue(256)
  })

  it('saves the TeleOCR layout mode and a serving precision', async () => {
    respond(item({
      adapter_id: 'teleocr', display_name: 'TeleOCR',
      recommended: { serving: { ...SERVING, max_model_len: 16384 }, generation: {}, image: { max_pixels: 64000000 }, workflow: { layout_mode: 'Detection' } },
      workflow_fields: [wf('layout_mode', 'enum', {
        default: 'Detection', values: ['Detection', 'Segmentation'], label: 'settings.teleocrLayoutMode', essential: true,
        hints: [['Detection', 'settings.teleocrDetectionHint'], ['Segmentation', 'settings.teleocrSegmentationHint']],
      })],
    }))
    render(<ModelSettingsSection isAdmin adapterId="teleocr" destination="vast" />)
    fireEvent.change(await screen.findByLabelText('Modalità layout'), { target: { value: 'Segmentation' } })
    await openTab(/Server/)
    fireEvent.change(screen.getByLabelText('Precisione numerica'), { target: { value: 'half' } })
    save()
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith('/system/model-settings/teleocr', {
      serving: { dtype: 'half' }, workflow: { layout_mode: 'Segmentation' },
    }))
  })

  it('hides the sampling fields the vendor rejects', async () => {
    respond(item({ adapter_id: 'glm-ocr', display_name: 'GLM-OCR', generation_unsupported: ['presence_penalty', 'frequency_penalty'] }))
    render(<ModelSettingsSection isAdmin adapterId="glm-ocr" destination="vast" />)
    await openTab(/Testo/)
    expect(screen.getByLabelText('Casualità')).toBeInTheDocument()
    expect(screen.queryByLabelText('Penalità di presenza')).not.toBeInTheDocument()
  })

  it('forwards the PaddleOCR-VL pipeline controls', async () => {
    const keys = ['use_layout_detection', 'use_doc_orientation_classify']
    respond(item({
      adapter_id: 'paddleocr-vl', display_name: 'PaddleOCR-VL-1.6',
      recommended: { serving: SERVING, generation: {}, image: { max_pixels: null, min_pixels: null }, workflow: Object.fromEntries([...keys, 'layout_threshold', 'layout_shape_mode'].map((k) => [k, null])) },
      image_fields: ['min_pixels', 'max_pixels'],
      workflow_fields: [
        ...keys.map((key) => wf(key, 'bool', { nullable: true, label: `settings.paddleWorkflow.${key}` })),
        wf('layout_threshold', 'float', { minimum: 0, maximum: 1, nullable: true }),
        wf('layout_shape_mode', 'enum', { nullable: true, values: ['auto', 'poly', 'quad', 'rect'], label: 'settings.paddleWorkflow.layout_shape_mode' }),
      ],
    }))
    render(<ModelSettingsSection isAdmin adapterId="paddleocr-vl" destination="vast" />)
    await openTab(/Pipeline del modello/)
    fireEvent.change(screen.getByLabelText('Rilevamento layout'), { target: { value: 'true' } })
    fireEvent.change(screen.getByLabelText('layout_threshold'), { target: { value: '0.4' } })
    fireEvent.change(screen.getByLabelText('Forma dei riquadri layout'), { target: { value: 'quad' } })
    await openTab(/Immagine/)
    fireEvent.change(screen.getByLabelText('Ingrandimento minimo'), { target: { value: '100000' } })
    save()
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith('/system/model-settings/paddleocr-vl', {
      image: { min_pixels: 100000 },
      workflow: { use_layout_detection: true, layout_threshold: 0.4, layout_shape_mode: 'quad' },
    }))
  })

  it('is read-only for non-admins', async () => {
    render(<ModelSettingsSection isAdmin={false} adapterId="monkeyocrv2-parsing" destination="vast" />)
    expect(await screen.findByText(/regolazione dei preset è riservata agli amministratori/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva' })).toBeDisabled()
    expect(await screen.findByLabelText('Dettaglio dell’immagine')).toBeDisabled()
  })

  it('without a hub choice, offers its own model choice', async () => {
    render(<ModelSettingsSection isAdmin />)
    expect(await screen.findByLabelText('Modello supportato')).toBeInTheDocument()
  })

  it('builds the panel from the schema, without rules per model', () => {
    // Adding a model must not require touching this component: comparing the id with
    // a literal is the smell.
    const source = readFileSync('src/pages/settings/ModelSettingsSection.tsx', 'utf8')
    expect(source).not.toMatch(/adapter_id === '/)
    expect(source).not.toMatch(/'(qwen3-vl-8b|paddleocr-vl|mineru2\.5|monkeyocrv2-parsing)'/)
  })
})
