import { describe, expect, it } from 'vitest'

import config from './vite.config'

describe('Vite Market proxy', () => {
  it('forwards the same-origin catalog bridge during local development', () => {
    const proxy = (config as {
      server?: { proxy?: Record<string, unknown> }
    }).server?.proxy ?? {}

    expect(
      Object.keys(proxy).some((pattern) =>
        new RegExp(pattern).test('/market/catalog/api/v1/plugins')
      )
    ).toBe(true)
  })

  it('forwards the GitHub mirror speed test during local development', () => {
    const proxy = (config as {
      server?: { proxy?: Record<string, unknown> }
    }).server?.proxy ?? {}

    expect(
      Object.keys(proxy).some((pattern) =>
        new RegExp(pattern).test('/market/github-proxy/measure')
      )
    ).toBe(true)
  })

  it('forwards the knowledge bridge but keeps the /knowledge page on Vite', () => {
    const proxy = (config as {
      server?: { proxy?: Record<string, unknown> }
    }).server?.proxy ?? {}
    const patterns = Object.keys(proxy).map((pattern) => new RegExp(pattern))

    expect(patterns.some((pattern) => pattern.test('/market/knowledge/packs/jobs?token=t'))).toBe(true)
    expect(patterns.some((pattern) => pattern.test('/knowledge'))).toBe(false)
  })

  it('forwards only the hosted document API namespace during local development', () => {
    const proxy = (config as {
      server?: { proxy?: Record<string, unknown> }
    }).server?.proxy ?? {}

    expect(proxy).toHaveProperty('/api/documents')
    expect(proxy).not.toHaveProperty('/api')
  })

  it('forwards the CSRF token bootstrap endpoint during local development', () => {
    const proxy = (config as {
      server?: { proxy?: Record<string, unknown> }
    }).server?.proxy ?? {}

    expect(
      Object.keys(proxy).some((pattern) =>
        new RegExp(pattern).test('/security/csrf-token')
      )
    ).toBe(true)
  })
})
