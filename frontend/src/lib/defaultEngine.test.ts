import { describe, expect, it } from 'vitest'
import { defaultEngine, defaultRecognitionEngine, savedModelOf } from './defaultEngine'

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

describe('defaultRecognitionEngine', () => {
  // Lloyd's: the active model is off, Gemini and OCRia are saved with their keys.
  it('opens on a saved model rather than the local OCR when the active one is not ready', () => {
    expect(defaultRecognitionEngine({ recommended: 'ocr', ocrAvailable: true }, { enabled: false, available: false }, [{ id: 1 }, { id: 3 }])).toBe('profile:1')
    expect(savedModelOf('profile:3')).toBe(3)
    expect(savedModelOf('model')).toBeNull()
  })

  it('keeps the active model when it answers, and the OCR when nothing is saved', () => {
    expect(defaultRecognitionEngine(null, { enabled: true, available: true }, [{ id: 1 }])).toBe('model')
    expect(defaultRecognitionEngine({ recommended: 'ocr', ocrAvailable: true }, { enabled: false, available: false }, [])).toBe('ocr')
  })
})
