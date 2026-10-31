import { beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { apiGet, apiPut } from '../../lib/api'
import {
  saveInferenceToBackend,
  syncInferenceFromBackend,
  testInferenceConnection,
} from '../inference'
import { LlamaIndexSetup } from './LlamaIndexSetup'
import type { ModelItem } from './registry'

vi.mock('../../lib/api', () => ({ apiGet: vi.fn(), apiPut: vi.fn() }))
vi.mock('../inference', () => ({
  useInference: () => ({
    enabled: false,
    url: '',
    model: '',
    adapterId: '',
    provider: null,
    hasApiKey: false,
    available: false,
  }),
  saveInferenceToBackend: vi.fn(),
  syncInferenceFromBackend: vi.fn(),
  testInferenceConnection: vi.fn(),
}))

const settings = {
  adapter_id: 'llamaindex-ocria-1',
  display_name: 'OCRia-1 (LlamaIndex)',
  recommended: { workflow: {} },
  overrides: {},
  effective: {
    workflow: {
      tier: 'agentic',
      version: 'latest',
      confidence_score_effort: 'standard',
      languages: ['eng'],
      aggressive_table_extraction: true,
      disable_heuristics: false,
      specialized_chart_parsing: 'off',
      cost_optimizer: false,
      ignore_diagonal_text: false,
      ignore_hidden_text: false,
      ignore_text_in_image: false,
      camera_photo_correction: false,
      crop_enabled: false,
      crop_box: { top: 0, bottom: 0, left: 0, right: 0 },
      watermark_handling: 'move_to_end',
      extract_printed_page_number: false,
      markdown_annotate_links: true,
      markdown_annotate_revisions: false,
      markdown_annotate_line_numbers: false,
      markdown_compact_tables: true,
      custom_prompt: '',
      job_timeout_in_seconds: 300,
      job_timeout_extra_time_per_page_in_seconds: 30,
      allowed_page_failure_ratio: 0,
      disable_cache: false,
    },
  },
  restart_required: false,
}

const model = {
  adapter_id: 'llamaindex-ocria-1',
  display_name: 'OCRia-1 (LlamaIndex)',
  tasks: ['layout', 'text', 'table'],
  table_format: 'html',
  hf_repo: '',
  approx_size_gb: null,
  license_note: '',
  train_toolchain: 'none',
  serve_backend: 'llamaindex-api',
  maturity: 'experimental',
  supports_native: true,
  supports_two_stage: false,
  supports_end2end: false,
  export_ready: false,
  local_serve_ready: false,
  local: { runnable: false, runtime: null, reason: 'no_local_runtime', mlx_repo: null },
  cloud_serve_ready: false,
  cloud_template: null,
  download_only: false,
  installed: false,
  downloading: false,
  expected_bytes: null,
  path: null,
  size_bytes: 0,
  state: 'absent',
  error: null,
  vram_warning: null,
} satisfies ModelItem

describe('LlamaIndexSetup', () => {
  beforeEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.mocked(apiGet).mockResolvedValue(settings)
    vi.mocked(apiPut).mockResolvedValue(settings)
    vi.mocked(testInferenceConnection).mockResolvedValue({
      ok: true,
      url: 'https://api.cloud.llamaindex.ai',
      model: 'ocria-1',
      models_available: ['latest', '2026-09-01'],
    })
    vi.mocked(saveInferenceToBackend).mockResolvedValue({
      enabled: true,
      url: 'https://api.cloud.llamaindex.ai',
      model: 'ocria-1',
      adapterId: 'llamaindex-ocria-1',
      provider: 'llamaindex',
      apiKey: '',
      hasApiKey: true,
      extraHeaders: {},
      timeout: 120,
      maxPixels: null,
      isCloud: true,
      available: true,
      latencyMs: 10,
      modelsAvailable: [],
      error: null,
    })
    vi.mocked(syncInferenceFromBackend).mockResolvedValue({} as never)
  })

  it('salva il workflow e attiva il provider LlamaIndex con la chiave inserita', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<LlamaIndexSetup model={model} onClose={onClose} />)

    await screen.findByRole('heading', { name: 'Configurazione OCRia-1' })
    await user.type(screen.getByLabelText('API key personale'), 'llx-user-key')
    await user.click(screen.getAllByRole('button', { name: 'Salva e usa OCRia-1' })[0])

    await waitFor(() => expect(saveInferenceToBackend).toHaveBeenCalledWith(expect.objectContaining({
      url: 'https://api.cloud.llamaindex.ai',
      model: 'ocria-1',
      adapterId: 'llamaindex-ocria-1',
      provider: 'llamaindex',
      apiKey: 'llx-user-key',
    })))
    expect(apiPut).toHaveBeenCalledWith(
      '/system/model-settings/llamaindex-ocria-1',
      expect.objectContaining({
        workflow: expect.objectContaining({ tier: 'agentic', languages: ['eng'] }),
      }),
    )
    expect(onClose).toHaveBeenCalled()
  })

  it('offre Agentic Plus e i soli valori filigrana accettati da LlamaCloud', async () => {
    const user = userEvent.setup()
    render(<LlamaIndexSetup model={model} onClose={vi.fn()} />)

    await screen.findByRole('heading', { name: 'Configurazione OCRia-1' })
    await user.click(screen.getByRole('radio', { name: /Agentic Plus/ }))
    expect(screen.getByRole('radio', { name: /Agentic Plus/ })).toHaveAttribute('aria-checked', 'true')
    const watermark = screen.getByDisplayValue('Sposta in fondo') as HTMLSelectElement
    expect(Array.from(watermark.options, (option) => option.value)).toEqual(['move_to_end', 'move_to_start', 'remove'])

    await user.type(screen.getByLabelText('API key personale'), 'llx-user-key')
    await user.click(screen.getAllByRole('button', { name: 'Salva e usa OCRia-1' })[0])
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith(
      '/system/model-settings/llamaindex-ocria-1',
      expect.objectContaining({ workflow: expect.objectContaining({ tier: 'agentic_plus' }) }),
    ))
  })
})
