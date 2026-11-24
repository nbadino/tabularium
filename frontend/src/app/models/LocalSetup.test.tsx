// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { LocalSetup } from './LocalSetup'
import type { ModelItem } from './registry'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    apiGet: vi.fn(async () => ({ running: false, adapter_id: null, port: null, pid: null, error: null })),
  }
})

afterEach(cleanup)

// The Lloyd's line reader as `/api/models` returns it before its package is
// installed (pinned on the backend by `tests/test_indra_lloyds.py`): no repository.
const reader: ModelItem = {
  adapter_id: 'indra-lloyds',
  display_name: "Indra · Lloyd's registers",
  tasks: ['table'],
  table_format: 'otsl',
  hf_repo: '',
  approx_size_gb: 0.02,
  license_note: '',
  train_toolchain: 'none',
  serve_backend: 'indra-native',
  maturity: 'experimental',
  supports_native: true,
  supports_two_stage: false,
  supports_end2end: false,
  export_ready: false,
  local_serve_ready: true,
  local: { runnable: true, runtime: 'onnxruntime', reason: null, mlx_repo: null },
  cloud_serve_ready: false,
  vast_serve_ready: false,
  cloud_template: null,
  download_only: false,
  downloadable: false,
  installed: false,
  downloading: false,
  expected_bytes: null,
  path: null,
  size_bytes: 0,
  state: 'absent',
  error: null,
  vram_warning: null,
}

describe('LocalSetup', () => {
  it('tells how to install a model that has no repository to download', async () => {
    render(<LocalSetup model={reader} onChanged={vi.fn(async () => {})} />)
    expect(await screen.findByText(/tabularium models import .* --adapter indra-lloyds/)).toBeInTheDocument()
    // No download button: there is nothing to download.
    expect(screen.queryByRole('button', { name: /^(Download|Scarica|Télécharger)/ })).not.toBeInTheDocument()
  })
})
