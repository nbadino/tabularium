/**
 * Preset di addestramento.
 *
 * Sedici iperparametri non sono una scelta: sono un ostacolo. Il preset è la
 * decisione che quasi tutti prendono davvero — quanto tempo di GPU spendere —
 * e i valori restano visibili e modificabili sotto «Parametri avanzati».
 *
 * I valori partono dagli iperparametri canonici del repo ufficiale
 * MonkeyOCRv2 (AGENTS.md): nessuna reinvenzione del pipeline.
 */
import type { TrainConfigBody } from '../../lib/types'

export const BASE_CFG: TrainConfigBody = {
  model: 'zenosai/MonkeyOCRv2-B-Parsing',
  model_path: '',
  train_type: 'lora',
  lora_rank: 8,
  lora_alpha: 32,
  freeze_vit: true,
  epochs: 1,
  learning_rate: 1e-5,
  batch_size: 4,
  grad_accum: 1,
  max_length: 16384,
  max_pixels: 1003520,
  gpus: '0',
  nproc: 1,
  eval_steps: 200,
}

export interface Preset {
  id: string
  /** Chiave i18n del nome (es. `training.presetQuickName`). */
  nomeKey: string
  /** Chiave i18n della descrizione. */
  percheKey: string
  cfg: Partial<TrainConfigBody>
}

export const PRESETS: Preset[] = [
  {
    id: 'rapido',
    nomeKey: 'training.presetQuickName',
    percheKey: 'training.presetQuickWhy',
    cfg: {
      train_type: 'lora',
      epochs: 1,
      learning_rate: 1e-5,
      batch_size: 2,
      grad_accum: 2,
      lora_rank: 8,
      lora_alpha: 32,
      max_length: 8192,
      eval_steps: 100,
    },
  },
  {
    id: 'bilanciato',
    nomeKey: 'training.presetBalancedName',
    percheKey: 'training.presetBalancedWhy',
    cfg: {
      train_type: 'lora',
      epochs: 1,
      learning_rate: 1e-5,
      batch_size: 4,
      grad_accum: 1,
      lora_rank: 8,
      lora_alpha: 32,
      max_length: 16384,
      eval_steps: 200,
    },
  },
  {
    id: 'qualita',
    nomeKey: 'training.presetQualityName',
    percheKey: 'training.presetQualityWhy',
    cfg: {
      train_type: 'lora',
      epochs: 3,
      learning_rate: 5e-6,
      batch_size: 2,
      grad_accum: 4,
      lora_rank: 16,
      lora_alpha: 64,
      max_length: 16384,
      eval_steps: 200,
    },
  },
]

/** Il preset che corrisponde alla configurazione corrente, se ce n'è uno. */
export function matchPreset(cfg: TrainConfigBody): string | null {
  const p = PRESETS.find((preset) =>
    (Object.keys(preset.cfg) as Array<keyof TrainConfigBody>).every(
      (k) => cfg[k] === preset.cfg[k],
    ),
  )
  return p?.id ?? null
}