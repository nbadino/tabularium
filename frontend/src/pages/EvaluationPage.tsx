import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { apiPost } from '../lib/api'
import type { EvalPage, EvalReport } from '../lib/types'
import { pages } from '../lib/vocab'
import { ErrorNotice, Field, Loading, Module, Notice, WarnNotice, PageHeader } from '../app/ui'
import { PipelineStrip } from '../app/PipelineView'
import { buildPipeline, usePipelineState } from '../app/pipeline'
import { useProjects, writeActiveProject } from '../app/activeProject'
import { useInference } from '../app/inference'
import { IconEvaluate, IconPlayground } from '../app/icons'
import { useI18n, tn } from '../i18n'
import { pageLabel } from '../lib/pageLabel'
import { useModelNames } from '../app/models/names'

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
  const nameOf = useModelNames()
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
  const { workflow, dataset, training } = usePipelineState(projectId === '' ? null : projectId, { runs: false })
  const stages = buildPipeline({ project, workflow, dataset, training })
  const trainingReady = training?.run?.state === 'finished'
  const inferenceReady = inference.enabled && inference.available
  // La verità sono le pagine approvate, non il training: misurare il modello
  // di partenza sul proprio corpus è il riferimento che il fine-tuning deve
  // battere, e serve anche a scegliere fra i modelli del catalogo.
  const goldPages = workflow?.approved_pages ?? 0
  const goldReady = goldPages > 0

  const a = report?.aggregates

  return (
    <div className="p-3">
      <PageHeader title={t('evaluate.title')} intro={t('evaluate.intro')} />

      {error != null && (
        <div className="mb-3">
          <ErrorNotice error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {projectId !== '' && goldReady && <PipelineStrip stages={stages} here="evaluate" />}

      {!inference.enabled && (
        <div className="mb-3 flex items-center justify-between border border-[color:var(--color-rule)] bg-[color:var(--color-fill)] px-3 py-2 text-[12px] text-[color:var(--color-ink-2)]">
          <span>{t('cloud.card.inferenceDisabledNotice')}</span>
          {/* Modello e destinazione si scelgono nello stesso flusso, dalla pagina Modelli. */}
          <Link to="/modelli" className="btn btn-sm">
            {inference.model ? t('recognition.changeModel') : t('recognition.chooseModel')}
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
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[color:var(--color-rule)] pt-2 text-[12px]">
            <span className="lbl !mb-0">{t('recognition.computeProfile')}</span>
            <strong title={inference.model || undefined}>{nameOf(inference.adapterId) ?? (inference.model || '—')}</strong>
            <span className="text-[color:var(--color-ink-2)]">
              · {inference.provider ? t(`recognition.provider.${inference.provider}`) : t('recognition.locationLocal')}
            </span>
            <Link to="/modelli" className="font-semibold no-underline">
              {inference.model ? t('recognition.changeModel') : t('recognition.chooseModel')}
            </Link>
          </div>
          {projectId !== '' && workflow && !goldReady && (
            <Notice tone="warn">
              <span>{t('evaluate.needsGold')}</span>{' '}
              <Link to={workflow.next_page ? `/annotazione?project=${projectId}&page=${workflow.next_page.id}` : '/annotazione'} className="font-semibold underline underline-offset-2">
                {t('dataset.material.openStudio')}
              </Link>
            </Notice>
          )}
          {projectId !== '' && goldReady && !trainingReady && (
            <Notice>{t('evaluate.baselineNote', { n: goldPages })}</Notice>
          )}
          {inference.enabled && !inferenceReady && (
            <Notice tone="warn">
              <span>{t('recognition.unreachableModel', { model: nameOf(inference.adapterId) ?? inference.model, url: inference.url })}</span>{' '}
              <Link to="/modelli" className="font-semibold underline underline-offset-2">
                {t('recognition.changeModel')}
              </Link>
            </Notice>
          )}
          <button
            onClick={() => void run()}
            disabled={running || projectId === '' || !goldReady || !inferenceReady}
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
              sub={t(report.scope === 'all_approved' ? 'evaluate.ofApproved' : 'evaluate.ofVal', { n: report.val_pages })}
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

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {a.layout_strict?.f1 != null && (
              <Metric
                tab={t('evaluate.strictF1')}
                value={pct(a.layout_strict.f1)}
                sub={t('evaluate.strictF1Sub', {
                  matched: a.layout_strict.matched,
                  gt: a.layout_strict.n_gt,
                  pred: a.layout_strict.n_pred,
                })}
                meaning={t('evaluate.strictF1Meaning')}
              />
            )}
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

          {a.layout_strict?.per_label && Object.keys(a.layout_strict.per_label).length > 0 && (
            <Module tab={t('evaluate.perLabel')} quiet flush>
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left text-[color:var(--color-ink-3)]">
                    <th className="px-2 py-1 font-normal">{t('evaluate.perLabelLabel')}</th>
                    <th className="px-2 py-1 text-right font-normal">F1</th>
                    <th className="px-2 py-1 text-right font-normal">{t('evaluate.perLabelFound')}</th>
                    <th className="px-2 py-1 text-right font-normal">{t('evaluate.perLabelInvented')}</th>
                  </tr>
                </thead>
                <tbody className="mono">
                  {Object.entries(a.layout_strict.per_label)
                    .sort(([, x], [, y]) => y.n_gt - x.n_gt)
                    .map(([label, m]) => (
                      <tr key={label} className="border-t border-[color:var(--color-rule)]">
                        <td className="px-2 py-1">{label}</td>
                        <td className="px-2 py-1 text-right">{pct(m.f1)}</td>
                        <td className="px-2 py-1 text-right">{m.matched}/{m.n_gt}</td>
                        <td className="px-2 py-1 text-right">{m.n_pred - m.matched}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
              <p className="px-2 py-1.5 text-[11px] text-[color:var(--color-ink-2)]">{t('evaluate.perLabelMeaning')}</p>
            </Module>
          )}

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
                      <span className="mono min-w-0 flex-1 truncate">{pageLabel(p)}</span>
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
                aux={<span className="mono truncate">{pageLabel(selPage)}</span>}
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
      <div className="py-4">
        <Loading label={t('evaluate.overlayLoading')} />
      </div>
    )
  }
  // I bbox sono 0–1000 per asse: x sulla larghezza, y sull'altezza.
  const kx = (v: number) => (v / 1000) * img.w
  const ky = (v: number) => (v / 1000) * img.h

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
              x={kx(it.bbox[0])}
              y={ky(it.bbox[1])}
              width={kx(it.bbox[2] - it.bbox[0])}
              height={ky(it.bbox[3] - it.bbox[1])}
              fill="rgb(230 0 18 / 0.10)"
              stroke="#a3000e"
              strokeWidth={1.4}
            />
          ))}
          {gt.map((it, i) => (
            <rect
              key={`g${i}`}
              x={kx(it.bbox[0])}
              y={ky(it.bbox[1])}
              width={kx(it.bbox[2] - it.bbox[0])}
              height={ky(it.bbox[3] - it.bbox[1])}
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
