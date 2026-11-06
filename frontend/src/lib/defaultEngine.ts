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

/** A saved model this user can run: it needs no key, or they added theirs. */
export function canUseSavedModel(model: { key_required?: boolean; has_key?: boolean }): boolean {
  return !model.key_required || !!model.has_key
}

/**
 * The Recognition page also offers the saved models (API services reached with
 * each user's own key, nothing to serve): when the active model is not ready and
 * the user can run a saved one, the page opens on it rather than on the local OCR.
 */
export function defaultRecognitionEngine(
  engines: { recommended: 'model' | 'ocr' | null; ocrAvailable: boolean } | null,
  inference: { enabled: boolean; available: boolean },
  savedModels: { id: number; key_required?: boolean; has_key?: boolean }[],
): RecognitionEngine {
  const base = defaultEngine(engines, inference)
  if (base === 'model' && inference.enabled && inference.available) return 'model'
  const usable = savedModels.find(canUseSavedModel)
  if (usable) return `profile:${usable.id}`
  return base
}

/** The saved model an engine reads with; null for the active model and the OCR. */
export function savedModelOf(engine: RecognitionEngine): number | null {
  return engine.startsWith('profile:') ? Number(engine.slice('profile:'.length)) : null
}
