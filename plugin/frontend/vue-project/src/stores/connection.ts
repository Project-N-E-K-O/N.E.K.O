import { defineStore } from 'pinia'
import { ref } from 'vue'

// 连续失败次数阈值，超过此值才标记为断连
const DISCONNECT_THRESHOLD = 3
// 健康检查间隔（毫秒）
const HEALTH_CHECK_INTERVAL = 10000

export const useConnectionStore = defineStore('connection', () => {
  const disconnected = ref(false)
  const authRequired = ref(false)
  const lastAuthErrorMessage = ref<string | null>(null)
  
  // 连续失败计数
  let consecutiveFailures = 0
  // 健康检查定时器
  let healthCheckTimer: number | null = null

  function markDisconnected() {
    // 增加失败计数，只有连续多次失败才标记为断连
    consecutiveFailures++
    if (consecutiveFailures >= DISCONNECT_THRESHOLD) {
      disconnected.value = true
    }
  }

  function markConnected() {
    // 重置失败计数
    consecutiveFailures = 0
    disconnected.value = false
  }
  
  // 强制标记断连（用于确定的断连场景）
  function forceDisconnected() {
    consecutiveFailures = DISCONNECT_THRESHOLD
    disconnected.value = true
  }
  
  // 启动健康检查
  function startHealthCheck() {
    if (healthCheckTimer) return
    
    // 动态获取 API 基础 URL
    const getApiBaseUrl = () => {
      // 开发环境使用代理，生产环境使用完整 URL
      const envUrl = import.meta.env.VITE_API_BASE_URL
      if (envUrl) return envUrl
      return import.meta.env.DEV ? '' : window.location.origin
    }
    
    const checkHealth = async () => {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 5000)
      try {
        // 使用短超时的健康检查请求
        const baseUrl = getApiBaseUrl()
        const response = await fetch(`${baseUrl}/health`, {
          method: 'GET',
          signal: controller.signal
        })
        clearTimeout(timeoutId)
        if (response.ok) {
          markConnected()
        } else {
          markDisconnected()
        }
      } catch {
        clearTimeout(timeoutId)
        markDisconnected()
      }
    }
    
    // 立即检查一次
    checkHealth()
    // 定期检查
    healthCheckTimer = window.setInterval(checkHealth, HEALTH_CHECK_INTERVAL)
  }
  
  // 停止健康检查
  function stopHealthCheck() {
    if (healthCheckTimer) {
      clearInterval(healthCheckTimer)
      healthCheckTimer = null
    }
  }

  function requireAuth(message?: string) {
    authRequired.value = true
    if (typeof message === 'string' && message.trim()) {
      lastAuthErrorMessage.value = message
    }
  }

  function clearAuthRequired() {
    authRequired.value = false
    lastAuthErrorMessage.value = null
  }

  return {
    disconnected,
    authRequired,
    lastAuthErrorMessage,
    markDisconnected,
    markConnected,
    forceDisconnected,
    startHealthCheck,
    stopHealthCheck,
    requireAuth,
    clearAuthRequired
  }
})
