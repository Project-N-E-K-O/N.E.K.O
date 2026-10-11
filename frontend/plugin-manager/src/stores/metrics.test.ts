import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { getAllMetrics, getPluginMetrics } from '@/api/metrics'
import { useMetricsStore } from './metrics'

vi.mock('@/api/metrics', () => ({
  getAllMetrics: vi.fn(),
  getPluginMetrics: vi.fn(),
  getPluginMetricsHistory: vi.fn(),
}))

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

const metric = (plugin_id: string) => ({ plugin_id, timestamp: '2026-10-09T00:00:00Z' }) as any

describe('metrics store fetchAllMetrics', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
    vi.mocked(getAllMetrics).mockReset()
    vi.mocked(getPluginMetrics).mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('drops a response that arrives after a newer request took over', async () => {
    const stale = deferred<any>()
    const fresh = deferred<any>()
    vi.mocked(getAllMetrics).mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise)
    const store = useMetricsStore()

    const first = store.fetchAllMetrics()
    // The 15s guard releases the slot while the first request is still in flight.
    vi.advanceTimersByTime(15000)
    const second = store.fetchAllMetrics()

    fresh.resolve({ metrics: [metric('alive')], global: { total: 1 } })
    await expect(second).resolves.toMatchObject({ global: { total: 1 } })
    expect(Object.keys(store.currentMetrics)).toEqual(['alive'])

    stale.resolve({ metrics: [metric('alive'), metric('stopped')], global: { total: 2 } })
    await expect(first).resolves.toBeUndefined()
    expect(Object.keys(store.currentMetrics)).toEqual(['alive'])
    expect(store.allMetrics.map((m) => m.plugin_id)).toEqual(['alive'])
  })

  it('does not clear the pending slot of a newer request when an old one settles', async () => {
    const stale = deferred<any>()
    const fresh = deferred<any>()
    vi.mocked(getAllMetrics).mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise)
    const store = useMetricsStore()

    store.fetchAllMetrics()
    vi.advanceTimersByTime(15000)
    const second = store.fetchAllMetrics()

    stale.resolve({ metrics: [] })
    await Promise.resolve()
    await Promise.resolve()
    // The newer request is still pending, so a third call joins it instead of starting another.
    const third = store.fetchAllMetrics()
    expect(store.loading).toBe(true)
    expect(getAllMetrics).toHaveBeenCalledTimes(2)

    fresh.resolve({ metrics: [], global: { total: 0 } })
    await expect(third).resolves.toMatchObject({ global: { total: 0 } })
    await second
    expect(store.loading).toBe(false)
  })

  it('keeps a per-plugin sample fetched after the full snapshot was requested', async () => {
    const full = deferred<any>()
    vi.mocked(getAllMetrics).mockReturnValueOnce(full.promise)
    vi.mocked(getPluginMetrics).mockResolvedValueOnce({ metrics: metric('started') } as any)
    const store = useMetricsStore()

    const pending = store.fetchAllMetrics()
    await store.fetchPluginMetrics('started')
    full.resolve({ metrics: [metric('other')] })
    await pending

    expect(Object.keys(store.currentMetrics).sort()).toEqual(['other', 'started'])
  })

  it('keeps a plugin removed when its own later request found no metrics', async () => {
    const full = deferred<any>()
    vi.mocked(getAllMetrics).mockReturnValueOnce(full.promise)
    vi.mocked(getPluginMetrics).mockRejectedValueOnce({ response: { status: 404 } })
    const store = useMetricsStore()

    const pending = store.fetchAllMetrics()
    await store.fetchPluginMetrics('stopped')
    full.resolve({ metrics: [metric('other'), metric('stopped')] })
    await pending

    expect(Object.keys(store.currentMetrics)).toEqual(['other'])
  })

  it('lets a full snapshot requested later replace an older per-plugin sample', async () => {
    vi.mocked(getPluginMetrics).mockResolvedValueOnce({ metrics: metric('stopped') } as any)
    vi.mocked(getAllMetrics).mockResolvedValueOnce({ metrics: [metric('other')] } as any)
    const store = useMetricsStore()

    await store.fetchPluginMetrics('stopped')
    await store.fetchAllMetrics()

    expect(Object.keys(store.currentMetrics)).toEqual(['other'])
  })

  it('ignores an older per-plugin response that settles after a newer one', async () => {
    const older = deferred<any>()
    vi.mocked(getPluginMetrics)
      .mockReturnValueOnce(older.promise)
      .mockRejectedValueOnce({ response: { status: 404 } })
    const store = useMetricsStore()

    const first = store.fetchPluginMetrics('stopped')
    await store.fetchPluginMetrics('stopped')
    older.resolve({ metrics: metric('stopped') })
    await first

    expect(store.currentMetrics).toEqual({})
  })

  it('ignores a per-plugin response issued before a full refresh that already applied', async () => {
    const older = deferred<any>()
    vi.mocked(getPluginMetrics).mockReturnValueOnce(older.promise)
    vi.mocked(getAllMetrics).mockResolvedValueOnce({ metrics: [metric('other')] } as any)
    const store = useMetricsStore()

    const pending = store.fetchPluginMetrics('stopped')
    await store.fetchAllMetrics()
    older.resolve({ metrics: metric('stopped') })
    await pending

    expect(Object.keys(store.currentMetrics)).toEqual(['other'])
  })

  it('removes a plugin whose id matches an Object.prototype property', async () => {
    vi.mocked(getAllMetrics)
      .mockResolvedValueOnce({ metrics: [metric('constructor'), metric('other')] } as any)
      .mockResolvedValueOnce({ metrics: [metric('other')] } as any)
    const store = useMetricsStore()

    await store.fetchAllMetrics()
    expect(Object.keys(store.currentMetrics).sort()).toEqual(['constructor', 'other'])
    await store.fetchAllMetrics()

    expect(Object.keys(store.currentMetrics)).toEqual(['other'])
  })

  it('keeps a newer per-plugin sample for a plugin named __proto__', async () => {
    const full = deferred<any>()
    vi.mocked(getAllMetrics).mockReturnValueOnce(full.promise)
    vi.mocked(getPluginMetrics).mockResolvedValueOnce({ metrics: metric('__proto__') } as any)
    const store = useMetricsStore()

    const pending = store.fetchAllMetrics()
    await store.fetchPluginMetrics('__proto__')
    full.resolve({ metrics: [metric('other')] })
    await pending

    expect(Object.keys(store.currentMetrics).sort()).toEqual(['__proto__', 'other'])
    expect(store.getCurrentMetrics('__proto__')?.plugin_id).toBe('__proto__')
  })

  it('returns null for a missing plugin named like an Object.prototype property', () => {
    const store = useMetricsStore()
    expect(store.getCurrentMetrics('constructor')).toBeNull()
    expect(store.getCurrentMetrics('toString')).toBeNull()
  })
})
