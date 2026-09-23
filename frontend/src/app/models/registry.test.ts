import { describe, expect, it } from 'vitest'
import { destinationVerdict, isSelectable, recommendedDestination, resolveStep, type ModelItem } from './registry'

function model(over: Partial<ModelItem> = {}): ModelItem {
  return {
    adapter_id: 'monkeyocrv2-parsing',
    display_name: 'MonkeyOCRv2-Parsing',
    tasks: ['layout', 'text', 'table'],
    table_format: 'otsl',
    hf_repo: 'zenosai/MonkeyOCRv2-B-Parsing',
    approx_size_gb: 1.5,
    license_note: '',
    train_toolchain: 'ms-swift',
    serve_backend: 'vllm',
    maturity: 'supported',
    supports_native: true,
    supports_two_stage: true,
    supports_end2end: true,
    export_ready: true,
    local_serve_ready: true,
    local: { runnable: false, runtime: null, reason: 'model_unsupported_locally', mlx_repo: null },
    cloud_serve_ready: true,
    cloud_template: 'monkeyocrv2',
    download_only: false,
    installed: false,
    downloading: false,
    expected_bytes: null,
    path: null,
    size_bytes: 0,
    state: 'absent',
    error: null,
    vram_warning: null,
    ...over,
  }
}

describe('destinationVerdict', () => {
  it('rifiuta il locale con la causa del backend', () => {
    const v = destinationVerdict(model(), 'local')
    expect(v.ok).toBe(false)
    expect(v.reasonKey).toBe('localCompute.reason.model_unsupported_locally')
  })

  it('accetta il locale quando la macchina può servirlo', () => {
    const m = model({ local: { runnable: true, runtime: 'mlx-vlm', reason: null, mlx_repo: 'mlx/x' } })
    expect(destinationVerdict(m, 'local').ok).toBe(true)
    expect(recommendedDestination(m)).toBe('local')
  })

  it('Modal solo con una template', () => {
    expect(destinationVerdict(model({ cloud_template: null }), 'modal').ok).toBe(false)
    expect(destinationVerdict(model(), 'modal').ok).toBe(true)
  })

  it('un checkpoint personalizzato si pubblica su Vast/Modal, non su RunPod', () => {
    const custom = model({ adapter_id: 'custom-foo', cloud_serve_ready: false, cloud_template: null })
    expect(destinationVerdict(custom, 'vast')).toEqual({ ok: true, publish: true })
    expect(destinationVerdict(custom, 'modal')).toEqual({ ok: true, publish: true })
    expect(destinationVerdict(custom, 'runpod').ok).toBe(false)
  })

  it('senza parser e senza ricette il modello non è selezionabile', () => {
    const m = model({ supports_native: false, cloud_serve_ready: false, cloud_template: null })
    expect(isSelectable(m)).toBe(false)
    expect(recommendedDestination(m)).toBeNull()
  })

  it('raccomanda il serverless prima delle GPU a noleggio', () => {
    expect(recommendedDestination(model())).toBe('modal')
  })
})

describe('resolveStep', () => {
  it('senza modello resta sul primo passo', () => {
    expect(resolveStep('configure', null, 'vast')).toBe('model')
  })

  it('una destinazione che non accoglie il modello riporta al secondo passo', () => {
    expect(resolveStep('configure', model(), 'local')).toBe('destination')
    expect(resolveStep(null, model(), null)).toBe('destination')
  })

  it('rispetta il passo chiesto quando i prerequisiti ci sono', () => {
    expect(resolveStep('configure', model(), 'modal')).toBe('configure')
    expect(resolveStep('model', model(), 'modal')).toBe('model')
    expect(resolveStep('destination', model(), 'modal')).toBe('destination')
  })
})
