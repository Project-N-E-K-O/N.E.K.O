const DEFAULT_BASE_URL = 'https://api.dataforseo.com'
const DEFAULT_REQUEST_TIMEOUT_MS = 120_000

export const DATAFORSEO_ENDPOINTS = Object.freeze({
  searchVolume: '/v3/keywords_data/google_ads/search_volume/live',
  keywordDifficulty: '/v3/dataforseo_labs/google/bulk_keyword_difficulty/live',
  organicSerp: '/v3/serp/google/organic/live/advanced',
})

export class DataForSeoApiError extends Error {
  constructor(message, {
    endpoint,
    statusCode,
    retryable = false,
    fatal = false,
    costUsd = 0,
    billingUncertain = false,
  } = {}) {
    super(message)
    this.name = 'DataForSeoApiError'
    this.endpoint = endpoint
    this.statusCode = statusCode
    this.retryable = Boolean(retryable)
    this.fatal = Boolean(fatal)
    this.costUsd = Number.isFinite(Number(costUsd)) ? Number(costUsd) : 0
    this.billingUncertain = Boolean(billingUncertain)
  }
}

const RETRYABLE_API_STATUS_CODES = new Set([
  40101, // Internal search-engine server error.
  40103, // Task execution failed.
  40202, // Per-minute rate limit exceeded.
  40209, // Too many simultaneous queries.
  50000,
  50001,
  50301,
  50302,
  50303,
  50304,
  50401,
  50402,
])

const FATAL_API_STATUS_CODES = new Set([
  40100, // Authorization failed.
  40104, // Account verification required.
  40200, // Payment required.
  40201, // Account access paused.
  40203, // Account cost limit exceeded.
  40204, // Subscription required.
  40205, // Hourly duplicate-task limit exceeded.
  40206, // Daily duplicate-task limit exceeded.
  40207, // IP is not whitelisted.
  40208, // Account is blocked.
  40210, // Insufficient funds.
])

function payloadCost(payload) {
  const topLevelCost = Number(payload?.cost)
  if (Number.isFinite(topLevelCost)) return topLevelCost
  return (payload?.tasks ?? []).reduce((sum, task) => {
    const taskCost = Number(task?.cost)
    return sum + (Number.isFinite(taskCost) ? taskCost : 0)
  }, 0)
}

function isRetryableApiStatus(statusCode) {
  return RETRYABLE_API_STATUS_CODES.has(Number(statusCode))
}

function isFatalApiStatus(statusCode) {
  return FATAL_API_STATUS_CODES.has(Number(statusCode))
}

function isRetryableHttpStatus(statusCode) {
  const code = Number(statusCode)
  return code === 408 || code === 425 || code === 429 || code >= 500
}

function isFatalHttpStatus(statusCode) {
  const code = Number(statusCode)
  return code === 401 || code === 402 || code === 403
}

function requireCredential(value, name) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new TypeError(`${name} must be a non-empty string`)
  }
  return value
}

function normalizeTimeout(value) {
  if (value == null) return null
  const timeoutMs = Number(value)
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0) {
    throw new TypeError('timeoutMs must be a positive integer or null')
  }
  return timeoutMs
}

function apiMessage(payload) {
  if (!payload || typeof payload !== 'object') return ''
  const code = Number.isInteger(payload.status_code) ? ` ${payload.status_code}` : ''
  const message = typeof payload.status_message === 'string' ? `: ${payload.status_message}` : ''
  return `${code}${message}`
}

function assertSuccessfulPayload(payload, endpoint) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new DataForSeoApiError(
      `DataForSEO returned an invalid JSON envelope for ${endpoint}`,
      { endpoint, costUsd: payloadCost(payload), billingUncertain: true },
    )
  }

  if (payload.status_code !== 20000) {
    const statusCode = payload.status_code
    throw new DataForSeoApiError(
      `DataForSEO rejected ${endpoint}${apiMessage(payload)}`,
      {
        endpoint,
        statusCode,
        retryable: isRetryableApiStatus(statusCode),
        fatal: isFatalApiStatus(statusCode),
        costUsd: payloadCost(payload),
      },
    )
  }

  const failedTasks = Array.isArray(payload.tasks)
    ? payload.tasks.filter(task => task?.status_code !== 20000)
    : []

  if ((payload.tasks_error ?? 0) > 0 || failedTasks.length > 0) {
    const statusCode = failedTasks[0]?.status_code
    const summary = failedTasks
      .map(task => `${task?.status_code ?? 'unknown'}: ${task?.status_message ?? 'task failed'}`)
      .join('; ')
    throw new DataForSeoApiError(
      `DataForSEO task failure for ${endpoint}${summary ? ` (${summary})` : ''}`,
      {
        endpoint,
        statusCode,
        retryable: failedTasks.some(task => isRetryableApiStatus(task?.status_code)),
        fatal: failedTasks.some(task => isFatalApiStatus(task?.status_code)),
        costUsd: payloadCost(payload),
      },
    )
  }
}

export class DataForSeoClient {
  constructor({ login, password, fetchImpl = globalThis.fetch, baseUrl = DEFAULT_BASE_URL }) {
    this.login = requireCredential(login, 'DataForSEO login')
    this.password = requireCredential(password, 'DataForSEO password')
    if (typeof fetchImpl !== 'function') {
      throw new TypeError('fetchImpl must be a function')
    }
    this.fetchImpl = fetchImpl
    this.baseUrl = String(baseUrl).replace(/\/$/, '')
  }

  async post(endpoint, tasks, { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS } = {}) {
    if (typeof endpoint !== 'string' || !endpoint.startsWith('/v3/')) {
      throw new TypeError('DataForSEO endpoint must start with /v3/')
    }
    if (!Array.isArray(tasks) || tasks.length === 0) {
      throw new TypeError('DataForSEO request must contain at least one task')
    }

    const authorization = Buffer.from(`${this.login}:${this.password}`, 'utf8').toString('base64')
    const normalizedTimeoutMs = normalizeTimeout(timeoutMs)
    const requestOptions = {
      method: 'POST',
      headers: {
        Authorization: `Basic ${authorization}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(tasks),
    }
    if (normalizedTimeoutMs != null) {
      requestOptions.signal = AbortSignal.timeout(normalizedTimeoutMs)
    }

    let response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${endpoint}`, requestOptions)
    } catch (error) {
      throw new DataForSeoApiError(
        `DataForSEO network request failed for ${endpoint}: ${error?.message ?? 'unknown error'}`,
        { endpoint, billingUncertain: true },
      )
    }

    let responseText
    try {
      responseText = await response.text()
    } catch (error) {
      throw new DataForSeoApiError(
        `DataForSEO response body read failed for ${endpoint}: ${error?.message ?? 'unknown error'}`,
        {
          endpoint,
          statusCode: response.status,
          fatal: isFatalHttpStatus(response.status),
          billingUncertain: true,
        },
      )
    }
    let payload
    try {
      payload = JSON.parse(responseText)
    } catch {
      throw new DataForSeoApiError(
        `DataForSEO returned non-JSON data for ${endpoint} (HTTP ${response.status})`,
        {
          endpoint,
          statusCode: response.status,
          fatal: isFatalHttpStatus(response.status),
          billingUncertain: true,
        },
      )
    }

    if (!response.ok) {
      throw new DataForSeoApiError(
        `DataForSEO HTTP ${response.status} for ${endpoint}${apiMessage(payload)}`,
        {
          endpoint,
          statusCode: response.status,
          retryable: isRetryableHttpStatus(response.status),
          fatal: isFatalHttpStatus(response.status),
          costUsd: payloadCost(payload),
        },
      )
    }

    assertSuccessfulPayload(payload, endpoint)
    return payload
  }
}
