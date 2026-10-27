/**
 * The reference model, and therefore the default selection everywhere a model is
 * chosen before the user has chosen one: the inference form, the dataset builder, the
 * training preset and the cloud runner.
 *
 * It is a default, not a capability: the fact that MonkeyOCRv2-Parsing is the reference
 * model lives here once, and no screen should compare against it to decide behaviour.
 */
export const DEFAULT_MODEL_ID = 'monkeyocrv2-parsing'
