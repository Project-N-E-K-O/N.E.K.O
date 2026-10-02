/**
 * HTTP 请求封装
 */
import axios, { AxiosError as RequestAxiosError } from 'axios'
import type { AxiosInstance, InternalAxiosRequestConfig, AxiosResponse, AxiosError, AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import { API_BASE_URL, API_TIMEOUT } from './constants'
import { useConnectionStore } from '@/stores/connection'
import { i18n } from '@/i18n'

let lastNetworkErrorShownAt = 0

export type ErrorDisplayRequestConfig = AxiosRequestConfig & {
  /** Let the caller replace the generic interceptor toast with a domain message. */
  suppressErrorMessage?: boolean
  /** Suppress only the expected stopped-plugin response for panel probes. */
  suppressPluginNotRunningMessage?: boolean
  preserveMessagesOn404?: boolean
  /** i18n key used when Axios, rather than the server, times this request out. */
  timeoutErrorMessageKey?: string
  /** Internal guard: a failed CSRF request is retried at most once. */
  csrfRetryAttempted?: boolean
  /** Bootstrap errors carry caller display options but cannot retry a mutation. */
  csrfBootstrapFailed?: boolean
}

type HeaderBag = Record<string, unknown> & {
  delete?: (name: string) => void
  get?: (name: string) => unknown
  set?: (name: string, value: unknown) => void
}

function isFormDataPayload(data: unknown): data is FormData {
  return typeof FormData !== 'undefined' && data instanceof FormData
}

function readHeader(headers: HeaderBag, name: string): unknown {
  if (typeof headers.get === 'function') {
    const value = headers.get(name)
    if (value != null) return value
  }
  return headers[name] ?? headers[name.toLowerCase()]
}

function writeHeader(headers: HeaderBag, name: string, value: string): void {
  if (typeof headers.set === 'function') {
    headers.set(name, value)
    return
  }
  headers[name] = value
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

export function readErrorCode(error: AxiosError): string {
  const headers = error.response?.headers
  if (headers && typeof headers.get === 'function') {
    const value = headers.get('X-Error-Code')
    if (value != null) return String(value)
  }
  const headerValue = headers?.['x-error-code'] ?? headers?.['X-Error-Code']
  if (headerValue != null) return String(headerValue)
  const data = error.response?.data
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>
    if (typeof record.code === 'string') return record.code
    if (typeof record.error_code === 'string') return record.error_code
    if (record.detail && typeof record.detail === 'object') {
      const detail = record.detail as Record<string, unknown>
      if (typeof detail.code === 'string') return detail.code
      if (typeof detail.error_code === 'string') return detail.error_code
    }
  }
  return ''
}

export function shouldSuppressPluginNotRunningMessage(error: AxiosError): boolean {
  const requested = Boolean(
    (error.config as ErrorDisplayRequestConfig | undefined)?.suppressPluginNotRunningMessage,
  )
  return requested && readErrorCode(error) === 'PLUGIN_NOT_RUNNING'
}

export function shouldSuppressErrorMessage(error: AxiosError): boolean {
  return Boolean((error.config as ErrorDisplayRequestConfig | undefined)?.suppressErrorMessage)
    || shouldSuppressPluginNotRunningMessage(error)
}

export function isRequestTimeout(error: AxiosError): boolean {
  return error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT'
}

let pendingHealthProbe: Promise<boolean> | null = null

const CSRF_TOKEN_HEADER = 'X-CSRF-Token'
let csrfToken: string | null = null
let pendingCsrfToken: Promise<string> | null = null

function isMutationMethod(method: unknown): boolean {
  return typeof method === 'string' && ['post', 'put', 'patch', 'delete'].includes(method.toLowerCase())
}

function requestPath(url: unknown): string {
  if (typeof url !== 'string') return ''
  try {
    return new URL(url, API_BASE_URL || 'http://localhost').pathname
  } catch {
    return url.split(/[?#]/, 1)[0] ?? ''
  }
}

/**
 * Mirrors the plugin server routes guarded by require_plugin_mutation_access:
 * lifecycle actions plus package upload/install (including legacy aliases).
 * Keep it in lockstep with the server; a guarded route missing here fails
 * with a token 403 that is never refreshed or retried.
 */
function isCsrfProtectedPluginMutation(config: Pick<AxiosRequestConfig, 'method' | 'url'>): boolean {
  if (!isMutationMethod(config.method)) return false
  const path = requestPath(config.url)
  const method = config.method?.toLowerCase()
  if (method === 'delete') return /^\/plugin\/[^/]+$/.test(path)
  return /^\/plugin\/[^/]+\/(?:start|stop|refresh|reload)$/.test(path)
    || /^\/plugins\/(?:refresh|reload)$/.test(path)
    || (method === 'post'
      && /^\/plugin-cli\/(?:upload|upload-and-install|upload-and-unpack|install|unpack)$/.test(path))
}

/** Shared bootstrap has its own API_TIMEOUT; lifecycle timeouts apply after it. */
function loadCsrfToken(): Promise<string> {
  if (csrfToken) return Promise.resolve(csrfToken)
  if (pendingCsrfToken) return pendingCsrfToken

  let requestPromise: Promise<string>
  requestPromise = axios.get<{ csrf_token?: unknown }>('/security/csrf-token', {
    baseURL: API_BASE_URL,
    timeout: API_TIMEOUT,
    headers: { Accept: 'application/json' },
  }).then((response) => {
    const value = response.data?.csrf_token
    if (typeof value !== 'string' || !value) {
      throw new Error('CSRF token bootstrap response was invalid')
    }
    csrfToken = value
    return value
  }).finally(() => {
    if (pendingCsrfToken === requestPromise) pendingCsrfToken = null
  })

  pendingCsrfToken = requestPromise
  return requestPromise
}

function isCsrfValidationFailure(error: AxiosError): boolean {
  const headers = error.response?.headers as HeaderBag | undefined
  // Cross-port frontends can read the JSON reason without expanding global
  // CORS exposed headers; same-origin callers may also use the response header.
  const data = error.response?.data as { detail?: { csrf_failure?: string } } | undefined
  return error.response?.status === 403 && readErrorCode(error) === 'csrf_validation_failed'
    && (data?.detail?.csrf_failure === 'token'
      || Boolean(headers && readHeader(headers, 'X-CSRF-Failure') === 'token'))
}

function invalidateCsrfTokenIfCurrent(config: AxiosRequestConfig | undefined): void {
  if (!csrfToken || !config?.headers) return
  const sentToken = readHeader(config.headers as HeaderBag, CSRF_TOKEN_HEADER)
  if (typeof sentToken === 'string' && sentToken === csrfToken) csrfToken = null
}

type HealthProbeResult = {
  serverHealthy: boolean
  /** Only the request that created the probe may advance the failure counter. */
  initiatedByThisRequest: boolean
}

/** Verify the server independently of the failed request, without re-entering this interceptor. */
export function probeServerHealth(): Promise<HealthProbeResult> {
  if (pendingHealthProbe) {
    return pendingHealthProbe.then((serverHealthy) => ({
      serverHealthy,
      initiatedByThisRequest: false,
    }))
  }
  const probe = axios.get('/health', {
    baseURL: API_BASE_URL,
    timeout: 5000,
  }).then((response) => response.status >= 200 && response.status < 300)
    .catch(() => false)
  pendingHealthProbe = probe
  void probe.finally(() => {
    if (pendingHealthProbe === probe) {
      pendingHealthProbe = null
    }
  })
  return probe.then((serverHealthy) => ({
    serverHealthy,
    initiatedByThisRequest: true,
  }))
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
  async (config: InternalAxiosRequestConfig) => {
    if (isCsrfProtectedPluginMutation(config)) {
      // Protected calls are fail-closed: bootstrap failure means the original
      // state-changing request is never sent. Other mutation APIs are outside
      // this contract and must not depend on the plugin token service.
      let token: string
      try {
        token = await loadCsrfToken()
      } catch (cause) {
        // Bootstrap is shared by concurrent callers. Create a separate error
        // for each caller instead of mutating its shared config/display policy.
        const source = axios.isAxiosError(cause) ? cause : undefined
        const failureConfig = { ...config, csrfBootstrapFailed: true } as InternalAxiosRequestConfig
        // The protected operation has not been sent. Its domain timeout label
        // would incorrectly imply that the plugin itself timed out.
        delete (failureConfig as ErrorDisplayRequestConfig).timeoutErrorMessageKey
        const error = new RequestAxiosError(
          source?.message || i18n.global.t('messages.requestFailed'),
          source?.code,
          failureConfig,
          source?.request,
          source?.response,
        )
        error.cause = cause
        throw error
      }
      if (!config.headers) config.headers = {} as InternalAxiosRequestConfig['headers']
      writeHeader(config.headers as HeaderBag, CSRF_TOKEN_HEADER, token)
    }
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
    if (axios.isCancel(error) || error.code === 'ERR_CANCELED') {
      return Promise.reject(error)
    }
    const requestConfig = error.config as ErrorDisplayRequestConfig | undefined
    if (
      isCsrfValidationFailure(error)
      && requestConfig
      && isCsrfProtectedPluginMutation(requestConfig)
      && !requestConfig.csrfBootstrapFailed
      && !requestConfig.csrfRetryAttempted
    ) {
      // A rotated token can invalidate an in-flight request. Retry exactly
      // once, and only discard the token that this request actually sent so a
      // newer concurrent bootstrap result cannot be clobbered.
      invalidateCsrfTokenIfCurrent(requestConfig)
      return service.request({
        ...requestConfig,
        csrfRetryAttempted: true,
      } as ErrorDisplayRequestConfig)
    }
    // 对于 404 错误，不输出错误日志（这是正常的，某些资源可能不存在）
    // 对于 401/403 错误，也不输出错误日志
    const status = error.response?.status
    const suppressErrorMessage = shouldSuppressErrorMessage(error)
    if (!suppressErrorMessage && status !== 404 && status !== 401 && status !== 403) {
      console.error('Response error:', error)
    }

    let message = i18n.global.t('messages.requestFailed')
    let confirmedDisconnected = false
    
    if (error.response) {
      try {
        const connectionStore = useConnectionStore()
        connectionStore.markConnected()
      } catch (err) {
        console.debug('Connection store not available:', err)
      }
      // 服务器返回了错误状态码
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
        case 404: {
          message = formatHttpError(error) || i18n.global.t('messages.resourceNotFound')
          // 404 错误不显示通用错误消息，让调用方自己处理
          const preserveMessagesOn404 = Boolean(
            (error.config as ErrorDisplayRequestConfig | undefined)?.preserveMessagesOn404,
          )
          if (!preserveMessagesOn404) {
            ElMessage.closeAll()
          }
          break
        }
        case 500:
          message = formatHttpError(error) || i18n.global.t('messages.internalServerError')
          break
        case 503:
          message = formatHttpError(error) || i18n.global.t('messages.serviceUnavailable')
          break
        default:
          message = formatHttpError(error) || i18n.global.t('messages.requestFailedWithStatus', { status })
      }
    } else if (error.request && isRequestTimeout(error)) {
      // Axios 超时只说明当前请求未按时完成，不能据此标记服务器断连。
      const timeoutMessageKey = (error.config as ErrorDisplayRequestConfig | undefined)
        ?.timeoutErrorMessageKey || 'messages.requestTimeout'
      message = i18n.global.t(timeoutMessageKey)
    } else if (error.request) {
      // 只有独立健康检查也失败时，才将无响应请求归类为断网。
      const { serverHealthy, initiatedByThisRequest } = await probeServerHealth()
      message = serverHealthy
        ? i18n.global.t('messages.requestFailed')
        : i18n.global.t('messages.networkError')
      confirmedDisconnected = !serverHealthy
      let wasDisconnected = false
      try {
        const connectionStore = useConnectionStore()
        wasDisconnected = connectionStore.disconnected
        if (serverHealthy) {
          connectionStore.markConnected()
        } else if (initiatedByThisRequest) {
          connectionStore.markDisconnected()
        }
      } catch (err) {
        console.debug('Connection store not available:', err)
      }
      const now = Date.now()
      if (confirmedDisconnected && !suppressErrorMessage && !wasDisconnected
        && now - lastNetworkErrorShownAt > 15000) {
        lastNetworkErrorShownAt = now
        ElMessage.error(message)
      }
    } else {
      // 其他错误
      message = error.message || i18n.global.t('messages.requestFailed')
    }

    // 对于 401/403/404，不显示错误消息，交给调用方决定是否提示
    if (error.response && [401, 403, 404].includes(error.response.status)) {
      return Promise.reject(error)
    }
    
    if (confirmedDisconnected) {
      return Promise.reject(error)
    }

    if (!suppressErrorMessage) {
      ElMessage.error(message)
    }
    return Promise.reject(error)
  }
)

export default service
