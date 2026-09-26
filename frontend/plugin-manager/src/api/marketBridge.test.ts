// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchBridge } from './marketBridge'

function stubFetch(): void {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).startsWith('/market/bridge-token')) {
      return new Response(JSON.stringify({ bridge_token: 'tok' }), { status: 200 })
    }
    throw new TypeError('Failed to fetch')
  }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchBridge transport failures', () => {
  it('reports a rejected fetch as null by default', async () => {
    stubFetch()
    await expect(fetchBridge('/market/installed')).resolves.toBeNull()
  })

  it('rethrows a rejected fetch when the caller asks for it', async () => {
    stubFetch()
    // Install callers read `null` as "pairing required"; a network failure
    // must stay distinguishable so their manual-download fallback still runs.
    await expect(
      fetchBridge('/market/install', { method: 'POST' }, { throwOnTransportError: true }),
    ).rejects.toThrow('Failed to fetch')
  })
})
