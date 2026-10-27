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
import { apiGet, apiPatch, apiPost } from '../lib/api'
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
  oidcEnabled: boolean
  oidcButtonLabel: string
}

const INITIAL: AuthState = {
  phase: 'loading',
  enabled: true,
  needsSetup: false,
  allowRegistration: false,
  instanceName: '',
  user: null,
  oidcEnabled: false,
  oidcButtonLabel: 'SSO',
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
      oidcEnabled: s.oidc_enabled,
      oidcButtonLabel: s.oidc_button_label,
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

/** Accetta un invito: sceglie la password e lascia la sessione attiva. */
export async function acceptInvite(token: string, password: string): Promise<void> {
  const user = await apiPost<User>('/auth/invite/accept', { token, password })
  set({ user, needsSetup: false })
}

/** Rilegge l'utente dal server (es. dopo un cambio password forzato). */
export async function refresh(): Promise<void> {
  try {
    const user = await apiGet<User>('/auth/me')
    set({ user })
  } catch {
    // Un 401 verrà già gestito dall'evento globale di sessione scaduta.
  }
}

/** Aggiorna il proprio profilo (email). */
export async function updateProfile(email: string | null): Promise<void> {
  const user = await apiPatch<User>('/auth/me', { email })
  set({ user })
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
  acceptInvite: (token: string, password: string) => Promise<void>
  refresh: () => Promise<void>
  updateProfile: (email: string | null) => Promise<void>
}

/** Sottoscrive il componente allo stato di autenticazione. */
export function useAuth(): Auth {
  const s = useSyncExternalStore(subscribe, getSnapshot)
  return { ...s, login, setup, register, logout, acceptInvite, refresh, updateProfile }
}

/**
 * Può governare l'istanza (impostazioni, backup, modelli, filesystem)?
 * In modalità locale (`TABULARIUM_AUTH=off`) non ci sono ruoli e il backend
 * consente tutto: prima quattro schermate lo ricontrollavano a mano, e le
 * Impostazioni si mettevano in sola lettura dicendo «solo un amministratore»
 * a chi era l'unico utente.
 */
export function useCanAdminister(): boolean {
  const s = useSyncExternalStore(subscribe, getSnapshot)
  return !s.enabled || s.user?.role === 'admin'
}
