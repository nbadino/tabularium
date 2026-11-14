import { describe, expect, it } from 'vitest'
import { byPublicScore, destinationForProvider, destinationVerdict, isSelectable, orderedDestinations, recommendedDestination, resolveStep, type ModelItem } from './registry'

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
    vast_serve_ready: true,
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

  it('Vast segue la ricetta di serving, non la template Modal', () => {
    // Unlimited-OCR-2: nessuna template Modal, ma lo script Vast installa la
    // build SGLang del produttore dalla ricetta.
    const uocr2 = model({
      adapter_id: 'unlimited-ocr-2',
      serve_backend: 'sglang-openai',
      cloud_serve_ready: false,
      vast_serve_ready: true,
      cloud_template: null,
    })
    expect(destinationVerdict(uocr2, 'vast')).toEqual({ ok: true })
    expect(destinationVerdict(uocr2, 'modal')).toEqual({ ok: false, reasonKey: 'modelsHub.why.noModalTemplate' })
    expect(destinationVerdict(uocr2, 'runpod')).toEqual({ ok: false, reasonKey: 'modelsHub.why.noCloudRecipe' })
    expect(recommendedDestination(uocr2)).toBe('vast')
    const noRecipe = model({ vast_serve_ready: false })
    expect(destinationVerdict(noRecipe, 'vast')).toEqual({ ok: false, reasonKey: 'modelsHub.why.noCloudRecipe' })
  })

  it('un checkpoint personalizzato si pubblica su Vast/Modal, non su RunPod', () => {
    const custom = model({ adapter_id: 'custom-foo', cloud_serve_ready: false, cloud_template: null })
    expect(destinationVerdict(custom, 'vast')).toEqual({ ok: true, publish: true })
    expect(destinationVerdict(custom, 'modal')).toEqual({ ok: true, publish: true })
    expect(destinationVerdict(custom, 'runpod').ok).toBe(false)
  })

  it('senza parser e senza ricette il modello non è selezionabile', () => {
    const m = model({ supports_native: false, cloud_serve_ready: false, vast_serve_ready: false, cloud_template: null })
    expect(isSelectable(m)).toBe(false)
    expect(recommendedDestination(m)).toBeNull()
  })

  it('un modello da gateway esterno si configura come endpoint', () => {
    // Gemini 4 Argon: niente layout, niente ricette cloud, ma un protocollo
    // proprio (Google generateContent via OpenCode) → l'endpoint è la via.
    const argon = model({
      adapter_id: 'gemini-argon',
      serve_backend: 'opencode-gateway',
      supports_native: false,
      supports_two_stage: false,
      supports_end2end: false,
      local_serve_ready: false,
      cloud_serve_ready: false,
      vast_serve_ready: false,
      cloud_template: null,
    })
    expect(destinationVerdict(argon, 'manual').ok).toBe(true)
    expect(isSelectable(argon)).toBe(true)
    expect(recommendedDestination(argon)).toBe('manual')
  })

  it('OCRia-1 usa la destinazione LlamaIndex, non un endpoint generico', () => {
    const ocria = model({
      adapter_id: 'llamaindex-ocria-1',
      display_name: 'OCRia-1 (LlamaIndex)',
      serve_backend: 'llamaindex-api',
      supports_native: true,
      local_serve_ready: false,
      cloud_serve_ready: false,
      vast_serve_ready: false,
      cloud_template: null,
    })
    expect(destinationVerdict(ocria, 'llamaindex')).toEqual({ ok: true })
    expect(destinationVerdict(ocria, 'manual').ok).toBe(false)
    expect(isSelectable(ocria)).toBe(true)
    expect(recommendedDestination(ocria)).toBe('llamaindex')
  })

  it('raccomanda il serverless prima delle GPU a noleggio', () => {
    expect(recommendedDestination(model())).toBe('modal')
  })
})

describe('destinationForProvider', () => {
  it('un provider "custom" salvato è la destinazione «Endpoint»', () => {
    // Il gateway Argon si registra come `custom` (CHECK del profilo) ma
    // nell'hub è «Endpoint»: senza mappatura l'hub non riconosceva la scelta.
    expect(destinationForProvider('custom')).toBe('manual')
    expect(destinationForProvider('modal')).toBe('modal')
    expect(destinationForProvider('llamaindex')).toBe('llamaindex')
    expect(destinationForProvider('local')).toBe('local')
    expect(destinationForProvider(null)).toBeNull()
    expect(destinationForProvider('boh')).toBeNull()
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

describe('orderedDestinations', () => {
  it('lists what can host the model first and keeps the rest, greyed, at the end', () => {
    const order = orderedDestinations(model())
    const firstImpossible = order.findIndex((d) => !destinationVerdict(model(), d).ok)
    expect(firstImpossible).toBeGreaterThan(0)
    expect(order.slice(firstImpossible).every((d) => !destinationVerdict(model(), d).ok)).toBe(true)
    expect(order).toContain('local')
    expect(order.indexOf('local')).toBeGreaterThanOrEqual(firstImpossible)
  })
})

describe('byPublicScore', () => {
  const scored = (id: string, value: number | null) =>
    model({
      adapter_id: id,
      display_name: id,
      benchmark: { benchmark: 'OmniDocBench v1.6', metric: 'Overall', value, reported_by: value == null ? null : 'leaderboard', source: null, note: '' },
    })

  it('puts the highest public score first and models without one last, in catalog order', () => {
    const items = [scored('a', null), scored('b', 91.8), scored('c', 96.91), scored('d', null), model({ adapter_id: 'e', benchmark: undefined })]
    expect([...items].sort(byPublicScore).map((m) => m.adapter_id)).toEqual(['c', 'b', 'a', 'd', 'e'])
  })
})

describe('host blocker', () => {
  it('greys out a model the server itself cannot run, on every destination, with the reason', () => {
    const blocked = model({ adapter_id: 'paddleocr-vl', host_blocker: 'cpu_no_avx2', local: { runnable: true, runtime: 'vllm', reason: null, mlx_repo: null } })
    for (const dest of ['local', 'llamaindex', 'vast', 'runpod', 'modal', 'manual'] as const) {
      expect(destinationVerdict(blocked, dest)).toEqual({ ok: false, reasonKey: 'modelsHub.why.host.cpu_no_avx2' })
    }
    expect(isSelectable(blocked)).toBe(false)
    expect(isSelectable(model())).toBe(true)
  })
})
