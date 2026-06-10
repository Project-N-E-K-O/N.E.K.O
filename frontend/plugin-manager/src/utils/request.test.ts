// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'

import { formatHttpError, stripJsonContentTypeForFormData } from './request'

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
