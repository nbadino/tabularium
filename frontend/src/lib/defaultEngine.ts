/**
 * Which engine a new recognition starts with: the model when it answers,
 * otherwise the local OCR, so an install without a GPU never opens on an
 * engine that cannot run. An explicit choice by the user always wins.
 */
export function defaultEngine(
  engines: { recommended: 'model' | 'ocr' | null; ocrAvailable: boolean } | null,
  inference: { enabled: boolean; available: boolean },
): 'model' | 'ocr' {
  const modelReady = inference.enabled && inference.available
  if (modelReady) return 'model'
  if (engines && !engines.ocrAvailable) return 'model'
  return 'ocr'
}

/** An engine of the Recognition page: the active model, the local OCR, or a saved model. */
export type RecognitionEngine = 'model' | 'ocr' | `profile:${number}`

/**
 * The Recognition page also offers the saved models (API services with their own
 * key, nothing to serve): when the active model is not ready and one is saved,
 * the page opens on it rather than on the local OCR.
 */
export function defaultRecognitionEngine(
  engines: { recommended: 'model' | 'ocr' | null; ocrAvailable: boolean } | null,
  inference: { enabled: boolean; available: boolean },
  savedModels: { id: number }[],
): RecognitionEngine {
  const base = defaultEngine(engines, inference)
  if (base === 'model' && inference.enabled && inference.available) return 'model'
  if (savedModels.length > 0) return `profile:${savedModels[0].id}`
  return base
}

/** The saved model an engine reads with; null for the active model and the OCR. */
export function savedModelOf(engine: RecognitionEngine): number | null {
  return engine.startsWith('profile:') ? Number(engine.slice('profile:'.length)) : null
}
