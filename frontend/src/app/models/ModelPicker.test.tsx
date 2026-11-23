import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ModelPicker } from './ModelPicker'
import type { ModelItem } from './registry'

afterEach(cleanup)

const base: ModelItem = {
  adapter_id: 'remote-model',
  display_name: 'Remote Model',
  tasks: ['layout', 'text', 'table'],
  table_format: 'otsl',
  hf_repo: 'org/remote-model',
  approx_size_gb: 1.5,
  license_note: '',
  train_toolchain: 'ms-swift',
  serve_backend: 'vllm-openai',
  maturity: 'supported',
  supports_native: true,
  supports_two_stage: false,
  supports_end2end: true,
  export_ready: true,
  local_serve_ready: true,
  local: { runnable: false, runtime: null, reason: 'no_local_runtime', mlx_repo: null },
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
}

const remote = base
const local: ModelItem = {
  ...base,
  adapter_id: 'local-model',
  display_name: 'Local Model',
  hf_repo: 'org/local-model',
  local: { runnable: true, runtime: 'onnxruntime', reason: null, mlx_repo: null },
}

function renderPicker() {
  render(
    <ModelPicker
      models={[remote, local]}
      loading={false}
      selectedId={null}
      onSelect={vi.fn()}
      onChanged={vi.fn(async () => {})}
    />,
  )
}

const allButton = () => screen.getByRole('button', { name: /^(All|Tutti|Tous) \(2\)$/ })
const hereButton = () => screen.getByRole('button', { name: /\(1\)$/ })

describe('ModelPicker', () => {
  it('lists the whole catalogue by default, even when one model runs on this computer', () => {
    // Reported symptom (23 Nov): after installing the first local model the page
    // opened on "Run on this computer (1)" and the other 19 models looked gone.
    renderPicker()
    expect(screen.getByText('Remote Model')).toBeInTheDocument()
    expect(screen.getByText('Local Model')).toBeInTheDocument()
    expect(allButton()).toHaveAttribute('aria-pressed', 'true')
    expect(hereButton()).toHaveAttribute('aria-pressed', 'false')
  })

  it('narrows to the models that run here only when asked', () => {
    renderPicker()
    fireEvent.click(hereButton())
    expect(screen.queryByText('Remote Model')).not.toBeInTheDocument()
    expect(screen.getByText('Local Model')).toBeInTheDocument()
    fireEvent.click(allButton())
    expect(screen.getByText('Remote Model')).toBeInTheDocument()
  })
})
