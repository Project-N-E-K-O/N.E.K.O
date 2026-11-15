import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import { getPlugin, getPlugins, getPluginSummaries, getPluginStatus, refreshPluginsRegistry, setPluginAutoStart } from '@/api/plugins'
import type { PluginMeta, PluginStatusData } from '@/types/api'

vi.mock('@/i18n', () => ({
  getLocale: () => 'zh-CN',
  i18n: { global: { t: (key: string) => key } },
}))

vi.mock('@/api/plugins', () => ({
  getPlugin: vi.fn(),
  getPlugins: vi.fn(),
  getPluginSummaries: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  reloadAllPlugins: vi.fn(),
  setPluginAutoStart: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
}))

function plugin(overrides: Partial<PluginMeta> = {}): PluginMeta {
  return {
    id: 'demo', name: 'Demo', description: '', version: '1.0.0', type: 'plugin',
    status: 'stopped', ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((complete) => { resolve = complete })
  return { promise, resolve }
}

function runtime(status: string): PluginStatusData {
  return { plugin_id: 'demo', source: 'main_process_synthetic', status: { status } }
}

function expectVisibleStatus(store: ReturnType<typeof usePluginStore>, status: string) {
  expect(store.pluginSummariesWithStatus.find(item => item.id === 'demo')?.status).toBe(status)
  expect(store.getPluginById('demo')?.status).toBe(status)
}

describe('plugin state ownership and registry cache invalidation', () => {
  afterEach(() => { vi.restoreAllMocks() })

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    vi.mocked(getPlugin).mockResolvedValue(plugin())
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [plugin()], message: '' })
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(getPluginStatus).mockResolvedValue({ plugins: {} })
    vi.mocked(refreshPluginsRegistry).mockResolvedValue({
      success: true, added: [], updated: ['demo'], removed: [], removed_running: [],
      unchanged: [], failed: [], scanned_count: 1,
    })
  })

  it.each(['child_process', 'main_process_direct', undefined])(
    'never lets a %s business report replace the host-confirmed lifecycle', async (source) => {
      const store = usePluginStore()
      await store.fetchPluginSummaries()
      await store.fetchPluginDetail('demo')
      for (const status of ['running', 'idle']) {
        const report = { plugin_id: 'demo', source, status: { status } }
        vi.mocked(getPluginStatus).mockResolvedValueOnce({ plugins: { demo: report } })
        await store.fetchPluginStatus()
        expectVisibleStatus(store, 'stopped')
        expect(store.pluginStatuses.demo).toEqual(report)
        vi.mocked(getPluginStatus).mockResolvedValueOnce(report)
        await store.fetchPluginStatus('demo')
        expectVisibleStatus(store, 'stopped')
      }
    },
  )

  it('rejects non-lifecycle values even when a snapshot claims a host source', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('idle'))
    await store.fetchPluginStatus('demo')
    expectVisibleStatus(store, 'stopped')
  })

  it('invalidates installed state and returns without waiting for an unrelated detail', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    const slow = deferred<PluginMeta>()
    vi.mocked(getPlugin).mockReturnValueOnce(slow.promise)
    const revision = store.registryRevision
    let settled = false
    const sync = store.syncRegistryAndFetchSummaries().then(() => { settled = true })
    for (let i = 0; i < 20; i++) await Promise.resolve()
    try {
      expect(store.registryRevision).toBe(revision + 1)
      expect(settled).toBe(true)
    } finally {
      slow.resolve(plugin())
      await sync
    }
  })

  it('skips redundant invalidation for an unchanged read refresh, but not a mutation', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    const revision = store.registryRevision
    await store.syncRegistryAndFetchSummaries({ invalidateInstalled: false })
    expect(store.registryRevision).toBe(revision)
    await store.syncRegistryAndFetchSummaries()
    expect(store.registryRevision).toBe(revision + 1)
  })

  it('invalidates a read refresh when install provenance changes without a version change', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    const revision = store.registryRevision
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin({
      install_source: {
        source: 'imported', reason: null, installed_at: '2026-10-21T00:00:00Z',
        source_detail: { package_filename: 'demo.neko-plugin', package_sha256: 'changed' },
      },
    })], message: '' })
    await store.syncRegistryAndFetchSummaries({ invalidateInstalled: false })
    expect(store.registryRevision).toBe(revision + 1)
  })

  it.each(['single', 'full'] as const)('uses the independent %s runtime snapshot in list and detail views', async (scope) => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    vi.mocked(getPluginStatus).mockResolvedValueOnce(scope === 'single'
      ? runtime('running')
      : { plugins: { demo: runtime('running') } })

    await store.fetchPluginStatus(scope === 'single' ? 'demo' : undefined)

    expectVisibleStatus(store, 'running')
  })

  it('also accepts a flat status response from older servers', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    vi.mocked(getPluginStatus).mockResolvedValueOnce({ source: 'main_process_synthetic', status: 'running' } as any)

    await store.fetchPluginStatus('demo')

    expectVisibleStatus(store, 'running')
  })

  it.each(['summary', 'detail'] as const)('does not let an older %s response hide newer runtime state', async (source) => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    const old = deferred<any>()
    let metadata: Promise<void>
    if (source === 'summary') {
      vi.mocked(getPluginSummaries).mockReturnValueOnce(old.promise)
      metadata = store.fetchPluginSummaries(true)
    } else {
      vi.mocked(getPlugin).mockReturnValueOnce(old.promise)
      metadata = store.fetchPluginDetail('demo', true)
    }
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')

    old.resolve(source === 'summary' ? { plugins: [plugin()], message: '' } : plugin())
    await metadata

    expectVisibleStatus(store, 'running')
  })

  it.each(['summary', 'detail'] as const)('does not let an older runtime response replace a newer %s state', async (source) => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    const old = deferred<any>()
    vi.mocked(getPluginStatus).mockReturnValueOnce(old.promise)
    const statusRequest = store.fetchPluginStatus('demo')
    if (source === 'summary') {
      vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin({ status: 'running' })], message: '' })
      await store.fetchPluginSummaries(true)
    } else {
      vi.mocked(getPlugin).mockResolvedValueOnce(plugin({ status: 'running' }))
      await store.fetchPluginDetail('demo', true)
    }

    old.resolve(runtime('stopped'))
    await statusRequest

    expectVisibleStatus(store, 'running')
  })

  it('keeps the newer of two single-plugin runtime responses', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    const old = deferred<any>()
    vi.mocked(getPluginStatus)
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(runtime('running'))
    const oldRequest = store.fetchPluginStatus('demo')
    await store.fetchPluginStatus('demo')

    old.resolve(runtime('stopped'))
    await oldRequest

    expectVisibleStatus(store, 'running')
  })

  it('does not let an older single-plugin response overwrite a newer full runtime snapshot', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    const old = deferred<PluginStatusData>()
    vi.mocked(getPluginStatus)
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce({ plugins: { demo: runtime('running') } })
    const oldRequest = store.fetchPluginStatus('demo')

    await store.fetchPluginStatus(undefined, true)
    old.resolve(runtime('stopped'))
    await oldRequest

    expectVisibleStatus(store, 'running')
  })

  it('accepts an older successful runtime response when the newer request failed', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    const old = deferred<PluginStatusData>()
    vi.mocked(getPluginStatus)
      .mockReturnValueOnce(old.promise)
      .mockRejectedValueOnce(new Error('offline'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const oldRequest = store.fetchPluginStatus('demo')

    await expect(store.fetchPluginStatus('demo')).resolves.toBe('failed')
    old.resolve(runtime('running'))
    await oldRequest

    expectVisibleStatus(store, 'running')
  })

  it.each(['summary', 'detail'] as const)('lets a successful newer %s snapshot supersede an applied runtime state', async (source) => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')
    if (source === 'summary') {
      vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin()], message: '' })
      await store.fetchPluginSummaries(true)
    } else {
      vi.mocked(getPlugin).mockResolvedValueOnce(plugin())
      await store.fetchPluginDetail('demo', true)
    }

    expectVisibleStatus(store, 'stopped')
  })

  it('falls back to metadata when a runtime response has no usable status', async () => {
    const store = usePluginStore()
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin({ status: 'running' })], message: '' })
    await store.fetchPluginSummaries()
    vi.mocked(getPluginStatus).mockResolvedValueOnce({ plugin_id: 'demo', status: {} })

    await store.fetchPluginStatus('demo')

    expectVisibleStatus(store, 'running')
  })

  it.each(['load_failed', 'source_missing'])('preserves registry %s until new metadata clears the fault', async (status) => {
    const store = usePluginStore()
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin({ status })], message: '' })
    vi.mocked(getPlugin).mockResolvedValueOnce(plugin({ status }))
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    // The runtime endpoint synthesizes stopped for an unregistered host; only
    // metadata has the registry's source/load diagnostic.
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('stopped'))
    await store.fetchPluginStatus('demo')
    expectVisibleStatus(store, status)

    await store.fetchPluginSummaries(true)
    expectVisibleStatus(store, 'stopped')
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')
    expectVisibleStatus(store, 'running')
  })

  it('retains readable runtime state after a failed refresh', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')
    vi.mocked(getPluginStatus).mockRejectedValueOnce(new Error('offline'))
    const log = vi.spyOn(console, 'error').mockImplementation(() => {})

    await expect(store.fetchPluginStatus('demo')).resolves.toBe('failed')

    expectVisibleStatus(store, 'running')
    log.mockRestore()
  })

  it('revalidates a loaded detail after registry synchronization replaces the same ID', async () => {
    const store = usePluginStore()
    await store.fetchPluginDetail('demo')
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin({ version: '2.0.0' })], message: '' })
    vi.mocked(getPlugin).mockResolvedValueOnce(plugin({ version: '2.0.0' }))

    await store.syncRegistryAndFetchSummaries()

    expect(store.getPluginById('demo')?.version).toBe('2.0.0')
    expect(getPlugin).toHaveBeenCalledTimes(2)
  })

  it('prevents a detail started before registry synchronization from restoring the old version', async () => {
    const store = usePluginStore()
    const old = deferred<PluginMeta>()
    vi.mocked(getPlugin)
      .mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(plugin({ version: '2.0.0' }))
    const oldRequest = store.fetchPluginDetail('demo')
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin({ version: '2.0.0' })], message: '' })

    await store.syncRegistryAndFetchSummaries()
    old.resolve(plugin())
    await oldRequest

    expect(store.getPluginById('demo')?.version).toBe('2.0.0')
  })

  it('keeps cached detail readable without failing a successful registry sync when revalidation fails', async () => {
    const store = usePluginStore()
    await store.fetchPluginDetail('demo')
    vi.mocked(getPlugin).mockRejectedValueOnce(new Error('detail unavailable'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await expect(store.syncRegistryAndFetchSummaries()).resolves.toMatchObject({ registryRefreshed: true })

    expect(getPlugin).toHaveBeenCalledTimes(2)
    expect(store.getPluginById('demo')?.version).toBe('1.0.0')
    warn.mockRestore()
  })

  it('removes detail and runtime state for an ID absent from a fresh registry snapshot', async () => {
    const store = usePluginStore()
    await store.fetchPluginDetail('demo')
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [], message: '' })

    await store.syncRegistryAndFetchSummaries()

    expect(store.getPluginById('demo')).toBeNull()
    expect(store.pluginSummariesWithStatus).toEqual([])
    expect(store.pluginStatuses.demo).toBeUndefined()
  })

  it('invalidates consumers after either registry or summary failure while preserving the original error', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    for (const failure of ['registry', 'summary'] as const) {
      setActivePinia(createPinia())
      const store = usePluginStore()
      await store.fetchPluginDetail('demo')
      const original = new Error(`${failure} unavailable`)
      if (failure === 'registry') vi.mocked(refreshPluginsRegistry).mockRejectedValueOnce(original)
      else vi.mocked(getPluginSummaries).mockRejectedValueOnce(original)
      vi.mocked(getPlugin).mockRejectedValueOnce(new Error('detail revalidation also failed'))
      const revision = store.registryRevision

      await expect(store.syncRegistryAndFetchSummaries()).rejects.toBe(original)

      expect(store.registryRevision).toBe(revision + 1)
      expect(store.getPluginById('demo')?.version).toBe('1.0.0')
    }
  })

  it('does not restore removed runtime cache when a pre-removal full status request completes', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    const old = deferred<{ plugins: Record<string, PluginStatusData> }>()
    vi.mocked(getPluginStatus).mockReturnValueOnce(old.promise)
    const statusRequest = store.fetchPluginStatus()
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [], message: '' })

    await store.syncRegistryAndFetchSummaries()
    old.resolve({ plugins: { demo: runtime('running') } })
    await statusRequest

    expect(store.getPluginById('demo')).toBeNull()
    expect(store.pluginStatuses.demo).toBeUndefined()
  })

  it.each([
    ['single', 'before'], ['full', 'before'], ['single', 'during'], ['full', 'during'],
  ] as const)('fences an old %s response started %s the removal read across same-ID reinstall', async (scope, timing) => {
    const store = usePluginStore()
    const summary = plugin()
    delete summary.status
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [summary], message: '' })
    await store.fetchPluginSummaries()
    const removed = deferred<{ plugins: PluginMeta[]; message: string }>()
    let removalRequest: Promise<void> | undefined
    if (timing === 'during') {
      vi.mocked(getPluginSummaries).mockReturnValueOnce(removed.promise)
      removalRequest = store.fetchPluginSummaries(true)
    }
    const old = deferred<any>()
    vi.mocked(getPluginStatus).mockReturnValueOnce(old.promise)
    const request = store.fetchPluginStatus(scope === 'single' ? 'demo' : undefined)

    if (removalRequest) {
      removed.resolve({ plugins: [], message: '' })
      await removalRequest
    } else {
      vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [], message: '' })
      await store.fetchPluginSummaries(true)
    }
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [{ ...summary, version: '2.0.0' }], message: '' })
    await store.fetchPluginSummaries(true)
    old.resolve(scope === 'single' ? runtime('running') : { plugins: { demo: runtime('running') } })
    await request

    expect(store.getPluginById('demo')?.version).toBe('2.0.0')
    expectVisibleStatus(store, 'stopped')
    expect(store.pluginStatuses.demo).toBeUndefined()
    // The removal fence must reject the old install, not disable future reads.
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')
    expectVisibleStatus(store, 'running')
  })

  it('fences concurrent status reads only for removed IDs, keeping live IDs updateable', async () => {
    const store = usePluginStore()
    const kept = plugin({ id: 'kept' })
    vi.mocked(getPluginSummaries).mockResolvedValueOnce({ plugins: [plugin(), kept], message: '' })
    await store.fetchPluginSummaries()
    const removed = deferred<{ plugins: PluginMeta[]; message: string }>()
    vi.mocked(getPluginSummaries).mockReturnValueOnce(removed.promise)
    const removal = store.fetchPluginSummaries(true)
    const status = deferred<{ plugins: Record<string, PluginStatusData> }>()
    vi.mocked(getPluginStatus).mockReturnValueOnce(status.promise)
    const statusRequest = store.fetchPluginStatus()
    removed.resolve({ plugins: [kept], message: '' })
    await removal
    status.resolve({ plugins: { demo: runtime('running'), kept: { ...runtime('running'), plugin_id: 'kept' } } })
    await statusRequest
    expect(store.getPluginById('demo')).toBeNull()
    expect(store.pluginStatuses.demo).toBeUndefined()
    expect(store.getPluginById('kept')?.status).toBe('running')
  })

  it('does not give stale metadata status new precedence when confirming an auto-start preference', async () => {
    const store = usePluginStore()
    await store.fetchPluginSummaries()
    await store.fetchPluginDetail('demo')
    vi.mocked(getPluginStatus).mockResolvedValueOnce(runtime('running'))
    await store.fetchPluginStatus('demo')
    vi.mocked(setPluginAutoStart).mockResolvedValueOnce({
      success: true, plugin_id: 'demo', auto_start: false, message: '',
    })

    await store.setAutoStart('demo', false, { refresh: false })

    expectVisibleStatus(store, 'running')
    expect(store.getPluginById('demo')?.autoStart).toBe(false)
    expect(store.pluginSummariesWithStatus[0]?.autoStart).toBe(false)
  })
})
