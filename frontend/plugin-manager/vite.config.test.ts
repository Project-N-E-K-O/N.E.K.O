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

  it('forwards only the hosted document API namespace during local development', () => {
    const proxy = (config as {
      server?: { proxy?: Record<string, unknown> }
    }).server?.proxy ?? {}

    expect(proxy).toHaveProperty('/api/documents')
    expect(proxy).not.toHaveProperty('/api')
  })
})
