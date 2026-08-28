import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { usePluginStore } from './plugin'
import { getPlugins, getPluginStatus, refreshPluginsRegistry, startPlugin } from '@/api/plugins'

const translate = vi.hoisted(() => vi.fn(
  (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}`,
))

vi.mock('@/i18n', () => ({
  getLocale: () => 'zh-CN',
  i18n: {
    global: {
      t: translate,
    },
  },
}))

vi.mock('@/api/plugins', () => ({
  getPlugins: vi.fn(),
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
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getPlugins).mockResolvedValue({ plugins: [], message: '' })
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
    expect(getPlugins).toHaveBeenCalledTimes(1)
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
})
