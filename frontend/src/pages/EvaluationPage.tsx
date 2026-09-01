import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiPost } from '../lib/api'
import type { EvalPage, EvalReport } from '../lib/types'
import { pages } from '../lib/vocab'
import { ErrorNotice, Field, Module, WarnNotice } from '../app/ui'
import { PipelineStrip } from '../app/Pipeline'
import { buildPipeline, usePipelineState } from '../app/pipeline'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useInference } from '../app/inference'
import { IconEvaluate, IconPlayground } from '../app/icons'
import { useI18n, tn } from '../i18n'

const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
const num = (v: number | null | undefined, d = 3) => (v == null ? '—' : v.toFixed(d))

/** Una misura, con il suo significato accanto: un numero da solo non insegna nulla. */
function Metric({
  tab,
  value,
  sub,
  meaning,
}: {
  tab: string
  value: string
  sub?: string
  meaning?: string
}) {
  return (
    <Module tab={tab} quiet>
      <div className="mono text-[24px] font-semibold leading-none">{value}</div>
      {sub && <p className="mono mt-1 text-[11px] text-[color:var(--color-ink-3)]">{sub}</p>}
      {meaning && (
        <p className="mt-1 text-[11px] leading-snug text-[color:var(--color-ink-2)]">{meaning}</p>
      )}
    </Module>
  )
}

export default function EvaluationPage() {
  const { t } = useI18n()
  const [projectId, setProjectId] = useState<number | ''>('')
  const inference = useInference()
  const [withText, setWithText] = useState(true)
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<EvalReport | null>(null)
  const [selPage, setSelPage] = useState<EvalPage | null>(null)
  const [error, setError] = useState<unknown>(null)

  const onProject = (pid: number | '') => {
    setProjectId(pid)
    writeActiveProject(pid === '' ? null : pid)
    setReport(null)
    setSelPage(null)
  }

  const projects = useProjects(onProject, setError)

  const run = async () => {
    if (projectId === '') return
    setRunning(true)
    setError(null)
    setReport(null)
    try {
      const rep = await apiPost<EvalReport>(`/projects/${projectId}/evaluate`, {
        server_url: inference.url.trim() || null,
        model: inference.model.trim() || null,
        with_text: withText,
        limit: 50,
      })
      setReport(rep)
      setSelPage(rep.pages[0] ?? null)
    } catch (e) {
      setError(e)
    } finally {
      setRunning(false)
    }
  }

  const project = projects.find((p) => p.id === projectId) ?? null
  const { workflow, dataset, training } = usePipelineState(projectId === '' ? null : projectId)
  const stages = buildPipeline({ project, workflow, dataset, training })

  const a = report?.aggregates

  return (
    <div className="p-3">
      <div className="mb-3 border-b border-[color:var(--color-rule-strong)] pb-3">
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.03em]">
          {t('evaluate.title')}
        </h1>
        <p className="mt-1 max-w-[80ch] text-[13px] text-[color:var(--color-ink-2)]">
          {t('evaluate.intro')}
        </p>
      </div>

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {projectId !== '' && <PipelineStrip stages={stages} here="evaluate" />}

      {!inference.enabled && (
        <div className="mb-3 flex items-center justify-between border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px] text-[color:var(--color-ink-2)]">
          <span>{t('cloud.card.inferenceDisabledNotice')}</span>
          {/* Un solo posto configura la GPU: la card Inferenza in Impostazioni. */}
          <Link to="/impostazioni" className="btn btn-sm">
            {t('cloud.card.inferenceConfigure')}
          </Link>
        </div>
      )}

      <div className="mb-3">
        <Module tab={t('evaluate.run')}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('evaluate.project')}>
              <select
                value={projectId}
                onChange={(e) => onProject(e.target.value === '' ? '' : Number(e.target.value))}
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
            <div className="flex items-end pb-1.5">
              <label className="flex cursor-pointer items-center gap-2 text-[12px]">
                <input
                  type="checkbox"
                  checked={withText}
                  onChange={(e) => setWithText(e.target.checked)}
                />
                {t('evaluate.withText')}
              </label>
            </div>
          </div>
          <button
            onClick={() => void run()}
            disabled={running || projectId === ''}
            className="btn btn-primary mt-3"
          >
            <IconPlayground size={13} />
            {running ? t('evaluate.running') : t('evaluate.runBtn')}
          </button>
          {running && (
            <p className="mt-2 max-w-[80ch] text-[12px] text-[color:var(--color-ink-2)]">
              {t('evaluate.runningNote')}
            </p>
          )}
        </Module>
      </div>

      {report && a && (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Metric
              tab={t('evaluate.pagesEvaluated')}
              value={String(report.pages_evaluated)}
              sub={t('evaluate.ofVal', { n: report.val_pages })}
            />
            <Metric
              tab={t('evaluate.precision')}
              value={pct(a.layout.precision)}
              sub={t('evaluate.precisionSub', { matched: a.layout.matched, total: a.layout.n_pred })}
              meaning={t('evaluate.precisionMeaning')}
            />
            <Metric
              tab={t('evaluate.recall')}
              value={pct(a.layout.recall)}
              sub={t('evaluate.recallSub', { n: a.layout.n_gt })}
              meaning={t('evaluate.recallMeaning')}
            />
            <Metric
              tab={t('evaluate.order')}
              value={num(a.order.mean_levenshtein_norm)}
              sub={t('evaluate.orderSub', { pct: pct((a.order.exact_pct ?? 0) / 100) })}
              meaning={t('evaluate.orderMeaning')}
            />
            <Metric
              tab={t('evaluate.textCer')}
              value={num(a.text.mean_cer)}
              sub={t('evaluate.textCerSub', { n: a.text.n, wer: num(a.text.mean_wer) })}
              meaning={t('evaluate.textCerMeaning')}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <Metric
              tab={t('evaluate.iou')}
              value={num(a.layout.mean_iou_of_matched)}
              meaning={t('evaluate.iouMeaning')}
            />
            <Metric
              tab={t('evaluate.tablesStruct')}
              value={a.tables?.structure_ok_pct == null ? '—' : pct(a.tables.structure_ok_pct / 100)}
              sub={`n=${a.tables?.n ?? 0}`}
              meaning={t('evaluate.tablesStructMeaning')}
            />
            <Metric
              tab={t('evaluate.tablesText')}
              value={num(a.tables?.mean_cell_cer)}
              meaning={t('evaluate.tablesTextMeaning')}
            />
          </div>

          {report.warnings.length > 0 && (
            <WarnNotice title={tn('common.warningsCount', report.warnings.length)}>
              <ul className="list-inside list-disc space-y-0.5">
                {report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </WarnNotice>
          )}

          <div className="grid gap-3 lg:grid-cols-2">
            <Module
              tab={t('evaluate.valPages')}
              quiet
              flush
              aux={<span>{pages(report.pages.length)}</span>}
            >
              <ul className="ruled max-h-[28rem] overflow-y-auto">
                {report.pages.map((p) => (
                  <li key={p.page_id}>
                    <button
                      onClick={() => setSelPage(p)}
                      aria-pressed={selPage?.page_id === p.page_id}
                      className={`flex w-full items-center gap-2 px-2 py-1 text-left text-[12px] ${
                        selPage?.page_id === p.page_id
                          ? 'bg-[color:var(--color-sig-wash)] font-semibold'
                          : 'hover:bg-[color:var(--color-fill)]'
                      }`}
                    >
                      <span className="mono min-w-0 flex-1 truncate">{p.rel_path}</span>
                      {p.error ? (
                        <span className="shrink-0 text-[color:var(--color-sig-text)]">{t('evaluate.error')}</span>
                      ) : (
                        <span className="mono shrink-0 text-[color:var(--color-ink-2)]">
                          {p.layout.matched}/{p.layout.n_gt} {t('evaluate.ordShort', { n: p.order.levenshtein_norm.toFixed(2) })}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </Module>

            {selPage && (selPage.gt_items?.length || selPage.pred_items?.length) ? (
              <Module
                tab={t('evaluate.overlay')}
                quiet
                aux={<span className="mono truncate">{selPage.rel_path}</span>}
              >
                <PageOverlay
                  pageId={selPage.page_id}
                  gt={selPage.gt_items ?? []}
                  pred={selPage.pred_items ?? []}
                />
              </Module>
            ) : (
              <Module tab={t('evaluate.overlay')} quiet>
                <div className="flex flex-col items-start gap-2 py-4">
                  <IconEvaluate size={22} />
                  <p className="text-[12px] text-[color:var(--color-ink-2)]">
                    {t('evaluate.overlayEmpty')}
                  </p>
                </div>
              </Module>
            )}
          </div>

          {selPage && selPage.text && selPage.text.length > 0 && (
            <Module
              tab={t('evaluate.textRec')}
              quiet
              aux={<span>{tn('evaluate.firstLines', Math.min(10, selPage.text.length))}</span>}
              flush
            >
              <ul className="ruled">
                {selPage.text.slice(0, 10).map((tItem, i) => (
                  <li key={i} className="grid gap-1 px-2 py-1.5 text-[12px] sm:grid-cols-[8rem_1fr_1fr]">
                    <span className="lbl !mb-0">{tItem.label}</span>
                    <span className="mono">
                      <span className="text-[color:var(--color-ink-3)]">{t('evaluate.yours')}</span>
                      {tItem.gt.slice(0, 90)}
                    </span>
                    <span className="mono">
                      <span className="text-[color:var(--color-ink-3)]">{t('evaluate.predicted')}</span>
                      {tItem.hyp.slice(0, 90)}
                    </span>
                  </li>
                ))}
              </ul>
            </Module>
          )}

          {selPage && selPage.actions && selPage.actions.length > 0 && (
            <WarnNotice title={t('evaluate.suggestedActions')}>
              <ul className="list-inside list-disc space-y-0.5">
                {selPage.actions.map((action) => <li key={action}>{action}</li>)}
              </ul>
            </WarnNotice>
          )}
        </div>
      )}
    </div>
  )
}

function PageOverlay({
  pageId,
  gt,
  pred,
}: {
  pageId: number
  gt: Array<{ bbox: number[]; label: string }>
  pred: Array<{ bbox: number[]; label: string }>
}) {
  const { t } = useI18n()
  const [img, setImg] = useState<{ w: number; h: number } | null>(null)
  useEffect(() => {
    setImg(null)
    const im = new Image()
    im.onload = () => setImg({ w: im.naturalWidth, h: im.naturalHeight })
    im.src = `/api/pages/${pageId}/preview`
  }, [pageId])

  if (!img) {
    return (
      <p className="py-4 text-[12px] text-[color:var(--color-ink-2)]">
        {t('evaluate.overlayLoading')}
      </p>
    )
  }
  const k = (v: number) => (v / 1000) * img.w

  return (
    <figure className="m-0">
      <div className="lighttable relative overflow-hidden border border-[color:var(--color-rule)]">
        <img
          src={`/api/pages/${pageId}/preview`}
          alt={t('evaluate.overlayAlt')}
          className="block w-full"
        />
        <svg
          viewBox={`0 0 ${img.w} ${img.h}`}
          className="absolute inset-0 h-full w-full"
          aria-hidden="true"
        >
          {pred.map((it, i) => (
            <rect
              key={`p${i}`}
              x={k(it.bbox[0])}
              y={k(it.bbox[1])}
              width={k(it.bbox[2] - it.bbox[0])}
              height={k(it.bbox[3] - it.bbox[1])}
              fill="rgb(230 0 18 / 0.10)"
              stroke="#e60012"
              strokeWidth={1.4}
            />
          ))}
          {gt.map((it, i) => (
            <rect
              key={`g${i}`}
              x={k(it.bbox[0])}
              y={k(it.bbox[1])}
              width={k(it.bbox[2] - it.bbox[0])}
              height={k(it.bbox[3] - it.bbox[1])}
              fill="none"
              stroke="#111111"
              strokeWidth={1.2}
              strokeDasharray="5 3"
            />
          ))}
        </svg>
      </div>
      <figcaption className="mt-1.5 flex flex-wrap gap-4 text-[11px] text-[color:var(--color-ink-2)]">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-4 border border-[color:var(--color-sig)] bg-[rgb(230_0_18/0.10)]" />
          {t('evaluate.predLegend')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-4 border border-dashed border-[color:var(--color-ink)]" />
          {t('evaluate.gtLegend')}
        </span>
      </figcaption>
    </figure>
  )
}