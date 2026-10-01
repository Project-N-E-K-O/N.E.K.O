// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios, { AxiosHeaders } from 'axios'
import type { AxiosError, AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios'

const requestMocks = vi.hoisted(() => ({
  errorMessage: vi.fn(),
  closeAllMessages: vi.fn(),
  connectionStore: {
    disconnected: false,
    markConnected: vi.fn(),
    markDisconnected: vi.fn(),
  },
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: requestMocks.errorMessage,
    closeAll: requestMocks.closeAllMessages,
  },
}))

vi.mock('@/stores/connection', () => ({
  useConnectionStore: () => requestMocks.connectionStore,
}))

vi.mock('@/i18n', () => ({
  i18n: {
    global: {
      t: (key: string) => key,
    },
  },
}))

import request, { formatHttpError, stripJsonContentTypeForFormData } from './request'

type ErrorScenario = {
  message: string
  code?: string
  request?: unknown
  response?: {
    status: number
    data: unknown
    headers?: Record<string, string>
  }
}

function rejectWith(scenario: ErrorScenario, config: AxiosRequestConfig = {}): Promise<unknown> {
  return request.get('/test', {
    ...config,
    adapter: async (requestConfig) => {
      const error = Object.assign(new Error(scenario.message), scenario, {
        config: requestConfig,
        isAxiosError: true,
        name: 'AxiosError',
        toJSON: () => ({}),
      }) as AxiosError
      throw error
    },
  })
}

describe('request FormData handling', () => {
  it('removes application/json Content-Type so the browser can set multipart boundary', () => {
    const formData = new FormData()
    formData.append('file', new Blob(['demo']), 'demo.neko-plugin')
    const config = {
      data: formData,
      headers: {
        'Content-Type': 'application/json',
      },
    } as unknown as InternalAxiosRequestConfig

    stripJsonContentTypeForFormData(config)

    expect((config.headers as Record<string, unknown>)['Content-Type']).toBeUndefined()
  })

  it('leaves JSON Content-Type intact for JSON payloads', () => {
    const config = {
      data: { plugin: 'demo' },
      headers: {
        'Content-Type': 'application/json',
      },
    } as unknown as InternalAxiosRequestConfig

    stripJsonContentTypeForFormData(config)

    expect((config.headers as Record<string, unknown>)['Content-Type']).toBe('application/json')
  })
})

describe('formatHttpError', () => {
  it('formats FastAPI 422 array details into readable messages', () => {
    const message = formatHttpError({
      response: {
        data: {
          detail: [
            {
              loc: ['body', 'plugin_refs', 0, 'directory_name'],
              msg: 'Field required',
            },
            {
              loc: ['query', 'on_conflict'],
              msg: 'String should match pattern',
            },
          ],
        },
      },
    })

    expect(message).toBe(
      'body.plugin_refs.0.directory_name: Field required; query.on_conflict: String should match pattern',
    )
  })

  it('formats object details without leaking [object Object]', () => {
    const message = formatHttpError({
      response: {
        data: {
          detail: {
            code: 'PLUGIN_CLI_INVALID_REQUEST',
            details: {
              action: 'build',
              error_type: 'ValueError',
            },
          },
        },
      },
    })

    expect(message).toContain('PLUGIN_CLI_INVALID_REQUEST')
    expect(message).toContain('ValueError')
    expect(message).not.toContain('[object Object]')
  })

  it('prefers explicit server messages when present', () => {
    const message = formatHttpError({
      response: {
        data: {
          message: 'target_dir must be inside packages root',
          code: 'PLUGIN_CLI_INVALID_REQUEST',
          details: { action: 'build' },
        },
      },
    })

    expect(message).toBe('target_dir must be inside packages root')
  })

  it('returns an empty string for HTTP responses without useful details', () => {
    const message = formatHttpError({
      response: {
        data: {},
      },
      message: 'Request failed with status code 500',
    })

    expect(message).toBe('')
  })
})

describe('hosted panel error suppression', () => {
  beforeEach(() => {
    requestMocks.errorMessage.mockReset()
    requestMocks.closeAllMessages.mockReset()
    requestMocks.connectionStore.disconnected = false
    requestMocks.connectionStore.markConnected.mockReset()
    requestMocks.connectionStore.markDisconnected.mockReset()
    requestMocks.connectionStore.markDisconnected.mockImplementation(() => {
      requestMocks.connectionStore.disconnected = true
    })
  })

  it('silences PLUGIN_NOT_RUNNING only when an automatic panel request opts in', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: { detail: 'Plugin is not running' },
        headers: { 'x-error-code': 'PLUGIN_NOT_RUNNING' },
      },
    }, {
      suppressPluginNotRunningMessage: true,
    } as AxiosRequestConfig)).rejects.toThrow('Request failed with status code 409')

    expect(consoleError).not.toHaveBeenCalled()
    expect(requestMocks.errorMessage).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })

  it('lets a domain caller replace the generic error toast', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Request failed with status code 500',
      response: {
        status: 500,
        data: { detail: 'C:\\Users\\name\\private.neko-plugin is invalid' },
      },
    }, {
      suppressErrorMessage: true,
    } as AxiosRequestConfig)).rejects.toThrow('Request failed with status code 500')

    expect(consoleError).not.toHaveBeenCalled()
    expect(requestMocks.errorMessage).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })

  it('keeps PLUGIN_NOT_RUNNING visible for a user-initiated panel request', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Request failed with status code 409',
      response: {
        status: 409,
        data: { detail: 'Plugin is not running' },
        headers: { 'x-error-code': 'PLUGIN_NOT_RUNNING' },
      },
    }, {
      suppressPluginNotRunningMessage: false,
    } as AxiosRequestConfig)).rejects.toThrow('Request failed with status code 409')

    expect(consoleError).toHaveBeenCalledWith('Response error:', expect.anything())
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('Plugin is not running')
    consoleError.mockRestore()
  })

  it('preserves existing messages for opted-in 404 requests', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Request failed with status code 404',
      response: {
        status: 404,
        data: { detail: 'Missing plugin source' },
      },
    }, {
      preserveMessagesOn404: true,
    } as AxiosRequestConfig)).rejects.toThrow('Request failed with status code 404')

    expect(requestMocks.closeAllMessages).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })

  it('still closes existing messages for ordinary 404 requests', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Request failed with status code 404',
      response: {
        status: 404,
        data: { detail: 'Missing plugin source' },
      },
    })).rejects.toThrow('Request failed with status code 404')

    expect(requestMocks.closeAllMessages).toHaveBeenCalledTimes(1)
    consoleError.mockRestore()
  })

  it('does not hide a 500 response from an automatic panel request', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Request failed with status code 500',
      response: {
        status: 500,
        data: { detail: 'Internal failure' },
      },
    }, {
      suppressPluginNotRunningMessage: true,
    } as AxiosRequestConfig)).rejects.toThrow('Request failed with status code 500')

    expect(consoleError).toHaveBeenCalledWith('Response error:', expect.anything())
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('Internal failure')
    consoleError.mockRestore()
  })

  it('does not repeat a network error when shared probe waiters are already disconnected', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const healthProbe = vi.spyOn(axios, 'get').mockRejectedValue(new Error('health unavailable'))
    requestMocks.connectionStore.disconnected = true

    const results = await Promise.allSettled([
      rejectWith({ message: 'Network Error', request: {} }),
      rejectWith({ message: 'Network Error', request: {} }),
      rejectWith({ message: 'Network Error', request: {} }),
    ])

    expect(results.every((result) => result.status === 'rejected')).toBe(true)
    expect(healthProbe).toHaveBeenCalledTimes(1)
    expect(requestMocks.errorMessage).not.toHaveBeenCalled()
    healthProbe.mockRestore()
    consoleError.mockRestore()
  })

  it('does not hide a network error from an automatic panel request', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const healthProbe = vi.spyOn(axios, 'get').mockRejectedValue(new Error('health unavailable'))

    await expect(rejectWith({
      message: 'Network Error',
      request: {},
    }, {
      suppressPluginNotRunningMessage: true,
    } as AxiosRequestConfig)).rejects.toThrow('Network Error')

    expect(consoleError).toHaveBeenCalledWith('Response error:', expect.anything())
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('messages.networkError')
    expect(requestMocks.connectionStore.markDisconnected).toHaveBeenCalledTimes(1)
    healthProbe.mockRestore()
    consoleError.mockRestore()
  })

  it('keeps the connection marked healthy when the failed endpoint is followed by a successful health check', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const healthProbe = vi.spyOn(axios, 'get').mockResolvedValue({ status: 200 })

    await expect(rejectWith({
      message: 'Network Error',
      request: {},
    })).rejects.toThrow('Network Error')

    expect(healthProbe).toHaveBeenCalledWith('/health', expect.objectContaining({ timeout: 5000 }))
    expect(requestMocks.connectionStore.markConnected).toHaveBeenCalledTimes(1)
    expect(requestMocks.connectionStore.markDisconnected).not.toHaveBeenCalled()
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('messages.requestFailed')
    healthProbe.mockRestore()
    consoleError.mockRestore()
  })

  it('counts a shared failed health probe only once', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const healthProbe = vi.spyOn(axios, 'get').mockRejectedValue(new Error('health unavailable'))

    const results = await Promise.allSettled([
      rejectWith({ message: 'Network Error', request: {} }),
      rejectWith({ message: 'Network Error', request: {} }),
      rejectWith({ message: 'Network Error', request: {} }),
    ])

    expect(results).toHaveLength(3)
    expect(results.every((result) => result.status === 'rejected')).toBe(true)
    expect(healthProbe).toHaveBeenCalledTimes(1)
    expect(requestMocks.connectionStore.markDisconnected).toHaveBeenCalledTimes(1)
    healthProbe.mockRestore()
    consoleError.mockRestore()
  })

  it.each([
    ['ECONNABORTED', {}, 'messages.requestTimeout'],
    ['ETIMEDOUT', { timeoutErrorMessageKey: 'messages.pluginLifecycleTimeout' }, 'messages.pluginLifecycleTimeout'],
  ] as const)('reports %s as a timeout without probing health or marking a disconnect', async (code, config, messageKey) => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const healthProbe = vi.spyOn(axios, 'get')

    await expect(rejectWith({
      message: 'timeout exceeded',
      code,
      request: {},
    }, config as AxiosRequestConfig)).rejects.toMatchObject({ code })

    expect(healthProbe).not.toHaveBeenCalled()
    expect(requestMocks.connectionStore.markDisconnected).not.toHaveBeenCalled()
    expect(requestMocks.errorMessage).toHaveBeenCalledWith(messageKey)
    healthProbe.mockRestore()
    consoleError.mockRestore()
  })

  it('does not treat an intentionally canceled request as a disconnect', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'canceled',
      code: 'ERR_CANCELED',
      request: {},
    })).rejects.toMatchObject({ code: 'ERR_CANCELED' })

    expect(consoleError).not.toHaveBeenCalled()
    expect(requestMocks.connectionStore.markDisconnected).not.toHaveBeenCalled()
    expect(requestMocks.errorMessage).not.toHaveBeenCalled()
    consoleError.mockRestore()
  })
})

describe('mutation CSRF guard', () => {
  it('does not bootstrap the lifecycle token for unrelated mutations', async () => {
    const tokenBootstrap = vi.spyOn(axios, 'get')
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({
      data: { ok: true },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
      request: {},
    }))

    await expect(request.post('/api/model-config/slots', {}, { adapter })).resolves.toEqual({ ok: true })
    expect(tokenBootstrap).not.toHaveBeenCalled()
    expect(adapter).toHaveBeenCalledTimes(1)
    tokenBootstrap.mockRestore()
  })

  it('does not retry a bootstrap 403 as the lifecycle mutation', async () => {
    const retryAdapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({
      data: { csrf_token: 'fresh-token' },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
      request: {},
    }))
    const bootstrapError = Object.assign(new Error('bootstrap rejected'), {
      config: {
        url: '/security/csrf-token',
        method: 'get',
        headers: {},
        adapter: retryAdapter,
      },
      response: {
        status: 403,
        data: { error_code: 'csrf_validation_failed' },
        headers: {},
      },
      isAxiosError: true,
      name: 'AxiosError',
    })
    const tokenBootstrap = vi.spyOn(axios, 'get').mockRejectedValue(bootstrapError)
    const mutationAdapter = vi.fn()

    await expect(request.post('/plugin/demo/stop', {}, { adapter: mutationAdapter }))
      .rejects.toMatchObject({ response: { status: 403 } })
    expect(tokenBootstrap).toHaveBeenCalledTimes(1)
    expect(retryAdapter).not.toHaveBeenCalled()
    expect(mutationAdapter).not.toHaveBeenCalled()
    tokenBootstrap.mockRestore()
  })

  it('bootstraps the token, attaches it, and retries one rejected mutation', async () => {
    const tokenBootstrap = vi.spyOn(axios, 'get').mockResolvedValue({
      data: { csrf_token: 'csrf-test-token' },
    } as any)
    const sentTokens: unknown[] = []
    let attempts = 0

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const headers = config.headers as any
      sentTokens.push(typeof headers?.get === 'function'
        ? headers.get('X-CSRF-Token')
        : headers?.['X-CSRF-Token'] ?? headers?.['x-csrf-token'])
      attempts += 1
      if (attempts === 1) {
        throw Object.assign(new Error('CSRF rejected'), {
          config,
          response: {
            status: 403,
            data: { detail: { csrf_failure: 'token' } },
            headers: { 'X-Error-Code': 'csrf_validation_failed' },
          },
          isAxiosError: true,
          name: 'AxiosError',
        })
      }
      return {
        data: { ok: true },
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
        request: {},
      }
    })

    await expect(request.post('/plugin/demo/stop', {}, { adapter })).resolves.toEqual({ ok: true })
    expect(tokenBootstrap).toHaveBeenCalledTimes(2)
    expect(adapter).toHaveBeenCalledTimes(2)
    expect(sentTokens).toEqual(['csrf-test-token', 'csrf-test-token'])
    tokenBootstrap.mockRestore()
  })
})


describe('CSRF bootstrap error policy', () => {
  beforeEach(() => {
    vi.resetModules()
    requestMocks.errorMessage.mockClear()
  })

  it('uses generic bootstrap timeout and preserves caller silence without sharing error config', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockRejectedValue(Object.assign(new Error('timeout'), {
      isAxiosError: true, code: 'ECONNABORTED', request: {},
      config: { url: '/security/csrf-token', method: 'get' },
    }))
    const adapter = vi.fn()
    const results = await Promise.allSettled([
      fresh.post('/plugin/demo/start', {}, { adapter, timeoutErrorMessageKey: 'messages.pluginLifecycleTimeout' } as AxiosRequestConfig),
      fresh.post('/plugin/demo/stop', {}, { adapter, suppressErrorMessage: true } as AxiosRequestConfig),
    ])
    expect(bootstrap).toHaveBeenCalledTimes(1)
    expect(adapter).not.toHaveBeenCalled()
    expect(results.every(result => result.status === 'rejected')).toBe(true)
    expect(requestMocks.errorMessage).toHaveBeenCalledTimes(1)
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('messages.requestTimeout')
    for (const result of results) {
      if (result.status === 'rejected') {
        expect(result.reason.config.csrfBootstrapFailed).toBe(true)
        expect(result.reason.config.timeoutErrorMessageKey).toBeUndefined()
      }
    }
    bootstrap.mockRestore()
  })

  it('uses the existing translated failure message for invalid bootstrap data', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockResolvedValue({ data: {} } as any)
    const adapter = vi.fn()
    await expect(fresh.post('/plugin/demo/start', {}, { adapter })).rejects.toMatchObject({
      message: 'messages.requestFailed', config: { csrfBootstrapFailed: true },
    })
    expect(adapter).not.toHaveBeenCalled()
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('messages.requestFailed')
    bootstrap.mockRestore()
  })

  it('does not refresh or retry a rejected Origin', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockResolvedValue({ data: { csrf_token: 'token' } } as any)
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      throw Object.assign(new Error('Origin rejected'), {
        config, isAxiosError: true,
        response: { status: 403, data: {}, headers: {
          'X-Error-Code': 'csrf_validation_failed', 'X-CSRF-Failure': 'origin',
        } },
      })
    })
    await expect(fresh.post('/plugin/demo/stop', {}, { adapter })).rejects.toThrow('Origin rejected')
    expect(adapter).toHaveBeenCalledTimes(1)
    expect(bootstrap).toHaveBeenCalledTimes(1)
    bootstrap.mockRestore()
  })
})

describe('package import CSRF contract', () => {
  beforeEach(() => {
    vi.resetModules()
    requestMocks.errorMessage.mockClear()
  })

  function readSent(config: InternalAxiosRequestConfig) {
    // dispatchRequest normalizes headers to AxiosHeaders before the adapter.
    const headers = AxiosHeaders.from(config.headers)
    return {
      url: config.url,
      method: config.method,
      token: headers.get('X-CSRF-Token') ?? null,
      contentType: headers.get('Content-Type'),
      timeout: config.timeout,
      form: config.data instanceof FormData ? config.data : null,
    }
  }

  function packageForm(): FormData {
    const form = new FormData()
    form.append('file', new File(['pkg'], 'demo.neko-plugin'))
    return form
  }

  const tokenFailure = (config: InternalAxiosRequestConfig) => Object.assign(new Error('CSRF rejected'), {
    config,
    isAxiosError: true,
    name: 'AxiosError',
    response: {
      status: 403,
      data: { detail: { error_code: 'csrf_validation_failed', csrf_failure: 'token' } },
      headers: { 'X-Error-Code': 'csrf_validation_failed', 'X-CSRF-Failure': 'token' },
    },
  })

  const ok = (config: InternalAxiosRequestConfig) => ({
    data: { ok: true }, status: 200, statusText: 'OK', headers: {}, config, request: {},
  })

  it('attaches the token to the multipart upload used by the import dialog', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockResolvedValue({ data: { csrf_token: 'tok-1' } } as never)
    const sent: ReturnType<typeof readSent>[] = []
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      sent.push(readSent(config))
      return ok(config)
    })
    const form = packageForm()

    await expect(fresh.post('/plugin-cli/upload', form, {
      adapter, timeout: 300_000, suppressErrorMessage: true,
    } as AxiosRequestConfig)).resolves.toEqual({ ok: true })

    expect(bootstrap).toHaveBeenCalledWith('/security/csrf-token', expect.anything())
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ token: 'tok-1', form, timeout: 300_000 })
    expect(String(sent[0]!.contentType ?? '')).not.toContain('application/json')
    bootstrap.mockRestore()
  })

  it('retries a multipart token failure once with a fresh token and the same FormData', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get')
      .mockResolvedValueOnce({ data: { csrf_token: 'stale' } } as never)
      .mockResolvedValueOnce({ data: { csrf_token: 'rotated' } } as never)
    const sent: ReturnType<typeof readSent>[] = []
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      sent.push(readSent(config))
      if (sent.length === 1) throw tokenFailure(config)
      return ok(config)
    })
    const form = packageForm()

    await expect(fresh.post('/plugin-cli/upload-and-install?on_conflict=fail', form, {
      adapter, timeout: 300_000, suppressErrorMessage: true,
    } as AxiosRequestConfig)).resolves.toEqual({ ok: true })

    expect(bootstrap).toHaveBeenCalledTimes(2)
    expect(sent.map((item) => item.token)).toEqual(['stale', 'rotated'])
    expect(sent.every((item) => item.form === form && item.timeout === 300_000)).toBe(true)
    expect(sent.every((item) => !String(item.contentType ?? '').includes('application/json'))).toBe(true)
    bootstrap.mockRestore()
  })

  it('stops after the single retry without a toast for silenced callers', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockResolvedValue({ data: { csrf_token: 'tok' } } as never)
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => { throw tokenFailure(config) })

    await expect(fresh.post('/plugin-cli/install', { package: 'demo.neko-plugin' }, {
      adapter, suppressErrorMessage: true,
    } as AxiosRequestConfig)).rejects.toMatchObject({ response: { status: 403 } })
    expect(adapter).toHaveBeenCalledTimes(2)
    expect(requestMocks.errorMessage).not.toHaveBeenCalled()
    bootstrap.mockRestore()
  })

  it('never sends the package when token bootstrap fails', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockRejectedValue(Object.assign(new Error('bootstrap 403'), {
      isAxiosError: true,
      response: { status: 403, data: { detail: { error_code: 'csrf_validation_failed', csrf_failure: 'origin' } }, headers: {} },
      config: { url: '/security/csrf-token', method: 'get' },
    }))
    const adapter = vi.fn()

    await expect(fresh.post('/plugin-cli/upload', packageForm(), {
      adapter, suppressErrorMessage: true,
    } as AxiosRequestConfig)).rejects.toMatchObject({ config: { csrfBootstrapFailed: true } })
    expect(adapter).not.toHaveBeenCalled()
    bootstrap.mockRestore()
  })

  it('covers exactly the server-guarded package routes', async () => {
    const fresh = (await import('./request')).default
    const bootstrap = vi.spyOn(axios, 'get').mockResolvedValue({ data: { csrf_token: 'tok' } } as never)
    const sent: ReturnType<typeof readSent>[] = []
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      sent.push(readSent(config))
      return ok(config)
    })

    for (const url of [
      '/plugin-cli/upload',
      '/plugin-cli/upload-and-install',
      '/plugin-cli/upload-and-unpack',
      '/plugin-cli/install',
      '/plugin-cli/unpack',
      '/plugin-cli/install-plan',
      '/plugin-cli/build',
    ]) {
      await fresh.post(url, {}, { adapter })
    }
    await fresh.delete('/plugin-cli/upload?package=demo.neko-plugin', { adapter })

    expect(sent.map((item) => [item.method, item.url, item.token])).toEqual([
      ['post', '/plugin-cli/upload', 'tok'],
      ['post', '/plugin-cli/upload-and-install', 'tok'],
      ['post', '/plugin-cli/upload-and-unpack', 'tok'],
      ['post', '/plugin-cli/install', 'tok'],
      ['post', '/plugin-cli/unpack', 'tok'],
      ['post', '/plugin-cli/install-plan', null],
      ['post', '/plugin-cli/build', null],
      ['delete', '/plugin-cli/upload?package=demo.neko-plugin', null],
    ])
    bootstrap.mockRestore()
  })
})
