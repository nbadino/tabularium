import { useEffect, useMemo, useRef, useState } from 'react'
import { ErrorNotice, Modal, Module } from '../../app/ui'
import { IconCheck, IconClose } from '../../app/icons'
import { apiDelete, apiGet, apiPost } from '../../lib/api'
import type { TransformEngine, TransformState } from '../../lib/types'
import { useI18n } from '../../i18n'

type Point = [number, number]

interface Props {
  pageId: number
  width: number
  height: number
  hasBlocks: boolean
  onClose: () => void
  onAccepted: () => Promise<void> | void
}

const perspectiveDefault: Point[] = [
  [0.03, 0.03],
  [0.97, 0.03],
  [0.97, 0.97],
  [0.03, 0.97],
]

function makeMesh(rows = 4, cols = 4): Point[][] {
  return Array.from({ length: rows }, (_, r) =>
    Array.from({ length: cols }, (_, c) => [c / (cols - 1), r / (rows - 1)] as Point),
  )
}

export default function PageTransformReview({
  pageId,
  width,
  height,
  hasBlocks,
  onClose,
  onAccepted,
}: Props) {
  const { t } = useI18n()
  const [state, setState] = useState<TransformState | null>(null)
  const [engine, setEngine] = useState<TransformEngine>('uvdoc')
  const [perspective, setPerspective] = useState<Point[]>(perspectiveDefault)
  const [mesh, setMesh] = useState<Point[][]>(() => makeMesh())
  const [drag, setDrag] = useState<{ kind: 'perspective' | 'mesh'; index: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown | null>(null)
  const overlayRef = useRef<SVGSVGElement>(null)

  const refresh = async () => {
    const next = await apiGet<TransformState>(`/pages/${pageId}/transform`)
    setState(next)
    if (next.engines?.uvdoc?.available === false) setEngine('deskew')
  }

  useEffect(() => {
    void refresh().catch(setError)
  }, [pageId])

  const flatMesh = useMemo(() => mesh.flat(), [mesh])
  const manual = engine === 'perspective' || engine === 'mesh'

  const updateDraggedPoint = (clientX: number, clientY: number) => {
    if (!drag || !overlayRef.current) return
    const rect = overlayRef.current.getBoundingClientRect()
    const point: Point = [
      Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (clientY - rect.top) / rect.height)),
    ]
    if (drag.kind === 'perspective') {
      setPerspective((current) => current.map((p, i) => (i === drag.index ? point : p)))
      return
    }
    const cols = mesh[0].length
    const row = Math.floor(drag.index / cols)
    const col = drag.index % cols
    setMesh((current) =>
      current.map((line, r) => line.map((p, c) => (r === row && c === col ? point : p))),
    )
  }

  const generate = async () => {
    setBusy(true)
    setError(null)
    try {
      const body: Record<string, unknown> = { engine }
      if (engine === 'perspective') {
        body.perspective_points = perspective.map(([x, y]) => [x * (width - 1), y * (height - 1)])
      }
      if (engine === 'mesh') body.mesh_grid = mesh
      setState(await apiPost<TransformState>(`/pages/${pageId}/transform/candidate`, body))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    setBusy(true)
    setError(null)
    try {
      setState(await apiDelete<TransformState>(`/pages/${pageId}/transform/candidate`))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const accept = async () => {
    if (hasBlocks && !window.confirm(t('annotate.transformAcceptConfirm'))) return
    setBusy(true)
    setError(null)
    try {
      setState(
        await apiPost<TransformState>(
          `/pages/${pageId}/transform/accept${hasBlocks ? '?confirm=true' : ''}`,
        ),
      )
      await onAccepted()
      onClose()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const resetControls = () => {
    setPerspective(perspectiveDefault)
    setMesh(makeMesh())
  }

  return (
    <Modal
      title={t('annotate.transformTitle')}
      wide
      onClose={onClose}
      footer={
        <>
          {state?.candidate && (
            <button type="button" className="btn" disabled={busy} onClick={() => void reject()}>
              <IconClose size={11} />
              {t('annotate.transformReject')}
            </button>
          )}
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            {t('common.close')}
          </button>
          {state?.candidate && (
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void accept()}>
              <IconCheck size={11} />
              {t('annotate.transformAccept')}
            </button>
          )}
        </>
      }
    >
      <div className="grid min-h-0 gap-3 p-3 lg:grid-cols-[250px_minmax(0,1fr)]">
        <Module tab={t('annotate.transformEngine')} quiet>
          <div className="grid gap-1.5">
            {(['deskew', 'uvdoc', 'docscanner', 'perspective', 'mesh'] as const).map((value) => (
              <label
                key={value}
                className={`flex items-start gap-2 border border-[color:var(--color-rule)] p-2 ${
                  state?.engines?.[value]?.available === false
                    ? 'cursor-not-allowed opacity-50'
                    : 'cursor-pointer'
                }`}
              >
                <input
                  type="radio"
                  name="transform-engine"
                  value={value}
                  checked={engine === value}
                  onChange={() => setEngine(value)}
                  disabled={state?.engines?.[value]?.available === false}
                />
                <span className="min-w-0">
                  <b className="block text-[12px]">{t(`annotate.transformEngine_${value}`)}</b>
                  <span className="block text-[11px] leading-snug text-[color:var(--color-ink-3)]">
                    {t(`annotate.transformEngine_${value}Hint`)}
                    {state?.engines?.[value]?.available === false
                      ? ` ${t('annotate.transformUnavailable')}`
                      : ''}
                  </span>
                </span>
              </label>
            ))}
          </div>
          {manual && (
            <button type="button" className="btn mt-2 w-full" onClick={resetControls}>
              {t('annotate.transformResetPoints')}
            </button>
          )}
          <button type="button" className="btn btn-primary mt-2 w-full" disabled={busy} onClick={() => void generate()}>
            {busy ? t('annotate.transformGenerating') : t('annotate.transformGenerate')}
          </button>
          {state?.active && (
            <p className="mt-2 border-t border-[color:var(--color-rule)] pt-2 text-[11px] text-[color:var(--color-ink-2)]">
              {t('annotate.transformActive', {
                engine: state.active.actual_engine ?? state.active.engine,
              })}
            </p>
          )}
        </Module>

        <div className="min-w-0">
          {error !== null && (
            <ErrorNotice error={error} onDismiss={() => setError(null)} />
          )}
          <div className={`grid gap-2 ${state?.candidate ? 'md:grid-cols-2' : ''}`}>
            <Module tab={manual ? t('annotate.transformMovePoints') : t('annotate.transformOriginal')} flush>
              <div className="flex min-h-64 items-center justify-center bg-[color:var(--color-table)]">
                <div className="relative inline-block max-h-[62vh] max-w-full leading-none">
                  {state && (
                    <img
                      src={`${state.original_preview_url}?t=${state.candidate?.created_at ?? ''}`}
                      alt={t('annotate.transformOriginalAlt')}
                      className="block max-h-[62vh] max-w-full object-contain"
                    />
                  )}
                  {manual && state && (
                    <svg
                    ref={overlayRef}
                    viewBox="0 0 1000 1000"
                    preserveAspectRatio="none"
                    className="absolute inset-0 h-full w-full touch-none"
                    onPointerMove={(e) => updateDraggedPoint(e.clientX, e.clientY)}
                    onPointerUp={() => setDrag(null)}
                    onPointerLeave={() => setDrag(null)}
                  >
                    {engine === 'perspective' ? (
                      <>
                        <polygon
                          points={perspective.map(([x, y]) => `${x * 1000},${y * 1000}`).join(' ')}
                          fill="rgb(230 0 18 / 0.08)"
                          stroke="var(--color-sig)"
                          strokeWidth="3"
                          vectorEffect="non-scaling-stroke"
                        />
                        {perspective.map(([x, y], index) => (
                          <circle
                            key={index}
                            cx={x * 1000}
                            cy={y * 1000}
                            r="13"
                            fill="white"
                            stroke="var(--color-sig)"
                            strokeWidth="4"
                            vectorEffect="non-scaling-stroke"
                            onPointerDown={(e) => {
                              e.currentTarget.setPointerCapture(e.pointerId)
                              setDrag({ kind: 'perspective', index })
                            }}
                          />
                        ))}
                      </>
                    ) : (
                      <>
                        {mesh.map((row, r) => (
                          <polyline
                            key={`r${r}`}
                            points={row.map(([x, y]) => `${x * 1000},${y * 1000}`).join(' ')}
                            fill="none"
                            stroke="var(--color-sig)"
                            strokeWidth="2"
                            vectorEffect="non-scaling-stroke"
                          />
                        ))}
                        {mesh[0].map((_, c) => (
                          <polyline
                            key={`c${c}`}
                            points={mesh.map((row) => `${row[c][0] * 1000},${row[c][1] * 1000}`).join(' ')}
                            fill="none"
                            stroke="var(--color-sig)"
                            strokeWidth="2"
                            vectorEffect="non-scaling-stroke"
                          />
                        ))}
                        {flatMesh.map(([x, y], index) => (
                          <circle
                            key={index}
                            cx={x * 1000}
                            cy={y * 1000}
                            r="10"
                            fill="white"
                            stroke="var(--color-sig)"
                            strokeWidth="3"
                            vectorEffect="non-scaling-stroke"
                            onPointerDown={(e) => {
                              e.currentTarget.setPointerCapture(e.pointerId)
                              setDrag({ kind: 'mesh', index })
                            }}
                          />
                        ))}
                      </>
                    )}
                    </svg>
                  )}
                </div>
              </div>
            </Module>

            {state?.candidate && state.candidate_preview_url && (
              <Module
                tab={t('annotate.transformCandidate')}
                aux={
                  <span className="badge">
                    {state.candidate.actual_engine ?? state.candidate.engine}
                  </span>
                }
                flush
              >
                <div className="flex min-h-64 items-center justify-center bg-[color:var(--color-table)]">
                  <img
                    src={`${state.candidate_preview_url}?t=${state.candidate.created_at ?? ''}`}
                    alt={t('annotate.transformCandidateAlt')}
                    className="block max-h-[62vh] max-w-full object-contain"
                  />
                </div>
                {(state.candidate.warnings?.length || state.candidate.error) && (
                  <div className="border-t border-[color:var(--color-warn-rule)] bg-[color:var(--color-warn-wash)] p-2 text-[11px] text-[color:var(--color-warn)]">
                    {state.candidate.warnings?.join(', ')} {state.candidate.error}
                  </div>
                )}
              </Module>
            )}
          </div>
        </div>
      </div>
    </Modal>
  )
}
