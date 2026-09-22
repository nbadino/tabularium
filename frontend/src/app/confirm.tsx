/**
 * La conferma delle azioni distruttive.
 *
 * Il browser sa fare una sola conferma: `window.confirm`, che non appartiene a
 * nessun sistema visivo, blocca il thread (quindi anche lo streaming dei log e
 * i polling) e in un webview può essere soppressa. Le azioni più pericolose
 * dell'app — noleggiare una GPU, distruggere una risorsa cloud, cancellare
 * tutte le annotazioni di una pagina — non possono dipendere da quel dialog.
 *
 * Qui la conferma è la stessa `Modal` di tutto il resto: piastra, filetti,
 * focus trap, Escape, `btn-danger` sul comando che distrugge. Il chiamante
 * scrive cosa sta per succedere; la superficie è una sola.
 *
 * `useConfirm` è asincrono (`await confirm(...)`): `window.confirm` era
 * sincrono e bloccava, ma il prezzo — rendere `async` i gestori — è quello che
 * si paga per non congelare l'interfaccia mentre si chiede conferma.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Modal } from './ui'
import { useI18n } from '../i18n'

export interface ConfirmRequest {
  /** Titolo della modale: il nome dell'azione, non «Conferma». */
  title: string
  /** Cosa sta per succedere, con la conseguenza. */
  message: string
  /** Etichetta del comando che esegue. Predefinita: «Conferma». */
  acceptLabel?: string
  /** Etichetta del comando che rinuncia. Predefinita: «Annulla». */
  cancelLabel?: string
}

export type Confirmer = (request: ConfirmRequest) => Promise<boolean>

const ConfirmContext = createContext<Confirmer | null>(null)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n()
  const [request, setRequest] = useState<ConfirmRequest | null>(null)
  const resolver = useRef<((ok: boolean) => void) | null>(null)

  const confirm = useCallback<Confirmer>((next) => {
    // Una richiesta nuova chiude la precedente come rifiutata: non si
    // accodano due conferme, e nessuna promise resta appesa.
    resolver.current?.(false)
    setRequest(next)
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve
    })
  }, [])

  const settle = (ok: boolean) => {
    const resolve = resolver.current
    resolver.current = null
    setRequest(null)
    resolve?.(ok)
  }

  const value = useMemo(() => confirm, [confirm])

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {request && (
        <Modal
          title={request.title}
          onClose={() => settle(false)}
          footer={
            <div className="flex items-center justify-end gap-2 border-t border-[color:var(--color-rule)] bg-[color:var(--color-panel)] px-4 py-2.5">
              <button type="button" className="btn" onClick={() => settle(false)}>
                {request.cancelLabel ?? t('common.cancel')}
              </button>
              <button type="button" className="btn btn-danger" onClick={() => settle(true)}>
                {request.acceptLabel ?? t('common.confirm')}
              </button>
            </div>
          }
        >
          <p className="max-w-[70ch] whitespace-pre-line text-[13px] text-[color:var(--color-ink)]">
            {request.message}
          </p>
        </Modal>
      )}
    </ConfirmContext.Provider>
  )
}

/**
 * Il modo in cui un componente chiede conferma. Fuori dal provider ricade sul
 * dialog nativo: succede solo nei test dei componenti isolati, dove non
 * esiste una superficie da rendere — ma il comportamento resta corretto.
 */
export function useConfirm(): Confirmer {
  const confirm = useContext(ConfirmContext)
  return (
    confirm ??
    ((request) => Promise.resolve(window.confirm(`${request.title}\n\n${request.message}`)))
  )
}
