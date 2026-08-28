/**
 * Stato di autenticazione del frontend (self-hosted).
 *
 * Stesso stile di `i18n/index.ts`: piccolo store modulare senza dipendenze,
 * sottoscritto dai componenti via `useSyncExternalStore`. Il gate (AuthGate)
 * interroga `/auth/status` — endpoint pubblico — e decide cosa mostrare:
 * setup del primo amministratore, login, o l'app vera e propria.
 *
 * Quando qualsiasi richiesta API risponde 401 (sessione scaduta, account
 * disattivato) l'utente viene azzerato e il gate riporta al login.
 */
import { useSyncExternalStore } from 'react'
import { apiGet, apiPost } from '../lib/api'
import type { AuthStatus, User } from '../lib/types'

export interface AuthState {
  /** `loading` prima della risposta di `/auth/status`. */
  phase: 'loading' | 'ready'
  /** True quando il backend richiede il login (modalità auth). */
  enabled: boolean
  needsSetup: boolean
  allowRegistration: boolean
  instanceName: string
  user: User | null
}

const INITIAL: AuthState = {
  phase: 'loading',
  enabled: true,
  needsSetup: false,
  allowRegistration: false,
  instanceName: '',
  user: null,
}

let state: AuthState = INITIAL
const listeners = new Set<() => void>()

function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

function getSnapshot(): AuthState {
  return state
}

function set(next: Partial<AuthState>): void {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

/** Ricarica lo stato dal backend. Da chiamare all'avvio dell'app. */
export async function bootstrap(): Promise<void> {
  try {
    const s = await apiGet<AuthStatus>('/auth/status')
    set({
      phase: 'ready',
      enabled: s.auth_enabled,
      needsSetup: s.needs_setup,
      allowRegistration: s.allow_registration,
      instanceName: s.instance_name,
      user: s.user,
    })
  } catch {
    // Backend non raggiungibile: senza stato utile, la modalità locale
    // dell'UI (senza login) è la meno peggio — l'header segnala il problema.
    set({ phase: 'ready', enabled: false, user: null })
  }
}

export async function login(username: string, password: string): Promise<void> {
  const user = await apiPost<User>('/auth/login', { username, password })
  set({ user, needsSetup: false })
}

export async function setup(
  username: string,
  password: string,
  email?: string,
): Promise<void> {
  const user = await apiPost<User>('/auth/setup', {
    username,
    password,
    email: email?.trim() || undefined,
  })
  set({ user, needsSetup: false })
}

export async function register(
  username: string,
  password: string,
  email?: string,
): Promise<void> {
  const user = await apiPost<User>('/auth/register', {
    username,
    password,
    email: email?.trim() || undefined,
  })
  set({ user })
}

export async function logout(): Promise<void> {
  try {
    await apiPost('/auth/logout')
  } catch {
    // Il backend pulisce comunque il cookie all'avvio; qui conta azzerare lo stato.
  }
  set({ user: null })
}

// Sessione decaduta da un'altra linguetta o per scadenza: il backend ha
// risposto 401 a una richiesta qualsiasi → torna al login.
if (typeof window !== 'undefined') {
  window.addEventListener('tabularium:unauthorized', () => {
    set({ user: null, needsSetup: false })
  })
}

export interface Auth extends AuthState {
  login: (username: string, password: string) => Promise<void>
  setup: (username: string, password: string, email?: string) => Promise<void>
  register: (username: string, password: string, email?: string) => Promise<void>
  logout: () => Promise<void>
}

/** Sottoscrive il componente allo stato di autenticazione. */
export function useAuth(): Auth {
  const s = useSyncExternalStore(subscribe, getSnapshot)
  return { ...s, login, setup, register, logout }
}
