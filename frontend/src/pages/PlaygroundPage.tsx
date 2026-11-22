import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost, apiPostForm } from '../lib/api'
import type { PageItem, PlaygroundResult, PrefillEngines } from '../lib/types'
import { blocks } from '../lib/vocab'
import { ErrorNotice, Field, Loading, Module, Notice, PageHeader } from '../app/ui'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useInference } from '../app/inference'
import { projectPath } from '../app/projectPaths'
import { IconCopy, IconPlayground } from '../app/icons'
import { useI18n, tn } from '../i18n'
import { labelName } from '../i18n/labelName'
import { pageLabel } from '../lib/pageLabel'
import { useModelNames } from '../app/models/names'
import { API_BASE } from '../lib/endpoints'

function providerLabel(provider: string | null | undefined, t: (key: string) => string): string {
  if (provider && ['local', 'ssh', 'vast', 'runpod', 'modal', 'custom', 'llamaindex'].includes(provider)) {
    return t(`recognition.provider.${provider}`)
  }
  return provider || t('recognition.locationLocal')
}

export default function PlaygroundPage() {
  const { t } = useI18n()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [pages, setPages] = useState<PageItem[]>([])
  const [pageId, setPageId] = useState<number | null>(null)
  const inference = useInference()
  const nameOf = useModelNames()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<PlaygroundResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [img, setImg] = useState<{ w: number; h: number } | null>(null)
  const [copied, setCopied] = useState(false)
  const [hovered, setHovered] = useState<number | null>(null)
  const [showCoords, setShowCoords] = useState(false)
  const [uploaded, setUploaded] = useState<{ file: File; url: string } | null>(null)
  const uploadInputRef = useRef<HTMLInputElement>(null)
  /** Motore della prova: il modello servito oppure l'OCR locale, che
   *  funziona anche quando nessun endpoint risponde. */
  const [engine, setEngine] = useState<'model' | 'ocr'>('model')
  const [ocr, setOcr] = useState<PrefillEngines['ocr'] | null>(null)
  useEffect(() => {
    apiGet<PrefillEngines>('/system/prefill-engines')
      .then((out) => setOcr(out.ocr))
      .catch(() => setOcr(null))
  }, [])
  useEffect(() => {
    return () => {
      if (uploaded) URL.revokeObjectURL(uploaded.url)
    }
  }, [uploaded])

  const pickUpload = (file: File | null) => {
    if (!file) return
    const nextUrl = URL.createObjectURL(file)
    setUploaded({ file, url: nextUrl })
    setPageId(null)
    setResult(null)
    setImg(null)
    setError(null)
  }

  const clearTrial = () => {
    setUploaded(null)
    if (uploadInputRef.current) uploadInputRef.current.value = ''
    setResult(null)
    setImg(null)
    setError(null)
    setHovered(null)
  }

  const onProject = async (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setPages([])
    setPageId(null)
    setResult(null)
    if (pid === '') return
    try {
      const r = await apiGet<{ items: PageItem[] }>(`/projects/${pid}/pages`)
      setPages(r.items)
    } catch (e) {
      setError(e)
    }
  }

  const projects = useProjects((pid) => void onProject(pid), setError)

  const analyse = async () => {
    if (!uploaded && (!projectId || !pageId)) return
    setBusy(true)
    setError(null)
    setImg(null)
    try {
      let res: PlaygroundResult
      let src: string
      if (uploaded) {
        const form = new FormData()
        form.append('file', uploaded.file)
        form.append('engine', activeEngine)
        res = await apiPostForm<PlaygroundResult>('/playground/parse-upload', form)
        src = uploaded.url
      } else {
        res = await apiPost<PlaygroundResult>('/playground/parse', {
          project_id: projectId,
          page_id: pageId,
          engine: activeEngine,
        })
        src = `${API_BASE}/pages/${pageId}/preview`
      }
      setResult(res)
      const im = new Image()
      im.onload = () => setImg({ w: im.naturalWidth, h: im.naturalHeight })
      im.src = src
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const copyMd = async () => {
    if (!result) return
    const md = result.items
      .map((it) => (it.content ? `${it.label}: ${it.content}` : it.label))
      .join('\n\n')
    try {
      await navigator.clipboard.writeText(md)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard fallback */
    }
  }

  const kx = (v: number) => (img && img.w ? (v / 1000) * img.w : 0)
  const ky = (v: number) => (img && img.h ? (v / 1000) * img.h : 0)
  const inferenceReady = inference.enabled && inference.available
  // Senza endpoint che risponda si parte dall'OCR locale, se c'è: una prova
  // spenta per un modello irraggiungibile non dice nulla della pagina.
  const activeEngine: 'model' | 'ocr' = engine === 'model' && !inferenceReady && ocr?.available ? 'ocr' : engine
  const engineReady = activeEngine === 'ocr' ? Boolean(ocr?.available) : inferenceReady
  const previewSrc = uploaded ? uploaded.url : pageId ? `${API_BASE}/pages/${pageId}/preview` : ''

  return (
    <div className="p-3">
      <PageHeader title={t('playground.title')} />
      <div className="mb-3 flex flex-wrap items-center gap-3 border border-[color:var(--color-rule)] p-3">
        <p className="max-w-[85ch] text-[13px] text-[color:var(--color-ink-2)]">{t('workflow.tryHint')}</p>
        <Link
          to={projectId !== '' ? projectPath(projectId, 'riconoscimenti', pageId ? { project: projectId, pages: pageId } : undefined) : '/riconosci'}
          className="btn no-underline"
        >
          {t('workflow.saveRecognition')}
        </Link>
        <Link to={projectId !== '' ? `/progetti/${projectId}` : '/progetti'} className="btn btn-sm no-underline">{t('workflow.addPages')}</Link>
      </div>

      {activeEngine === 'model' && !inference.enabled && (
        <div className="mb-3 flex items-center justify-between border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px] text-[color:var(--color-ink-2)]">
          <span>{t('cloud.card.inferenceDisabledNotice')}</span>
          {/* Modello e destinazione si scelgono nello stesso flusso, dalla pagina Modelli. */}
          <Link to="/modelli" className="btn btn-sm">
            {inference.model ? t('recognition.changeModel') : t('recognition.chooseModel')}
          </Link>
        </div>
      )}
      {activeEngine === 'model' && inference.enabled && !inference.available && (
        <div className="mb-3">
          <Notice tone="warn">
            <span>{t('recognition.unreachableModel', { model: nameOf(inference.adapterId) ?? inference.model, url: inference.url })}</span>{' '}
            <Link to="/modelli" className="font-semibold underline underline-offset-2">
              {t('recognition.changeModel')}
            </Link>
          </Notice>
        </div>
      )}

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <div className="mb-3">
        <Module tab={t('playground.run')}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('playground.project')}>
              <select
                value={projectId}
                disabled={busy}
                onChange={(e) => void onProject(e.target.value === '' ? '' : Number(e.target.value))}
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
            <Field label={t('playground.page')}>
              <select
                value={pageId ?? ''}
                onChange={(e) => {
                  const next = e.target.value === '' ? null : Number(e.target.value)
                  setPageId(next)
                  if (next != null) {
                    setUploaded(null)
                    if (uploadInputRef.current) uploadInputRef.current.value = ''
                  }
                  setResult(null)
                  setImg(null)
                }}
                disabled={busy || pages.length === 0}
                className="fld"
              >
                <option value="">
                  {projectId === '' ? t('playground.pickProject') : t('playground.pickPage')}
                </option>
                {pages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {pageLabel(p)}
                  </option>
                ))}
              </select>
            </Field>

          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[color:var(--color-rule)] pt-3">
            <input
              ref={uploadInputRef}
              type="file"
              accept=".jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff"
              className="hidden"
              onChange={(e) => pickUpload(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => uploadInputRef.current?.click()}
            >
              {t('playground.uploadImage')}
            </button>
            {uploaded ? (
              <span className="mono text-[12px] text-[color:var(--color-ink-2)]">{uploaded.file.name}</span>
            ) : (
              <span className="text-[12px] text-[color:var(--color-ink-2)]">{t('playground.uploadHint')}</span>
            )}
          </div>
          <div className="mt-3 max-w-md">
            <Field label={t('playground.engine')} hint={activeEngine === 'ocr' ? t('playground.ocrNote') : undefined}>
              <select value={activeEngine} onChange={(e) => setEngine(e.target.value as 'model' | 'ocr')} className="fld">
                <option value="model" disabled={!inferenceReady}>
                  {t('playground.engineModel', { name: nameOf(inference.adapterId) ?? (inference.model || '—') })}
                  {!inferenceReady ? ` — ${t('recognition.modelUnavailable')}` : ''}
                </option>
                <option value="ocr" disabled={!ocr?.available}>
                  {t('playground.engineOcr', { engine: ocr?.engine ?? '—' })}
                </option>
              </select>
            </Field>
            {!inferenceReady && (
              <p className="mt-1 text-[11px] text-[color:var(--color-ink-2)]">
                {t('recognition.activeModelUnavailableHint')}{' '}
                <Link to="/modelli" className="font-semibold underline underline-offset-2">{t('recognition.configureInModels')}</Link>
              </p>
            )}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => void analyse()}
              disabled={busy || (!pageId && !uploaded) || !engineReady}
              className="btn btn-primary"
            >
              <IconPlayground size={13} />
              {busy ? t('playground.analysing') : t('playground.analyse')}
            </button>
            {result?.ok && (
              <button onClick={() => void copyMd()} className="btn">
                <IconCopy size={13} />
                {copied ? t('playground.copied') : t('playground.copyMd')}
              </button>
            )}
            {(result || uploaded || error != null) && (
              <button type="button" onClick={clearTrial} disabled={busy} className="btn">
                {t('playground.clear')}
              </button>
            )}
            {result && (
              <span className="mono ml-auto text-[11px] text-[color:var(--color-ink-3)]">
                {result.model} · {providerLabel(result.provider, t)}
              </span>
            )}
          </div>
        </Module>
      </div>

      {result && previewSrc ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Module tab={t('playground.predLayout')} quiet>
            {img ? (
              <figure className="m-0">
                <div className="lighttable flex justify-center overflow-hidden border border-[color:var(--color-rule)] p-1">
                  <div className="relative inline-block max-w-full">
                    <img
                      src={previewSrc}
                      alt={t('playground.overlayAltPred')}
                      className="block max-h-[68vh] w-auto max-w-full"
                    />
                    <svg
                      viewBox={`0 0 ${img.w} ${img.h}`}
                      className="absolute inset-0 h-full w-full"
                      aria-hidden="true"
                    >
                      {result.items.map((it, i) => (
                        <rect
                          key={i}
                          x={kx(it.bbox_norm[0])}
                          y={ky(it.bbox_norm[1])}
                          width={kx(it.bbox_norm[2] - it.bbox_norm[0])}
                          height={ky(it.bbox_norm[3] - it.bbox_norm[1])}
                          fill={hovered === i ? 'rgb(230 0 18 / 0.24)' : 'rgb(230 0 18 / 0.09)'}
                          stroke="#a3000e"
                          strokeWidth={hovered === i ? 3 : 1.4}
                        />
                      ))}
                    </svg>
                  </div>
                </div>
                <figcaption className="mt-1.5 text-[11px] text-[color:var(--color-ink-2)]">
                  {tn('playground.legend', result.items.length)}
                </figcaption>
              </figure>
            ) : (
              <div className="py-4">
                <Loading label={t('playground.previewLoading')} />
              </div>
            )}
          </Module>

          <Module
            tab={t('playground.blocks')}
            quiet
            flush
            aux={
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-pressed={showCoords}
                  onClick={() => setShowCoords((v) => !v)}
                >
                  {t('playground.toggleCoords')}
                </button>
                <span>{blocks(result.items.length)}</span>
              </div>
            }
          >
            <ol className="ruled max-h-[68vh] overflow-y-auto">
              {result.items.map((it, i) => (
                <li
                  key={i}
                  onMouseEnter={() => setHovered(i)}
                  onMouseLeave={() => setHovered(null)}
                  className={`px-2 py-1.5 ${hovered === i ? 'bg-[color:var(--color-sig-wash)]' : ''}`}
                >
                  <div className="flex items-center gap-2">
                    <span className="mono w-6 shrink-0 text-[11px] text-[color:var(--color-ink-3)]">
                      {i + 1}
                    </span>
                    <span className="text-[12px] font-semibold">{labelName(it.label)}</span>
                    {showCoords && (
                      <span className="mono ml-auto shrink-0 text-[11px] text-[color:var(--color-ink-3)]">
                        [{it.bbox_px.join(', ')}]
                      </span>
                    )}
                  </div>
                  {it.content && (
                    <p className="mt-1 pl-8 text-[12px] leading-relaxed text-[color:var(--color-ink-2)]">
                      {it.content}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </Module>
        </div>
      ) : (
        <Module tab={t('playground.noRun')} quiet>
          <div className="flex flex-col items-start gap-2 py-4">
            <IconPlayground size={22} />
            <p className="max-w-[70ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('playground.noRunBody')}
            </p>
          </div>
        </Module>
      )}
    </div>
  )
}
