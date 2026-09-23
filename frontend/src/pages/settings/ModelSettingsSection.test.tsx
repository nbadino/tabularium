// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { apiGet, apiPut } from '../../lib/api'
import ModelSettingsSection from './ModelSettingsSection'

vi.mock('../../lib/api', () => ({
  apiGet: vi.fn(async () => ({ items: [{
    adapter_id: 'monkeyocrv2-parsing', display_name: 'MonkeyOCRv2-Parsing',
    recommended: { serving: { gpu_memory_utilization: 0.9, max_model_len: 24576, max_num_seqs: 8, max_num_batched_tokens: 24576 }, generation: {}, image: { max_pixels: null } },
    overrides: {},
    effective: { serving: { gpu_memory_utilization: 0.9, max_model_len: 24576, max_num_seqs: 8, max_num_batched_tokens: 24576 }, generation: {}, image: { max_pixels: null } },
    restart_required: false,
  }] })),
  apiPut: vi.fn(async (_path: string, payload: Record<string, unknown>) => ({
    adapter_id: 'monkeyocrv2-parsing', display_name: 'MonkeyOCRv2-Parsing',
    recommended: { serving: { gpu_memory_utilization: 0.9, max_model_len: 24576, max_num_seqs: 8, max_num_batched_tokens: 24576 }, generation: {}, image: { max_pixels: null } },
    overrides: payload, effective: payload, restart_required: Boolean(payload.serving),
  })),
}))

describe('ModelSettingsSection', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(cleanup)

  it('carica valori consigliati e salva override distinti per il modello', async () => {
    render(<ModelSettingsSection isAdmin />)
    expect((await screen.findAllByDisplayValue('24576')).length).toBeGreaterThan(0)
    const temperature = screen.getByLabelText(/Temperature/i)
    fireEvent.change(temperature, { target: { value: '0.2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salva override' }))
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith(
      '/system/model-settings/monkeyocrv2-parsing',
      { generation: { temperature: 0.2 } },
    ))
    expect(apiGet).toHaveBeenCalledWith('/system/model-settings')
  })

  it('rende i controlli di sola lettura se l’utente non è admin', async () => {
    render(<ModelSettingsSection isAdmin={false} />)
    expect(await screen.findByText(/regolazione dei preset è riservata agli amministratori/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Salva override' })).toBeDisabled()
  })

  it('salva la modalità layout ufficiale di TeleOCR', async () => {
    vi.mocked(apiGet).mockResolvedValueOnce({ items: [{
      adapter_id: 'teleocr', display_name: 'TeleOCR',
      recommended: { serving: { max_model_len: 16384, gpu_memory_utilization: 0.95 }, generation: {}, image: { max_pixels: 64000000 }, workflow: { layout_mode: 'Detection' } },
      overrides: {},
      effective: { serving: { max_model_len: 16384, gpu_memory_utilization: 0.95 }, generation: {}, image: { max_pixels: 64000000 }, workflow: { layout_mode: 'Detection' } },
      restart_required: false,
    }] } as never)
    render(<ModelSettingsSection isAdmin />)
    const mode = await screen.findByLabelText('Modalità layout')
    fireEvent.change(mode, { target: { value: 'Segmentation' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salva override' }))
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith(
      '/system/model-settings/teleocr',
      { workflow: { layout_mode: 'Segmentation' } },
    ))
  })

  it('inoltra i controlli del workflow ufficiale PaddleOCR-VL', async () => {
    const workflow = Object.fromEntries([
      'use_layout_detection', 'use_doc_orientation_classify', 'use_doc_unwarping',
      'use_chart_recognition', 'use_seal_recognition', 'use_ocr_for_image_block',
      'format_block_content', 'merge_layout_blocks', 'use_queues',
    ].map((key) => [key, null]))
    workflow.layout_threshold = null
    workflow.layout_unclip_ratio = null
    workflow.layout_merge_bboxes_mode = null
    vi.mocked(apiGet).mockResolvedValueOnce({ items: [{
      adapter_id: 'paddleocr-vl', display_name: 'PaddleOCR-VL-1.6',
      recommended: { serving: { max_model_len: 12288 }, generation: {}, image: { max_pixels: null }, workflow },
      overrides: {},
      effective: { serving: { max_model_len: 12288 }, generation: {}, image: { max_pixels: null }, workflow },
      restart_required: false,
    }] } as never)
    render(<ModelSettingsSection isAdmin />)
    const layout = await screen.findByLabelText('Rilevamento layout')
    fireEvent.change(layout, { target: { value: 'true' } })
    fireEvent.change(screen.getByLabelText(/layout_threshold/), { target: { value: '0.4' } })
    fireEvent.change(screen.getByLabelText(/layout_merge_bboxes_mode/), { target: { value: 'union' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salva override' }))
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith(
      '/system/model-settings/paddleocr-vl',
      { workflow: {
        use_layout_detection: true,
        layout_threshold: 0.4,
        layout_merge_bboxes_mode: 'union',
      } },
    ))
  })

})
