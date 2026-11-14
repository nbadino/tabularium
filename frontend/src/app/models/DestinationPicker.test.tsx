import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { DestinationPicker } from './DestinationPicker'
import type { ModelItem } from './registry'

afterEach(cleanup)

// Unlimited-OCR-2 as `/api/models` returns it on a server without CUDA
// (pinned on the backend by `tests/test_unlimited_ocr2.py`): no Modal template,
// a serve recipe the Vast setup script runs, the vendor's block parser.
const uocr2: ModelItem = {
  adapter_id: 'unlimited-ocr-2',
  display_name: 'Unlimited-OCR-2',
  tasks: ['layout', 'text', 'table', 'formula'],
  table_format: 'html',
  hf_repo: 'baidu/Unlimited-OCR-2',
  approx_size_gb: 6.7,
  license_note: 'MIT.',
  train_toolchain: 'ms-swift',
  serve_backend: 'sglang-openai',
  maturity: 'experimental',
  supports_native: true,
  supports_two_stage: false,
  supports_end2end: true,
  export_ready: true,
  local_serve_ready: true,
  local: { runnable: false, runtime: null, reason: 'no_local_runtime', mlx_repo: null },
  cloud_serve_ready: false,
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
}

function card(name: RegExp) {
  return screen.getByRole('button', { name })
}

describe('DestinationPicker', () => {
  it('offers Vast.ai for Unlimited-OCR-2, not only the compatible endpoint', () => {
    // Reported symptom: the picker left only the endpoint clickable.
    render(<DestinationPicker model={uocr2} selected={null} caps={null} onSelect={vi.fn()} />)
    expect(card(/Vast\.ai/)).toBeEnabled()
    expect(card(/Compatible endpoint|Endpoint compatibile|Endpoint compatible/)).toBeEnabled()
    // Where it really cannot run, the card stays disabled and says why.
    expect(card(/^Modal/)).toBeDisabled()
    expect(card(/RunPod/)).toBeDisabled()
    expect(card(/LlamaIndex/)).toBeDisabled()
  })
})
