import { describe, expect, it } from 'vitest'
import { MONKEYOCR_8GB, recipeDefaults } from './presets'
import type { TrainRecipeInfo } from '../../lib/types'

const glm: TrainRecipeInfo = {
  adapter_id: 'glm-ocr',
  display_name: 'GLM-OCR',
  hf_repo: 'zai-org/GLM-OCR',
  toolchain: 'llama-factory',
  train_types: ['lora', 'full'],
  data_view: 'llamafactory-sharegpt',
  families: ['text_rec', 'table', 'formula'],
  official_doc: 'https://example.test/glm',
  maturity: 'experimental',
  note: '',
  defaults: { learning_rate: 1e-4, epochs: 3, batch_size: 4 },
  type_defaults: { full: { learning_rate: 1e-5 } },
  flags: {},
  prompts: {},
  trainable: true,
  script_ready: true,
  merge_supported: true,
  official_notes: [],
  verified_on: '',
}

describe('recipe defaults', () => {
  it('applies the official LoRA learning rate', () => {
    const cfg = recipeDefaults(glm, 'lora')
    expect(cfg.adapter_id).toBe('glm-ocr')
    expect(cfg.model).toBe('zai-org/GLM-OCR')
    expect(cfg.learning_rate).toBe(1e-4)
    expect(cfg.epochs).toBe(3)
  })

  it('specialises the learning rate for full tuning', () => {
    expect(recipeDefaults(glm, 'full').learning_rate).toBe(1e-5)
  })

  it('keeps the official learning rate when adapting MonkeyOCRv2 LoRA to an 8 GB GPU', () => {
    expect(MONKEYOCR_8GB.train_type).toBe('lora')
    expect(MONKEYOCR_8GB.max_length).toBe(8192)
    expect(MONKEYOCR_8GB.batch_size).toBe(1)
    expect('learning_rate' in MONKEYOCR_8GB).toBe(false)
  })
})
