import type { Project, TrainConfigBody } from '../../lib/types'
import { trainTypeLabel } from '../../lib/vocab'
import { Collapsible, Field, Module } from '../../app/ui'
import { IconPlayground } from '../../app/icons'
import { BASE_CFG, matchPreset, PRESETS } from './presets'
import { useI18n } from '../../i18n'

function NumField({
  label,
  hint,
  value,
  onChange,
  step = 1,
}: {
  label: string
  hint?: string
  value: number
  onChange: (v: number) => void
  step?: number
}) {
  return (
    <Field label={label} hint={hint}>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="fld fld-mono"
      />
    </Field>
  )
}

interface TrainingConfigFormProps {
  projects: Project[]
  projectId: number | ''
  cfg: TrainConfigBody
  busy: boolean
  isActive: boolean
  stopArmed: boolean
  onProjectChange: (pid: number | '') => void
  onConfigChange: (patch: Partial<TrainConfigBody>) => void
  onStart: () => void
  onStop: () => void
}

export default function TrainingConfigForm({
  projects,
  projectId,
  cfg,
  busy,
  isActive,
  stopArmed,
  onProjectChange,
  onConfigChange,
  onStart,
  onStop,
}: TrainingConfigFormProps) {
  const { t } = useI18n()
  const set = onConfigChange
  const active = matchPreset(cfg)

  return (
    <div className="space-y-3">
      <Module tab={t('training.corpusPreset')}>
        <Field label={t('training.project')}>
          <select
            value={projectId}
            onChange={(e) => onProjectChange(e.target.value === '' ? '' : Number(e.target.value))}
            className="fld"
          >
            <option value="">{t('common.chooseProject')}</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>

        <fieldset className="mt-3 min-w-0">
          <legend className="lbl">{t('training.preset')}</legend>
          <div className="grid gap-px bg-[color:var(--color-rule)] sm:grid-cols-3">
            {PRESETS.map((p) => {
              const on = active === p.id
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => set(p.cfg)}
                  aria-pressed={on}
                  className={`flex flex-col gap-1 p-2 text-left ${
                    on
                      ? 'bg-[color:var(--color-sig-wash)] outline outline-2 -outline-offset-2 outline-[color:var(--color-sig)]'
                      : 'bg-[color:var(--color-sheet)] hover:bg-[color:var(--color-fill)]'
                  }`}
                >
                  <span className="text-[13px] font-semibold">{t(p.nomeKey)}</span>
                  <span className="text-[11px] leading-snug text-[color:var(--color-ink-2)]">
                    {t(p.percheKey)}
                  </span>
                </button>
              )
            })}
          </div>
          {active === null && (
            <p className="mt-1.5 text-[11px] text-[color:var(--color-ink-3)]">
              {t('training.customConfig')}
            </p>
          )}
        </fieldset>
      </Module>

      <Module tab={t('training.essentials')}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('training.baseModel')}>
            <select
              value={cfg.model}
              onChange={(e) => set({ model: e.target.value })}
              className="fld"
            >
              <option value="zenosai/MonkeyOCRv2-B-Parsing">MonkeyOCRv2-B-Parsing</option>
              <option value="zenosai/MonkeyOCRv2-S-Parsing">
                {t('training.modelLighter')}
              </option>
            </select>
          </Field>
          <Field label={t('training.trainType')}>
            <select
              value={cfg.train_type}
              onChange={(e) => set({ train_type: e.target.value as 'lora' | 'full' })}
              className="fld"
            >
              {(['lora', 'full'] as const).map((k) => (
                <option key={k} value={k}>
                  {trainTypeLabel(k)}
                </option>
              ))}
            </select>
          </Field>
          <NumField
            label={t('training.epochs')}
            hint={t('training.epochsHint')}
            value={cfg.epochs ?? BASE_CFG.epochs!}
            onChange={(v) => set({ epochs: v })}
            step={0.5}
          />
          <NumField
            label={t('training.lr')}
            hint={t('training.lrHint')}
            value={cfg.learning_rate ?? BASE_CFG.learning_rate!}
            onChange={(v) => set({ learning_rate: v })}
            step={1e-6}
          />
        </div>
      </Module>

      <Collapsible tab={t('training.advanced')} quiet aux={<span>{t('training.advancedCount')}</span>}>
        <p className="mb-3 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
          {t('training.advancedHint')}
        </p>

        {(cfg.train_type === 'lora' || !cfg.train_type) && (
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <NumField
              label={t('training.loraRank')}
              value={cfg.lora_rank ?? 8}
              onChange={(v) => set({ lora_rank: v })}
            />
            <NumField
              label={t('training.loraAlpha')}
              value={cfg.lora_alpha ?? 32}
              onChange={(v) => set({ lora_alpha: v })}
            />
            <div className="flex items-end pb-1.5">
              <label className="flex cursor-pointer items-center gap-2 text-[12px]">
                <input
                  type="checkbox"
                  checked={cfg.freeze_vit ?? true}
                  onChange={(e) => set({ freeze_vit: e.target.checked })}
                />
                {t('training.freezeVit')}
              </label>
            </div>
          </div>
        )}

        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          <NumField
            label={t('training.batch')}
            value={cfg.batch_size ?? 4}
            onChange={(v) => set({ batch_size: v })}
          />
          <NumField
            label={t('training.gradAccum')}
            value={cfg.grad_accum ?? 1}
            onChange={(v) => set({ grad_accum: v })}
          />
          <NumField
            label={t('training.evalEvery')}
            value={cfg.eval_steps ?? 200}
            onChange={(v) => set({ eval_steps: v })}
          />
        </div>

        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          <NumField
            label={t('training.maxLength')}
            value={cfg.max_length ?? 16384}
            onChange={(v) => set({ max_length: v })}
          />
          <NumField
            label={t('training.maxPixels')}
            value={cfg.max_pixels ?? 1003520}
            onChange={(v) => set({ max_pixels: v })}
          />
          <NumField label={t('training.processes')} value={cfg.nproc ?? 1} onChange={(v) => set({ nproc: v })} />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('training.gpu')} hint={t('training.gpuHint')}>
            <input
              value={cfg.gpus ?? '0'}
              onChange={(e) => set({ gpus: e.target.value })}
              className="fld fld-mono"
            />
          </Field>
          <Field
            label={t('training.modelPath')}
            hint={t('training.modelPathHint')}
          >
            <input
              value={cfg.model_path ?? ''}
              onChange={(e) => set({ model_path: e.target.value })}
              placeholder={t('training.modelPathPlaceholder')}
              className="fld fld-mono"
            />
          </Field>
        </div>
      </Collapsible>

      <Module tab={t('training.executor')}>
        <Field label={t('training.executorType')} hint={t('training.executorHint')}>
          <select
            value={cfg.executor ?? 'local'}
            onChange={(e) => set({ executor: e.target.value as TrainConfigBody['executor'] })}
            className="fld"
          >
            <option value="local">{t('training.executorLocal')}</option>
            <option value="ssh">{t('training.executorSsh')}</option>
            <option value="vast">{t('training.executorVast')}</option>
            <option value="runpod">{t('training.executorRunpod')}</option>
          </select>
        </Field>
        {cfg.executor && cfg.executor !== 'local' && <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label={t('training.sshHost')} hint={t('training.sshHostHint')}>
            <input value={cfg.ssh_host ?? ''} onChange={(e) => set({ ssh_host: e.target.value })} className="fld fld-mono" placeholder="gpu.example" />
          </Field>
          <Field label={t('training.sshUser')}>
            <input value={cfg.ssh_user ?? 'root'} onChange={(e) => set({ ssh_user: e.target.value })} className="fld fld-mono" />
          </Field>
          <NumField label={t('training.sshPort')} value={cfg.ssh_port ?? 22} onChange={(v) => set({ ssh_port: v })} />
          <Field label={t('training.sshKey')} hint={t('training.sshKeyHint')}>
            <input value={cfg.ssh_key_path ?? ''} onChange={(e) => set({ ssh_key_path: e.target.value })} className="fld fld-mono" placeholder="~/.ssh/id_ed25519" />
          </Field>
          <Field label={t('training.sshRoot')} hint={t('training.sshRootHint')}>
            <input value={cfg.ssh_root ?? '/tmp/tabularium-runs'} onChange={(e) => set({ ssh_root: e.target.value })} className="fld fld-mono" />
          </Field>
        </div>}
      </Module>

      <Module tab={t('training.launch')}>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={onStart}
            disabled={busy || projectId === '' || isActive}
            className="btn btn-primary"
          >
            <IconPlayground size={13} />
            {busy ? t('training.starting') : t('training.startTraining')}
          </button>
          <button onClick={onStop} disabled={!isActive} className="btn btn-danger">
            {stopArmed ? t('training.confirmStop') : t('training.stopRun')}
          </button>
          {stopArmed && (
            <span className="text-[11px] text-[color:var(--color-sig-text)]">
              {t('training.stopNote')}
            </span>
          )}
        </div>
        <p className="mt-2 max-w-[80ch] text-[11px] text-[color:var(--color-ink-3)]">
          {t('training.envNote', {
            env: 'monkeyocrv2-train',
            repo: 'TABULARIUM_TRAIN_REPO',
          })}
        </p>
      </Module>
    </div>
  )
}
