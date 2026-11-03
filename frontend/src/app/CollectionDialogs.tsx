import { useState } from 'react'
import type { CollectionItem } from '../lib/types'
import { useI18n, tn } from '../i18n'
import { Field, Modal } from './ui'

/** Create a collection, or rename one. */
export function CollectionNameDialog({
  collection,
  busy,
  error,
  onSubmit,
  onClose,
}: {
  collection: CollectionItem | null
  busy: boolean
  error: string | null
  onSubmit: (name: string) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [name, setName] = useState(collection?.name ?? '')
  const trimmed = name.trim()
  return (
    <Modal
      title={collection ? t('collections.renameTitle', { name: collection.name }) : t('collections.createTitle')}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button type="submit" form="collection-name" className="btn btn-primary" disabled={busy || !trimmed || trimmed === collection?.name}>
            {collection ? t('collections.rename') : t('collections.create')}
          </button>
        </>
      }
    >
      <form id="collection-name" className="p-3" onSubmit={(e) => { e.preventDefault(); if (trimmed) onSubmit(trimmed) }}>
        <Field label={t('collections.name')} hint={collection ? undefined : t('collections.nameHint')}>
          <input className="fld" autoFocus maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder={t('collections.namePlaceholder')} />
        </Field>
        {error && <p role="alert" className="mt-2 text-[12px] text-[color:var(--color-sig-text)]">{error}</p>}
      </form>
    </Modal>
  )
}

/**
 * Deleting a collection always says what happens to its pages. Moving them to
 * "No collection" is the default; deleting them needs the collection's name typed
 * and is offered only to whoever may delete pages.
 */
export function DeleteCollectionDialog({
  collection,
  canDeletePages,
  busy,
  onConfirm,
  onClose,
}: {
  collection: CollectionItem
  canDeletePages: boolean
  busy: boolean
  onConfirm: (pages: 'unassign' | 'delete') => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [mode, setMode] = useState<'unassign' | 'delete'>('unassign')
  const [typed, setTyped] = useState('')
  const ready = mode === 'unassign' || typed === collection.name
  return (
    <Modal
      title={t('collections.deleteTitle', { name: collection.name })}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>{t('common.cancel')}</button>
          <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={() => onConfirm(mode)}>
            {mode === 'delete' ? tn('collections.deleteWithPages', collection.pages) : t('collections.deleteKeepPages')}
          </button>
        </>
      }
    >
      <fieldset className="space-y-3 p-3 text-[13px]">
        <legend className="mb-2">{tn('collections.deleteQuestion', collection.pages)}</legend>
        <label className="flex items-start gap-2">
          <input type="radio" name="collection-delete" checked={mode === 'unassign'} onChange={() => setMode('unassign')} />
          <span>
            <b className="font-semibold">{t('collections.unassignOption')}</b>
            <span className="block text-[12px] text-[color:var(--color-ink-2)]">{t('collections.unassignHint')}</span>
          </span>
        </label>
        {canDeletePages && collection.pages > 0 && (
          <label className="flex items-start gap-2">
            <input type="radio" name="collection-delete" checked={mode === 'delete'} onChange={() => setMode('delete')} />
            <span>
              <b className="font-semibold text-[color:var(--color-sig-text)]">{t('collections.deleteOption')}</b>
              <span className="block text-[12px] text-[color:var(--color-ink-2)]">{t('collections.deleteHint')}</span>
            </span>
          </label>
        )}
        {mode === 'delete' && (
          <Field label={t('collections.typeName', { name: collection.name })}>
            <input className="fld" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          </Field>
        )}
      </fieldset>
    </Modal>
  )
}
