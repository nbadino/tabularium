/**
 * Traduzione degli errori in qualcosa su cui si può agire.
 *
 * Regola del prodotto: nessuna eccezione grezza raggiunge mai lo schermo.
 * Ogni errore esce come {titolo, messaggio, suggerimento} — cosa è successo
 * e cosa fare adesso. Il dettaglio tecnico resta disponibile ma richiuso.
 * Tutto è localizzato via i18n: al cambio lingua gli errori seguono.
 */
import { t } from '../i18n'
import { ApiError } from './api'

export interface Explained {
  /** Cosa è successo, in una riga. */
  titolo: string
  /** Il problema, nel vocabolario del dominio. */
  messaggio: string
  /** Il rimedio: sempre presente, sempre un'azione. */
  suggerimento: string
  /** Testo grezzo per la sezione «dettaglio tecnico», mai mostrato di default. */
  dettaglio: string
}

/** Estrae il `detail` di FastAPI da un corpo di risposta JSON. */
function detailOf(body: string): string | null {
  try {
    const parsed: unknown = JSON.parse(body)
    if (parsed && typeof parsed === 'object' && 'detail' in parsed) {
      const d = (parsed as { detail: unknown }).detail
      if (typeof d === 'string') return d
      if (Array.isArray(d) && d.length > 0) {
        // errori di validazione pydantic
        const first = d[0] as { msg?: string; loc?: unknown[] }
        if (first?.msg) {
          const campo = Array.isArray(first.loc) ? first.loc.slice(-1)[0] : null
          return campo ? `${String(campo)}: ${first.msg}` : first.msg
        }
      }
    }
  } catch {
    /* il corpo non era JSON */
  }
  return null
}

/** Il sostantivo giusto per la risorsa toccata dalla richiesta fallita. */
function resourceOf(path: string): string {
  if (path.includes('/annotations')) return t('errors.resource.annotations')
  if (path.includes('/table')) return t('errors.resource.table')
  if (path.includes('/training')) return t('errors.resource.training')
  if (path.includes('/datasets')) return t('errors.resource.datasets')
  if (path.includes('/evaluate')) return t('errors.resource.evaluate')
  if (path.includes('/playground')) return t('errors.resource.playground')
  if (path.includes('/prelabel')) return t('errors.resource.prelabel')
  if (path.includes('/scan')) return t('errors.resource.scan')
  if (path.includes('/conventions')) return t('errors.resource.conventions')
  if (path.includes('/pages')) return t('errors.resource.pages')
  if (path.includes('/projects')) return t('errors.resource.projects')
  return t('errors.resource.resource')
}

export function describeError(e: unknown): Explained {
  if (e instanceof ApiError) {
    const detail = detailOf(e.body)
    const risorsa = resourceOf(e.path)
    const dettaglio = `${e.method} ${e.path} → ${e.status ?? 'nessuna risposta'}\n${e.body}`.trim()

    // Il server non ha risposto affatto.
    if (e.status === null) {
      return {
        titolo: t('errors.backendDownTitle'),
        messaggio: t('errors.backendDownMsg'),
        suggerimento: t('errors.backendDownHint'),
        dettaglio,
      }
    }

    switch (e.status) {
      case 400:
        return {
          titolo: t('errors.badRequestTitle'),
          messaggio: detail ?? t('errors.badRequestMsg', { resource: risorsa }),
          suggerimento: t('errors.badRequestHint'),
          dettaglio,
        }
      case 404:
        return {
          titolo: t('errors.notFoundTitle'),
          messaggio: detail ?? t('errors.notFoundMsg', { resource: risorsa }),
          suggerimento: t('errors.notFoundHint'),
          dettaglio,
        }
      case 409:
        return {
          titolo: t('errors.conflictTitle'),
          messaggio: detail ?? t('errors.conflictMsg', { resource: risorsa }),
          suggerimento: t('errors.conflictHint'),
          dettaglio,
        }
      case 413:
        return {
          titolo: t('errors.tooLargeTitle'),
          messaggio: detail ?? t('errors.tooLargeMsg', { resource: risorsa }),
          suggerimento: t('errors.tooLargeHint'),
          dettaglio,
        }
      case 422:
        return {
          titolo: t('errors.invalidTitle'),
          messaggio: detail ?? t('errors.invalidMsg'),
          suggerimento: t('errors.invalidHint'),
          dettaglio,
        }
      case 500:
      case 502:
      case 503:
        return {
          titolo: t('errors.serverTitle'),
          messaggio: detail ?? t('errors.serverMsg', { resource: risorsa }),
          suggerimento: t('errors.serverHint'),
          dettaglio,
        }
      default:
        return {
          titolo: t('errors.statusTitle', { status: String(e.status) }),
          messaggio: detail ?? t('errors.statusMsg', { resource: risorsa }),
          suggerimento: t('errors.statusHint'),
          dettaglio,
        }
    }
  }

  if (e instanceof Error) {
    return {
      titolo: t('errors.unexpectedTitle'),
      messaggio: e.message,
      suggerimento: t('errors.unexpectedHint'),
      dettaglio: e.stack ?? e.message,
    }
  }

  return {
    titolo: t('errors.unexpectedTitle'),
    messaggio: t('errors.unexpectedMsg'),
    suggerimento: t('errors.unexpectedHint'),
    dettaglio: String(e),
  }
}

/** Errore semplice per i punti in cui serve `String(e)` come messaggio. */
export function errMsg(key: string, vars?: Record<string, string | number>): string {
  return t(key, vars)
}