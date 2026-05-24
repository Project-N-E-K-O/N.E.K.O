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

export function useMarketAuth() {
  const { t } = useI18n()
  const marketAuth = ref<MarketAuthStatus>({ authenticated: false })
  const marketAuthBusy = ref(false)
  const bridgeToken = ref(localStorage.getItem('neko_bridge_token') || '')
  let marketAuthPollTimer: number | null = null

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

  async function loadMarketAuthStatus(): Promise<void> {
    const token = await ensureBridgeToken({ forceRefresh: true })
    if (!token) return
    try {
      const res = await fetch(`/market/oauth/status?token=${encodeURIComponent(token)}`)
      if (!res.ok) return
      marketAuth.value = await res.json()
    } catch {
      // 登录态只是增强能力，失败不影响 Market 浏览和安装。
    }
  }

  function stopMarketAuthPolling(): void {
    if (marketAuthPollTimer !== null) {
      clearInterval(marketAuthPollTimer)
      marketAuthPollTimer = null
    }
  }

  function startMarketAuthPolling(): void {
    stopMarketAuthPolling()
    const deadline = Date.now() + 5 * 60 * 1000
    marketAuthPollTimer = window.setInterval(async () => {
      if (Date.now() > deadline) {
        stopMarketAuthPolling()
        marketAuthBusy.value = false
        ElMessage.warning(t('market.loginPending'))
        return
      }

      const token = await ensureBridgeToken()
      if (!token) return
      try {
        const res = await fetch(`/market/oauth/complete?token=${encodeURIComponent(token)}`, {
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
          ElMessage.success(t('market.loginSuccess'))
        }
      } catch (error) {
        stopMarketAuthPolling()
        marketAuthBusy.value = false
        ElMessage.error(error instanceof Error ? error.message : t('market.loginFailed'))
      }
    }, 2000)
  }

  async function startMarketLogin(retried = false): Promise<void> {
    const token = await ensureBridgeToken({ forceRefresh: true })
    if (!token) {
      ElMessage.warning(t('market.pairRequired'))
      return
    }
    marketAuthBusy.value = true
    try {
      const res = await fetch(`/market/oauth/start?token=${encodeURIComponent(token)}`, {
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
      await fetch(`/market/oauth/logout?token=${encodeURIComponent(token)}`, {
        method: 'POST',
      })
      marketAuth.value = { authenticated: false }
      ElMessage.success(t('market.logoutSuccess'))
    } finally {
      marketAuthBusy.value = false
    }
  }

  onBeforeUnmount(stopMarketAuthPolling)

  return {
    marketAuth,
    marketAuthBusy,
    marketAuthDisplayName,
    loadMarketAuthStatus,
    logoutMarketAccount,
    startMarketLogin,
    stopMarketAuthPolling,
  }
}
