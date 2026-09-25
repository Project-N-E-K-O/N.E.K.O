import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import { getPlugins, getPluginSummaries, getPluginStatus, refreshPluginsRegistry, startPlugin } from '@/api/plugins'

const translate = vi.hoisted(() => vi.fn(
  (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}`,
))

const locale = vi.hoisted(() => ({ value: 'zh-CN' }))
vi.mock('@/i18n', () => ({
  getLocale: () => locale.value,
  i18n: {
    global: {
      t: translate,
    },
  },
}))

vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(),
  getPluginSummaries: vi.fn(),
  getPluginStatus: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  reloadPlugin: vi.fn(),
  refreshPluginsRegistry: vi.fn(),
}))

function registryRefreshResult() {
  return {
    success: true,
    added: [],
    updated: [],
    removed: [],
    removed_running: [],
    unchanged: [],
    failed: [],
    scanned_count: 0,
  }
}

describe('plugin store registry refresh policy', () => {
  beforeEach(() => {
    locale.value = 'zh-CN'
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(getPluginSummaries).mockResolvedValue({ plugins: [], message: '' })
    vi.mocked(getPluginStatus).mockResolvedValue({} as any)
    vi.mocked(startPlugin).mockResolvedValue({ success: true, plugin_id: 'demo', message: '' })
    vi.mocked(refreshPluginsRegistry).mockResolvedValue(registryRefreshResult())
  })

  it('runs the plugin list registry sync only once per manager window', async () => {
    const store = usePluginStore()

    const first = await store.ensurePluginListRegistrySynced()
    const second = await store.ensurePluginListRegistrySynced()

    expect(first?.registryRefreshed).toBe(true)
    expect(second).toBeNull()
    expect(store.pluginListRegistrySynced).toBe(true)
    expect(refreshPluginsRegistry).toHaveBeenCalledTimes(1)
    expect(getPluginSummaries).toHaveBeenCalledTimes(1)
  })

  it('does not reuse an in-flight list request from a different locale', async () => {
    let complete!: (value: any) => void
    vi.mocked(getPlugins).mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
    const store = usePluginStore()
    const old = store.fetchPlugins()
    locale.value = 'en-US'
    await store.ensurePlugins()
    expect(getPlugins).toHaveBeenCalledTimes(2)
    complete({ plugins: [plugin('stale')] })
    await old
    expect(store.plugins).toEqual([])
  })

  it('does not overwrite a fresh single status with an older full snapshot', async () => {
    let complete!: (value: any) => void
    vi.mocked(getPluginStatus)
      .mockImplementationOnce(() => new Promise(resolve => { complete = resolve }))
      .mockResolvedValueOnce({ status: 'running' } as any)
    const store = usePluginStore()
    const old = store.fetchPluginStatus()
    await store.fetchPluginStatus('demo')
    complete({ plugins: { demo: { status: 'stopped' } } })
    await old
    expect(store.pluginStatuses.demo?.status).toBe('running')
    expect(store.pluginStatusSnapshotLoaded).toBe(false)
  })

  it('marks explicit registry syncs as satisfying the first plugin list open', async () => {
    const store = usePluginStore()

    await store.syncRegistryAndFetch()
    const initialOpenResult = await store.ensurePluginListRegistrySynced()

    expect(initialOpenResult).toBeNull()
    expect(store.pluginListRegistrySynced).toBe(true)
    expect(refreshPluginsRegistry).toHaveBeenCalledTimes(1)
    expect(getPlugins).toHaveBeenCalledTimes(1)
  })

  it('localizes unauthenticated registry refresh warnings', async () => {
    vi.mocked(refreshPluginsRegistry).mockRejectedValue({ response: { status: 401 } })
    const store = usePluginStore()

    const result = await store.syncRegistryAndFetch()

    expect(translate).toHaveBeenCalledWith('messages.pluginListRefreshUnauthenticated')
    expect(result.warningMessage).toBe('messages.pluginListRefreshUnauthenticated')
  })

  it('localizes partial registry refresh warnings', async () => {
    vi.mocked(refreshPluginsRegistry).mockResolvedValue({
      ...registryRefreshResult(),
      success: false,
      failed: [{ plugin_id: 'broken', config_path: 'broken/plugin.toml', error: 'bad entry' }],
    })
    const store = usePluginStore()

    const result = await store.syncRegistryAndFetch()

    expect(translate).toHaveBeenCalledWith('messages.pluginListRefreshPartial', {
      target: 'broken',
      error: 'bad entry',
    })
    expect(result.warningMessage).toBe(
      'messages.pluginListRefreshPartial{"target":"broken","error":"bad entry"}',
    )
  })

  it('localizes unauthorized registry refresh warnings', async () => {
    vi.mocked(refreshPluginsRegistry).mockRejectedValue({ response: { status: 403 } })
    const store = usePluginStore()

    const result = await store.syncRegistryAndFetch()

    expect(translate).toHaveBeenCalledWith('messages.pluginListRefreshForbidden')
    expect(result.warningMessage).toBe('messages.pluginListRefreshForbidden')
  })

  it('uses the unknown warning when a failure has no target', async () => {
    vi.mocked(refreshPluginsRegistry).mockResolvedValue({
      ...registryRefreshResult(),
      success: false,
      failed: [{ plugin_id: '', config_path: '', error: 'bad entry' }],
    })
    const store = usePluginStore()

    const result = await store.syncRegistryAndFetch()

    expect(translate).toHaveBeenCalledWith('messages.pluginListRefreshPartialUnknown')
    expect(result.warningMessage).toBe('messages.pluginListRefreshPartialUnknown')
  })

  it('uses the multiple-failure warning and config path target', async () => {
    vi.mocked(refreshPluginsRegistry).mockResolvedValue({
      ...registryRefreshResult(),
      success: false,
      failed: [
        { plugin_id: '', config_path: 'first/plugin.toml', error: 'first error' },
        { plugin_id: 'second', config_path: 'second/plugin.toml', error: 'second error' },
      ],
    })
    const store = usePluginStore()

    const result = await store.syncRegistryAndFetch()

    expect(translate).toHaveBeenCalledWith('messages.pluginListRefreshPartialMultiple', {
      count: 2,
      target: 'first/plugin.toml',
      error: 'first error',
    })
    expect(result.warningMessage).toBe(
      'messages.pluginListRefreshPartialMultiple{"count":2,"target":"first/plugin.toml","error":"first error"}',
    )
  })

  it('continues fetching the plugin list after a registry 404', async () => {
    vi.mocked(refreshPluginsRegistry).mockRejectedValue({ response: { status: 404 } })
    const store = usePluginStore()

    const result = await store.syncRegistryAndFetch({ preserveMessagesOn404: true })

    expect(translate).toHaveBeenCalledWith('messages.resourceNotFound')
    expect(result.warningMessage).toBe('messages.resourceNotFound')
    expect(getPlugins).toHaveBeenCalledWith('zh-CN', { preserveMessagesOn404: true })
  })

  it('can defer lifecycle refreshes so batch operations refresh once afterward', async () => {
    const store = usePluginStore()

    await store.start('demo', { refresh: false })

    expect(startPlugin).toHaveBeenCalledWith('demo')
    expect(getPluginStatus).not.toHaveBeenCalled()
    expect(getPlugins).not.toHaveBeenCalled()
  })

  it('reuses a fresh plugin snapshot and refetches it after the TTL', async () => {
    const store = usePluginStore()
    const initialNow = Date.now()
    const now = vi.spyOn(Date, 'now').mockReturnValue(initialNow)

    await store.ensurePlugins()
    await store.ensurePlugins()
    expect(getPlugins).toHaveBeenCalledOnce()

    now.mockReturnValue(initialNow + 10_001)
    await store.ensurePlugins()
    expect(getPlugins).toHaveBeenCalledTimes(2)
    now.mockRestore()
  })

  it('reuses a fresh full status snapshot', async () => {
    const store = usePluginStore()
    await store.ensurePluginStatus()
    await store.ensurePluginStatus()

    expect(getPluginStatus).toHaveBeenCalledOnce()
    expect(store.pluginStatusSnapshotLoaded).toBe(true)
  })

  it('lets a forced status refresh supersede an older response', async () => {
    const store = usePluginStore()
    let resolveOld!: (value: any) => void
    vi.mocked(getPluginStatus)
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
      .mockResolvedValueOnce({ plugins: { fresh: { status: 'running' } } } as any)

    const oldRequest = store.fetchPluginStatus()
    const freshRequest = store.fetchPluginStatus(undefined, true)
    resolveOld({ plugins: { stale: { status: 'stopped' } } })
    await Promise.all([oldRequest, freshRequest])

    expect(store.pluginStatuses).toEqual({ fresh: { status: 'running' } })
  })

  it('invalidates a plugin list response that arrives after timeout', async () => {
    vi.useFakeTimers()
    try {
      const store = usePluginStore()
      let resolveLate!: (value: any) => void
      vi.mocked(getPlugins).mockImplementationOnce(() => new Promise((resolve) => { resolveLate = resolve }) as any)

      const request = store.fetchPlugins()
      vi.advanceTimersByTime(15_000)
      resolveLate({ plugins: [plugin('late')] })
      await expect(request).rejects.toThrow('获取插件列表超时')

      expect(store.plugins).toEqual([])
      expect(store.pluginsSnapshotLoaded).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})

function plugin(id: string) {
  return { id, name: id, description: '', version: '1.0.0', type: 'plugin' }
}
