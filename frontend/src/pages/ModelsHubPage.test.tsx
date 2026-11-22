// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { MemoryRouter } from 'react-router'
import ModelsHubPage from './ModelsHubPage'

vi.mock('../app/auth', () => ({
  useCanAdminister: () => false,
  useAuth: () => ({ user: { id: 3, username: 'viewer', role: 'viewer', active: true }, enabled: true }),
}))

vi.mock('../app/inference', () => ({
  useInference: () => ({ enabled: false, configured: false, url: '', model: '', adapterId: '', provider: null, available: false }),
  syncInferenceFromBackend: vi.fn(async () => ({})),
}))

vi.mock('../app/models/registry', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../app/models/registry')>()
  return {
    ...actual,
    readStoredModelRegistry: () => [],
    fetchModelRegistry: vi.fn(async () => [
      {
        adapter_id: 'monkeyocrv2-parsing',
        display_name: 'MonkeyOCRv2 Parsing',
        tasks: ['layout', 'text', 'table'],
        table_format: 'otsl',
        hf_repo: 'echo840/MonkeyOCR-pro-1.2B',
        approx_size_gb: 3.5,
        license_note: '',
        train_toolchain: 'ms-swift',
        serve_backend: 'vllm',
        maturity: 'supported',
        supports_native: true,
        supports_two_stage: true,
        supports_end2end: true,
        export_ready: true,
        local_serve_ready: true,
        local: { runnable: true, runtime: 'vllm', reason: null, mlx_repo: null },
        cloud_serve_ready: true,
        vast_serve_ready: true,
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
      },
    ]),
  }
})

vi.mock('../lib/api', () => ({
  apiGet: vi.fn(async () => ({ capabilities: {} })),
  apiPut: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(),
}))

afterEach(() => cleanup())

describe('ModelsHubPage', () => {
  it('disables model selection and custom model creation for Viewer accounts with explicit read-only notice', async () => {
    render(<MemoryRouter><ModelsHubPage /></MemoryRouter>)
    expect(await screen.findByText(/Hai accesso in sola lettura/)).toBeInTheDocument()
    const choose = await screen.findByRole('button', { name: 'Scegli' })
    expect(choose).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Aggiungi/ })).toBeNull()
  })
})
