/** Initial form values and model-specific training configurations. */
import type { TrainConfigBody, TrainRecipeInfo } from '../../lib/types'
import { DEFAULT_MODEL_ID } from '../../lib/constants'

export const BASE_CFG: TrainConfigBody = {
  executor: 'local',
  ssh_user: 'root',
  ssh_port: 22,
  ssh_root: '/tmp/tabularium-runs',
  ssh_train_repo: '',
  ssh_python: '',
  resume_run_id: '',
  model: 'zenosai/MonkeyOCRv2-B-Parsing',
  adapter_id: DEFAULT_MODEL_ID,
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
  auto_prepare: true,
}

/** Measured MonkeyOCRv2 LoRA settings for an 8 GB CUDA GPU. */
export const MONKEYOCR_8GB: Partial<TrainConfigBody> = {
  train_type: 'lora',
  epochs: 1,
  batch_size: 1,
  grad_accum: 4,
  lora_rank: 8,
  lora_alpha: 32,
  max_length: 8192,
  eval_steps: 200,
}

/**
 * I default ufficiali del modello scelto, per il tipo di training scelto.
 *
 * Vive fuori dal componente perché è la regola che decide cosa viene
 * addestrato: `type_defaults` specializza il learning rate per LoRA/full
 * (GLM 1e-4/1e-5, PaddleFormers 5e-4/5e-6). Il preset non la sovrascrive.
 */
export function recipeDefaults(
  recipe: TrainRecipeInfo | undefined,
  trainType: 'lora' | 'full',
): Partial<TrainConfigBody> {
  if (!recipe) return { train_type: trainType }
  return {
    adapter_id: recipe.adapter_id,
    model: recipe.hf_repo,
    train_type: trainType,
    ...recipe.defaults,
    ...(recipe.type_defaults?.[trainType] ?? {}),
  }
}
