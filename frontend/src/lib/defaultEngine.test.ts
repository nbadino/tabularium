import { describe, expect, it } from 'vitest'
import { defaultEngine } from './defaultEngine'

describe('defaultEngine', () => {
  it('uses the local OCR until a model answers', () => {
    expect(defaultEngine(null, { enabled: true, available: false })).toBe('ocr')
    expect(defaultEngine({ recommended: 'ocr', ocrAvailable: true }, { enabled: true, available: false })).toBe('ocr')
  })

  it('uses the model when it answers', () => {
    expect(defaultEngine({ recommended: 'model', ocrAvailable: true }, { enabled: true, available: true })).toBe('model')
    expect(defaultEngine(null, { enabled: true, available: true })).toBe('model')
  })

  it('keeps the model when the OCR engine is missing', () => {
    expect(defaultEngine({ recommended: 'model', ocrAvailable: false }, { enabled: true, available: false })).toBe('model')
  })

  it('does not pick a disabled model', () => {
    expect(defaultEngine({ recommended: 'model', ocrAvailable: true }, { enabled: false, available: true })).toBe('ocr')
  })
})
