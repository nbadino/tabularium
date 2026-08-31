import { useState } from 'react'
import { Link } from 'react-router'
import { apiGet, apiPost } from '../lib/api'
import type { PageItem, PlaygroundResult } from '../lib/types'
import { blocks } from '../lib/vocab'
import { ErrorNotice, Field, Module } from '../app/ui'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useInference } from '../app/inference'
import { IconCopy, IconPlayground } from '../app/icons'
import { useI18n, tn } from '../i18n'

export default function PlaygroundPage() {
  const { t } = useI18n()
  const [projectId, setProjectId] = useState<number | ''>('')
  const [pages, setPages] = useState<PageItem[]>([])
  const [pageId, setPageId] = useState<number | null>(null)
  const inference = useInference()
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<PlaygroundResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [img, setImg] = useState<{ w: number; h: number } | null>(null)
  const [copied, setCopied] = useState(false)
  const [hovered, setHovered] = useState<number | null>(null)

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
    if (!projectId || !pageId) return
    setBusy(true)
    setError(null)
    setImg(null)
    try {
      const res = await apiPost<PlaygroundResult>('/playground/parse', {
        project_id: projectId,
        page_id: pageId,
        server_url: inference.url.trim() || null,
        model: inference.model.trim() || null,
      })
      setResult(res)
      const im = new Image()
      im.onload = () => setImg({ w: im.naturalWidth, h: im.naturalHeight })
      im.src = `/api/pages/${pageId}/preview`
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

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('playground.title')}
        </h1>
        <p className="mt-1 max-w-[80ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('playground.intro')}
        </p>
      </div>

      {!inference.enabled && (
        <div className="mb-3 flex items-center justify-between border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px] text-[color:var(--color-ink-2)]">
          <span>{t('cloud.card.inferenceDisabledNotice')}</span>
          {/* Un solo posto configura la GPU: la card Inferenza in Home. */}
          <Link to="/" className="btn btn-sm">
            {t('cloud.card.inferenceConfigure')}
          </Link>
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
                onChange={(e) => setPageId(e.target.value === '' ? null : Number(e.target.value))}
                disabled={pages.length === 0}
                className="fld"
              >
                <option value="">
                  {projectId === '' ? t('playground.pickProject') : t('playground.pickPage')}
                </option>
                {pages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.rel_path}
                  </option>
                ))}
              </select>
            </Field>

          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => void analyse()}
              disabled={busy || !pageId}
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
            {result && (
              <span className="mono ml-auto text-[11px] text-[color:var(--color-ink-3)]">
                {result.model} · {result.server}
              </span>
            )}
          </div>
        </Module>
      </div>

      {result && pageId ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Module tab={t('playground.predLayout')} quiet>
            {img ? (
              <figure className="m-0">
                <div className="lighttable relative overflow-hidden border border-[color:var(--color-rule)]">
                  <img
                    src={`/api/pages/${pageId}/preview`}
                    alt={t('playground.overlayAltPred')}
                    className="block w-full"
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
                        stroke="#e60012"
                        strokeWidth={hovered === i ? 3 : 1.4}
                      />
                    ))}
                  </svg>
                </div>
                <figcaption className="mt-1.5 text-[11px] text-[color:var(--color-ink-2)]">
                  {tn('playground.legend', result.items.length)}
                </figcaption>
              </figure>
            ) : (
              <p className="py-4 text-[12px] text-[color:var(--color-ink-2)]">
                {t('playground.previewLoading')}
              </p>
            )}
          </Module>

          <Module
            tab={t('playground.blocks')}
            quiet
            flush
            aux={<span>{blocks(result.items.length)}</span>}
          >
            <ol className="ruled max-h-[70vh] overflow-y-auto">
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
                    <span className="text-[12px] font-semibold">{it.label}</span>
                    <span className="mono ml-auto shrink-0 text-[11px] text-[color:var(--color-ink-3)]">
                      [{it.bbox_px.join(', ')}]
                    </span>
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