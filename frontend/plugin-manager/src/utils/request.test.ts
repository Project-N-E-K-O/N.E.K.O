// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest'
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

  it('does not hide a network error from an automatic panel request', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    await expect(rejectWith({
      message: 'Network Error',
      request: {},
    }, {
      suppressPluginNotRunningMessage: true,
    } as AxiosRequestConfig)).rejects.toThrow('Network Error')

    expect(consoleError).toHaveBeenCalledWith('Response error:', expect.anything())
    expect(requestMocks.errorMessage).toHaveBeenCalledWith('messages.networkError')
    consoleError.mockRestore()
  })
})
