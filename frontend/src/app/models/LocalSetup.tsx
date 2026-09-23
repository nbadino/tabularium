/**
 * Passo 3 dell'hub Modelli, destinazione locale: una lista di controllo.
 *
 * Tre voci in ordine — pesi, server, verifica — ognuna con il suo stato
 * scritto e un solo gesto. Una voce non si attiva finché la precedente non è
 * soddisfatta, così la pagina dice sempre qual è la prossima cosa da fare
 * invece di offrire sette pulsanti equivalenti.
 */
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { apiDelete, apiGet, apiPost } from '../../lib/api'
import { localRuntimeLabel } from '../../lib/vocab'
import { useI18n } from '../../i18n'
import { Module, Notice, Progress } from '../ui'
import { ChecklistItem, type ChecklistState } from './Checklist'
import { syncInferenceFromBackend } from '../inference'
import { IconWarn } from '../icons'
import { fmtBytes, fmtDuration, type ModelItem } from './registry'

/**
 * Stato del server locale. Due verità distinte, e la distinzione conta:
 * `running` = il processo è vivo, `ready` = l'endpoint risponde davvero.
 * Fra le due passano i minuti in cui vLLM carica i pesi.
 */
interface ServeStatus {
  running: boolean
  starting?: boolean
  ready?: boolean
  adapter_id: string | null
  port: number | null
  pid: number | null
  error: string | null
  phase?: string | null
  elapsed_s?: number | null
  log_tail?: string
}

interface HuggingFaceAuthState {
  state: 'disconnected' | 'awaiting_authorization' | 'connected' | 'error' | string
  verification_uri_complete?: string | null
  user_code?: string | null
  username?: string | null
  error?: string | null
}

/** Chiave i18n dell'etichetta di fase: nessun enum grezzo sullo schermo. */
const SERVE_PHASE_KEY: Record<string, string> = {
  preparing: 'cloud.models.servePhasePreparing',
  preparing_repo: 'cloud.models.servePhasePreparingRepo',
  preparing_runtime: 'cloud.models.servePhasePreparingRuntime',
  preparing_draft: 'cloud.models.servePhasePreparingDraft',
  preparing_image: 'cloud.models.servePhasePreparingImage',
  launching: 'cloud.models.servePhaseLaunching',
  loading: 'cloud.models.servePhaseLoading',
  ready: 'cloud.models.servePhaseReady',
  failed: 'cloud.models.servePhaseFailed',
}

/** Coda del log che resta incollata all'ultima riga: durante un'attesa lunga
 *  la riga viva è l'ultima, e doverla inseguire a mano è una piccola crudeltà. */
function LogTail({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null)
  useEffect(() => {
    const el = ref.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text])
  return (
    <pre
      ref={ref}
      className="mono mt-1 max-h-40 overflow-auto whitespace-pre-wrap border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] p-2 text-[11px] leading-relaxed"
    >
      {text}
    </pre>
  )
}

interface LocalSetupProps {
  model: ModelItem
  /** Rilegge il registro (download, cancellazione) forzando la cache. */
  onChanged: () => Promise<void>
}

export function LocalSetup({ model, onChanged }: LocalSetupProps) {
  const { t } = useI18n()
  const [serve, setServe] = useState<ServeStatus>({ running: false, adapter_id: null, port: null, pid: null, error: null })
  const [hf, setHf] = useState<HuggingFaceAuthState>({ state: 'disconnected' })
  const [port, setPort] = useState('8889')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [rate, setRate] = useState<number | null>(null)
  const firstSample = useRef<{ t: number; bytes: number } | null>(null)

  const mlx = model.local.runtime === 'mlx-vlm'

  const loadServe = async () => {
    try {
      setServe(await apiGet<ServeStatus>('/models/serve/status'))
    } catch {
      /* lo stato precedente resta visibile */
    }
  }
  const loadHf = async () => {
    try {
      setHf(await apiGet<HuggingFaceAuthState>('/models/huggingface/auth'))
    } catch {
      /* l'autenticazione Hub è accessoria */
    }
  }

  useEffect(() => {
    void loadServe()
    void loadHf()
    const id = setInterval(() => void loadServe(), 3000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (hf.state !== 'awaiting_authorization') return
    const id = setInterval(() => void loadHf(), 3000)
    return () => clearInterval(id)
  }, [hf.state])

  // Durante il download il registro si rilegge ogni 2 s; la velocità è la
  // media dal primo campione, non l'ultimo scatto.
  const runtimeInstalling = model.runtime_state === 'installing'
  useEffect(() => {
    if (!model.downloading && !runtimeInstalling) {
      firstSample.current = null
      setRate(null)
      return
    }
    const id = setInterval(() => void onChanged(), 2000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model.downloading, runtimeInstalling])

  useEffect(() => {
    if (!model.downloading) return
    const now = Date.now()
    const first = firstSample.current
    if (!first || model.size_bytes < first.bytes) {
      firstSample.current = { t: now, bytes: model.size_bytes }
      setRate(null)
      return
    }
    const seconds = (now - first.t) / 1000
    if (seconds >= 3) setRate((model.size_bytes - first.bytes) / seconds)
  }, [model.downloading, model.size_bytes])

  const thisServing = serve.adapter_id === model.adapter_id
  const ready = thisServing && !!serve.ready
  const starting = thisServing && !ready && (!!serve.starting || serve.running) && serve.phase !== 'failed'
  const failed = thisServing && serve.phase === 'failed'
  const otherServing = !thisServing && serve.adapter_id && (serve.running || serve.starting)

  // Quando il server diventa pronto il profilo attivo è già stato scritto dal
  // backend (`serve/start`): si riallinea l'indicatore globale.
  const wasReady = useRef(false)
  useEffect(() => {
    if (ready && !wasReady.current) void syncInferenceFromBackend().catch(() => {})
    wasReady.current = ready
  }, [ready])

  const weightsReady = mlx || model.installed
  const weightsState: ChecklistState = weightsReady
    ? 'done'
    : model.state === 'failed'
      ? 'failed'
      : 'active'
  const serverState: ChecklistState = !weightsReady
    ? 'waiting'
    : ready
      ? 'done'
      : failed
        ? 'failed'
        : 'active'
  const verifyState: ChecklistState = ready ? 'done' : 'waiting'

  const run = async (fn: () => Promise<unknown>, errorKey: string) => {
    setBusy(true)
    setNotice(null)
    try {
      await fn()
    } catch (e) {
      setNotice(t(errorKey, { error: String(e) }))
    } finally {
      setBusy(false)
    }
  }

  const download = () =>
    run(async () => {
      await apiPost(`/models/${model.adapter_id}/download`, {})
      await onChanged()
    }, 'cloud.models.downloadError')

  const cancelDownload = () =>
    run(async () => {
      await apiPost(`/models/${model.adapter_id}/download/cancel`, {})
      await onChanged()
    }, 'cloud.models.cancelError')

  const deleteWeights = () => {
    if (!confirmDelete) {
      setConfirmDelete(true)
      setTimeout(() => setConfirmDelete(false), 5000)
      return
    }
    setConfirmDelete(false)
    void run(async () => {
      await apiDelete(`/models/${model.adapter_id}`)
      await onChanged()
    }, 'cloud.models.deleteError')
  }

  const start = () =>
    run(async () => {
      const parsed = Number(port)
      if (!Number.isInteger(parsed) || parsed < 1024 || parsed > 65535) throw new Error(t('cloud.models.invalidPort'))
      setServe(await apiPost<ServeStatus>(`/models/${model.adapter_id}/serve/start`, { port: parsed }))
      await loadServe()
    }, 'cloud.models.serveError')

  const stop = () =>
    run(async () => {
      await apiPost('/models/serve/stop', {})
      await loadServe()
    }, 'cloud.models.serveError')

  const connectHf = async () => {
    // Il popup va riservato in modo sincrono dal click, o il browser blocca la
    // scheda OAuth aperta dopo la richiesta.
    const popup = window.open('', '_blank', 'noopener,noreferrer')
    try {
      const next = await apiPost<HuggingFaceAuthState>('/models/huggingface/auth/start', {})
      setHf(next)
      if (next.verification_uri_complete) {
        if (popup) popup.location.href = next.verification_uri_complete
        else window.open(next.verification_uri_complete, '_blank', 'noopener,noreferrer')
      } else popup?.close()
    } catch (e) {
      popup?.close()
      setNotice(String(e))
    }
  }

  const expected = model.expected_bytes ?? (model.approx_size_gb ? model.approx_size_gb * 1024 ** 3 : null)
  // Mai 100% finché il registro non dichiara installato.
  const pct = expected ? Math.min(99, (model.size_bytes / expected) * 100) : undefined
  const eta = rate && rate > 0 && expected ? Math.max(0, expected - model.size_bytes) / rate : null

  const hfLine = (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
      <span>
        {hf.state === 'connected'
          ? t('cloud.models.hfConnected', { username: hf.username || '' })
          : hf.state === 'awaiting_authorization'
            ? t('cloud.models.hfAwaiting', { code: hf.user_code || '' })
            : t('cloud.models.hfNotConnected')}
      </span>
      {hf.state === 'awaiting_authorization' && hf.verification_uri_complete && (
        <button type="button" className="btn btn-sm" onClick={() => window.open(hf.verification_uri_complete!, '_blank', 'noopener,noreferrer')}>
          {t('cloud.models.hfOpenLogin')}
        </button>
      )}
      {hf.state !== 'connected' && hf.state !== 'awaiting_authorization' && (
        <button type="button" className="btn btn-sm" onClick={() => void connectHf()}>
          {t('cloud.models.hfConnect')}
        </button>
      )}
      {hf.error && <span className="text-[color:var(--color-sig-text)]">{hf.error}</span>}
    </div>
  )

  return (
    <Module
      tab={`3 · ${t('modelsHub.step.configure')}`}
      aux={
        <span className="text-[11px] font-semibold">
          {model.display_name} · {t('recognition.provider.local')} · {localRuntimeLabel(model.local.runtime)}
        </span>
      }
    >
      {notice && <Notice tone="warn">{notice}</Notice>}
      {model.vram_warning && (
        <p className="mb-2 flex items-start gap-1 text-[11px] text-[color:var(--color-warn)]">
          <IconWarn size={12} />
          <span>{model.vram_warning}</span>
        </p>
      )}

      <ol>
        {/* 1 — Pesi */}
        <ChecklistItem
          n={1}
          title={t('modelsHub.local.weightsTitle')}
          state={weightsState}
          stateLabel={
            mlx
              ? t('modelsHub.local.weightsAuto')
              : model.installed
                ? t('cloud.models.installed', { size: fmtBytes(model.size_bytes) })
                : model.downloading
                  ? t('cloud.models.downloading', { size: fmtBytes(model.size_bytes) })
                  : model.state === 'failed'
                    ? t('cloud.models.failed')
                    : t('cloud.models.notInstalled')
          }
        >
          {mlx ? (
            <p>{t('modelsHub.local.weightsMlxBody', { repo: model.local.mlx_repo ?? model.hf_repo })}</p>
          ) : model.installed ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono text-[11px]">{model.path ?? model.hf_repo}</span>
              <button
                type="button"
                className={`btn btn-sm ${confirmDelete ? 'text-[color:var(--color-sig-text)]' : ''}`}
                disabled={busy || starting || ready}
                onClick={deleteWeights}
              >
                {confirmDelete ? t('cloud.models.confirmDelete') : t('modelsHub.local.deleteWeights')}
              </button>
            </div>
          ) : model.downloading ? (
            <div>
              <Progress
                value={pct}
                indeterminate={pct == null}
                label={t('cloud.models.downloadProgressAria', { name: model.display_name })}
              />
              <div className="mt-1 flex flex-wrap items-baseline gap-x-3 text-[11px]">
                <span className="mono">
                  {expected
                    ? t('cloud.models.downloadedOf', { done: fmtBytes(model.size_bytes), total: fmtBytes(expected) })
                    : t('cloud.models.downloadedSoFar', { done: fmtBytes(model.size_bytes) })}
                </span>
                {pct != null && <span className="mono">{Math.round(pct)}%</span>}
                {rate != null && <span className="mono">{t('cloud.models.downloadRate', { rate: fmtBytes(rate) })}</span>}
                <span>{eta != null ? t('cloud.models.downloadEta', { time: fmtDuration(eta) }) : t('cloud.models.downloadEtaUnknown')}</span>
                <button type="button" className="btn btn-sm ml-auto" disabled={busy} onClick={() => void cancelDownload()}>
                  {t('cloud.models.cancel')}
                </button>
              </div>
            </div>
          ) : (
            <div>
              <p>
                {t('modelsHub.local.weightsVllmBody', {
                  repo: model.hf_repo,
                  size: model.approx_size_gb != null ? `~${model.approx_size_gb} GB` : '—',
                })}
              </p>
              {model.checkpoint_detached && (
                <p className="mt-1 text-[color:var(--color-warn)]">{t('cloud.models.checkpointDetached')}</p>
              )}
              {model.downloadable !== false && (
                <button type="button" className="btn btn-primary mt-2" disabled={busy} onClick={() => void download()}>
                  {busy ? t('cloud.models.downloadStarting') : t('modelsHub.local.downloadCta')}
                </button>
              )}
            </div>
          )}
          {model.error && <p className="mt-1 text-[11px] text-[color:var(--color-sig-text)]">{model.error}</p>}
          {model.adapter_id === 'paddleocr-vl' && model.runtime_state && model.runtime_state !== 'ready' && (
            <p className="mt-1 text-[11px]">{t('cloud.models.paddleRuntime', { state: model.runtime_state })}</p>
          )}
          {model.runtime_error && <p className="mt-1 text-[11px] text-[color:var(--color-sig-text)]">{model.runtime_error}</p>}
          {/* L'account Hub non è un prerequisito: toglie i limiti dei download
              anonimi e apre i repo con licenza da accettare. */}
          {!weightsReady || mlx ? hfLine : null}
        </ChecklistItem>

        {/* 2 — Server */}
        <ChecklistItem
          n={2}
          title={t('modelsHub.local.serverTitle')}
          state={serverState}
          stateLabel={
            ready
              ? t('cloud.models.serving', { port: String(serve.port) })
              : starting
                ? t(SERVE_PHASE_KEY[serve.phase ?? 'preparing'] ?? 'cloud.models.servePhasePreparing')
                : failed
                  ? t('cloud.models.servePhaseFailed')
                  : weightsReady
                    ? t('modelsHub.local.serverStopped')
                    : t('modelsHub.local.waitingWeights')
          }
        >
          {otherServing && (
            <p className="mb-2 text-[color:var(--color-warn)]">
              {t('modelsHub.local.otherServing', { model: serve.adapter_id ?? '' })}
            </p>
          )}
          {ready || starting ? (
            <div>
              {starting && <Progress indeterminate label={t('cloud.models.serveProgressAria', { name: model.display_name })} />}
              <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px]">
                <span className="mono">http://127.0.0.1:{serve.port ?? port}/v1</span>
                {serve.elapsed_s != null && starting && (
                  <span className="mono text-[color:var(--color-ink-3)]">
                    {t('cloud.models.serveElapsed', { time: fmtDuration(serve.elapsed_s) })}
                  </span>
                )}
                <button type="button" className="btn btn-sm ml-auto" disabled={busy} onClick={() => void stop()}>
                  {t('cloud.models.serveStop')}
                </button>
              </div>
              {starting && <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('cloud.models.serveProgressHint')}</p>}
            </div>
          ) : (
            <div>
              <p>{t(mlx ? 'modelsHub.local.serverBodyMlx' : 'modelsHub.local.serverBodyVllm')}</p>
              <div className="mt-2 flex flex-wrap items-end gap-2">
                <label className="block">
                  <span className="lbl">{t('cloud.models.servePort')}</span>
                  <input
                    className="fld fld-mono w-24"
                    type="number"
                    min={1024}
                    max={65535}
                    value={port}
                    disabled={!weightsReady}
                    onChange={(e) => setPort(e.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={!weightsReady || busy || !model.local_serve_ready}
                  onClick={() => void start()}
                >
                  {busy ? t('cloud.models.serveStarting') : failed ? t('modelsHub.local.retry') : t('modelsHub.local.startCta')}
                </button>
              </div>
            </div>
          )}
          {thisServing && serve.error && <p className="mt-1 text-[11px] text-[color:var(--color-sig-text)]">{serve.error}</p>}
          {thisServing && serve.log_tail && (starting || failed) && (
            <details className="mt-1.5" open={failed}>
              <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-[0.04em] text-[color:var(--color-ink-3)]">
                {t('cloud.models.serveLog')}
              </summary>
              <LogTail text={serve.log_tail} />
            </details>
          )}
        </ChecklistItem>

        {/* 3 — Verifica */}
        <ChecklistItem
          n={3}
          title={t('modelsHub.local.readyTitle')}
          state={verifyState}
          stateLabel={ready ? t('recognition.modelReady') : t('modelsHub.local.notYet')}
        >
          {ready ? (
            <div className="flex flex-wrap items-center gap-2">
              <span>{t('modelsHub.local.readyBody', { model: model.display_name })}</span>
              <Link to="/" className="btn btn-primary no-underline">
                {t('modelsHub.goRecognize')}
              </Link>
              <Link to="/playground" className="btn no-underline">
                {t('modelsHub.tryPlayground')}
              </Link>
            </div>
          ) : (
            <p>{t('modelsHub.local.readyPending')}</p>
          )}
        </ChecklistItem>
      </ol>
    </Module>
  )
}
