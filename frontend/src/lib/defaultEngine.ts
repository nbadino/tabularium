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
