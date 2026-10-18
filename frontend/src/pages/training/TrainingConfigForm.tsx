/**
 * Il form del training, nell'ordine in cui la decisione si prende davvero:
 * **dove si addestra → modello e preset → controlli e avvio**.
 *
 * Prima «dove» stava in fondo, dopo dodici iperparametri, e partiva da
 * «Locale» anche su un Mac senza CUDA: la risposta arrivava solo al clic,
 * come errore. Ora la destinazione è il primo passo e dichiara prima del
 * clic se questa macchina può ospitare il training; i controlli preliminari
 * del backend si leggono dal vivo accanto al pulsante, non dopo.
 */
import { useEffect, useState } from 'react'
import type { Project, TrainConfigBody, TrainRecipeInfo, TrainingPreflight } from '../../lib/types'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../../lib/api'
import { trainTypeLabel } from '../../lib/vocab'
import { Badge, Collapsible, Field, Module, Notice, SkeletonLines } from '../../app/ui'
import { IconPlayground } from '../../app/icons'
import { BASE_CFG, matchPreset, PRESETS, recipeDefaults } from './presets'
import { useI18n } from '../../i18n'

type Executor = NonNullable<TrainConfigBody['executor']>

const EXECUTORS: readonly Executor[] = ['local', 'vast', 'runpod', 'ssh']

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

interface VastInstance {
  id: number | string
  label: string
  is_running: boolean
  status: string
  dph_total: number | null
  ssh_host: string | null
  ssh_port: number | null
}

/**
 * Le istanze Vast.ai dell'account, se la chiave è già nel vault: scegliere
 * una GPU che si sta già pagando vale più di ricopiarne host e porta.
 */
function VastInstancePicker({ host, onPick }: { host: string; onPick: (inst: VastInstance) => void }) {
  const { t } = useI18n()
  const [state, setState] = useState<'loading' | 'nokey' | 'ready' | 'error'>('loading')
  const [items, setItems] = useState<VastInstance[]>([])
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const secret = await apiGet<{ configured: boolean }>('/system/secrets/vast_api_key')
        if (!secret.configured) {
          if (alive) setState('nokey')
          return
        }
        const res = await apiPost<{ items: VastInstance[] }>('/system/cloud/vast/instances', {
          credential_ref: 'vault:vast_api_key',
        })
        if (!alive) return
        setItems(res.items)
        setState('ready')
      } catch {
        if (alive) setState('error')
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  if (state === 'loading') return <SkeletonLines lines={4} label={t('common.loading')} />
  if (state === 'nokey' || state === 'error') {
    return (
      <p className="text-[12px] text-[color:var(--color-ink-2)]">
        {t(state === 'nokey' ? 'training.where.vastNoKey' : 'training.where.vastError')}{' '}
        <Link to="/modelli">{t('training.where.openModels')}</Link>
      </p>
    )
  }
  const running = items.filter((inst) => inst.is_running && inst.ssh_host && inst.ssh_port)
  if (running.length === 0) {
    return (
      <p className="text-[12px] text-[color:var(--color-ink-2)]">
        {t('training.where.vastNone')} <Link to="/modelli">{t('training.where.openModels')}</Link>
      </p>
    )
  }
  return (
    <div className="divide-y divide-[color:var(--color-rule)] border border-[color:var(--color-rule)]">
      {running.map((inst) => {
        const chosen = inst.ssh_host === host
        return (
          <div key={inst.id} className={`flex flex-wrap items-center gap-3 p-2 ${chosen ? 'bg-[color:var(--color-sig-wash)]' : ''}`}>
            <div className="min-w-0 flex-1">
              <span className="font-semibold">{inst.label}</span>
              {inst.dph_total != null && <span className="mono ml-2 text-[11px]">${inst.dph_total.toFixed(3)}/h</span>}
              <div className="mono text-[11px] text-[color:var(--color-ink-3)]">
                ID {inst.id} · {inst.ssh_host}:{inst.ssh_port}
              </div>
            </div>
            {chosen ? (
              <Badge tone="ok">{t('training.where.instanceChosen')}</Badge>
            ) : (
              <button type="button" className="btn btn-sm btn-primary" onClick={() => onPick(inst)}>
                {t('training.where.useInstance')}
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

interface TrainingConfigFormProps {
  projects: Project[]
  projectId: number | ''
  cfg: TrainConfigBody
  busy: boolean
  isActive: boolean
  datasetReady: boolean
  stopArmed: boolean
  /** Questa macchina ha una GPU NVIDIA visibile (nvidia-smi). */
  localGpu: boolean
  /** Controlli preliminari del backend sulla configurazione corrente. */
  preflight: TrainingPreflight | null
  preflightLoading: boolean
  onProjectChange: (pid: number | '') => void
  onConfigChange: (patch: Partial<TrainConfigBody>) => void
  onStart: () => void
  onStop: () => void
}

/** La destinazione effettiva: «Locale» senza GPU non è una scelta, è un default vuoto. */
export function effectiveExecutor(cfg: TrainConfigBody, localGpu: boolean): Executor | null {
  const chosen = cfg.executor ?? 'local'
  return chosen === 'local' && !localGpu ? null : chosen
}

export default function TrainingConfigForm({
  projects,
  projectId,
  cfg,
  busy,
  isActive,
  datasetReady,
  stopArmed,
  localGpu,
  preflight,
  preflightLoading,
  onProjectChange,
  onConfigChange,
  onStart,
  onStop,
}: TrainingConfigFormProps) {
  const { t } = useI18n()
  const set = onConfigChange
  const active = matchPreset(cfg)
  const [recipes, setRecipes] = useState<TrainRecipeInfo[]>([])
  useEffect(() => {
    apiGet<{ items: TrainRecipeInfo[] }>('/system/train-recipes')
      .then((o) => setRecipes(o.items))
      .catch(() => {})
  }, [])
  const recipe =
    recipes.find((r) => r.adapter_id === cfg.adapter_id) ??
    recipes.find((r) => r.adapter_id === 'monkeyocrv2-parsing')
  // `planned`/`inference-only` restano nella lista: spiegarne il motivo è
  // meglio che nasconderli e lasciare credere che il modello non esista.
  const recipeItems = recipes.filter((r) => r.trainable || r.maturity === 'planned')
  const recipeReady = recipe != null && recipe.trainable && recipe.script_ready

  const chooseAdapter = (adapterId: string) => {
    const next = recipes.find((r) => r.adapter_id === adapterId)
    if (!next) return
    const trainType = (next.train_types.includes(cfg.train_type ?? 'lora')
      ? cfg.train_type
      : next.train_types[0]) ?? 'lora'
    set(recipeDefaults(next, trainType as 'lora' | 'full'))
  }

  const chooseTrainType = (trainType: 'lora' | 'full') => {
    if (recipe) set(recipeDefaults(recipe, trainType))
    else set({ train_type: trainType })
  }
  const [rentBusy, setRentBusy] = useState(false)
  const [rentNotice, setRentNotice] = useState<string | null>(null)
  const [rentDph, setRentDph] = useState('1')
  const [rentVram, setRentVram] = useState('16')

  // Noleggia e prepara da sé un host Vast per il training (M18): cerca l'offerta
  // più economica, la noleggia con `prepare_training`, attende l'endpoint SSH e
  // compila i campi da sé. `main` = ultima versione su GitHub (nessuna release).
  const rentTraining = async () => {
    setRentBusy(true)
    setRentNotice(null)
    try {
      const out = await apiPost<{ instance_id: number; ssh: { user: string; python: string; train_repo: string } }>(
        '/system/cloud/vast/rent-training',
        { tabularium_ref: 'main', max_dph: Number(rentDph) || 1, min_gpu_ram_gb: Number(rentVram) || 16 },
      )
      set({ ssh_user: out.ssh.user, ssh_python: out.ssh.python, ssh_train_repo: out.ssh.train_repo })
      setRentNotice(t('training.where.rentWaiting'))
      // Attende che l'istanza esponga l'SSH (può metterci qualche minuto).
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => window.setTimeout(r, 10000))
        const list = await apiGet<{ items: Array<{ id: number; ssh_direct_host: string | null; ssh_host: string | null; ssh_port: number | null }> }>('/system/cloud/vast/instances')
        const inst = list.items.find((x) => Number(x.id) === Number(out.instance_id))
        const host = inst?.ssh_direct_host || inst?.ssh_host
        if (host) {
          set({ ssh_host: host, ssh_port: inst?.ssh_port ?? 22 })
          setRentNotice(t('training.where.rentReady'))
          break
        }
      }
    } catch (e) {
      setRentNotice(String(e))
    } finally {
      setRentBusy(false)
    }
  }
  const executor = effectiveExecutor(cfg, localGpu)
  const remote = executor !== null && executor !== 'local'
  const canStart =
    !busy && projectId !== '' && !isActive && datasetReady && executor !== null && preflight?.ready === true && recipeReady

  return (
    <div className="space-y-3">
      <Module tab={t('training.project')}>
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
      </Module>

      {/* 1 — Dove si addestra */}
      <Module tab={`1 · ${t('training.where.title')}`}>
        <p className="max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">{t('training.where.intro')}</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {EXECUTORS.map((kind) => {
            const blocked = kind === 'local' && !localGpu
            const on = executor === kind
            return (
              <button
                key={kind}
                type="button"
                disabled={blocked}
                aria-pressed={on}
                onClick={() => set({ executor: kind })}
                className={`flex flex-col border p-3 text-left disabled:cursor-not-allowed disabled:bg-[color:var(--color-fill)] ${
                  on
                    ? 'border-[color:var(--color-sig)] bg-[color:var(--color-sig-wash)] outline outline-1 outline-[color:var(--color-sig)]'
                    : 'border-[color:var(--color-rule)] bg-[color:var(--color-sheet)] hover:border-[color:var(--color-ink)] disabled:hover:border-[color:var(--color-rule)]'
                }`}
              >
                <span className={`flex items-center gap-2 text-[14px] font-bold ${blocked ? 'text-[color:var(--color-ink-3)]' : ''}`}>
                  {t(`training.where.${kind}`)}
                  {!localGpu && kind === 'vast' && <Badge tone="ok">{t('modelsHub.recommended')}</Badge>}
                </span>
                <span className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">
                  {blocked ? t('training.where.localBlocked') : t(`training.where.${kind}Body`)}
                </span>
              </button>
            )
          })}
        </div>

        {remote && (
          <div className="mt-3 space-y-3 border-t border-[color:var(--color-rule)] pt-3">
            {executor === 'vast' && (
              <div>
                <span className="lbl">{t('training.where.vastPick')}</span>
                <VastInstancePicker
                  host={cfg.ssh_host ?? ''}
                  onPick={(inst) => set({ ssh_host: inst.ssh_host ?? '', ssh_port: inst.ssh_port ?? 22, ssh_user: 'root' })}
                />
              </div>
            )}
            {executor === 'vast' && (
              <div className="border border-[color:var(--color-rule)] p-2">
                <span className="lbl">{t('training.where.rentTraining')}</span>
                <p className="mb-2 text-[11px] text-[color:var(--color-ink-3)]">{t('training.where.rentTrainingHint')}</p>
                <div className="flex flex-wrap items-end gap-2">
                  <Field label={t('training.where.rentMaxDph')}>
                    <input className="fld !w-24" value={rentDph} onChange={(e) => setRentDph(e.target.value)} />
                  </Field>
                  <Field label={t('training.where.rentMinVram')}>
                    <input className="fld !w-24" value={rentVram} onChange={(e) => setRentVram(e.target.value)} />
                  </Field>
                  <button type="button" className="btn btn-sm" disabled={rentBusy} onClick={() => void rentTraining()}>
                    {rentBusy ? t('training.where.rentBusy') : t('training.where.rentGo')}
                  </button>
                </div>
                {rentNotice && <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">{rentNotice}</p>}
              </div>
            )}
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label={t('training.sshHost')} hint={t('training.sshHostHint')}>
                <input value={cfg.ssh_host ?? ''} onChange={(e) => set({ ssh_host: e.target.value })} className="fld fld-mono" placeholder="gpu.example" />
              </Field>
              <NumField label={t('training.sshPort')} value={cfg.ssh_port ?? 22} onChange={(v) => set({ ssh_port: v })} />
              <Field label={t('training.sshUser')}>
                <input value={cfg.ssh_user ?? 'root'} onChange={(e) => set({ ssh_user: e.target.value })} className="fld fld-mono" />
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('training.sshTrainRepo')} hint={t('training.sshTrainRepoHint')}>
                <input value={cfg.ssh_train_repo ?? ''} onChange={(e) => set({ ssh_train_repo: e.target.value })} className="fld fld-mono" placeholder="/root/MonkeyOCRv2/parsing/train" />
              </Field>
              <Field label={t('training.sshPython')} hint={t('training.sshPythonHint')}>
                <input value={cfg.ssh_python ?? ''} onChange={(e) => set({ ssh_python: e.target.value })} className="fld fld-mono" placeholder="/opt/venv/bin/python" />
              </Field>
            </div>
            <p className="text-[11px] text-[color:var(--color-ink-3)]">{t('training.where.remoteEnvNote')}</p>
            <details>
              <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
                {t('training.where.sshAdvanced')}
              </summary>
              <div className="mt-2 grid gap-3 sm:grid-cols-2">
                <Field
                  label={t('training.sshKey')}
                  hint={executor === 'ssh' ? t('training.sshKeyHint') : t('training.where.keyDefault')}
                >
                  <input value={cfg.ssh_key_path ?? ''} onChange={(e) => set({ ssh_key_path: e.target.value })} className="fld fld-mono" placeholder="~/.ssh/id_ed25519" />
                </Field>
                <Field label={t('training.sshRoot')} hint={t('training.sshRootHint')}>
                  <input value={cfg.ssh_root ?? '/tmp/tabularium-runs'} onChange={(e) => set({ ssh_root: e.target.value })} className="fld fld-mono" />
                </Field>
              </div>
            </details>
          </div>
        )}
      </Module>

      {/* 2 — Modello e preset */}
      <Module tab={`2 · ${t('training.corpusPreset')}`}>
        <div className="grid gap-3 sm:grid-cols-2">
          {/* Il modello *è* la ricetta: scegliere un adapter cambia toolchain,
              tipi di training ammessi e default. Niente più deduzione dal
              nome del checkpoint (una stringa che sbagliava MinerU, dots.ocr
              e PaddleOCR-VL). */}
          <Field label={t('training.baseModel')} hint={t('training.baseModelHint')}>
            <select
              value={recipe?.adapter_id ?? cfg.adapter_id ?? ''}
              onChange={(e) => chooseAdapter(e.target.value)}
              className="fld"
            >
              {recipeItems.map((r) => (
                <option key={r.adapter_id} value={r.adapter_id} disabled={!r.trainable}>
                  {r.display_name}
                  {!r.trainable ? ` · ${t('training.recipeNotTrainable')}` : ''}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t('training.trainType')}>
            <select
              value={cfg.train_type}
              onChange={(e) => chooseTrainType(e.target.value as 'lora' | 'full')}
              className="fld"
              disabled={!recipe || recipe.train_types.length <= 1}
            >
              {(recipe?.train_types ?? (['lora', 'full'] as const)).map((k) => (
                <option key={k} value={k}>
                  {trainTypeLabel(k)}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {recipe && (
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[color:var(--color-ink-3)]">
            <Badge tone={recipe.maturity === 'verified' ? 'ok' : 'neutral'}>{recipe.maturity}</Badge>
            <span className="mono">{recipe.toolchain}</span>
            {recipe.official_doc && (
              <a href={recipe.official_doc} target="_blank" rel="noreferrer" className="underline underline-offset-2">
                {t('training.officialDoc')}
              </a>
            )}
            {recipe.verified_on && <span>{recipe.verified_on}</span>}
          </div>
        )}
        {recipe && !recipeReady && (
          <div className="mt-2">
            <Notice tone="warn">
              {recipe.note || t('training.recipeNotReady', { toolchain: recipe.toolchain })}
            </Notice>
          </div>
        )}
        {recipe?.official_notes?.length ? (
          <details className="mt-2">
            <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-2)]">
              {t('training.officialNotes')}
            </summary>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-[color:var(--color-ink-2)]">
              {recipe.official_notes.map((note) => <li key={note}>{note}</li>)}
            </ul>
          </details>
        ) : null}

        <fieldset className="mt-3 min-w-0">
          <legend className="lbl">{t('training.preset')}</legend>
          <div className="grid gap-px bg-[color:var(--color-rule)] sm:grid-cols-2">
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
                  <span className="text-[11px] leading-snug text-[color:var(--color-ink-2)]">{t(p.percheKey)}</span>
                </button>
              )
            })}
          </div>
          {active === null && (
            <p className="mt-1.5 text-[11px] text-[color:var(--color-ink-3)]">{t('training.customConfig')}</p>
          )}
        </fieldset>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
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
        <p className="mb-3 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">{t('training.advancedHint')}</p>

        {(cfg.train_type === 'lora' || !cfg.train_type) && (
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <NumField label={t('training.loraRank')} value={cfg.lora_rank ?? 8} onChange={(v) => set({ lora_rank: v })} />
            <NumField label={t('training.loraAlpha')} value={cfg.lora_alpha ?? 32} onChange={(v) => set({ lora_alpha: v })} />
            {/* `freeze_vit` esiste solo per i modelli che hanno un ViT
                separato nel toolchain (MonkeyOCRv2). Mostrarlo per un
                modello senza ViT sarebbe un flag che non fa nulla. */}
            {recipe && 'freeze_vit' in recipe.flags && (
              <div className="flex items-end pb-1.5">
                <label className="flex cursor-pointer items-center gap-2 text-[12px]">
                  <input type="checkbox" checked={cfg.freeze_vit ?? true} onChange={(e) => set({ freeze_vit: e.target.checked })} />
                  {t('training.freezeVit')}
                </label>
              </div>
            )}
          </div>
        )}

        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          <NumField label={t('training.batch')} value={cfg.batch_size ?? 4} onChange={(v) => set({ batch_size: v })} />
          <NumField label={t('training.gradAccum')} value={cfg.grad_accum ?? 1} onChange={(v) => set({ grad_accum: v })} />
          <NumField label={t('training.evalEvery')} value={cfg.eval_steps ?? 200} onChange={(v) => set({ eval_steps: v })} />
        </div>

        <div className="mb-3 grid gap-3 sm:grid-cols-3">
          <NumField label={t('training.maxLength')} value={cfg.max_length ?? 16384} onChange={(v) => set({ max_length: v })} />
          <NumField label={t('training.maxPixels')} value={cfg.max_pixels ?? 1003520} onChange={(v) => set({ max_pixels: v })} />
          <NumField label={t('training.processes')} value={cfg.nproc ?? 1} onChange={(v) => set({ nproc: v })} />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('training.gpu')} hint={t('training.gpuHint')}>
            <input value={cfg.gpus ?? '0'} onChange={(e) => set({ gpus: e.target.value })} className="fld fld-mono" />
          </Field>
          <Field label={t('training.modelPath')} hint={t('training.modelPathHint')}>
            <input
              value={cfg.model_path ?? ''}
              onChange={(e) => set({ model_path: e.target.value })}
              placeholder={t('training.modelPathPlaceholder')}
              className="fld fld-mono"
            />
          </Field>
        </div>
        {/* Il checkpoint della ricetta è il default, non un vincolo: la
            variante più piccola dello stesso modello (MonkeyOCRv2-S) o un
            repo già scaricato si addestrano cambiando questa stringa. */}
        <Field label={t('training.checkpoint')} hint={t('training.checkpointHint')}>
          <input
            value={cfg.model ?? ''}
            onChange={(e) => set({ model: e.target.value })}
            placeholder={recipe?.hf_repo ?? ''}
            className="fld fld-mono"
          />
        </Field>
        <Field label={t('training.resumeRun')} hint={t('training.resumeRunHint')}>
          <input
            value={cfg.resume_run_id ?? ''}
            onChange={(e) => set({ resume_run_id: e.target.value })}
            className="fld fld-mono"
            placeholder="run_..."
          />
        </Field>
      </Collapsible>

      {/* 3 — Controlli e avvio */}
      <Module tab={`3 · ${t('training.launch')}`}>
        {projectId !== '' && !datasetReady && (
          <Notice tone="warn">
            <span>{t('pipeline.steps.trainNeeds')}</span>{' '}
            <Link to="/dataset" className="font-semibold underline underline-offset-2">
              {t('pipeline.steps.datasetAction')}
            </Link>
          </Notice>
        )}
        {projectId !== '' && executor === null && <Notice tone="warn">{t('training.where.chooseFirst')}</Notice>}

        {/* I controlli del backend, letti prima del clic: ciò che bloccherà
            il run è scritto qui, con la causa, e il pulsante resta spento. */}
        {projectId !== '' && executor !== null && (
          <div className="mt-2">
            <span className="lbl">{t('training.checks.title')}</span>
            {preflightLoading && !preflight ? (
              <p className="text-[12px] text-[color:var(--color-ink-3)]">{t('training.checks.running')}</p>
            ) : preflight ? (
              <ul className="space-y-1 text-[12px]">
                {preflight.errors.map((error) => (
                  <li key={error} className="flex gap-2">
                    <Badge tone="warn">{t('training.checks.blocking')}</Badge>
                    <span>{error}</span>
                  </li>
                ))}
                {preflight.warnings.map((warning) => (
                  <li key={warning} className="flex gap-2">
                    <Badge tone="neutral">{t('training.checks.warning')}</Badge>
                    <span className="text-[color:var(--color-ink-2)]">{warning}</span>
                  </li>
                ))}
                {preflight.ready && preflight.errors.length === 0 && (
                  <li className="flex gap-2">
                    <Badge tone="ok">{t('training.checks.ok')}</Badge>
                    <span>
                      {t('training.checks.okBody', {
                        train: String(preflight.dataset.counts['train.jsonl'] ?? 0),
                        val: String(preflight.dataset.counts['val.jsonl'] ?? 0),
                      })}
                    </span>
                  </li>
                )}
              </ul>
            ) : null}
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button onClick={onStart} disabled={!canStart} className="btn btn-primary">
            <IconPlayground size={13} />
            {busy ? t('training.starting') : t('training.startTraining')}
          </button>
          <button onClick={onStop} disabled={!isActive} className="btn btn-danger">
            {stopArmed ? t('training.confirmStop') : t('training.stopRun')}
          </button>
          {!isActive && cfg.resume_run_id && (
            <span className="mono text-[11px] text-[color:var(--color-ink-2)]">
              {t('training.resumeConfigured')}: {cfg.resume_run_id}
            </span>
          )}
          {stopArmed && <span className="text-[11px] text-[color:var(--color-sig-text)]">{t('training.stopNote')}</span>}
        </div>
        {executor === 'local' && recipe && (
          <p className="mt-2 max-w-[80ch] text-[11px] text-[color:var(--color-ink-3)]">
            {t(`training.envNote.${recipe.toolchain}`, {
              env: 'monkeyocrv2-train',
              repo: 'TABULARIUM_TRAIN_REPO',
            })}
          </p>
        )}
      </Module>
    </div>
  )
}
