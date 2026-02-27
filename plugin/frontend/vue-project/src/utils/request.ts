/**
 * HTTP 请求封装
 */
import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import { API_BASE_URL, API_TIMEOUT } from './constants'
import { useAuthStore } from '@/stores/auth'
import { useConnectionStore } from '@/stores/connection'
import { i18n } from '@/i18n'

let lastNetworkErrorShownAt = 0

async function handleAuthError(message?: string) {
  try {
    const authStore = useAuthStore()
    authStore.clearAuthCode()
  } catch (err) {
    console.debug('Auth store not available:', err)
  }

  try {
    const connectionStore = useConnectionStore()
    connectionStore.requireAuth(message)
  } catch (err) {
    console.debug('Connection store not available:', err)
  }

  ElMessage.closeAll()
}

// 创建 axios 实例
const service: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  timeout: API_TIMEOUT,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
service.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // 添加认证头
    try {
      const authStore = useAuthStore()
      const authHeader = authStore.getAuthHeader()
      if (authHeader && config.headers) {
        config.headers.Authorization = authHeader
      }
    } catch (err) {
      // Store 可能还未初始化，忽略错误
      // 在开发环境下可能会看到这个错误，但不影响功能
    }
    return config
  },
  (error: AxiosError) => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
service.interceptors.response.use(
  (response: AxiosResponse) => {
    try {
      const connectionStore = useConnectionStore()
      connectionStore.markConnected()
    } catch (err) {
      console.debug('Connection store not available:', err)
    }
    // Axios 默认只会把 2xx 响应放到这里，直接返回 data 即可
    return response.data
  },
  async (error: AxiosError) => {
    // 对于 404 错误，不输出错误日志（这是正常的，某些资源可能不存在）
    // 对于 401/403 错误，也不输出错误日志（会自动跳转登录页）
    const status = error.response?.status
    if (status !== 404 && status !== 401 && status !== 403) {
      console.error('Response error:', error)
    }

    let message = i18n.global.t('messages.requestFailed')
    
    if (error.response) {
      try {
        const connectionStore = useConnectionStore()
        connectionStore.markConnected()
      } catch (err) {
        console.debug('Connection store not available:', err)
      }
      // 服务器返回了错误状态码
      const data = error.response.data as any

      switch (status) {
        case 400:
          message = data.detail || i18n.global.t('messages.badRequest')
          break
        case 401:
          message = i18n.global.t('auth.unauthorized')
          await handleAuthError(message)
          break
        case 403:
          message = data.detail || i18n.global.t('auth.forbidden')
          await handleAuthError(message)
          break
        case 404:
          message = data.detail || i18n.global.t('messages.resourceNotFound')
          // 404 错误不显示通用错误消息，让调用方自己处理
          ElMessage.closeAll()
          break
        case 500:
          message = data.detail || i18n.global.t('messages.internalServerError')
          break
        case 503:
          message = data.detail || i18n.global.t('messages.serviceUnavailable')
          break
        default:
          message = data.detail || i18n.global.t('messages.requestFailedWithStatus', { status })
      }
    } else if (error.request) {
      // 请求已发出，但没有收到响应
      message = i18n.global.t('messages.networkError')
      try {
        const connectionStore = useConnectionStore()
        const wasDisconnected = connectionStore.disconnected
        connectionStore.markDisconnected()
        const now = Date.now()
        if (!wasDisconnected && now - lastNetworkErrorShownAt > 15000) {
          lastNetworkErrorShownAt = now
          ElMessage.error(message)
        }
      } catch (err) {
        console.debug('Connection store not available:', err)
      }
    } else {
      // 其他错误
      message = error.message || i18n.global.t('messages.requestFailed')
    }

    // 对于 401/403/404，不显示错误消息（已处理或由调用方处理）
    if (error.response && [401, 403, 404].includes(error.response.status)) {
      return Promise.reject(error)
    }
    
    if (error.request && !error.response) {
      return Promise.reject(error)
    }

    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default service

