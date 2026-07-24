// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ElMessage } from 'element-plus'

import { useMarketAuth, type MarketAccountSummary } from './useMarketAuth'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

vi.mock('@/utils/openExternal', () => ({
  openExternalUrl: vi.fn(),
}))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

const accountSummary: MarketAccountSummary = {
  authenticated: true,
  profile: { display_name: 'Old account' },
  sources: {
    auth: { status: 'ready' },
    market: { status: 'ready' },
  },
}

describe('useMarketAuth', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('neko_bridge_token', 'bridge-token')
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it('keeps the authenticated state when logout returns an HTTP error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'logout rejected' }, 500))
    )
    const auth = useMarketAuth()
    auth.marketAuth.value = { authenticated: true }

    await expect(auth.logoutMarketAccount()).rejects.toThrow('logout rejected')

    expect(auth.marketAuth.value.authenticated).toBe(true)
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(auth.marketAuthBusy.value).toBe(false)
  })

  it('reconciles an explicitly expired account summary with the auth state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          authenticated: false,
          sources: {
            auth: { status: 'unavailable' },
            market: { status: 'unavailable' },
          },
        })
      )
    )
    const auth = useMarketAuth()
    auth.marketAuth.value = { authenticated: true }

    await auth.loadMarketAccountSummary()

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(auth.marketAccountSummary.value).toBeNull()
    expect(auth.marketAccountSummaryBusy.value).toBe(false)
  })

  it('does not restore an old account summary after logout', async () => {
    const pendingSummary = deferred<Response>()
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith('/account-summary')) return pendingSummary.promise
      if (String(input).endsWith('/logout')) return Promise.resolve(jsonResponse({ message: 'ok' }))
      return Promise.reject(new Error(`Unexpected request: ${String(input)}`))
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()
    auth.marketAuth.value = { authenticated: true }

    const loading = auth.loadMarketAccountSummary()
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/market/oauth/account-summary', expect.any(Object))
    })

    await auth.logoutMarketAccount()
    pendingSummary.resolve(jsonResponse(accountSummary))
    await loading

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(auth.marketAccountSummary.value).toBeNull()
    expect(auth.marketAccountSummaryBusy.value).toBe(false)
  })
})
