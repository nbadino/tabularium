/**
 * Where the API lives, relative to the page.
 *
 * The base follows `import.meta.env.BASE_URL`, so a build served under `/tabularium/`
 * calls `/tabularium/api/...`: the reverse proxy strips the prefix and the backend
 * keeps answering on `/api/...`. With the default base the result is exactly `/api`.
 *
 * This lives in its own module because tests mock `lib/api` wholesale to stub the HTTP
 * client; the URL builder is not part of that contract.
 */
export const API_BASE = `${import.meta.env.BASE_URL.replace(/\/$/, '')}/api`

/** Absolute API URL for a path that may or may not already carry the `/api` prefix. */
export function apiUrl(path: string): string {
  return path.startsWith('/api') ? path : `${API_BASE}${path}`
}
