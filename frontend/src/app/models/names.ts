/**
 * Il nome di un modello come lo conosce l'utente.
 *
 * Il backend registra il nome *servito* (`mlx-community/Qwen3-VL-8B-Instruct-4bit`,
 * `MonkeyOCRv2`): è quello che l'endpoint vuole sentirsi chiedere, non quello
 * che la libreria mostra. Indicatore globale e liste delle elaborazioni lo
 * stampavano così com'era, e lo stesso modello aveva due nomi a due schermate
 * di distanza. Qui l'adapter torna al suo nome di catalogo.
 */
import { useEffect, useState } from 'react'
import { t } from '../../i18n'
import type { RecognitionRun } from '../../lib/types'
import { fetchModelRegistry, readStoredModelRegistry, type ModelItem } from './registry'

export type ModelNameOf = (adapterId: string | null | undefined) => string | null

export function useModelNames(): ModelNameOf {
  const [items, setItems] = useState<ModelItem[]>(readStoredModelRegistry)
  useEffect(() => {
    let alive = true
    fetchModelRegistry(false)
      .then((next) => alive && setItems(next))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])
  return (adapterId) => items.find((m) => m.adapter_id === adapterId)?.display_name ?? null
}

/** Titolo di un'elaborazione: il motore OCR si chiama tale, un modello per nome. */
export function runTitle(run: Pick<RecognitionRun, 'engine' | 'adapter_id' | 'model_name'>, nameOf: ModelNameOf): string {
  if (run.engine === 'ocr') {
    return run.model_name ? `${t('recognition.localOcr')} · ${run.model_name}` : t('recognition.localOcr')
  }
  return nameOf(run.adapter_id) ?? run.model_name ?? t('recognition.servedModel')
}
