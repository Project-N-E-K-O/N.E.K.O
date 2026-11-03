import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { reactive } from 'vue'
import { fetchBridge } from '@/api/marketBridge'
import { useMarketInstalledStore } from './marketInstalled'

const pluginState = reactive({ registryRevision: 0 })
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginState }))
vi.mock('@/api/marketBridge', () => ({ fetchBridge: vi.fn() }))
const first = {
  plugin_id: 'demo',
  effective_source: 'market',
  effective_version: '1.0.0',
  latest_install_source: {
    plugin_market_id: '15',
    channel: 'stable',
    version: '1.0.0',
    package_url: 'https://example/demo.zip',
  },
}
const response = (installed: unknown[]) =>
  new Response(JSON.stringify({ installed }), { status: 200 })
function deferred<T>() {
  let resolve!: (value: T) => void
  return {
    promise: new Promise<T>((r) => {
      resolve = r
    }),
    resolve: (value: T) => resolve(value),
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  pluginState.registryRevision = 0
})

describe('shared installed market projection', () => {
  it('deduplicates a snapshot request and indexes market and runtime identities separately', async () => {
    const request = deferred<Response | null>()
    vi.mocked(fetchBridge).mockReturnValue(request.promise)
    const store = useMarketInstalledStore()
    const a = store.ensureFresh()
    const b = store.ensureFresh({ force: true })
    expect(fetchBridge).toHaveBeenCalledTimes(1)
    request.resolve(response([first, { plugin_id: '15', effective_version: '9.0.0' }]))
    await Promise.all([a, b])
    expect(store.getInstalledState({ id: 15, slug: 'elsewhere' })?.installed_version).toBe('1.0.0')
    expect(store.getInstalledState({ id: 20, slug: '15' })?.installed_version).toBe('9.0.0')
    expect(store.loaded).toBe(true)
    expect(store.stale).toBe(false)
    await store.ensureFresh()
    expect(fetchBridge).toHaveBeenCalledTimes(1)
  })

  it('invalidates without a mounted market, then replaces the snapshot after an uninstall', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(response([first]))
      .mockResolvedValueOnce(response([]))
    const store = useMarketInstalledStore()
    await store.ensureFresh()
    pluginState.registryRevision += 1
    expect(store.stale).toBe(true)
    expect(store.loaded).toBe(true)
    expect(store.entries).toHaveLength(1)
    expect(fetchBridge).toHaveBeenCalledTimes(1)
    await store.ensureFresh()
    expect(store.entries).toEqual([])
    expect(store.getInstalledState({ id: 15 })).toBeUndefined()
    expect(store.stale).toBe(false)
  })

  it('preserves the successful snapshot and stays retryable after a failed refresh', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(response([first]))
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(response([]))
    const store = useMarketInstalledStore()
    await store.ensureFresh()
    pluginState.registryRevision += 1
    expect(await store.ensureFresh()).toBeNull()
    expect(store.loaded).toBe(true)
    expect(store.stale).toBe(true)
    expect(store.entries[0]?.installed_version).toBe('1.0.0')
    await store.ensureFresh()
    expect(store.entries).toEqual([])
    expect(store.stale).toBe(false)
  })

  it('retains the snapshot after a malformed successful response', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(response([first]))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ error: 'missing installed projection' }))
      )
    const store = useMarketInstalledStore()
    await store.ensureFresh()
    expect(await store.ensureFresh({ force: true })).toBeNull()
    expect(store.entries[0]?.installed_version).toBe('1.0.0')
    expect(store.loaded).toBe(true)
    expect(store.stale).toBe(true)
  })

  it('rejects malformed installed entries without erasing the last snapshot', async () => {
    vi.mocked(fetchBridge)
      .mockResolvedValueOnce(response([first]))
      .mockResolvedValueOnce(response([null]))
    const store = useMarketInstalledStore()
    await store.ensureFresh()
    await expect(store.ensureFresh({ force: true })).resolves.toBeNull()
    expect(store.entries[0]?.installed_version).toBe('1.0.0')
    expect(store.loaded).toBe(true)
    expect(store.stale).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('does not consider a first failed request an empty loaded snapshot', async () => {
    vi.mocked(fetchBridge).mockRejectedValueOnce(new Error('offline'))
    const store = useMarketInstalledStore()
    expect(await store.ensureFresh()).toBeNull()
    expect(store.loaded).toBe(false)
    expect(store.stale).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('rejects a pre-invalidation response after a newer snapshot has succeeded', async () => {
    const old = deferred<Response | null>()
    const current = deferred<Response | null>()
    vi.mocked(fetchBridge).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const store = useMarketInstalledStore()
    const pendingOld = store.ensureFresh()
    pluginState.registryRevision += 1
    const pendingCurrent = store.ensureFresh()
    current.resolve(response([]))
    await pendingCurrent
    old.resolve(response([first]))
    expect(await pendingOld).toBeNull()
    expect(store.entries).toEqual([])
    expect(store.stale).toBe(false)
  })

  it('keeps loading the current request when a superseded request finishes first', async () => {
    const old = deferred<Response | null>()
    const current = deferred<Response | null>()
    vi.mocked(fetchBridge).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const store = useMarketInstalledStore()
    const pendingOld = store.ensureFresh()
    pluginState.registryRevision += 1
    const pendingCurrent = store.ensureFresh()
    old.resolve(response([first]))
    await pendingOld
    expect(store.loading).toBe(true)
    expect(store.loaded).toBe(false)
    expect(store.stale).toBe(true)
    current.resolve(response([]))
    await pendingCurrent
    expect(store.loading).toBe(false)
    expect(store.stale).toBe(false)
  })
})
