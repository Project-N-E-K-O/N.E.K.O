/**
 * Same-origin, token-authenticated access to the local Market bridge.
 *
 * The bridge lives on the plugin server that also serves this SPA, so every
 * call goes to a relative ``/market/*`` path. The token is fetched once from
 * ``/market/bridge-token`` and re-fetched once on a 403 before giving up.
 *
 * Extracted from three near-identical copies (MarketPanel, the update store,
 * and now the install-task store) — keep this the only implementation.
 */
const TOKEN_STORAGE_KEY = 'neko_bridge_token'
const LOG_PREFIX = '[market-bridge]'

let cachedToken = ''
let inflightToken: Promise<string> | null = null

function readStoredToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) || ''
  } catch {
    // Privacy mode / quota: the in-memory token still works for this session.
    return ''
  }
}

function storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // ignored
  }
}

export async function ensureBridgeToken(forceRefresh = false): Promise<string> {
  if (forceRefresh) {
    cachedToken = ''
    try {
      localStorage.removeItem(TOKEN_STORAGE_KEY)
    } catch {
      // ignored
    }
  }
  if (cachedToken) return cachedToken
  if (inflightToken) return inflightToken

  inflightToken = (async () => {
    try {
      const res = await fetch('/market/bridge-token')
      if (res.ok) {
        const data = await res.json().catch(() => null)
        const token = String(data?.bridge_token || '')
        if (token) {
          cachedToken = token
          storeToken(token)
        }
      }
    } catch (err) {
      console.warn(LOG_PREFIX, 'token request failed', err)
    }
    if (!cachedToken) cachedToken = readStoredToken()
    return cachedToken
  })()

  try {
    return await inflightToken
  } finally {
    inflightToken = null
  }
}

export function bridgeUrl(path: string, token: string): string {
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}token=${encodeURIComponent(token)}`
}

/**
 * Returns ``null`` when the bridge is unreachable or unauthenticated, so
 * callers treat transport failure as a value instead of an exception.
 */
export async function fetchBridge(
  path: string,
  init?: RequestInit,
  options: { retryOnForbidden?: boolean } = {},
): Promise<Response | null> {
  const token = await ensureBridgeToken()
  if (!token) {
    console.warn(LOG_PREFIX, 'request skipped: no bridge token', path)
    return null
  }

  let res: Response
  try {
    res = await fetch(bridgeUrl(path, token), init)
  } catch (err) {
    console.warn(LOG_PREFIX, 'request failed', path, err)
    return null
  }
  if (res.status !== 403 || options.retryOnForbidden === false) return res

  const freshToken = await ensureBridgeToken(true)
  if (!freshToken) return res
  try {
    return await fetch(bridgeUrl(path, freshToken), init)
  } catch (err) {
    console.warn(LOG_PREFIX, 'retry failed', path, err)
    return null
  }
}

/** Stable error code out of a bridge error body (``detail.code`` or ``code``). */
export function readErrorCode(body: unknown): string {
  const record = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  const detail = record.detail && typeof record.detail === 'object'
    ? record.detail as Record<string, unknown>
    : null
  return String(detail?.code || detail?.error_code || record.code || record.error_code || '')
}
