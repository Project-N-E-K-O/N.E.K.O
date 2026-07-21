const DEFAULT_BASE_URL = 'https://api.dataforseo.com'

export const DATAFORSEO_ENDPOINTS = Object.freeze({
  searchVolume: '/v3/keywords_data/google_ads/search_volume/live',
  keywordDifficulty: '/v3/dataforseo_labs/google/bulk_keyword_difficulty/live',
  organicSerp: '/v3/serp/google/organic/live/advanced',
})

export class DataForSeoApiError extends Error {
  constructor(message, { endpoint, statusCode } = {}) {
    super(message)
    this.name = 'DataForSeoApiError'
    this.endpoint = endpoint
    this.statusCode = statusCode
  }
}

function requireCredential(value, name) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new TypeError(`${name} must be a non-empty string`)
  }
  return value
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
      { endpoint },
    )
  }

  if (payload.status_code !== 20000) {
    throw new DataForSeoApiError(
      `DataForSEO rejected ${endpoint}${apiMessage(payload)}`,
      { endpoint, statusCode: payload.status_code },
    )
  }

  const failedTasks = Array.isArray(payload.tasks)
    ? payload.tasks.filter(task => task?.status_code !== 20000)
    : []

  if ((payload.tasks_error ?? 0) > 0 || failedTasks.length > 0) {
    const summary = failedTasks
      .map(task => `${task?.status_code ?? 'unknown'}: ${task?.status_message ?? 'task failed'}`)
      .join('; ')
    throw new DataForSeoApiError(
      `DataForSEO task failure for ${endpoint}${summary ? ` (${summary})` : ''}`,
      { endpoint, statusCode: failedTasks[0]?.status_code },
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

  async post(endpoint, tasks) {
    if (typeof endpoint !== 'string' || !endpoint.startsWith('/v3/')) {
      throw new TypeError('DataForSEO endpoint must start with /v3/')
    }
    if (!Array.isArray(tasks) || tasks.length === 0) {
      throw new TypeError('DataForSEO request must contain at least one task')
    }

    const authorization = Buffer.from(`${this.login}:${this.password}`, 'utf8').toString('base64')
    let response
    try {
      response = await this.fetchImpl(`${this.baseUrl}${endpoint}`, {
        method: 'POST',
        headers: {
          Authorization: `Basic ${authorization}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(tasks),
      })
    } catch (error) {
      throw new DataForSeoApiError(
        `DataForSEO network request failed for ${endpoint}: ${error?.message ?? 'unknown error'}`,
        { endpoint },
      )
    }

    const responseText = await response.text()
    let payload
    try {
      payload = JSON.parse(responseText)
    } catch {
      throw new DataForSeoApiError(
        `DataForSEO returned non-JSON data for ${endpoint} (HTTP ${response.status})`,
        { endpoint, statusCode: response.status },
      )
    }

    if (!response.ok) {
      throw new DataForSeoApiError(
        `DataForSEO HTTP ${response.status} for ${endpoint}${apiMessage(payload)}`,
        { endpoint, statusCode: response.status },
      )
    }

    assertSuccessfulPayload(payload, endpoint)
    return payload
  }
}
