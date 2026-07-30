// @vitest-environment happy-dom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps Auth connected and warns when Market is temporarily unavailable', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return jsonResponse({
          completed: true,
          authenticated: true,
          market_state: 'unavailable',
          retryable: true,
        })
      }
      if (path === '/market/oauth/status') {
        statusCalls += 1
        return jsonResponse({
          authenticated: true,
          market_state: statusCalls === 1 ? 'unavailable' : 'ready',
          retryable: statusCalls === 1,
          user: statusCalls === 1 ? null : { username: 'recovered-user' },
        })
      }
      if (path === '/market/oauth/account-summary') {
        return jsonResponse({
          authenticated: true,
          sources: {
            auth: { status: 'ready' },
            market: { status: 'unavailable' },
          },
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    await vi.advanceTimersByTimeAsync(2000)

    expect(auth.marketAuth.value.authenticated).toBe(true)
    expect(auth.marketAuth.value.market_state).toBe('unavailable')
    expect(auth.marketAuthStateMessageKey.value).toBe('market.marketUnavailable')
    expect(ElMessage.warning).toHaveBeenCalledWith('market.marketUnavailable')
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(auth.marketAuthBusy.value).toBe(false)

    await vi.advanceTimersByTimeAsync(5000)

    expect(statusCalls).toBe(2)
    expect(auth.marketAuth.value.market_state).toBe('ready')
    expect(ElMessage.success).toHaveBeenCalledWith('market.loginSuccess')
  })

  it('keeps the token pending until Auth confirms the subject', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return jsonResponse({
          completed: true,
          authenticated: false,
          auth_state: 'pending',
          market_state: null,
          retryable: true,
        })
      }
      if (path === '/market/oauth/status') {
        statusCalls += 1
        return jsonResponse(
          statusCalls === 1
            ? {
                authenticated: false,
                auth_state: 'pending',
                market_state: null,
                retryable: true,
              }
            : {
                authenticated: true,
                auth_state: 'ready',
                market_state: 'ready',
                retryable: false,
                user: { username: 'confirmed-user' },
              }
        )
      }
      if (path === '/market/oauth/account-summary') {
        return jsonResponse(accountSummary)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    await vi.advanceTimersByTimeAsync(2000)

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(auth.marketAuth.value.auth_state).toBe('pending')
    expect(auth.marketAuthStateMessageKey.value).toBe('market.authVerificationPending')
    expect(ElMessage.warning).toHaveBeenCalledWith('market.authVerificationPending')
    expect(ElMessage.success).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(5000)

    expect(statusCalls).toBe(2)
    expect(auth.marketAuth.value.authenticated).toBe(true)
    expect(auth.marketAuth.value.auth_state).toBe('ready')
    expect(auth.marketAuth.value.market_state).toBe('ready')
    expect(ElMessage.success).toHaveBeenCalledWith('market.loginSuccess')
  })

  it('continues retrying a persisted pending identity after a status network error', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/status') {
        statusCalls += 1
        if (statusCalls === 1) {
          throw new Error('temporary status network failure')
        }
        return jsonResponse({
          authenticated: true,
          auth_state: 'ready',
          market_state: 'ready',
          retryable: false,
          user: { username: 'recovered-user' },
        })
      }
      if (path === '/market/oauth/account-summary') {
        return jsonResponse(accountSummary)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    expect(await auth.loadMarketAuthStatus()).toBe(false)
    await vi.advanceTimersByTimeAsync(5000)

    expect(statusCalls).toBe(2)
    expect(auth.marketAuth.value.authenticated).toBe(true)
    expect(auth.marketAuth.value.auth_state).toBe('ready')
    expect(auth.marketAuth.value.market_state).toBe('ready')
    expect(ElMessage.success).toHaveBeenCalledWith('market.loginSuccess')
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

  it('does not restore Market readiness after logout during an in-flight retry', async () => {
    vi.useFakeTimers()
    const retryStatus = deferred<Response>()
    let statusCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return jsonResponse({
          completed: true,
          authenticated: true,
          market_state: 'unavailable',
          retryable: true,
        })
      }
      if (path === '/market/oauth/status') {
        statusCalls += 1
        if (statusCalls === 1) {
          return jsonResponse({
            authenticated: true,
            market_state: 'unavailable',
            retryable: true,
          })
        }
        return retryStatus.promise
      }
      if (path === '/market/oauth/logout') {
        return jsonResponse({ message: 'ok' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)

    await auth.logoutMarketAccount()
    retryStatus.resolve(jsonResponse({
      authenticated: true,
      market_state: 'ready',
      user: { username: 'late-user' },
    }))
    await vi.advanceTimersByTimeAsync(0)

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('market.logoutSuccess')
  })

  it('does not apply an OAuth completion that returns after logout', async () => {
    vi.useFakeTimers()
    const pendingComplete = deferred<Response>()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return pendingComplete.promise
      }
      if (path === '/market/oauth/logout') {
        return jsonResponse({ message: 'ok' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    const completing = vi.advanceTimersByTimeAsync(2000)
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/market/oauth/complete', expect.any(Object))
    })

    await auth.logoutMarketAccount()
    pendingComplete.resolve(jsonResponse({
      completed: true,
      authenticated: true,
      auth_state: 'ready',
      market_state: 'ready',
    }))
    await completing

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('market.logoutSuccess')
  })

  it('does not show an OAuth completion error that returns after logout', async () => {
    vi.useFakeTimers()
    const pendingComplete = deferred<Response>()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return pendingComplete.promise
      }
      if (path === '/market/oauth/logout') {
        return jsonResponse({ message: 'ok' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    const completing = vi.advanceTimersByTimeAsync(2000)
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/market/oauth/complete', expect.any(Object))
    })

    await auth.logoutMarketAccount()
    pendingComplete.resolve(jsonResponse({ detail: 'late OAuth failure' }, 502))
    await completing

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('market.logoutSuccess')
  })

  it('does not show a late login success after logout during summary loading', async () => {
    vi.useFakeTimers()
    const pendingSummary = deferred<Response>()
    let statusCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return jsonResponse({
          completed: true,
          authenticated: true,
          auth_state: 'ready',
          market_state: 'unavailable',
          retryable: true,
        })
      }
      if (path === '/market/oauth/status') {
        statusCalls += 1
        return jsonResponse({
          authenticated: true,
          auth_state: 'ready',
          market_state: statusCalls === 1 ? 'unavailable' : 'ready',
          retryable: statusCalls === 1,
        })
      }
      if (path === '/market/oauth/account-summary') {
        return pendingSummary.promise
      }
      if (path === '/market/oauth/logout') {
        return jsonResponse({ message: 'ok' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    await vi.advanceTimersByTimeAsync(2000)
    const retry = vi.advanceTimersByTimeAsync(5000)
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/market/oauth/account-summary', expect.any(Object))
    })

    await auth.logoutMarketAccount()
    pendingSummary.resolve(jsonResponse(accountSummary))
    await retry

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('market.logoutSuccess')
  })

  it('does not show a late login success after logout during immediate status loading', async () => {
    vi.useFakeTimers()
    const pendingStatus = deferred<Response>()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return jsonResponse({
          completed: true,
          authenticated: true,
          auth_state: 'ready',
          market_state: 'ready',
          retryable: false,
        })
      }
      if (path === '/market/oauth/status') {
        return pendingStatus.promise
      }
      if (path === '/market/oauth/logout') {
        return jsonResponse({ message: 'ok' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    const completing = vi.advanceTimersByTimeAsync(2000)
    await vi.waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/market/oauth/status', expect.any(Object))
    })

    await auth.logoutMarketAccount()
    pendingStatus.resolve(jsonResponse({
      authenticated: true,
      auth_state: 'ready',
      market_state: 'ready',
    }))
    await completing

    expect(auth.marketAuth.value.authenticated).toBe(false)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledWith('market.logoutSuccess')
  })

  it('localizes an Auth token rejection from OAuth completion', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path === '/market/bridge-token') {
        return jsonResponse({ bridge_token: 'fresh-bridge-token' })
      }
      if (path === '/market/oauth/start') {
        return jsonResponse({ auth_url: 'https://auth.test/authorize' })
      }
      if (path === '/market/oauth/complete') {
        return jsonResponse({ detail: 'auth_token_rejected' }, 401)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const auth = useMarketAuth()

    await auth.startMarketLogin()
    await vi.advanceTimersByTimeAsync(2000)

    expect(ElMessage.error).toHaveBeenCalledWith('market.authTokenRejected')
    expect(auth.marketAuth.value.authenticated).toBe(false)
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
