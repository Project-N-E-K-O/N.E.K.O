/**
 * HTTP 请求封装
 */
import axios from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import { API_BASE_URL, API_TIMEOUT } from './constants'
import { useConnectionStore } from '@/stores/connection'
import { i18n } from '@/i18n'

let lastNetworkErrorShownAt = 0

type HeaderBag = Record<string, unknown> & {
  delete?: (name: string) => void
}

function isFormDataPayload(data: unknown): data is FormData {
  return typeof FormData !== 'undefined' && data instanceof FormData
}

function readHeader(headers: HeaderBag, name: string): unknown {
  return headers[name] ?? headers[name.toLowerCase()]
}

function deleteHeader(headers: HeaderBag, name: string): void {
  if (typeof headers.delete === 'function') {
    headers.delete(name)
    return
  }
  delete headers[name]
  delete headers[name.toLowerCase()]
}

export function stripJsonContentTypeForFormData(config: InternalAxiosRequestConfig): InternalAxiosRequestConfig {
  if (!isFormDataPayload(config.data) || !config.headers) {
    return config
  }
  const headers = config.headers as HeaderBag
  const contentType = readHeader(headers, 'Content-Type')
  if (typeof contentType === 'string' && contentType.toLowerCase().includes('application/json')) {
    deleteHeader(headers, 'Content-Type')
  }
  return config
}

function stringifyDetail(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value.trim()
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value
      .map((item) => stringifyDetail(item))
      .filter(Boolean)
      .join('; ')
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    if (typeof record.msg === 'string') {
      const loc = Array.isArray(record.loc) ? `${record.loc.join('.')}: ` : ''
      return `${loc}${record.msg}`.trim()
    }
    if (typeof record.message === 'string') return record.message.trim()
    if (typeof record.detail === 'string') return record.detail.trim()
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

export function formatHttpError(error: unknown): string {
  const anyError = error as any
  const data = anyError?.response?.data
  const parts = [
    stringifyDetail(data?.detail),
    stringifyDetail(data?.message),
    stringifyDetail(data?.code),
    stringifyDetail(data?.details),
  ].filter(Boolean)
  if (parts[0]) return parts[0]
  return !anyError?.response && error instanceof Error ? error.message : ''
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
    return stripJsonContentTypeForFormData(config)
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
    // 对于 401/403 错误，也不输出错误日志
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
          message = formatHttpError(error) || i18n.global.t('messages.badRequest')
          break
        case 401:
          message = i18n.global.t('auth.unauthorized')
          break
        case 403:
          message = formatHttpError(error) || i18n.global.t('auth.forbidden')
          break
        case 404:
          message = formatHttpError(error) || i18n.global.t('messages.resourceNotFound')
          // 404 错误不显示通用错误消息，让调用方自己处理
          ElMessage.closeAll()
          break
        case 500:
          message = formatHttpError(error) || i18n.global.t('messages.internalServerError')
          break
        case 503:
          message = formatHttpError(error) || i18n.global.t('messages.serviceUnavailable')
          break
        default:
          message = formatHttpError(error) || i18n.global.t('messages.requestFailedWithStatus', { status })
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

    // 对于 401/403/404，不显示错误消息，交给调用方决定是否提示
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
