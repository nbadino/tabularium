/**
 * The shared history of a page's model comparison: who decided what, when, and why.
 *
 * Every decision taken in the comparison (an area settled, a model's reading used, a
 * cell or a group confirmed, a model's table added…) is kept on the server, so another
 * person on the page sees it here. A decision can be reverted (the value before it
 * returns, and the revert is a row too) after an inline confirmation, and anyone who
 * can edit can leave a comment, on the page or in reply to one decision.
 */
import { useState } from 'react'
import { useI18n } from '../../i18n'
import type { ConsensusDecision } from '../consensus'

function columnLetter(c: number): string {
  let out = ''
  let n = c + 1
  while (n > 0) {
    const rest = (n - 1) % 26
    out = String.fromCharCode(65 + rest) + out
    n = Math.floor((n - 1) / 26)
  }
  return out
}

export function ConsensusHistory({ items, canEdit, onRevert, onComment }: {
  /** Newest first. */
  items: ConsensusDecision[]
  canEdit: boolean
  /** Rejects when the revert was refused: the confirmation stays open. */
  onRevert: (decision: ConsensusDecision, comment: string) => Promise<void> | void
  /** Rejects when the comment was not saved: the text stays. */
  onComment: (text: string, aboutId: number | null) => Promise<void> | void
}) {
  const { t, tn, locale } = useI18n()
  const [text, setText] = useState('')
  const [replyTo, setReplyTo] = useState<ConsensusDecision | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const byId = new Map(items.map((item) => [item.id, item]))
  const nameOf = (item: ConsensusDecision) => item.user.username ?? t('consensus.history.someone')

  const what = (kind: ConsensusDecision['kind'], item: ConsensusDecision) =>
    t(`consensus.history.kind.${kind}`, { model: item.model_name ?? item.model_key ?? '' })
  const describe = (item: ConsensusDecision) =>
    item.kind === 'revert'
      ? t('consensus.history.kind.revert', { what: what(item.target_kind, item) })
      : what(item.kind, item)
  const where = (item: ConsensusDecision) => {
    if (item.cluster_id != null) return t('consensus.history.area', { n: item.cluster_id + 1 })
    const cells = item.after?.cells ?? item.before?.cells ?? []
    if (cells.length === 1) return t('consensus.history.cell', { cell: `${columnLetter(cells[0].c)}${cells[0].r + 1}` })
    if (cells.length > 1) return tn('consensus.history.cells', cells.length)
    return ''
  }
  const when = (iso: string) => {
    const date = new Date(iso)
    return Number.isNaN(date.getTime()) ? iso : date.toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' })
  }

  const send = async () => {
    const body = text.trim()
    if (!body || busy) return
    setBusy(true)
    try {
      await onComment(body, replyTo?.id ?? null)
      setText('')
      setReplyTo(null)
    } catch {
      // Not saved (the page shows why): the text stays for another try.
    } finally {
      setBusy(false)
    }
  }
  const revert = async (item: ConsensusDecision) => {
    if (busy) return
    setBusy(true)
    try {
      await onRevert(item, reason.trim())
      setConfirming(null)
      setReason('')
    } catch {
      // Refused or failed (the page shows why): the confirmation stays open.
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="border-t border-[color:var(--color-rule-strong)] p-2.5 text-[12px]" data-testid="consensus-history">
      <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-3)]">
        {t('consensus.history.title', { n: items.length })}
      </summary>
      <p className="mt-1 text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.history.hint')}</p>

      {canEdit && (
        <div className="mt-2 space-y-1">
          {replyTo && (
            <div className="flex items-center gap-2 text-[11px] text-[color:var(--color-ink-2)]">
              <span>{t('consensus.history.replyingTo', { name: nameOf(replyTo) })}</span>
              <button type="button" className="underline" onClick={() => setReplyTo(null)}>{t('consensus.history.cancelReply')}</button>
            </div>
          )}
          <label className="sr-only" htmlFor="consensus-history-comment">{t('consensus.history.commentLabel')}</label>
          <textarea
            id="consensus-history-comment"
            className="input w-full text-[12px]"
            rows={2}
            maxLength={2000}
            value={text}
            placeholder={t('consensus.history.commentPlaceholder')}
            onChange={(e) => setText(e.target.value)}
          />
          <button type="button" className="btn" disabled={busy || !text.trim()} onClick={() => void send()}>
            {t('consensus.history.send')}
          </button>
        </div>
      )}

      {items.length === 0 ? (
        <p className="mt-2 text-[color:var(--color-ink-3)]">{t('consensus.history.empty')}</p>
      ) : (
        <ol className="mt-2 space-y-2">
          {items.map((item) => {
            const revertedBy = item.reverted_by != null ? byId.get(item.reverted_by) : undefined
            const place = where(item)
            return (
              <li key={item.id} className={`border-l-2 pl-2 ${item.reverted_by != null ? 'border-[color:var(--color-rule)] opacity-70' : 'border-[color:var(--color-rule-strong)]'}`} data-testid="decision">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className={`font-semibold ${item.reverted_by != null ? 'line-through' : ''}`}>{describe(item)}</span>
                  {place && <span className="text-[color:var(--color-ink-2)]">{place}</span>}
                  {item.about_id != null && item.kind === 'comment' && (
                    <span className="mono text-[11px] text-[color:var(--color-ink-3)]">{t('consensus.history.about', { id: item.about_id })}</span>
                  )}
                  <span className="mono text-[11px] text-[color:var(--color-ink-3)]">#{item.id}</span>
                </div>
                <div className="text-[11px] text-[color:var(--color-ink-2)]">
                  {nameOf(item)} · <time dateTime={item.created_at}>{when(item.created_at)}</time>
                  {revertedBy && <> · {t('consensus.history.reverted', { name: nameOf(revertedBy) })}</>}
                </div>
                {item.comment && <p className="mt-0.5 whitespace-pre-wrap">{item.comment}</p>}
                {canEdit && (
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[11px]">
                    {item.revertible && confirming !== item.id && (
                      <button type="button" className="font-semibold underline" onClick={() => { setConfirming(item.id); setReason('') }}>
                        {t('consensus.history.revert')}
                      </button>
                    )}
                    {!item.revertible && item.reverted_by == null && (item.kind === 'adopt_page' || item.kind === 'removed' || item.kind === 'use_model') && (
                      <span className="text-[color:var(--color-ink-3)]">{t('consensus.history.notRevertible')}</span>
                    )}
                    <button type="button" className="underline" onClick={() => setReplyTo(item)}>{t('consensus.history.reply')}</button>
                  </div>
                )}
                {confirming === item.id && (
                  <div className="mt-1 space-y-1 border border-[color:var(--color-rule-strong)] p-2" role="group" aria-label={t('consensus.history.revert')}>
                    <p>
                      {item.kind === 'add_table'
                        ? t('consensus.history.revertTableConfirm', { model: item.model_name ?? '' })
                        : t('consensus.history.revertConfirm', { name: nameOf(item) })}
                    </p>
                    <label className="block text-[11px]">
                      {t('consensus.history.revertCommentLabel')}
                      <input className="input mt-0.5 w-full" maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} />
                    </label>
                    <div className="flex gap-2">
                      <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void revert(item)}>{t('consensus.history.revertAccept')}</button>
                      <button type="button" className="btn" onClick={() => setConfirming(null)}>{t('consensus.history.cancel')}</button>
                    </div>
                  </div>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </details>
  )
}
