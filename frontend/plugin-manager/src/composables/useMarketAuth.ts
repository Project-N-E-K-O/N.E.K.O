import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { openExternalUrl } from '@/utils/openExternal'

interface MarketAuthStatus {
  authenticated: boolean
  user?: {
    username?: string
    display_name?: string
    email?: string
  } | null
  expires_at?: number | null
  market_web_url?: string
}

export interface MarketAccountSummary {
  authenticated: boolean
  profile?: {
    display_name?: string | null
    username?: string | null
    avatar_url?: string | null
    login_method?: string | null
  } | null
  market?: {
    member_days?: number | null
    published_plugins?: number | null
    installed_plugins?: number | null
    total_downloads?: number | null
  } | null
  sources: Record<string, { status: 'ready' | 'unavailable' }>
  expires_at?: number | null
}

export function useMarketAuth() {
  const { t } = useI18n()
  const marketAuth = ref<MarketAuthStatus>({ authenticated: false })
  const marketAccountSummary = ref<MarketAccountSummary | null>(null)
  const marketAccountSummaryBusy = ref(false)
  const marketAuthBusy = ref(false)
  const bridgeToken = ref(localStorage.getItem('neko_bridge_token') || '')
  let marketAuthPollTimer: number | null = null
  // Sticky stop flag for the recursive setTimeout poll loop. Read by
  // ``tick`` and ``schedule`` inside ``startMarketAuthPolling`` so a
  // pending timer that fires after ``stopMarketAuthPolling`` exits early.
  let pollingStopped = false

  const marketAuthDisplayName = computed(() => {
    const user = marketAuth.value.user
    return user?.display_name || user?.username || user?.email || t('market.account')
  })

  async function ensureBridgeToken(options: { forceRefresh?: boolean } = {}): Promise<string> {
    if (bridgeToken.value && !options.forceRefresh) return bridgeToken.value
    if (options.forceRefresh) {
      bridgeToken.value = ''
      localStorage.removeItem('neko_bridge_token')
    }
    try {
      const res = await fetch('/market/bridge-token')
      if (res.ok) {
        const data = await res.json()
        if (data.bridge_token) {
          bridgeToken.value = data.bridge_token
          localStorage.setItem('neko_bridge_token', data.bridge_token)
        }
      }
    } catch {
      // 登录是增强能力，失败时让调用方按未配对处理。
    }
    if (!bridgeToken.value && !options.forceRefresh) {
      bridgeToken.value = localStorage.getItem('neko_bridge_token') || ''
    }
    return bridgeToken.value
  }

  /**
   * Wrap ``fetch`` with the bridge ``Authorization: Bearer`` header.
   *
   * Phase 3 of the PR-1480 review-fix work (bug 1.6 / req 2.6): all
   * ``/market/oauth/*`` calls used to attach the bridge token via
   * ``?token=...`` query string, which leaks the token into:
   *   - browser history,
   *   - ``Referer`` headers when the page navigates,
   *   - reverse-proxy / CDN access logs.
   *
   * The backend (see ``plugin/server/routes/market_bridge.py::_verify_token``)
   * accepts BOTH the legacy ``?token=...`` query parameter and the
   * preferred ``Authorization: Bearer <token>`` header, with the header
   * winning when both are present. This helper enforces "header always,
   * never query" on the frontend side.
   *
   * Scope is intentionally narrow — only ``/market/oauth/*`` is migrated.
   * ``/market/install``, ``/market/tasks/*``, ``/market/installed``,
   * ``/market/token-exchange``, and ``/market/bridge-token`` are NOT
   * migrated in this PR (see design.md § Out of Scope) because they are
   * not the leakage vector and changing them would expand the cross-
   * process blast radius without proportional benefit.
   *
   * If ``ensureBridgeToken`` returns an empty string the helper still
   * issues the request without the header — callers handle the resulting
   * 403 the same way they handled the legacy "no token" case (typically
   * by surfacing ``market.pairRequired``).
   */
  async function authedFetch(input: string, init: RequestInit = {}): Promise<Response> {
    const token = await ensureBridgeToken()
    const headers = new Headers(init.headers)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    return fetch(input, { ...init, headers })
  }

  async function loadMarketAuthStatus(): Promise<void> {
    const token = await ensureBridgeToken({ forceRefresh: true })
    if (!token) return
    try {
      const res = await authedFetch('/market/oauth/status')
      if (!res.ok) return
      marketAuth.value = await res.json()
    } catch {
      // 登录态只是增强能力，失败不影响 Market 浏览和安装。
    }
  }

  async function loadMarketAccountSummary(): Promise<void> {
    if (!marketAuth.value.authenticated) {
      marketAccountSummary.value = null
      return
    }
    const token = await ensureBridgeToken()
    if (!token) return
    marketAccountSummaryBusy.value = true
    try {
      const res = await authedFetch('/market/oauth/account-summary')
      if (!res.ok) return
      const summary = await res.json() as MarketAccountSummary
      marketAccountSummary.value = summary.authenticated ? summary : null
    } catch {
      // The small account card is progressive enhancement. Keep the known
      // login status when one source is temporarily unavailable.
    } finally {
      marketAccountSummaryBusy.value = false
    }
  }

  function stopMarketAuthPolling(): void {
    pollingStopped = true
    if (marketAuthPollTimer !== null) {
      clearTimeout(marketAuthPollTimer)
      marketAuthPollTimer = null
    }
  }

  /**
   * Poll ``/market/oauth/complete`` until the user finishes the OAuth flow.
   *
   * Implementation notes:
   *
   * - **Recursive setTimeout instead of setInterval**: ``setInterval`` fires
   *   every 2s independent of how long the previous request took; if the
   *   network round-trip exceeds 2s the next interval starts a *parallel*
   *   request, and both ``then`` branches race to call
   *   ``stopMarketAuthPolling`` / ``ElMessage.success`` / ``loginFailed``.
   *   With recursive setTimeout we only schedule the next tick after the
   *   previous one finishes (or is skipped because ``inFlight``).
   * - ``inFlight`` belt-and-suspenders: if a tick is somehow scheduled
   *   while the previous fetch is still in flight (race with manual
   *   ``startMarketAuthPolling`` re-entry), the new tick exits early and
   *   schedules the next one.
   * - ``pollingStopped`` is module-private (set by
   *   ``stopMarketAuthPolling``) and re-checked inside ``tick`` so that a
   *   scheduled tick still pending when the user navigates away exits
   *   without firing any UI side effect.
   * - The ``finally`` block resets ``inFlight`` even on thrown errors so
   *   the very next ``stopMarketAuthPolling`` (called inside the catch
   *   branch) doesn't leave the flag pinned to ``true`` and block any
   *   future ``startMarketAuthPolling`` call from polling.
   */
  function startMarketAuthPolling(): void {
    stopMarketAuthPolling()
    pollingStopped = false
    let inFlight = false
    const deadline = Date.now() + 5 * 60 * 1000

    const tick = async () => {
      if (pollingStopped) return
      if (Date.now() > deadline) {
        stopMarketAuthPolling()
        marketAuthBusy.value = false
        ElMessage.warning(t('market.loginPending'))
        return
      }
      if (inFlight) {
        // Defensive: should never happen with recursive setTimeout, but
        // keeps the contract explicit for future maintainers.
        schedule()
        return
      }

      const token = await ensureBridgeToken()
      if (!token) {
        schedule()
        return
      }

      inFlight = true
      try {
        const res = await authedFetch('/market/oauth/complete', {
          method: 'POST',
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          throw new Error(err.detail || t('market.loginFailed'))
        }
        const data = await res.json()
        if (data.completed) {
          stopMarketAuthPolling()
          marketAuthBusy.value = false
          await loadMarketAuthStatus()
          await loadMarketAccountSummary()
          ElMessage.success(t('market.loginSuccess'))
          return
        }
      } catch (error) {
        stopMarketAuthPolling()
        marketAuthBusy.value = false
        ElMessage.error(error instanceof Error ? error.message : t('market.loginFailed'))
        return
      } finally {
        // Reset BEFORE schedule() so a re-entrant
        // ``startMarketAuthPolling`` call (e.g. user clicks "log in" again
        // after an error) doesn't see a stale ``true``.
        inFlight = false
      }

      schedule()
    }

    const schedule = () => {
      if (pollingStopped) return
      marketAuthPollTimer = window.setTimeout(tick, 2000)
    }

    schedule()
  }

  async function startMarketLogin(retried = false): Promise<void> {
    const token = await ensureBridgeToken({ forceRefresh: true })
    if (!token) {
      ElMessage.warning(t('market.pairRequired'))
      return
    }
    marketAuthBusy.value = true
    try {
      const res = await authedFetch('/market/oauth/start', {
        method: 'POST',
      })
      if (res.status === 403) {
        bridgeToken.value = ''
        localStorage.removeItem('neko_bridge_token')
        if (!retried) return startMarketLogin(true)
        throw new Error(t('market.pairRequired'))
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || t('market.loginFailed'))
      }
      const data = await res.json()
      if (data.auth_url) {
        openExternalUrl(data.auth_url)
        ElMessage.info(t('market.loginStarted'))
        startMarketAuthPolling()
      } else {
        throw new Error(t('market.loginFailed'))
      }
    } catch (error) {
      marketAuthBusy.value = false
      ElMessage.error(error instanceof Error ? error.message : t('market.loginFailed'))
    }
  }

  async function logoutMarketAccount(): Promise<void> {
    const token = await ensureBridgeToken()
    if (!token) return
    marketAuthBusy.value = true
    try {
      await authedFetch('/market/oauth/logout', {
        method: 'POST',
      })
      marketAuth.value = { authenticated: false }
      marketAccountSummary.value = null
      ElMessage.success(t('market.logoutSuccess'))
    } finally {
      marketAuthBusy.value = false
    }
  }

  onBeforeUnmount(stopMarketAuthPolling)

  return {
    marketAuth,
    marketAccountSummary,
    marketAccountSummaryBusy,
    marketAuthBusy,
    marketAuthDisplayName,
    loadMarketAuthStatus,
    loadMarketAccountSummary,
    logoutMarketAccount,
    startMarketLogin,
    stopMarketAuthPolling,
  }
}
